/* Speech-to-text for the answer box, as a small state machine.

   Why this exists (bugs in the first version it replaces):
   - two quick clicks could leave TWO recognisers running, so words were duplicated and Stop only stopped one of them;
   - text typed or corrected while listening was overwritten by the next speech result;
   - pressing Start while the question was still being read aloud made the mic transcribe the question as the answer;
   - Submit right after Stop could lose the last few words (the final result arrives after stop()).

   Rules: exactly one recogniser is ever live (every handler checks it is still the current one); the textarea is the single
   source of truth (committed text = whatever is in it, plus finished speech); stop() resolves only when the final words
   have been delivered, so callers can read the textarea straight afterwards. */
class Dictation {
  constructor({ textarea, button, lang = 'en-IN', shouldIgnore = () => false, onMessage = () => {}, beforeStart = () => {}, SR } = {}) {
    this.beforeStart = beforeStart;
    this.ta = textarea; this.btn = button; this.lang = lang; this.shouldIgnore = shouldIgnore; this.onMessage = onMessage;
    this.SR = SR !== undefined ? SR : (typeof window !== 'undefined' ? (window.SpeechRecognition || window.webkitSpeechRecognition) : null);
    this.state = 'idle';           // idle | starting | listening | stopping
    this.want = false;             // what the user asked for; auto-restarts follow this
    this.rec = null; this.committed = ''; this.pendingStart = false; this.waiters = []; this.failStreak = 0; this.netFails = 0; this.startedAt = 0;
    if (this.ta) this.ta.addEventListener('input', () => this._onUserEdit());
    if (this.btn) this.btn.addEventListener('click', () => this.toggle());
    this._render();
  }
  get supported() { return !!this.SR; }
  get wanted() { return this.want; }

  toggle() {
    if (this.state === 'stopping') {                       // a click while stopping flips the queued intent
      if (this.pendingStart) { this.pendingStart = false; this.want = false; this._render(); } else this.start();
      return;
    }
    if (this.state === 'idle') return this.start();
    return this.stop();
  }

  start() {
    if (!this.supported) return;
    if (this.state === 'stopping') { this.pendingStart = true; this.want = true; this._render(); return; }
    if (this.state !== 'idle') return;
    this.beforeStart();                                   // e.g. stop the interviewer's voice so the mic does not transcribe it
    this.want = true; this.netFails = 0; this.failStreak = 0; this.onMessage(null); this._begin();
  }

  /** Resolves once the recogniser has fully stopped and its last results are in the textarea. */
  stop() {
    this.want = false; this.pendingStart = false;
    if (this.state === 'idle' || !this.rec) { this.state = 'idle'; this._render(); return Promise.resolve(); }
    const p = new Promise((res) => this.waiters.push(res));
    this.state = 'stopping'; this._render();
    const mine = this.rec;
    try { mine.stop(); } catch { /* already stopped */ }
    setTimeout(() => { if (this.rec === mine) { try { mine.abort(); } catch {} this._ended(mine); } }, 900);   // never hang if onend does not fire
    return p;
  }

  /** New question: forget the baseline. */
  reset() { this.committed = ''; }

  _begin() {
    if (this.rec || !this.want) return;
    this.committed = this._base(this.ta ? this.ta.value : '');
    const r = new this.SR();
    r.lang = this.lang; r.continuous = true; r.interimResults = true; r.maxAlternatives = 1;
    this.rec = r; this.state = 'starting'; this.startedAt = Date.now(); this._render();
    r.onstart = () => { if (this.rec !== r) return; this.state = this.want ? 'listening' : 'stopping'; this._render(); };
    r.onresult = (e) => { if (this.rec !== r) return; this._result(e); };
    r.onerror = (e) => { if (this.rec !== r) return; this._error(e && e.error); };
    r.onend = () => { if (this.rec !== r) return; this._ended(r); };
    try { r.start(); }
    catch (err) { this.rec = null; this.want = false; this.state = 'idle'; this._render(); this.onMessage('Speech recognition could not start. You can type your answer instead.'); this._flush(); }
  }

  _base(text) { return text && !/\s$/.test(text) ? text + ' ' : text; }

  _result(e) {
    if (this.shouldIgnore()) return;                    // the interviewer (or the voice) is talking: not the candidate's words
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const res = e.results[i], text = res[0].transcript;
      if (res.isFinal) { this.committed += this._base(text.trim()); if (this.onFinal && text.trim()) this.onFinal(text.trim()); } else interim += text;
    }
    this.ta.value = this.committed + interim;
    this.netFails = 0;
    if (this.onActivity) this.onActivity(!!(this.committed + interim).trim());
  }

  _error(code) {
    if (code === 'not-allowed' || code === 'service-not-allowed') { this.want = false; this.onMessage('The microphone or speech recognition is blocked in this browser. Allow it, or type your answer.'); }
    else if (code === 'audio-capture') { this.want = false; this.onMessage('No microphone was found. Check it is connected, or type your answer.'); }
    else if (code === 'network') { if (++this.netFails >= 3) { this.want = false; this.onMessage('Speech recognition lost its connection. Please type your answer.'); } }
    /* 'no-speech' and 'aborted' are normal: onend follows and we restart if still wanted */
  }

  _ended(r) {
    if (this.rec !== r) return;
    this.rec = null;
    const userStopped = this.state === 'stopping';
    this._flush();                                       // anyone waiting in stop() can now read the finished text
    const quick = Date.now() - this.startedAt < 500 && !userStopped;
    this.failStreak = quick ? this.failStreak + 1 : 0;
    if (this.want && this.failStreak >= 4) { this.want = false; this.onMessage('Speech recognition keeps stopping. Please type your answer.'); }
    if (this.want) {                                     // the browser ends sessions after silence (or Start was queued): carry on
      this.pendingStart = false; this.state = 'starting'; this._render();
      setTimeout(() => { if (this.want && !this.rec) this._begin(); }, quick ? 400 : 120);
      return;
    }
    this.pendingStart = false; this.state = 'idle'; this._render();
  }
  _flush() { const w = this.waiters; this.waiters = []; w.forEach((f) => f()); }

  _onUserEdit() {
    this.committed = this._base(this.ta.value);          // what is in the box now is the truth
    if (this.rec && (this.state === 'listening' || this.state === 'starting')) {
      // drop the half-heard sentence so it cannot come back on top of the edit: restart the recogniser quietly
      const old = this.rec; this.rec = null;
      try { old.onresult = old.onend = old.onerror = null; old.abort(); } catch {}
      this.state = 'starting'; this._render();
      setTimeout(() => { if (this.want && !this.rec) this._begin(); }, 80);
    }
  }

  _render() {
    if (!this.btn) return;
    const label = { idle: 'Start speaking', starting: this.want ? 'Starting…' : 'Start speaking', listening: 'Stop speaking', stopping: 'Stopping…' }[this.state];
    this.btn.textContent = label; this.btn.dataset.state = this.state; this.btn.setAttribute('aria-pressed', String(this.state === 'listening' || this.state === 'starting' && this.want));
    this.btn.setAttribute('aria-busy', String(this.state === 'starting' || this.state === 'stopping'));
    this.btn.hidden = !this.supported;
  }
}
if (typeof module !== 'undefined') module.exports = Dictation;
