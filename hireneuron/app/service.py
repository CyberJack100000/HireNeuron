"""Creating interviews: parse JD + resume, generate questions, persist."""
import secrets
from datetime import datetime, timezone

from . import db, llm, questions as Q, resume as R
from .interview import merge_config

SAMPLE_DIR = db.Path(__file__).resolve().parent.parent / "data"


def prepare(job_title: str, description: str, skills: list[str], resume_text: str, config: dict, custom: list[str] | None) -> dict:
    description, resume_text = description[:30_000], resume_text[:60_000]          # bound the work the parsers can be asked to do
    custom = [c.strip()[:500] for c in (custom or []) if isinstance(c, str) and c.strip()][:40]
    if len(description.strip()) < 30:
        raise ValueError("Job description is too short.")
    if len(resume_text.strip()) < 30:
        raise ValueError("Resume text is too short.")
    cfg = merge_config(config)
    job = R.parse_job(job_title, description, skills)
    job["description"] = description                                           # kept so the recruiter can reuse this job for another candidate
    resume = R.parse_resume(resume_text)
    match = R.match_skills(job["skills"], resume["skills"])
    qs = Q.generate(job, resume, match, cfg, custom)
    if llm.enabled():  # optionally enrich with LLM questions, validated and deduplicated
        extra = llm.generate_questions(job, resume, 2, [q.text for q in qs])
        for e in extra:
            if not any(Q.too_similar(e["text"], q.text) for q in qs) and len(qs) < cfg["n_questions"] + 2:
                qs.insert(len(qs) - 1, Q.Question(e["text"], e["category"], e["difficulty"], e["skill"], e["expected"], "llm", "Suggested by the LLM from the JD and resume."))
        qs = qs[: cfg["n_questions"]]
    return {"job": job, "resume": resume, "match": match, "config": cfg, "questions": [q.to_dict() for q in qs]}


def create(prepared: dict, candidate_name: str | None = None, candidate_email: str | None = None) -> dict:
    iid = secrets.token_hex(6)
    token = secrets.token_urlsafe(16)
    cand = {"name": candidate_name or prepared["resume"].get("name") or "Candidate", "email": candidate_email or prepared["resume"].get("email")}
    db.execute("INSERT INTO interviews(id,token,created_at,status,job,candidate,resume,match,config,questions,consent,state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (iid, token, datetime.now(timezone.utc).isoformat(timespec="seconds"), "created", db.dumps(prepared["job"]), db.dumps(cand),
                db.dumps(prepared["resume"]), db.dumps(prepared["match"]), db.dumps(prepared["config"]), db.dumps(prepared["questions"]),
                db.dumps(None), db.dumps({"cursor": 0, "followups_used": 0, "pending": None})))
    return {"id": iid, "token": token}
