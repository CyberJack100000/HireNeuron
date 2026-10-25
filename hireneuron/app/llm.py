"""Optional LLM backend. Off unless ANTHROPIC_API_KEY is set. Every call is validated and any failure
falls back to the deterministic engine, so the product never depends on the network.

NOTE: this path has only been tested with a stub client in the sandbox (no API key available)."""
import json
import os
import re

MODEL = os.environ.get("HN_LLM_MODEL", "claude-sonnet-5-5")
_client = None


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or _client is not None


def set_client(c):  # tests inject a stub
    global _client
    _client = c


def _get():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def complete_json(system: str, user: str, max_tokens: int = 1500):
    """Returns parsed JSON or None on any failure."""
    try:
        msg = _get().messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                                     messages=[{"role": "user", "content": user}])
        text = "".join(getattr(b, "text", "") for b in msg.content)
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        return json.loads(m.group(1)) if m else None
    except Exception:
        return None


def generate_questions(job: dict, resume: dict, n: int, existing: list[str]) -> list[dict]:
    sys = ("You write interview questions. Reply with JSON only: a list of objects with keys "
           "text, category (technical|behavioral|experience|scenario), difficulty (basic|intermediate|advanced), "
           "skill, expected (list of 3-5 short concept phrases a good answer covers). No duplicates of the existing questions.")
    usr = json.dumps({"job": {k: job.get(k) for k in ("title", "skills", "responsibilities", "requirements")},
                      "resume": {k: resume.get(k) for k in ("skills", "projects", "experience", "years_experience")},
                      "count": n, "existing": existing})
    data = complete_json(sys, usr)
    out = []
    if isinstance(data, list):
        for q in data:
            if isinstance(q, dict) and isinstance(q.get("text"), str) and len(q["text"]) > 20 \
                    and q.get("category") in ("technical", "behavioral", "experience", "scenario") \
                    and isinstance(q.get("expected"), list):
                out.append({"text": q["text"].strip(), "category": q["category"],
                            "difficulty": q.get("difficulty") if q.get("difficulty") in ("basic", "intermediate", "advanced") else "intermediate",
                            "skill": q.get("skill") if isinstance(q.get("skill"), str) else None,
                            "expected": [str(e) for e in q["expected"]][:6]})
    return out[:n]


def grade_answer(question: str, expected: list[str], answer: str) -> dict | None:
    """Returns {score 1-5, evidence [verbatim quotes], explanation} or None. Evidence must be verbatim."""
    sys = ("You grade one interview answer against the concepts a good answer covers. Judge only the substance of the "
           "text, never fluency, accent or style. Reply with JSON only: {\"score\": 1-5, \"evidence\": [verbatim quotes "
           "copied from the answer], \"missing\": [concepts not covered], \"explanation\": one or two sentences}.")
    data = complete_json(sys, json.dumps({"question": question, "expected_concepts": expected, "answer": answer}), 600)
    if not isinstance(data, dict):
        return None
    try:
        score = int(data["score"])
    except Exception:
        return None
    if not 1 <= score <= 5:
        return None
    ev = [e for e in data.get("evidence", []) if isinstance(e, str) and e and e in answer]
    if score >= 3 and not ev:  # a positive score must be backed by a real quote
        return None
    return {"score": score, "evidence": ev[:3], "missing": [str(m) for m in data.get("missing", [])][:5],
            "explanation": str(data.get("explanation", ""))[:400]}
