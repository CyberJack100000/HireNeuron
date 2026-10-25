// Run with:  node --test web/test/gaze.test.mjs
// These tests drive the maths with SIMULATED faces (no real eyes available in CI). They show the logic is sound and how it
// degrades with glasses-like noise; real-world accuracy still has to be measured on real people.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const Gaze = createRequire(import.meta.url)('../gaze.js');

function rng(seed) { let s = seed >>> 0; return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296; }
const gauss = (r) => Math.sqrt(-2 * Math.log(r() + 1e-12)) * Math.cos(2 * Math.PI * r());
const clip01 = (v) => Math.max(0, Math.min(1, v));

/** One simulated frame. G = where the person looks (0..1). Head turns hs of the way, eyes do the rest.
    opts: irisNoise, glare (prob a frame has a glare-corrupted iris), irisDead (iris pure noise), blink, noBlend, sign, hs, drift {x,y}, headOnly */
function frame(G, o, r, extra = {}) {
  const hs = extra.hs ?? o.hs ?? 0.3, sign = o.sign ?? 1;
  const E = { x: (G.x - 0.5) * (1 - hs), y: (G.y - 0.5) * (1 - hs) };
  const H = { x: (G.x - 0.5) * hs + (o.drift ? o.drift.x : 0), y: (G.y - 0.5) * hs + (o.drift ? o.drift.y : 0) };
  if (extra.fixedEyes) { E.x = 0; E.y = 0; H.x = (extra.headTo.x - 0.5); H.y = (extra.headTo.y - 0.5); }
  const lm = Array.from({ length: 478 }, () => ({ x: 0.5, y: 0.5 })); const set = (i, x, y) => { lm[i] = { x, y }; };
  set(33, 0.38, 0.42); set(133, 0.46, 0.42); set(159, 0.42, 0.405); set(145, 0.42, 0.435);
  set(263, 0.62, 0.42); set(362, 0.54, 0.42); set(386, 0.58, 0.405); set(374, 0.58, 0.435);
  set(1, 0.5 + H.x * 0.02, 0.55 + H.y * 0.015); set(13, 0.5, 0.65);
  const nz = o.irisNoise ?? 0.0006;
  const glare = (o.glare && r() < o.glare) || o.irisDead;
  const jit = () => (glare ? (r() - 0.5) * (o.irisDead ? 0.1 : 0.02) : gauss(r) * nz);
  const ix = -E.x * sign * 0.06, iy = E.y * 0.03;                       // iris shifts opposite to screen-x in the unmirrored image
  set(468, 0.42 + ix + jit(), 0.42 + iy + jit() * 0.5); set(473, 0.58 + ix + jit(), 0.42 + iy + jit() * 0.5);
  let blend = null;
  if (!o.noBlend) {
    const bn = () => gauss(r) * 0.03, eh = E.x * sign * 1.6, ev = E.y * 1.6;     // positive eh = looking toward the user's right
    blend = { eyeBlinkLeft: r() < (o.blink || 0) ? 0.9 : 0.05, eyeBlinkRight: 0.05,
      eyeLookInLeft: clip01(Math.max(0, eh) + bn()), eyeLookOutLeft: clip01(Math.max(0, -eh) + bn()), eyeLookInRight: clip01(Math.max(0, -eh) + bn()), eyeLookOutRight: clip01(Math.max(0, eh) + bn()),
      eyeLookUpLeft: clip01(Math.max(0, -ev) + bn()), eyeLookUpRight: clip01(Math.max(0, -ev) + bn()), eyeLookDownLeft: clip01(Math.max(0, ev) + bn()), eyeLookDownRight: clip01(Math.max(0, ev) + bn()) };
  }
  const m = new Array(16).fill(0); m[0] = m[5] = 1; m[10] = 1; m[15] = 1;
  m[8] = H.x * 0.9 + gauss(r) * 0.004; m[9] = H.y * 0.9 + gauss(r) * 0.004; m[12] = (o.drift ? o.drift.x * 40 : 0) + gauss(r) * 0.1; m[13] = (o.drift ? o.drift.y * 40 : 0) + gauss(r) * 0.1; m[14] = -50;
  return Gaze.features({ lm, blend, matrix: o.noMatrix ? null : m });
}

function calibrateUser(r, o) {
  const samples = [];
  Gaze.CAL_POINTS.forEach(([x, y], pt) => { for (let k = 0; k < 22; k++) { const f = frame({ x, y }, o, r); if (f && f.f) samples.push({ f: f.f, x, y, pt }); } });
  return Gaze.calibrate(samples, { hasBlend: !o.noBlend });
}
function challenge(cal, r, o, { mode = 'follow', lag = 0.25, validDrop = 0, seconds = 8, driftEnd } = {}) {
  const path = Gaze.makePath(seconds, r); const samples = []; let wx = 0.5, wy = 0.5, vx = 0, vy = 0;
  for (let t = 0; t < seconds; t += 0.1) {
    const dot = path(t); let ex = {}; let G;
    if (mode === 'follow') G = path(Math.max(0, t - lag));
    else if (mode === 'fixed') G = { x: 0.52, y: 0.45 };                                   // staring at something else
    else if (mode === 'wander') { vx = vx * 0.9 + gauss(r) * 0.03; vy = vy * 0.9 + gauss(r) * 0.03; wx = Math.max(0.05, Math.min(0.95, wx + vx)); wy = Math.max(0.05, Math.min(0.95, wy + vy)); G = { x: wx, y: wy }; }
    else if (mode === 'headOnly') { G = { x: 0.5, y: 0.5 }; ex = { fixedEyes: true, headTo: path(Math.max(0, t - lag)) }; }
    const oo = driftEnd ? { ...o, drift: { x: driftEnd.x * (t / seconds), y: driftEnd.y * (t / seconds) } } : o;
    const f = r() < validDrop ? null : frame(G, oo, r, ex);
    samples.push({ t, dot, f: f && f.f ? f.f : null });
  }
  return Gaze.score(cal, samples);
}
const rate = (n, fn) => { let c = 0; for (let i = 0; i < n; i++) if (fn(i)) c++; return c / n; };

const NORMAL = { irisNoise: 0.0006, blink: 0.03 };
const GLASSES = { irisNoise: 0.0022, glare: 0.1, blink: 0.04 };         // frame reflections: noisier iris and occasional glare flashes
const GLASSES_BAD = { irisDead: true, blink: 0.04 };                    // heavy glare: the iris landmarks are useless

test('parseResult reads the MediaPipe result shape (landmarks, blendshape categories, 4x4 matrix)', () => {
  const lm = Array.from({ length: 478 }, () => ({ x: 0.5, y: 0.5, z: 0 }));
  const res = { faceLandmarks: [lm], faceBlendshapes: [{ headIndex: 0, headName: '', categories: [{ index: 0, score: 0.1, categoryName: 'eyeBlinkLeft', displayName: '' }, { index: 1, score: 0.7, categoryName: 'eyeLookInLeft', displayName: '' }] }],
    facialTransformationMatrixes: [{ rows: 4, columns: 4, data: Array.from({ length: 16 }, (_, i) => i) }] };
  const d = Gaze.parseResult(res);
  assert.equal(d.faces, 1); assert.equal(d.lm.length, 478); assert.equal(d.blend.eyeLookInLeft, 0.7); assert.equal(d.matrix.length, 16);
  assert.deepEqual(Gaze.parseResult({ faceLandmarks: [], faceBlendshapes: [], facialTransformationMatrixes: [] }), { faces: 0, lm: null, blend: null, matrix: null });
  assert.equal(Gaze.parseResult(null).faces, 0);
});

test('features: blinks are skipped, missing pieces fall back, bad data is rejected', () => {
  const r = rng(1);
  assert.ok(frame({ x: 0.5, y: 0.5 }, { blink: 0 }, r).f.length === 8);
  assert.equal(frame({ x: 0.5, y: 0.5 }, { blink: 1 }, r).blink, true);
  assert.ok(frame({ x: 0.5, y: 0.5 }, { noMatrix: true }, r).f.every(Number.isFinite));       // landmark head pose fallback
  assert.equal(frame({ x: 0.5, y: 0.5 }, { noBlend: true }, r).hasBlend, false);
  assert.equal(Gaze.features({ lm: [{ x: 0, y: 0 }] }), null); assert.equal(Gaze.features(null), null);
});

test('calibration is good for a normal face and works with either sign convention', () => {
  for (const sign of [1, -1]) {
    const cal = calibrateUser(rng(10 + sign), { ...NORMAL, sign });
    assert.ok(cal.ok && cal.grade === 'good', `sign ${sign}: ${cal.grade} cv ${cal.cvError}`);
    const f = frame({ x: 0.2, y: 0.8 }, { sign, irisNoise: 0 }, rng(3)).f; const p = Gaze.predict(cal, f);
    assert.ok(Math.abs(p.x - 0.2) < 0.12 && Math.abs(p.y - 0.8) < 0.12, JSON.stringify(p));
  }
});

test('glasses: noisy iris and glare flashes still calibrate well (blendshapes and head carry the signal)', () => {
  const grades = [];
  for (let i = 0; i < 12; i++) { const cal = calibrateUser(rng(100 + i), GLASSES); assert.ok(cal.ok, `run ${i}: ${cal.reason} ${cal.cvError}`); grades.push(cal.grade); }
  assert.ok(grades.filter((g) => g === 'good' || g === 'fair').length >= 11, grades.join());
});

test('when the iris landmarks are wrecked, calibration drops them and still works', () => {
  const cal = calibrateUser(rng(7), GLASSES_BAD);
  assert.ok(cal.ok, cal.reason); assert.ok(!cal.mode.includes('iris') && !cal.mode.startsWith('eyes'), `mode ${cal.mode}`);
  const noBlendEither = calibrateUser(rng(8), { ...GLASSES_BAD, noBlend: true });                  // nothing but head pose is left
  assert.equal(noBlendEither.mode, 'head');
});

test('without blendshapes (older model) it falls back to iris + head', () => {
  const cal = calibrateUser(rng(9), { ...NORMAL, noBlend: true });
  assert.ok(cal.ok && cal.mode === 'iris+head', `${cal.mode} ${cal.grade}`);
});

test('honest candidates pass: normal, with glasses, with heavy glare, slow reaction, head drifting', () => {
  const cases = [
    ['normal', NORMAL, {}, 0.97], ['glasses', GLASSES, {}, 0.95], ['heavy glare (iris useless)', GLASSES_BAD, {}, 0.93],
    ['slow reaction (0.45 s)', NORMAL, { lag: 0.45 }, 0.95], ['head drifts while seated', NORMAL, { driftEnd: { x: 0.02, y: -0.015 } }, 0.9],
    ['glasses + slow reaction + drift', GLASSES, { lag: 0.4, driftEnd: { x: 0.015, y: 0.01 } }, 0.88],
  ];
  for (const [name, o, ch, min] of cases) {
    const N = 60; const p = rate(N, (i) => { const r = rng(1000 + i); const cal = calibrateUser(r, o); return cal.ok && challenge(cal, r, o, { mode: 'follow', ...ch }).status === 'passed'; });
    console.log(`  honest/${name}: ${(p * 100).toFixed(0)}% pass`); assert.ok(p >= min, `${name}: only ${(p * 100).toFixed(0)}% passed`);
  }
});

test('eyes fixed on something else fail (the cheating scenario), also with glasses', () => {
  for (const [name, o] of [['normal', NORMAL], ['glasses', GLASSES]]) {
    const N = 60; const p = rate(N, (i) => { const r = rng(2000 + i); const cal = calibrateUser(r, o); return cal.ok && challenge(cal, r, o, { mode: 'fixed' }).status === 'failed'; });
    console.log(`  fixed gaze/${name}: ${(p * 100).toFixed(0)}% fail`); assert.ok(p >= 0.95, `${name}: only ${(p * 100).toFixed(0)}% failed`);
  }
});

test('random wandering gaze is caught most of the time (an honest false-pass rate is reported)', () => {
  const N = 120; let pass = 0;
  for (let i = 0; i < N; i++) { const r = rng(3000 + i); const cal = calibrateUser(r, NORMAL); if (challenge(cal, r, NORMAL, { mode: 'wander' }).status === 'passed') pass++; }
  console.log(`  wandering gaze: ${((pass / N) * 100).toFixed(1)}% falsely pass`); assert.ok(pass / N <= 0.1, `${pass}/${N} wandering runs passed`);
});

test('turning only the head with the dot passes (it is still attention to the dot); documented, not hidden', () => {
  const p = rate(40, (i) => { const r = rng(4000 + i); const cal = calibrateUser(r, NORMAL); return challenge(cal, r, NORMAL, { mode: 'headOnly' }).status === 'passed'; });
  assert.ok(p >= 0.6, `head-only pass rate ${p}`);
});

test('missing frames make the check "unavailable", never "failed"', () => {
  const r = rng(5); const cal = calibrateUser(r, NORMAL);
  const res = challenge(cal, r, NORMAL, { mode: 'fixed', validDrop: 0.7 });
  assert.equal(res.status, 'unavailable');
});

test('calibration refuses a face whose eyes cannot be tracked, so nobody is judged on it', () => {
  const r = rng(6); const samples = [];
  Gaze.CAL_POINTS.forEach(([x, y], pt) => { for (let k = 0; k < 22; k++) samples.push({ f: [0.5 + gauss(r) * 0.0005, 0.5, 0, 0, 0.001 * gauss(r), 0, 0, 0], x, y, pt }); });   // no signal at all
  const cal = Gaze.calibrate(samples); assert.equal(cal.ok, false); assert.equal(cal.grade, 'poor'); assert.match(cal.reason, /could not be tracked/);
  assert.equal(Gaze.calibrate(samples.slice(0, 20)).ok, false);                                  // far too few frames
});

test('the check cannot be passed by a tiny eye movement (slope check)', () => {
  const r = rng(11); const cal = calibrateUser(r, NORMAL); const path = Gaze.makePath(8, r); const samples = [];
  for (let t = 0; t < 8; t += 0.1) { const d = path(t); const f = frame({ x: 0.5 + (d.x - 0.5) * 0.08, y: 0.5 + (d.y - 0.5) * 0.08 }, NORMAL, r, { hs: 0.3 }); samples.push({ t, dot: d, f: f && f.f ? f.f : null }); }
  const res = Gaze.score(cal, samples); assert.notEqual(res.status, 'passed');
});

test('schedule: requested count, inside the interview window, spaced apart', () => {
  const s = Gaze.schedule(3, 1800, rng(8)); assert.equal(s.length, 3); assert.ok(s.every((t) => t >= 360 && t <= 1530)); assert.ok(s[1] - s[0] >= 90 && s[2] - s[1] >= 90);
  assert.ok(Gaze.schedule(8, 300, rng(9)).length < 8);
});

test('the dot path stays on screen and keeps moving', () => {
  const path = Gaze.makePath(8, rng(10)); let minX = 1, maxX = 0, minY = 1, maxY = 0;
  for (let t = 0; t <= 8; t += 0.1) { const p = path(t); assert.ok(p.x >= 0.05 && p.x <= 0.95 && p.y >= 0.05 && p.y <= 0.95); minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x); minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y); }
  assert.ok(maxX - minX > 0.25 && maxY - minY > 0.2);
});
