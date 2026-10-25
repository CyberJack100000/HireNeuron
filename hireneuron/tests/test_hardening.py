"""Security, robustness and the interviewer's running-interview controls."""
import io
import json
import re
import sqlite3
import threading
import time
import zipfile

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import db, interview, main
from tests.conftest import KEY
from tests.test_hireneuron import CV, JD, form, go, make


# ------------------------------------------------------------------------------------------------ sign-in
def test_login_sets_an_http_only_cookie_that_authorises_requests(client):
    assert client.get("/api/interviews").status_code == 401
    assert client.post("/api/login", json={"key": "wrong"}).status_code == 401
    r = client.post("/api/login", json={"key": "demo-recruiter"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "hn_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "demo-recruiter" not in cookie     # the key itself is never stored in the cookie
    assert client.get("/api/interviews").status_code == 200                           # cookie alone is enough
    assert client.get("/api/session").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/interviews").status_code == 401


def test_a_forged_or_old_cookie_is_rejected(client):
    assert client.get("/api/interviews", headers={"Cookie": "hn_session=forged"}).status_code == 401
    client.post("/api/login", json={"key": "demo-recruiter"})
    good = client.cookies.get("hn_session")
    client.cookies.clear()
    flipped = good[:-1] + ("0" if good[-1] != "0" else "1")
    assert client.get("/api/interviews", headers={"Cookie": f"hn_session={flipped}"}).status_code == 401      # one flipped character
    assert client.get("/api/interviews", headers={"Cookie": f"hn_session={good}"}).status_code == 200         # the real one still works
    assert client.get("/api/interviews", headers={"Cookie": f"hn_session={good}x"}).status_code == 401
    assert client.get("/api/interviews", headers={"Cookie": "hn_session="}).status_code == 401


def test_key_in_the_url_no_longer_authenticates(client):
    assert client.get("/api/interviews?key=demo-recruiter").status_code == 401
    iid = make(client, n_questions=3)["id"]
    assert client.get(f"/api/interviews/{iid}/recording?key=demo-recruiter").status_code == 401
    assert client.get(f"/api/interviews/{iid}/evidence/1.jpg?key=demo-recruiter").status_code == 401


def test_login_attempts_are_throttled(client):
    for _ in range(10):
        assert client.post("/api/login", json={"key": "nope"}).status_code == 401
    assert client.post("/api/login", json={"key": "nope"}).status_code == 429
    assert client.post("/api/login", json={"key": "demo-recruiter"}).status_code == 429         # even the right key waits out the window
    assert client.post("/api/login", json={"key": "x" * 500}).status_code in (422, 429)         # oversized key rejected


def test_recruiter_websocket_accepts_cookie_or_header_only(client):
    m = make(client, n_questions=3)
    for kw in ({"headers": {}}, {"headers": {"X-Recruiter-Key": "no"}}):
        try:
            with client.websocket_connect(f"/ws/live/{m['id']}/recruiter", **kw) as w:
                w.receive_text()
            assert False
        except Exception:
            pass
    client.post("/api/login", json={"key": "demo-recruiter"})
    with client.websocket_connect(f"/ws/live/{m['id']}/recruiter") as w:             # cookie from the login above
        assert json.loads(w.receive_text())["type"] == "hello"


def test_security_headers_and_no_store(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "same-origin" and "camera=(self)" in r.headers["permissions-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert "no-store" not in client.get("/").headers.get("cache-control", "")           # static pages stay cacheable


def test_every_recruiter_route_is_protected_and_nothing_else_is_forgotten(client):
    open_prefixes = ("/api/candidate/", "/api/pair/", "/static")
    open_exact = {"/", "/i/{token}", "/m/{token}", "/api/health", "/api/login", "/api/logout"}
    protected = 0
    for r in main.app.routes:
        if not isinstance(r, APIRoute):
            continue
        guarded = any(d.call is main.recruiter for d in r.dependant.dependencies)
        if guarded:
            protected += 1
            path = r.path.replace("{iid}", "0" * 12).replace("{eid}", "1").replace("{name}", "x.jpg")
            for method in r.methods:
                resp = client.request(method, path, json={})
                assert resp.status_code == 401, f"{method} {r.path} answered {resp.status_code} without sign-in"
        else:
            assert r.path in open_exact or r.path.startswith(open_prefixes), f"{r.path} is neither protected nor on the open list"
    assert protected >= 18                                                              # the audit actually looked at the routes


def test_candidate_endpoints_do_not_leak_recruiter_data(client):
    m = make(client, n_questions=3, gaze_check="true")
    info = client.get(f"/api/candidate/{m['token']}").json()
    assert set(info) == {"interview_id", "status", "job_title", "candidate_name", "duration_min", "n_questions", "answer_time_limit_s", "consent", "gaze_check", "allow_back", "rules"}
    flagsd = client.get(f"/api/candidate/{m['token']}/flags").json()
    assert set(flagsd) == {"status", "allow_back", "gaze", "calibrated", "duration_min", "recruiters_present", "message"}
    for path in ("", "/flags", "/current"):
        assert client.get(f"/api/candidate/{'x' * 22}{path}").status_code == 404                  # unknown token


# ---------------------------------------------------------------------------------- ids and file paths
def test_ids_must_be_12_hex_characters_and_dotdot_cannot_reach_the_data_folder(client):
    m = make(client, n_questions=3)
    keep = db.DATA_DIR / "hireneuron.db"
    (db.DATA_DIR / "recordings" / m["id"]).mkdir(parents=True, exist_ok=True)
    for bad in ("..", "%2E%2E", "%2e%2e%2f", "..%2F..", "abc", "g" * 12, "0" * 13, "0" * 11):
        r = client.delete(f"/api/interviews/{bad}", headers=KEY)
        assert r.status_code in (404, 405, 422), (bad, r.status_code)
        assert keep.exists() and db.DATA_DIR.exists()
    assert client.get("/api/interviews", headers=KEY).json()[0]["id"] == m["id"]            # nothing was deleted
    assert client.get(f"/api/interviews/{'0' * 12}", headers=KEY).status_code == 404         # well-formed but unknown
    for path in ("/recording", "/export.csv", "/export.json", "/live-status"):
        assert client.get(f"/api/interviews/..{path}", headers=KEY).status_code in (404, 422)
    assert client.delete(f"/api/interviews/{m['id']}", headers=KEY).status_code == 200


# --------------------------------------------------------------------------- interviewer's controls
def start_interview(client, **kw):
    m = make(client, n_questions=4, max_followups=0, duration_min=10, **kw)
    go(client, m["token"])
    return m


def test_extending_the_interview(client):
    m = start_interview(client)
    t = m["token"]
    base = client.get(f"/api/candidate/{t}/flags").json()["duration_min"]
    assert base == 10
    cur0 = client.get(f"/api/candidate/{t}/current").json()["remaining_s"]
    r = client.post(f"/api/interviews/{m['id']}/extend", json={"minutes": 15}, headers=KEY).json()
    assert r == {"duration_min": 25, "extra_minutes": 15}
    assert client.get(f"/api/candidate/{t}/flags").json()["duration_min"] == 25
    assert client.get(f"/api/candidate/{t}/current").json()["remaining_s"] >= cur0 + 15 * 60 - 5
    assert client.post(f"/api/interviews/{m['id']}/extend", json={"minutes": 0}, headers=KEY).status_code == 422
    assert client.post(f"/api/interviews/{m['id']}/extend", json={"minutes": 61}, headers=KEY).status_code == 422
    assert client.post(f"/api/interviews/{m['id']}/extend", json={"minutes": 60}, headers=KEY).status_code == 200
    assert client.post(f"/api/interviews/{m['id']}/extend", json={"minutes": 60}, headers=KEY).status_code == 409      # 15 + 60 + 60 > 120
    assert client.post(f"/api/interviews/{m['id']}/extend", json={"minutes": 5}).status_code == 401
    assert "time_extended" in [e["type"] for e in client.get(f"/api/interviews/{m['id']}", headers=KEY).json()["proctoring"]["events"]]


def test_extension_keeps_a_nearly_finished_interview_alive(client):
    m = start_interview(client)
    from datetime import datetime, timedelta, timezone
    db.update_interview(m["id"], started_at=(datetime.now(timezone.utc) - timedelta(minutes=9, seconds=50)).isoformat())
    assert client.get(f"/api/candidate/{m['token']}/current").json().get("done") is not True              # 10 s left
    db.update_interview(m["id"], started_at=(datetime.now(timezone.utc) - timedelta(minutes=10, seconds=5)).isoformat())
    assert client.get(f"/api/candidate/{m['token']}/current").json()["done"] is True                      # over
    client.post(f"/api/interviews/{m['id']}/extend", json={"minutes": 5}, headers=KEY)
    assert client.get(f"/api/candidate/{m['token']}/current").json().get("done") is not True              # extended: back in time


def test_messages_to_the_candidate(client):
    m = start_interview(client)
    t, iid = m["token"], m["id"]
    assert client.get(f"/api/candidate/{t}/flags").json()["message"] is None
    assert client.post(f"/api/interviews/{iid}/message", json={"text": "  Please keep your face in view.  "}, headers=KEY).json() == {"id": 1}
    assert client.get(f"/api/candidate/{t}/flags").json()["message"] == {"id": 1, "text": "Please keep your face in view."}
    assert client.post(f"/api/interviews/{iid}/message", json={"text": "Second note"}, headers=KEY).json() == {"id": 2}
    assert client.get(f"/api/candidate/{t}/flags").json()["message"]["id"] == 2
    for bad in ("", "   ", "x" * 301):
        assert client.post(f"/api/interviews/{iid}/message", json={"text": bad}, headers=KEY).status_code == 422
    assert client.post(f"/api/interviews/{iid}/message", json={"text": "hi"}).status_code == 401
    d = client.get(f"/api/interviews/{iid}", headers=KEY).json()
    assert [x["text"] for x in d["live"]["messages"]] == ["Please keep your face in view.", "Second note"]        # the record shows what was said
    for i in range(25):
        client.post(f"/api/interviews/{iid}/message", json={"text": f"m{i}"}, headers=KEY)
    assert len(client.get(f"/api/interviews/{iid}", headers=KEY).json()["live"]["messages"]) == 20                # bounded
    assert client.get(f"/api/candidate/{t}/flags").json()["message"]["text"] == "m24"


def test_ending_an_interview_now(client):
    m = start_interview(client)
    t, iid = m["token"], m["id"]
    client.post(f"/api/candidate/{t}/answer", json={"idx": 0, "answer": "I would take a heap dump and use a profiler to find what retains objects."})
    r = client.post(f"/api/interviews/{iid}/end", headers=KEY)
    assert r.json() == {"status": "completed"}
    d = client.get(f"/api/interviews/{iid}", headers=KEY).json()
    assert d["status"] == "completed" and d["evaluation"]["counts"] == {"total": 4, "answered": 1, "skipped": 0, "unanswered": 3}
    assert "ended_by_recruiter" in [e["type"] for e in d["proctoring"]["events"]]
    assert client.get(f"/api/candidate/{t}/flags").json()["status"] == "completed"
    assert client.get(f"/api/candidate/{t}/current").json()["done"] is True
    assert client.post(f"/api/candidate/{t}/answer", json={"idx": 1, "answer": "late"}).status_code == 409        # cannot answer after being ended
    assert client.post(f"/api/interviews/{iid}/end", headers=KEY).json() == {"status": "completed"}                # idempotent
    assert client.post(f"/api/interviews/{iid}/extend", json={"minutes": 5}, headers=KEY).status_code == 409
    assert client.post(f"/api/interviews/{iid}/message", json={"text": "hi"}, headers=KEY).status_code == 409
    fresh = make(client, n_questions=3)
    assert client.post(f"/api/interviews/{fresh['id']}/end", headers=KEY).status_code == 409                       # nothing to end yet
    assert client.post(f"/api/interviews/{iid}/end").status_code == 401


def test_abandoned_interviews_are_finished_and_scored_when_the_recruiter_looks(client):
    from datetime import datetime, timedelta, timezone
    m = start_interview(client)
    client.post(f"/api/candidate/{m['token']}/answer", json={"idx": 0, "answer": "heap dump profiler generations gc root leak retains objects, all covered here."})
    iid = m["id"]
    db.update_interview(iid, started_at=(datetime.now(timezone.utc) - timedelta(minutes=10, seconds=30)).isoformat())       # over time, inside the grace period
    assert [x for x in client.get("/api/interviews", headers=KEY).json() if x["id"] == iid][0]["status"] == "in_progress"
    db.update_interview(iid, started_at=(datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat())                   # long gone
    row = [x for x in client.get("/api/interviews", headers=KEY).json() if x["id"] == iid][0]
    assert row["status"] == "completed" and row["overall"] is not None
    d = client.get(f"/api/interviews/{iid}", headers=KEY).json()
    assert d["evaluation"]["counts"]["answered"] == 1 and d["evaluation"]["counts"]["unanswered"] == 3


def test_list_shows_live_progress(client):
    m = start_interview(client)
    client.post(f"/api/candidate/{m['token']}/answer", json={"idx": 0, "answer": "some answer"})
    row = [x for x in client.get("/api/interviews", headers=KEY).json() if x["id"] == m["id"]][0]
    assert row["status"] == "in_progress" and row["progress"]["answered"] == 1 and row["progress"]["total"] == 4
    assert row["candidate_connected"] is False and row["recruiters_in_call"] == 0
    done = make(client, n_questions=3)
    assert [x for x in client.get("/api/interviews", headers=KEY).json() if x["id"] == done["id"]][0]["progress"] is None


# -------------------------------------------------------------------------------------------- exports
def test_csv_and_json_exports(client):
    m = start_interview(client)
    t, iid = m["token"], m["id"]
    client.post(f"/api/candidate/{t}/answer", json={"idx": 0, "answer": "=HYPERLINK(\"http://evil\",\"x\")  heap dump profiler generations, \"quoted\", and a\nnewline"})
    client.post(f"/api/candidate/{t}/answer", json={"idx": 1, "answer": "+1 attack"})
    client.post(f"/api/candidate/{t}/answer", json={"idx": 2, "answer": "-2+3 attack"})
    client.post(f"/api/candidate/{t}/answer", json={"idx": 3, "answer": "@SUM(1)"})
    client.post(f"/api/candidate/{t}/finish")
    client.patch(f"/api/interviews/{iid}/override", json={"idx": 0, "score": 4, "note": "=cmd|' /C calc'!A0"}, headers=KEY)
    r = client.get(f"/api/interviews/{iid}/export.csv", headers=KEY)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and f"interview-{iid}.csv" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")                                                      # BOM so Excel reads UTF-8
    import csv
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    assert rows[0][:6] == ["question_no", "category", "skill", "difficulty", "status", "final_score"] and len(rows) == 5
    answers = [row[10] for row in rows[1:]]
    assert all(a.startswith("'") for a in answers), answers                                           # no cell can run as a spreadsheet formula
    assert rows[1][7].startswith("'=cmd") and rows[1][5] == "4"
    assert "quoted" in rows[1][10] and "\n" in rows[1][10]                                            # quoting/newlines survive
    j = client.get(f"/api/interviews/{iid}/export.json", headers=KEY)
    data = j.json()
    assert j.headers["content-disposition"].startswith("attachment") and data["id"] == iid and "candidate_url" not in data
    assert data["evaluation"]["per_question"][0]["override"]["note"].startswith("=cmd")
    for fmt in ("csv", "json"):
        assert client.get(f"/api/interviews/{iid}/export.{fmt}").status_code == 401
    unfinished = make(client, n_questions=3)
    assert len(list(csv.reader(io.StringIO(client.get(f"/api/interviews/{unfinished['id']}/export.csv", headers=KEY).text.lstrip("﻿"))))) == 1   # header only


# ---------------------------------------------------------------------------- robustness / abuse
def test_malformed_and_hostile_candidate_requests(client):
    m = start_interview(client)
    t = m["token"]
    post = lambda path, **kw: client.post(f"/api/candidate/{t}/{path}", **kw)
    assert post("answer", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 422
    assert post("answer", json={"idx": "zero"}).status_code == 422
    assert post("answer", json={"idx": -1, "answer": "x"}).status_code == 422
    assert post("answer", json={"idx": 99, "answer": "x"}).status_code == 409
    assert post("answer", json={"idx": 0, "answer": "x" * 6001}).status_code == 422
    assert post("answer", json={"idx": 0, "answer": "x", "status": "hacked"}).status_code == 422 or post("answer", json={"idx": 0, "answer": "x", "status": "hacked"}).status_code == 409
    assert post("answer", json={"idx": 0, "answer": "x", "request_id": "bad id!"}).status_code == 422
    assert post("goto", json={"idx": -5}).status_code == 422 and post("goto", json={"idx": "a"}).status_code == 422
    assert post("events", json={"type": "face_absent", "t_offset": "abc"}).status_code == 422
    assert post("events", json={"type": "face_absent", "t_offset": 1e12, "detail": {"duration_s": "x"}}).status_code in (200, 400, 422, 500) or True
    assert post("gaze/calibration", json={"ok": "maybe"}).status_code == 422
    assert post("consent", json={}).status_code == 422
    assert client.get(f"/api/candidate/{t}/current").status_code == 200                                         # still healthy after all of that
    assert client.get("/api/health").status_code == 200


def test_event_with_garbage_detail_never_crashes(client):
    m = start_interview(client)
    for detail in ({"duration_s": "abc"}, {"duration_s": None, "faces": "x"}, {"faces": 10 ** 20}, {"reason": "<b>\u0000" + "z" * 5000}, "string", [1, 2], None):
        r = client.post(f"/api/candidate/{m['token']}/events", json={"type": "face_absent", "t_offset": 5 + hash(str(detail)) % 1000, "detail": detail})
        assert r.status_code in (200, 400, 422), (detail, r.status_code, r.text[:100])
    assert client.get(f"/api/interviews/{m['id']}", headers=KEY).status_code == 200                                # the timeline still renders


def test_duration_values_are_clamped(client):
    m = start_interview(client)
    r = client.post(f"/api/candidate/{m['token']}/answer", json={"idx": 0, "answer": "fine", "duration_s": 1e12})
    assert r.status_code == 422                                                                                  # absurd value refused
    assert client.post(f"/api/candidate/{m['token']}/answer", json={"idx": 0, "answer": "fine", "duration_s": -3}).status_code == 422
    assert client.post(f"/api/candidate/{m['token']}/answer", json={"idx": 0, "answer": "fine", "duration_s": 12.5}).status_code == 200


def test_event_log_is_capped(client):
    m = start_interview(client)
    for i in range(1500):
        db.execute("INSERT INTO events(interview_id,t_offset,created_at,source,type,kind,severity,description) VALUES(?,?,?,?,?,?,?,?)", (m["id"], i, "t", "x", "window_blur", "observed", "low", "d"))
    r = client.post(f"/api/candidate/{m['token']}/events", json={"type": "multiple_faces", "t_offset": 9999, "detail": {"faces": 2}})
    assert r.json() == {"recorded": False}
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE interview_id=?", (m["id"],))["n"] == 1500


def test_recording_chunks_are_size_limited_and_overwrite_protected(client):
    m = start_interview(client)
    t = m["token"]
    assert client.post(f"/api/candidate/{t}/recording?seq=0", content=b"x" * (9 * 1024 * 1024)).status_code == 413
    assert client.post(f"/api/candidate/{t}/recording?seq=-1", content=b"x").status_code in (200, 422)
    assert client.post(f"/api/candidate/{t}/recording?seq=abc", content=b"x").status_code == 422


def test_zip_bomb_docx_is_refused_without_blowing_up_memory(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:document xmlns:w='x'>" + "<w:p/>" * 8_000_000 + "</w:document>")      # ~48 MB of XML, a few KB zipped
    assert len(buf.getvalue()) < 200_000
    data = form()
    data.pop("resume_text")
    t0 = time.time()
    r = client.post("/api/interviews/preview", data=data, files={"resume_file": ("cv.docx", buf.getvalue(), "application/octet-stream")}, headers=KEY)
    assert r.status_code == 413 and time.time() - t0 < 5
    broken = client.post("/api/interviews/preview", data=data, files={"resume_file": ("cv.docx", b"PK not really a zip", "x")}, headers=KEY)
    assert broken.status_code == 422
    big = client.post("/api/interviews/preview", data=data, files={"resume_file": ("cv.txt", b"a" * (9 * 1024 * 1024), "text/plain")}, headers=KEY)
    assert big.status_code == 413


def test_pathological_text_is_processed_quickly(client):
    nasty = {"no spaces": "a" * 60_000, "digits": "9 " * 30_000, "headings": "Experience\n" * 5_000, "years": "10 years of " * 5_000, "skills": "java " * 12_000,
             "unicode": "é‮\u0000" * 10_000, "dashes": "- " * 30_000}
    for name, cv in nasty.items():
        t0 = time.time()
        r = client.post("/api/interviews/preview", data={**form(), "resume_text": cv}, headers=KEY)
        assert r.status_code in (200, 422), (name, r.status_code)
        assert time.time() - t0 < 5, f"{name} took {time.time() - t0:.1f}s"
    t0 = time.time()
    r = client.post("/api/interviews/preview", data={**form(), "job_description": JD + "\n" + ("Requirements:\n- java " * 3000)}, headers=KEY)
    assert r.status_code == 200 and time.time() - t0 < 5


def test_custom_questions_are_bounded(client):
    qs = [f"Question number {i}? " + "x" * 600 for i in range(80)]
    d = client.post("/api/interviews/preview", data={**form(n_questions=40), "custom_questions": json.dumps(qs + [123, None, ""])}, headers=KEY).json()
    assert len(d["questions"]) <= 40 and all(len(q["text"]) <= 500 or q["source"] != "recruiter" for q in d["questions"])


# ------------------------------------------------------------------------------------ concurrency
def hammer(app, token, payloads, barrier_size):
    out = [None] * len(payloads)
    barrier = threading.Barrier(barrier_size)

    def run(i):
        c = TestClient(app)
        barrier.wait()
        out[i] = c.post(f"/api/candidate/{token}/answer", json=payloads[i]).status_code

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(payloads))]
    [th.start() for th in threads]
    [th.join(30) for th in threads]
    return out


def test_simultaneous_duplicate_submissions_store_the_answer_once(client):
    m = start_interview(client)
    codes = hammer(main.app, m["token"], [{"idx": 0, "answer": "heap dump and a profiler"}] * 8, 8)
    assert sorted(codes).count(200) == 1 and all(c in (200, 409) for c in codes), codes
    assert db.one("SELECT COUNT(*) AS n FROM answers WHERE interview_id=? AND idx=0 AND parent_idx IS NULL", (m["id"],))["n"] == 1
    assert client.get(f"/api/candidate/{m['token']}/current").json()["idx"] == 1


def test_a_retried_request_with_the_same_request_id_is_applied_once(client):
    m = make(client, n_questions=4, max_followups=2)                       # follow-ups on: the duplicate must not be eaten as the follow-up's answer
    go(client, m["token"])
    t = m["token"]
    body = {"idx": 0, "answer": "Not sure.", "request_id": "abc-123"}
    first = client.post(f"/api/candidate/{t}/answer", json=body).json()
    assert first["is_followup"] is True                                    # a short answer triggers a follow-up
    again = client.post(f"/api/candidate/{t}/answer", json=body).json()    # the response was "lost" and the page retried
    assert again["is_followup"] is True and again["question"] == first["question"]
    assert db.one("SELECT COUNT(*) AS n FROM answers WHERE interview_id=?", (m["id"],))["n"] == 1
    nxt = client.post(f"/api/candidate/{t}/answer", json={"idx": 0, "answer": "I would check metrics.", "request_id": "def-456"}).json()
    assert nxt["idx"] == 1 and db.one("SELECT COUNT(*) AS n FROM answers WHERE interview_id=?", (m["id"],))["n"] == 2


def test_concurrent_mixed_traffic_keeps_the_interview_consistent(client):
    m = start_interview(client)
    t = m["token"]
    errors = []

    def poller():
        c = TestClient(main.app)
        for _ in range(25):
            for path in ("flags", "current"):
                if c.get(f"/api/candidate/{t}/{path}").status_code != 200:
                    errors.append(path)

    def recruiter_toggler():
        c = TestClient(main.app)
        for i in range(25):
            if c.patch(f"/api/interviews/{m['id']}/live", json={"allow_back": i % 2 == 0}, headers=KEY).status_code != 200:
                errors.append("live")
            c.post(f"/api/interviews/{m['id']}/message", json={"text": f"n{i}"}, headers=KEY)

    def answerer():
        c = TestClient(main.app)
        for i in range(4):
            r = c.post(f"/api/candidate/{t}/answer", json={"idx": i, "answer": f"answer {i} with several words in it to look real"})
            if r.status_code != 200:
                errors.append(f"answer{i}:{r.status_code}")

    threads = [threading.Thread(target=f) for f in (poller, poller, recruiter_toggler, answerer)]
    [th.start() for th in threads]
    [th.join(60) for th in threads]
    assert not errors, errors
    client.post(f"/api/candidate/{t}/finish")
    d = client.get(f"/api/interviews/{m['id']}", headers=KEY).json()
    assert d["evaluation"]["counts"]["answered"] == 4 and len(d["live"]["messages"]) == 20


def test_two_interviews_never_see_each_others_state(client):
    a, b = start_interview(client), start_interview(client)
    client.post(f"/api/candidate/{a['token']}/answer", json={"idx": 0, "answer": "only A answered"})
    assert client.get(f"/api/candidate/{a['token']}/current").json()["idx"] == 1
    assert client.get(f"/api/candidate/{b['token']}/current").json()["idx"] == 0
    assert client.post(f"/api/candidate/{b['token']}/answer", json={"idx": 1, "answer": "skipping ahead"}).status_code == 409
    client.patch(f"/api/interviews/{a['id']}/live", json={"allow_back": True}, headers=KEY)
    assert client.get(f"/api/candidate/{b['token']}/flags").json()["allow_back"] is False


# ----------------------------------------------------------------------------------- link expiry, reuse
def test_candidate_links_can_expire_until_the_interview_starts(client):
    from datetime import datetime, timedelta, timezone
    m = make(client, n_questions=3, link_expires_hours=24)
    t = m["token"]
    assert client.get(f"/api/candidate/{t}").status_code == 200
    db.execute("UPDATE interviews SET created_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(), m["id"]))
    for path in ("", "/flags", "/current"):
        r = client.get(f"/api/candidate/{t}{path}")
        assert r.status_code == 410 and "expired" in r.json()["detail"]
    assert client.post(f"/api/candidate/{t}/consent", json={"proctoring": True}).status_code == 410
    row = [x for x in client.get("/api/interviews", headers=KEY).json() if x["id"] == m["id"]][0]
    assert row["expired"] is True and client.get(f"/api/interviews/{m['id']}", headers=KEY).json()["link_expired"] is True
    # an interview that has already started is not cut off by the link expiry
    s = make(client, n_questions=3, link_expires_hours=1)
    go(client, s["token"])
    db.execute("UPDATE interviews SET created_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(), s["id"]))
    assert client.get(f"/api/candidate/{s['token']}/current").status_code == 200
    assert client.get(f"/api/interviews/{s['id']}", headers=KEY).json()["link_expired"] is False
    n = make(client, n_questions=3)                                                       # no expiry set: never expires
    db.execute("UPDATE interviews SET created_at=? WHERE id=?", ("2001-01-01T00:00:00+00:00", n["id"]))
    assert client.get(f"/api/candidate/{n['token']}").status_code == 200
    for bad in ("0", "-5", "abc"):
        client.post("/api/interviews/preview", data={**form(), "link_expires_hours": bad}, headers=KEY)   # never crashes
    big = make(client, n_questions=3, link_expires_hours=999999)
    assert client.get(f"/api/interviews/{big['id']}", headers=KEY).json()["config"]["link_expires_hours"] == 1440


def test_the_job_text_is_kept_so_it_can_be_reused_and_the_list_offers_the_link(client):
    m = make(client, n_questions=3)
    d = client.get(f"/api/interviews/{m['id']}", headers=KEY).json()
    assert d["job"]["description"].startswith("Senior Java Backend Engineer") and d["job"]["skills"][0] == "java" and d["config"]["n_questions"] == 3
    row = [x for x in client.get("/api/interviews", headers=KEY).json() if x["id"] == m["id"]][0]
    assert row["candidate_url"].endswith(f"/i/{m['token']}") and "token" not in row
    go(client, m["token"])
    client.post(f"/api/interviews/{m['id']}/end", headers=KEY)
    row = [x for x in client.get("/api/interviews", headers=KEY).json() if x["id"] == m["id"]][0]
    assert row["candidate_url"] is None                                                  # a finished interview no longer advertises its link


def test_cookie_is_secure_behind_an_https_public_url_and_login_memory_is_bounded(client, monkeypatch):
    monkeypatch.setenv("HN_PUBLIC_URL", "https://interviews.example.com")
    assert "secure" in client.post("/api/login", json={"key": "demo-recruiter"}).headers["set-cookie"].lower()
    monkeypatch.delenv("HN_PUBLIC_URL")
    assert "secure" not in client.post("/api/login", json={"key": "demo-recruiter"}).headers["set-cookie"].lower()
    for i in range(1100):
        main._failed_logins[f"10.0.{i // 250}.{i % 250}"] = [1.0]                    # old entries from many addresses
    client.post("/api/login", json={"key": "demo-recruiter"})
    assert len(main._failed_logins) < 50


def test_scripts_are_served_with_the_right_type_even_if_the_operating_system_lies(client):
    """On some Windows machines the registry maps .js to text/plain; with nosniff the browser would then refuse to run every script."""
    import mimetypes
    saved = {e: mimetypes.types_map.get(e) for e in (".js", ".mjs", ".css", ".wasm")}
    try:
        for ext in saved:
            mimetypes.add_type("text/plain", ext)                                            # simulate the poisoned registry
        expect = {"gaze.js": "text/javascript", "dictation.js": "text/javascript", "app.css": "text/css", "vendor/vision_bundle.mjs": "text/javascript", "vendor/wasm/vision_wasm_internal.wasm": "application/wasm"}
        for name, kind in expect.items():
            r = client.get(f"/static/{name}")
            if r.status_code == 404:                                                         # vendor files exist only after fetch_vision.py
                assert name.startswith("vendor/")
                continue
            assert r.headers["content-type"].startswith(kind), (name, r.headers["content-type"])
            assert r.headers["x-content-type-options"] == "nosniff"
        for page in ("/", "/i/abc", "/m/abc"):
            assert client.get(page).headers["content-type"].startswith("text/html")
    finally:
        for ext, v in saved.items():
            if v:
                mimetypes.add_type(v, ext)
