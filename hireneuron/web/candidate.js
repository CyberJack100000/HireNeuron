const token = location.pathname.split('/').pop();
const params = new URLSearchParams(location.search);
const app = $('#app');
const C = (p, o = {}) => api(`/api/candidate/${token}${p}`, o);
const RTC = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const S = {
  info: null, flags: null, stream: null, proctor: null, recorder: null, recSeq: 0, recQueue: Promise.resolve(), startedAt: null, started: false,
  paired: false, cur: null, qStart: 0, elapsedPaused: 0, paused: false, submitting: false, speaking: false, qSeq: 0, drafts: {},
  gazeCal: null, gazeRan: 0, gazeAbort: false, gazeTimer: null, gazeRunning: false, interviewerTalking: false, dict: null,
};
const offset = () => (S.startedAt ? (Date.now() - S.startedAt) / 1000 : 0);
const durMin = () => (S.flags && S.flags.duration_min) || S.info.duration_min;
const reqId = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID() : `r${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`).replace(/[^A-Za-z0-9_-]/g, '');
let ui = {}, dev = {};

async function init() {
  document.documentElement.classList.add('js');
  $('#theme-slot').append(Theme.button());
  try { S.info = await C(''); S.flags = await C('/flags'); } catch (e) { return fail(e.status === 404 ? 'This interview link is not valid. Please check the link you were sent.' : e.message); }
  $('#who').textContent = `${S.info.job_title} interview`;
  if (S.info.status === 'completed') return finished();
  setInterval(pollFlags, 3000);
  consentStep();
}
function fail(msg) { app.replaceChildren(h('div', { class: 'panel grid', style: 'max-width:640px' }, h('h1', {}, 'We could not open this interview'), h('p', {}, msg))); }

// 1. Rules and consent -------------------------------------------------------------------------
function consentStep() {
  const i = S.info;
  const cp = h('input', { type: 'checkbox', id: 'c-proc' }), cr = h('input', { type: 'checkbox', id: 'c-rec' }), cs = h('input', { type: 'checkbox', id: 'c-sec' });
  const box = (el, title, text) => h('label', { class: 'check' }, el, h('span', {}, h('strong', {}, title), h('br'), text));
  const btn = h('button', { class: 'btn', disabled: true, onclick: async () => { btn.disabled = true;
    try { await C('/consent', { method: 'POST', json: { proctoring: cp.checked, recording: cr.checked, secondary_camera: cs.checked } }); S.consent = { proctoring: cp.checked, recording: cr.checked, secondary_camera: cs.checked }; deviceStep(); } catch (e) { toast(e.message); btn.disabled = false; } } }, 'Agree and continue');
  cp.addEventListener('change', () => { btn.disabled = !cp.checked; });
  app.replaceChildren(h('div', { class: 'steps' }, h('h1', {}, `Welcome. Your ${i.job_title} interview.`),
    h('p', { class: 'muted' }, `${i.n_questions} questions, about ${i.duration_min} minutes. An AI interviewer asks each question out loud and you answer by speaking. You have up to ${Math.round(i.answer_time_limit_s / 60 * 10) / 10} minutes per answer.`),
    h('div', { class: 'panel grid' }, h('h2', {}, 'Before you start'), h('ul', { class: 'rules' }, i.rules.map((r) => h('li', {}, r)))),
    h('div', { class: 'panel grid' }, h('h2', {}, 'Your consent'),
      box(cp, 'Monitoring (required)', 'Your camera and microphone are analysed during the interview to flag possible integrity concerns for a human recruiter, and a recruiter may watch and talk to you live. Snapshots are saved only when something is flagged.' + (i.gaze_check.enabled ? ' This interview also includes a short eye-tracking calibration and a few random moving-dot attention checks. You can skip any of them.' : '')),
      box(cr, 'Recording (optional)', 'Save a video recording that the recruiter can review.'),
      box(cs, 'Second camera (optional)', 'Connect your phone as a second camera that shows your desk and room.'),
      h('p', { class: 'small muted' }, 'You can ask the recruiter to delete your data at any time.'), h('div', {}, btn))));
}

// 2. Camera, microphone and speech checks ----------------------------------------------------------
async function deviceStep() {
  const video = h('video', { autoplay: true, muted: true, playsinline: true, 'aria-label': 'Your camera preview' });
  const meterEl = h('i'); const rows = {};
  const row = (k, label, hidden) => (rows[k] = h('li', { hidden: !!hidden }, h('span', { class: 'dot wait' }), h('span', {}, label)));
  const set = (k, state, text) => { rows[k].firstChild.className = 'dot ' + state; rows[k].lastChild.textContent = text; };
  const go = h('button', { class: 'btn', disabled: true, onclick: begin }, 'Start the interview');
  const gate = h('p', { class: 'gate', hidden: true, role: 'status' });
  const tips = h('p', { class: 'tips muted', hidden: true });
  const status = h('ul', { class: 'status' }, row('cam', 'Camera: checking'), row('mic', 'Microphone: checking'), row('face', 'Face: checking'), row('speech', 'Speech recognition: checking'), row('gaze', 'Eye tracking: not calibrated yet', true));
  const calBtn = h('button', { class: 'btn', hidden: true, id: 'cal-btn', onclick: async (e) => { e.target.disabled = true; set('gaze', 'wait', 'Eye tracking: calibrating…'); S.gazeAbort = false;
      const r = await runCalibration(); S.gazeCal = r; reportCalibration(r); showCalibration(r); e.target.disabled = false; e.target.textContent = 'Calibrate again'; updateGate(); } }, 'Calibrate eye tracking (about 20 seconds)');
  dev = { set, go, gate, tips, calBtn, rows };
  const practiceBox = h('textarea', { id: 'practice-box', rows: 2, 'aria-label': 'Practice box: your spoken words appear here', placeholder: 'Press "Start speaking", say your name, and watch it appear here. This is only a test; nothing is saved.' });
  const practiceBtn = h('button', { class: 'btn quiet', type: 'button', id: 'practice-btn' });
  const practiceMsg = h('p', { class: 'small muted' });
  S.practice = new Dictation({ textarea: practiceBox, button: practiceBtn, onMessage: (m) => { practiceMsg.textContent = m || ''; } });
  const practice = S.practice.supported ? h('div', { class: 'grid', style: 'gap:8px' }, h('h2', { class: 'sub' }, 'Try your microphone'), practiceBox, h('div', { class: 'actions' }, practiceBtn), practiceMsg) : null;
  app.replaceChildren(h('div', { class: 'steps', style: 'max-width:980px' }, h('h1', {}, 'Check your camera and microphone'),
    h('div', { class: 'cam panel' }, video, h('div', { class: 'grid' }, status, h('div', {}, h('p', { class: 'small muted' }, 'Say a few words. The bar should move.'), h('div', { class: 'meter' }, meterEl)),
      h('div', { class: 'actions' }, h('button', { class: 'btn quiet', onclick: () => speak('This is your AI interviewer. Can you hear me clearly?') }, 'Test the interviewer voice'), calBtn), tips, practice)),
    h('div', { id: 'pair-slot' }), gate, h('div', { class: 'actions' }, go, h('span', { class: 'small muted' }, 'Look at the screen while the face check calibrates.'))));
  try { S.stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: { echoCancellation: true, noiseSuppression: true } }); }
  catch (e) {
    if (!window.isSecureContext || !navigator.mediaDevices) { set('cam', 'bad', 'This page must be opened over HTTPS (or on localhost) before the browser allows the camera. Ask the interviewer for a secure link.'); set('mic', 'bad', 'Microphone unavailable for the same reason.'); return; }
    const why = e && e.name === 'NotFoundError' ? 'No camera or microphone was found. Connect one and reload.' : e && e.name === 'NotReadableError' ? 'The camera is being used by another program. Close it and reload.' : 'Camera blocked. Allow camera and microphone access in your browser, then reload.';
    set('cam', 'bad', why); set('mic', 'bad', 'Microphone: ' + (e && e.name === 'NotFoundError' ? 'not found.' : 'blocked or busy.')); return; }
  video.srcObject = S.stream; await video.play().catch(() => {}); S.video = video;
  set('cam', 'ok', 'Camera: working'); set('mic', 'wait', 'Microphone: say something');
  const ac = new (window.AudioContext || window.webkitAudioContext)(); const an = ac.createAnalyser(); an.fftSize = 512; ac.createMediaStreamSource(new MediaStream(S.stream.getAudioTracks())).connect(an);
  const buf = new Uint8Array(an.fftSize); let heard = false;
  const iv = setInterval(() => { an.getByteTimeDomainData(buf); let s = 0; for (const b of buf) { const x = (b - 128) / 128; s += x * x; } const lv = Math.sqrt(s / buf.length); meterEl.style.transform = `scaleX(${Math.min(1, lv * 6)})`;
    if (lv > 0.04 && !heard) { heard = true; set('mic', 'ok', 'Microphone: working'); } }, 100);
  S.cleanupCheck = () => { clearInterval(iv); ac.close().catch(() => {}); };
  const SRok = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  set('speech', SRok ? 'ok' : 'wait', SRok ? 'Speech recognition: available' : 'Speech recognition is not supported here. You will type your answers instead (Chrome or Edge recommended).');
  S.proctor = new Proctor({ token, primary: video, secondary: null, offset, wantGaze: true, onStatus: (m) => set('face', 'wait', m) });
  S.proctor.start({ stream: S.stream }).then(async () => {
    if (!S.proctor.engine || !S.proctor.engine.kind) return set('face', 'wait', 'Face checks unavailable in this browser');
    set('face', 'wait', 'Face: calibrating, look at the screen');
    const ok = await S.proctor.calibrate(); set('face', ok ? 'ok' : 'wait', ok ? 'Face: detected and calibrated' : 'Face not clearly detected. Move into better light and face the camera.'); });
  if (S.consent.secondary_camera) pairStep($('#pair-slot'));
  Live.connect();
  const saved = loadCalibration(); if (saved && S.flags.calibrated) { S.gazeCal = saved; showCalibration(saved); }
  applyFlags();
}

function updateGate() {
  if (!dev.go) return;
  const g = S.flags.gaze, calibrated = !!(S.gazeCal && S.gazeCal.ok) || !!S.flags.calibrated;       // the server remembers a calibration done earlier in this interview
  const blocked = g.enabled && g.required && !calibrated;
  dev.go.disabled = blocked;
  dev.gate.hidden = !(g.enabled && (blocked || (g.bypass && !calibrated)));
  dev.gate.textContent = blocked ? 'The interviewer requires eye-tracking calibration before this interview can start. Press "Calibrate eye tracking". If it does not work for you, tell the interviewer: they can let you start without it. This page updates by itself when they do.'
    : g.bypass && !calibrated ? 'The interviewer has allowed you to start without eye tracking.' : '';
}
function showCalibration(r) {
  const label = { good: 'good', fair: 'fair', limited: 'limited' }[r.grade];
  const msg = r.ok ? `Eye tracking: ready (${label} accuracy${r.excluded && r.excluded.length ? `; ${r.excluded.join(' and ')} could not be used, so head movement counts more` : ''}). A few random dot checks will run during the interview.`
    : `Eye tracking: ${r.reason}. The recruiter will see that the checks could not run, and it counts against nobody.`;
  dev.set('gaze', r.ok ? (r.grade === 'limited' ? 'wait' : 'ok') : 'bad', msg);
  const glasses = 'Wearing glasses? Tilt the screen slightly down or move lamps so they do not reflect in your lenses, clean the lenses, sit about 50 to 70 cm from the camera with your face centred, and press Calibrate again.';
  dev.tips.hidden = r.ok && r.grade === 'good'; dev.tips.textContent = glasses;
}
function saveCalibration(r) { try { if (r && r.ok) sessionStorage.setItem(`hn_cal_${token}`, JSON.stringify(r)); } catch {} }
function loadCalibration() { try { const r = JSON.parse(sessionStorage.getItem(`hn_cal_${token}`)); return r && r.ok && r.model ? r : null; } catch { return null; } }
async function reportCalibration(r) {
  saveCalibration(r);
  try { await C('/gaze/calibration', { method: 'POST', json: { ok: !!r.ok, mode: r.mode || '', grade: r.grade || '', error: r.cvError != null ? Math.min(5, r.cvError) : null, reason: r.reason || '' } }); } catch (e) { console.warn(e); }
}

// Flags the interviewer can change live (requirement, bypass, gaze on/off, going back) --------------------------------
async function pollFlags() {
  try {
    const f = await C('/flags'); const before = JSON.stringify(S.flags); S.flags = f;
    if (f.status === 'completed' && !S.finishing) return endedByInterviewer();      // finished by the interviewer (or time ran out while the page was away)
    showInterviewerMessage(f.message);
    if (before !== JSON.stringify(f)) applyFlags();
    if (S.started && !S.submitting && S.cur) await syncFromServer(f);
  } catch (e) { /* offline: try again next tick */ }
}
function showInterviewerMessage(m) {
  if (!m) return;
  let seen = 0; try { seen = +sessionStorage.getItem(`hn_msg_${token}`) || 0; } catch {}
  if (m.id <= seen) return;
  try { sessionStorage.setItem(`hn_msg_${token}`, String(m.id)); } catch {}
  document.getElementById('msg-banner') && document.getElementById('msg-banner').remove();
  const b = h('div', { id: 'msg-banner', class: 'msg-banner', role: 'alert' }, h('span', {}, h('strong', {}, 'Message from your interviewer: '), m.text), h('button', { class: 'btn quiet', type: 'button', onclick: () => b.remove() }, 'OK'));
  document.body.append(b);
}
function endedByInterviewer() {
  if (S.finishing) return; S.finishing = true; S.submitting = true;
  clearInterval(S.gazeTimer); S.gazeAbort = true; releaseAwake();
  if (S.recorder && S.recorder.state !== 'inactive') { try { S.recorder.stop(); } catch {} }
  if (S.dict) S.dict.stop(); cancelSpeech();
  endSecondary().then(() => { if (S.ws) S.ws.close(); });
  if (S.proctor) S.proctor.stop(); if (S.stream) S.stream.getTracks().forEach((t) => t.stop()); Live.close(); if (Live.tile) Live.tile.remove();
  finished(true);
}
function applyFlags() {
  const g = S.flags.gaze;
  if (dev.rows) { dev.rows.gaze.hidden = !g.enabled; dev.calBtn.hidden = !g.enabled; if (!g.enabled) dev.tips.hidden = true; updateGate(); }
  if (S.started) {
    if (!g.enabled) { S.gazeAbort = true; clearInterval(S.gazeTimer); S.gazeTimer = null; }
    else if (!S.gazeTimer) startGazeChecks();
  }
}
async function syncFromServer(f) {                         // e.g. the interviewer switched "going back" off while the candidate was on an old question
  const mark = S.qSeq;
  const fresh = await C('/current');
  if (S.submitting || S.qSeq !== mark) return;               // the candidate moved on while this request was in flight: the answer is stale
  if (fresh.done) return finish();
  if (fresh.revisiting !== S.cur.revisiting || fresh.idx !== S.cur.idx || fresh.is_followup !== S.cur.is_followup) {
    toast('The interviewer changed what is allowed. Returning to your current question.'); return showQuestion(fresh);
  }
  S.cur.can_back = fresh.can_back; S.cur.previous = fresh.previous; renderNav();
}

// 3. Optional second camera (phone) --------------------------------------------------------------------
async function pairStep(slot) {
  const status = h('p', { class: 'muted' }, 'Scan the code with your phone camera to connect it.');
  const sec = h('video', { autoplay: true, muted: true, playsinline: true, style: 'max-width:320px', hidden: true, 'aria-label': 'Your phone camera preview' });
  slot.replaceChildren(h('div', { class: 'panel grid' }, h('h2', {}, 'Second camera (optional)'), status));
  let p; try { p = await C('/pair'); } catch (e) { return status.replaceChildren(e.message); }
  const qr = h('div', { class: 'qr', role: 'img', 'aria-label': 'QR code to connect your phone' }); qr.innerHTML = p.qr_svg;   // server-generated SVG, no user content
  slot.firstChild.append(h('div', { class: 'cam', style: 'grid-template-columns:auto 1fr' }, h('div', { class: 'grid', style: 'gap:8px' }, qr, h('p', { class: 'small muted', style: 'overflow-wrap:anywhere;max-width:300px' }, 'Or type this address into your phone: ' + p.pair_url)), h('div', { class: 'grid' }, h('p', {}, 'Place the phone so it shows your desk and the area around you, from the side or behind.'),
    !p.secure_context && h('p', { class: 'banner' }, 'This link is not HTTPS, so phone browsers will refuse the camera. The recruiter needs to serve this app over HTTPS (see the README).'), sec)));
  S.secVideo = sec; S.proctor.secondary = sec;
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/pair/${p.pair_token}/desktop`); S.ws = ws;
  const pc = new RTCPeerConnection(RTC); S.pairPc = pc;
  pc.ontrack = (e) => { S.secStream = e.streams[0]; startSecondaryRecording(); sec.srcObject = e.streams[0]; sec.hidden = false; S.paired = true; status.textContent = 'Phone camera connected. Keep the phone page open.'; S.proctor.watchSecondary(); Live.addSecondary(e.streams[0]); };
  pc.onicecandidate = (e) => { if (e.candidate && ws.readyState === 1) ws.send(JSON.stringify({ type: 'ice', candidate: e.candidate })); };
  ws.onmessage = async (m) => { const d = JSON.parse(m.data);
    if (d.type === 'phone-joined') status.textContent = 'Phone connecting…';
    if (d.type === 'phone-left') { S.paired = false; sec.hidden = true; status.textContent = 'Phone camera disconnected. Scan the code again to reconnect.'; }
    if (d.type === 'offer') { await pc.setRemoteDescription(d); const a = await pc.createAnswer(); await pc.setLocalDescription(a); ws.send(JSON.stringify(pc.localDescription)); }
    if (d.type === 'ice') { try { await pc.addIceCandidate(d.candidate); } catch {} } };
}

// 3b. Live call: the interviewer can join like a video call ---------------------------------------------------------
const Live = {
  ws: null, id: null, pcs: {}, closing: false, retries: 0, remote: { video: null, audio: null }, secStream: null, tile: null,
  connect() {
    if (this.ws || !S.info) return;
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/live/${S.info.interview_id}/candidate?token=${encodeURIComponent(token)}`); this.ws = ws;
    ws.onmessage = (m) => { let d; try { d = JSON.parse(m.data); } catch { return; } this.onMessage(d); };
    ws.onclose = (e) => { this.ws = null; Object.keys(this.pcs).forEach((id) => this.drop(id)); if (!this.closing && e.code !== 4401 && e.code !== 4409 && this.retries++ < 20) setTimeout(() => this.connect(), 3000); };
  },
  send(o) { if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(o)); },
  onMessage(d) {
    if (d.type === 'hello') { this.id = d.id; this.retries = 0; d.peers.filter((p) => p.role === 'recruiter').forEach((p) => this.offerTo(p.id)); }
    else if (d.type === 'peer-joined' && d.role === 'recruiter') { toast('Your interviewer has joined.'); this.offerTo(d.id); }
    else if (d.type === 'peer-left') { if (this.pcs[d.id]) { this.drop(d.id); if (!Object.keys(this.pcs).length) this.hideTile(); toast('Your interviewer has left.'); } }
    else if (d.type === 'answer' && this.pcs[d.from]) { this.pcs[d.from].setRemoteDescription(d).catch(() => {}); }
    else if (d.type === 'ice' && this.pcs[d.from]) { this.pcs[d.from].addIceCandidate(d.candidate).catch(() => {}); }
    else if (d.type === 'state' && this.tile) { this.camOn = !!d.cam; this.refresh(); }
  },
  offerTo(pid) {
    if (this.pcs[pid] || !S.stream) return;
    const pc = new RTCPeerConnection(RTC); this.pcs[pid] = pc; let making = false;
    S.stream.getTracks().forEach((t) => pc.addTrack(t, S.stream));
    if (this.secStream) this.secStream.getVideoTracks().forEach((t) => pc.addTrack(t, this.secStream));
    pc.onicecandidate = (e) => { if (e.candidate) this.send({ type: 'ice', to: pid, candidate: e.candidate }); };
    pc.ontrack = (e) => this.onTrack(e.track);
    pc.onnegotiationneeded = async () => {
      if (making) return; making = true;
      try { this.send({ type: 'meta', to: pid, primary: S.stream.id, secondary: this.secStream ? this.secStream.id : null }); await pc.setLocalDescription(await pc.createOffer()); this.send({ type: 'offer', to: pid, sdp: pc.localDescription.sdp }); }
      catch (e) { console.warn('offer failed', e); } finally { making = false; }
    };
    pc.onconnectionstatechange = () => { if (pc.connectionState === 'failed' && this.pcs[pid] === pc) { this.drop(pid); setTimeout(() => this.offerTo(pid), 2000); } };
  },
  drop(pid) { const pc = this.pcs[pid]; delete this.pcs[pid]; if (pc) { pc.ontrack = pc.onicecandidate = pc.onnegotiationneeded = pc.onconnectionstatechange = null; try { pc.close(); } catch {} } },
  addSecondary(stream) {                                  // forward the phone camera to every recruiter on the call
    this.secStream = stream;
    Object.values(this.pcs).forEach((pc) => { stream.getVideoTracks().forEach((t) => { if (!pc.getSenders().some((s) => s.track === t)) pc.addTrack(t, stream); }); });
  },
  onTrack(track) {
    // Renegotiation (e.g. the phone camera is added) creates extra, empty receiver tracks. A track is only used once media really
    // flows through it, otherwise an empty placeholder would replace the interviewer's real video.
    this.showTile();
    track.onunmute = () => { this.remote[track.kind] = track; this.refresh(); if (track.kind === 'audio') { this.watchAudio(track); Mix.add(track); } };
    track.onmute = () => { if (this.remote[track.kind] === track) { this.remote[track.kind] = null; this.refresh(); } if (track.kind === 'audio') Mix.drop(track); };
    if (!track.muted) track.onunmute();
  },
  refresh() {
    if (!this.video) return;
    const tracks = [this.remote.video, this.remote.audio].filter(Boolean);
    this.video.srcObject = tracks.length ? new MediaStream(tracks) : null; this.video.play().catch(() => {});
    const showing = this.camOn !== false && !!this.remote.video;
    this.video.style.visibility = showing ? 'visible' : 'hidden';
    this.text.textContent = showing ? 'Your interviewer' : 'Your interviewer (camera off)';
  },
  showTile() {
    if (this.tile) { this.tile.hidden = false; return; }
    this.video = h('video', { autoplay: true, playsinline: true, 'aria-label': 'Your interviewer' });
    this.text = h('span', {}, 'Your interviewer');
    this.mute = h('button', { class: 'btn quiet', type: 'button', style: 'padding:4px 10px;font-size:13px', onclick: () => { this.video.muted = !this.video.muted; this.mute.textContent = this.video.muted ? 'Unmute' : 'Mute'; } }, 'Mute');
    this.tile = h('div', { id: 'live-tile', role: 'region', 'aria-label': 'Interviewer on the call' }, this.video, h('div', { class: 'row' }, this.text, this.mute)); document.body.append(this.tile); this.refresh();
  },
  hideTile() { if (this.tile) this.tile.hidden = true; this.remote = { video: null, audio: null }; this.camOn = undefined; },
  watchAudio(track) {                                      // while the interviewer talks, the speech recogniser must not treat it as the candidate's answer
    if (this.analyser) return;
    try { const ac = new (window.AudioContext || window.webkitAudioContext)(); const an = ac.createAnalyser(); an.fftSize = 512; ac.createMediaStreamSource(new MediaStream([track])).connect(an); this.analyser = an; const buf = new Uint8Array(an.fftSize);
      setInterval(() => { an.getByteTimeDomainData(buf); let s = 0; for (const b of buf) { const x = (b - 128) / 128; s += x * x; } if (Math.sqrt(s / buf.length) > 0.02) { S.interviewerTalking = true; clearTimeout(this.quiet); this.quiet = setTimeout(() => { S.interviewerTalking = false; }, 1500); } }, 150); } catch {}
  },
  close() { this.closing = true; Object.keys(this.pcs).forEach((id) => this.drop(id)); if (this.ws) this.ws.close(); },
};

// 3c. Gaze attention check: calibration, then a few random moving-dot challenges -------------------------------------------
function gazeLayer(message, onSkip) {
  const dot = h('div', { id: 'gaze-dot', 'aria-hidden': 'true' });
  const msg = h('div', { id: 'gaze-msg', role: 'status' }, h('span', {}, message), onSkip && h('button', { class: 'btn quiet', onclick: onSkip }, 'Skip this check'));
  const layer = h('div', { id: 'gaze-layer' }, msg, dot); document.body.append(layer);      // message first, dot last: the dot is always on top
  const move = (x, y) => { dot.style.transform = `translate(${x * innerWidth}px, ${y * innerHeight}px)`; };
  move(0.5, 0.5);
  return { layer, dot, msg, move, setMsg: (t, compact) => { msg.firstChild.textContent = t; msg.classList.toggle('compact', !!compact); }, close: () => layer.remove() };
}
async function sampleFrame() {
  const e = S.proctor && S.proctor.engine; if (!e || e.kind !== 'mediapipe') return null;
  const det = await e.detect(); if (!det || det.faces !== 1 || !det.lm) return null;
  const f = Gaze.features(det); return f && f.f ? f : null;
}
async function runCalibration() {
  if (!S.proctor || !S.proctor.engine || S.proctor.engine.kind !== 'mediapipe') return { ok: false, grade: 'poor', reason: 'eye tracking needs the face model, which is not available in this browser' };
  let cancelled = false;
  const L = gazeLayer('Calibration: look at the blue dot as it moves and keep your head fairly still. It takes about 20 seconds.', () => { cancelled = true; });
  const onKey = (e) => { if (e.key === 'Escape') cancelled = true; }; addEventListener('keydown', onKey);
  await sleep(2200); L.setMsg('Keep looking at the dot.', true);
  const samples = []; let hasBlend = false;
  for (let i = 0; i < Gaze.CAL_POINTS.length && !cancelled; i++) {
    const [x, y] = Gaze.CAL_POINTS[i]; L.move(x, y); await sleep(650);
    const end = Date.now() + 950; while (Date.now() < end && !cancelled) { const f = await sampleFrame(); if (f) { samples.push({ f: f.f, x, y, pt: i }); hasBlend = hasBlend || f.hasBlend; } await sleep(60); }
  }
  removeEventListener('keydown', onKey); L.close();
  if (cancelled) return { ok: false, grade: 'poor', reason: 'calibration was skipped' };
  return Gaze.calibrate(samples, { hasBlend });
}
async function runChallenge() {
  const gc = S.flags.gaze; let declined = false; S.paused = true; S.gazeRunning = true; S.gazeAbort = false;
  const L = gazeLayer('Attention check: follow the blue dot with your eyes for a few seconds. You can skip it if you cannot do this comfortably.', () => { declined = true; });
  const onKey = (e) => { if (e.key === 'Escape') declined = true; }; addEventListener('keydown', onKey);
  await sleep(1800); L.setMsg('Follow the dot. Press Escape to stop.', true);
  const path = Gaze.makePath(gc.duration_s); const samples = []; const t0 = performance.now();
  while (!declined && !S.gazeAbort && (performance.now() - t0) / 1000 < gc.duration_s) {
    const t = (performance.now() - t0) / 1000; const d = path(t); L.move(d.x, d.y);
    const f = await sampleFrame(); samples.push({ t, dot: d, f: f ? f.f : null }); await sleep(70);
  }
  removeEventListener('keydown', onKey); L.close(); S.paused = false; S.gazeRunning = false;
  if (S.gazeAbort) return;                                            // the interviewer turned the check off: nothing is recorded
  S.gazeRan++;
  if (declined) return S.proctor.report('gaze_check_unavailable', { reason: 'declined by candidate' }, S.video);
  const res = Gaze.score(S.gazeCal, samples);
  if (res.status === 'unavailable') S.proctor.report('gaze_check_unavailable', { reason: res.reason }, S.video);
  else S.proctor.report(res.status === 'passed' ? 'gaze_check_passed' : 'gaze_check_failed', { score: res.score }, S.video);
}
function startGazeChecks() {
  const g = S.flags.gaze; if (!g.enabled || S.gazeTimer) return;
  if ((!S.gazeCal || !S.gazeCal.ok) && S.flags.calibrated && !S.gazeNotified) { S.gazeNotified = true; S.proctor.report('gaze_check_unavailable', { reason: 'calibrated earlier but not on this page, so no dot checks' }, S.video); return; }
  if (!S.gazeCal || !S.gazeCal.ok) { if (!S.gazeNotified) { S.gazeNotified = true; S.proctor.report('gaze_check_unavailable', { reason: g.bypass ? 'interviewer allowed the interview without eye tracking' : (S.gazeCal && S.gazeCal.reason) || 'candidate did not calibrate eye tracking' }, S.video); } return; }
  const remaining = Math.max(60, durMin() * 60 - offset()); const n = Math.max(0, g.count - S.gazeRan);
  const times = Gaze.schedule(n, remaining).map((t) => t + offset()); let next = 0;
  S.gazeTimer = setInterval(() => {
    if (next >= times.length) { clearInterval(S.gazeTimer); return; }
    const left = S.cur ? S.cur.time_limit_s - (Date.now() - S.qStart - S.elapsedPaused) / 1000 : 0;
    if (offset() >= times[next] && !S.speaking && !S.submitting && !S.paused && !S.gazeRunning && !S.cur.revisiting && left > 25) { next++; runChallenge(); }
  }, 1000);
}

// 4. The interview --------------------------------------------------------------------------------------
async function begin() {
  if (dev.go) dev.go.disabled = true;
  if (S.practice) await S.practice.stop();                       // only one speech recogniser may ever be running
  let cur; try { cur = await C('/start', { method: 'POST' }); } catch (e) { if (dev.go) dev.go.disabled = false; return toast(e.message); }
  if (S.cleanupCheck) S.cleanupCheck();
  keepAwake(); document.addEventListener('visibilitychange', () => { if (!document.hidden && S.started && !S.finishing) keepAwake(); });
  window.onbeforeunload = (e) => { if (S.started && !S.finishing) { e.preventDefault(); e.returnValue = ''; return ''; } };
  S.startedAt = Date.now() - ((durMin() * 60) - (cur.remaining_s ?? durMin() * 60)) * 1000; S.started = true;
  if (S.consent.recording) { startRecording(); startSecondaryRecording(); }
  room(); showQuestion(cur); applyFlags(); startGazeChecks();
}
function releaseAwake() { try { S.wake && S.wake.release(); } catch {} S.wake = null; }
async function keepAwake() {                                   // a long interview must not be interrupted by the screen going to sleep
  try { if ('wakeLock' in navigator) S.wake = await navigator.wakeLock.request('screen'); } catch { /* not allowed or not supported */ }
}
/* The recording carries both voices: the candidate's microphone and, when the interviewer is on the call, theirs. */
const Mix = {
  ac: null, dest: null, remote: new Map(),
  stream() {
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      this.ac = new AC(); this.dest = this.ac.createMediaStreamDestination();
      this.ac.createMediaStreamSource(new MediaStream(S.stream.getAudioTracks())).connect(this.dest);
      this.ac.resume().catch(() => {});
      return new MediaStream([...S.stream.getVideoTracks(), ...this.dest.stream.getAudioTracks()]);
    } catch { this.ac = null; return S.stream; }
  },
  add(track) {
    if (!this.ac || this.remote.has(track)) return;
    try { const src = this.ac.createMediaStreamSource(new MediaStream([track])); src.connect(this.dest); this.remote.set(track, src); } catch { /* the recording just misses that voice */ }
  },
  drop(track) { const src = this.remote.get(track); if (src) { try { src.disconnect(); } catch {} this.remote.delete(track); } },
};
function recordingStream() { return S.stream.getAudioTracks().length ? Mix.stream() : S.stream; }
function sendSpeech(text) {                                    // spoken words go into the interview's activity record (best effort)
  const t = S.startedAt ? Math.max(0, (Date.now() - S.startedAt) / 1000) : 0;
  fetch(`/api/candidate/${token}/transcript`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: text.slice(0, 600), t_offset: t }) }).catch(() => {});
}
/* The phone camera is recorded separately (one file per connection), so the interviewer can review both views afterwards. */
function startSecondaryRecording() {
  const stream = S.secStream; if (!window.MediaRecorder || !stream || !S.started || S.finishing || !S.consent || !S.consent.recording) return;
  if (S.secRec && S.secRec.state !== 'inactive' && S.secRecStream === stream) return;
  if (S.secRec && S.secRec.state !== 'inactive') { try { S.secRec.stop(); } catch {} }
  const part = Math.min(S.secPart = (S.secPart === undefined ? 0 : S.secPart + 1), 9); let seq = 0;
  try {
    const rec = new MediaRecorder(stream, { mimeType: MediaRecorder.isTypeSupported('video/webm;codecs=vp8') ? 'video/webm;codecs=vp8' : 'video/webm' }); S.secRec = rec; S.secRecStream = stream;
    S.secQueue = S.secQueue || Promise.resolve();
    rec.ondataavailable = (e) => { if (!e.data.size) return; const n = seq++; S.secQueue = S.secQueue.then(async () => { for (let t = 0; t < 5; t++) { try { await fetch(`/api/candidate/${token}/recording?cam=secondary&part=${part}&seq=${n}`, { method: 'POST', body: e.data }); return; } catch { await sleep(2000); } } }); };
    rec.start(5000);
  } catch (e) { console.warn('second camera recording unavailable', e); }
}
/* Interview over: tell the phone to switch its camera off, close the connection and stop recording it. */
async function endSecondary() {
  if (S.secRec && S.secRec.state !== 'inactive') { try { S.secRec.stop(); } catch {} }
  try { if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify({ type: 'end' })); } catch {}
  await sleep(300);
  try { if (S.pairPc) S.pairPc.close(); } catch {}
  S.paired = false; S.secStream = null; if (S.secVideo) { S.secVideo.srcObject = null; S.secVideo.hidden = true; }
}
function startRecording() {
  if (!window.MediaRecorder) return;
  try {
    S.recorder = new MediaRecorder(recordingStream(), { mimeType: MediaRecorder.isTypeSupported('video/webm;codecs=vp8,opus') ? 'video/webm;codecs=vp8,opus' : 'video/webm' });
    S.recorder.ondataavailable = (e) => { if (!e.data.size) return; const seq = S.recSeq++; S.recQueue = S.recQueue.then(async () => { for (let t = 0; t < 5; t++) { try { await fetch(`/api/candidate/${token}/recording?seq=${seq}`, { method: 'POST', body: e.data }); return; } catch { await sleep(2000); } } }); };
    S.recorder.start(5000);
  } catch (e) { console.warn('recording unavailable', e); }
}

function room() {
  const demo = params.get('demo') === '1' ? demoPanel() : null;
  ui = {};
  ui.face = h('div', { class: 'face' }); ui.tag = h('span', { class: 'tag' }); ui.q = h('h2', { id: 'q', tabindex: -1 });
  ui.ta = h('textarea', { id: 'ans', 'aria-label': 'Your answer' });
  ui.ta.addEventListener('paste', () => S.proctor && S.proctor.report('paste_detected'));
  ui.mic = h('button', { class: 'btn quiet', type: 'button', id: 'mic-btn' });
  ui.hint = h('p', { class: 'small muted', 'aria-live': 'polite' });
  S.dict = new Dictation({ textarea: ui.ta, button: ui.mic, shouldIgnore: () => S.speaking || S.interviewerTalking, onMessage: (m) => { ui.hint.textContent = m || defaultHint(); }, beforeStart: () => cancelSpeech() });
  S.dict.onFinal = sendSpeech;
  S.dict.onActivity = (hasText) => { if (S.proctor) S.proctor.noteQuiet(false, hasText); };
  ui.submit = h('button', { class: 'btn', type: 'button', onclick: () => submit('answered') }, 'Submit answer');
  ui.skip = h('button', { class: 'btn quiet', type: 'button', onclick: () => confirm('Skip this question? You cannot come back to it unless the interviewer allows going back.') && submit('skipped') }, 'Skip question');
  ui.back = h('button', { class: 'btn quiet', type: 'button', hidden: true, onclick: returnToCurrent }, 'Back to my current question');
  ui.nav = h('div', { class: 'nav-chips', hidden: true, 'aria-label': 'Earlier questions' });
  ui.timer = h('div', { class: 'timer', 'aria-label': 'Time left for this answer' }, '0:00'); ui.total = h('p', { class: 'small muted' }); ui.prog = h('p', { class: 'small muted' });
  ui.notice = h('div', { class: 'banner', hidden: true, role: 'alert' });
  ui.review = h('div', { class: 'gate', hidden: true, role: 'status' });
  const cam = h('video', { autoplay: true, muted: true, playsinline: true, 'aria-label': 'Your camera' }); cam.srcObject = S.stream; cam.play().catch(() => {});
  app.replaceChildren(h('div', { class: 'room' },
    h('div', { class: 'grid' }, ui.notice, h('div', { class: 'iv' }, h('div', { class: 'iv-head' }, ui.face, h('div', {}, h('strong', {}, 'Neuron'), h('div', { class: 'small' }, 'Your AI interviewer'))), h('div', {}, ui.tag), ui.q,
      h('div', { class: 'actions' }, h('button', { class: 'btn quiet', type: 'button', onclick: hearAgain }, 'Hear it again'))),
      h('div', { class: 'answer-box' }, ui.review, ui.ta, h('div', { class: 'actions' }, ui.mic, ui.submit, ui.skip, ui.back), ui.hint, ui.nav)),
    h('div', { class: 'side' }, h('div', { class: 'panel' }, ui.timer, ui.prog, ui.total), h('div', { class: 'panel' }, cam, h('p', { class: 'small muted', style: 'margin-top:8px' }, 'Your camera. Monitoring is active.')), S.secVideo && S.paired && h('div', { class: 'panel' }, h('p', { class: 'small' }, 'Second camera is connected.')), demo)));
  if (S.proctor) { S.proctor.primary = cam; S.video = cam; }
  setInterval(tickTimer, 250);
  addEventListener('offline', () => { S.paused = true; banner('You appear to be offline. Your timer is paused and your answer is kept. We will reconnect automatically.'); });
  addEventListener('online', () => { S.paused = S.gazeRunning; banner(null); });
}
const defaultHint = () => { const btn = S.cur && S.cur.revisiting ? 'Save changed answer' : 'Submit answer'; return S.dict && S.dict.supported ? `Speak naturally, or type. Press "${btn}" when you are done.` : `Type your answer, then press "${btn}".`; };
function banner(msg) { ui.notice.hidden = !msg; ui.notice.textContent = msg || ''; }

function speak(text) {
  return new Promise((res) => {
    if (!text || !('speechSynthesis' in window)) return res();
    speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(text); u.rate = 0.98; u.lang = 'en-IN';
    S.speaking = true; ui.face && ui.face.classList.add('talking');
    let done = false; const end = () => { if (done) return; done = true; S.speaking = false; ui.face && ui.face.classList.remove('talking'); res(); };
    u.onend = end; u.onerror = end; speechSynthesis.speak(u);
    setTimeout(end, Math.min(60000, text.length * 90 + 2000));   // never get stuck if the voice never fires onend
  });
}
function cancelSpeech() { if ('speechSynthesis' in window) speechSynthesis.cancel(); S.speaking = false; ui.face && ui.face.classList.remove('talking'); }
async function hearAgain() {
  if (!S.cur) return;
  const was = S.dict.wanted; await S.dict.stop();                 // never let the mic listen to the voice
  await speak(S.cur.question);
  if (was && !S.submitting) S.dict.start();
}

function draftKey(c) { return `${c.idx}${c.is_followup ? 'f' : ''}`; }
async function showQuestion(cur) {
  if (cur.done) return finish();
  if (S.cur && !S.cur.revisiting && ui.ta && ui.ta.value.trim()) S.drafts[draftKey(S.cur)] = ui.ta.value;   // keep what was typed for the current question
  await S.dict.stop();
  const seq = ++S.qSeq;
  S.cur = cur; S.qStart = Date.now(); S.elapsedPaused = 0; S.lastSpeech = Date.now(); S.nudged = false; S.submitting = false;
  ui.q.textContent = cur.question; ui.tag.textContent = cur.is_followup ? 'Follow-up' : cur.category; ui.ta.disabled = false; ui.submit.disabled = false;
  ui.ta.value = cur.revisiting ? (cur.previous_answer || '') : (S.drafts[draftKey(cur)] || '');
  ui.submit.textContent = cur.revisiting ? 'Save changed answer' : 'Submit answer'; ui.skip.hidden = !!cur.revisiting; ui.back.hidden = !cur.revisiting;
  ui.back.textContent = `Back to my current question (${cur.furthest + 1})`;
  ui.review.hidden = !cur.revisiting;
  ui.review.textContent = cur.revisiting ? `You are looking at question ${cur.number} again${cur.previous_status === 'skipped' ? ', which you skipped' : ''}. Change your answer and save it, or go back to where you were. The interview clock keeps running.` : '';
  ui.prog.textContent = cur.revisiting ? `Reviewing question ${cur.number} of ${cur.total}` : cur.is_followup ? 'Follow-up question' : `Question ${cur.number} of ${cur.total}`; ui.total.textContent = '';
  ui.hint.textContent = defaultHint(); ui.timer.textContent = cur.revisiting ? '–' : fmtTime(cur.time_limit_s);
  renderNav(); ui.q.focus(); if (S.proctor) S.proctor.noteQuiet(!cur.revisiting, false);
  if (cur.revisiting) return;
  await speak(cur.question);
  if (seq === S.qSeq && !S.submitting && S.dict.supported && !ui.ta.value.trim()) S.dict.start();
}
function renderNav() {
  const c = S.cur; if (!c || !ui.nav) return;
  ui.nav.hidden = !c.can_back;
  if (!c.can_back) return;
  ui.nav.replaceChildren(...[h('span', { class: 'small muted' }, 'Earlier questions:'), ...c.previous.map((p) => h('button', { type: 'button', class: `chip-q ${p.status === 'answered' ? '' : 'skipped'} ${c.revisiting && c.idx === p.idx ? 'here' : ''}`, 'aria-label': `Question ${p.idx + 1}, ${p.status}${p.revisions ? ', changed ' + p.revisions + ' times' : ''}`, onclick: () => goBack(p.idx) }, String(p.idx + 1))),
    !c.revisiting && h('span', { class: 'chip-q current', 'aria-current': 'step' }, String(c.number))].filter(Boolean));
}
async function goBack(idx) {
  if (S.submitting) return;
  if (S.cur && !S.cur.revisiting && ui.ta.value.trim()) S.drafts[draftKey(S.cur)] = ui.ta.value;
  try { showQuestion(await C('/goto', { method: 'POST', json: { idx } })); }
  catch (e) { toast(e.message); try { showQuestion(await C('/current')); } catch {} }
}
async function returnToCurrent() { if (S.submitting) return; try { showQuestion(await C('/return', { method: 'POST' })); } catch (e) { toast(e.message); } }

function tickTimer() {
  if (!S.cur || S.submitting) return;
  if (S.paused) { S.elapsedPaused += 250; return; }
  const total = durMin() * 60 - offset(); ui.total.textContent = `${fmtTime(total)} left in the interview`;
  if (total <= 0) return finish();
  if (S.cur.revisiting) return;                                   // no per-answer limit while reviewing
  const used = (Date.now() - S.qStart - S.elapsedPaused) / 1000; const left = S.cur.time_limit_s - used;
  ui.timer.textContent = fmtTime(left); ui.timer.classList.toggle('low', left < 15);
  if (!S.speaking && !S.nudged && !ui.ta.value.trim() && (Date.now() - S.lastSpeech) / 1000 > 15 && used > 15) { S.nudged = true; ui.hint.textContent = 'I could not hear anything. Check your microphone, or type your answer.'; speak('I could not hear an answer. Please speak when you are ready, or type it.'); }
  if (left <= 0) submit(ui.ta.value.trim() ? 'answered' : 'timeout');
}

async function submit(status) {
  if (S.submitting || !S.cur) return;
  S.submitting = true; cancelSpeech(); ui.submit.disabled = true;
  await S.dict.stop();                                            // waits for the last spoken words to arrive in the box
  const text = ui.ta.value.trim();
  if (S.cur.revisiting && (status !== 'answered' || !text)) { S.submitting = false; ui.submit.disabled = false; toast('Write an answer to save, or go back to your current question.'); return; }
  ui.ta.disabled = true;
  const body = { idx: S.cur.idx, answer: ui.ta.value, status, duration_s: S.cur.revisiting ? null : Math.max(0, (Date.now() - S.qStart - S.elapsedPaused) / 1000), request_id: reqId() };
  for (;;) {            // retry until the server has the answer (handles connectivity drops)
    try { const next = await C('/answer', { method: 'POST', json: body }); banner(null); delete S.drafts[draftKey(S.cur)]; return showQuestion(next); }
    catch (e) {
      if (e.status && e.status < 500) { toast(e.message); S.submitting = false; ui.submit.disabled = false; ui.ta.disabled = false; try { showQuestion(await C('/current')); } catch {} return; }
      banner('Connection lost. Your answer is saved on this page and will be sent when you are back online.'); await sleep(3000);
    }
  }
}

async function finish() {
  if (S.finishing) return; S.finishing = true; S.submitting = true;
  if (S.dict) await S.dict.stop();
  if (S.recorder && S.recorder.state !== 'inactive') { S.recorder.stop(); await S.recQueue; await sleep(800); await S.recQueue; }
  if (S.secRec && S.secRec.state !== 'inactive') { S.secRec.stop(); await sleep(800); await (S.secQueue || Promise.resolve()); }
  try { await C('/finish', { method: 'POST' }); } catch (e) { if (e.status !== 409) toast(e.message); }
  clearInterval(S.gazeTimer); S.gazeAbort = true; releaseAwake();
  await endSecondary(); if (S.ws) S.ws.close();
  if (S.proctor) S.proctor.stop(); if (S.stream) S.stream.getTracks().forEach((t) => t.stop()); Live.close(); if (Live.tile) Live.tile.remove(); finished();
}
function finished(ended) {
  window.onbeforeunload = null; document.getElementById('msg-banner') && document.getElementById('msg-banner').remove();
  app.replaceChildren(h('div', { class: 'panel grid', style: 'max-width:640px' }, h('h1', {}, 'Thank you. The interview is complete.'), ended && h('p', {}, 'The interview has ended. Your answers so far have been saved.'),
    h('p', {}, 'Your answers have been saved. A recruiter will review them and get in touch about the next steps. You can close this page now.'),
    h('p', { class: 'small muted' }, 'AI scoring supports the recruiter but does not make the hiring decision.')));
}

// Demo controls (?demo=1): simulate integrity events to show the log and false-alarm handling ------------------------
function demoPanel() {
  const mk = (label, type, detail) => h('button', { class: 'btn quiet', onclick: () => { S.proctor.report(type, detail || {}, S.video); toast('Simulated: ' + label); } }, label);
  return h('div', { class: 'demo' }, h('strong', {}, 'Demo controls'), h('p', { class: 'small' }, 'Simulate events to see how the recruiter log separates observed events from inferred patterns.'),
    h('div', { class: 'row' }, mk('Look away', 'looking_away', { duration_s: 8 }), mk('Second person', 'multiple_faces', { faces: 2 }), mk('Face absent', 'face_absent', { duration_s: 10 }), mk('Loud audio', 'audio_burst'), mk('Phone disconnect', 'secondary_disconnected'), mk('Gaze check passed', 'gaze_check_passed', { score: 0.8 }), mk('Gaze check missed', 'gaze_check_failed', { score: 0.2 })));
}
init();
