# HireNeuron: briefing for the presentation and for judges' questions

Everything below describes what the prototype really does today. Where something is limited or untested on real hardware, it says so. Judges
trust a team that knows its own limits more than one that overclaims.

---

## 1. The one-minute version

**Problem.** First-round interviews take recruiters hours, are inconsistent from candidate to candidate, and remote interviews are easy to cheat in
(a helper off-camera, a second device, looking answers up).

**HireNeuron.** A web app where an AI interviewer reads the job description and the candidate's resume, writes relevant questions, asks them
by voice, listens, asks follow-ups, scores the answers with quoted evidence, and watches for integrity problems with the primary camera, a
phone as a second camera, the microphone and the browser. Everything lands on a recruiter dashboard where a **human makes the decision**.

**Our stance, in one sentence.** The AI gathers evidence; it never decides. Flags are labelled *observed* or *inferred*, scoring looks only at what
was said, nobody is auto-rejected, and every action by either side is written to a tamper-evident record.

---

## 2. How it works (architecture)

```
 Recruiter browser                    FastAPI server (Python)                   Candidate browser
 ─────────────────                    ─────────────────────                     ─────────────────
 create interview  ───────────────▶   resume + JD analysis                      consent, device check,
 live call, controls  ◀───WebSocket──▶ question generation                       eye-tracking calibration
 report, overrides                    interview state machine  ◀──────────────▶  spoken / typed answers
                                      scoring + proctoring rules                 face + head + audio detection
                                      hash-chained audit log                     (runs IN the browser)
                                      SQLite + recording files        ▲
                                                                      │ WebRTC, peer to peer (server only relays setup messages)
                                                          Phone browser (second camera) ──▶ candidate page ──▶ interviewer
```

**Stack.** Python 3 + FastAPI + SQLite on the server. Plain HTML/JavaScript on the front end, no build step. MediaPipe Face Landmarker (face,
head pose, eye landmarks) running in the candidate's browser. Web Speech API for speech-to-text and text-to-speech. WebRTC for the phone camera and
the interviewer's live call. WebSockets for signalling. About 4,000 lines of Python and JavaScript plus tests.

**Why detection runs in the browser.** Video never has to be uploaded to be analysed. Only small snapshots of flagged moments and the consented
recording are sent. That is better for privacy and cheaper to scale (the server does no video processing).

**LLM optional.** With an Anthropic API key the system can use an LLM for question writing and grading. Its grading must quote the answer verbatim,
otherwise it falls back to the built-in rules. Without a key everything still works (rule-based). *The LLM path has only been tested with a stub.*

---

## 3. The interview lifecycle, mapped to the problem statement (A to G)

| PS section | What the judges will see | How it works |
| --- | --- | --- |
| **A. Setup and onboarding** | Recruiter enters job title, description, skills, level, uploads a PDF/DOCX/TXT resume, sets duration, number of questions (default 10), categories, difficulty. The candidate opens a link, reads the rules, gives consent, tests camera and microphone. | Resume parsing extracts skills, years of experience, education, projects, certifications. Limits on file size and a zip-bomb guard. Consent is stored and required. |
| **B. Question generation** | A preview of the questions with the reason each was chosen. Recruiter can remove, reorder, or add their own questions. | Skills in the JD are intersected with the resume. Mix of technical, behavioural, experience and scenario questions. Difficulty follows the level (junior to senior). Near-duplicates are removed. Skills the JD needs but the resume lacks get a "how would you get productive" question. 25-skill question bank; other skills fall back to generic ones. |
| **C. AI interviewer** | An interviewer panel that speaks each question, the candidate answers by voice or typing, follow-ups when an answer is thin, a timer, skip, silence handling. | Text-to-speech reads the question; speech-to-text fills the answer box, which the candidate can edit. A small, tested state machine makes the start/stop speaking button reliable. Follow-ups are capped (`max_followups`) so the interview never overruns. Answers are saved safely against double-clicks and flaky networks. |
| **D. Proctoring** | A timeline of events with timestamps, descriptions, severity and snapshot evidence. | Face absent, multiple faces, head turned away, camera or microphone lost, tab switch, window blur, fullscreen exit, paste into the answer box, unexpected audio. Repeated events inside 8 seconds are merged (false-alarm control). **Observed** events come from detectors; **inferred** patterns (for example, repeated look-aways) are derived separately and worded as possibilities with the events they are based on. |
| **E. Phone as second camera** | A QR code on the candidate screen; scanning it opens a page that streams the phone camera. The recruiter sees both views. | Short-lived pairing token, WebRTC peer to peer (server only relays setup), people-count check on the phone view, disconnect logged, phone view **recorded** and shown on the report, camera **switched off automatically when the interview ends**. |
| **F. Evaluation** | 1 to 5 per question, four dimensions (technical knowledge, problem solving, communication, role-specific), strengths, gaps, what needs further assessment, and the answer's own words quoted as evidence. | Each question carries expected concepts; answers are checked against them and the matched sentence is quoted. Scores look **only at content**, never speed, accent, face or appearance. The AI's original score is always kept next to any human override. |
| **G. Dashboard and report** | Search, filter, live status, per-candidate report with answers, scores, timeline, recording, decision and notes. | Recruiter can override any score with a reason, mark a flag "not an issue" or "confirm concern", re-run scoring (overrides survive), export CSV/JSON, print to PDF. |

---

## 4. Features that go beyond the problem statement

1. **Gaze attention check ("blue dot").** The interviewer can switch on random moving dots; the candidate must follow them with their eyes. Calibration
   uses 13 points, ridge regression and leave-one-dot-out validation, handles glasses (it picks the most reliable signal and refuses to run when the signal
   is too poor), and a miss is only a low-severity flag. The interviewer can make it **required** (the interview will not start without it) and
   **bypass** it for any candidate.
2. **The interviewer can join like a video call**, turn their camera and microphone on, send messages to the candidate, add time, end the interview,
   and change settings while it runs.
3. **Go back to earlier questions**, switchable per candidate or globally, even mid-interview. Revisions are kept in a history.
4. **Full activity record (audit trail).** Every action by interviewer and candidate, plus a transcript of what each said, in one ordered, hash-chained log.
   Editing or removing an entry in the middle shows "Record altered". Deleting an interview leaves a trace that it happened.
5. **Recording of both voices** (the interviewer's voice is mixed in) and a separate phone-camera recording.
6. **Dark mode, accessibility, mobile layouts.** Zero accessibility violations in automated audits across all pages, both themes, desktop and phone width.
7. **404 pages**, link expiry, abandoned interviews finish automatically.

---

## 5. Our answer to each judging criterion

| Criterion | Our answer |
| --- | --- |
| Accuracy and relevance of questions | Built from the JD-resume overlap, shows the rationale per question, difficulty by level, duplicates removed, recruiter can edit before sending. Honest limit: 25-skill bank; LLM option for open-ended roles. |
| Quality of AI interaction and follow-ups | Spoken questions, adaptive follow-ups based on which expected concepts the answer missed, context kept, capped so the interview stays on time. |
| Transcription and evaluation | Browser speech-to-text, editable by the candidate. Scoring against expected concepts with quoted evidence, four rubric dimensions, human override. |
| Primary and secondary camera | Both views live to the interviewer, both recorded, people check on both, disconnect logged, auto-off at the end. |
| Identify simulated events, manage false alarms | A "simulate event" demo mode (add `?demo=1` to the candidate link). Cooldown merging, observed vs inferred, low severity for ambiguous signals, calibration-relative head pose, human review on every flag, nothing automatic. |
| Dashboard quality | Search/filter/live status, full report, overrides that keep the AI's original score, exports, print. |
| Integration, reliability, privacy, security | Privacy: detection in the browser, consent required, delete-everything button. Security: signed sign-in cookie, rate-limited login, hardened inputs (path traversal, zip bomb, regex DoS, CSV formula injection), security headers, tamper-evident log. Reliability: idempotent answer saving, per-interview locks, retries for recordings, wake lock. 73 automated server tests, 27 front-end maths tests, and browser suites using a real browser. |
| Innovation, scalability, clarity | Gaze dot check, audit trail, two-way call. Scalability: video analysis is on the candidate's device, the server only stores events and recordings (see section 8 for what would change at scale). |

---

## 6. Demo script (about 5 minutes)

1. **(20 s) Frame it.** "AI gathers evidence, humans decide."
2. **(60 s) Recruiter creates an interview.** Dashboard, New interview, "Fill with a sample job and resume", analyse. Show the questions and the reason for each; remove one, add your own. Turn on the eye-tracking check, create, copy the link.
3. **(90 s) Candidate side.** Open the link in a private window. Consent, camera and microphone check, eye-tracking calibration, scan the QR with a phone (or open the phone page in a second tab). Start. The interviewer speaks; answer one by voice, one by typing.
4. **(60 s) Interviewer joins live.** Recruiter window, "Join the live call": both videos visible, phone view visible, send a message, change a setting.
5. **(60 s) Cheating simulation.** Candidate link with `?demo=1` shows buttons that fire events (second face, tab switch). Look away, cover the camera, paste text. Watch them appear in the timeline.
6. **(60 s) Report.** End the interview. Show scores with quoted evidence, flags with snapshots and the recording, override a score, mark one flag "not an issue", save a decision. Open "Full activity record" and show the transcript and "Record intact". Unplug one thing to show the phone camera shutting off at the end.

*Fallback if the network or camera fails:* "Load a simulated demo interview" on the dashboard shows a finished report immediately.

**Before the demo.** Use Chrome or Edge on the candidate machine. Use an HTTPS address (a Codespace or tunnel) if a phone is involved. Change the default recruiter key. Do a full run-through the day before.

---

## 7. Suggested slide outline (about 12 slides)

1. **Title:** HireNeuron, AI video interviews you can trust. Team, one-line pitch.
2. **The problem:** screening takes hours, is inconsistent, remote cheating is easy.
3. **Our idea:** AI interviewer plus integrity watch; human decides. Show the lifecycle in one line.
4. **Live demo flow:** the 10-step workflow as a diagram.
5. **Smart questions:** JD and resume overlap, categories, difficulty, example questions.
6. **The interviewer:** voice, follow-ups, time limits, reliability (silence, network).
7. **Proctoring:** the signals list, observed vs inferred, false-alarm control.
8. **Second camera and eye check:** QR pairing, both feeds, blue-dot attention test.
9. **Fair evaluation:** rubric, quoted evidence, content only, human override.
10. **Recruiter dashboard and audit trail:** report screenshot, tamper-evident record.
11. **Privacy, security, reliability:** browser-side detection, consent, hardening, tests.
12. **Limits and roadmap:** honest list, then next steps. Close with the "AI gathers, humans decide" line.

Screenshots worth capturing: the question preview, the live call with both cameras, the proctoring timeline with a snapshot, the report with quoted evidence, the activity record, the dark-mode report.

---

## 8. Likely questions from judges, and honest answers

**Is the AI actually doing the interviewing, or is it a script?**
The question set is generated from the job and resume, and follow-ups depend on which expected concepts the candidate missed. By default this is a rule/template engine, which makes it predictable and explainable. An LLM can be plugged in for question writing and grading; its answers must quote the candidate verbatim or the system falls back to rules. *We have only tested the LLM path with a stub.*

**How do you stop it unfairly accusing candidates?**
Detectors write *observed* events. A separate step derives *inferred* patterns and labels them as possibilities with the evidence attached. Looking away alone is low severity, repeated events are merged, head pose is measured against the candidate's own calibration, and no flag affects the score or disqualifies anyone. The recruiter marks each flag "not an issue" or "confirm concern".

**Can a candidate fool the system?**
Yes, a determined one can, and we say so. Detection runs in the candidate's browser, so a hostile client could tamper with it, and the eye-tracking calibration result is reported by the browser. The server does enforce the rules it can (start is blocked without consent and required calibration), and the second camera, the recording and the audit log give a recruiter independent evidence. Hardening against a hostile browser would need server-side verification, which is a roadmap item.

**How accurate is the eye tracking, with glasses?**
It is webcam gaze estimation, which is inherently imprecise, and we treat it that way: it is optional, a miss is only a low-severity hint, and the recruiter can bypass it. In our simulator it passed honest users (including glare and glasses) and failed fixed-gaze attempts, but *we have not validated it on a panel of real people*; that is the first thing to do with more time.

**Does it detect a second voice?**
Not reliably. The browser can tell loud from quiet, so we flag "unexpected audio activity" as a low-severity hint. Identifying a second speaker needs server-side speaker diarisation. It is on the roadmap.

**What about privacy and consent?**
Face and head detection run in the browser; only snapshots of flagged moments and the consented recording leave the device. Consent for proctoring is required, recording and the phone camera are separate opt-ins, the recruiter can delete everything for a candidate, candidate links can expire, and the audit log records who opened the recording. Spoken answers use the browser's speech service, which may send audio to the browser vendor; a server-side transcriber would avoid that.

**Why is the evaluation trustworthy if it's keyword-based?**
It is a transparent baseline: every score lists the expected points covered and missed and quotes the sentence that matched, so a recruiter can see why. It can miss correct answers phrased differently, which is why overrides keep the AI's original score visible and why the LLM option exists. It deliberately ignores accent, speed and appearance.

**What stops the interviewer abusing the record, or editing it?**
Every entry stores the hash of the previous one; editing or removing one in the middle is detected and shown on the report, and the report shows a fingerprint to save externally. It cannot detect someone with database access rebuilding the whole chain, or cutting entries off the end, so the fingerprint should be stored elsewhere at review time.

**Will it scale?**
Video analysis happens on each candidate's device, so server load is mostly signalling and storage. The prototype uses SQLite and local files and a single server; production would use PostgreSQL, object storage for recordings, a TURN relay for video through strict firewalls, and a worker queue for scoring.

**Does the live call work on any network?**
It uses peer-to-peer WebRTC with a public STUN server and no relay. On some strict networks (hostel or corporate Wi-Fi) it will not connect; adding a TURN server fixes that.

**What did you build versus reuse?**
Reused open source: MediaPipe (face landmarks), FastAPI, segno (QR codes), pypdf and docx readers, and the browsers' speech and WebRTC support. Built by us: the question engine, interview state machine, scoring and evidence quoting, the proctoring event model and false-alarm logic, gaze calibration and attention check, the dashboard, the live call and phone pairing, and the audit trail.

**How did you test it?**
73 automated server tests (including security and hostile-input tests), 27 front-end maths tests, and browser suites that drive the real pages with a fake camera: full interview flow, live call, phone pairing and shutdown, accessibility audits in both themes and at phone width. *Speech was tested against a simulated recogniser, and face/head detection only on a fake camera with no face; test both with real people before relying on them.*

---

## 9. Honest limits (say these before a judge finds them)

- Face and head-pose thresholds are untuned guesses until tested with real people.
- Eye-tracking results come from a simulator, not a user study.
- No reliable second-voice detection.
- Speech runs in the browser's own service (Web Speech), not a server-side model.
- Scoring is concept matching; wording variety can cost a correct answer a point. Overrides are essential.
- A hostile browser could tamper with client-side detection and the calibration result.
- One shared recruiter key (no per-recruiter accounts), SQLite, no TURN relay, no TLS handled by the app itself (use HTTPS from the host).
- Resume PDFs need a text layer (no OCR).

## 10. Roadmap ("what we would build next")

1. Validate detection thresholds and eye tracking with a pilot of real candidates; tune false-alarm rates.
2. Server-side transcription (Whisper) and speaker diarisation, which also gives a better transcript and second-voice detection.
3. Server-side verification of integrity signals, plus TURN for reliable video.
4. Recruiter accounts, roles and per-user audit; PostgreSQL and object storage.
5. LLM-assisted question writing and grading as the default, with the verbatim-quote guard.
6. Bias monitoring: compare score distributions across groups, add a recruiter-facing fairness check.
7. Calendar integration, multi-language interviews, ATS export.

---

## 11. Quick facts and numbers

- Default 10 questions; duration 5 to 120 minutes; follow-ups 0 to 10; four question categories; four rubric dimensions; scores 1 to 5.
- Proctoring event types: 14 detector and system types, 5 interviewer-action types and 3 gaze-check types (22 in all, see `app/proctor.py`).
- Repeated identical events are merged within 8 seconds; at most 1,500 events per interview.
- Gaze calibration: 13 points; attention checks 1 to 8 per interview, 5 to 15 seconds each.
- Activity record limits: 20,000 entries and 6,000 transcript lines per interview.

## 12. Where things are in the code (for "show us the code")

- `app/questions.py`, `app/skills.py`: question generation. `app/resume.py`: resume parsing. `app/service.py`: setup and input limits.
- `app/interview.py`: interview state machine (follow-ups, go back, finish). `app/evaluate.py`: scoring and evidence.
- `app/proctor.py` and `web/proctor.js`: event types, merging, inference, face and head detection. `web/gaze.js`: calibration and attention check.
- `app/signaling.py`, `web/mobile.html`: phone pairing. `app/live.py`: interviewer's live call. `app/audit.py`: tamper-evident record.
- `web/candidate.js`, `web/recruiter.js`: the two front ends. `tests/`, `web/test/`: automated tests.
