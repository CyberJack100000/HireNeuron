const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function h(tag, attrs = {}, ...kids) {           // tiny DOM builder: all text goes through textContent (no XSS)
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') el.className = v; else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'html') throw new Error('no raw html'); else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
let toastTimer;
function toast(msg) {
  let t = $('#toast'); if (!t) { t = h('div', { id: 'toast', class: 'toast', role: 'status', 'aria-live': 'polite' }); document.body.append(t); }
  t.textContent = msg; t.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 3200);
}
async function api(path, { method = 'GET', body, form, json, key } = {}) {
  const headers = {};
  if (key) headers['X-Recruiter-Key'] = key;
  let payload = body;
  if (json !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(json); }
  if (form) payload = form;
  const r = await fetch(path, { method, headers, body: payload });
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json().catch(() => ({})) : null;
  if (!r.ok) { const e = new Error((data && (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail))) || `Request failed (${r.status})`); e.status = r.status; throw e; }
  return data;
}
const fmtTime = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
function reveal() {
  document.documentElement.classList.add('js');
  const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), { threshold: 0.1 });
  $$('.rv').forEach((el) => io.observe(el));
}

/* Light/dark theme. A stored choice wins; otherwise the system setting is used. The inline <head> script applies it before first paint. */
const Theme = {
  stored() { try { return localStorage.getItem('hn_theme'); } catch { return null; } },
  current() { return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'; },
  set(mode, persist = true) {
    document.documentElement.dataset.theme = mode;
    if (persist) { try { localStorage.setItem('hn_theme', mode); } catch {} }
    document.querySelectorAll('.theme-toggle').forEach((b) => Theme.paint(b));
  },
  paint(b) { const dark = Theme.current() === 'dark'; b.textContent = dark ? 'Light mode' : 'Dark mode'; b.setAttribute('aria-pressed', String(dark)); b.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode'); },
  button() { const b = h('button', { class: 'btn quiet theme-toggle', type: 'button', onclick: () => Theme.set(Theme.current() === 'dark' ? 'light' : 'dark') }); Theme.paint(b); return b; },
};
if (window.matchMedia) matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => { if (!Theme.stored()) Theme.set(e.matches ? 'dark' : 'light', false); });
