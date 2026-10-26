"""Per-business detection settings: thresholds and sensitivity.

Sensitivity presets set the statistical thresholds and the rule tolerances together.
Changing any of those values by hand switches sensitivity to "custom".
Payment limits (large/small) are amounts in the business's own currency, so they are
not part of the presets; they are suggested from the business's own data instead.
"""
import json
import statistics

DEFAULT_SETTINGS = {
    # Sprint 2: rule checks
    "dup_window_hours": 72.0,      # max hours between two charges to count as a possible double charge
    "dup_tolerance_pct": 5.0,      # max difference between the two amounts, in percent
    "large_limit": 50_000.0,       # flag payments at or above this amount
    "small_limit": 10.0,           # flag payments at or below this amount
    "spike_multiplier": 2.0,       # flag a category-day at or above this many times its daily average
    "limits_source": "default",    # default | data | owner  (who set the payment limits)
    # Sprint 3: statistical checks
    "statistical_enabled": True,
    "zscore_threshold": 3.0,       # flag when an amount is this many standard deviations from average
    "iqr_multiplier": 1.5,         # fence = Q1 - k*IQR .. Q3 + k*IQR
    "min_category_samples": 10,    # smaller categories are compared with all payments instead
    "sensitivity": "medium",       # low | medium | high | custom
}

SENSITIVITY_PRESETS = {
    "low":    {"zscore_threshold": 3.5, "iqr_multiplier": 3.0, "spike_multiplier": 2.5, "dup_tolerance_pct": 2.0},
    "medium": {"zscore_threshold": 3.0, "iqr_multiplier": 1.5, "spike_multiplier": 2.0, "dup_tolerance_pct": 5.0},
    "high":   {"zscore_threshold": 2.5, "iqr_multiplier": 1.0, "spike_multiplier": 1.5, "dup_tolerance_pct": 8.0},
}
PRESET_KEYS = set(SENSITIVITY_PRESETS["medium"])

# key -> (min, max) for numeric settings
LIMITS = {
    "dup_window_hours": (1, 24 * 31),
    "dup_tolerance_pct": (0, 50),
    "large_limit": (0.01, 1e12),
    "small_limit": (0, 1e12),
    "spike_multiplier": (1, 20),
    "zscore_threshold": (1, 10),
    "iqr_multiplier": (0.5, 10),
    "min_category_samples": (3, 1000),
}
FRIENDLY = {
    "dup_window_hours": "Double-charge time window (hours)",
    "dup_tolerance_pct": "Double-charge amount tolerance (%)",
    "large_limit": "Large-payment limit",
    "small_limit": "Small-payment limit",
    "spike_multiplier": "Category jump multiplier",
    "zscore_threshold": "Z-score threshold",
    "iqr_multiplier": "IQR range multiplier",
    "min_category_samples": "Minimum payments per category",
}


class SettingsError(ValueError):
    """An invalid setting the owner needs to fix."""


def get_settings(conn, business_id):
    row = conn.execute("SELECT value FROM detection_settings WHERE business_id = ?", (business_id,)).fetchone()
    return {**DEFAULT_SETTINGS, **(json.loads(row["value"]) if row else {})}


def save_settings(conn, business_id, settings):
    conn.execute(
        """INSERT INTO detection_settings (business_id, value) VALUES (?, ?)
           ON CONFLICT(business_id) DO UPDATE SET value = excluded.value""",
        (business_id, json.dumps(settings)),
    )
    conn.commit()


def apply_changes(current, changes):
    """Return new settings after validating the owner's changes."""
    new = dict(current)
    changes = dict(changes or {})
    unknown = set(changes) - set(DEFAULT_SETTINGS) - {"reset"}
    if unknown:
        raise SettingsError(f"Unknown setting(s): {', '.join(sorted(unknown))}.")

    if changes.pop("reset", False):
        new = dict(DEFAULT_SETTINGS)

    if "sensitivity" in changes:
        level = changes.pop("sensitivity")
        if level not in (*SENSITIVITY_PRESETS, "custom"):
            raise SettingsError("Sensitivity must be low, medium, high or custom.")
        new["sensitivity"] = level
        if level in SENSITIVITY_PRESETS:
            new.update(SENSITIVITY_PRESETS[level])

    if "statistical_enabled" in changes:
        new["statistical_enabled"] = bool(changes.pop("statistical_enabled"))
    changes.pop("limits_source", None)  # set by the system, not the owner

    for key, value in changes.items():
        low, high = LIMITS[key]
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise SettingsError(f"{FRIENDLY[key]} must be a number.") from None
        if not low <= number <= high:
            raise SettingsError(f"{FRIENDLY[key]} must be between {low:g} and {high:g}.")
        if number == new[key]:
            continue  # unchanged values (e.g. resubmitted by a form) don't switch to custom
        new[key] = int(number) if key == "min_category_samples" else number
        if key in PRESET_KEYS:
            new["sensitivity"] = "custom"
        if key in ("large_limit", "small_limit"):
            new["limits_source"] = "owner"

    if new["small_limit"] >= new["large_limit"]:
        raise SettingsError("The small-payment limit must be lower than the large-payment limit.")
    return new


def update_settings(conn, business_id, changes):
    new = apply_changes(get_settings(conn, business_id), changes)
    save_settings(conn, business_id, new)
    return new


def suggest_limits(amounts):
    """Suggest large/small payment limits from a business's own payments (top and bottom 1%)."""
    if len(amounts) < 20:
        return None
    cuts = statistics.quantiles(amounts, n=100, method="inclusive")
    small, large = round(cuts[0], 2), round(cuts[-1], 2)
    if small >= large:
        return None
    return {"large_limit": large, "small_limit": small}


def fill_limits_from_data(conn, business_id, amounts):
    """If the owner hasn't set payment limits yet, set them from their own data."""
    settings = get_settings(conn, business_id)
    if settings["limits_source"] == "owner":
        return settings
    suggestion = suggest_limits(amounts)
    if suggestion:
        settings.update(suggestion, limits_source="data")
        save_settings(conn, business_id, settings)
    return settings
