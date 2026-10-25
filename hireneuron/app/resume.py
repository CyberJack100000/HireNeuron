"""Resume (PDF/DOCX/TXT) text extraction and rule-based parsing, plus job-description skill extraction."""
import io
import re
import zipfile
from xml.etree import ElementTree

from .skills import CERT_HINTS, DEGREE_HINTS, SKILLS

MAX_BYTES = 8 * 1024 * 1024
MAX_XML_BYTES = 20 * 1024 * 1024
MAX_TEXT = 60_000
HEADINGS = {
    "summary": r"summary|profile|objective|about me",
    "experience": r"(?:work |professional )?experience|employment(?: history)?|internships?",
    "projects": r"(?:academic |personal |key )?projects?",
    "education": r"education|academics?|qualifications?",
    "skills": r"(?:technical |key )?skills|technologies|tech stack",
    "certifications": r"certifications?|certificates?|courses|achievements|awards",
    "responsibilities": r"responsibilit(?:y|ies)|what you(?:'ll| will) do|duties|role",
    "requirements": r"requirements?|qualifications|what we(?:'re| are) looking for|must have",
}
_HEAD_RE = {k: re.compile(rf"^\s*(?:{v})\s*:?\s*$", re.I) for k, v in HEADINGS.items()}


class ResumeError(Exception):
    def __init__(self, msg, status=422):
        super().__init__(msg)
        self.status = status


def extract_text(filename: str, data: bytes) -> str:
    return _extract_text(filename, data)[:MAX_TEXT]


def _extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ResumeError("File too large (max 8 MB).", 413)
    name = (filename or "").lower()
    if name.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="replace")
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        try:
            text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages[:30])
        except Exception:
            raise ResumeError("Could not read this PDF.")
        if len(text.strip()) < 30:
            raise ResumeError("This PDF has no text layer (scanned image). Upload a text PDF, DOCX or TXT.")
        return text
    if name.endswith(".docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if z.getinfo("word/document.xml").file_size > MAX_XML_BYTES:     # a small file can expand to gigabytes (zip bomb)
                    raise ResumeError("This DOCX is too large to read safely.", 413)
                root = ElementTree.fromstring(z.read("word/document.xml"))
        except ResumeError:
            raise
        except Exception:
            raise ResumeError("Could not read this DOCX.")
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paras = ["".join(t.text or "" for t in p.iter(ns + "t")) for p in root.iter(ns + "p")]
        return "\n".join(paras)
    raise ResumeError("Unsupported file type. Upload PDF, DOCX or TXT.", 415)


def _skills_in(text: str) -> dict[str, int]:
    low = text.lower()
    found = {}
    for skill, aliases in SKILLS.items():
        n = sum(len(re.findall(r"(?<![\w.])" + re.escape(a) + r"(?![\w])", low)) for a in aliases)
        if n:
            found[skill] = n
    return found


def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"_top": []}
    cur = "_top"
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        head = next((k for k, rx in _HEAD_RE.items() if rx.match(s)), None)
        if head:
            cur = head
            sections.setdefault(cur, [])
        else:
            sections.setdefault(cur, []).append(s)
    return sections


def _bullets(lines: list[str]) -> list[str]:
    return [re.sub(r"^[\-•*●▪\d.)\s]+", "", l).strip() for l in lines if len(l.strip()) > 3]


def _years(text: str) -> float | None:
    vals = [float(x) for x in re.findall(r"(?<![\d.])(\d{1,2}(?:\.\d)?)[ \t]{0,3}\+?[ \t]{0,3}(?:years?|yrs?)\b", text, re.I)]
    vals = [v for v in vals if v <= 40]
    if vals:
        return max(vals)
    total = 0.0
    for a, b in re.findall(r"\b((?:19|20)\d{2})\s*[-–to]+\s*((?:19|20)\d{2}|present|current)\b", text, re.I):
        end = 2026 if not b.isdigit() else int(b)
        total += max(0, end - int(a))
    return total or None


_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}")      # every part bounded: an unbounded "[\w.+-]+@" rescans the whole text from each position


def find_email(text: str) -> str | None:
    m = _EMAIL.search(text[:20000])
    return m.group(0) if m else None


def parse_resume(text: str) -> dict:
    sec = split_sections(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    name = next((l for l in lines[:4] if len(l.split()) <= 5 and not re.search(r"[@\d]", l)
                 and not any(_HEAD_RE[k].match(l) for k in _HEAD_RE)), None)
    email = find_email(text)
    skills = _skills_in(text)
    edu = [l for l in lines if any(h in l.lower() for h in DEGREE_HINTS)][:4]
    certs = _bullets(sec.get("certifications", [])) or [l for l in lines if any(h in l.lower() for h in CERT_HINTS)]
    projects = _bullets(sec.get("projects", []))
    experience = _bullets(sec.get("experience", []))
    return {
        "name": name, "email": email, "skills": sorted(skills, key=lambda s: -skills[s]), "skill_mentions": skills,
        "years_experience": _years(text), "education": edu, "projects": projects[:8],
        "experience": experience[:12], "certifications": certs[:6], "word_count": len(text.split()),
    }


def parse_job(title: str, description: str, skills: list[str] | None = None) -> dict:
    sec = split_sections(description)
    extracted = _skills_in(description)
    given = [s.strip().lower() for s in (skills or []) if s.strip()]
    required = []
    for s in given:  # map recruiter-entered skills onto taxonomy names where possible
        canon = next((k for k, al in SKILLS.items() if s == k or s in al), s)
        if canon not in required:
            required.append(canon)
    for s in sorted(extracted, key=lambda k: -extracted[k]):
        if s not in required:
            required.append(s)
    return {
        "title": title, "skills": required[:14],
        "responsibilities": _bullets(sec.get("responsibilities", []))[:8],
        "requirements": _bullets(sec.get("requirements", []))[:8],
        "years_required": _years(description),
    }


def match_skills(job_skills: list[str], resume_skills: list[str]) -> dict:
    rs = set(resume_skills)
    matched = [s for s in job_skills if s in rs]
    missing = [s for s in job_skills if s not in rs]
    return {"matched": matched, "missing": missing,
            "coverage": round(len(matched) / len(job_skills), 2) if job_skills else None}
