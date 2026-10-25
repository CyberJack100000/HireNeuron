"""Seeds a complete, clearly-labelled SIMULATED interview so the dashboard can be demonstrated without a camera."""
from pathlib import Path

from . import db, interview, proctor, service
from .evaluate import split_concept

DATA = Path(__file__).resolve().parent.parent / "data"


def sample_inputs() -> tuple[str, str, str]:
    return ("Senior Java Backend Engineer", (DATA / "sample_jd.txt").read_text(encoding="utf-8"), (DATA / "sample_resume.txt").read_text(encoding="utf-8"))


def _answer(q: dict, strength: float) -> str:
    exp = q.get("expected") or []
    n = max(0, round(len(exp) * strength))
    if not exp:
        return "I would approach it step by step, check the requirements first, and then discuss the options with the team."
    terms = [split_concept(c)[1][0] for c in exp[:n]]
    if not terms:
        return "I am not completely sure about this one."
    parts = [f"First, I would think about {terms[0]}."]
    if len(terms) > 1:
        parts.append(f"This matters because {terms[1]} affects how it behaves in production.")
    if len(terms) > 2:
        parts.append(f"For example, in my last project we dealt with {terms[2]} directly.")
    for t in terms[3:]:
        parts.append(f"Finally, I would also consider {t}.")
    return " ".join(parts)


def seed(base_dir) -> dict:
    title, jd, cv = sample_inputs()
    prepared = service.prepare(title, jd, ["java", "spring boot"], cv, {"n_questions": 10, "max_followups": 2}, ["Why do you want to work on payments systems?"])
    made = service.create(prepared, "Souparna Saha (simulated)", "26ee3014@rgipt.ac.in")
    iv = interview.get(made["id"])
    db.update_interview(iv["id"], consent={"recording": False, "proctoring": True, "secondary_camera": True, "at": interview.now(), "simulated": True})
    st = interview.start(interview.get(iv["id"]))
    strengths = [0.9, 0.8, 0.75, 0.3, 0.9, 0.5, 0.0, 0.8, 0.6, 0.85, 0.7]
    i = 0
    while not st.get("done"):
        iv = interview.get(iv["id"])
        if st["is_followup"]:
            st = interview.submit(iv, st["idx"], "Yes, for instance we had to tune the connection pool and add retries with backoff after an incident.", "answered", 30)
            continue
        q = iv["questions"][st["idx"]]
        s = strengths[i % len(strengths)]
        i += 1
        if s == 0.0:
            st = interview.submit(iv, st["idx"], "", "skipped", 3)
        else:
            st = interview.submit(iv, st["idx"], _answer(q, s), "answered", 55 + i * 4)
    for t, typ, d in [(95, "looking_away", {"duration_s": 7}), (180, "looking_away", {"duration_s": 9}), (260, "tab_hidden", {"duration_s": 12}),
                      (330, "looking_away", {"duration_s": 11}), (410, "secondary_disconnected", {}), (440, "secondary_connected", {}),
                      (520, "audio_burst", {}), (640, "looking_away", {"duration_s": 8})]:
        proctor.record(iv["id"], {"type": typ, "t_offset": t, "detail": d}, base_dir)
    interview.finish(interview.get(iv["id"]))
    return made
