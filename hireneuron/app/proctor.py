"""Proctoring incident log. Distinguishes OBSERVED events (a detector saw something) from INFERRED suspicions
(derived patterns). Nothing here disqualifies anyone: every item is for human review."""
import base64
import re
from datetime import datetime, timezone

from . import db

ALLOWED = {
    # type: (source, default severity, description template)
    "face_absent": ("primary", "medium", "No face visible in the primary camera for {duration_s:.0f}s."),
    "multiple_faces": ("primary", "high", "{faces} faces visible in the primary camera."),
    "looking_away": ("primary", "low", "Head turned away from the screen for {duration_s:.0f}s."),
    "camera_lost": ("primary", "high", "Primary camera stopped or was disconnected."),
    "mic_lost": ("audio", "high", "Microphone stopped or was disconnected."),
    "audio_burst": ("audio", "low", "Unexpected audio activity while the candidate was expected to be listening."),
    "secondary_connected": ("secondary", "info", "Secondary phone camera connected."),
    "secondary_disconnected": ("secondary", "medium", "Secondary phone camera disconnected."),
    "secondary_multiple_people": ("secondary", "high", "{faces} people visible in the secondary camera."),
    "tab_hidden": ("browser", "medium", "Interview tab was hidden or the candidate switched tabs for {duration_s:.0f}s."),
    "window_blur": ("browser", "low", "Interview window lost focus."),
    "fullscreen_exit": ("browser", "low", "Candidate left fullscreen mode."),
    "paste_detected": ("browser", "low", "Text was pasted into the answer box."),
    "detector_unavailable": ("system", "info", "Face detection is unavailable in this browser; face-based checks did not run."),
    "recruiter_joined": ("live", "info", "A recruiter joined the live call."),
    "recruiter_left": ("live", "info", "A recruiter left the live call."),
    "recruiter_message": ("live", "info", "The interviewer sent the candidate a message."),
    "time_extended": ("live", "info", "The interviewer extended the interview time."),
    "ended_by_recruiter": ("live", "info", "The interviewer ended the interview."),
    "gaze_check_passed": ("gaze", "info", "Attention check passed: gaze followed the moving dot (match {score:.2f})."),
    "gaze_check_failed": ("gaze", "low", "Attention check: gaze did not clearly follow the moving dot (match {score:.2f}). Webcam gaze tracking is imprecise, so a single miss means little."),
    "gaze_check_unavailable": ("gaze", "info", "Attention check did not run ({reason})."),
}
COOLDOWN_S = 8
MAX_EVIDENCE = 200_000
MAX_EVENTS = 1500


def _sev_rank(s):
    return {"info": 0, "low": 1, "medium": 2, "high": 3}.get(s, 0)


def record(iid: str, payload: dict, base_dir) -> dict | None:
    typ = payload.get("type")
    if typ not in ALLOWED:
        raise ValueError("Unknown event type.")
    source, severity, tpl = ALLOWED[typ]
    detail = payload.get("detail") or {}
    reason = re.sub(r"[^a-zA-Z0-9 ,.'-]", "", str(detail.get("reason") or "unknown"))[:80]
    safe = {"duration_s": float(detail.get("duration_s") or 0), "faces": int(detail.get("faces") or 0),
            "score": float(detail.get("score") or 0), "reason": reason}
    try:
        desc = tpl.format(**safe)
    except Exception:
        desc = tpl
    t_off = max(0.0, min(float(payload.get("t_offset") or 0), 86400.0))
    if db.one("SELECT COUNT(*) AS n FROM events WHERE interview_id=?", (iid,))["n"] >= MAX_EVENTS:      # a runaway or hostile client cannot bloat the database
        return None
    # merge with a recent identical event instead of flooding the log (false-alarm control)
    last = db.one("SELECT * FROM events WHERE interview_id=? AND type=? AND kind='observed' ORDER BY id DESC LIMIT 1", (iid, typ))
    if last and 0 <= t_off - (last["t_offset"] or 0) < COOLDOWN_S and typ not in ("secondary_connected", "secondary_disconnected", "camera_lost", "mic_lost", "recruiter_joined", "recruiter_left", "recruiter_message", "time_extended", "ended_by_recruiter"):
        return None
    eid = db.execute("INSERT INTO events(interview_id,t_offset,created_at,source,type,kind,severity,description,evidence,basis,review) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (iid, t_off, datetime.now(timezone.utc).isoformat(timespec="seconds"), source, typ, "observed", severity, desc, None, None, None))
    ev = payload.get("evidence_b64")
    if isinstance(ev, str) and ev:
        raw = base64.b64decode(re.sub(r"^data:image/\w+;base64,", "", ev), validate=False)[:MAX_EVIDENCE]
        if raw[:3] == b"\xff\xd8\xff":  # only accept JPEG
            d = base_dir / "evidence" / iid
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{eid}.jpg").write_bytes(raw)
            db.execute("UPDATE events SET evidence=? WHERE id=?", (f"{eid}.jpg", eid))
    derive_inferences(iid)
    return db.one("SELECT * FROM events WHERE id=?", (eid,))


def _window_count(events: list[dict], span: float) -> int:
    ts = sorted(e["t_offset"] or 0 for e in events)
    best = 0
    for i, t in enumerate(ts):
        best = max(best, sum(1 for u in ts[i:] if u - t <= span))
    return best


def derive_inferences(iid: str):
    """Recompute inferred suspicions from observed events. Idempotent."""
    kept = {e["type"]: e["review"] for e in db.many("SELECT type, review FROM events WHERE interview_id=? AND kind='inferred'", (iid,)) if e["review"]}
    db.execute("DELETE FROM events WHERE interview_id=? AND kind='inferred'", (iid,))
    obs = db.many("SELECT * FROM events WHERE interview_id=? AND kind='observed' ORDER BY id", (iid,))
    by = lambda *types: [e for e in obs if e["type"] in types]
    rules = []
    away = by("looking_away")
    dur = sum(float(re.search(r"for (\d+)s", e["description"]).group(1)) if re.search(r"for (\d+)s", e["description"]) else 0 for e in away)
    if len(away) >= 3 and _window_count(away, 300) >= 3 or dur >= 45:
        rules.append(("attention_pattern", "medium", away,
                      f"Head was turned away from the screen {len(away)} times (about {dur:.0f}s in total). This pattern may mean the candidate is reading from another source, but it is also common when thinking or reading the question."))
    faces = by("multiple_faces", "secondary_multiple_people")
    if len(faces) >= 2:
        rules.append(("possible_assistance", "high", faces,
                      f"More than one person was visible on {len(faces)} occasions. This may indicate help from another person, or may be someone passing by; review the evidence."))
    focus = by("tab_hidden", "window_blur", "fullscreen_exit")
    if len(focus) >= 3:
        rules.append(("repeated_focus_loss", "medium", focus,
                      f"The interview window lost focus {len(focus)} times. This could be a notification or another application, or could indicate looking something up."))
    audio = by("audio_burst")
    if len(audio) >= 3:
        rules.append(("repeated_unexpected_audio", "low", audio,
                      f"Unexpected audio was detected {len(audio)} times while the candidate was meant to be listening. Voices cannot be identified automatically; review any recording."))
    feeds = by("camera_lost", "mic_lost", "secondary_disconnected")
    if len(feeds) >= 2:
        rules.append(("feed_interruptions", "medium", feeds,
                      f"Camera, microphone or secondary feed was interrupted {len(feeds)} times. This is often a connectivity problem; check whether it coincided with answers."))
    g_ok, g_bad = by("gaze_check_passed"), by("gaze_check_failed")
    if len(g_bad) >= 2 and len(g_bad) * 2 >= len(g_ok) + len(g_bad):
        rules.append(("gaze_check_pattern", "medium", g_bad,
                      f"Gaze did not clearly follow the dot in {len(g_bad)} of {len(g_ok) + len(g_bad)} attention checks. This can mean the candidate was looking elsewhere, "
                      "but glasses, lighting, camera position and some eye conditions also cause misses; look at the other evidence before drawing a conclusion."))
    for typ, sev, basis, desc in rules:
        db.execute("INSERT INTO events(interview_id,t_offset,created_at,source,type,kind,severity,description,evidence,basis,review) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (iid, basis[-1]["t_offset"], datetime.now(timezone.utc).isoformat(timespec="seconds"), "derived", typ, "inferred", sev,
                    desc + " Not proof of misconduct.", None, db.dumps([e["id"] for e in basis]), db.dumps(kept.get(typ))))


def timeline(iid: str) -> dict:
    ev = db.many("SELECT * FROM events WHERE interview_id=? ORDER BY t_offset, id", (iid,))
    flagged = [e for e in ev if e["severity"] in ("medium", "high") and not (e.get("review") or {}).get("status") == "dismissed"]
    gz = {"passed": sum(e["type"] == "gaze_check_passed" for e in ev), "failed": sum(e["type"] == "gaze_check_failed" for e in ev),
          "unavailable": sum(e["type"] == "gaze_check_unavailable" for e in ev)}
    return {"events": ev, "gaze": gz, "counts": {"observed": sum(e["kind"] == "observed" for e in ev), "inferred": sum(e["kind"] == "inferred" for e in ev),
                                     "high": sum(e["severity"] == "high" for e in ev), "medium": sum(e["severity"] == "medium" for e in ev)},
            "needs_review": bool(flagged),
            "note": "Events are indicators for human review. None of them disqualifies a candidate automatically."}


def review(event_id: int, status: str, note: str):
    if status not in ("dismissed", "confirmed", "noted"):
        raise ValueError("Invalid status.")
    db.execute("UPDATE events SET review=? WHERE id=?", (db.dumps({"status": status, "note": note[:500]}), event_id))
