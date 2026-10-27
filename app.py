"""Treasury Guard — Increments 1-4.

Run:  python app.py   then open http://127.0.0.1:5000

Server-rendered pages plus a JSON API under /api (see treasury_guard/api.py and the README).
"""
import os
import secrets
import time
from pathlib import Path

from flask import Flask, Response, abort, flash, g, redirect, render_template, request, session, url_for

from treasury_guard import (accounts, activity, alerts, anomaly_model, csv_import, database, demo_data, detection,
                            flags, insights, labels, mailer, reports, risk, settings, transactions)
from treasury_guard.accounts import AccountError
from treasury_guard.alerts import AlertSettingsError
from treasury_guard.csv_import import CsvImportError
from treasury_guard.api import api
from treasury_guard.settings import SettingsError

SEVERITY_LEVELS = ("high", "medium", "low")


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
    def page_context():
        b = g.get("business")
        return {
            "current_business": b,
            "current_user": accounts.get_user(g.db, g.user_id) if b else None,
            "unread_alerts": alerts.unread_count(g.db, b["id"]) if b else 0,
            "can": lambda needed: bool(b) and accounts.can(b["role"], needed),
            "role_names": labels.ROLE_NAMES,
            "rule_names": labels.FLAG_TYPE_NAMES,
            "method_names": labels.METHOD_NAMES,
            "status_names": labels.STATUS_NAMES,
            "severity_names": labels.SEVERITY_NAMES,
            "short_date": labels.short_date,
        }

    def guard(role="viewer"):
        """Redirect to login if signed out; 403 if the user's role is below `role`."""
        if g.business is None:
            return redirect(url_for("login_page", next=request.path))
        if not accounts.can(g.business["role"], role):
            abort(403)
        return None

    def log(action, detail=""):
        activity.log(g.db, g.business["id"], g.user_id, action, detail)

    def fmt():
        return labels.money_formatter(g.business["currency"])

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("message.html", title="You can't do that",
                               text="Your role on this team doesn't allow it. Ask the owner if you need access."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("message.html", title="Page not found",
                               text="That page doesn't exist, or the flag no longer applies after a re-check."), 404

    # ---------- Sign up / log in ----------

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
                activity.log(g.db, business_id, user_id, "Created the account")
                alerts.update_settings(g.db, business_id, {"email_to": form.get("email", "").strip().lower()})
                if form.get("load_demo"):
                    demo_data.load_demo_data(g.db, business_id)
                    detection.run_detection(g.db, business_id)
                    activity.log(g.db, business_id, user_id, "Loaded sample data")
                return redirect(url_for("dashboard"))
        return render_template("signup.html", error=error, form=form, business_types=accounts.BUSINESS_TYPES)

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        error = None
        if request.method == "POST":
            user = accounts.authenticate(g.db, request.form.get("email"), request.form.get("password"))
            business = accounts.get_business_for_user(g.db, user["id"]) if user else None
            if business:
                session.clear()
                session["user_id"] = user["id"]
                activity.log(g.db, business["id"], user["id"], "Logged in")
                next_url = request.args.get("next", "")
                return redirect(next_url if next_url.startswith("/") and not next_url.startswith("//")
                                else url_for("dashboard"))
            error = "Email or password is incorrect."
        return render_template("login.html", error=error, form=request.form)

    @app.route("/logout", methods=["GET", "POST"])
    def logout_page():
        session.clear()
        return redirect(url_for("login_page"))

    # ---------- Dashboard ----------

    @app.template_filter("money")
    def money_filter(amount):
        currency = g.business["currency"] if g.get("business") else "PHP"
        return labels.money_formatter(currency)(amount or 0)

    @app.template_filter("compact")
    def compact_filter(amount):
        """Short money for chart axes: ₱12.5k, ₱1.2M."""
        symbol = labels.CURRENCY_SYMBOLS.get(g.business["currency"] if g.get("business") else "PHP", "")
        for size, suffix in ((1e6, "M"), (1e3, "k")):
            if amount >= size:
                return f"{symbol}{amount / size:.1f}".rstrip("0").rstrip(".") + suffix
        return f"{symbol}{amount:.0f}"

    @app.template_filter("when")
    def when_filter(stamp):
        """'2026-10-27T05:47:13' -> 'Oct 27, 2026, 5:47 AM'."""
        if not stamp:
            return ""
        day = labels.short_date(stamp)
        if len(stamp) > 10:
            hh, mm = int(stamp[11:13]), stamp[14:16]
            return f"{day}, {hh % 12 or 12}:{mm} {'AM' if hh < 12 else 'PM'}"
        return day

    def check_descriptions(s, model_threshold):
        """One line per check, describing its current setting."""
        fraud_level = s["fraud_min_probability"] or model_threshold
        return [
            ("duplicate", f"Same payee, amounts within {s['dup_tolerance_pct']:g}%, at most "
                          f"{s['dup_window_hours']:g} hours apart"),
            ("large", f"{money_filter(s['large_limit'])} or more"),
            ("small", f"{money_filter(s['small_limit'])} or less"),
            ("spike", f"A day's category spending {s['spike_multiplier']:g}x its daily average"),
            ("zscore", f"{s['zscore_threshold']:g} standard deviations from the category average"
                       + ("" if s["statistical_enabled"] else " (off)")),
            ("iqr", f"Beyond {s['iqr_multiplier']:g}x the category's normal range"
                    + ("" if s["statistical_enabled"] else " (off)")),
            ("fraud", (f"Fraud risk {fraud_level * 100:.0f}% or more" if fraud_level else "Fraud model")
                      + ("" if s["fraud_model_enabled"] else " (off)")),
            ("ml", f"More unusual than {s['ml_percentile']:g}% of your payments"
                   + ("" if s["ml_enabled"] else " (off)")),
        ]

    def model_threshold_or_none():
        try:
            anomaly_model.load_models()
            return anomaly_model.model_card()["variants"]["with_time"]["threshold"]
        except (anomaly_model.ModelUnavailable, OSError, KeyError):
            return None

    @app.get("/")
    def dashboard():
        if (r := guard()):
            return r
        bid = g.business["id"]
        counts = flags.count_by(g.db, bid, "type")
        return render_template(
            "dashboard.html",
            overview=insights.overview(g.db, bid),
            trend=insights.trend(g.db, bid),
            categories=insights.categories(g.db, bid),
            counts=counts,
            total=sum(counts.values()),
            by_severity=flags.count_by(g.db, bid, "severity"),
            by_status=flags.count_by(g.db, bid, "status"),
            to_review=flags.list_flags(g.db, bid, status="new")[:5],
            recent_alerts=alerts.list_alerts(g.db, bid, limit=5),
            last_run=flags.last_run(g.db, bid),
            checks=check_descriptions(settings.get_settings(g.db, bid), model_threshold_or_none()),
            headline=lambda f: labels.headline(f, fmt()),
        )

    @app.post("/run")
    def run():
        if (r := guard("owner")):
            return r
        result = detection.run_detection(g.db, g.business["id"])
        log("Checked all payments again", f"{result['total']} flags")
        flash(f"Checked {result['scanned']} payments: {result['total']} flags, {result['alerts']} new alerts.")
        return redirect(request.referrer or url_for("dashboard"))

    # ---------- Flags and the review workflow ----------

    @app.get("/flags")
    def flags_page():
        if (r := guard()):
            return r
        bid = g.business["id"]
        a = request.args
        sel = {k: a.get(k, "") for k in ("type", "status", "severity")}
        sel["sort"] = "date" if a.get("sort") == "date" else "severity"
        rows = flags.list_flags(g.db, bid, sel["type"] or None, sel["status"] or None, sel["severity"] or None,
                                sort=sel["sort"])
        return render_template("flags.html", flags=rows, selected=sel,
                               total=sum(flags.count_by(g.db, bid, "type").values()),
                               headline=lambda f: labels.headline(f, fmt()))

    @app.get("/flags/<flag_key>")
    def flag_page(flag_key):
        if (r := guard()):
            return r
        bid = g.business["id"]
        f = flags.get_flag(g.db, bid, flag_key)
        if not f:
            abort(404)
        alerts.mark_read_for_flag(g.db, bid, flag_key)
        related = [x for x in flags.flags_for_transactions(g.db, bid, f["txn_ids"]) if x["flag_key"] != flag_key]
        return render_template(
            "flag.html", f=f, txns=transactions.by_ids(g.db, bid, f["txn_ids"]), related=related,
            reviewer=accounts.get_user(g.db, f["reviewed_by"]) if f["reviewed_by"] else None,
            headline=labels.headline(f, fmt()), description=labels.FLAG_TYPE_DESCRIPTIONS.get(f["type"], ""),
            next_steps=labels.FLAG_NEXT_STEPS.get(f["type"], []), facts=labels.flag_facts(f, fmt()),
            severity_help=labels.SEVERITY_HELP, status_help=labels.STATUS_HELP,
            in_queue=request.args.get("queue") == "1", left=flags.count_by(g.db, bid, "status").get("new", 0),
            related_headline=lambda x: labels.headline(x, fmt()),
        )

    @app.post("/flags/<flag_key>/status")
    def update_status(flag_key):
        if (r := guard("reviewer")):
            return r
        bid = g.business["id"]
        status = request.form.get("status")
        if status not in flags.STATUSES:
            abort(400)
        f = flags.get_flag(g.db, bid, flag_key)
        if not f:
            abort(404)
        flags.set_status(g.db, bid, flag_key, status, request.form.get("note"), g.user_id)
        log("Reviewed a flag", f"{labels.headline(f, fmt())}: {labels.STATUS_NAMES[status]}")
        if request.form.get("then") == "next":
            nxt = flags.next_to_review(g.db, bid, after_key=flag_key)
            return redirect(url_for("flag_page", flag_key=nxt, queue=1) if nxt else url_for("review_done"))
        flash(f"Saved as “{labels.STATUS_NAMES[status]}”.")
        return redirect(url_for("flag_page", flag_key=flag_key))

    @app.post("/flags/bulk")
    def bulk_status():
        if (r := guard("reviewer")):
            return r
        keys = request.form.getlist("keys")
        status = request.form.get("status")
        if status not in flags.STATUSES or not keys:
            flash("Tick at least one flag first.", "error")
            return redirect(request.referrer or url_for("flags_page"))
        n = sum(flags.set_status(g.db, g.business["id"], k, status, None, g.user_id) for k in keys)
        log("Reviewed flags in bulk", f"{n} flags: {labels.STATUS_NAMES[status]}")
        flash(f"Marked {n} flag{'s' if n != 1 else ''} as “{labels.STATUS_NAMES[status]}”.")
        return redirect(request.referrer or url_for("flags_page"))

    @app.get("/review")
    def review():
        if (r := guard()):
            return r
        nxt = flags.next_to_review(g.db, g.business["id"])
        return redirect(url_for("flag_page", flag_key=nxt, queue=1) if nxt else url_for("review_done"))

    @app.get("/review/done")
    def review_done():
        if (r := guard()):
            return r
        return render_template("message.html", title="All caught up",
                               text="Every flag has been reviewed. New ones will show up here and in your alerts.",
                               link=(url_for("dashboard"), "Back to the overview"))

    # ---------- Combined risk, transactions ----------

    @app.get("/risk")
    def risk_page():
        if (r := guard()):
            return r
        level = request.args.get("level", "")
        total, rows = risk.ranked(g.db, g.business["id"], level=level if level in SEVERITY_LEVELS else None)
        return render_template("risk.html", rows=rows, total=total, level=level)

    @app.get("/transactions")
    def transactions_page():
        if (r := guard()):
            return r
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
                               risk=risk.scores_by_transaction(g.db, g.business["id"]))

    # ---------- Alerts ----------

    @app.get("/alerts")
    def alerts_page():
        if (r := guard()):
            return r
        bid = g.business["id"]
        outbox = alerts.outbox_for(g.db)
        return render_template("alerts.html", alerts=alerts.list_alerts(g.db, bid),
                               existing={f["flag_key"] for f in flags.list_flags(g.db, bid)},
                               settings=alerts.get_settings(g.db, bid), levels=SEVERITY_LEVELS,
                               live_mail=mailer.is_live(), outbox=mailer.recent_outbox(outbox), outbox_path=outbox)

    @app.post("/alerts/read")
    def alerts_read():
        if (r := guard()):
            return r
        alerts.mark_read(g.db, g.business["id"], request.form.getlist("ids") or None)
        return redirect(request.referrer or url_for("alerts_page"))

    @app.post("/alerts/settings")
    def alerts_settings():
        if (r := guard("owner")):
            return r
        form = request.form
        try:
            alerts.update_settings(g.db, g.business["id"], {
                "in_app_min_severity": form.get("in_app_min_severity"),
                "email_min_severity": form.get("email_min_severity"),
                "email_enabled": "email_enabled" in form, "email_to": form.get("email_to", "")})
        except AlertSettingsError as e:
            flash(str(e), "error")
        else:
            log("Changed alert settings")
            flash("Alert settings saved.")
        return redirect(url_for("alerts_page"))

    @app.post("/alerts/test-email")
    def alerts_test_email():
        if (r := guard("owner")):
            return r
        s = alerts.get_settings(g.db, g.business["id"])
        if "@" not in s["email_to"]:
            flash("Save an email address first.", "error")
            return redirect(url_for("alerts_page"))
        result = mailer.send(alerts.outbox_for(g.db), s["email_to"], "Treasury Guard test email",
                             f"Hi,\n\nThis is a test from Treasury Guard for {g.business['name']}. "
                             "If you can read this, email alerts will reach you.\n")
        flash(f"Test email sent to {s['email_to']}." if result == "sent" else
              f"Development mode: nothing was sent. The email was saved to the outbox as {Path(result).name}.")
        return redirect(url_for("alerts_page"))

    # ---------- Reports ----------

    def report_range():
        def valid(v):
            return v if v and len(v) == 10 and v[4] == "-" and v[7] == "-" else None
        return valid(request.args.get("from")), valid(request.args.get("to"))

    @app.get("/reports")
    def reports_page():
        if (r := guard()):
            return r
        date_from, date_to = report_range()
        return render_template("reports.html", s=reports.summary(g.db, g.business["id"], date_from, date_to),
                               date_from=date_from or "", date_to=date_to or "",
                               headline=lambda f: labels.headline(f, fmt()))

    def csv_response(text, name):
        return Response(text, mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/reports/flags.csv")
    def report_flags_csv():
        if (r := guard()):
            return r
        return csv_response(reports.flags_csv(g.db, g.business["id"], *report_range()), "treasury_guard_flags.csv")

    @app.get("/reports/transactions.csv")
    def report_transactions_csv():
        if (r := guard()):
            return r
        return csv_response(reports.transactions_csv(g.db, g.business["id"], *report_range()),
                            "treasury_guard_transactions.csv")

    # ---------- Activity log and team ----------

    @app.get("/activity")
    def activity_page():
        if (r := guard()):
            return r
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        bid = g.business["id"]
        pages = max(1, -(-activity.count(g.db, bid) // 50))
        return render_template("activity.html", rows=activity.list_activity(g.db, bid, 50, (page - 1) * 50),
                               page=page, pages=pages)

    @app.route("/team", methods=["GET", "POST"])
    def team_page():
        if (r := guard()):
            return r
        bid = g.business["id"]
        error = None
        if request.method == "POST":
            if (r := guard("owner")):
                return r
            form = request.form
            try:
                if form.get("action") == "add":
                    accounts.add_member(g.db, bid, form.get("email"), form.get("full_name"), form.get("password"),
                                        form.get("role"))
                    log("Added a team member", f"{form.get('full_name')} ({labels.ROLE_NAMES.get(form.get('role'))})")
                    flash(f"Added {form.get('full_name')}. Give them their email and password so they can log in.")
                elif form.get("action") == "role":
                    accounts.set_member_role(g.db, bid, int(form.get("user_id", 0)), form.get("role"))
                    log("Changed a team member's role", labels.ROLE_NAMES.get(form.get("role"), ""))
                    flash("Role updated.")
                elif form.get("action") == "remove":
                    accounts.remove_member(g.db, bid, int(form.get("user_id", 0)))
                    log("Removed a team member")
                    flash("Removed from the team.")
                return redirect(url_for("team_page"))
            except (AccountError, ValueError) as e:
                error = str(e)
        return render_template("team.html", members=accounts.list_members(g.db, bid), error=error,
                               form=request.form, role_help=labels.ROLE_HELP)

    # ---------- Business profile, AI models, detection settings ----------

    @app.route("/business", methods=["GET", "POST"])
    def business_page():
        if (r := guard()):
            return r
        bid = g.business["id"]
        message = error = None
        if request.method == "POST":
            if (r := guard("owner")):
                return r
            try:
                if request.form.get("action") == "add_category":
                    name = accounts.add_category(g.db, bid, request.form.get("category"))
                    log("Added a category", name)
                    message = f"Added category {name}."
                else:
                    accounts.update_business(g.db, bid, {
                        k: request.form.get(k, "") for k in ("name", "business_type", "currency", "address")})
                    g.business = accounts.get_business_for_user(g.db, g.user_id)
                    log("Changed the business profile")
                    message = "Saved."
            except AccountError as e:
                error = str(e)
        return render_template("business.html", business=g.business, message=message, error=error,
                               business_types=accounts.BUSINESS_TYPES, currencies=accounts.CURRENCIES,
                               categories=accounts.list_categories(g.db, bid))

    @app.get("/model")
    def model_page():
        if (r := guard()):
            return r
        card, status = None, "ready"
        try:
            card = anomaly_model.model_card()
            anomaly_model.load_models()
        except OSError:
            status = "No trained model yet. Run: python -m treasury_guard.training"
        except anomaly_model.ModelUnavailable as e:
            status = str(e)
        return render_template("model.html", card=card, status=status)

    @app.route("/rules", methods=["GET", "POST"])
    def rules():
        if (r := guard()):
            return r
        bid = g.business["id"]
        current = settings.get_settings(g.db, bid)
        error = None
        if request.method == "POST":
            if (r := guard("owner")):
                return r
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
                new = settings.update_settings(g.db, bid, changes)
            except SettingsError as e:
                error = str(e)
                current = {**current, **request.form.to_dict()}
            else:
                result = detection.run_detection(g.db, bid)
                log("Changed detection settings", labels.SENSITIVITY_NAMES.get(new["sensitivity"], ""))
                flash(f"Settings saved. All payments were checked again: {result['total']} flags.")
                return redirect(url_for("dashboard"))
        amounts = [t["amount"] for t in transactions.load_outflows_for_detection(g.db, bid)]
        return render_template("rules.html", settings=current, error=error,
                               suggested=settings.suggest_limits(amounts),
                               model_threshold=model_threshold_or_none(),
                               sensitivity_names=labels.SENSITIVITY_NAMES)

    # ---------- CSV upload (two steps: preview + column mapping, then import) ----------

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

    def _after_import(summary, action):
        result = detection.run_detection(g.db, g.business["id"])
        log(action, f"{summary['filename']}: {summary['rows_imported']} rows imported")
        found = {labels.FLAG_TYPE_NAMES.get(k, k): v for k, v in result["counts"].items()}
        return _upload_page(summary=summary, found=found, found_total=result["total"], new_alerts=result["alerts"])

    @app.get("/upload")
    def upload_page():
        if (r := guard("owner")):
            return r
        return _upload_page()

    @app.post("/upload")
    def upload_preview():
        if (r := guard("owner")):
            return r
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
        if (r := guard("owner")):
            return r
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
        return _after_import(summary, "Uploaded a file")

    @app.post("/upload/demo")
    def upload_demo():
        if (r := guard("owner")):
            return r
        try:
            summary = demo_data.load_demo_data(g.db, g.business["id"])
        except CsvImportError as e:
            return _upload_page(error=str(e))
        return _after_import(summary, "Loaded sample data")

    @app.get("/template.csv")
    def template_csv():
        return redirect(url_for("api.upload_template"))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
