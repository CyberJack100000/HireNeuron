"""Interview runtime: question flow, limited adaptive follow-ups, time limits, answer tracking."""
import functools
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import audit, db, evaluate, flags, signaling

DEFAULT_CONFIG = {"duration_min": 30, "n_questions": 10, "categories": ["technical", "behavioral", "experience", "scenario"],
                  "difficulty": None, "max_followups": 2, "answer_time_limit_s": 120, "rubric_weights": {},
                  "gaze_check": {"enabled": False, "count": 3, "duration_s": 8, "required": False}, "allow_back": False,
                  "link_expires_hours": None}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def merge_config(cfg: dict | None) -> dict:
    c = {**DEFAULT_CONFIG, **{k: v for k, v in (cfg or {}).items() if v is not None}}
    c["duration_min"] = max(5, min(int(c["duration_min"]), 120))
    c["max_followups"] = max(0, min(int(c["max_followups"]), 10))
    c["answer_time_limit_s"] = max(20, min(int(c["answer_time_limit_s"]), 600))
    gc = {**DEFAULT_CONFIG["gaze_check"], **((cfg or {}).get("gaze_check") or {})}
    c["gaze_check"] = {"enabled": bool(gc["enabled"]), "count": max(1, min(int(gc["count"]), 8)), "duration_s": max(5, min(int(gc["duration_s"]), 15)),
                       "required": bool(gc["enabled"] and gc.get("required"))}
    c["allow_back"] = bool(c["allow_back"])
    ex = c.get("link_expires_hours")
    c["link_expires_hours"] = max(1, min(int(ex), 24 * 60)) if ex else None        # the candidate link stops working if the interview has not started by then
    return c


def link_expired(iv: dict) -> bool:
    """Only an interview that has not started can expire; once it is running the candidate may finish it."""
    hrs = (iv.get("config") or {}).get("link_expires_hours")
    if not hrs or iv["status"] != "created":
        return False
    return datetime.now(timezone.utc) > datetime.fromisoformat(iv["created_at"]) + timedelta(hours=hrs)


_locks: dict[str, threading.RLock] = defaultdict(threading.RLock)


def serialized(fn):
    """Requests for one interview run in worker threads. Without this, a double-click or a retried request could pass the
    'is this the expected question?' check twice and store the same answer twice. The interview is re-read inside the lock."""
    @functools.wraps(fn)
    def wrapper(iv, *args, **kwargs):
        with _locks[iv["id"]]:
            return fn(get(iv["id"]) or iv, *args, **kwargs)
    return wrapper


def duration_min(iv: dict) -> int:
    """Configured length plus any time the interviewer added while it runs."""
    return int(iv["config"]["duration_min"]) + int((iv.get("live") or {}).get("extra_minutes") or 0)


GRACE_S = 600


def sweep(iv: dict) -> dict:
    """An interview whose time ran out long ago and was never finished (candidate closed the tab) is finished now,
    so it gets its evaluation and stops showing as 'in progress'."""
    if iv and iv["status"] == "in_progress" and _elapsed(iv) > duration_min(iv) * 60 + GRACE_S:
        return finish(iv, by="system")
    return iv


def get(iid: str) -> dict:
    return db.one("SELECT * FROM interviews WHERE id=?", (iid,))


def by_token(token: str) -> dict | None:
    return db.one("SELECT * FROM interviews WHERE token=?", (token,))


def _answers(iid: str) -> list[dict]:
    return db.many("SELECT * FROM answers WHERE interview_id=? ORDER BY id", (iid,))


def _elapsed(iv: dict) -> float:
    if not iv.get("started_at"):
        return 0.0
    start = datetime.fromisoformat(iv["started_at"])
    return (datetime.now(timezone.utc) - start).total_seconds()


def progress(iv: dict) -> dict:
    ans = [a for a in _answers(iv["id"]) if a["parent_idx"] is None]
    return {"total": len(iv["questions"]), "answered": sum(a["status"] == "answered" for a in ans),
            "skipped": sum(a["status"] == "skipped" for a in ans),
            "unanswered": sum(a["status"] in ("unanswered", "timeout") for a in ans)}


@serialized
def start(iv: dict) -> dict:
    if iv["status"] == "completed":
        return {"done": True}
    if iv["status"] != "in_progress":
        db.update_interview(iv["id"], status="in_progress", started_at=now(),
                            state={**(iv["state"] or {}), "cursor": 0, "followups_used": 0, "pending": None, "viewing": None})
        audit.log(iv["id"], "candidate", "interview_started", {"questions": len(iv["questions"])})
    return current(get(iv["id"]))


def _main_answers(iid: str) -> dict:
    return {a["idx"]: a for a in _answers(iid) if a["parent_idx"] is None}


def _nav(iv: dict, st: dict, allow_back: bool) -> dict:
    """What the candidate may jump back to: every earlier question, with its status."""
    if not allow_back or st.get("pending"):
        return {"can_back": False, "previous": []}
    done = _main_answers(iv["id"])
    prev = [{"idx": i, "status": (done[i]["status"] if i in done else "unanswered"), "revisions": (done[i]["revisions"] if i in done else 0)}
            for i in range(min(st["cursor"], len(iv["questions"])))]
    return {"can_back": bool(prev), "previous": prev}


def current(iv: dict) -> dict:
    st = iv["state"] or {"cursor": 0, "followups_used": 0, "pending": None}
    cfg = iv["config"]
    eff = flags.effective(iv)
    remaining = duration_min(iv) * 60 - _elapsed(iv)
    if st.get("viewing") is not None and not eff["allow_back"]:       # the interviewer switched going back off mid-interview
        st["viewing"] = None
        db.update_interview(iv["id"], state=st)
    if iv["status"] == "completed" or st["cursor"] >= len(iv["questions"]) and not st.get("pending") and st.get("viewing") is None or remaining <= 0:
        return {"done": True, "progress": progress(iv)}
    nav = _nav(iv, st, eff["allow_back"])
    base = {"remaining_s": int(remaining), "total": len(iv["questions"]), "progress": progress(iv), **nav, "furthest": st["cursor"]}
    if st.get("viewing") is not None:
        i = st["viewing"]
        q = iv["questions"][i]
        prev = _main_answers(iv["id"]).get(i)
        return {**base, "done": False, "idx": i, "is_followup": False, "revisiting": True, "question": q["text"], "category": q["category"],
                "time_limit_s": cfg["answer_time_limit_s"], "number": i + 1,
                "previous_answer": (prev or {}).get("answer") or "", "previous_status": (prev or {}).get("status") or "unanswered",
                "revisions": (prev or {}).get("revisions", 0)}
    pend = st.get("pending")
    if pend:
        return {**base, "done": False, "idx": pend["parent_idx"], "is_followup": True, "revisiting": False, "question": pend["text"], "category": "follow-up",
                "time_limit_s": min(cfg["answer_time_limit_s"], 90), "number": st["cursor"] + 1}
    q = iv["questions"][st["cursor"]]
    return {**base, "done": False, "idx": st["cursor"], "is_followup": False, "revisiting": False, "question": q["text"], "category": q["category"],
            "time_limit_s": cfg["answer_time_limit_s"], "number": st["cursor"] + 1}


@serialized
def goto(iv: dict, idx: int) -> dict:
    """Open an earlier question for review or revision (only if the interviewer allows going back)."""
    if iv["status"] != "in_progress":
        raise ValueError("Interview is not in progress.")
    st = iv["state"]
    if not flags.effective(iv)["allow_back"]:
        raise PermissionError("Going back to earlier questions is not allowed in this interview.")
    if st.get("pending"):
        raise ValueError("Answer the follow-up question first.")
    if not 0 <= idx < st["cursor"]:
        raise ValueError("That question has not been asked yet.")
    st["viewing"] = idx
    db.update_interview(iv["id"], state=st)
    audit.log(iv["id"], "candidate", "went_back", {"idx": idx})
    return current(get(iv["id"]))


@serialized
def return_to_current(iv: dict) -> dict:
    st = iv["state"]
    st["viewing"] = None
    db.update_interview(iv["id"], state=st)
    return current(get(iv["id"]))


def _followup_for(q: dict, answer: str) -> str | None:
    hits, missed = evaluate.concept_hits(answer, q.get("expected") or [])
    n = evaluate.words(answer)
    if q.get("expected") and missed and len(hits) < len(q["expected"]) * 0.5:
        label = missed[0].split("|")[0]
        return f"You did not mention {label}. How does it come into play here?"
    if n < 20:
        return "Could you walk me through a concrete example from your own experience?"
    if hits and q["category"] == "technical":
        return f"You mentioned {hits[0]['concept']}. What trade-offs or pitfalls have you hit with it in practice?"
    return None


@serialized
def submit(iv: dict, idx: int, answer: str, status: str, duration_s: float | None, request_id: str | None = None) -> dict:
    if iv["status"] != "in_progress":
        raise ValueError("Interview is not in progress.")
    st = iv["state"]
    seen = st.setdefault("seen", [])
    if request_id and request_id in seen:               # a retry of a request the server already applied (its response was lost)
        return current(iv)
    if request_id:
        st["seen"] = (seen + [request_id])[-30:]
    pend = st.get("pending")
    cfg = iv["config"]
    answer = (answer or "").strip()[:6000]
    duration_s = None if duration_s is None else max(0.0, min(float(duration_s), 86400.0))
    audit.log(iv["id"], "candidate", "answer_submitted", {"idx": idx, "status": status, "chars": len(answer), "fingerprint": audit.fingerprint(answer),
                                                         "followup": bool(pend), "seconds": duration_s})
    if status not in ("answered", "skipped", "timeout", "unanswered"):
        raise ValueError("Invalid status.")
    if st.get("viewing") is None:
        if status == "answered" and not answer:
            status = "unanswered"
        if status == "timeout" and answer:
            status = "answered"          # time ran out but they said something
    if st.get("viewing") is not None:                      # revising an earlier answer
        if not flags.effective(iv)["allow_back"]:
            st["viewing"] = None
            db.update_interview(iv["id"], state=st)
            raise ValueError("Going back is no longer allowed. Continue with the current question.")
        if idx != st["viewing"]:
            raise ValueError("Unexpected question index.")
        if status != "answered" or not answer:
            raise ValueError("Write a non-empty answer, or return to the current question.")
        row = _main_answers(iv["id"]).get(idx)
        if row is None:
            q = iv["questions"][idx]
            db.execute("INSERT INTO answers(interview_id,idx,parent_idx,question,category,answer,status,duration_s,created_at,revisions,history) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (iv["id"], idx, None, q["text"], q["category"], answer, "answered", duration_s, now(), 1, db.dumps([])))
        else:
            hist = (row.get("history") or []) + [{"answer": row["answer"], "status": row["status"], "at": row["created_at"]}]
            db.execute("UPDATE answers SET answer=?, status='answered', duration_s=?, revisions=revisions+1, history=? WHERE id=?",
                       (answer, duration_s, db.dumps(hist), row["id"]))
        st["viewing"] = None
        db.update_interview(iv["id"], state=st)
        return current(get(iv["id"]))
    if pend:
        if idx != pend["parent_idx"]:
            raise ValueError("Unexpected question index.")
        db.execute("INSERT INTO answers(interview_id,idx,parent_idx,question,category,answer,status,duration_s,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (iv["id"], idx, idx, pend["text"], "follow-up", answer, status, duration_s, now()))
        st["pending"] = None
        st["cursor"] += 1
    else:
        if idx != st["cursor"]:
            raise ValueError("Unexpected question index.")
        q = iv["questions"][idx]
        db.execute("INSERT INTO answers(interview_id,idx,parent_idx,question,category,answer,status,duration_s,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (iv["id"], idx, None, q["text"], q["category"], answer, status, duration_s, now()))
        remaining = duration_min(iv) * 60 - _elapsed(iv)
        fu = None
        if status == "answered" and st["followups_used"] < cfg["max_followups"] and remaining > 120:
            fu = _followup_for(q, answer)
        if fu:
            st["pending"] = {"parent_idx": idx, "text": fu}
            st["followups_used"] += 1
        else:
            st["cursor"] += 1
    db.update_interview(iv["id"], state=st)
    return current(get(iv["id"]))


@serialized
def finish(iv: dict, by: str = "candidate") -> dict:
    if iv["status"] == "completed":
        return iv
    done = {a["idx"] for a in _answers(iv["id"]) if a["parent_idx"] is None}
    for i, q in enumerate(iv["questions"]):
        if i not in done:
            db.execute("INSERT INTO answers(interview_id,idx,parent_idx,question,category,answer,status,duration_s,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (iv["id"], i, None, q["text"], q["category"], "", "unanswered", None, now()))
    st = iv["state"] or {}
    st["viewing"] = None
    db.update_interview(iv["id"], status="completed", ended_at=now(), state=st)
    audit.log(iv["id"], by if by in audit.ACTORS else "system", "interview_finished", {"by": by})
    signaling.end_for(iv["id"])
    iv = get(iv["id"])
    ev = evaluate.evaluate(iv, _answers(iv["id"]))
    db.update_interview(iv["id"], evaluation=ev)
    return get(iv["id"])


def reevaluate(iid: str) -> dict:
    iv = get(iid)
    ev = evaluate.evaluate(iv, _answers(iid))
    db.update_interview(iid, evaluation=ev)
    return ev
