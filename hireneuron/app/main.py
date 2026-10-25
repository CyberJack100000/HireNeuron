"""HireNeuron API.  Run from hireneuron/:  uvicorn app.main:app --reload"""
import asyncio
import csv
import hmac
import io
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import segno
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Path as PathParam, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field

from . import audit, db, demo, evaluate, flags, interview, live, proctor, resume as R, service, signaling

WEB = Path(__file__).resolve().parent.parent / "web"
RECRUITER_KEY = os.environ.get("HN_RECRUITER_KEY", "demo-recruiter")
MAX_RECORDING_BYTES = 400 * 1024 * 1024

app = FastAPI(title="HireNeuron API", version="0.2.0")
# Python takes file types from the operating system. On some Windows machines the registry says ".js" is "text/plain", and together with
# "nosniff" the browser then refuses to run the page's scripts. So the types the app needs are fixed here, whatever the machine says.
FILE_TYPES = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".wasm": "application/wasm",
              ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".task": "application/octet-stream", ".json": "application/json"}


class Static(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        kind = FILE_TYPES.get(Path(path).suffix.lower())
        if kind and resp.status_code in (200, 304):
            resp.headers["content-type"] = kind
        return resp


app.mount("/static", Static(directory=WEB), name="static")

# Interview ids are 12 hex characters. Anything else is rejected before it can reach a file path (a crafted id such as ".."
# must never be able to point the delete / recording / evidence code at the data folder itself).
IID = Annotated[str, PathParam(pattern=r"^[0-9a-f]{12}$")]


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()")
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"          # responses hold personal data
    return resp


# ----------------------------------------------------------------------------- auth
SESSION_SECRET = secrets.token_bytes(32)          # sign-ins stop working when the server restarts
COOKIE = "hn_session"
_failed_logins: dict[str, list[float]] = {}


def _session_value() -> str:
    return hmac.new(SESSION_SECRET, RECRUITER_KEY.encode(), "sha256").hexdigest()


def _key_ok(given: str | None) -> bool:
    return bool(given) and secrets.compare_digest(given.encode(), RECRUITER_KEY.encode())


def _cookie_ok(value: str | None) -> bool:
    return bool(value) and secrets.compare_digest(value.encode(), _session_value().encode())


def recruiter(request: Request, x_recruiter_key: str | None = Header(None)):
    """Recruiter endpoints accept the sign-in cookie (browser) or the X-Recruiter-Key header (scripts). The key is never accepted
    in a URL, so it cannot end up in access logs or browser history."""
    if _key_ok(x_recruiter_key) or _cookie_ok(request.cookies.get(COOKIE)):
        return
    raise HTTPException(401, "Recruiter sign-in required.")


class LoginIn(BaseModel):
    key: str = Field(max_length=200)


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    now = time.time()
    if len(_failed_logins) > 1000:                       # forget old addresses so this cannot grow without bound
        for k in [k for k, v in _failed_logins.items() if not v or now - v[-1] > 300]:
            del _failed_logins[k]
    recent = [t for t in _failed_logins.get(ip, []) if now - t < 300]
    if len(recent) >= 10:
        raise HTTPException(429, "Too many wrong keys. Wait a few minutes and try again.")
    if not _key_ok(body.key):
        _failed_logins[ip] = recent + [now]
        audit.log(audit.GLOBAL, "system", "recruiter_login_failed", {"ip": ip})
        raise HTTPException(401, "That key is not valid.")
    _failed_logins.pop(ip, None)
    audit.log(audit.GLOBAL, "interviewer", "recruiter_login", {"ip": ip})
    response.set_cookie(COOKIE, _session_value(), httponly=True, samesite="lax", max_age=12 * 3600, path="/",
                        secure=request.url.scheme == "https" or os.environ.get("HN_PUBLIC_URL", "").startswith("https://"))   # behind an HTTPS tunnel the app itself sees http
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/session", dependencies=[Depends(recruiter)])
def session_ok():
    return {"ok": True}


def candidate(token: str) -> dict:
    iv = interview.by_token(token)
    if not iv:
        raise HTTPException(404, "Interview link not found.")
    if interview.link_expired(iv):
        raise HTTPException(410, "This interview link has expired. Please ask the recruiter for a new one.")
    return iv


def offset(iv: dict) -> float:
    if not iv.get("started_at"):
        return 0.0
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iv["started_at"])).total_seconds()


def _file_url() -> str:
    """hireneuron/public_url.txt: the address to hand out, for when the app should always advertise one fixed address (first line, https only)."""
    try:
        line = (Path(__file__).resolve().parent.parent / "public_url.txt").read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return ""
    return line if re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?", line.rstrip("/")) else ""


def public_base(request: Request) -> str:
    """The address candidates and phones should open. HN_PUBLIC_URL wins, then public_url.txt; inside a GitHub Codespace it is worked out from the
    Codespace's own name (the forwarded port is HTTPS); behind any other HTTPS proxy the forwarded headers are used."""
    env = os.environ.get("HN_PUBLIC_URL") or _file_url()
    if env:
        return env.rstrip("/")
    cs, dom = os.environ.get("CODESPACE_NAME"), os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    if cs and dom:
        return f"https://{cs}-{os.environ.get('HN_PORT', '8000')}.{dom}"
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    if proto in ("http", "https") and re.fullmatch(r"[A-Za-z0-9.-]+(:\d+)?", host):
        return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")


# ------------------------------------------------------------------------- pages
@app.get("/")
def home():
    return FileResponse(WEB / "recruiter.html", media_type=FILE_TYPES[".html"])


def not_found_page() -> FileResponse:
    return FileResponse(WEB / "404.html", media_type=FILE_TYPES[".html"], status_code=404)


@app.exception_handler(StarletteHTTPException)
async def http_errors(request: Request, exc: StarletteHTTPException):
    """A person who opens an address that does not exist gets a readable page. Programs (API, WebSocket, static files) keep getting JSON."""
    path = request.url.path
    if exc.status_code == 404 and request.method in ("GET", "HEAD") and not path.startswith(("/api/", "/ws/", "/static/")) \
            and "text/html" in request.headers.get("accept", ""):
        return not_found_page()
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.get("/i/{token}")
def page_candidate(token: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", token) or not interview.by_token(token):      # an unknown or mistyped link is a 404, not a blank interview page
        return not_found_page()
    return FileResponse(WEB / "candidate.html", media_type=FILE_TYPES[".html"])


@app.get("/m/{token}")
def page_mobile(token: str):
    return FileResponse(WEB / "mobile.html", media_type=FILE_TYPES[".html"])


@app.get("/api/health")
def health():
    from . import llm
    return {"status": "ok", "llm": llm.enabled(), "vision_vendored": (WEB / "vendor" / "vision_bundle.mjs").exists()}


# ------------------------------------------------------------- recruiter: setup
def _truthy(v) -> bool:
    return str(v or "").lower() in ("1", "true", "on", "yes")


async def _inputs(job_title, job_description, skills, level, n_questions, categories, difficulty, duration_min,
                  max_followups, answer_time_limit_s, custom_questions, resume_file, resume_text, gaze_check=None, gaze_count=None,
                  gaze_required=None, allow_back=None, link_expires_hours=None):
    if resume_file is not None:
        try:
            text = R.extract_text(resume_file.filename or "", await resume_file.read())
        except R.ResumeError as e:
            raise HTTPException(e.status, str(e))
    else:
        text = resume_text or ""
    cfg = {"level": level or None, "n_questions": n_questions, "categories": [c for c in (categories or "").split(",") if c] or None,
           "difficulty": difficulty or None, "duration_min": duration_min, "max_followups": max_followups,
           "answer_time_limit_s": answer_time_limit_s,
           "gaze_check": {"enabled": _truthy(gaze_check), "required": _truthy(gaze_required), **({"count": gaze_count} if gaze_count else {})},
           "allow_back": _truthy(allow_back) if allow_back is not None else flags.get_settings()["allow_back_default"],
           "link_expires_hours": link_expires_hours}
    try:
        custom = json.loads(custom_questions) if custom_questions else []
    except ValueError:
        raise HTTPException(400, "custom_questions must be a JSON list.")
    sk = [s for s in (skills or "").split(",") if s.strip()]
    return job_title, job_description or "", sk, text, cfg, custom


FORM = dict(job_title=Form(...), job_description=Form(...), skills=Form(None), level=Form(None), n_questions=Form(None),
            categories=Form(None), difficulty=Form(None), duration_min=Form(None), max_followups=Form(None),
            answer_time_limit_s=Form(None), custom_questions=Form(None), resume_text=Form(None), gaze_check=Form(None), gaze_count=Form(None), gaze_required=Form(None), allow_back=Form(None), link_expires_hours=Form(None))


@app.post("/api/interviews/preview", dependencies=[Depends(recruiter)])
async def preview(resume_file: UploadFile | None = File(None), job_title: str = FORM["job_title"], job_description: str = FORM["job_description"],
                  skills: str | None = FORM["skills"], level: str | None = FORM["level"], n_questions: int | None = FORM["n_questions"],
                  categories: str | None = FORM["categories"], difficulty: str | None = FORM["difficulty"], duration_min: int | None = FORM["duration_min"],
                  max_followups: int | None = FORM["max_followups"], answer_time_limit_s: int | None = FORM["answer_time_limit_s"],
                  custom_questions: str | None = FORM["custom_questions"], resume_text: str | None = FORM["resume_text"],
                  gaze_check: str | None = FORM["gaze_check"], gaze_count: int | None = FORM["gaze_count"],
                  gaze_required: str | None = FORM["gaze_required"], allow_back: str | None = FORM["allow_back"],
                  link_expires_hours: int | None = FORM["link_expires_hours"]):
    args = await _inputs(job_title, job_description, skills, level, n_questions, categories, difficulty, duration_min,
                         max_followups, answer_time_limit_s, custom_questions, resume_file, resume_text, gaze_check, gaze_count, gaze_required, allow_back, link_expires_hours)
    try:
        return service.prepare(*args)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/interviews", dependencies=[Depends(recruiter)])
async def create_interview(request: Request, resume_file: UploadFile | None = File(None), job_title: str = FORM["job_title"],
                           job_description: str = FORM["job_description"], skills: str | None = FORM["skills"], level: str | None = FORM["level"],
                           n_questions: int | None = FORM["n_questions"], categories: str | None = FORM["categories"],
                           difficulty: str | None = FORM["difficulty"], duration_min: int | None = FORM["duration_min"],
                           max_followups: int | None = FORM["max_followups"], answer_time_limit_s: int | None = FORM["answer_time_limit_s"],
                           custom_questions: str | None = FORM["custom_questions"], resume_text: str | None = FORM["resume_text"],
                           gaze_check: str | None = FORM["gaze_check"], gaze_count: int | None = FORM["gaze_count"],
                           gaze_required: str | None = FORM["gaze_required"], allow_back: str | None = FORM["allow_back"],
                           link_expires_hours: int | None = FORM["link_expires_hours"],
                           questions_json: str | None = Form(None), candidate_name: str | None = Form(None), candidate_email: str | None = Form(None)):
    args = await _inputs(job_title, job_description, skills, level, n_questions, categories, difficulty, duration_min,
                         max_followups, answer_time_limit_s, custom_questions, resume_file, resume_text, gaze_check, gaze_count, gaze_required, allow_back, link_expires_hours)
    try:
        prepared = service.prepare(*args)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if questions_json:  # the recruiter edited the previewed list
        try:
            edited = json.loads(questions_json)
            assert isinstance(edited, list) and 1 <= len(edited) <= 40 and all(isinstance(q.get("text"), str) and q["text"].strip() for q in edited)
        except Exception:
            raise HTTPException(400, "questions_json is invalid.")
        prepared["questions"] = [{"text": q["text"].strip(), "category": q.get("category", "technical"), "difficulty": q.get("difficulty", "intermediate"),
                                  "skill": q.get("skill"), "expected": q.get("expected", []), "source": q.get("source", "recruiter"), "rationale": q.get("rationale", "")} for q in edited]
    made = service.create(prepared, candidate_name, candidate_email)
    return {**made, "candidate_url": f"{public_base(request)}/i/{made['token']}", "n_questions": len(prepared["questions"])}


@app.post("/api/demo", dependencies=[Depends(recruiter)])
def make_demo():
    made = demo.seed(db.DATA_DIR)
    return made


# --------------------------------------------------------- recruiter: review
def _summary(iv: dict, request: Request) -> dict:
    ev = iv.get("evaluation")
    return {"id": iv["id"], "status": iv["status"], "created_at": iv["created_at"], "job_title": iv["job"]["title"],
            "candidate": iv["candidate"]["name"], "overall": ev["overall"] if ev else None,
            "candidate_url": f"{public_base(request)}/i/{iv['token']}" if iv["status"] != "completed" else None, "expired": interview.link_expired(iv),
            "flags": db.one("SELECT COUNT(*) AS n FROM events WHERE interview_id=? AND severity IN ('medium','high')", (iv["id"],))["n"],
            "candidate_connected": live.present(iv["id"], "candidate") > 0, "recruiters_in_call": live.present(iv["id"], "recruiter"),
            "progress": interview.progress(iv) if iv["status"] == "in_progress" else None}


@app.get("/api/interviews", dependencies=[Depends(recruiter)])
def list_interviews(request: Request):
    return [_summary(interview.sweep(iv), request) for iv in db.many("SELECT * FROM interviews ORDER BY created_at DESC")]


@app.get("/api/interviews/{iid}", dependencies=[Depends(recruiter)])
def get_interview(iid: IID, request: Request):
    return _detail(iid, request)


def _detail(iid: str, request: Request) -> dict:
    iv = interview.get(iid)
    if not iv:
        raise HTTPException(404, "Not found.")
    iv = interview.sweep(iv)
    ev = evaluate.apply_overrides(iv["evaluation"], iv["overrides"]) if iv["evaluation"] else None
    rec = (db.DATA_DIR / "recordings" / iid / "primary.webm")
    sec_parts = len(list((db.DATA_DIR / "recordings" / iid).glob("secondary*.webm")))
    dur = None
    if iv["started_at"] and iv["ended_at"]:
        dur = (datetime.fromisoformat(iv["ended_at"]) - datetime.fromisoformat(iv["started_at"])).total_seconds()
    return {"id": iid, "status": iv["status"], "created_at": iv["created_at"], "started_at": iv["started_at"], "ended_at": iv["ended_at"],
            "duration_s": dur, "job": iv["job"], "candidate": iv["candidate"], "resume": iv["resume"], "match": iv["match"],
            "config": iv["config"], "consent": iv["consent"], "questions": iv["questions"], "evaluation": ev,
            "proctoring": proctor.timeline(iid), "decision": iv["decision"], "recording": rec.exists(), "recording_secondary": sec_parts,
            "live": flags.live_of(iv), "effective": flags.effective(iv), "calibration": (iv["state"] or {}).get("calibration"),
            "call": {"candidate_connected": live.present(iid, "candidate") > 0, "recruiters": live.present(iid, "recruiter")},
            "candidate_url": f"{public_base(request)}/i/{iv['token']}", "link_expired": interview.link_expired(iv)}


class Override(BaseModel):
    idx: int
    score: int | None = Field(None, ge=1, le=5)
    note: str = Field("", max_length=600)


@app.patch("/api/interviews/{iid}/override", dependencies=[Depends(recruiter)])
def override(iid: IID, body: Override):
    iv = interview.get(iid)
    if not iv or not iv["evaluation"]:
        raise HTTPException(404, "Not found or not evaluated.")
    ov = iv["overrides"] or {}
    if body.score is None and not body.note:
        ov.pop(str(body.idx), None)
    else:
        ov[str(body.idx)] = {"score": body.score, "note": body.note, "at": interview.now()}
    db.update_interview(iid, overrides=ov)
    audit.log(iid, "interviewer", "score_override", {"idx": body.idx, "score": body.score, "note": body.note})
    return {"ok": True}


class Decision(BaseModel):
    decision: str = Field(pattern="^(advance|hold|reject|undecided)$")
    notes: str = Field("", max_length=2000)


@app.patch("/api/interviews/{iid}/decision", dependencies=[Depends(recruiter)])
def set_decision(iid: IID, body: Decision):
    if not interview.get(iid):
        raise HTTPException(404, "Not found.")
    db.update_interview(iid, decision={"decision": body.decision, "notes": body.notes, "at": interview.now()})
    audit.log(iid, "interviewer", "decision", {"decision": body.decision, "notes": body.notes})
    return {"ok": True}


class Review(BaseModel):
    status: str
    note: str = ""


@app.patch("/api/events/{eid}/review", dependencies=[Depends(recruiter)])
def review_event(eid: int, body: Review):
    try:
        proctor.review(eid, body.status, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    row = db.one("SELECT interview_id, type FROM events WHERE id=?", (eid,))
    if row:
        audit.log(row["interview_id"], "interviewer", "flag_reviewed", {"event_id": eid, "type": row["type"], "status": body.status, "note": body.note})
    return {"ok": True}


@app.post("/api/interviews/{iid}/reevaluate", dependencies=[Depends(recruiter)])
def reevaluate(iid: IID):
    if not interview.get(iid):
        raise HTTPException(404, "Not found.")
    interview.reevaluate(iid)
    audit.log(iid, "interviewer", "rescored", {})
    return {"ok": True}


@app.delete("/api/interviews/{iid}", dependencies=[Depends(recruiter)])
def delete_interview(iid: IID):
    import shutil
    if interview.get(iid):
        audit.log(audit.GLOBAL, "interviewer", "interview_deleted", {"interview": iid})
    for t in ("answers", "events", "audit"):
        db.execute(f"DELETE FROM {t} WHERE interview_id=?", (iid,))
    db.execute("DELETE FROM interviews WHERE id=?", (iid,))
    for sub in ("recordings", "evidence"):
        shutil.rmtree(db.DATA_DIR / sub / iid, ignore_errors=True)
    return {"deleted": True}


def _rec_name(cam: str, part: int) -> str:
    return "primary.webm" if cam == "primary" else ("secondary.webm" if part == 0 else f"secondary-{part}.webm")


@app.get("/api/interviews/{iid}/recording", dependencies=[Depends(recruiter)])
def recording(iid: IID, cam: str = Query("primary", pattern="^(primary|secondary)$"), part: int = Query(0, ge=0, le=9)):
    p = db.DATA_DIR / "recordings" / iid / _rec_name(cam, part)
    if not p.exists():
        raise HTTPException(404, "No recording.")
    audit.log(iid, "interviewer", "recording_viewed", {"camera": cam, "part": part})
    return FileResponse(p, media_type="video/webm")


@app.get("/api/interviews/{iid}/evidence/{name}", dependencies=[Depends(recruiter)])
def evidence(iid: IID, name: str):
    if not name.endswith(".jpg") or "/" in name or ".." in name:
        raise HTTPException(400, "Bad name.")
    p = db.DATA_DIR / "evidence" / iid / name
    if not p.exists():
        raise HTTPException(404, "No evidence.")
    return FileResponse(p, media_type="image/jpeg")


# ------------------------------------------------------------------ candidate
@app.get("/api/candidate/{token}")
def cand_info(token: str):
    iv = candidate(token)
    cfg = iv["config"]
    eff = flags.effective(iv)
    return {"interview_id": iv["id"], "status": iv["status"], "job_title": iv["job"]["title"], "candidate_name": iv["candidate"]["name"],
            "duration_min": eff["duration_min"], "n_questions": len(iv["questions"]), "answer_time_limit_s": cfg["answer_time_limit_s"],
            "consent": iv["consent"], "gaze_check": eff["gaze"], "allow_back": eff["allow_back"],
            "rules": RULES + (GAZE_RULE if eff["gaze"]["enabled"] else []) + (BACK_RULE if eff["allow_back"] else []) + LIVE_RULE}


RULES = ["Sit in a quiet, well-lit room and keep your face visible to the camera.",
         "Do not use other people, notes, other devices or websites to help you answer.",
         "Answer out loud in your own words. You can skip a question, but you cannot go back.",
         "The interview is led by an AI. A recruiter reviews the results and makes the final decision.",
         "Warnings from the monitoring are reviewed by a person and never disqualify you automatically."]


GAZE_RULE = ["A few times during the interview a blue dot will move across the screen for a few seconds. Please follow it with your eyes. "
             "This is an attention check. A miss is never treated as cheating on its own, and you can decline it if you cannot do it comfortably."]


BACK_RULE = ["You can go back to earlier questions and change your answers while the interviewer allows it."]
LIVE_RULE = ["The recruiter may join the interview live, like a video call. You will see a notice on screen whenever someone joins."]


class Consent(BaseModel):
    recording: bool = False
    proctoring: bool
    secondary_camera: bool = False


@app.post("/api/candidate/{token}/consent")
def cand_consent(token: str, body: Consent):
    iv = candidate(token)
    if not body.proctoring:
        raise HTTPException(400, "Consent to proctoring is required to take this interview.")
    db.update_interview(iv["id"], consent={**body.model_dump(), "at": interview.now()})
    audit.log(iv["id"], "candidate", "consent_given", body.model_dump())
    return {"ok": True}


@app.post("/api/candidate/{token}/start")
def cand_start(token: str):
    iv = candidate(token)
    if not iv["consent"]:
        raise HTTPException(409, "Consent is required before starting.")
    if flags.effective(iv)["gaze"]["required"] and not flags.calibrated(iv):
        raise HTTPException(409, "Eye-tracking calibration is required before this interview can start. If it does not work for you, ask the interviewer to bypass it.")
    return interview.start(iv)


@app.get("/api/candidate/{token}/current")
def cand_current(token: str):
    return interview.current(candidate(token))


class Answer(BaseModel):
    idx: int = Field(ge=0, le=1000)
    answer: str = Field("", max_length=6000)
    status: str = Field("answered", max_length=12)
    duration_s: float | None = Field(None, ge=0, le=1_000_000)
    request_id: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9_-]*$")


@app.post("/api/candidate/{token}/answer")
def cand_answer(token: str, body: Answer):
    iv = candidate(token)
    try:
        return interview.submit(iv, body.idx, body.answer, body.status, body.duration_s, body.request_id)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/candidate/{token}/finish")
def cand_finish(token: str):
    iv = candidate(token)
    if iv["status"] == "created":
        raise HTTPException(409, "Interview has not started.")
    interview.finish(iv)
    return {"done": True}


class EventIn(BaseModel):
    type: str
    t_offset: float = 0
    detail: dict | None = None
    evidence_b64: str | None = Field(None, max_length=400_000)


@app.post("/api/candidate/{token}/events")
def cand_event(token: str, body: EventIn):
    iv = candidate(token)
    if not (iv["consent"] or {}).get("proctoring"):
        raise HTTPException(409, "No proctoring consent.")
    try:
        ev = proctor.record(iv["id"], body.model_dump(), db.DATA_DIR)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if ev is not None:
        audit.log(iv["id"], "candidate", "proctor_event", {"type": ev["type"], "severity": ev["severity"], "t": ev["t_offset"], "event_id": ev["id"]})
    return {"recorded": ev is not None}


@app.post("/api/candidate/{token}/recording")
async def cand_recording(token: str, request: Request, seq: int = Query(0), cam: str = Query("primary", pattern="^(primary|secondary)$"), part: int = Query(0, ge=0, le=9)):
    iv = candidate(token)
    if not (iv["consent"] or {}).get("recording"):
        raise HTTPException(403, "Recording consent not given.")
    d = db.DATA_DIR / "recordings" / iv["id"]
    d.mkdir(parents=True, exist_ok=True)
    f = d / _rec_name(cam, part)
    chunk = await request.body()
    if len(chunk) > 8 * 1024 * 1024 or (f.exists() and f.stat().st_size + len(chunk) > MAX_RECORDING_BYTES):
        raise HTTPException(413, "Recording too large.")
    with open(f, "ab" if seq else "wb") as fh:
        fh.write(chunk)
    if not seq:
        audit.log(iv["id"], "candidate", "recording_started", {"camera": cam, "part": part})
    return {"ok": True}


class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    t_offset: float = Field(0, ge=0, le=86400)


def _speech(iid: str, who: str, body: Speech) -> dict:
    text = " ".join("".join(c for c in body.text if c.isprintable()).split())
    if not text:
        return {"recorded": False}
    # the time is taken from the server's own clock for this interview, not from what the browser claims
    return {"recorded": audit.log(iid, who, "speech", {"text": text, "t": round(offset(interview.get(iid)), 1)}) is not None}


@app.post("/api/candidate/{token}/transcript")
def cand_transcript(token: str, body: Speech):
    """What the candidate said, as heard by their browser's speech recogniser. Needs the proctoring consent and a running interview."""
    iv = candidate(token)
    if not (iv["consent"] or {}).get("proctoring") or iv["status"] != "in_progress":
        raise HTTPException(409, "The interview is not running.")
    return _speech(iv["id"], "candidate", body)


@app.post("/api/interviews/{iid}/transcript", dependencies=[Depends(recruiter)])
def interviewer_transcript(iid: IID, body: Speech):
    iv = interview.get(iid)
    if not iv:
        raise HTTPException(404, "Not found.")
    if iv["status"] != "in_progress":
        raise HTTPException(409, "The interview is not running.")
    return _speech(iid, "interviewer", body)


@app.get("/api/interviews/{iid}/audit", dependencies=[Depends(recruiter)])
def get_audit(iid: IID):
    if not interview.get(iid):
        raise HTTPException(404, "Not found.")
    return {"entries": audit.entries(iid), "verify": audit.verify(iid)}


@app.get("/api/candidate/{token}/pair")
def cand_pair(token: str, request: Request):
    iv = candidate(token)
    if not (iv["consent"] or {}).get("secondary_camera"):
        raise HTTPException(403, "Secondary camera consent not given.")
    p = signaling.create(iv["id"])
    url = f"{public_base(request)}/m/{p['token']}"
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=6, border=4, dark="#101A14", light="#FFFFFF", omitsize=True)   # viewBox, so CSS can scale it without cutting it off; 4 modules of quiet zone as the standard asks
    return {"pair_token": p["token"], "pair_url": url, "qr_svg": buf.getvalue().decode(), "expires_in": p["expires_in"],
            "secure_context": url.startswith("https://") or "localhost" in url or "127.0.0.1" in url}


@app.get("/api/pair/{tok}")
def pair_info(tok: str):
    p = signaling.get(tok)
    if not p or p.get("ended"):
        raise HTTPException(404, "This pairing link has expired. Scan the QR code again.")
    return {"ok": True}


@app.websocket("/ws/pair/{tok}/{role}")
async def ws_pair(ws: WebSocket, tok: str, role: str):
    p = signaling.get(tok)
    if not p or role not in ("desktop", "phone"):
        await ws.close(code=4404)
        return
    await ws.accept()
    if p.get("ended"):
        await ws.send_text(json.dumps({"type": "end"}))
        await ws.close(code=4410)
        return
    old = p[role]
    p[role] = ws
    if old is not None:
        try:
            await old.close(code=4409)
        except Exception:
            pass
    iv = interview.get(p["interview_id"])
    other_role = "phone" if role == "desktop" else "desktop"
    if role == "phone":
        if iv:
            proctor.record(iv["id"], {"type": "secondary_connected", "t_offset": offset(iv)}, db.DATA_DIR)
        p["phone_seen"] = True
        if p["desktop"]:
            await p["desktop"].send_text(json.dumps({"type": "phone-joined"}))
            await ws.send_text(json.dumps({"type": "desktop-ready"}))
    elif p["phone"]:
        await ws.send_text(json.dumps({"type": "phone-joined"}))
        await p["phone"].send_text(json.dumps({"type": "desktop-ready"}))
    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), 3)
            except asyncio.TimeoutError:
                if p.get("ended"):                           # the interview finished (candidate left, time ran out, or the interviewer ended it)
                    await ws.send_text(json.dumps({"type": "end"}))
                    await ws.close(code=4410)
                    break
                continue
            if len(msg) > 65536:
                continue
            other = p[other_role]
            if other is not None:
                await other.send_text(msg)
    except WebSocketDisconnect:
        pass
    finally:
        if p[role] is ws:
            p[role] = None
            if role == "phone" and iv and not p.get("ended"):
                proctor.record(iv["id"], {"type": "secondary_disconnected", "t_offset": offset(interview.get(iv["id"]))}, db.DATA_DIR)
                if p["desktop"]:
                    try:
                        await p["desktop"].send_text(json.dumps({"type": "phone-left"}))
                    except Exception:
                        pass


# ------------------------------------------------- live switches, settings, flags
class SettingsIn(BaseModel):
    allow_back_default: bool | None = None
    back_global_off: bool | None = None
    gaze_global_off: bool | None = None


@app.get("/api/settings", dependencies=[Depends(recruiter)])
def read_settings():
    return flags.get_settings()


@app.patch("/api/settings", dependencies=[Depends(recruiter)])
def write_settings(body: SettingsIn):
    before = flags.get_settings()
    after = flags.set_settings(body.model_dump())
    for k, v in after.items():
        if before.get(k) != v:
            audit.log(audit.GLOBAL, "interviewer", "global_setting_changed", {"setting": k, "from": before.get(k), "to": v})
    return after


class LiveSwitches(BaseModel):
    allow_back: bool | None = None      # explicit null = inherit the interview's configured value
    gaze_enabled: bool | None = None
    gaze_bypass: bool | None = None


@app.patch("/api/interviews/{iid}/live", dependencies=[Depends(recruiter)])
def set_live(iid: IID, body: LiveSwitches):
    iv = interview.get(iid)
    if not iv:
        raise HTTPException(404, "Not found.")
    cur = flags.live_of(iv)
    before = dict(cur)
    for k in body.model_fields_set:
        cur[k] = getattr(body, k)
    cur["gaze_bypass"] = bool(cur["gaze_bypass"])
    db.update_interview(iid, live=cur)
    for k in ("allow_back", "gaze_enabled", "gaze_bypass"):
        if k in body.model_fields_set and before.get(k) != cur.get(k):
            audit.log(iid, "interviewer", "setting_changed", {"setting": k, "from": before.get(k), "to": cur.get(k)})
    iv = interview.get(iid)
    st = iv["state"] or {}
    if st.get("viewing") is not None and not flags.effective(iv)["allow_back"]:   # going back switched off while the candidate was on an old question
        st["viewing"] = None
        db.update_interview(iid, state=st)
    return {"live": cur, "effective": flags.effective(interview.get(iid))}


@app.get("/api/candidate/{token}/flags")
def cand_flags(token: str):
    iv = interview.sweep(candidate(token))
    eff = flags.effective(iv)
    msgs = flags.live_of(iv)["messages"]
    return {"status": iv["status"], "allow_back": eff["allow_back"], "gaze": eff["gaze"], "calibrated": flags.calibrated(iv),
            "duration_min": eff["duration_min"], "recruiters_present": live.present(iv["id"], "recruiter"),
            "message": ({"id": msgs[-1]["id"], "text": msgs[-1]["text"]} if msgs else None)}


class Calibration(BaseModel):
    ok: bool
    mode: str = Field("", max_length=24, pattern=r"^[a-z+ -]*$")
    grade: str = Field("", max_length=12, pattern=r"^[a-z]*$")
    error: float | None = Field(None, ge=0, le=5)
    reason: str = Field("", max_length=120)


@app.post("/api/candidate/{token}/gaze/calibration")
def cand_calibration(token: str, body: Calibration):
    """Client-reported (a browser cannot be forced to be honest): it gates the start button and is shown to the recruiter."""
    iv = candidate(token)
    st = iv["state"] or {}
    reason = "".join(c for c in body.reason if c.isalnum() or c in " ,.'-")
    st["calibration"] = {"ok": body.ok, "mode": body.mode, "grade": body.grade, "error": body.error, "reason": reason, "at": interview.now()}
    db.update_interview(iv["id"], state=st)
    audit.log(iv["id"], "candidate", "eye_calibration", {"ok": body.ok, "grade": body.grade, "mode": body.mode, "error": body.error, "reason": reason})
    return {"ok": True}


class Goto(BaseModel):
    idx: int = Field(ge=0, le=100)


@app.post("/api/candidate/{token}/goto")
def cand_goto(token: str, body: Goto):
    iv = candidate(token)
    try:
        return interview.goto(iv, body.idx)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/candidate/{token}/return")
def cand_return(token: str):
    iv = candidate(token)
    if iv["status"] != "in_progress":
        raise HTTPException(409, "Interview is not in progress.")
    return interview.return_to_current(iv)


@app.get("/api/interviews/{iid}/live-status", dependencies=[Depends(recruiter)])
def live_status(iid: IID):
    iv = interview.get(iid)
    if not iv:
        raise HTTPException(404, "Not found.")
    iv = interview.sweep(iv)
    cur = interview.current(iv) if iv["status"] == "in_progress" else None
    recent = db.many("SELECT id, t_offset, type, kind, severity, description FROM events WHERE interview_id=? ORDER BY id DESC LIMIT 8", (iid,))
    return {"status": iv["status"], "candidate": iv["candidate"]["name"], "job_title": iv["job"]["title"],
            "current": ({"number": cur.get("number"), "total": cur.get("total"), "question": cur.get("question"), "is_followup": cur.get("is_followup"),
                         "revisiting": cur.get("revisiting"), "remaining_s": cur.get("remaining_s"), "progress": cur.get("progress")} if cur and not cur.get("done") else None),
            "events": recent, "effective": flags.effective(iv), "live": flags.live_of(iv), "calibration": (iv["state"] or {}).get("calibration"),
            "call": {"candidate_connected": live.present(iid, "candidate") > 0, "recruiters": live.present(iid, "recruiter"), "phone": signaling.phone_connected(iid)}}


@app.websocket("/ws/live/{iid}/{role}")
async def ws_live(ws: WebSocket, iid: str, role: str, token: str | None = Query(None)):
    iv = interview.get(iid) if re.fullmatch(r"[0-9a-f]{12}", iid) else None
    if not iv or role not in ("candidate", "recruiter"):
        await ws.close(code=4404)
        return
    if role == "candidate":
        authed = secrets.compare_digest((token or "").encode(), iv["token"].encode())
    else:                                              # browsers send the sign-in cookie on the WebSocket handshake
        authed = _cookie_ok(ws.cookies.get(COOKIE)) or _key_ok(ws.headers.get("x-recruiter-key"))
    if not authed:
        await ws.close(code=4401)
        return
    await ws.accept()
    peer = await live.join(iid, role, ws)
    if role == "recruiter":
        audit.log(iid, "interviewer", "joined_call", {})
        proctor.record(iid, {"type": "recruiter_joined", "t_offset": offset(interview.get(iid))}, db.DATA_DIR)
    try:
        while True:
            text = await ws.receive_text()
            if len(text) > live.MAX_MESSAGE:
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") in ("offer", "answer", "ice", "meta", "state"):
                await live.relay(iid, peer, msg)
    except WebSocketDisconnect:
        pass
    finally:
        if await live.leave(iid, peer) and role == "recruiter":
            audit.log(iid, "interviewer", "left_call", {})
            proctor.record(iid, {"type": "recruiter_left", "t_offset": offset(interview.get(iid))}, db.DATA_DIR)


# ----------------------------------------------- running-interview controls: time, messages, ending
class Extend(BaseModel):
    minutes: int = Field(ge=1, le=60)


@app.post("/api/interviews/{iid}/extend", dependencies=[Depends(recruiter)])
def extend(iid: IID, body: Extend):
    iv = interview.get(iid)
    if not iv:
        raise HTTPException(404, "Not found.")
    if iv["status"] == "completed":
        raise HTTPException(409, "This interview has already finished.")
    cur = flags.live_of(iv)
    if cur["extra_minutes"] + body.minutes > 120:
        raise HTTPException(409, "An interview can be extended by at most 120 minutes in total.")
    cur["extra_minutes"] += body.minutes
    db.update_interview(iid, live=cur)
    audit.log(iid, "interviewer", "time_extended", {"minutes": body.minutes, "total_extra": cur["extra_minutes"]})
    proctor.record(iid, {"type": "time_extended", "t_offset": offset(iv)}, db.DATA_DIR)
    return {"duration_min": interview.duration_min(interview.get(iid)), "extra_minutes": cur["extra_minutes"]}


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=300)


@app.post("/api/interviews/{iid}/message", dependencies=[Depends(recruiter)])
def send_message(iid: IID, body: Message):
    """A short note shown on the candidate's screen (they pick it up within a few seconds). Kept in the record."""
    iv = interview.get(iid)
    if not iv:
        raise HTTPException(404, "Not found.")
    if iv["status"] == "completed":
        raise HTTPException(409, "This interview has already finished.")
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Write a message first.")
    cur = flags.live_of(iv)
    mid = (cur["messages"][-1]["id"] + 1) if cur["messages"] else 1
    cur["messages"] = (cur["messages"] + [{"id": mid, "text": text, "at": interview.now()}])[-20:]
    db.update_interview(iid, live=cur)
    audit.log(iid, "interviewer", "message_sent", {"text": text})
    proctor.record(iid, {"type": "recruiter_message", "t_offset": offset(iv)}, db.DATA_DIR)
    return {"id": mid}


@app.post("/api/interviews/{iid}/end", dependencies=[Depends(recruiter)])
def end_now(iid: IID):
    """Finish a running interview immediately: unanswered questions are marked, scoring runs, the candidate's page closes."""
    iv = interview.get(iid)
    if not iv:
        raise HTTPException(404, "Not found.")
    if iv["status"] == "created":
        raise HTTPException(409, "The candidate has not started yet. Delete the interview if you no longer need it.")
    if iv["status"] == "in_progress":
        proctor.record(iid, {"type": "ended_by_recruiter", "t_offset": offset(iv)}, db.DATA_DIR)
        interview.finish(iv, by="interviewer")
    return {"status": "completed"}


# ------------------------------------------------------------------------- exports
def _csv_cell(v) -> str:
    """Spreadsheet programs run cells that start with = + - @ as formulas; neutralise them (candidate answers are untrusted text)."""
    t = "" if v is None else str(v)
    return "'" + t if t[:1] in ("=", "+", "-", "@", "\t", "\r") else t


@app.get("/api/interviews/{iid}/export.json", dependencies=[Depends(recruiter)])
def export_json(iid: IID, request: Request):
    data = _detail(iid, request)
    data.pop("candidate_url", None)                    # the secret link is not part of an export
    audit.log(iid, "interviewer", "exported", {"format": "json"})
    data["audit"] = {"verify": audit.verify(iid), "entries": audit.entries(iid)}
    return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="interview-{iid}.json"'})


@app.get("/api/interviews/{iid}/export.csv", dependencies=[Depends(recruiter)])
def export_csv(iid: IID, request: Request):
    d = _detail(iid, request)
    ev = d["evaluation"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["question_no", "category", "skill", "difficulty", "status", "final_score", "ai_score", "override_note", "revisions", "question", "answer", "explanation"])
    for p in (ev["per_question"] if ev else []):
        w.writerow([_csv_cell(x) for x in (p["idx"] + 1, p["category"], p["skill"], p["difficulty"], p["status"], p["score"], p.get("ai_score"),
                                           (p.get("override") or {}).get("note"), p.get("revisions", 0), p["question"], p["answer"], p["explanation"])])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="interview-{iid}.csv"'})
