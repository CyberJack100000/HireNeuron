import base64
import io
import json
import zipfile
from pathlib import Path

from app import db, demo, evaluate, interview, llm
from tests.conftest import KEY

DATA = Path(__file__).resolve().parent.parent / "data"
JD = (DATA / "sample_jd.txt").read_text(encoding="utf-8")
CV = (DATA / "sample_resume.txt").read_text(encoding="utf-8")


def form(**kw):
    base = {"job_title": "Senior Java Backend Engineer", "job_description": JD, "skills": "java,spring boot", "resume_text": CV}
    base.update({k: str(v) for k, v in kw.items()})
    return base


def make(client, **kw):
    r = client.post("/api/interviews", data=form(**kw), headers=KEY)
    assert r.status_code == 200, r.text
    return r.json()


def go(client, token, consent=None):
    c = client.post(f"/api/candidate/{token}/consent", json=consent or {"recording": True, "proctoring": True, "secondary_camera": True})
    assert c.status_code == 200
    return client.post(f"/api/candidate/{token}/start").json()


def test_auth_required(client):
    assert client.get("/api/interviews").status_code == 401
    assert client.get("/api/interviews", headers={"X-Recruiter-Key": "wrong"}).status_code == 401
    assert client.get("/api/interviews", headers=KEY).status_code == 200


def test_preview_extracts_and_defaults_to_ten_questions(client):
    d = client.post("/api/interviews/preview", data=form(), headers=KEY).json()
    assert len(d["questions"]) == 10
    assert "java" in d["resume"]["skills"] and d["resume"]["name"] == "Souparna Saha"
    assert d["match"]["missing"] == ["kubernetes"]
    texts = [q["text"] for q in d["questions"]]
    assert len(set(texts)) == 10
    assert any("kubernetes" in t.lower() for t in texts)          # resume gap probed
    assert any("payments gateway" in t.lower() for t in texts)    # grounded in the resume
    assert {q["category"] for q in d["questions"]} >= {"technical", "behavioral", "experience", "scenario"}


def test_question_count_custom_and_difficulty(client):
    d = client.post("/api/interviews/preview", data=form(n_questions=6, custom_questions=json.dumps(["Why payments?"])), headers=KEY).json()
    assert len(d["questions"]) == 6 and d["questions"][0]["source"] == "recruiter"
    d = client.post("/api/interviews/preview", data=form(n_questions=15, level="junior"), headers=KEY).json()
    assert len(d["questions"]) == 15
    tech = [q for q in d["questions"] if q["category"] == "technical"]
    assert all(q["difficulty"] in ("basic", "intermediate") for q in tech)
    d = client.post("/api/interviews/preview", data=form(level="senior"), headers=KEY).json()
    assert any(q["difficulty"] == "advanced" for q in d["questions"])


def test_docx_resume_upload(client):
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(f"<w:p><w:r><w:t>{l}</w:t></w:r></w:p>" for l in CV.splitlines()) + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    data = form()
    data.pop("resume_text")
    r = client.post("/api/interviews/preview", data=data, files={"resume_file": ("cv.docx", buf.getvalue(), "application/octet-stream")}, headers=KEY)
    assert r.status_code == 200 and "spring boot" in r.json()["resume"]["skills"]
    assert client.post("/api/interviews/preview", data=data, files={"resume_file": ("cv.exe", b"x", "x")}, headers=KEY).status_code == 415


def test_consent_gate_and_interview_flow_with_followup_cap(client):
    m = make(client, n_questions=4, max_followups=1)
    t = m["token"]
    assert client.post(f"/api/candidate/{t}/start").status_code == 409            # no consent yet
    assert client.post(f"/api/candidate/{t}/consent", json={"proctoring": False}).status_code == 400
    cur = go(client, t)
    assert cur["idx"] == 0 and cur["total"] == 4 and not cur["is_followup"]
    # a very short answer triggers exactly one follow-up (cap = 1)
    nxt = client.post(f"/api/candidate/{t}/answer", json={"idx": 0, "answer": "Not sure."}).json()
    assert nxt["is_followup"] and nxt["idx"] == 0
    nxt = client.post(f"/api/candidate/{t}/answer", json={"idx": 0, "answer": "I would check it."}).json()
    assert nxt["idx"] == 1 and not nxt["is_followup"]
    nxt = client.post(f"/api/candidate/{t}/answer", json={"idx": 1, "answer": "short"}).json()
    assert not nxt["is_followup"] and nxt["idx"] == 2                              # cap reached: interview not extended
    assert client.post(f"/api/candidate/{t}/answer", json={"idx": 0, "answer": "x"}).status_code == 409   # cannot go back
    nxt = client.post(f"/api/candidate/{t}/answer", json={"idx": 2, "status": "skipped"}).json()
    assert nxt["idx"] == 3
    client.post(f"/api/candidate/{t}/finish")
    r = client.get(f"/api/interviews/{m['id']}", headers=KEY).json()
    c = r["evaluation"]["counts"]
    assert c == {"total": 4, "answered": 2, "skipped": 1, "unanswered": 1}
    assert r["status"] == "completed" and len(r["questions"]) == 4


def test_no_followups_when_disabled(client):
    m = make(client, n_questions=3, max_followups=0)
    go(client, m["token"])
    nxt = client.post(f"/api/candidate/{m['token']}/answer", json={"idx": 0, "answer": "ok"}).json()
    assert nxt["idx"] == 1 and not nxt["is_followup"]


def test_scoring_is_evidence_based_and_ignores_style():
    q = {"text": "Explain GC", "category": "technical", "difficulty": "basic", "skill": "java",
         "expected": ["heap|generation", "gc root|reachab", "heap dump|profiler", "leak|retain"]}
    strong = evaluate.heuristic_grade(q["text"], q["expected"], "The heap is split into generations. Reachability from a GC root decides what is collected. "
                                      "To find a leak I take a heap dump and use a profiler to see what retains objects.")
    weak = evaluate.heuristic_grade(q["text"], q["expected"], "I think it cleans memory.")
    assert strong["score"] >= 4 and weak["score"] == 1
    assert strong["evidence"] and all(e in "The heap is split into generations. Reachability from a GC root decides what is collected. To find a leak I take a heap dump and use a profiler to see what retains objects." for e in strong["evidence"])
    assert "Did not mention" in weak["explanation"]
    # same content, different delivery (filler words, casing) -> same score
    a = evaluate.heuristic_grade(q["text"], q["expected"], "um so the HEAP has generations uh and reachability from a gc root and a heap dump with a profiler to find a leak that retains stuff yeah")
    assert a["score"] == strong["score"]


def test_llm_grader_must_quote_the_answer_verbatim():
    class Msg:
        def __init__(self, t): self.content = [type("B", (), {"text": t})()]
    class Stub:
        def __init__(self, payload): self.payload = payload
        class messages:  # noqa
            pass
    def stub(payload):
        s = Stub(payload)
        s.messages = type("M", (), {"create": lambda self, **k: Msg(json.dumps(payload))})()
        return s
    q = {"text": "Explain GC", "expected": ["heap"], "category": "technical", "difficulty": "basic", "skill": "java"}
    ans = "The heap is divided into generations."
    llm.set_client(stub({"score": 5, "evidence": ["The heap is divided into generations."], "missing": [], "explanation": "Good."}))
    g = evaluate.grade(q, ans)
    assert g["engine"] == "llm" and g["score"] == 5
    llm.set_client(stub({"score": 5, "evidence": ["invented quote that is not in the answer"], "missing": [], "explanation": "Great."}))
    g = evaluate.grade(q, ans)
    assert g["engine"] == "rules"                          # fabricated evidence is rejected -> fallback
    llm.set_client(stub({"score": 9}))
    assert evaluate.grade(q, ans)["engine"] == "rules"     # invalid score rejected
    llm.set_client(None)


def test_proctoring_observed_vs_inferred_and_false_alarm_control(client):
    m = make(client, n_questions=3)
    go(client, m["token"])
    t = m["token"]
    post = lambda **b: client.post(f"/api/candidate/{t}/events", json=b)
    assert post(type="nonsense").status_code == 400
    assert post(type="looking_away", t_offset=10, detail={"duration_s": 6}).json()["recorded"]
    assert not post(type="looking_away", t_offset=12, detail={"duration_s": 3}).json()["recorded"]   # cooldown: merged, not flooded
    r = client.get(f"/api/interviews/{m['id']}", headers=KEY).json()["proctoring"]
    assert r["counts"]["inferred"] == 0 and not r["needs_review"]                                     # one glance is not a suspicion
    post(type="looking_away", t_offset=60, detail={"duration_s": 8})
    post(type="looking_away", t_offset=120, detail={"duration_s": 9})
    ev = client.get(f"/api/interviews/{m['id']}", headers=KEY).json()["proctoring"]["events"]
    inferred = [e for e in ev if e["kind"] == "inferred"]
    assert len(inferred) == 1 and inferred[0]["type"] == "attention_pattern" and len(inferred[0]["basis"]) == 3
    assert "Not proof of misconduct" in inferred[0]["description"] and inferred[0]["severity"] == "medium"
    assert all(e["kind"] == "observed" for e in ev if e["type"] == "looking_away")
    # reviewing keeps the verdict with a human and survives recomputation
    client.patch(f"/api/events/{inferred[0]['id']}/review", json={"status": "dismissed", "note": "reading the question"}, headers=KEY)
    post(type="multiple_faces", t_offset=200, detail={"faces": 2})
    ev = client.get(f"/api/interviews/{m['id']}", headers=KEY).json()["proctoring"]["events"]
    att = next(e for e in ev if e["type"] == "attention_pattern")
    assert att["review"]["status"] == "dismissed"


def test_evidence_snapshot_only_accepts_jpeg(client):
    m = make(client, n_questions=3)
    go(client, m["token"])
    jpeg = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 50).decode()
    client.post(f"/api/candidate/{m['token']}/events", json={"type": "multiple_faces", "t_offset": 5, "detail": {"faces": 2}, "evidence_b64": jpeg})
    client.post(f"/api/candidate/{m['token']}/events", json={"type": "camera_lost", "t_offset": 9, "evidence_b64": base64.b64encode(b"<script>").decode()})
    ev = client.get(f"/api/interviews/{m['id']}", headers=KEY).json()["proctoring"]["events"]
    mf = next(e for e in ev if e["type"] == "multiple_faces")
    assert mf["evidence"] and client.get(f"/api/interviews/{m['id']}/evidence/{mf['evidence']}", headers=KEY).content.startswith(b"\xff\xd8\xff")
    assert next(e for e in ev if e["type"] == "camera_lost")["evidence"] is None
    assert client.get(f"/api/interviews/{m['id']}/evidence/..%2F..%2Fx.jpg", headers=KEY).status_code in (400, 404)


def test_recording_requires_consent(client):
    m = make(client, n_questions=3)
    go(client, m["token"], {"recording": False, "proctoring": True})
    assert client.post(f"/api/candidate/{m['token']}/recording?seq=0", content=b"abc").status_code == 403
    m2 = make(client, n_questions=3)
    go(client, m2["token"])
    assert client.post(f"/api/candidate/{m2['token']}/recording?seq=0", content=b"abc").status_code == 200
    assert client.post(f"/api/candidate/{m2['token']}/recording?seq=1", content=b"def").status_code == 200
    assert client.get(f"/api/interviews/{m2['id']}/recording", headers=KEY).content == b"abcdef"
    assert client.get(f"/api/interviews/{m2['id']}/recording").status_code == 401


def test_secondary_camera_pairing_logs_events_and_relays(client):
    m = make(client, n_questions=3)
    go(client, m["token"])
    pair = client.get(f"/api/candidate/{m['token']}/pair").json()
    assert pair["qr_svg"].startswith("<?xml") or "<svg" in pair["qr_svg"]
    tok = pair["pair_token"]
    assert pair["pair_url"].endswith(f"/m/{tok}")
    with client.websocket_connect(f"/ws/pair/{tok}/desktop") as desk:
        with client.websocket_connect(f"/ws/pair/{tok}/phone") as phone:
            assert json.loads(desk.receive_text())["type"] == "phone-joined"
            assert json.loads(phone.receive_text())["type"] == "desktop-ready"
            phone.send_text(json.dumps({"type": "offer", "sdp": "x"}))
            assert json.loads(desk.receive_text())["type"] == "offer"
            desk.send_text(json.dumps({"type": "answer", "sdp": "y"}))
            assert json.loads(phone.receive_text())["type"] == "answer"
        assert json.loads(desk.receive_text())["type"] == "phone-left"
    types = [e["type"] for e in client.get(f"/api/interviews/{m['id']}", headers=KEY).json()["proctoring"]["events"]]
    assert "secondary_connected" in types and "secondary_disconnected" in types
    try:
        with client.websocket_connect("/ws/pair/bogus/phone"):
            assert False
    except Exception:
        pass


def test_pairing_needs_consent(client):
    m = make(client, n_questions=3)
    go(client, m["token"], {"proctoring": True, "secondary_camera": False})
    assert client.get(f"/api/candidate/{m['token']}/pair").status_code == 403


def test_override_decision_and_delete(client):
    r = client.post("/api/demo", headers=KEY).json()
    iid = r["id"]
    d = client.get(f"/api/interviews/{iid}", headers=KEY).json()
    assert d["status"] == "completed" and d["evaluation"]["overall"] and len(d["questions"]) == 10
    before = d["evaluation"]["per_question"][0]["score"]
    client.patch(f"/api/interviews/{iid}/override", json={"idx": 0, "score": 2, "note": "Missed the key point"}, headers=KEY)
    d = client.get(f"/api/interviews/{iid}", headers=KEY).json()
    q0 = d["evaluation"]["per_question"][0]
    assert q0["score"] == 2 and q0["ai_score"] == before and q0["override"]["note"] == "Missed the key point"
    assert client.patch(f"/api/interviews/{iid}/override", json={"idx": 0, "score": 9}, headers=KEY).status_code == 422
    client.patch(f"/api/interviews/{iid}/decision", json={"decision": "hold", "notes": "Probe Kubernetes"}, headers=KEY)
    assert client.get(f"/api/interviews/{iid}", headers=KEY).json()["decision"]["decision"] == "hold"
    assert client.delete(f"/api/interviews/{iid}", headers=KEY).json() == {"deleted": True}
    assert client.get(f"/api/interviews/{iid}", headers=KEY).status_code == 404


def test_demo_report_content(client):
    d = client.get(f"/api/interviews/{client.post('/api/demo', headers=KEY).json()['id']}", headers=KEY).json()
    ev = d["evaluation"]
    assert set(ev["dimensions"]) == {"technical", "problem_solving", "communication", "role_specific"}
    assert ev["counts"]["skipped"] == 1 and ev["counts"]["answered"] == 9
    assert any("kubernetes" in f.lower() for f in ev["further_assessment"])
    assert ev["per_question"][1]["evidence"] and all(isinstance(p["explanation"], str) for p in ev["per_question"])
    kinds = {e["kind"] for e in d["proctoring"]["events"]}
    assert kinds == {"observed", "inferred"} and d["proctoring"]["needs_review"]
    assert any("followups" in p and p["followups"] for p in ev["per_question"])


def test_gaze_check_setting_and_wording(client):
    off = make(client, n_questions=3)
    assert client.get(f"/api/candidate/{off['token']}").json()["gaze_check"]["enabled"] is False
    on = make(client, n_questions=3, gaze_check="true", gaze_count=4)
    info = client.get(f"/api/candidate/{on['token']}").json()
    assert info["gaze_check"] == {"enabled": True, "required": False, "bypass": False, "count": 4, "duration_s": 8}
    assert any("blue dot" in r and "never treated as cheating" in r for r in info["rules"])     # disclosed in advance
    assert not any("blue dot" in r for r in client.get(f"/api/candidate/{off['token']}").json()["rules"])
    huge = make(client, n_questions=3, gaze_check="true", gaze_count=99)
    assert client.get(f"/api/candidate/{huge['token']}").json()["gaze_check"]["count"] == 8


def test_gaze_failures_are_low_alone_and_only_patterns_are_inferred(client):
    m = make(client, n_questions=3, gaze_check="true")
    go(client, m["token"])
    post = lambda **b: client.post(f"/api/candidate/{m['token']}/events", json=b)
    evs = lambda: client.get(f"/api/interviews/{m['id']}", headers=KEY).json()["proctoring"]
    post(type="gaze_check_passed", t_offset=60, detail={"score": 0.82})
    post(type="gaze_check_failed", t_offset=200, detail={"score": 0.21})
    t = evs()
    failed = [e for e in t["events"] if e["type"] == "gaze_check_failed"]
    assert failed[0]["severity"] == "low" and "imprecise" in failed[0]["description"] and "0.21" in failed[0]["description"]
    assert t["counts"]["inferred"] == 0 and not t["needs_review"]                 # one miss is not a suspicion
    post(type="gaze_check_unavailable", t_offset=250, detail={"reason": "declined by candidate <script>"})
    assert evs()["counts"]["inferred"] == 0                                      # unavailable never counts against anyone
    post(type="gaze_check_failed", t_offset=400, detail={"score": 0.1})
    t = evs()
    inferred = [e for e in t["events"] if e["kind"] == "inferred"]
    assert [e["type"] for e in inferred] == ["gaze_check_pattern"] and inferred[0]["severity"] == "medium"
    assert "glasses" in inferred[0]["description"] and "Not proof of misconduct" in inferred[0]["description"]
    assert t["gaze"] == {"passed": 1, "failed": 2, "unavailable": 1}
    unavailable = next(e for e in t["events"] if e["type"] == "gaze_check_unavailable")
    assert "<" not in unavailable["description"]                                   # reason text is sanitised


# ------------------------------------------------------------------ requirement, bypass, live switches
def calibrate(client, token, ok=True):
    return client.post(f"/api/candidate/{token}/gaze/calibration", json={"ok": ok, "mode": "eyes+head", "grade": "good", "error": 0.1, "reason": ""})


def test_required_eye_tracking_blocks_start_until_calibrated_or_bypassed(client):
    m = make(client, n_questions=3, gaze_check="true", gaze_required="true")
    t = m["token"]
    client.post(f"/api/candidate/{t}/consent", json={"proctoring": True})
    r = client.post(f"/api/candidate/{t}/start")
    assert r.status_code == 409 and "calibration is required" in r.json()["detail"]
    assert client.get(f"/api/candidate/{t}/flags").json()["gaze"]["required"] is True
    calibrate(client, t, ok=False)
    assert client.post(f"/api/candidate/{t}/start").status_code == 409              # a failed calibration does not unlock it
    # the interviewer bypasses it for this candidate only
    assert client.patch(f"/api/interviews/{m['id']}/live", json={"gaze_bypass": True}, headers=KEY).status_code == 200
    assert client.get(f"/api/candidate/{t}/flags").json()["gaze"] == {"enabled": True, "required": False, "bypass": True, "count": 3, "duration_s": 8}
    assert client.post(f"/api/candidate/{t}/start").status_code == 200
    other = make(client, n_questions=3, gaze_check="true", gaze_required="true")      # another candidate is still blocked
    client.post(f"/api/candidate/{other['token']}/consent", json={"proctoring": True})
    assert client.post(f"/api/candidate/{other['token']}/start").status_code == 409
    calibrate(client, other["token"], ok=True)
    assert client.post(f"/api/candidate/{other['token']}/start").status_code == 200
    d = client.get(f"/api/interviews/{other['id']}", headers=KEY).json()
    assert d["calibration"]["ok"] and d["calibration"]["grade"] == "good"


def test_requirement_needs_the_check_to_be_enabled(client):
    m = make(client, n_questions=3, gaze_required="true")                           # required without enabling the check is meaningless
    t = m["token"]
    client.post(f"/api/candidate/{t}/consent", json={"proctoring": True})
    assert client.get(f"/api/candidate/{t}/flags").json()["gaze"]["required"] is False
    assert client.post(f"/api/candidate/{t}/start").status_code == 200


def test_calibration_report_is_sanitised(client):
    m = make(client, n_questions=3, gaze_check="true")
    assert client.post(f"/api/candidate/{m['token']}/gaze/calibration", json={"ok": True, "mode": "<script>", "grade": "good"}).status_code == 422
    assert client.post(f"/api/candidate/{m['token']}/gaze/calibration", json={"ok": False, "reason": "bad <b>light</b>"}).status_code == 200
    reason = client.get(f"/api/interviews/{m['id']}", headers=KEY).json()["calibration"]["reason"]
    assert "<" not in reason and ">" not in reason


def test_gaze_can_be_disabled_for_one_candidate_or_globally_mid_interview(client):
    a = make(client, n_questions=3, gaze_check="true", gaze_required="true")
    b = make(client, n_questions=3, gaze_check="true")
    for m in (a, b):
        calibrate(client, m["token"])
        go(client, m["token"])
    flags = lambda m: client.get(f"/api/candidate/{m['token']}/flags").json()["gaze"]
    assert flags(a)["enabled"] and flags(b)["enabled"]
    client.patch(f"/api/interviews/{a['id']}/live", json={"gaze_enabled": False}, headers=KEY)         # just this candidate
    assert not flags(a)["enabled"] and not flags(a)["required"] and flags(b)["enabled"]
    client.patch(f"/api/interviews/{a['id']}/live", json={"gaze_enabled": None}, headers=KEY)          # back to inheriting
    assert flags(a)["enabled"]
    assert client.patch("/api/settings", json={"gaze_global_off": True}, headers=KEY).json()["gaze_global_off"] is True
    assert not flags(a)["enabled"] and not flags(b)["enabled"]                                          # global kill switch wins
    assert client.patch("/api/settings", json={"gaze_global_off": False}, headers=KEY).json()["gaze_global_off"] is False
    assert flags(a)["enabled"]
    assert client.patch("/api/settings", json={"gaze_global_off": True}).status_code == 401


def test_go_back_is_off_by_default_and_blocked(client):
    m = make(client, n_questions=4, max_followups=0)
    go(client, m["token"])
    client.post(f"/api/candidate/{m['token']}/answer", json={"idx": 0, "answer": "first answer here"})
    assert client.get(f"/api/candidate/{m['token']}/current").json()["can_back"] is False
    assert client.post(f"/api/candidate/{m['token']}/goto", json={"idx": 0}).status_code == 403


def test_go_back_revise_and_live_switching(client):
    m = make(client, n_questions=4, max_followups=0, allow_back="true")
    t, iid = m["token"], m["id"]
    go(client, t)
    post = lambda p, **b: client.post(f"/api/candidate/{t}/{p}", json=b)
    cur = post("answer", idx=0, answer="I would use a heap dump with a profiler to find a leak.").json()
    cur = post("answer", idx=1, status="skipped").json()
    assert cur["idx"] == 2 and cur["can_back"] and [p["status"] for p in cur["previous"]] == ["answered", "skipped"]
    assert post("goto", idx=2).status_code == 409 and post("goto", idx=9).status_code == 409           # only earlier questions
    v = post("goto", idx=0).json()
    assert v["revisiting"] and v["idx"] == 0 and v["previous_answer"].startswith("I would use a heap dump") and v["furthest"] == 2
    assert post("answer", idx=2, answer="x").status_code == 409                                         # must answer the question being viewed
    assert post("answer", idx=0, status="skipped").status_code == 409                                   # cannot blank an answer by "skipping"
    back = post("answer", idx=0, answer="Revised: generations, GC roots, heap dump and a profiler to find what retains objects.").json()
    assert not back["revisiting"] and back["idx"] == 2                                                  # returns to where they were
    v = post("goto", idx=1).json()                                                                      # revisit a skipped one
    assert v["previous_status"] == "skipped"
    assert post("answer", idx=1, answer="Now I will answer this one properly with several concrete points.").status_code == 200
    # the interviewer switches going back off while the candidate is looking at an old question
    post("goto", idx=0)
    client.patch(f"/api/interviews/{iid}/live", json={"allow_back": False}, headers=KEY)
    cur = client.get(f"/api/candidate/{t}/current").json()
    assert not cur["revisiting"] and cur["idx"] == 2 and cur["can_back"] is False
    assert post("goto", idx=0).status_code == 403
    client.patch(f"/api/interviews/{iid}/live", json={"allow_back": None}, headers=KEY)                 # inherit config (on) again
    assert post("goto", idx=0).status_code == 200
    post("return")
    client.patch("/api/settings", json={"back_global_off": True}, headers=KEY)                          # global kill switch
    assert post("goto", idx=0).status_code == 403
    client.patch("/api/settings", json={"back_global_off": False}, headers=KEY)
    post("answer", idx=2, answer="third"); post("answer", idx=3, answer="fourth")
    client.post(f"/api/candidate/{t}/finish")
    q = client.get(f"/api/interviews/{iid}", headers=KEY).json()["evaluation"]["per_question"]
    assert q[0]["revisions"] == 1 and q[0]["answer"].startswith("Revised:") and q[0]["history"][0]["answer"].startswith("I would use")
    assert q[1]["revisions"] == 1 and q[1]["history"][0]["status"] == "skipped" and q[1]["status"] == "answered"
    assert client.get(f"/api/interviews/{iid}", headers=KEY).json()["evaluation"]["counts"]["answered"] == 4


def test_allow_back_default_setting_applies_to_new_interviews(client):
    assert client.get("/api/settings", headers=KEY).json() == {"allow_back_default": False, "back_global_off": False, "gaze_global_off": False}
    client.patch("/api/settings", json={"allow_back_default": True}, headers=KEY)
    m = make(client, n_questions=3)
    assert client.get(f"/api/candidate/{m['token']}").json()["allow_back"] is True
    m2 = make(client, n_questions=3, allow_back="false")                                                 # explicit choice beats the default
    assert client.get(f"/api/candidate/{m2['token']}").json()["allow_back"] is False


def test_database_from_an_older_version_is_upgraded(tmp_path):
    import sqlite3
    from app import db
    db.reset()
    old = sqlite3.connect(tmp_path / "hireneuron.db")
    old.executescript("CREATE TABLE interviews (id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, job TEXT, candidate TEXT, resume TEXT, match TEXT, config TEXT, questions TEXT, consent TEXT, started_at TEXT, ended_at TEXT, evaluation TEXT, overrides TEXT, decision TEXT, state TEXT);"
                      "CREATE TABLE answers (id INTEGER PRIMARY KEY AUTOINCREMENT, interview_id TEXT NOT NULL, idx INTEGER NOT NULL, parent_idx INTEGER, question TEXT NOT NULL, category TEXT, answer TEXT, status TEXT NOT NULL, duration_s REAL, created_at TEXT NOT NULL);"
                      "INSERT INTO answers(interview_id, idx, question, answer, status, created_at) VALUES('x', 0, 'q', 'a', 'answered', 't');")
    old.commit(); old.close()
    db.reset(tmp_path)
    row = db.many("SELECT * FROM answers")[0]
    assert row["revisions"] == 0 and row["history"] is None
    db.execute("UPDATE interviews SET live=NULL")                        # column exists
    db.reset()


def test_live_call_signalling_and_audit_events(client):
    m = make(client, n_questions=3)
    t, iid = m["token"], m["id"]
    go(client, t)
    assert client.get(f"/api/interviews/{iid}/live-status", headers=KEY).json()["call"] == {"candidate_connected": False, "recruiters": 0, "phone": False}
    RH = {"X-Recruiter-Key": "demo-recruiter"}
    for bad, hdr in ((f"/ws/live/{iid}/candidate?token=wrong", {}), (f"/ws/live/{iid}/recruiter", {"X-Recruiter-Key": "wrong"}), (f"/ws/live/{iid}/recruiter?key=demo-recruiter", {}),
                     ("/ws/live/nope/recruiter", RH), (f"/ws/live/{'0' * 12}/recruiter", RH)):
        try:
            with client.websocket_connect(bad, headers=hdr) as w:
                w.receive_text()
            assert False, bad
        except Exception:
            pass
    with client.websocket_connect(f"/ws/live/{iid}/candidate?token={t}") as cand:
        hello = json.loads(cand.receive_text())
        assert hello["type"] == "hello" and hello["peers"] == []
        with client.websocket_connect(f"/ws/live/{iid}/recruiter", headers=RH) as rec:
            rhello = json.loads(rec.receive_text())
            assert rhello["type"] == "hello" and [p["role"] for p in rhello["peers"]] == ["candidate"]
            joined = json.loads(cand.receive_text())
            assert joined["type"] == "peer-joined" and joined["role"] == "recruiter"
            st = client.get(f"/api/interviews/{iid}/live-status", headers=KEY).json()
            assert st["call"] == {"candidate_connected": True, "recruiters": 1, "phone": False}
            assert client.get(f"/api/candidate/{t}/flags").json()["recruiters_present"] == 1
            cand.send_text(json.dumps({"type": "offer", "to": joined["id"], "sdp": "S"}))
            got = json.loads(rec.receive_text())
            assert got["type"] == "offer" and got["from"] == hello["id"] and got["sdp"] == "S"
            rec.send_text(json.dumps({"type": "answer", "to": hello["id"], "sdp": "A"}))
            assert json.loads(cand.receive_text())["type"] == "answer"
            rec.send_text(json.dumps({"type": "evil", "to": hello["id"]}))                      # unknown message types are dropped
            rec.send_text(json.dumps({"type": "ice", "to": rhello["id"], "candidate": {}}))      # a peer cannot message its own role
            rec.send_text(json.dumps({"type": "ice", "to": hello["id"], "candidate": {"c": 1}}))
            assert json.loads(cand.receive_text())["type"] == "ice"
        assert json.loads(cand.receive_text())["type"] == "peer-left"
    types = [e["type"] for e in client.get(f"/api/interviews/{iid}", headers=KEY).json()["proctoring"]["events"]]
    assert "recruiter_joined" in types and "recruiter_left" in types


def test_live_status_shows_progress_and_effective_switches(client):
    m = make(client, n_questions=3, allow_back="true", gaze_check="true")
    go(client, m["token"])
    s = client.get(f"/api/interviews/{m['id']}/live-status", headers=KEY).json()
    assert s["status"] == "in_progress" and s["current"]["number"] == 1 and s["current"]["total"] == 3
    assert s["effective"]["allow_back"] is True and s["effective"]["gaze"]["enabled"] is True
    assert client.get(f"/api/interviews/{m['id']}/live-status").status_code == 401
