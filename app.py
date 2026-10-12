"""Treasury Guard — Increment 2 MVP.

Run:  python app.py   then open http://127.0.0.1:5000
"""
from flask import Flask, abort, redirect, render_template, request, url_for

from treasury_guard import storage
from treasury_guard.data_loader import load_transactions
from treasury_guard.rules import DEFAULT_SETTINGS, run_all_rules

app = Flask(__name__)
TRANSACTIONS = load_transactions()

RULE_NAMES = {
    "duplicate": "Duplicate charge",
    "large": "Large outflow",
    "small": "Small outflow",
    "spike": "Category spike",
}
STATUS_NAMES = {"new": "Needs review", "confirmed": "Confirmed", "dismissed": "Dismissed"}
LATER_MODULES = {
    "statistical": "Statistical detection (Z-score, IQR)",
    "hybrid": "Hybrid ML score (Isolation Forest)",
    "alerts": "Alerts",
    "reports": "Reports",
    "audit": "Audit trail",
    "users": "Users & roles",
}


def run_detection(conn):
    settings = storage.get_settings(conn, DEFAULT_SETTINGS)
    flags = run_all_rules(TRANSACTIONS, settings)
    storage.save_flags(conn, flags, scanned=len(TRANSACTIONS), settings=settings)


@app.context_processor
def nav_items():
    return {"later_modules": LATER_MODULES}


@app.get("/")
def dashboard():
    conn = storage.connect()
    if storage.last_run(conn) is None:
        run_detection(conn)  # first start: run once so the dashboard isn't empty

    flag_type = request.args.get("type") or None
    status = request.args.get("status") or None
    flags = storage.list_flags(conn, flag_type, status)
    counts = storage.count_by_type(conn)
    return render_template(
        "dashboard.html",
        flags=flags,
        counts=counts,
        total=sum(counts.values()),
        last_run=storage.last_run(conn),
        scanned=len(TRANSACTIONS),
        settings=storage.get_settings(conn, DEFAULT_SETTINGS),
        rule_names=RULE_NAMES,
        status_names=STATUS_NAMES,
        selected_type=flag_type or "",
        selected_status=status or "",
    )


@app.post("/run")
def run():
    run_detection(storage.connect())
    return redirect(url_for("dashboard"))


@app.post("/flags/<flag_id>/status")
def update_status(flag_id):
    status = request.form.get("status")
    if status not in storage.STATUSES:
        abort(400)
    storage.set_status(storage.connect(), flag_id, status)
    return redirect(request.referrer or url_for("dashboard"))


def _validate(form):
    try:
        s = {
            "dup_window_hours": float(form["dup_window_hours"]),
            "dup_tolerance_pct": float(form["dup_tolerance_pct"]),
            "large_limit": float(form["large_limit"]),
            "small_limit": float(form["small_limit"]),
            "spike_multiplier": float(form["spike_multiplier"]),
        }
    except (KeyError, ValueError):
        return None, "Enter a number in every field."
    if s["dup_window_hours"] < 1:
        return None, "Set the duplicate time window to at least 1 hour."
    if not 0 <= s["dup_tolerance_pct"] <= 50:
        return None, "Set the amount tolerance between 0 and 50%."
    if s["small_limit"] < 0 or s["small_limit"] >= s["large_limit"]:
        return None, "The small-outflow limit must be 0 or more and lower than the large-outflow limit."
    if s["spike_multiplier"] < 1:
        return None, "Set the spike multiplier to 1 or higher."
    return s, None


@app.route("/rules", methods=["GET", "POST"])
def rules():
    conn = storage.connect()
    error = None
    settings = storage.get_settings(conn, DEFAULT_SETTINGS)
    if request.method == "POST":
        if "reset" in request.form:
            new_settings, error = dict(DEFAULT_SETTINGS), None
        else:
            new_settings, error = _validate(request.form)
        if not error:
            storage.save_settings(conn, new_settings)
            run_detection(conn)
            return redirect(url_for("dashboard"))
        settings = {**settings, **request.form.to_dict()}
    return render_template("rules.html", settings=settings, error=error)


@app.get("/later/<slug>")
def later(slug):
    if slug not in LATER_MODULES:
        abort(404)
    return render_template("placeholder.html", title=LATER_MODULES[slug])


if __name__ == "__main__":
    app.run(debug=True)
