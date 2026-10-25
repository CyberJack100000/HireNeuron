/* Client-side proctoring. Everything here is an INDICATOR sent to the server as an observed event; humans decide.
   Not implemented: voice identification / additional-voice detection (needs diarisation on the server). */
class FaceEngine {
  constructor(video, opts = {}) { this.video = video; this.opts = opts; this.kind = null; this.landmarker = null; this.native = null; }
  async init() {
    try {
      const vision = await import('/static/vendor/vision_bundle.mjs');
      const fileset = await vision.FilesetResolver.forVisionTasks('/static/vendor/wasm');
      const mk = (delegate) => vision.FaceLandmarker.createFromOptions(fileset, { baseOptions: { modelAssetPath: '/static/vendor/face_landmarker.task', delegate }, runningMode: 'VIDEO', numFaces: this.opts.numFaces || 3,
        outputFaceBlendshapes: !!this.opts.gaze, outputFacialTransformationMatrixes: !!this.opts.gaze });   // eye-direction scores and head pose, needed only for gaze checks
      try { this.landmarker = await mk('GPU'); } catch { this.landmarker = await mk('CPU'); }
      this.kind = 'mediapipe'; return true;
    } catch (e) { console.warn('MediaPipe unavailable', e); }
    if ('FaceDetector' in window) { try { this.native = new FaceDetector({ fastMode: true, maxDetectedFaces: 4 }); this.kind = 'native'; return true; } catch {} }
    return false;
  }
  /** returns {faces, yaw, pitch} (yaw/pitch ratios are null unless landmarks are available) */
  async detect() {
    const v = this.video;
    if (!v || v.readyState < 2 || !v.videoWidth) return null;
    if (this.landmarker) {
      const r = this.landmarker.detectForVideo(v, performance.now());
      const f = r.faceLandmarks || [];
      let yaw = null, pitch = null;
      if (f[0]) {
        const L = f[0], nose = L[1], le = L[33], re = L[263], mouth = L[13];
        const eyeMidX = (le.x + re.x) / 2, eyeMidY = (le.y + re.y) / 2, eyeDist = Math.abs(re.x - le.x) || 1e-6;
        yaw = (nose.x - eyeMidX) / eyeDist;
        pitch = (nose.y - eyeMidY) / ((mouth.y - eyeMidY) || 1e-6);
      }
      const det = typeof Gaze !== 'undefined' ? Gaze.parseResult(r) : { blend: null, matrix: null };
      return { faces: f.length, yaw, pitch, lm: f[0] || null, blend: det.blend, matrix: det.matrix };
    }
    if (this.native) { try { return { faces: (await this.native.detect(v)).length, yaw: null, pitch: null }; } catch { return null; } }
    return null;
  }
}

class Proctor {
  constructor({ token, primary, secondary, offset, onStatus, wantGaze }) {
    Object.assign(this, { token, primary, secondary, offset, wantGaze, onStatus: onStatus || (() => {}) });
    this.engine = null; this.secEngine = null; this.calib = null; this.state = { absent: null, multi: 0, away: null, secMulti: 0, hidden: null };
    this.timers = []; this.queue = []; this.sending = false; this.lastSent = {};
  }
  snapshot(video) {
    try {
      const c = document.createElement('canvas'); const w = 320; c.width = w; c.height = Math.round(w * (video.videoHeight || 240) / (video.videoWidth || 320));
      c.getContext('2d').drawImage(video, 0, 0, c.width, c.height); return c.toDataURL('image/jpeg', 0.6);
    } catch { return null; }
  }
  report(type, detail = {}, video = null) {
    const payload = { type, t_offset: this.offset(), detail };
    const v = video || this.primary; if (v && !['mic_lost', 'tab_hidden', 'window_blur', 'fullscreen_exit', 'paste_detected', 'detector_unavailable'].includes(type)) payload.evidence_b64 = this.snapshot(v);
    this.queue.push(payload); this.flush();
  }
  async flush() {                     // retries when the connection drops; events are never lost silently
    if (this.sending) return; this.sending = true;
    while (this.queue.length) {
      try { await api(`/api/candidate/${this.token}/events`, { method: 'POST', json: this.queue[0] }); this.queue.shift(); }
      catch (e) { if (e.status && e.status < 500 && e.status !== 0) { this.queue.shift(); } else await new Promise((r) => setTimeout(r, 3000)); }
    }
    this.sending = false;
  }
  async calibrate(ms = 3000) {         // look at the screen: neutral head pose becomes the baseline
    if (!this.engine) return false; const ys = [], ps = []; const end = Date.now() + ms;
    while (Date.now() < end) { const d = await this.engine.detect(); if (d && d.faces === 1 && d.yaw != null) { ys.push(d.yaw); ps.push(d.pitch); } await new Promise((r) => setTimeout(r, 150)); }
    if (ys.length < 5) return false; const med = (a) => a.sort((x, y) => x - y)[a.length >> 1]; this.calib = { yaw: med(ys), pitch: med(ps) }; return true;
  }
  async start({ stream }) {
    this.engine = new FaceEngine(this.primary, { gaze: !!this.wantGaze }); const ok = await this.engine.init();
    if (!ok) { this.report('detector_unavailable'); this.onStatus('Face checks are unavailable in this browser. Other checks are still running.'); }
    // feed loss
    stream.getTracks().forEach((t) => { t.addEventListener('ended', () => this.report(t.kind === 'video' ? 'camera_lost' : 'mic_lost')); });
    // focus / visibility
    document.addEventListener('visibilitychange', () => { if (document.hidden) this.state.hidden = Date.now(); else if (this.state.hidden) { this.report('tab_hidden', { duration_s: (Date.now() - this.state.hidden) / 1000 }); this.state.hidden = null; } });
    addEventListener('blur', () => { setTimeout(() => { if (!document.hasFocus() && !document.hidden) this.report('window_blur'); }, 600); });
    document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && this.wasFull) this.report('fullscreen_exit'); this.wasFull = !!document.fullscreenElement; });
    // audio level (sound while nothing is being said or read aloud)
    try {
      const ac = new (window.AudioContext || window.webkitAudioContext)(); const src = ac.createMediaStreamSource(new MediaStream(stream.getAudioTracks())); const an = ac.createAnalyser(); an.fftSize = 1024; src.connect(an);
      const buf = new Uint8Array(an.fftSize); this.level = 0; this.audioCtx = ac;
      this.timers.push(setInterval(() => { an.getByteTimeDomainData(buf); let s = 0; for (const b of buf) { const x = (b - 128) / 128; s += x * x; } this.level = Math.sqrt(s / buf.length); }, 100));
    } catch (e) { console.warn('audio meter unavailable', e); }
    this.timers.push(setInterval(() => this.tick(), 500));
  }
  noteQuiet(isQuietPhase, hasText) { this.quiet = isQuietPhase; this.hasText = hasText; }
  async tick() {
    if (this.engine && this.engine.kind) {
      const d = await this.engine.detect(); const now = Date.now();
      if (d) {
        const S = this.state;
        // absence: no face for 6s
        if (d.faces === 0) { S.absent = S.absent || now; if (now - S.absent > 6000 && !S.absentSent) { S.absentSent = true; this.report('face_absent', { duration_s: (now - S.absent) / 1000 }); } }
        else { S.absent = null; S.absentSent = false; }
        // several faces for 3 consecutive samples (1.5s)
        S.multi = d.faces > 1 ? S.multi + 1 : 0; if (S.multi === 3) this.report('multiple_faces', { faces: d.faces });
        // attention away: deviation from the calibrated baseline held for 4s
        if (d.faces === 1 && d.yaw != null && this.calib) {
          const away = Math.abs(d.yaw - this.calib.yaw) > 0.22 || Math.abs(d.pitch - this.calib.pitch) > 0.2;
          if (away) { S.away = S.away || now; } else if (S.away) { const dur = (now - S.away) / 1000; S.away = null; if (dur >= 4) this.report('looking_away', { duration_s: dur }); }
        }
      }
    }
    if (this.secEngine) {
      const d = await this.secEngine.detect(); if (d) { this.state.secMulti = d.faces > 1 ? this.state.secMulti + 1 : 0; if (this.state.secMulti === 3) this.report('secondary_multiple_people', { faces: d.faces }, this.secondary); }
    }
    // sound while the candidate should be listening and no words were recognised
    if (this.quiet && !this.hasText && this.level > 0.08) { this.loud = (this.loud || 0) + 1; if (this.loud === 8) this.report('audio_burst'); } else this.loud = 0;
  }
  async watchSecondary() { if (this.secEngine || !this.secondary) return; this.secEngine = new FaceEngine(this.secondary, { numFaces: 4 }); if (!(await this.secEngine.init())) this.secEngine = null; }
  stop() { this.timers.forEach(clearInterval); this.timers = []; if (this.audioCtx) this.audioCtx.close().catch(() => {}); }
}
