"""Treasury Guard — Increments 1-3.

Run:  python app.py   then open http://127.0.0.1:5000

HTML pages (placeholders until the real frontend is built) plus a JSON API under /api
(see treasury_guard/api.py and the README).
"""
import os
import secrets
from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, request, session, url_for

from treasury_guard import accounts, database, demo_data, detection, flags, labels, settings, transactions
from treasury_guard.accounts import AccountError
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

    # ---------- Existing placeholder pages (templates unchanged) ----------

    def flags_for_template(rows):
        """The current dashboard template expects these field names."""
        return [{**f, "id": f["flag_key"], "timestamp": f["occurred_at"],
                 "customer_id": f["counterparty"], "merchant_id": f["merchant"]} for f in rows]

    @app.get("/")
    def dashboard():
        if (redirect_response := login_required_page()):
            return redirect_response
        bid = g.business["id"]
        flag_type = request.args.get("type") or None
        status = request.args.get("status") or None
        counts = flags.count_by(g.db, bid, "type")
        return render_template(
            "dashboard.html",
            flags=flags_for_template(flags.list_flags(g.db, bid, flag_type, status, sort="date")),
            counts=counts,
            total=sum(counts.values()),
            last_run=flags.last_run(g.db, bid),
            scanned=transactions.count_transactions(g.db, bid),
            settings=settings.get_settings(g.db, bid),
            rule_names=labels.FLAG_TYPE_NAMES,
            status_names=labels.STATUS_NAMES,
            selected_type=flag_type or "",
            selected_status=status or "",
        )

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
            changes = {"reset": True} if "reset" in request.form else {
                k: request.form[k] for k in
                ("dup_window_hours", "dup_tolerance_pct", "large_limit", "small_limit", "spike_multiplier")
                if k in request.form
            }
            try:
                settings.update_settings(g.db, bid, changes)
            except SettingsError as e:
                error = str(e)
                current = {**current, **request.form.to_dict()}
            else:
                detection.run_detection(g.db, bid)
                return redirect(url_for("dashboard"))
        return render_template("rules.html", settings=current, error=error)

    @app.get("/later/<slug>")
    def later(slug):
        if slug not in labels.LATER_MODULES:
            abort(404)
        return render_template("placeholder.html", title=labels.LATER_MODULES[slug])

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
