"""The activity record: every action by the interviewer and the candidate is chained, and tampering is detectable."""
from app import audit, db
from tests.conftest import KEY
from tests.test_hireneuron import go, make


def acts(client, iid):
    r = client.get(f"/api/interviews/{iid}/audit", headers=KEY).json()
    return r, [(e["actor"], e["action"]) for e in r["entries"]]


def test_actions_of_both_sides_are_recorded_in_order(client):
    m = make(client, n_questions=3)
    t, iid = m["token"], m["id"]
    go(client, t)
    cur = client.get(f"/api/candidate/{t}/current").json()
    client.post(f"/api/candidate/{t}/answer", json={"idx": cur["idx"], "answer": "I used a queue and retries.", "status": "answered"})
    client.patch(f"/api/interviews/{iid}/live", json={"gaze_bypass": True}, headers=KEY)
    client.patch(f"/api/interviews/{iid}/live", json={"gaze_bypass": True}, headers=KEY)         # no change: not recorded twice
    client.post(f"/api/interviews/{iid}/message", json={"text": "Please speak up"}, headers=KEY)
    client.post(f"/api/interviews/{iid}/extend", json={"minutes": 5}, headers=KEY)
    client.post(f"/api/interviews/{iid}/end", headers=KEY)
    r, a = acts(client, iid)
    for want in (("candidate", "consent_given"), ("candidate", "interview_started"), ("candidate", "answer_submitted"), ("interviewer", "setting_changed"),
                 ("interviewer", "message_sent"), ("interviewer", "time_extended"), ("interviewer", "interview_finished")):
        assert want in a, want
    assert a.count(("interviewer", "setting_changed")) == 1
    assert [e["seq"] for e in r["entries"]] == list(range(1, len(a) + 1))
    sub = next(e for e in r["entries"] if e["action"] == "answer_submitted")["detail"]
    assert sub["chars"] == len("I used a queue and retries.") and "queue" not in str(sub)           # a fingerprint, not a second copy of the text
    assert r["verify"]["ok"] and r["verify"]["count"] == len(a)


def test_editing_or_removing_an_entry_breaks_the_chain(client):
    m = make(client, n_questions=3)
    iid = m["id"]
    go(client, m["token"])
    for i in range(4):
        client.post(f"/api/interviews/{iid}/message", json={"text": f"note {i}"}, headers=KEY)
    head = audit.verify(iid)["head"]
    assert audit.verify(iid)["ok"]
    db.execute("UPDATE audit SET detail=? WHERE interview_id=? AND seq=4", ('{"text":"changed"}', iid))
    v = audit.verify(iid)
    assert not v["ok"] and v["broken_at"] == 4
    db.execute("UPDATE audit SET detail=? WHERE interview_id=? AND seq=4", ('{"text":"note 1"}', iid))   # (restored text differs in key order: still broken, honest)
    db.execute("DELETE FROM audit WHERE interview_id=? AND seq=3", (iid,))
    assert not audit.verify(iid)["ok"] and audit.verify(iid)["broken_at"] == 4
    assert head and len(head) == 64


def test_transcript_lines_from_both_sides(client):
    m = make(client, n_questions=3)
    t, iid = m["token"], m["id"]
    assert client.post(f"/api/candidate/{t}/transcript", json={"text": "hello"}).status_code == 409        # not running yet
    go(client, t)
    assert client.post(f"/api/candidate/{t}/transcript", json={"text": "I would shard\x00 by user id.", "t_offset": 12.34}).json() == {"recorded": True}
    assert client.post(f"/api/interviews/{iid}/transcript", json={"text": "Why not by region?", "t_offset": 15}, headers=KEY).json() == {"recorded": True}
    assert client.post(f"/api/interviews/{iid}/transcript", json={"text": "x"}).status_code == 401
    assert client.post(f"/api/candidate/wrongtoken/transcript", json={"text": "x"}).status_code in (404, 403)
    assert client.post(f"/api/candidate/{t}/transcript", json={"text": ""}).status_code == 422
    assert client.post(f"/api/candidate/{t}/transcript", json={"text": "y" * 601}).status_code == 422
    assert client.post(f"/api/candidate/{t}/transcript", json={"text": "   \x01 "}).json() == {"recorded": False}
    lines = audit.transcript(iid)
    assert [(l["who"], l["text"]) for l in lines] == [("candidate", "I would shard by user id."), ("interviewer", "Why not by region?")]
    assert all(0 <= l["t"] < 60 for l in lines)                                                         # the server's clock, not the browser's claimed 12.34 / 15
    client.post(f"/api/candidate/{t}/finish")
    assert client.post(f"/api/candidate/{t}/transcript", json={"text": "late"}).status_code == 409


def test_speech_is_capped_but_actions_still_recorded(client, monkeypatch):
    monkeypatch.setattr(audit, "MAX_SPEECH", 3)
    m = make(client, n_questions=3)
    t, iid = m["token"], m["id"]
    go(client, t)
    res = [client.post(f"/api/candidate/{t}/transcript", json={"text": f"w{i}"}).json()["recorded"] for i in range(5)]
    assert res == [True, True, True, False, False]
    client.post(f"/api/interviews/{iid}/message", json={"text": "still logged"}, headers=KEY)
    assert ("interviewer", "message_sent") in acts(client, iid)[1]


def test_review_override_decision_and_settings_are_recorded_and_delete_leaves_a_trace(client):
    from app import demo
    d = client.post("/api/demo", headers=KEY).json()
    iid = d["id"]
    client.patch(f"/api/interviews/{iid}/override", json={"idx": 0, "score": 2, "note": "weak"}, headers=KEY)
    client.patch(f"/api/interviews/{iid}/decision", json={"decision": "hold", "notes": "later"}, headers=KEY)
    ev = client.get(f"/api/interviews/{iid}", headers=KEY).json()["proctoring"]["events"][0]
    client.patch(f"/api/events/{ev['id']}/review", json={"status": "dismissed", "note": "glare"}, headers=KEY)
    client.post(f"/api/interviews/{iid}/reevaluate", headers=KEY)
    client.patch("/api/settings", json={"back_global_off": True}, headers=KEY)
    names = [a for _, a in acts(client, iid)[1]]
    assert {"score_override", "decision", "flag_reviewed", "rescored"} <= set(names)
    assert ("interviewer", "global_setting_changed") in [(e["actor"], e["action"]) for e in audit.entries(audit.GLOBAL)]
    client.delete(f"/api/interviews/{iid}", headers=KEY)
    assert db.many("SELECT 1 FROM audit WHERE interview_id=?", (iid,)) == []                              # the interview's own record goes with it
    gone = [e for e in audit.entries(audit.GLOBAL) if e["action"] == "interview_deleted"]
    assert gone and gone[-1]["detail"]["interview"] == iid                                                 # but the fact it was deleted stays


def test_export_carries_the_record_and_is_logged(client):
    m = make(client, n_questions=3)
    go(client, m["token"])
    ex = client.get(f"/api/interviews/{m['id']}/export.json", headers=KEY).json()
    assert ex["audit"]["verify"]["ok"] and any(e["action"] == "consent_given" for e in ex["audit"]["entries"])
    assert ("interviewer", "exported") in acts(client, m["id"])[1]
    assert client.get(f"/api/interviews/{m['id']}/audit").status_code == 401


# ------------------------------------------------------------------ second camera
import json
import pytest


def _pair(client, m):
    return client.get(f"/api/candidate/{m['token']}/pair").json()["pair_token"]


def test_phone_is_told_to_switch_off_when_the_interview_ends(client):
    m = make(client, n_questions=3)
    go(client, m["token"])
    tok = _pair(client, m)
    with client.websocket_connect(f"/ws/pair/{tok}/phone") as phone:
        assert client.get(f"/api/interviews/{m['id']}/live-status", headers=KEY).json()["call"]["phone"] is True
        client.post(f"/api/interviews/{m['id']}/end", headers=KEY)
        got = json.loads(phone.receive_text())                                   # within a few seconds, without the candidate's page doing anything
        assert got == {"type": "end"}
    assert client.get(f"/api/pair/{tok}").status_code == 404                     # the link no longer works
    with client.websocket_connect(f"/ws/pair/{tok}/phone") as late:               # a phone that reconnects afterwards is told straight away
        assert json.loads(late.receive_text()) == {"type": "end"}
    names = [e["type"] for e in client.get(f"/api/interviews/{m['id']}", headers=KEY).json()["proctoring"]["events"]]
    assert "secondary_disconnected" not in names                                 # switching off on purpose is not a "disconnect" flag


def test_second_camera_recording_is_stored_per_part_and_served(client):
    m = make(client, n_questions=3)
    t, iid = m["token"], m["id"]
    go(client, t)
    for part, body in ((0, b"phone-a"), (1, b"phone-b")):
        assert client.post(f"/api/candidate/{t}/recording?cam=secondary&part={part}&seq=0", content=body).status_code == 200
    assert client.post(f"/api/candidate/{t}/recording?cam=secondary&part=0&seq=1", content=b"+more").status_code == 200
    assert client.post(f"/api/candidate/{t}/recording?cam=other", content=b"x").status_code == 422
    assert client.post(f"/api/candidate/{t}/recording?cam=secondary&part=10", content=b"x").status_code == 422
    d = client.get(f"/api/interviews/{iid}", headers=KEY).json()
    assert d["recording_secondary"] == 2 and d["recording"] is False
    assert client.get(f"/api/interviews/{iid}/recording?cam=secondary&part=0", headers=KEY).content == b"phone-a+more"
    assert client.get(f"/api/interviews/{iid}/recording?cam=secondary&part=1", headers=KEY).content == b"phone-b"
    assert client.get(f"/api/interviews/{iid}/recording?cam=secondary&part=0").status_code == 401
    assert client.get(f"/api/interviews/{iid}/recording?cam=../x", headers=KEY).status_code == 422


def test_public_address_for_codespaces_and_proxies(client, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "_file_url", lambda: "")                            # the committed public_url.txt would otherwise win
    m = make(client, n_questions=3)
    go(client, m["token"])
    monkeypatch.setenv("CODESPACE_NAME", "infamous-spooky-crematorium-7qgrqvx66xq3x65j")
    monkeypatch.setenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    pair = client.get(f"/api/candidate/{m['token']}/pair").json()
    assert pair["pair_url"].startswith("https://infamous-spooky-crematorium-7qgrqvx66xq3x65j-8000.app.github.dev/m/") and pair["secure_context"] is True
    monkeypatch.setenv("HN_PUBLIC_URL", "https://example.test/")                  # an explicit setting always wins
    assert client.get(f"/api/candidate/{m['token']}/pair").json()["pair_url"].startswith("https://example.test/m/")
    monkeypatch.delenv("HN_PUBLIC_URL"); monkeypatch.delenv("CODESPACE_NAME")
    h = {"x-forwarded-proto": "https", "x-forwarded-host": "demo.example.org"}
    assert client.get(f"/api/candidate/{m['token']}/pair", headers=h).json()["pair_url"].startswith("https://demo.example.org/m/")
    bad = {"x-forwarded-proto": "https", "x-forwarded-host": "evil.test/<script>"}
    assert "evil" not in client.get(f"/api/candidate/{m['token']}/pair", headers=bad).json()["pair_url"]


def test_public_url_file_is_used_and_validated(client, monkeypatch, tmp_path):
    from app import main
    m = make(client, n_questions=3)
    go(client, m["token"])
    monkeypatch.delenv("HN_PUBLIC_URL", raising=False); monkeypatch.delenv("CODESPACE_NAME", raising=False)
    monkeypatch.setattr(main, "_file_url", lambda: "https://fixed.example.dev")
    assert client.get(f"/api/candidate/{m['token']}/pair").json()["pair_url"].startswith("https://fixed.example.dev/m/")
    monkeypatch.undo()
    assert main._file_url().startswith("https://") and main._file_url().endswith(".app.github.dev")      # the committed file is valid


def test_qr_svg_scales_instead_of_being_cut_off(client):
    m = make(client, n_questions=3)
    go(client, m["token"])
    svg = client.get(f"/api/candidate/{m['token']}/pair").json()["qr_svg"]
    head = svg[svg.index("<svg"):svg.index(">", svg.index("<svg"))]
    assert "viewBox" in head and " width=" not in head and " height=" not in head                # a fixed size without a viewBox gets cropped by CSS


# ------------------------------------------------------------------ 404 pages
HTML = {"accept": "text/html,application/xhtml+xml"}


def test_unknown_addresses_get_a_readable_404_page(client):
    for path in ("/nope", "/some/deep/path", "/i/aaaaaaaaaaaaaaaaaaaaaa", "/i/x", "/i/../etc/passwd"):
        r = client.get(path, headers=HTML)
        assert r.status_code == 404 and "text/html" in r.headers["content-type"] and "This page does not exist" in r.text, path
        assert r.headers["x-content-type-options"] == "nosniff"
    assert "nope" not in client.get("/nope<script>", headers=HTML).text                      # the address is never echoed back


def test_programs_still_get_json_errors(client):
    for path in ("/api/nothing", "/api/candidate/wrongtoken", "/static/missing.js"):
        r = client.get(path)
        assert r.status_code == 404 and r.headers["content-type"].startswith("application/json") or path.startswith("/static"), path
    r = client.get("/api/nothing", headers=HTML)                                                # even a browser asking for an API address gets JSON
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    assert client.get("/nope").json() == {"detail": "Not Found"}                                # no Accept: html -> unchanged behaviour
    assert client.post("/api/interviews", headers={"X-Recruiter-Key": "wrong"}).status_code == 401
    assert client.get("/api/health").status_code == 200


def test_real_pages_still_open(client):
    m = make(client, n_questions=3)
    assert client.get(f"/i/{m['token']}", headers=HTML).status_code == 200
    assert client.get("/", headers=HTML).status_code == 200
    assert client.get("/m/anything", headers=HTML).status_code == 200                          # pairing pages explain expiry themselves
    assert "This page does not exist" not in client.get(f"/i/{m['token']}", headers=HTML).text
