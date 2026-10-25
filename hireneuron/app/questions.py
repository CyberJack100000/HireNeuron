"""Question generation: JD + resume -> a deduplicated, difficulty-aware question set."""
import re
from dataclasses import asdict, dataclass, field

from .skills import BANK, BEHAVIORAL, LEVEL_MIX, SCENARIO_GENERIC

DEFAULT_COUNT = 10
CATEGORIES = ("technical", "behavioral", "experience", "scenario")
DEFAULT_MIX = {"technical": 0.5, "experience": 0.2, "scenario": 0.2, "behavioral": 0.1}
RUBRIC_DIMENSION = {"technical": "technical", "scenario": "problem_solving", "behavioral": "communication", "experience": "role_specific"}


@dataclass
class Question:
    text: str
    category: str
    difficulty: str
    skill: str | None = None
    expected: list[str] = field(default_factory=list)  # "label|alt|alt"
    source: str = "generated"        # generated | recruiter | llm
    rationale: str = ""

    def to_dict(self):
        return asdict(self)


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower())


def _tokens(t: str) -> set[str]:
    return {w for w in norm(t).split() if len(w) > 3}


def too_similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / min(len(ta), len(tb)) > 0.7


def allocate(n: int, categories: list[str]) -> dict[str, int]:
    cats = [c for c in categories if c in CATEGORIES] or list(CATEGORIES)
    weights = {c: DEFAULT_MIX[c] for c in cats}
    total = sum(weights.values())
    counts = {c: int(n * w / total) for c, w in weights.items()}
    order = sorted(cats, key=lambda c: -weights[c])
    i = 0
    while sum(counts.values()) < n:
        counts[order[i % len(order)]] += 1
        i += 1
    return counts


def _difficulty_plan(k: int, level: str, override: str | None) -> list[str]:
    mix = LEVEL_MIX.get(override if override in LEVEL_MIX else level, LEVEL_MIX["mid"])
    plan: list[str] = []
    for d in ("basic", "intermediate", "advanced"):
        plan += [d] * round(k * mix[d])
    while len(plan) < k:
        plan.append("intermediate")
    return plan[:k]


def level_from_years(years: float | None) -> str:
    if years is None:
        return "mid"
    return "junior" if years < 2 else "mid" if years < 6 else "senior"


def generate(job: dict, resume: dict, match: dict, config: dict, custom: list[str] | None = None) -> list[Question]:
    n = int(config.get("n_questions") or DEFAULT_COUNT)
    n = max(1, min(n, 40))
    level = config.get("level") or level_from_years(job.get("years_required") or resume.get("years_experience"))
    custom_qs = [Question(c.strip(), "technical", "custom", None, [], "recruiter") for c in (custom or []) if c.strip()]
    for q in custom_qs:
        q.category = "behavioral" if re.search(r"\b(tell me about a time|describe a time|conflict|teammate)\b", q.text, re.I) else "technical"
    n_generated = max(0, n - len(custom_qs))
    counts = allocate(n_generated, config.get("categories") or list(CATEGORIES))

    # Skills to ask about: required skills the candidate claims first, then claimed skills the job lists, then others
    banked = [s for s in job.get("skills", []) if s in BANK]
    claimed = [s for s in banked if s in match.get("matched", [])] or [s for s in resume.get("skills", []) if s in BANK]
    pool = claimed + [s for s in banked if s not in claimed] or [s for s in resume.get("skills", []) if s in BANK] or ["data structures", "system design"]

    out: list[Question] = []

    def add(q: Question) -> bool:
        if any(too_similar(q.text, x.text) for x in out + custom_qs):
            return False
        out.append(q)
        return True

    # technical
    diffs = _difficulty_plan(counts.get("technical", 0), level, config.get("difficulty"))
    per_skill: dict[str, int] = {}
    for i, d in enumerate(diffs):
        for attempt in range(len(pool) * 3):
            skill = pool[(i + attempt) % len(pool)]
            if per_skill.get(skill, 0) >= max(2, -(-len(diffs) // max(1, len(pool)))):
                continue
            for dd in (d, "intermediate", "basic", "advanced"):
                text, exp = BANK[skill][dd]
                if add(Question(text, "technical", dd, skill, exp, "generated",
                                f"{skill} is required by the job" + (" and claimed on the resume" if skill in claimed else "") + f"; {dd} for a {level} role.")):
                    per_skill[skill] = per_skill.get(skill, 0) + 1
                    break
            else:
                continue
            break

    # experience-based (from the resume), plus probing of required-but-missing skills
    exp_items = (resume.get("projects") or []) + (resume.get("experience") or [])
    exp_n = counts.get("experience", 0)
    made = 0
    for skill in match.get("missing", [])[:1]:  # one probe for the most important resume gap
        if made >= exp_n:
            break
        q = Question(f"This role needs {skill}, which does not appear on your resume. What exposure have you had to it, and how would you get productive quickly?",
                     "experience", "basic", skill if skill in BANK else None,
                     ["exposure|used|worked with|experience|familiar", "similar|related|transfer|equivalent", "learn|plan|ramp|practice|course|docs", "project|example|built|tried"],
                     "generated", f"Resume gap: {skill} is required but not listed.")
        if add(q):
            made += 1
    for item in exp_items:
        if made >= exp_n:
            break
        short = item if len(item) <= 110 else item[:107].rsplit(" ", 1)[0] + "..."
        q = Question(f"Your resume mentions: \"{short}\". What was your specific role, what was the hardest technical decision, and how did it turn out?",
                     "experience", "intermediate", None,
                     ["role|i built|i was responsible|my part|i led|i implemented", "decision|chose|trade-off|alternative|why", "challenge|difficult|hard|problem", "outcome|result|impact|delivered|metric", "learn|improve|next time"],
                     "generated", "Grounds the interview in the candidate's own claimed work.")
        if add(q):
            made += 1
    for text, exp in BEHAVIORAL:  # top up experience if the resume was thin
        if made >= exp_n:
            break
        if add(Question(text, "experience", "intermediate", None, exp, "generated", "Experience question (resume had few details).")):
            made += 1

    # scenario
    scen_n = counts.get("scenario", 0)
    sk_scen = [s for s in pool if "scenario" in BANK.get(s, {})]
    made = 0
    for i in range(scen_n):
        if i < len(sk_scen) and i < max(1, scen_n - 1):
            s = sk_scen[i]
            text, exp = BANK[s]["scenario"]
            ok = add(Question(text, "scenario", "advanced" if level == "senior" else "intermediate", s, exp, "generated", f"Scenario on {s}."))
        else:
            gi = i - len(sk_scen) if i >= len(sk_scen) else i
            text, exp = SCENARIO_GENERIC[gi % len(SCENARIO_GENERIC)]
            ok = add(Question(text, "scenario", "intermediate", None, exp, "generated", "General engineering scenario."))
        made += 1 if ok else 0

    # behavioural
    beh_n = counts.get("behavioral", 0)
    for text, exp in BEHAVIORAL:
        if sum(1 for q in out if q.category == "behavioral") >= beh_n:
            break
        add(Question(text, "behavioral", "intermediate", None, exp, "generated", "Behavioural (STAR) question."))

    # fill any shortfall (deduplication can drop questions) from the remaining bank
    cats_cycle = [c for c in (config.get("categories") or CATEGORIES) if c in CATEGORIES] or list(CATEGORIES)
    for skill in pool + list(BANK):
        if len(out) >= n_generated:
            break
        for dd in ("intermediate", "basic", "advanced"):
            text, exp = BANK[skill][dd]
            if "technical" in cats_cycle and add(Question(text, "technical", dd, skill, exp, "generated", "Filled to reach the requested count.")):
                break
    out = out[:n_generated]

    # order: easy -> hard, interleaving categories; recruiter questions lead
    rank = {"basic": 0, "custom": 1, "intermediate": 1, "advanced": 2}
    behav = [q for q in out if q.category == "behavioral"]
    rest = sorted([q for q in out if q.category != "behavioral"], key=lambda q: rank[q.difficulty])
    ordered = rest[:1] + behav[:1] + rest[1:] + behav[1:]
    return custom_qs + ordered
