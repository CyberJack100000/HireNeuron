"""Global settings and the per-interview 'effective' switches the recruiter can change live.

Precedence (highest first): global kill switch  >  this interview's live override  >  the interview's own configuration."""
from . import db

DEFAULT_SETTINGS = {"allow_back_default": False, "back_global_off": False, "gaze_global_off": False}


def get_settings() -> dict:
    out = dict(DEFAULT_SETTINGS)
    for r in db.many("SELECT key, value FROM settings"):
        if r["key"] in out:
            out[r["key"]] = r["value"] == "1"
    return out


def set_settings(changes: dict) -> dict:
    for k, v in changes.items():
        if k in DEFAULT_SETTINGS and v is not None:
            db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, "1" if v else "0"))
    return get_settings()


def live_of(iv: dict) -> dict:
    return {"allow_back": None, "gaze_enabled": None, "gaze_bypass": False, "extra_minutes": 0, "messages": [], **(iv.get("live") or {})}


def effective(iv: dict, settings: dict | None = None) -> dict:
    st = settings or get_settings()
    cfg, live = iv["config"], live_of(iv)
    gc = cfg.get("gaze_check") or {}
    allow_back = False if st["back_global_off"] else (live["allow_back"] if live["allow_back"] is not None else bool(cfg.get("allow_back")))
    gaze_on = False if st["gaze_global_off"] else (live["gaze_enabled"] if live["gaze_enabled"] is not None else bool(gc.get("enabled")))
    required = bool(gaze_on and gc.get("required") and not live["gaze_bypass"])
    return {"allow_back": allow_back, "duration_min": int(cfg.get("duration_min", 30)) + int(live["extra_minutes"] or 0),
            "gaze": {"enabled": gaze_on, "required": required, "bypass": bool(live["gaze_bypass"]),
                     "count": gc.get("count", 3), "duration_s": gc.get("duration_s", 8)},
            "global": {"back_off": st["back_global_off"], "gaze_off": st["gaze_global_off"]}}


def calibrated(iv: dict) -> bool:
    return bool(((iv.get("state") or {}).get("calibration") or {}).get("ok"))
