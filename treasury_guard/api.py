"""JSON API for the frontend.

All routes are under /api. Except register and login, every route needs a logged-in
session (cookie). Every request that changes data (POST/PUT/PATCH) must send the header
`X-Requested-With: TreasuryGuard`, which blocks cross-site form submissions (CSRF).
Errors come back as {"error": "message"} with a 4xx status.
"""
import json
from functools import wraps

from pathlib import Path

from flask import Blueprint, g, jsonify, request, send_file, session

from . import (accounts, activity, alerts, anomaly_model, csv_import, demo_data, detection, flags, insights, labels,
               risk, settings, severity, transactions)
from .accounts import AccountError
from .alerts import AlertSettingsError
from .csv_import import CsvImportError
from .settings import SettingsError

api = Blueprint("api", __name__, url_prefix="/api")
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "TreasuryGuard"


def error(message, status=400):
    return jsonify({"error": message}), status


@api.before_request
def require_csrf_header():
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        return error(f"Missing header {CSRF_HEADER}: {CSRF_VALUE}.", 403)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.business is None:
            return error("Log in first.", 401)
        return view(*args, **kwargs)
    return wrapper


def role_required(role):
    """Logged in with at least this role: owner > reviewer > viewer."""
    def decorate(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if g.business is None:
                return error("Log in first.", 401)
            if not accounts.can(g.business["role"], role):
                return error(f"Only a {role} or higher can do this.", 403)
            return view(*args, **kwargs)
        return wrapper
    return decorate


def log(action, detail=""):
    activity.log(g.db, g.business["id"], g.user_id, action, detail)


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def flag_for_api(f):
    return {
        **f,
        "type_name": labels.FLAG_TYPE_NAMES.get(f["type"], f["type"]),
        "method_name": labels.METHOD_NAMES.get(f["method"], f["method"]),
        "status_name": labels.STATUS_NAMES.get(f["status"], f["status"]),
        "severity_name": labels.SEVERITY_NAMES.get(f["severity"], f["severity"]),
    }


def detection_summary(result):
    return {**result, "counts_named": {labels.FLAG_TYPE_NAMES.get(k, k): v for k, v in result["counts"].items()}}


# ---------- Accounts ----------

@api.post("/auth/register")
def register():
    data = body()
    try:
        user_id, business_id = accounts.register_owner(
            g.db, data.get("email"), data.get("password"), data.get("full_name"),
            data.get("business_name"), data.get("business_type", "Other"),
        )
    except AccountError as e:
        return error(str(e))
    session.clear()
    session["user_id"] = user_id
    activity.log(g.db, business_id, user_id, "Created the account")
    alerts.update_settings(g.db, business_id, {"email_to": accounts.get_user(g.db, user_id)["email"]})
    return jsonify({"user": accounts.get_user(g.db, user_id),
                    "business": accounts.get_business_for_user(g.db, user_id)}), 201


@api.post("/auth/login")
def login():
    data = body()
    user = accounts.authenticate(g.db, data.get("email"), data.get("password"))
    if not user or not accounts.get_business_for_user(g.db, user["id"]):
        return error("Email or password is incorrect.", 401)
    session.clear()
    session["user_id"] = user["id"]
    return jsonify({"user": accounts.get_user(g.db, user["id"]),
                    "business": accounts.get_business_for_user(g.db, user["id"])})


@api.post("/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@api.get("/auth/me")
@login_required
def me():
    return jsonify({"user": accounts.get_user(g.db, g.user_id), "business": g.business})


# ---------- Business profile and categories ----------

@api.get("/business")
@login_required
def get_business():
    return jsonify({"business": g.business, "business_types": accounts.BUSINESS_TYPES,
                    "currencies": accounts.CURRENCIES})


@api.put("/business")
@role_required("owner")
def update_business():
    try:
        business = accounts.update_business(g.db, g.business["id"], body())
    except AccountError as e:
        return error(str(e))
    return jsonify({"business": business})


@api.get("/categories")
@login_required
def list_categories():
    return jsonify({"categories": accounts.list_categories(g.db, g.business["id"])})


@api.post("/categories")
@role_required("owner")
def add_category():
    try:
        name = accounts.add_category(g.db, g.business["id"], body().get("name"))
    except AccountError as e:
        return error(str(e))
    return jsonify({"added": name, "categories": accounts.list_categories(g.db, g.business["id"])}), 201


# ---------- CSV upload ----------

def uploaded_file():
    file = request.files.get("file")
    if not file or not file.filename:
        return None, None
    return file.filename, file.read()


@api.post("/uploads/preview")
@role_required("owner")
def preview_upload():
    filename, content = uploaded_file()
    if not filename:
        return error("Attach a CSV file in the 'file' field.")
    try:
        return jsonify(csv_import.preview_csv(content))
    except CsvImportError as e:
        return error(str(e))


@api.post("/uploads")
@role_required("owner")
def upload():
    filename, content = uploaded_file()
    if not filename:
        return error("Attach a CSV file in the 'file' field.")
    try:
        mapping = json.loads(request.form.get("mapping") or "{}")
    except json.JSONDecodeError:
        return error("'mapping' must be JSON, e.g. {\"date\": \"Date\", \"amount\": \"Amount\"}.")
    try:
        summary = csv_import.import_csv(g.db, g.business["id"], filename, content, mapping)
    except CsvImportError as e:
        return error(str(e))
    summary["detection"] = detection_summary(detection.run_detection(g.db, g.business["id"]))
    return jsonify(summary), 201


@api.get("/uploads/template")
def upload_template():
    """A ready-to-fill CSV with the recommended column names."""
    path = Path(__file__).resolve().parent.parent / "docs" / "transactions_template.csv"
    return send_file(path, mimetype="text/csv", as_attachment=True, download_name="treasury_guard_template.csv")


@api.get("/model")
def model_info():
    """What the AI model is, what it was trained on, and how well it did."""
    try:
        card = anomaly_model.model_card()
    except OSError:
        return error("No trained model yet. Run: python -m treasury_guard.training", 404)
    try:
        anomaly_model.load_models()
        card["status"] = "ready"
    except anomaly_model.ModelUnavailable as e:
        card["status"] = f"unavailable: {e}"
    return jsonify(card)


@api.get("/uploads")
@login_required
def list_uploads():
    return jsonify({"uploads": csv_import.list_uploads(g.db, g.business["id"])})


@api.post("/demo/load")
@role_required("owner")
def load_demo():
    try:
        summary = demo_data.load_demo_data(g.db, g.business["id"])
    except CsvImportError as e:
        return error(str(e))
    summary["detection"] = detection_summary(detection.run_detection(g.db, g.business["id"]))
    return jsonify(summary), 201


# ---------- Transactions ----------

@api.get("/transactions")
@login_required
def list_transactions():
    a = request.args
    try:
        result = transactions.list_transactions(
            g.db, g.business["id"], category=a.get("category"), date_from=a.get("from"), date_to=a.get("to"),
            direction=a.get("direction"), flagged_only=a.get("flagged") in ("1", "true"),
            page=a.get("page", 1), per_page=a.get("per_page", 50),
        )
    except ValueError:
        return error("page and per_page must be numbers.")
    for t in result["transactions"]:
        score = t["severity_score"]
        t["severity"] = None if score is None else severity.level_for(score)
    return jsonify(result)


# ---------- Detection settings ----------

@api.get("/detection/settings")
@login_required
def get_detection_settings():
    current = settings.get_settings(g.db, g.business["id"])
    amounts = [t["amount"] for t in transactions.load_outflows_for_detection(g.db, g.business["id"])]
    return jsonify({
        "settings": current,
        "sensitivity_presets": settings.SENSITIVITY_PRESETS,
        "sensitivity_names": labels.SENSITIVITY_NAMES,
        "suggested_limits": settings.suggest_limits(amounts),
        "setting_names": settings.FRIENDLY,
    })


@api.put("/detection/settings")
@role_required("owner")
def update_detection_settings():
    try:
        new = settings.update_settings(g.db, g.business["id"], body())
    except SettingsError as e:
        return error(str(e))
    result = detection.run_detection(g.db, g.business["id"])
    return jsonify({"settings": new, "detection": detection_summary(result)})


@api.post("/detection/run")
@role_required("owner")
def run_detection():
    return jsonify(detection_summary(detection.run_detection(g.db, g.business["id"])))


# ---------- Flags ----------

@api.get("/flags")
@login_required
def list_flags():
    a = request.args
    try:
        min_score = int(a["min_score"]) if a.get("min_score") else None
    except ValueError:
        return error("min_score must be a number from 0 to 100.")
    result = flags.list_flags(
        g.db, g.business["id"], flag_type=a.get("type"), status=a.get("status"),
        severity=a.get("severity"), min_score=min_score, sort=a.get("sort", "severity"),
    )
    return jsonify({"flags": [flag_for_api(f) for f in result], "count": len(result)})


@api.get("/flags/<flag_key>")
@login_required
def get_flag(flag_key):
    f = flags.get_flag(g.db, g.business["id"], flag_key)
    return jsonify(flag_for_api(f)) if f else error("Flag not found.", 404)


@api.patch("/flags/<flag_key>")
@role_required("reviewer")
def update_flag(flag_key):
    data = body()
    status = data.get("status")
    if status not in flags.STATUSES:
        return error(f"status must be one of: {', '.join(flags.STATUSES)}.")
    note = data.get("note")
    if note is not None and not isinstance(note, str):
        return error("note must be text.")
    if not flags.set_status(g.db, g.business["id"], flag_key, status, note, g.user_id):
        return error("Flag not found.", 404)
    log("Reviewed a flag", f"{flag_key}: {labels.STATUS_NAMES[status]}")
    return jsonify(flag_for_api(flags.get_flag(g.db, g.business["id"], flag_key)))


@api.get("/summary")
@login_required
def summary():
    bid = g.business["id"]
    by_type = flags.count_by(g.db, bid, "type")
    return jsonify({
        "transactions": transactions.count_transactions(g.db, bid),
        "flags_total": sum(by_type.values()),
        "flags_by_type": {k: {"name": labels.FLAG_TYPE_NAMES.get(k, k), "count": v} for k, v in by_type.items()},
        "flags_by_severity": flags.count_by(g.db, bid, "severity"),
        "flags_by_status": flags.count_by(g.db, bid, "status"),
        "flags_by_method": flags.count_by(g.db, bid, "method"),
        "last_run": flags.last_run(g.db, bid),
        "flag_type_names": labels.FLAG_TYPE_NAMES,
        "flag_type_descriptions": labels.FLAG_TYPE_DESCRIPTIONS,
    })


# ---------- Sprint 4: dashboard, alerts, combined risk; activity log and team ----------

@api.get("/dashboard")
@login_required
def dashboard():
    bid = g.business["id"]
    return jsonify({
        "overview": insights.overview(g.db, bid),
        "trend": insights.trend(g.db, bid),
        "categories": insights.categories(g.db, bid),
        "flags_by_severity": flags.count_by(g.db, bid, "severity"),
        "flags_by_status": flags.count_by(g.db, bid, "status"),
        "unread_alerts": alerts.unread_count(g.db, bid),
    })


@api.get("/flags/<flag_key>/explanation")
@login_required
def explain_flag(flag_key):
    """Plain-language explanation of a flag: what was found, what it means, what to check."""
    f = flags.get_flag(g.db, g.business["id"], flag_key)
    if not f:
        return error("Flag not found.", 404)
    fmt = labels.money_formatter(g.business["currency"])
    return jsonify({
        "headline": labels.headline(f, fmt), "reason": f["reason"],
        "what_it_means": labels.FLAG_TYPE_DESCRIPTIONS.get(f["type"], ""),
        "what_to_check": labels.FLAG_NEXT_STEPS.get(f["type"], []),
        "facts": [{"label": k, "value": v} for k, v in labels.flag_facts(f, fmt)],
        "severity_meaning": labels.SEVERITY_HELP[f["severity"]],
    })


@api.get("/alerts")
@login_required
def list_alerts():
    unread_only = request.args.get("unread") in ("1", "true")
    return jsonify({"alerts": alerts.list_alerts(g.db, g.business["id"], unread_only),
                    "unread": alerts.unread_count(g.db, g.business["id"])})


@api.post("/alerts/read")
@login_required
def read_alerts():
    """{ids: [..]} marks those alerts read; no ids marks all of them."""
    ids = body().get("ids")
    if ids is not None and not (isinstance(ids, list) and all(isinstance(i, int) for i in ids)):
        return error("ids must be a list of alert IDs.")
    alerts.mark_read(g.db, g.business["id"], ids)
    return jsonify({"unread": alerts.unread_count(g.db, g.business["id"])})


@api.get("/alerts/settings")
@login_required
def get_alert_settings():
    return jsonify({"settings": alerts.get_settings(g.db, g.business["id"])})


@api.put("/alerts/settings")
@role_required("owner")
def update_alert_settings():
    try:
        new = alerts.update_settings(g.db, g.business["id"], body())
    except AlertSettingsError as e:
        return error(str(e))
    log("Changed alert settings")
    return jsonify({"settings": new})


@api.get("/risk")
@login_required
def combined_risk():
    level = request.args.get("level")
    total, rows = risk.ranked(g.db, g.business["id"], level=level if level in flags.SEVERITIES else None)
    return jsonify({"total": total, "transactions": [
        {"transaction": r["txn"], "score": r["score"], "level": r["level"], "method_scores": r["methods"],
         "flags": [f["flag_key"] for f in r["flags"]]} for r in rows]})


@api.get("/activity")
@login_required
def list_activity():
    return jsonify({"activity": activity.list_activity(g.db, g.business["id"], 200)})


@api.get("/team")
@login_required
def list_team():
    return jsonify({"members": accounts.list_members(g.db, g.business["id"]), "your_role": g.business["role"]})
