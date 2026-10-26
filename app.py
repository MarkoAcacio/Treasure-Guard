"""Treasury Guard — Increments 1-3.

Run:  python app.py   then open http://127.0.0.1:5000

HTML pages (placeholders until the real frontend is built) plus a JSON API under /api
(see treasury_guard/api.py and the README).
"""
import os
import secrets
import time
from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, request, session, url_for

from treasury_guard import (accounts, anomaly_model, csv_import, database, demo_data, detection, flags, labels,
                            settings, severity, transactions)
from treasury_guard.accounts import AccountError
from treasury_guard.csv_import import CsvImportError
from treasury_guard.api import api
from treasury_guard.settings import SettingsError


def _secret_key(instance_dir):
    """Use TREASURY_GUARD_SECRET if set, otherwise a random key saved in instance/secret_key."""
    if os.environ.get("TREASURY_GUARD_SECRET"):
        return os.environ["TREASURY_GUARD_SECRET"]
    path = Path(instance_dir) / "secret_key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip()


def _percent_to_fraction(text):
    try:
        return float(text) / 100
    except ValueError:
        return text  # settings validation reports it


def create_app(db_path=None):
    app = Flask(__name__)
    db_path = db_path or database.DEFAULT_DB_PATH
    app.config.update(
        DB_PATH=str(db_path),
        SECRET_KEY=_secret_key(Path(db_path).parent),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,  # uploads are limited to 5 MB in csv_import
    )
    app.register_blueprint(api)

    @app.before_request
    def open_db_and_load_user():
        g.db = database.connect(app.config["DB_PATH"])
        g.user_id = session.get("user_id")
        g.business = accounts.get_business_for_user(g.db, g.user_id) if g.user_id else None
        if g.user_id and g.business is None:  # account was removed
            session.clear()
            g.user_id = None

    @app.teardown_request
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.context_processor
    def nav_items():
        return {"later_modules": labels.LATER_MODULES, "current_business": g.get("business")}

    def login_required_page():
        if g.business is None:
            return redirect(url_for("login_page", next=request.path))
        return None

    # ---------- Sign up / log in pages (new, barebones) ----------

    @app.route("/signup", methods=["GET", "POST"])
    def signup_page():
        error = None
        form = request.form
        if request.method == "POST":
            try:
                user_id, business_id = accounts.register_owner(
                    g.db, form.get("email"), form.get("password"), form.get("full_name"),
                    form.get("business_name"), form.get("business_type", "Other"),
                )
            except AccountError as e:
                error = str(e)
            else:
                session.clear()
                session["user_id"] = user_id
                if form.get("load_demo"):
                    demo_data.load_demo_data(g.db, business_id)
                    detection.run_detection(g.db, business_id)
                return redirect(url_for("dashboard"))
        return render_template("signup.html", error=error, form=form, business_types=accounts.BUSINESS_TYPES)

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        error = None
        if request.method == "POST":
            user = accounts.authenticate(g.db, request.form.get("email"), request.form.get("password"))
            if user:
                session.clear()
                session["user_id"] = user["id"]
                next_url = request.args.get("next", "")
                return redirect(next_url if next_url.startswith("/") and not next_url.startswith("//")
                                else url_for("dashboard"))
            error = "Email or password is incorrect."
        return render_template("login.html", error=error, form=request.form)

    @app.route("/logout", methods=["GET", "POST"])  # GET too, since the current pages have no logout button
    def logout_page():
        session.clear()
        return redirect(url_for("login_page"))

    # ---------- Pages ----------

    @app.template_filter("money")
    def money_filter(amount):
        currency = g.business["currency"] if g.get("business") else "PHP"
        return labels.money_formatter(currency)(amount or 0)

    def check_descriptions(s, model_threshold):
        """One line per check, describing its current setting."""
        fraud_level = s["fraud_min_probability"] or model_threshold
        return [
            ("duplicate", f"Same account, amounts within {s['dup_tolerance_pct']:g}%, at most "
                          f"{s['dup_window_hours']:g} hours apart"),
            ("large", f"Amount at or above {money_filter(s['large_limit'])}"),
            ("small", f"Amount at or below {money_filter(s['small_limit'])}"),
            ("spike", f"A day's category spending at least {s['spike_multiplier']:g}x its daily average for the month"),
            ("zscore", f"At least {s['zscore_threshold']:g} standard deviations from the category average"
                       + ("" if s["statistical_enabled"] else " (turned off)")),
            ("iqr", f"Beyond {s['iqr_multiplier']:g}x the normal range (IQR) of the category"
                    + ("" if s["statistical_enabled"] else " (turned off)")),
            ("fraud", (f"Fraud risk at least {fraud_level * 100:.0f}%" if fraud_level else "Fraud model")
                      + ("" if s["fraud_model_enabled"] else " (turned off)")),
            ("ml", f"More unusual than {s['ml_percentile']:g}% of your payments"
                   + ("" if s["ml_enabled"] else " (turned off)")),
        ]

    def model_threshold_or_none():
        try:
            anomaly_model.load_models()
            return anomaly_model.model_card()["variants"]["with_time"]["threshold"]
        except (anomaly_model.ModelUnavailable, OSError, KeyError):
            return None

    @app.get("/")
    def dashboard():
        if (redirect_response := login_required_page()):
            return redirect_response
        bid = g.business["id"]
        flag_type = request.args.get("type") or None
        status = request.args.get("status") or None
        severity = request.args.get("severity") or None
        sort = "date" if request.args.get("sort") == "date" else "severity"
        rows = flags.list_flags(g.db, bid, flag_type, status, severity, sort=sort)
        counts = flags.count_by(g.db, bid, "type")
        refs = transactions.references_for(g.db, bid, {i for f in rows for i in f["txn_ids"]})
        return render_template(
            "dashboard.html",
            flags=rows,
            refs=refs,
            counts=counts,
            total=sum(counts.values()),
            by_severity=flags.count_by(g.db, bid, "severity"),
            by_status=flags.count_by(g.db, bid, "status"),
            last_run=flags.last_run(g.db, bid),
            scanned=transactions.count_transactions(g.db, bid),
            checks=check_descriptions(settings.get_settings(g.db, bid), model_threshold_or_none()),
            rule_names=labels.FLAG_TYPE_NAMES,
            method_names=labels.METHOD_NAMES,
            status_names=labels.STATUS_NAMES,
            severity_names=labels.SEVERITY_NAMES,
            selected={"type": flag_type or "", "status": status or "", "severity": severity or "", "sort": sort},
        )

    @app.get("/transactions")
    def transactions_page():
        if (redirect_response := login_required_page()):
            return redirect_response
        a = request.args
        try:
            page = max(1, int(a.get("page", 1)))
        except ValueError:
            page = 1
        result = transactions.list_transactions(
            g.db, g.business["id"], category=a.get("category") or None, date_from=a.get("from") or None,
            date_to=a.get("to") or None, direction=a.get("direction") or None,
            flagged_only=a.get("flagged") == "1", page=page, per_page=50)
        pages = max(1, -(-result["total"] // result["per_page"]))
        args = {k: v for k, v in a.items() if k != "page" and v}
        return render_template("transactions.html", result=result, pages=pages, args=args,
                               categories=accounts.list_categories(g.db, g.business["id"]),
                               level_for=lambda score: None if score is None else labels.SEVERITY_NAMES[
                                   severity.level_for(score)])

    @app.route("/business", methods=["GET", "POST"])
    def business_page():
        if (redirect_response := login_required_page()):
            return redirect_response
        bid = g.business["id"]
        message = error = None
        if request.method == "POST":
            try:
                if request.form.get("action") == "add_category":
                    name = accounts.add_category(g.db, bid, request.form.get("category"))
                    message = f"Added category {name}."
                else:
                    g.business = accounts.update_business(g.db, bid, {
                        k: request.form.get(k, "") for k in ("name", "business_type", "currency", "address")})
                    message = "Saved."
            except AccountError as e:
                error = str(e)
        return render_template("business.html", business=g.business, message=message, error=error,
                               business_types=accounts.BUSINESS_TYPES, currencies=accounts.CURRENCIES,
                               categories=accounts.list_categories(g.db, bid),
                               user=accounts.get_user(g.db, g.user_id))

    @app.get("/model")
    def model_page():
        if (redirect_response := login_required_page()):
            return redirect_response
        card, status = None, "ready"
        try:
            card = anomaly_model.model_card()
            anomaly_model.load_models()
        except OSError:
            status = "No trained model yet. Run: python -m treasury_guard.training"
        except anomaly_model.ModelUnavailable as e:
            status = str(e)
        return render_template("model.html", card=card, status=status)

    @app.post("/run")
    def run():
        if (redirect_response := login_required_page()):
            return redirect_response
        detection.run_detection(g.db, g.business["id"])
        return redirect(url_for("dashboard"))

    @app.post("/flags/<flag_id>/status")
    def update_status(flag_id):
        if (redirect_response := login_required_page()):
            return redirect_response
        status = request.form.get("status")
        if status not in flags.STATUSES:
            abort(400)
        flags.set_status(g.db, g.business["id"], flag_id, status)
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/rules", methods=["GET", "POST"])
    def rules():
        if (redirect_response := login_required_page()):
            return redirect_response
        bid = g.business["id"]
        current = settings.get_settings(g.db, bid)
        error = None
        if request.method == "POST":
            form = request.form
            if "reset" in form:
                changes = {"reset": True}
            else:
                numbers = ("dup_window_hours", "dup_tolerance_pct", "large_limit", "small_limit", "spike_multiplier",
                           "zscore_threshold", "iqr_multiplier", "min_category_samples", "ml_percentile")
                changes = {k: form[k] for k in numbers if form.get(k, "") != ""}
                for switch in ("statistical_enabled", "fraud_model_enabled", "ml_enabled"):
                    changes[switch] = switch in form
                fraud_pct = form.get("fraud_min_probability_pct", "").strip()
                changes["fraud_min_probability"] = None if fraud_pct == "" else _percent_to_fraction(fraud_pct)
                level = form.get("sensitivity")
                if level and level != current["sensitivity"] and level in settings.SENSITIVITY_PRESETS:
                    # A new preset wins over the old values still sitting in the form.
                    for key in settings.PRESET_KEYS:
                        changes.pop(key, None)
                    changes["sensitivity"] = level
            try:
                settings.update_settings(g.db, bid, changes)
            except SettingsError as e:
                error = str(e)
                current = {**current, **request.form.to_dict()}
            else:
                detection.run_detection(g.db, bid)
                return redirect(url_for("dashboard"))
        amounts = [t["amount"] for t in transactions.load_outflows_for_detection(g.db, bid)]
        return render_template("rules.html", settings=current, error=error,
                               suggested=settings.suggest_limits(amounts),
                               model_threshold=model_threshold_or_none(),
                               sensitivity_names=labels.SENSITIVITY_NAMES)

    # ---------- CSV upload page (barebones, two steps: preview + column mapping, then import) ----------

    pending_dir = Path(app.config["DB_PATH"]).parent / "pending_uploads"
    PENDING_MAX_AGE = 3600  # seconds

    def _pending_path(token):
        if not token or not all(ch in "0123456789abcdef" for ch in token):
            return None
        return pending_dir / f"{token}.csv"

    def _clear_old_pending():
        if pending_dir.exists():
            for f in pending_dir.glob("*.csv"):
                if time.time() - f.stat().st_mtime > PENDING_MAX_AGE:
                    f.unlink(missing_ok=True)

    def _upload_page(**kwargs):
        return render_template("upload.html", uploads=csv_import.list_uploads(g.db, g.business["id"]),
                               fields=csv_import.FIELDS, **kwargs)

    @app.get("/upload")
    def upload_page():
        if (redirect_response := login_required_page()):
            return redirect_response
        return _upload_page()

    @app.post("/upload")
    def upload_preview():
        if (redirect_response := login_required_page()):
            return redirect_response
        file = request.files.get("file")
        if not file or not file.filename:
            return _upload_page(error="Choose a CSV file first.")
        content = file.read()
        try:
            preview = csv_import.preview_csv(content)
        except CsvImportError as e:
            return _upload_page(error=str(e))
        _clear_old_pending()
        pending_dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        _pending_path(token).write_bytes(content)
        session["pending_upload"] = {"token": token, "filename": file.filename}
        return _upload_page(preview=preview, filename=file.filename)

    @app.post("/upload/import")
    def upload_import():
        if (redirect_response := login_required_page()):
            return redirect_response
        pending = session.get("pending_upload") or {}
        path = _pending_path(pending.get("token"))
        if not path or not path.exists():
            return _upload_page(error="That upload expired. Choose the file again.")
        mapping = {field: request.form.get(f"map_{field}", "") for field in csv_import.FIELDS}
        content = path.read_bytes()
        try:
            summary = csv_import.import_csv(g.db, g.business["id"], pending["filename"], content, mapping)
        except CsvImportError as e:
            preview = csv_import.preview_csv(content)
            preview["suggested_mapping"] = {k: v for k, v in mapping.items() if v}
            return _upload_page(error=str(e), preview=preview, filename=pending["filename"])
        path.unlink(missing_ok=True)
        session.pop("pending_upload", None)
        result = detection.run_detection(g.db, g.business["id"])
        found = {labels.FLAG_TYPE_NAMES.get(k, k): v for k, v in result["counts"].items()}
        return _upload_page(summary=summary, found=found, found_total=result["total"])

    @app.post("/upload/demo")
    def upload_demo():
        if (redirect_response := login_required_page()):
            return redirect_response
        try:
            summary = demo_data.load_demo_data(g.db, g.business["id"])
        except CsvImportError as e:
            return _upload_page(error=str(e))
        result = detection.run_detection(g.db, g.business["id"])
        found = {labels.FLAG_TYPE_NAMES.get(k, k): v for k, v in result["counts"].items()}
        return _upload_page(summary=summary, found=found, found_total=result["total"])

    @app.get("/template.csv")
    def template_csv():
        return redirect(url_for("api.upload_template"))

    @app.get("/later/<slug>")
    def later(slug):
        if slug not in labels.LATER_MODULES:
            abort(404)
        return render_template("placeholder.html", title=labels.LATER_MODULES[slug])

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
