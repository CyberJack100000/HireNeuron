"""Answer evaluation against job-specific expected concepts. Substance only: nothing here looks at speaking
speed, accent, face or appearance. If an LLM is configured it grades first; its output must quote the answer
verbatim, otherwise the deterministic grader is used."""
import re

from . import llm
from .questions import RUBRIC_DIMENSION

DIMENSIONS = ("technical", "problem_solving", "communication", "role_specific")
DIMENSION_LABELS = {"technical": "Technical knowledge", "problem_solving": "Problem solving",
                    "communication": "Communication", "role_specific": "Role-specific competencies"}
DEFAULT_WEIGHTS = {d: 1.0 for d in DIMENSIONS}
STRUCTURE = re.compile(r"\b(first|second|then|next|because|therefore|for example|for instance|finally|as a result|so that|which means|in contrast|however)\b", re.I)


def split_concept(c: str) -> tuple[str, list[str]]:
    alts = [a.strip().lower() for a in c.split("|") if a.strip()]
    return alts[0], alts


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def concept_hits(answer: str, expected: list[str]) -> tuple[list[dict], list[str]]:
    low = answer.lower()
    sents = sentences(answer)
    hits, missed = [], []
    for c in expected:
        label, alts = split_concept(c)
        found = next((a for a in alts if a in low), None)
        if found:
            quote = next((s for s in sents if found in s.lower()), answer[:200])
            hits.append({"concept": label, "quote": quote[:240]})
        else:
            missed.append(label)
    return hits, missed


def words(text: str) -> int:
    return len(re.findall(r"\w+", text))


def heuristic_grade(question: str, expected: list[str], answer: str) -> dict:
    n = words(answer)
    if not expected:  # recruiter question with no rubric: only relevance to the question text
        q = {w for w in re.findall(r"[a-z]{5,}", question.lower())}
        a = set(re.findall(r"[a-z]{5,}", answer.lower()))
        cov = min(1.0, len(q & a) / max(3, len(q) * 0.4)) if q else 0.5
        hits, missed = [], []
        note = "No rubric concepts were defined for this question, so the score reflects relevance and length only; review manually."
    else:
        hits, missed = concept_hits(answer, expected)
        cov = len(hits) / len(expected)
        note = ""
    score = 1 + 4 * cov
    cap = 1 if n < 6 else 2 if n < 20 else 3 if n < 35 and cov < 0.8 else 5
    score = max(1, min(round(score), cap, 5))
    covered = ", ".join(h["concept"] for h in hits) or "none of the expected points"
    expl = f"Covered: {covered}."
    if missed:
        expl += f" Did not mention: {', '.join(missed)}."
    if n < 20:
        expl += f" The answer is short ({n} words), which limits the score."
    return {"score": score, "evidence": [h["quote"] for h in hits[:3]], "hits": [h["concept"] for h in hits],
            "missing": missed, "explanation": (expl + " " + note).strip(), "engine": "rules", "words": n}


def grade(question: dict, answer: str) -> dict:
    expected = question.get("expected") or []
    if llm.enabled():
        g = llm.grade_answer(question["text"], expected, answer)
        if g:
            base = heuristic_grade(question["text"], expected, answer)
            return {**base, **g, "hits": base["hits"], "engine": "llm"}
    return heuristic_grade(question["text"], expected, answer)


def structure_score(answers: list[str]) -> float | None:
    vals = []
    for a in answers:
        n = words(a)
        if n < 8:
            continue
        markers = len(set(m.lower() for m in STRUCTURE.findall(a)))
        vals.append(1 + min(4, markers + (1 if len(sentences(a)) >= 3 else 0) + (1 if n >= 40 else 0) - 0))
    return round(sum(vals) / len(vals), 2) if vals else None


def evaluate(interview: dict, answers: list[dict]) -> dict:
    qs = interview["questions"]
    config = interview.get("config") or {}
    weights = {**DEFAULT_WEIGHTS, **(config.get("rubric_weights") or {})}
    by_idx = {a["idx"]: a for a in answers if a.get("parent_idx") is None}
    follow = {}
    for a in answers:
        if a.get("parent_idx") is not None:
            follow.setdefault(a["parent_idx"], []).append(a)

    per_q = []
    for i, q in enumerate(qs):
        a = by_idx.get(i)
        status = a["status"] if a else "unanswered"
        text = (a or {}).get("answer") or ""
        # a follow-up answer is judged together with the main answer
        full = " ".join([text] + [f.get("answer") or "" for f in follow.get(i, [])]).strip()
        item = {"idx": i, "question": q["text"], "category": q["category"], "difficulty": q["difficulty"],
                "skill": q.get("skill"), "status": status, "answer": text,
                "followups": [{"question": f["question"], "answer": f.get("answer"), "status": f["status"]} for f in follow.get(i, [])],
                "revisions": (a or {}).get("revisions", 0), "history": (a or {}).get("history") or [],
                "dimension": RUBRIC_DIMENSION.get(q["category"], "technical")}
        if status == "answered" and full:
            item.update(grade(q, full))
        else:
            item.update({"score": None, "evidence": [], "hits": [], "missing": [c.split("|")[0] for c in q.get("expected", [])],
                         "explanation": "No answer was given." if status != "skipped" else "The candidate skipped this question.",
                         "engine": "none", "words": 0})
        per_q.append(item)

    dims = {}
    for d in DIMENSIONS:
        vals = [p["score"] for p in per_q if p["dimension"] == d and p["score"] is not None]
        dims[d] = round(sum(vals) / len(vals), 2) if vals else None
    struct = structure_score([p["answer"] for p in per_q if p["answer"]])
    if struct is not None:  # communication: STAR/behavioural answers + how well answers are organised
        parts = [x for x in (dims["communication"], struct) if x is not None]
        dims["communication"] = round(sum(parts) / len(parts), 2)

    present = {d: v for d, v in dims.items() if v is not None}
    wsum = sum(weights[d] for d in present) or 1
    overall = round(sum(v * weights[d] for d, v in present.items()) / wsum, 2) if present else None

    by_skill: dict[str, list[int]] = {}
    for p in per_q:
        if p["skill"] and p["score"] is not None:
            by_skill.setdefault(p["skill"], []).append(p["score"])
    skill_avg = {s: round(sum(v) / len(v), 2) for s, v in by_skill.items()}
    strengths = [f"{s}: solid answer (average {v}/5)" for s, v in skill_avg.items() if v >= 4]
    gaps = [f"{s}: weak or incomplete answer (average {v}/5)" for s, v in skill_avg.items() if v <= 2.5]
    for p in per_q:
        if p["status"] != "answered" and p["skill"]:
            gaps.append(f"{p['skill']}: question was {p['status']}")
    asked = {q.get("skill") for q in qs}
    resume_skills = (interview.get("resume") or {}).get("skills", [])
    missing_req = (interview.get("match") or {}).get("missing", [])
    further = [f"{s}: claimed on the resume but not assessed" for s in resume_skills[:6] if s not in asked]
    further += [f"{s}: required for the role but absent from the resume" for s in missing_req]
    further += [f"{s}: borderline answer ({v}/5), probe in a follow-up round" for s, v in skill_avg.items() if 2.5 < v < 4]

    answered = sum(1 for p in per_q if p["status"] == "answered")
    if overall is None:
        rec = "No scorable answers: this interview cannot support a recommendation."
    elif overall >= 4:
        rec = "Strong evidence against the job criteria. Consider advancing to the next round."
    elif overall >= 3:
        rec = "Mixed evidence. Consider a follow-up round on the areas listed under further assessment."
    else:
        rec = "Limited evidence of the required skills in this interview. Review the answers before deciding."
    return {
        "per_question": per_q, "dimensions": dims, "dimension_labels": DIMENSION_LABELS, "overall": overall,
        "skills": skill_avg, "strengths": strengths, "gaps": gaps, "further_assessment": further,
        "counts": {"total": len(qs), "answered": answered,
                   "skipped": sum(1 for p in per_q if p["status"] == "skipped"),
                   "unanswered": sum(1 for p in per_q if p["status"] not in ("answered", "skipped"))},
        "recommendation": rec,
        "caveats": ["AI-generated scores support, and never replace, a human decision.",
                    "Scoring uses the content of answers only, not speaking speed, accent, facial expression or appearance.",
                    "Proctoring events are indicators for human review and are not part of these scores."],
        "engine": "llm+rules" if llm.enabled() else "rules",
    }


def apply_overrides(evaluation: dict, overrides: dict | None) -> dict:
    """Recruiter overrides win; the AI score is kept alongside."""
    overrides = overrides or {}
    for p in evaluation["per_question"]:
        o = overrides.get(str(p["idx"]))
        p["ai_score"] = p["score"]
        p["override"] = o
        if o and o.get("score") is not None:
            p["score"] = o["score"]
    return evaluation
