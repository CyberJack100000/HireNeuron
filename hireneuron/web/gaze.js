/* Gaze "attention check": a blue dot moves across the screen and we measure how well the candidate's eyes follow it.
   Pure maths, no DOM, so it is unit-tested in Node (web/test/gaze.test.mjs).

   Signals per frame (8 numbers) from the MediaPipe Face Landmarker:
     iris   : iris position inside each eye from the iris landmarks (can be ruined by glasses glare / frames)
     blend  : the model's own eye-direction scores (eyeLookIn/Out/Up/Down per eye), which are trained for this and hold up better
     head   : forward direction of the head from the 3D face transformation matrix
     pos    : where the head sits in the camera view
   Calibration: the candidate looks at 13 dots at known screen positions. For each of several feature sets (all signals, without
   iris, without blendshapes, head only) a ridge regression maps features to screen x,y, and leave-one-dot-out cross-validation
   measures how well each would predict a dot it has NOT seen. The best set is used, so someone whose iris tracking is wrecked by
   glasses automatically falls back to the signals that still work, and calibration says honestly how good it is.
   Challenge: the predicted gaze path is compared with the dot path (Pearson correlation per axis, allowing for a natural reaction
   delay of up to ~0.6 s, plus a slope check so tiny eye movements cannot pass). Correlation ignores constant offsets, which is
   what makes it forgiving about exact accuracy.

   Honest limits: a webcam gives gaze to within a few degrees at best. A low score is only ever an indicator. */
const Gaze = (() => {
  const L = { aOuter: 33, aInner: 133, aUp: 159, aLow: 145, aIris: 468, bOuter: 263, bInner: 362, bUp: 386, bLow: 374, bIris: 473, nose: 1, mouth: 13 };
  const FEATURES = ['irisH', 'irisV', 'blendH', 'blendV', 'headX', 'headY', 'posX', 'posY'];
  const MODELS = [                                               // simplest first: ties go to the simpler set
    { name: 'head', idx: [4, 5, 6, 7], needs: [] },
    { name: 'blend+head', idx: [2, 3, 4, 5, 6, 7], needs: ['blend'] },
    { name: 'iris+head', idx: [0, 1, 4, 5, 6, 7], needs: [] },
    { name: 'eyes+head', idx: [0, 1, 2, 3, 4, 5, 6, 7], needs: [] },
  ];
  const CAL_POINTS = [[0.5, 0.5], [0.1, 0.1], [0.9, 0.1], [0.1, 0.9], [0.9, 0.9], [0.5, 0.1], [0.5, 0.9], [0.1, 0.5], [0.9, 0.5], [0.3, 0.3], [0.7, 0.3], [0.3, 0.7], [0.7, 0.7]];

  // ------------------------------------------------------------------ reading a MediaPipe result
  /** result = FaceLandmarkerResult. Returns {faces, lm, blend, matrix}. */
  function parseResult(result) {
    const faces = (result && result.faceLandmarks) ? result.faceLandmarks.length : 0;
    const lm = faces ? result.faceLandmarks[0] : null;
    let blend = null;
    const cats = result && result.faceBlendshapes && result.faceBlendshapes[0] && result.faceBlendshapes[0].categories;
    if (cats && cats.length) { blend = {}; for (const c of cats) blend[c.categoryName] = c.score; }
    const m = result && result.facialTransformationMatrixes && result.facialTransformationMatrixes[0];
    const matrix = m && m.data && m.data.length === 16 ? Array.from(m.data) : null;
    return { faces, lm, blend, matrix };
  }

  /** det = {lm, blend, matrix}. Returns {f:[8], hasBlend} or {blink:true} or null when unusable. */
  function features(det) {
    const lm = det && det.lm;
    if (!lm || lm.length < 478) return null;
    const p = (i) => lm[i], b = det.blend || null;
    const eyeW = Math.abs(p(L.aInner).x - p(L.aOuter).x) || 1e-6;
    const lidA = (p(L.aLow).y - p(L.aUp).y) / eyeW, lidB = (p(L.bLow).y - p(L.bUp).y) / (Math.abs(p(L.bInner).x - p(L.bOuter).x) || 1e-6);
    if (b ? (b.eyeBlinkLeft > 0.55 || b.eyeBlinkRight > 0.55) : (lidA < 0.12 || lidB < 0.12)) return { blink: true };    // eyes shut: skip the frame
    const rA = (p(L.aIris).x - p(L.aOuter).x) / ((p(L.aInner).x - p(L.aOuter).x) || 1e-6);
    const rB = (p(L.bIris).x - p(L.bOuter).x) / ((p(L.bInner).x - p(L.bOuter).x) || 1e-6);
    const vA = (p(L.aIris).y - p(L.aUp).y) / ((p(L.aLow).y - p(L.aUp).y) || 1e-6);
    const vB = (p(L.bIris).y - p(L.bUp).y) / ((p(L.bLow).y - p(L.bUp).y) || 1e-6);
    const irisH = (rA - rB) / 2, irisV = (vA + vB) / 2;
    let blendH = 0, blendV = 0;
    if (b && 'eyeLookInLeft' in b) {
      blendH = ((b.eyeLookInLeft + b.eyeLookOutRight) - (b.eyeLookOutLeft + b.eyeLookInRight)) / 2;
      blendV = ((b.eyeLookUpLeft + b.eyeLookUpRight) - (b.eyeLookDownLeft + b.eyeLookDownRight)) / 2;
    }
    let headX, headY, posX, posY;
    const m = det.matrix;
    if (m && Math.abs(m[10]) > 1e-6) {                         // column-major 4x4: third column = where the face points, last column = where it is
      headX = m[8] / m[10]; headY = m[9] / m[10];
      const z = Math.abs(m[14]) || 1; posX = m[12] / z; posY = m[13] / z;
    } else {                                                   // landmark fallback
      const eyeMidX = (p(L.aOuter).x + p(L.bOuter).x) / 2, eyeMidY = (p(L.aOuter).y + p(L.bOuter).y) / 2;
      const dist = Math.abs(p(L.bOuter).x - p(L.aOuter).x) || 1e-6;
      headX = (p(L.nose).x - eyeMidX) / dist; headY = (p(L.nose).y - eyeMidY) / ((p(L.mouth).y - eyeMidY) || 1e-6);
      posX = eyeMidX - 0.5; posY = eyeMidY - 0.5;
    }
    const f = [irisH, irisV, blendH, blendV, headX, headY, posX, posY];
    if (f.some((v) => !Number.isFinite(v))) return null;
    return { f, hasBlend: !!(b && 'eyeLookInLeft' in b) };
  }

  // ------------------------------------------------------------------ small linear algebra
  function solve(A, b) {
    const n = b.length, M = A.map((r, i) => [...r, b[i]]);
    for (let c = 0; c < n; c++) {
      let piv = c; for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
      [M[c], M[piv]] = [M[piv], M[c]]; if (Math.abs(M[c][c]) < 1e-12) return null;
      for (let r = c + 1; r < n; r++) { const f = M[r][c] / M[c][c]; for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k]; }
    }
    const x = new Array(n).fill(0);
    for (let i = n - 1; i >= 0; i--) { let s = M[i][n]; for (let k = i + 1; k < n; k++) s -= M[i][k] * x[k]; x[i] = s / M[i][i]; }
    return x;
  }
  function ridge(X, y, lambda) {                               // rows [features..., 1]; the bias is not penalised
    const d = X[0].length, A = Array.from({ length: d }, () => new Array(d).fill(0)), b = new Array(d).fill(0);
    X.forEach((row, i) => { for (let a = 0; a < d; a++) { b[a] += row[a] * y[i]; for (let c = 0; c < d; c++) A[a][c] += row[a] * row[c]; } });
    for (let a = 0; a < d - 1; a++) A[a][a] += lambda;
    return solve(A, b);
  }
  const median = (a) => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  const mean = (a) => a.reduce((s, v) => s + v, 0) / (a.length || 1);
  const std = (a) => { const m = mean(a); return Math.sqrt(mean(a.map((v) => (v - m) ** 2))); };

  function fit(samples, idx, lambda = 0.5) {
    const mu = idx.map((k) => mean(samples.map((s) => s.f[k])));
    const sd = idx.map((k, j) => std(samples.map((s) => s.f[k])) || 1);
    const sdSafe = sd.map((v) => (v < 1e-7 ? 1 : v));
    const X = samples.map((s) => [...idx.map((k, j) => (s.f[k] - mu[j]) / sdSafe[j]), 1]);
    const wx = ridge(X, samples.map((s) => s.x), lambda), wy = ridge(X, samples.map((s) => s.y), lambda);
    return wx && wy ? { idx, mu, sd: sdSafe, wx, wy } : null;
  }
  function predictWith(model, f) {
    let x = model.wx[model.wx.length - 1], y = model.wy[model.wy.length - 1];
    model.idx.forEach((k, j) => { const z = (f[k] - model.mu[j]) / model.sd[j]; x += model.wx[j] * z; y += model.wy[j] * z; });
    return { x, y };
  }
  const clamp = (v) => Math.max(-0.25, Math.min(1.25, v));

  /** samples: [{f:[8], x, y, pt}] (pt = calibration point index), hasBlend: whether blendshapes were available.
      Returns {ok, mode, cvError, grade, reason, model}. */
  function calibrate(samples, { hasBlend = true } = {}) {
    const byPt = {};
    samples.forEach((s) => { (byPt[s.pt] = byPt[s.pt] || []).push(s); });
    const pts = Object.keys(byPt).filter((k) => byPt[k].length >= 4);
    if (pts.length < 9) return { ok: false, grade: 'poor', reason: 'too few clear frames: face or eyes were not visible enough' };
    // per-point robust centre (median of each feature) used as the held-out test vector
    const centre = {}; pts.forEach((k) => { centre[k] = { f: FEATURES.map((_, j) => median(byPt[k].map((s) => s.f[j]))), x: byPt[k][0].x, y: byPt[k][0].y }; });
    const cx = mean(pts.map((k) => centre[k].x)), cy = mean(pts.map((k) => centre[k].y));
    const baseline = mean(pts.map((k) => Math.hypot(centre[k].x - cx, centre[k].y - cy)));   // error of always guessing the middle
    const usable = samples.filter((s) => pts.includes(String(s.pt)));
    // Signal-to-noise per signal group: how much a feature differs BETWEEN dots compared with how much it jitters WITHIN one dot.
    // Glare or frame reflections ruin the iris signal this way; such a group is left out instead of being trusted.
    const variance = (a) => { const m = mean(a); return mean(a.map((v) => (v - m) ** 2)); };
    const snr = (k) => variance(pts.map((p) => centre[p].f[k])) / (mean(pts.map((p) => variance(byPt[p].map((s) => s.f[k])))) + 1e-12);
    const groupOk = { iris: Math.max(snr(0), snr(1)) >= 3, blend: hasBlend && Math.max(snr(2), snr(3)) >= 3 };
    const excluded = [!groupOk.iris && 'iris', hasBlend && !groupOk.blend && 'blendshapes'].filter(Boolean);
    const results = [], seen = new Set();
    for (const m of MODELS) {
      const idx = m.idx.filter((k) => (k === 0 || k === 1 ? groupOk.iris : k === 2 || k === 3 ? groupOk.blend : true));
      if (seen.has(idx.join())) continue;
      seen.add(idx.join());
      const errs = [];
      for (const k of pts) {
        const train = usable.filter((s) => String(s.pt) !== k);
        const mdl = fit(train, idx); if (!mdl) { errs.push(1); continue; }
        const p = predictWith(mdl, centre[k].f); errs.push(Math.hypot(clamp(p.x) - centre[k].x, clamp(p.y) - centre[k].y));
      }
      const hasIris = idx.includes(0), hasBl = idx.includes(2);
      results.push({ m: { ...m, idx, name: hasIris && hasBl ? 'eyes+head' : hasIris ? 'iris+head' : hasBl ? 'blend+head' : 'head' }, cv: mean(errs) });
    }
    if (!results.length) return { ok: false, grade: 'poor', reason: 'calibration could not be solved' };
    const minCv = Math.min(...results.map((r) => r.cv));
    // richest model within 5% of the best: eye information is kept whenever it helps or is neutral, because people follow a moving
    // dot with their eyes more than with their head, so a head-only fit would under-react in the real check
    const best = results.filter((r) => r.cv <= minCv * 1.05 + 0.003).sort((a, b) => b.m.idx.length - a.m.idx.length || a.cv - b.cv)[0];
    const model = fit(usable, best.m.idx);
    const informative = best.cv <= 0.7 * baseline;                    // must beat "always guess the centre" by a clear margin
    const grade = !informative ? 'poor' : best.cv <= 0.12 ? 'good' : best.cv <= 0.2 ? 'fair' : best.cv <= 0.3 ? 'limited' : 'poor';
    const ok = grade !== 'poor';
    const reason = ok ? '' : (!informative ? 'eye movement could not be tracked (glare, a reflection or low light can cause this)' : 'calibration was not accurate enough');
    return { ok, mode: best.m.name, cvError: best.cv, baseline, grade, reason, model, points: pts.length, excluded };
  }
  const predict = (cal, f) => { const p = predictWith(cal.model, f); return { x: clamp(p.x), y: clamp(p.y) }; };

  // ------------------------------------------------------------------ the challenge
  function pearson(a, b) {
    const n = a.length; if (n < 8) return null;
    const ma = mean(a), mb = mean(b); let sab = 0, saa = 0, sbb = 0;
    for (let i = 0; i < n; i++) { const da = a[i] - ma, db = b[i] - mb; sab += da * db; saa += da * da; sbb += db * db; }
    if (saa < 1e-9 || sbb < 1e-9) return null;
    return { r: sab / Math.sqrt(saa * sbb), slope: sab / sbb };      // slope of prediction on dot: ~1 when the eyes really travel with the dot
  }
  function medianFilter(arr, k = 5) {                                 // removes single-frame glare spikes
    return arr.map((v, i) => { if (v == null) return null; const w = []; for (let j = i - (k >> 1); j <= i + (k >> 1); j++) if (arr[j] != null) w.push(arr[j]); return median(w); });
  }
  const PASS = { good: 0.55, fair: 0.55, limited: 0.5 };
  const MIN_SLOPE = 0.3, MAX_LAG_S = 0.6;

  /** samples: [{t (s), dot:{x,y}, f:[8]|null}] at ~10 Hz. */
  function score(cal, samples, { minValid = 0.6 } = {}) {
    const valid = samples.filter((s) => s.f).length;
    if (samples.length < 20 || valid / samples.length < minValid || valid < 20) return { status: 'unavailable', reason: 'the face or eyes were not visible for enough of the check', validRatio: valid / (samples.length || 1) };
    const preds = samples.map((s) => (s.f ? predict(cal, s.f) : null));
    const px = medianFilter(preds.map((p) => p && p.x)), py = medianFilter(preds.map((p) => p && p.y));
    const dt = median(samples.slice(1).map((s, i) => s.t - samples[i].t)) || 0.1;
    const dotX = samples.map((s) => s.dot.x), dotY = samples.map((s) => s.dot.y);
    const useX = std(dotX) >= 0.06, useY = std(dotY) >= 0.06;
    let best = null;
    for (let k = 0; k <= Math.round(MAX_LAG_S / dt); k++) {              // the eyes lag the dot by a natural reaction time
      const idx = []; for (let i = k; i < samples.length; i++) if (px[i] != null) idx.push(i);
      const axes = [];
      if (useX) { const c = pearson(idx.map((i) => px[i]), idx.map((i) => dotX[i - k])); if (c) axes.push(c); }
      if (useY) { const c = pearson(idx.map((i) => py[i]), idx.map((i) => dotY[i - k])); if (c) axes.push(c); }
      if (!axes.length) continue;
      const sc = mean(axes.map((a) => a.r)), sl = mean(axes.map((a) => a.slope)), worst = Math.min(...axes.map((a) => a.r));
      if (!best || sc > best.sc) best = { sc, sl, worst, k, axes };
    }
    if (!best) return { status: 'unavailable', reason: 'no usable eye signal', validRatio: valid / samples.length };
    const pass = PASS[cal.grade] || 0.5;
    const ok = best.sc >= pass && best.sl >= MIN_SLOPE && best.worst >= 0.25;
    return { status: ok ? 'passed' : 'failed', score: Math.max(0, best.sc), slope: best.sl, lagS: best.k * dt, rx: useX ? best.axes[0].r : null, validRatio: valid / samples.length };
  }

  /** Random smooth path through random waypoints, positions in 0.08..0.92. t in seconds. */
  function makePath(duration, rand = Math.random, legs = 5) {
    const pts = []; const pick = () => ({ x: 0.08 + 0.84 * rand(), y: 0.1 + 0.8 * rand() });
    pts.push(pick());
    while (pts.length <= legs) { const c = pick(); if (Math.hypot(c.x - pts[pts.length - 1].x, c.y - pts[pts.length - 1].y) > 0.35) pts.push(c); }
    const ease = (u) => u * u * (3 - 2 * u);
    return (t) => { const u = Math.min(0.9999, Math.max(0, t / duration)) * legs, i = Math.floor(u), k = ease(u - i);
      return { x: pts[i].x + (pts[i + 1].x - pts[i].x) * k, y: pts[i].y + (pts[i + 1].y - pts[i].y) * k }; };
  }
  /** Random times (seconds) for the checks: inside 20%..85% of the interview, at least minGap apart. */
  function schedule(count, totalSeconds, rand = Math.random, minGap = 90) {
    const lo = totalSeconds * 0.2, hi = totalSeconds * 0.85, out = [];
    for (let tries = 0; out.length < count && tries < 200; tries++) { const t = lo + rand() * (hi - lo); if (out.every((o) => Math.abs(o - t) >= minGap)) out.push(t); }
    return out.sort((a, b) => a - b);
  }
  return { parseResult, features, calibrate, predict, score, pearson, makePath, schedule, CAL_POINTS, FEATURES, MODELS };
})();
if (typeof module !== 'undefined') module.exports = Gaze;
