// Run with:  node --test web/test/dictation.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const Dictation = createRequire(import.meta.url)('../dictation.js');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class MockSR {                              // behaves like Chrome's recogniser: async start/stop, final result delivered on stop
  static instances = []; static active = 0; static maxActive = 0;
  constructor() { this.active = false; this.pending = ''; MockSR.instances.push(this); }
  start() { if (this.active) throw new Error('InvalidStateError'); this.active = true; MockSR.active++; MockSR.maxActive = Math.max(MockSR.maxActive, MockSR.active); setTimeout(() => this.onstart && this.onstart(), 5); }
  stop() { if (!this.active) return; setTimeout(() => { if (this.pending && this.onresult) this.final(this.pending); this.end(); }, 30); }
  abort() { this.pending = ''; this.end(); }
  end() { if (!this.active) return; this.active = false; MockSR.active--; this.onend && this.onend(); }
  interim(t) { this.pending = t; this.onresult && this.onresult({ resultIndex: 0, results: [{ isFinal: false, 0: { transcript: t } }] }); }
  final(t) { this.pending = ''; this.onresult && this.onresult({ resultIndex: 0, results: [{ isFinal: true, 0: { transcript: t } }] }); }
}
const reset = () => { MockSR.instances = []; MockSR.active = 0; MockSR.maxActive = 0; };
const live = () => MockSR.instances.filter((i) => i.active);
function fakeTextarea() { const l = {}; return { value: '', addEventListener: (e, f) => { l[e] = f; }, type(t) { this.value = t; l.input && l.input(); } }; }
function fakeButton() { const l = {}; return { textContent: '', dataset: {}, hidden: false, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, addEventListener: (e, f) => { l[e] = f; }, click() { l.click(); } }; }
function make(opts = {}) { reset(); const ta = fakeTextarea(), btn = fakeButton(), msgs = []; const d = new Dictation({ textarea: ta, button: btn, SR: MockSR, onMessage: (m) => msgs.push(m), ...opts }); return { d, ta, btn, msgs }; }

test('start listens, speech lands in the box, stop flushes the last words', async () => {
  const { d, ta, btn } = make();
  assert.equal(btn.textContent, 'Start speaking'); btn.click(); await sleep(20);
  assert.equal(d.state, 'listening'); assert.equal(btn.textContent, 'Stop speaking'); assert.equal(btn.attrs['aria-pressed'], 'true');
  live()[0].final('The heap is split into generations'); live()[0].interim('and the');
  assert.equal(ta.value, 'The heap is split into generations and the');
  await d.stop();                                           // the pending "and the" is delivered as a final result during stop
  assert.equal(ta.value, 'The heap is split into generations and the ');
  assert.equal(d.state, 'idle'); assert.equal(btn.textContent, 'Start speaking'); assert.equal(live().length, 0);
});

test('rapid clicking never leaves two recognisers running and ends in the state of the last click', async () => {
  const { d, btn } = make();
  for (let i = 0; i < 9; i++) { btn.click(); await sleep(i % 3 === 0 ? 0 : 7); }       // 9 clicks: odd number = last intent is "on"
  await sleep(600);
  assert.ok(MockSR.maxActive <= 1, `max simultaneously active: ${MockSR.maxActive}`);
  assert.equal(d.state, 'listening'); assert.equal(live().length, 1);
  btn.click(); await sleep(300);
  assert.equal(d.state, 'idle'); assert.equal(live().length, 0); assert.equal(MockSR.active, 0);
});

test('start while stopping is queued, not dropped, and never overlaps', async () => {
  const { d } = make(); d.start(); await sleep(20);
  const stopping = d.stop(); d.start();                       // user clicks Start again before the old session has ended
  await stopping; await sleep(300);
  assert.ok(MockSR.maxActive <= 1); assert.equal(d.state, 'listening'); assert.equal(live().length, 1);
  await d.stop();
});

test('text edited while listening is kept and speech continues after it', async () => {
  const { d, ta } = make(); d.start(); await sleep(20);
  live()[0].final('first point');
  ta.type('first point, corrected by hand');                  // the candidate fixes a mistake
  await sleep(150);                                           // the recogniser restarts quietly
  assert.equal(live().length, 1); assert.ok(MockSR.maxActive <= 1);
  live()[0].final('second point');
  assert.equal(ta.value, 'first point, corrected by hand second point ');
  await d.stop();
});

test('an interim sentence is not duplicated when the candidate types over it', async () => {
  const { d, ta } = make(); d.start(); await sleep(20);
  const first = live()[0]; first.interim('half heard sentence');
  ta.type('half heard sentence'); await sleep(150);          // typed text equals what was shown as interim
  first.final && first.onresult && first.final('half heard sentence');   // a late result from the aborted recogniser must be ignored
  assert.equal(ta.value, 'half heard sentence');
  await d.stop();
});

test('the browser ending the session after silence restarts it and keeps the text', async () => {
  const { d, ta } = make(); d.start(); await sleep(20);
  live()[0].final('before the pause'); await sleep(600);      // long enough that it is not a "fast fail"
  live()[0].end(); await sleep(250);
  assert.equal(d.state, 'listening'); assert.equal(live().length, 1);
  live()[0].final('after the pause');
  assert.equal(ta.value, 'before the pause after the pause ');
  await d.stop();
});

test('results are dropped while the interviewer or the voice is speaking', async () => {
  let talking = true; const { d, ta } = make({ shouldIgnore: () => talking });
  d.start(); await sleep(20); live()[0].final('the question being read out'); assert.equal(ta.value, '');
  talking = false; live()[0].final('my answer'); assert.equal(ta.value, 'my answer ');
  await d.stop();
});

test('permission errors stop cleanly with a message', async () => {
  const { d, msgs, btn } = make(); d.start(); await sleep(20);
  live()[0].onerror({ error: 'not-allowed' }); live()[0].end(); await sleep(50);
  assert.equal(d.state, 'idle'); assert.equal(d.wanted, false); assert.equal(btn.textContent, 'Start speaking'); assert.match(msgs.at(-1), /blocked/);
});

test('a recogniser that dies instantly is not restarted forever', async () => {
  const { d, msgs } = make(); d.start();
  for (let i = 0; i < 8 && d.wanted; i++) { await sleep(450); live()[0] && live()[0].end(); }
  await sleep(500);
  assert.equal(d.state, 'idle'); assert.match(msgs.at(-1), /keeps stopping/); assert.ok(MockSR.instances.length <= 6);
});

test('three network errors give up with a message', async () => {
  const { d, msgs } = make(); d.start(); await sleep(20);
  for (let i = 0; i < 3; i++) { const r = live()[0]; r.onerror({ error: 'network' }); r.end(); await sleep(550); }
  await sleep(300);
  assert.equal(d.state, 'idle'); assert.match(msgs.at(-1), /connection/);
});

test('unsupported browsers hide the button and never throw', () => {
  reset(); const btn = fakeButton(); const d = new Dictation({ textarea: fakeTextarea(), button: btn, SR: null });
  assert.equal(btn.hidden, true); d.start(); assert.equal(d.state, 'idle');
});

test('stop() with nothing running resolves immediately', async () => { const { d } = make(); await d.stop(); assert.equal(d.state, 'idle'); });
