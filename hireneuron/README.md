# HireNeuron: AI video interviewer with proctoring (hackathon PS1)

A working prototype of the full lifecycle in `HireNeuron_PS1.txt`: recruiter setup, resume + JD analysis, AI question
generation, a voice-led candidate interview, proctoring with a timestamped incident log, a phone as a second camera,
evidence-based scoring, and a recruiter dashboard. FastAPI + SQLite backend, plain HTML/JS front end (no build step).

## Run it

```bash
cd hireneuron
python -m venv .venv && source .venv/bin/activate     # Windows: python -m venv .venv  then  .venv\Scripts\activate
pip install -r requirements.txt
python scripts/fetch_vision.py        # one-off: downloads the face-detection model + library into web/vendor (needs internet)
python -m uvicorn app.main:app --port 8000     # default recruiter key is "demo-recruiter" (set HN_RECRUITER_KEY to change)
pytest -q                              # 59 backend tests
node --test "web/test/*.test.mjs"      # 15 eye-tracking maths tests + 12 speech-button tests (keep the quotes)
```

Open http://localhost:8000, sign in with the recruiter key, then **Load a simulated demo interview** to see a finished
report immediately, or **New interview → Fill with a sample job and resume** to run the real flow. Add `?demo=1` to a
candidate link to show buttons that simulate proctoring events for your demo.

There is a **Dark mode** button in the top bar of every page (it follows your system setting until you press it, and
remembers your choice).

**Use Chrome or Edge on the candidate machine** (spoken answers use the browser's speech recognition and voice; other
browsers fall back to typed answers).

### Running in a GitHub Codespace
Start the server with `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`, then in the Ports tab set port 8000 to **Public**. The app works out its own
HTTPS address from the Codespace name (for example `https://infamous-spooky-crematorium-7qgrqvx66xq3x65j-8000.app.github.dev`), so candidate links and the phone QR code
use it without any setting. The address can also be fixed in `public_url.txt` (first line; currently this project's Codespace), and `HN_PUBLIC_URL` overrides both. **When you move to a new Codespace or tunnel, edit `public_url.txt`** (or delete the file to go back to automatic detection). Change `HN_RECRUITER_KEY` before making the port public.

### Second camera (phone)
The phone's video goes to the candidate's page, is checked there (more than one person in view is flagged), is forwarded live to the interviewer's call page (a tile that always
shows whether a phone is connected), and is recorded separately (one file per connection, shown under the main recording on the report). When the interview ends, for any
reason, the phone page switches its camera off and the pairing link stops working; a phone that reconnects afterwards is told to stay off.

### Second camera on a real phone needs HTTPS
Phone browsers only allow the camera on HTTPS pages (or localhost). For a phone, expose the app over HTTPS and tell it
its public address:

```bash
cloudflared tunnel --url http://localhost:8000      # or: ngrok http 8000
HN_PUBLIC_URL=https://<the-tunnel-address> uvicorn app.main:app --port 8000
```
Open the candidate link from that same https address. On one computer, localhost works for testing (two browser tabs).

## What maps to what

| PS requirement | Where | Status |
| --- | --- | --- |
| A. Interview setup, resume PDF/DOCX/TXT, config, default 10 questions, consent + device check | `web/recruiter.js`, `app/resume.py`, `web/candidate.js` | Done, browser-tested |
| B. Question generation (JD ∩ resume, 4 categories, difficulty by level, recruiter questions, no duplicates) | `app/questions.py`, `app/skills.py` (+ optional LLM in `app/llm.py`) | Done: rule/template engine over a 25-skill bank |
| C. Voice interviewer, STT/TTS, follow-ups, time limits, silence/offline handling, answer tracking | `web/candidate.js`, `app/interview.py` | Done. Follow-ups capped by `max_followups` so the interview never overruns |
| D. Proctoring: face absence, multiple faces, head pose, audio, feed loss, tab focus, incident log with evidence | `web/proctor.js`, `app/proctor.py` | Done except additional-voice detection (see limits) |
| E. Phone as second camera: QR, token pairing, WebRTC feed, disconnect log, people check | `app/signaling.py`, `web/mobile.html`, `web/candidate.js` | Done; WebRTC pairing browser-tested |
| F. Evaluation: 1-5 rubric, 4 dimensions, evidence, strengths/gaps, summary | `app/evaluate.py` | Done (rule-based; LLM optional) |
| G. Dashboard: report, Q&A, scores, timeline, recording, overrides | `web/recruiter.js` | Done |

## Design decisions worth saying out loud in the demo

- **Observed vs inferred.** Detectors write *observed* events. A separate pass derives *inferred* patterns (for example
  three look-aways in five minutes) and words them as possibilities with the basis events attached ("Not proof of
  misconduct"). Looking away alone is `low` severity. Nothing disqualifies anyone; recruiters mark events "not an issue"
  or "confirm concern", and that decision survives recomputation.
- **False-alarm control.** Detectors debounce on the client (face absent 6 s, several faces 1.5 s, looking away 4 s
  against a per-candidate calibrated head pose), the server merges repeats within 8 s, and the log shows severity.
- **Scoring ignores delivery.** Answers are scored on expected concepts for the specific question and the job, with the
  quoted sentences shown as evidence. Speed, accent, face and appearance are never inputs; a test checks that filler
  words do not change the score. Proctoring is not part of the score.
- **LLM is optional and cannot invent evidence.** With `ANTHROPIC_API_KEY` set, answers are graded by the model, but its
  quotes must appear verbatim in the answer or the rules grader is used instead. Everything works with no key.
- **Privacy.** Explicit consent gate (monitoring required, recording and second camera optional); snapshots only on a
  flagged event; recordings and evidence are served only to the recruiter key; one-click "Delete all data".

## Interviewer controls (all can be changed while an interview is running)

| Control | Where | What it does |
| --- | --- | --- |
| **Require eye-tracking calibration** | New interview form | The candidate cannot start until calibration succeeds. The server enforces it too (`409` on start), not just the button. |
| **Bypass for one candidate** | Interview page or live call → *Let this candidate start without eye tracking* | Unlocks Start within about 3 seconds, without the candidate reloading. The report records that the checks did not run because of the bypass. |
| **Eye-tracking checks on/off for one candidate** | Same panel | Turning it off during a dot check closes it within seconds and records nothing. |
| **Allow going back to earlier questions** | New interview form, per candidate, or globally | The candidate sees chips for earlier questions, can edit and save an answer (earlier versions are kept and shown in the report), and returns to where they were; unsent drafts are kept. |
| **Global switches** | *Settings* | Defaults for new interviews, and kill switches for going back and for eye tracking that override every interview, including running ones. |
| **Add time / message the candidate / end now** | Interview page or live call → *Controls* | *+5/+10/+15 min* extends the clock (up to 120 minutes in total) and the candidate's timer follows within seconds. A message appears on the candidate's screen and is kept in the record. *End the interview now* finishes it, marks unanswered questions, runs scoring and closes the candidate's page (with a confirmation). |
| **Link expiry** | New interview form | Optional: the candidate link stops working after 24 hours, 3, 7 or 30 days if the interview has not started. A running interview is never cut off by it. |
| **Interview another candidate for this job** | Interview page | Opens the new-interview form with the job, skills and every setting prefilled, ready for the next resume. |
| **Dashboard** | Home | Search, status filter, live progress for running interviews ("2 of 10 answered, candidate online"), one-click *Join call* and *Copy link*, refreshes itself every 5 seconds. Abandoned interviews are finished and scored automatically ten minutes after their time ran out. |
| **Export and print** | Interview page | *Download CSV* (one row per question; cells that start with `=`, `+`, `-` or `@` are neutralised so a candidate's answer cannot run as a spreadsheet formula), *Download JSON*, and *Print or save as PDF* (a print layout without the controls). |
| **Join the live call** | Interview page → *Join the live call* | See and hear the candidate and their phone camera, talk to them (mic/camera buttons), change the controls above, see the current question and recent flags. Several recruiters can join. |

Precedence: global kill switch, then the per-candidate override, then the interview's own setting. The candidate page picks
changes up every 3 seconds. The candidate sees a notice and the interviewer's tile whenever someone joins, joining and leaving
are logged in the timeline, and the consent text says a recruiter may join live. Calls are peer-to-peer WebRTC (STUN only,
no TURN relay), so they can fail between very strict corporate networks.

## Activity record (for disputes and foul play)

Every interview keeps one ordered record of what **both sides** did. It is on each report under "Full activity record" and in `export.json`.

- **Interviewer actions:** joining and leaving the call, messages, extra time, going-back / eye-tracking / bypass switches, score overrides, decisions, flag reviews, re-scoring, opening the recording, exporting, ending the interview. Global setting changes, recruiter sign-ins (and failed ones) and interview deletions go to a separate global record, so deleting an interview still leaves a trace that it happened.
- **Candidate actions:** consent, start, each answer (length and fingerprint only; the text is in the answers table), skips, going back, eye calibration, proctoring events, recording start, finish.
- **Transcript:** what the candidate dictated and what the interviewer said (with their microphone on, in the live call), each line stamped with the server's own time offset. It comes from each browser's speech recogniser, so it can miss words and a voice can be picked up on the other person's microphone. The video recording (which now mixes in the interviewer's voice when they are on the call) is the authoritative record.
- **Tamper evidence:** each entry stores the hash of the one before it. Editing or removing an entry in the middle shows as "Record altered". It cannot detect someone with database access rebuilding the whole chain, or entries cut off the end, so copy the "fingerprint" shown on the report when you review; if it changes later without new activity, the record was changed.
- Limits: 6,000 transcript lines and 20,000 entries per interview. Deleting an interview deletes its record.
- **Tell people:** the consent screen already covers recording and proctoring; make sure your own notice also says the interviewer's actions and spoken words are logged.

## Gaze attention check (optional, the "blue dot")

Tick **Random gaze attention check** when creating an interview. The candidate calibrates their eyes once (13 dots, about
20 seconds). Then, a few times at random moments, a blue dot moves across the screen for about 8 seconds and the app
compares the candidate's gaze path with the dot's path (`web/gaze.js`).

**How it copes with glasses and different faces.** Each frame gives eight numbers: iris position (landmarks), the model's own
eye-direction scores (blendshapes), head direction and head position (3D face matrix). Calibration tries several combinations,
uses leave-one-dot-out cross-validation to measure how well each predicts a dot it has not seen, drops a signal group whose
signal-to-noise is poor (glare or frame reflections ruin the iris landmarks first), and keeps the richest model that is within
5% of the best. Blinks are skipped, single-frame glare spikes are median-filtered, the comparison allows a natural reaction delay
of up to 0.6 s, and a slope check stops tiny eye movements passing. Calibration reports its quality (good, fair, limited or
poor) and, if eye signals were unusable, says that head movement counts more. If it is poor the candidate gets glasses tips
and the check does not run, which counts against nobody. Correlation, not "is the gaze exactly on the dot", is used because
webcam gaze is only accurate to a few degrees and correlation ignores constant offsets.

How it is kept fair (this is deliberate, and matches the problem statement's "looking away is not proof of cheating"):

- The candidate is told in the rules and the consent text that dot checks will happen, though not when. They can press
  Escape or "Skip this check" at any time.
- One miss is a **low-severity observed event** that shows the match score. Only two or more misses (at least half of all
  checks) create an **inferred** flag, worded with the innocent explanations (glasses, lighting, camera position, eye conditions).
- If calibration fails, the face is not visible for most of a check, or the candidate skips, the result is "could not run".
  That never counts against the candidate and is shown to the recruiter as such.
- The question timer pauses during a check, and the dot is drawn above the instruction text so it is never hidden.

**What the tests show, and what they do not.** `node --test web/test/gaze.test.mjs` drives the maths with *simulated* faces
(normal, glasses-like noise with glare flashes, heavy glare where the iris is useless, slow reaction, head drift, blinks,
missing blendshapes). Current results: honest candidates pass 100% in every case; a candidate whose eyes stay fixed elsewhere
fails 100%; a randomly wandering gaze falsely passes about 4%; missing frames give "unavailable", never a miss. The browser
flow is tested with a simulated eye tracker (requirement, bypass, switch-off mid-check, calibration, pass, miss).
**These numbers come from a simulator I wrote, not from real people.** The simulator proves the logic, not the accuracy of
MediaPipe on real eyes behind real lenses. Before relying on it, test with real people (with and without glasses, different
lighting, laptop vs external webcam, and a few people deliberately looking elsewhere), then tune `calibrate` and `score`
in `web/gaze.js` (grade limits 0.12/0.2/0.3 of the screen, pass score 0.55, minimum slope 0.3).

## Security and reliability

- **Sign-in is a cookie, not a key in the URL.** `POST /api/login` sets an HttpOnly, SameSite=Lax cookie (Secure when served over HTTPS or
  when `HN_PUBLIC_URL` is https). The recruiter key is never accepted in a URL (so it cannot land in access logs or browser history), is
  not stored in the browser, and wrong keys are throttled (10 per 5 minutes per address). Scripts can still send `X-Recruiter-Key`.
- **Every recruiter route is checked.** A test walks all routes and fails if one is reachable without signing in, or if a new route is
  neither protected nor on the explicit open list.
- **Ids can't reach the file system.** Interview ids must be 12 hex characters. Before this, deleting an interview built a path from the
  URL, so an id of `..` could have deleted the data folder; a test now covers it.
- **Concurrency.** Requests for one interview are serialised, and answers carry a request id, so a double click or a retry after a lost
  response is applied once (a retried answer can no longer be mistaken for the answer to a follow-up question). Tested with parallel threads.
- **Hostile input.** Regular expressions were audited and bounded (a 60,000-character resume used to take 15 seconds because of the email
  pattern); DOCX zip bombs, oversized uploads and recordings, huge answers and runaway event logs are capped. A fuzz run of 36 hostile inputs
  against each parser stays under 0.4 s, and the harness is validated by a control that must flag the old vulnerable pattern.
- **Windows file types.** Python takes file types from the operating system, and on some Windows machines the registry says `.js` is
  `text/plain`; together with `nosniff` the browser then refuses to run every script. The app fixes the types it serves (a test
  poisons the type table to prove it).
- **Headers.** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, a camera/microphone permissions policy, and
  `Cache-Control: no-store` on every API response.
- **Accessibility.** The axe-core scanner reports zero violations on the recruiter, candidate and phone pages in both themes at 1280 and
  390 pixels wide (it is run as part of my checks, not in CI). Hidden elements are really hidden, headings do not skip levels, keyboard focus
  moves to the page heading after navigating.
- **Candidate resilience.** The page retries answers until the server has them, warns before the tab is closed mid-interview, keeps the
  screen awake, resumes at the same question after a reload (calibration is remembered), and explains camera problems specifically
  (no camera, camera in use, blocked, or not on HTTPS).

## Honest limits

- **Face and head-pose detection has not been tried on real faces.** In the sandbox I verified the MediaPipe model
  loads and runs and that it reports `face_absent` with a snapshot on a fake camera. Head-pose thresholds
  (`web/proctor.js`, about 0.22 yaw / 0.2 pitch relative to calibration) are untuned guesses: test with real people
  and adjust.
- **The calibration result and the bypass are enforced by the server, but the calibration result itself is reported by the
  candidate's browser.** A determined candidate could fake it. This stops accidents and honest mistakes, not a hostile client.
- **No additional-voice detection.** The browser can only tell loud sound from quiet. `audio_burst` means "sound while
  nothing was being said and no words recognised"; identifying a second speaker needs server-side diarisation.
- **Speech runs in the browser** (Web Speech API, which may send audio to the browser vendor). There is no
  server-side transcription yet. The start/stop speaking control is a tested state machine (`web/dictation.js`: one recogniser
  at a time, text edits are kept, the last words are saved on Submit, the interviewer's voice is not transcribed), but it has
  been tested against a simulated recogniser, not Chrome's real speech service.
- **The scorer is keyword/concept matching.** It rewards covering expected points and can miss correct answers that use
  different words, so recruiter overrides matter. The question bank covers 25 common skills; other skills fall back to
  generic questions. Use the LLM option for open-ended roles.
- The LLM path was only tested with a stub (no API key in the sandbox).
- One shared recruiter key (no per-user accounts or audit of which recruiter did what), no TLS termination, only login throttling,
  SQLite on local disk. Fine for a demo; add real accounts and a proper database before production. PDF resumes must have a text layer (no OCR here).

## Layout

```
app/        FastAPI app: main.py (routes), resume.py, questions.py, skills.py, interview.py, evaluate.py, proctor.py,
            flags.py (global + live switches), live.py (call signalling), signaling.py (phone camera), llm.py, demo.py, db.py, service.py
web/        recruiter.html/.js, candidate.html/.js, proctor.js, gaze.js, dictation.js, mobile.html, app.css, common.js, test/, vendor/ (fetched)
data/       sample_jd.txt, sample_resume.txt, runtime/ (database, recordings, evidence; git-ignored)
tests/      59 tests: generation, flow and follow-up cap, scoring, LLM guard, proctoring, pairing, privacy, overrides,
            requirement and bypass, live switches, go-back and revisions, live-call signalling, database upgrade,
            sign-in and route audit, id and path safety, time/message/end controls, exports, hostile input, concurrency, link expiry
```
