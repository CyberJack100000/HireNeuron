"""Tamper-evident activity record for each interview.

Every entry stores the hash of the entry before it, so editing, removing or re-ordering an entry in the middle breaks the chain
and `verify` reports where. What this does NOT do: someone who can rewrite the whole database can rebuild a consistent chain, and
cutting entries off the END cannot be seen from inside the database. That is why `head` (the last hash) is shown to the recruiter and
included in exports: a head copied elsewhere at review time proves nothing was removed afterwards.

Answers are stored here as a length and a short fingerprint only (the text itself is kept in the answers table), spoken words as
transcript lines, everything else as small structured details."""
import hashlib
import json
from datetime import datetime, timezone

from . import db

GENESIS = "0" * 64
GLOBAL = "_global"            # actions that belong to no single interview (sign-ins, global settings, deletions)
MAX_ENTRIES = 20000
MAX_SPEECH = 6000             # a runaway client cannot bloat the database with transcript lines
MAX_TEXT = 1200
ACTORS = {"candidate", "interviewer", "system"}


def _clean(v, depth=0):
    if isinstance(v, str):
        return v[:MAX_TEXT]
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    if depth < 3 and isinstance(v, dict):
        return {str(k)[:40]: _clean(x, depth + 1) for k, x in list(v.items())[:30]}
    if depth < 3 and isinstance(v, (list, tuple)):
        return [_clean(x, depth + 1) for x in list(v)[:30]]
    return str(v)[:MAX_TEXT]


def _digest(prev: str, seq: int, at: str, actor: str, action: str, detail: str) -> str:
    return hashlib.sha256("\x1f".join((prev, str(seq), at, actor, action, detail)).encode("utf-8")).hexdigest()


def fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def log(iid: str, actor: str, action: str, detail: dict | None = None) -> dict | None:
    """Append one entry. Returns it, or None when a transcript line is dropped because the interview is over its speech limit."""
    if actor not in ACTORS:
        raise ValueError("Unknown actor.")
    d = json.dumps(_clean(detail or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with db._lock:
        last = db.one("SELECT seq, hash FROM audit WHERE interview_id=? ORDER BY seq DESC LIMIT 1", (iid,))
        seq = (last["seq"] + 1) if last else 1
        if seq > MAX_ENTRIES:
            return None
        if action == "speech" and db.one("SELECT COUNT(*) AS n FROM audit WHERE interview_id=? AND action='speech'", (iid,))["n"] >= MAX_SPEECH:
            return None
        at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        prev = last["hash"] if last else GENESIS
        h = _digest(prev, seq, at, actor, action, d)
        db.execute("INSERT INTO audit(interview_id,seq,at,actor,action,detail,prev,hash) VALUES(?,?,?,?,?,?,?,?)", (iid, seq, at, actor, action, d, prev, h))
    return {"seq": seq, "at": at, "actor": actor, "action": action, "detail": json.loads(d), "hash": h}


def entries(iid: str) -> list[dict]:
    out = []
    for r in db.many("SELECT seq, at, actor, action, detail, hash FROM audit WHERE interview_id=? ORDER BY seq", (iid,)):
        try:
            r["detail"] = json.loads(r["detail"])
        except ValueError:
            r["detail"] = {}
        out.append(r)
    return out


def verify(iid: str) -> dict:
    """Walk the chain from the start. `ok` is False when any entry was changed, removed from the middle, or re-ordered."""
    prev, n = GENESIS, 0
    for r in db.many("SELECT seq, at, actor, action, detail, prev, hash FROM audit WHERE interview_id=? ORDER BY seq", (iid,)):
        n += 1
        if r["seq"] != n or r["prev"] != prev or _digest(prev, r["seq"], r["at"], r["actor"], r["action"], r["detail"]) != r["hash"]:
            return {"ok": False, "count": n, "broken_at": r["seq"], "head": prev}
        prev = r["hash"]
    return {"ok": True, "count": n, "broken_at": None, "head": prev}


def transcript(iid: str) -> list[dict]:
    return [{"seq": e["seq"], "at": e["at"], "who": e["actor"], "t": e["detail"].get("t"), "text": e["detail"].get("text", "")}
            for e in entries(iid) if e["action"] == "speech"]
