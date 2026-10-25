let authed = false, listTimer = null;
try { localStorage.removeItem('hn_key'); } catch {}        // older versions kept the key here; it is no longer stored in the browser
const app = $('#app');
const A = (path, o = {}) => api(path, o);
const RISK = { high: 'High', medium: 'Medium', low: 'Low', info: 'Info' };
const DIM = { technical: 'Technical knowledge', problem_solving: 'Problem solving', communication: 'Communication', role_specific: 'Role-specific competencies' };
const mmss = (s) => fmtTime(s || 0);

function nav() {
  const n = $('#nav'); n.replaceChildren(Theme.button());
  if (authed) n.append(h('a', { href: '#/settings', class: 'btn quiet' }, 'Settings'), h('a', { href: '#/new', class: 'btn' }, 'New interview'),
    h('button', { class: 'btn quiet', onclick: async () => { try { await api('/api/logout', { method: 'POST' }); } catch {} authed = false; location.hash = '#/'; route(); } }, 'Sign out'));
}

function login() {
  const input = h('input', { type: 'password', id: 'key', autocomplete: 'current-password', placeholder: 'Recruiter key' });
  const err = h('p', { class: 'small', role: 'alert', style: 'color:var(--danger-text);font-weight:700' });
  const go = async (e) => { e.preventDefault(); err.textContent = '';
    try { await api('/api/login', { method: 'POST', json: { key: input.value.trim() } }); authed = true; route(); }
    catch (x) { err.textContent = x.status === 401 ? 'That key is not valid.' : x.status === 429 ? x.message : x.message; input.select(); } };
  app.replaceChildren(h('form', { class: 'panel grid', style: 'max-width:520px', onsubmit: go },
    h('h1', {}, 'Recruiter sign in'), h('p', { class: 'muted' }, 'Enter the recruiter key set in HN_RECRUITER_KEY. The default for local demos is "demo-recruiter".'),
    h('label', { class: 'field' }, h('span', {}, 'Recruiter key'), input), err, h('button', { class: 'btn', type: 'submit' }, 'Sign in')));
  input.focus();
}

const listState = { q: '', status: 'all' };
function copyText(text, msg) {
  const done = () => toast(msg), fail = () => window.prompt('Copy this link:', text);
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fail); else fail();
}
async function list() {
  let rows = await A('/api/interviews');
  const body = h('div'); const summary = h('p', { class: 'muted', 'aria-live': 'polite' });
  const search = h('input', { type: 'search', id: 'search', placeholder: 'Search by candidate or job', 'aria-label': 'Search interviews', value: listState.q, style: 'min-width:240px;flex:1' });
  const filter = h('select', { id: 'status-filter', 'aria-label': 'Filter by status' }, [['all', 'All statuses'], ['in_progress', 'In progress'], ['completed', 'Completed'], ['created', 'Not started']].map(([v, t]) => h('option', { value: v, selected: listState.status === v }, t)));
  const head = h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Interviews'), h('p', { class: 'muted' }, 'AI scores and proctoring flags support your decision; they never make it.')),
    h('div', { class: 'actions' }, h('button', { class: 'btn quiet', onclick: async (e) => { e.target.disabled = true; try { const r = await A('/api/demo', { method: 'POST' }); location.hash = `#/i/${r.id}`; } catch (x) { toast(x.message); e.target.disabled = false; } } }, 'Load a simulated demo interview'),
      h('a', { class: 'btn', href: '#/new' }, 'New interview')));
  const row = (r) => h('div', { class: 'row', 'data-id': r.id },
    h('div', {}, h('a', { class: 'row-link', href: `#/i/${r.id}` }, h('strong', {}, r.candidate)), h('div', { class: 'small muted' }, new Date(r.created_at).toLocaleString())), h('div', {}, r.job_title),
    h('div', { class: 'grid', style: 'gap:6px;justify-items:start' }, h('span', { class: 'tag ' + (r.status === 'completed' ? 'low' : 'info') }, r.status.replace('_', ' ')),
      r.status === 'in_progress' && h('span', { class: 'small' }, `${r.progress.answered} of ${r.progress.total} answered · ${r.candidate_connected ? 'candidate online' : 'candidate offline'}${r.recruiters_in_call ? ` · ${r.recruiters_in_call} in the call` : ''}`)),
    h('div', { class: 'score' }, r.overall != null ? r.overall.toFixed(1) : '–'),
    h('div', { class: 'grid', style: 'gap:6px;justify-items:start' }, r.flags ? h('span', { class: 'tag medium' }, `${r.flags} to review`) : h('span', { class: 'muted small' }, 'No flags'),
      r.expired && h('span', { class: 'tag medium' }, 'link expired'),
      h('div', { class: 'actions', style: 'position:relative;z-index:1;gap:6px' }, r.status !== 'completed' && h('a', { class: 'btn', style: 'padding:8px 14px', href: `#/live/${r.id}` }, 'Join call'),
        r.candidate_url && !r.expired && h('button', { class: 'btn quiet', type: 'button', style: 'padding:8px 14px', onclick: () => copyText(r.candidate_url, 'Candidate link copied') }, 'Copy link'))));
  const draw = () => {
    const q = listState.q.trim().toLowerCase();
    const shown = rows.filter((r) => (listState.status === 'all' || r.status === listState.status) && (!q || `${r.candidate} ${r.job_title}`.toLowerCase().includes(q)));
    const n = (st) => rows.filter((r) => r.status === st).length;
    summary.textContent = rows.length ? `${rows.length} interview${rows.length === 1 ? '' : 's'}: ${n('in_progress')} in progress, ${n('completed')} completed, ${n('created')} not started. Showing ${shown.length}.` : '';
    body.replaceChildren(shown.length ? h('div', { class: 'list' }, shown.map(row)) : h('div', { class: 'empty' }, h('h2', {}, 'No interviews match'), h('p', { class: 'muted' }, 'Try a different search or status.'), h('button', { class: 'btn quiet', onclick: () => { listState.q = ''; listState.status = 'all'; search.value = ''; filter.value = 'all'; draw(); } }, 'Clear the filters')));
  };
  search.addEventListener('input', () => { listState.q = search.value; draw(); });
  filter.addEventListener('change', () => { listState.status = filter.value; draw(); });
  if (!rows.length) { app.replaceChildren(head, h('div', { class: 'empty' }, h('h2', {}, 'No interviews yet'), h('p', { class: 'muted' }, 'Create one from a job description and a resume, or load the simulated demo to see a finished report.'),
    h('a', { class: 'btn', href: '#/new' }, 'Create the first interview'))); }
  else { app.replaceChildren(head, h('div', { class: 'actions', style: 'margin-bottom:12px' }, search, filter), summary, body); draw(); }
  // running interviews change by themselves: keep the list fresh without losing the search box or the scroll position
  clearInterval(listTimer);
  listTimer = setInterval(async () => { if (!location.hash || location.hash === '#/') { try { const fresh = await A('/api/interviews'); if (JSON.stringify(fresh) !== JSON.stringify(rows)) { rows = fresh; if (rows.length) draw(); else route(); } } catch {} } else clearInterval(listTimer); }, 5000);
}

// ---------------------------------------------------------------- new interview
let draft = null;
function newInterview(prefill) {
  const f = (id, label, el) => h('label', { class: 'field' }, h('span', {}, label), el);
  const title = h('input', { id: 'title', required: true, placeholder: 'e.g. Senior Java Backend Engineer' });
  const jd = h('textarea', { id: 'jd', required: true, rows: 9, placeholder: 'Paste the job description (responsibilities, requirements)…' });
  const skills = h('input', { id: 'skills', placeholder: 'java, spring boot, kafka (optional, detected from the JD too)' });
  const level = h('select', { id: 'level' }, h('option', { value: '' }, 'Detect from the JD / resume'), ['junior', 'mid', 'senior'].map((l) => h('option', { value: l }, l)));
  const file = h('input', { type: 'file', id: 'cv', accept: '.pdf,.docx,.txt' });
  const cvText = h('textarea', { id: 'cvtext', rows: 5, placeholder: 'Or paste the resume text' });
  const n = h('input', { id: 'n', type: 'number', min: 1, max: 40, placeholder: '10 (default)' });
  const dur = h('input', { id: 'dur', type: 'number', min: 5, max: 120, placeholder: '30' });
  const tl = h('input', { id: 'tl', type: 'number', min: 20, max: 600, placeholder: '120' });
  const fu = h('input', { id: 'fu', type: 'number', min: 0, max: 10, placeholder: '2' });
  const diff = h('select', { id: 'diff' }, h('option', { value: '' }, 'Match the level'), ['junior', 'mid', 'senior'].map((l) => h('option', { value: l }, l)));
  const cats = ['technical', 'behavioral', 'experience', 'scenario'].map((c) => h('label', { class: 'chip' }, h('input', { type: 'checkbox', value: c, checked: true, style: 'margin-right:6px' }), c));
  const gazeOn = h('input', { type: 'checkbox', id: 'gaze', style: 'margin-right:8px;width:20px;height:20px' }); const gazeN = h('input', { id: 'gazen', type: 'number', min: 1, max: 8, placeholder: '3', style: 'width:80px;background:var(--paper);border:0;padding:8px 10px' });
  const gazeReq = h('input', { type: 'checkbox', id: 'gazereq', style: 'margin-right:8px;width:20px;height:20px' });
  const backOn = h('input', { type: 'checkbox', id: 'backon', style: 'margin-right:8px;width:20px;height:20px' });
  if (!prefill) A('/api/settings').then((st) => { backOn.checked = !!st.allow_back_default; }).catch(() => {});     // a prefilled form keeps the original interview's choice
  const backBox = h('div', { class: 'grid', style: 'gap:6px' }, h('label', { class: 'actions', style: 'font-weight:700' }, backOn, 'Let the candidate go back to earlier questions and change their answers'),
    h('p', { class: 'small muted' }, 'You can switch this on or off for one candidate, or for everyone, while the interview is running.'));
  const gazeBox = h('div', { class: 'grid', style: 'gap:8px' }, h('label', { class: 'actions', style: 'font-weight:700' }, gazeOn, 'Random gaze attention check (blue dot)'), h('div', { class: 'actions small muted' }, 'Number of checks', gazeN),
    h('label', { class: 'actions', style: 'font-weight:700' }, gazeReq, 'Require eye-tracking calibration before the interview can start'),
    h('p', { class: 'small muted' }, 'If required, the candidate cannot start until calibration succeeds. You can let any candidate start without it from the interview page, even mid-call.'),
    h('p', { class: 'small muted' }, 'The candidate calibrates their eyes once, then a blue dot moves across the screen a few times at random moments and their gaze is compared with it. The candidate is told in advance and can decline. Webcam eye tracking is imprecise, so a miss is a low-severity note and only repeated misses create a flag for you to review. It is never a cheating verdict.'));
  const expires = h('select', { id: 'expires' }, [['', 'Never'], ['24', '24 hours'], ['72', '3 days'], ['168', '7 days'], ['720', '30 days']].map(([v, t]) => h('option', { value: v }, t)));
  const custom = h('textarea', { id: 'custom', rows: 3, placeholder: 'One question per line (optional). They are asked first.' });
  const err = h('p', { class: 'small', style: 'color:var(--danger-text);font-weight:700' });
  const build = () => {
    const fd = new FormData();
    fd.set('job_title', title.value); fd.set('job_description', jd.value); fd.set('skills', skills.value);
    if (level.value) fd.set('level', level.value);
    if (file.files[0]) fd.set('resume_file', file.files[0]); else fd.set('resume_text', cvText.value);
    if (n.value) fd.set('n_questions', n.value); if (dur.value) fd.set('duration_min', dur.value);
    if (tl.value) fd.set('answer_time_limit_s', tl.value); if (fu.value) fd.set('max_followups', fu.value);
    if (diff.value) fd.set('difficulty', diff.value);
    if (gazeOn.checked) { fd.set('gaze_check', 'true'); if (gazeN.value) fd.set('gaze_count', gazeN.value); if (gazeReq.checked) fd.set('gaze_required', 'true'); }
    fd.set('allow_back', backOn.checked ? 'true' : 'false'); if (expires.value) fd.set('link_expires_hours', expires.value);
    const checked = cats.map((c) => c.firstChild).filter((i) => i.checked).map((i) => i.value); fd.set('categories', checked.join(','));
    const cq = custom.value.split('\n').map((s) => s.trim()).filter(Boolean); if (cq.length) fd.set('custom_questions', JSON.stringify(cq));
    return fd;
  };
  const sample = h('button', { type: 'button', class: 'btn quiet', onclick: async () => { // fills the form with the bundled sample inputs
    title.value = 'Senior Java Backend Engineer'; skills.value = 'java, spring boot';
    jd.value = SAMPLE_JD; cvText.value = SAMPLE_CV; toast('Sample job description and resume loaded'); } }, 'Fill with a sample job and resume');
  const form = h('form', { class: 'grid', onsubmit: async (e) => { e.preventDefault(); err.textContent = ''; const b = e.submitter; b.disabled = true;
      try { draft = { form: build(), data: await A('/api/interviews/preview', { method: 'POST', form: build() }) }; preview(); } catch (x) { err.textContent = x.message; } b.disabled = false; } },
    h('div', { class: 'grid two' },
      h('div', { class: 'panel grid' }, h('h2', {}, 'The job'), f('t', 'Job title', title), f('j', 'Job description', jd), f('s', 'Required skills', skills), f('l', 'Experience level', level)),
      h('div', { class: 'panel grid' }, h('h2', {}, 'The candidate'), f('c', 'Resume (PDF, DOCX or TXT)', file), cvText, h('p', { class: 'small muted' }, 'The resume is parsed for skills, experience, education, projects and certifications.'), sample)),
    h('div', { class: 'panel grid' }, h('h2', {}, 'Interview settings'),
      h('div', { class: 'grid three' }, f('n', 'Number of questions', n), f('d', 'Duration (minutes)', dur), f('t', 'Time per answer (seconds)', tl), f('f', 'Max follow-up questions', fu), f('df', 'Difficulty', diff), f('ex', 'Candidate link expires after', expires)),
      h('div', {}, h('h2', { class: 'sub' }, 'Question categories'), h('div', { class: 'chips', style: 'margin-top:8px' }, cats)), backBox, gazeBox, f('cq', 'Your own questions', custom)),
    err, h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'submit' }, 'Analyse and generate questions')));
  if (prefill) {                                           // "interview another candidate for this job": same job and settings, new resume
    const c = prefill.config || {}, g = c.gaze_check || {};
    title.value = prefill.job.title || ''; jd.value = prefill.job.description || ''; skills.value = (prefill.job.skills || []).join(', '); level.value = c.level || '';
    n.value = c.n_questions || ''; dur.value = c.duration_min || ''; tl.value = c.answer_time_limit_s || ''; fu.value = c.max_followups ?? ''; diff.value = c.difficulty || '';
    cats.forEach((ch) => { ch.firstChild.checked = (c.categories || []).includes(ch.firstChild.value); });
    backOn.checked = !!c.allow_back; gazeOn.checked = !!g.enabled; gazeN.value = g.count || ''; gazeReq.checked = !!g.required; expires.value = c.link_expires_hours ? String(c.link_expires_hours) : '';
  }
  app.replaceChildren(h('div', { class: 'head' }, h('h1', {}, 'New interview')), prefill && h('p', { class: 'note', style: 'margin-bottom:16px' }, `Prefilled from ${prefill.candidate.name}'s interview. Add the new candidate's resume below.`), form);
}

function preview() {
  const d = draft.data;
  const qs = d.questions;
  const qlist = h('div', { class: 'grid' });
  const draw = () => qlist.replaceChildren(...qs.map((q, i) => h('div', { class: 'qcard' },
    h('div', { class: 'qhead' }, h('div', { class: 'grid', style: 'gap:6px' }, h('div', { class: 'chips' }, h('span', { class: 'tag info' }, q.category), h('span', { class: 'tag low' }, q.difficulty), q.skill && h('span', { class: 'tag low' }, q.skill), q.source !== 'generated' && h('span', { class: 'tag observed' }, q.source)),
      h('h3', {}, q.text)), h('button', { class: 'btn quiet', onclick: () => { qs.splice(i, 1); draw(); } }, 'Remove')),
    q.rationale && h('p', { class: 'small muted' }, q.rationale))));
  draw();
  const extra = h('input', { placeholder: 'Add your own question', style: 'flex:1;background:var(--paper);border:0;padding:12px 14px;min-width:240px' });
  const err = h('p', { class: 'small', style: 'color:var(--danger-text);font-weight:700' });
  const make = async (e) => { const b = e.target; b.disabled = true;
    try { const fd = draft.form; fd.set('questions_json', JSON.stringify(qs)); const r = await A('/api/interviews', { method: 'POST', form: fd }); done(r); } catch (x) { err.textContent = x.message; b.disabled = false; } };
  app.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Review the questions'), h('p', { class: 'muted' }, `${qs.length} questions for ${d.job.title}. Remove or add before sending the link.`)),
    h('button', { class: 'btn quiet', onclick: newInterview }, 'Back to settings')),
    h('div', { class: 'grid two', style: 'margin-bottom:20px' },
      h('div', { class: 'panel grid' }, h('h2', { class: 'h2s' }, 'What we found in the resume'), h('p', {}, `${d.resume.name || 'Candidate'} · ${d.resume.years_experience ?? '?'} years · ${d.resume.education[0] || 'education not found'}`),
        h('div', { class: 'chips' }, d.resume.skills.map((s) => h('span', { class: 'chip' }, s))), d.resume.certifications.length > 0 && h('p', { class: 'small muted' }, 'Certifications: ' + d.resume.certifications.join('; '))),
      h('div', { class: 'panel grid' }, h('h2', { class: 'h2s' }, 'Match against the job'), h('div', { class: 'chips' }, d.match.matched.map((s) => h('span', { class: 'chip ok' }, s)), d.match.missing.map((s) => h('span', { class: 'chip no' }, s + ' (missing)'))),
        h('p', { class: 'small muted' }, d.match.coverage != null ? `${Math.round(d.match.coverage * 100)}% of required skills appear on the resume.` : ''))),
    qlist, h('div', { class: 'actions', style: 'margin-top:12px' }, extra, h('button', { class: 'btn quiet', onclick: () => { if (extra.value.trim()) { qs.push({ text: extra.value.trim(), category: 'technical', difficulty: 'custom', skill: null, expected: [], source: 'recruiter', rationale: 'Added by the recruiter. No rubric concepts, so review its score manually.' }); extra.value = ''; draw(); } } }, 'Add question')),
    err, h('div', { class: 'actions', style: 'margin-top:20px' }, h('button', { class: 'btn', onclick: make }, 'Create interview and get the candidate link')));
}

function done(r) {
  app.replaceChildren(h('div', { class: 'panel grid', style: 'max-width:760px' }, h('h1', {}, 'Interview ready'),
    h('p', {}, `${r.n_questions} questions prepared. Send this private link to the candidate:`), h('div', { class: 'link-box' }, r.candidate_url),
    h('p', { class: 'small muted' }, 'On the day the candidate will check their camera and microphone, give consent, and can pair a phone as a second camera by scanning a QR code.'),
    h('div', { class: 'actions' }, h('button', { class: 'btn', onclick: () => navigator.clipboard.writeText(r.candidate_url).then(() => toast('Link copied'), () => toast('Select and copy the link')) }, 'Copy link'),
      h('a', { class: 'btn quiet', href: `#/i/${r.id}` }, 'Open the interview record'))));
}

// ---------------------------------------------------------------------- report
const AUDIT_TEXT = {
  consent_given: () => 'Candidate gave consent', interview_started: (x) => `Candidate started the interview (${x.questions} questions)`,
  answer_submitted: (x) => `Candidate ${x.status === 'skipped' ? 'skipped' : 'submitted an answer to'} ${x.followup ? 'a follow-up on ' : ''}question ${x.idx + 1}${x.status === 'skipped' ? '' : ` (${x.chars} characters)`}`,
  went_back: (x) => `Candidate went back to question ${x.idx + 1}`, interview_finished: (x) => ({ candidate: 'Candidate finished the interview', interviewer: 'Interviewer ended the interview', system: 'Interview closed automatically (time ran out)' }[x.by] || 'Interview finished'),
  eye_calibration: (x) => `Eye-tracking calibration ${x.ok ? 'passed' : 'did not pass'}${x.grade ? ` (${x.grade})` : ''}${x.reason ? ': ' + x.reason : ''}`,
  proctor_event: (x) => `Proctoring: ${String(x.type).replace(/_/g, ' ')} (${x.severity})`, recording_started: () => 'Recording started',
  joined_call: () => 'Interviewer joined the call', left_call: () => 'Interviewer left the call', message_sent: (x) => `Interviewer sent a message: "${x.text}"`,
  time_extended: (x) => `Interviewer added ${x.minutes} minutes`, setting_changed: (x) => `Interviewer changed "${String(x.setting).replace(/_/g, ' ')}" from ${x.from === null ? 'default' : x.from} to ${x.to === null ? 'default' : x.to}`,
  score_override: (x) => `Interviewer set the score of question ${x.idx + 1} to ${x.score ?? 'AI score'}${x.note ? ' (' + x.note + ')' : ''}`, decision: (x) => `Interviewer decided: ${x.decision}${x.notes ? ' (' + x.notes + ')' : ''}`,
  flag_reviewed: (x) => `Interviewer marked "${String(x.type).replace(/_/g, ' ')}" as ${x.status}${x.note ? ' (' + x.note + ')' : ''}`, rescored: () => 'Interviewer re-ran the AI scoring',
  recording_viewed: () => 'Interviewer opened the recording', exported: (x) => `Interviewer exported the record (${x.format})`,
};
function auditPanel(id, hasRecording) {
  const body = h('div', { class: 'grid', style: 'gap:6px' }, h('p', { class: 'muted small' }, 'Loading…')), head = h('p', { class: 'small', role: 'status' }), chips = h('div', { class: 'chips' });
  let all = [], filter = 'all';
  const FILTERS = [['all', 'Everything'], ['speech', 'Transcript'], ['interviewer', 'Interviewer actions'], ['candidate', 'Candidate actions']];
  const draw = () => {
    chips.replaceChildren(...FILTERS.map(([k, label]) => h('button', { class: 'btn quiet', type: 'button', 'aria-pressed': String(filter === k), onclick: () => { filter = k; draw(); } }, label)));
    const rows = all.filter((e) => filter === 'all' || (filter === 'speech' ? e.action === 'speech' : e.actor === filter && e.action !== 'speech'));
    body.replaceChildren(...(rows.length ? rows.map((e) => {
      const clock = new Date(e.at).toLocaleTimeString(), when = e.detail && typeof e.detail.t === 'number' ? `${mmss(e.detail.t)} in` : clock;
      const who = e.actor === 'interviewer' ? 'Interviewer' : e.actor === 'candidate' ? 'Candidate' : 'System';
      const what = e.action === 'speech' ? `"${e.detail.text}"` : (AUDIT_TEXT[e.action] ? AUDIT_TEXT[e.action](e.detail || {}) : e.action.replace(/_/g, ' '));
      return h('div', { class: 'small', style: 'display:grid;grid-template-columns:84px 92px 1fr;gap:10px;align-items:baseline' }, h('span', { class: 'muted', title: clock }, when), h('span', { class: 'tag ' + (e.actor === 'interviewer' ? 'info' : 'low') }, e.action === 'speech' ? who + ' said' : who), h('span', {}, what));
    }) : [h('p', { class: 'muted small' }, 'Nothing here yet.')]));
  };
  const panel = h('div', { class: 'panel grid', id: 'audit' }, h('h2', { class: 'h2s' }, 'Full activity record'),
    h('p', { class: 'note' }, 'Everything the interviewer and the candidate did, in order, each entry linked to the one before it so edits are detectable. The transcript is what each browser\'s speech recogniser heard: useful for searching, but it can miss words, and a speaker\'s voice can be picked up on the other person\'s microphone. ' + (hasRecording ? 'The recording above is the authoritative record.' : 'No video recording was made for this interview.')),
    head, chips, body);
  A(`/api/interviews/${id}/audit`).then((r) => {
    all = r.entries;
    head.replaceChildren(h('strong', {}, r.verify.ok ? `Record intact: ${r.verify.count} entries.` : `Record altered: entry ${r.verify.broken_at} does not match the one before it.`),
      r.verify.ok && h('span', { class: 'muted' }, ` Fingerprint ${r.verify.head.slice(0, 16)}. Copy it somewhere safe when you review; if it changes later without new activity, the record was changed.`));
    draw();
  }).catch(() => { body.replaceChildren(h('p', { class: 'muted small' }, 'The record could not be loaded.')); });
  return panel;
}
async function report(id) {
  const d = await A(`/api/interviews/${id}`);
  const ev = d.evaluation;
  const wrap = h('div', { class: 'grid' });
  const dec = d.decision || { decision: 'undecided', notes: '' };
  const decSel = h('select', { id: 'dec' }, ['undecided', 'advance', 'hold', 'reject'].map((o) => h('option', { value: o, selected: dec.decision === o }, o[0].toUpperCase() + o.slice(1))));
  const notes = h('textarea', { rows: 3, placeholder: 'Your notes and reasons' }, dec.notes || '');
  notes.value = dec.notes || '';
  wrap.append(
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, d.candidate.name), h('p', { class: 'muted' }, `${d.job.title} · ${new Date(d.created_at).toLocaleString()}${d.duration_s ? ' · ' + mmss(d.duration_s) + ' long' : ''} · ${d.status.replace('_', ' ')}`)),
      h('div', { class: 'actions' }, d.status !== 'completed' && h('a', { class: 'btn', href: `#/live/${id}` }, 'Join the live call'), d.evaluation && h('a', { class: 'btn quiet', href: `/api/interviews/${id}/export.csv`, download: '' }, 'Download CSV'), d.evaluation && h('a', { class: 'btn quiet', href: `/api/interviews/${id}/export.json`, download: '' }, 'Download JSON'), d.evaluation && h('button', { class: 'btn quiet', type: 'button', onclick: () => window.print() }, 'Print or save as PDF'), h('a', { class: 'btn quiet', href: `#/new/${id}` }, 'Interview another candidate for this job'), h('a', { class: 'btn quiet', href: '#/' }, 'All interviews'))),
    ...(d.status !== 'completed' ? [controlsPanel(id, d, () => report(id))] : []),
    ...(d.status !== 'completed' ? [h('div', { class: 'panel grid' }, h('h2', { class: 'h2s' }, 'Not finished yet'), h('p', { class: 'muted' }, 'The candidate has not completed this interview. Candidate link:'), h('div', { class: 'link-box' }, d.candidate_url))] : []));
  if (ev) {
    const c = ev.counts;
    wrap.append(h('div', { class: 'grid two rv' },
      h('div', { class: 'panel grid' }, h('div', { class: 'big' }, ev.overall != null ? ev.overall.toFixed(1) : '–', h('span', { class: 'muted', style: 'font-size:22px;letter-spacing:0' }, ' / 5 overall')),
        h('p', {}, ev.recommendation), h('p', { class: 'small muted' }, `${c.answered} answered · ${c.skipped} skipped · ${c.unanswered} unanswered of ${c.total} · scored by ${ev.engine}`)),
      h('div', { class: 'panel grid' }, h('h2', { class: 'h2s' }, 'Assessment by area'), Object.entries(ev.dimensions).map(([k, v]) => h('div', { class: 'dim' }, h('span', {}, DIM[k]),
        h('div', { class: 'bar' }, h('i', { style: `transform:scaleX(${(v || 0) / 5})` })), h('strong', {}, v != null ? v.toFixed(1) : '–'))))));
    const list = (t, items, cls) => h('div', { class: 'panel grid' }, h('h2', { class: 'h2s' }, t), items.length ? h('ul', { class: 'grid', style: 'gap:8px' }, items.map((i) => h('li', { class: 'chip ' + (cls || '') }, i))) : h('p', { class: 'muted' }, 'Nothing to report.'));
    wrap.append(h('div', { class: 'grid three rv' }, list('Strengths', ev.strengths, 'ok'), list('Knowledge gaps', ev.gaps, 'no'), list('Further assessment', ev.further_assessment)));
    wrap.append(h('h2', { style: 'margin-top:24px' }, 'Questions, answers and scores'));
    ev.per_question.forEach((p) => {
      const sel = h('select', { 'aria-label': 'Override score' }, h('option', { value: '' }, 'AI score'), [1, 2, 3, 4, 5].map((n) => h('option', { value: n, selected: p.override && p.override.score === n }, `Set to ${n}`)));
      const note = h('input', { placeholder: 'Reason for override (optional)', value: (p.override && p.override.note) || '', style: 'flex:1;min-width:200px;background:var(--paper);border:0;padding:10px 12px' });
      wrap.append(h('div', { class: 'qcard rv' },
        h('div', { class: 'qhead' }, h('div', { class: 'grid', style: 'gap:6px' }, h('div', { class: 'chips' }, h('span', { class: 'tag info' }, p.category), h('span', { class: 'tag low' }, DIM[p.dimension]), p.skill && h('span', { class: 'tag low' }, p.skill), h('span', { class: 'tag ' + (p.status === 'answered' ? 'low' : 'medium') }, p.status)), h('h3', {}, p.question)),
          h('div', { class: 'big', style: 'font-size:48px' }, p.score != null ? p.score : '–', p.override && p.override.score ? h('div', { class: 'small muted', style: 'letter-spacing:0;font-weight:500' }, `AI said ${p.ai_score ?? '–'}`) : null)),
        p.answer ? h('div', { class: 'answer' }, p.answer) : h('p', { class: 'muted' }, p.explanation),
        p.followups.map((f) => h('div', { class: 'follow' }, h('strong', {}, 'Follow-up: '), f.question, h('div', { class: 'answer', style: 'margin-top:8px' }, f.answer || '(no answer)'))),
        p.revisions > 0 && h('div', { class: 'follow' }, h('strong', {}, `Changed ${p.revisions} time${p.revisions > 1 ? 's' : ''} after first answering. `), 'Earlier versions:', p.history.map((hh) => h('div', { class: 'answer', style: 'margin-top:8px' }, `(${hh.status}) ${hh.answer || 'no answer'}`))),
        p.answer && h('p', { class: 'small' }, h('strong', {}, 'Why this score: '), p.explanation),
        p.evidence.map((e) => h('div', { class: 'ev' }, h('strong', {}, 'Evidence: '), `“${e}”`)),
        p.status === 'answered' && h('div', { class: 'actions' }, sel, note, h('button', { class: 'btn quiet', onclick: async () => {
          await A(`/api/interviews/${id}/override`, { method: 'PATCH', json: { idx: p.idx, score: sel.value ? +sel.value : null, note: note.value } }); toast('Override saved'); report(id); } }, 'Save'))));
    });
  }
  const pt = d.proctoring;
  wrap.append(h('h2', { style: 'margin-top:24px' }, 'Proctoring timeline'), h('p', { class: 'note' }, pt.note + ' "Observed" means a detector saw it; "inferred" is a pattern we derived and may have an innocent explanation.'));
  if (d.live && d.live.messages && d.live.messages.length) wrap.append(h('div', { class: 'panel grid' }, h('h3', {}, 'Messages sent to the candidate'), h('ul', { class: 'grid small', style: 'gap:6px' }, d.live.messages.map((m) => h('li', {}, `${new Date(m.at).toLocaleTimeString()}: ${m.text}`)))));
  if (d.calibration) wrap.append(h('div', { class: 'panel' }, h('h3', {}, 'Eye-tracking calibration'), h('p', {}, calibrationText(d.calibration)), d.effective.gaze.bypass && h('p', { class: 'small muted' }, 'You allowed this candidate to start without it.')));
  if (pt.gaze && (pt.gaze.passed + pt.gaze.failed + pt.gaze.unavailable)) wrap.append(h('div', { class: 'panel' }, h('h3', {}, 'Gaze attention checks'),
    h('p', {}, `${pt.gaze.passed} passed, ${pt.gaze.failed} missed, ${pt.gaze.unavailable} could not run or were declined.`), h('p', { class: 'small muted' }, 'Webcam eye tracking is imprecise. Treat misses as a prompt to look at the evidence, not as proof.')));
  if (!pt.events.length) wrap.append(h('div', { class: 'panel' }, h('p', { class: 'muted' }, 'No proctoring events were recorded.')));
  const tl = h('div', { class: 'tl' });
  pt.events.forEach((e) => {
    const dismissed = e.review && e.review.status === 'dismissed';
    const btn = (label, status) => h('button', { class: 'btn quiet', onclick: async () => { await A(`/api/events/${e.id}/review`, { method: 'PATCH', json: { status, note: '' } }); report(id); } }, label);
    tl.append(h('div', { class: `ev-row ${e.severity} ${dismissed ? 'dismissed' : ''}` }, h('strong', {}, mmss(e.t_offset)),
      h('div', {}, h('div', { class: 'chips', style: 'margin-bottom:6px' }, h('span', { class: 'tag ' + e.kind }, e.kind), h('span', { class: 'tag ' + (e.severity === 'info' ? 'info' : e.severity) }, RISK[e.severity] || e.severity), h('span', { class: 'tag low' }, e.type.replace(/_/g, ' ')), e.review && h('span', { class: 'tag info' }, e.review.status)),
        h('p', {}, e.description), e.evidence && h('img', { alt: 'Evidence snapshot', src: `/api/interviews/${id}/evidence/${e.evidence}` })),
      e.severity !== 'info' && h('div', { class: 'actions' }, btn('Not an issue', 'dismissed'), btn('Confirm concern', 'confirmed'))));
  });
  wrap.append(tl);
  if (d.recording_secondary) wrap.append(h('div', { class: 'panel grid' }, h('h3', {}, 'Second camera (phone) recording'), ...Array.from({ length: Math.min(d.recording_secondary, 10) }, (_, i) => h('div', { class: 'grid', style: 'gap:6px' }, d.recording_secondary > 1 && h('p', { class: 'small muted' }, `Part ${i + 1} of ${d.recording_secondary} (the phone reconnected)`), h('video', { controls: true, src: `/api/interviews/${id}/recording?cam=secondary&part=${i}` }))), h('p', { class: 'small muted' }, 'The view from the candidate\'s phone, recorded with their consent. Flags such as "more than one person in the second camera" refer to this view.')));
  if (d.recording) wrap.append(h('div', { class: 'panel grid' }, h('h3', {}, 'Interview recording'), h('video', { controls: true, src: `/api/interviews/${id}/recording` }), h('p', { class: 'small muted' }, 'Recorded with the candidate\'s consent. Jump to flagged times shown above.')));
  wrap.append(auditPanel(id, !!d.recording));
  wrap.append(h('div', { class: 'panel grid' }, h('h2', {}, 'Your decision'), h('p', { class: 'muted' }, 'AI scores and proctoring flags are inputs, not verdicts. The decision is yours.'), h('label', { class: 'field', style: 'max-width:320px' }, h('span', {}, 'Decision'), decSel), h('label', { class: 'field' }, h('span', {}, 'Notes'), notes),
    h('div', { class: 'actions' }, h('button', { class: 'btn', onclick: async () => { await A(`/api/interviews/${id}/decision`, { method: 'PATCH', json: { decision: decSel.value, notes: notes.value } }); toast('Decision saved'); } }, 'Save decision'),
      h('button', { class: 'btn quiet', onclick: async () => { await A(`/api/interviews/${id}/reevaluate`, { method: 'POST' }); toast('Re-scored'); report(id); } }, 'Re-run AI scoring'),
      h('button', { class: 'btn danger', onclick: async () => { if (confirm('Delete this interview, its answers, evidence and recording permanently?')) { await A(`/api/interviews/${id}`, { method: 'DELETE' }); location.hash = '#/'; } } }, 'Delete all data'))));
  if (ev) wrap.append(h('ul', { class: 'grid small muted', style: 'gap:4px' }, ev.caveats.map((c) => h('li', {}, c))));
  app.replaceChildren(wrap); reveal();
}


function calibrationText(c) {
  if (!c) return 'The candidate has not calibrated eye tracking.';
  return c.ok ? `Calibrated: ${c.grade} accuracy using ${c.mode || 'the available signals'}${c.error != null ? ` (prediction error about ${(c.error * 100).toFixed(0)}% of the screen)` : ''}.`
    : `Calibration did not succeed${c.reason ? ': ' + c.reason : ''}. Eye-tracking checks cannot run for this candidate.`;
}

/** Switches the interviewer can change at any moment, for this one candidate. */
function controlsPanel(id, d, refresh) {
  const live = d.live || {}, eff = d.effective;
  const send = async (patch, msg) => { try { await A(`/api/interviews/${id}/live`, { method: 'PATCH', json: patch }); toast(msg); refresh(); } catch (e) { toast(e.message); } };
  const sel = (label, value, opts, onchange, id2) => h('label', { class: 'field' }, h('span', {}, label), h('select', { id: id2, onchange: (e) => onchange(e.target.value) }, opts.map(([v, t]) => h('option', { value: v, selected: String(value) === v }, t))));
  const triState = (v) => (v === null || v === undefined ? 'inherit' : String(v));
  const parse = (v) => (v === 'inherit' ? null : v === 'true');
  const g = eff.gaze;
  const msg = h('input', { type: 'text', id: 'msg-input', maxlength: 300, placeholder: 'Message for the candidate\'s screen', 'aria-label': 'Message to the candidate', style: 'flex:1;min-width:220px', onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); sendMsg(); } } });
  const sendMsg = async () => { const t = msg.value.trim(); if (!t) return toast('Write a message first.'); try { await A(`/api/interviews/${id}/message`, { method: 'POST', json: { text: t } }); msg.value = ''; toast('Message sent'); refresh(); } catch (e) { toast(e.message); } };
  const addTime = (m) => h('button', { class: 'btn quiet', type: 'button', onclick: async () => { try { const r = await A(`/api/interviews/${id}/extend`, { method: 'POST', json: { minutes: m } }); toast(`The interview is now ${r.duration_min} minutes long`); refresh(); } catch (e) { toast(e.message); } } }, `+${m} min`);
  const endBtn = h('button', { class: 'btn danger', type: 'button', id: 'end-now', onclick: async () => {
    if (!confirm('End this interview now? Unanswered questions are marked, scoring runs, and the candidate\'s page closes.')) return;
    try { await A(`/api/interviews/${id}/end`, { method: 'POST' }); toast('Interview ended'); refresh(); } catch (e) { toast(e.message); } } }, 'End the interview now');
  return h('div', { class: 'panel grid', id: 'controls' }, h('h2', { class: 'h2s' }, 'Controls for this candidate (change any time)'),
    h('div', { class: 'grid three' },
      sel('Going back to earlier questions', triState(live.allow_back), [['inherit', `Interview default (${d.config.allow_back ? 'allowed' : 'not allowed'})`], ['true', 'Allowed'], ['false', 'Not allowed']], (v) => send({ allow_back: parse(v) }, 'Saved'), 'sw-back'),
      sel('Eye-tracking checks', triState(live.gaze_enabled), [['inherit', `Interview default (${d.config.gaze_check.enabled ? 'on' : 'off'})`], ['true', 'On'], ['false', 'Off']], (v) => send({ gaze_enabled: parse(v) }, 'Saved'), 'sw-gaze'),
      h('div', { class: 'field' }, h('span', {}, 'Calibration requirement'), h('label', { class: 'actions', style: 'font-weight:500' }, h('input', { type: 'checkbox', id: 'sw-bypass', checked: !!live.gaze_bypass, style: 'width:20px;height:20px', onchange: (e) => send({ gaze_bypass: e.target.checked }, e.target.checked ? 'This candidate can now start without eye tracking' : 'Requirement restored') }), 'Let this candidate start without eye tracking'))),
    h('p', { class: 'small muted' }, `Right now: going back is ${eff.allow_back ? 'allowed' : 'not allowed'}; eye tracking is ${g.enabled ? (g.required ? 'on and required' : 'on') : 'off'}${g.bypass ? ' (requirement bypassed)' : ''}.` +
      (eff.global.back_off ? ' Going back is switched off for everyone in Settings.' : '') + (eff.global.gaze_off ? ' Eye tracking is switched off for everyone in Settings.' : '')),
    h('div', { class: 'grid', style: 'gap:10px' },
      h('div', { class: 'actions' }, h('strong', {}, `Time: ${eff.duration_min} minutes.`), h('span', { class: 'small muted' }, 'Add time:'), [5, 10, 15].map(addTime)),
      h('div', { class: 'actions' }, msg, h('button', { class: 'btn', type: 'button', id: 'msg-send', onclick: sendMsg }, 'Send message')),
      h('p', { class: 'small muted' }, 'A message appears on the candidate\'s screen within a few seconds and is kept in the record.'),
      h('div', { class: 'actions' }, endBtn)));
}

async function settingsPage() {
  const st = await A('/api/settings');
  const toggle = (key, title, text) => h('label', { class: 'check', style: 'display:grid;grid-template-columns:auto 1fr;gap:12px;background:var(--paper);padding:14px 16px' },
    h('input', { type: 'checkbox', checked: !!st[key], style: 'width:22px;height:22px;margin-top:3px', onchange: async (e) => { try { await A('/api/settings', { method: 'PATCH', json: { [key]: e.target.checked } }); toast('Saved'); } catch (x) { toast(x.message); e.target.checked = !e.target.checked; } } }),
    h('span', {}, h('strong', {}, title), h('br'), text));
  app.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'Settings'), h('p', { class: 'muted' }, 'Switches that apply to every interview, including ones already running.'))),
    h('div', { class: 'panel grid', style: 'max-width:820px' }, h('h2', {}, 'Going back to earlier questions'),
      toggle('allow_back_default', 'Allow going back by default for new interviews', 'You can still change it per interview when you create it, and per candidate while it runs.'),
      toggle('back_global_off', 'Turn off going back for everyone, right now', 'A kill switch: it overrides every interview and takes effect within a few seconds, even mid-interview.'),
      h('h2', { style: 'margin-top:16px' }, 'Eye-tracking checks'),
      toggle('gaze_global_off', 'Turn off eye-tracking checks for everyone, right now', 'A kill switch: no calibration requirement or dot checks run anywhere until you switch it back.')));
}

/** Join an interview like a video call: see and hear the candidate (and their phone camera), and speak to them. */
async function livePage(id) {
  let d = await A(`/api/interviews/${id}`);
  const RTCCFG = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };
  let ws, pc, candId = null, meta = {}, closed = false, poll, retries = 0; const local = { audio: null, video: null };
  const vMain = h('video', { autoplay: true, playsinline: true, 'aria-label': 'Candidate camera', style: 'width:100%;background:#0b140f;aspect-ratio:4/3;object-fit:cover' });
  const vSec = h('video', { autoplay: true, playsinline: true, muted: true, 'aria-label': 'Candidate second camera', style: 'width:100%;background:#0b140f;aspect-ratio:4/3;object-fit:cover' });
  const vSelf = h('video', { autoplay: true, playsinline: true, muted: true, 'aria-label': 'Your camera', style: 'width:100%;background:#0b140f;aspect-ratio:4/3;object-fit:cover' });
  const status = h('p', { class: 'note', role: 'status' }, 'Connecting…');
  const micBtn = h('button', { class: 'btn quiet', id: 'live-mic', type: 'button', onclick: () => toggle('audio') }), camBtn = h('button', { class: 'btn quiet', id: 'live-cam', type: 'button', onclick: () => toggle('video') });
  const leaveBtn = h('button', { class: 'btn danger', id: 'live-leave', type: 'button', onclick: leave }, 'Leave call');
  const side = h('div', { class: 'grid' }); const controlsSlot = h('div'); const secNote = h('p', { class: 'small muted', role: 'status', id: 'sec-note' }, 'The candidate has not connected a phone camera.'); const secTile = h('div', { class: 'panel', id: 'sec-tile' }, h('h2', { class: 'sub', style: 'margin-bottom:8px' }, 'Second camera (phone)'), vSec, secNote);
  const secLive = () => !!(vSec.srcObject && vSec.srcObject.getVideoTracks().some((t) => t.readyState === 'live'));
  const paint = () => { micBtn.textContent = local.audio ? 'Mute my microphone' : 'Turn on my microphone'; camBtn.textContent = local.video ? 'Turn off my camera' : 'Turn on my camera'; vSelf.srcObject = local.video ? new MediaStream([local.video]) : null; vSelf.parentElement && (vSelf.parentElement.hidden = !local.video); };
  const send = (o) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); };
  function applyLocal() {                                  // replaceTrack on the negotiated transceivers: no renegotiation needed
    if (!pc) return;
    for (const kind of ['audio', 'video']) { const t = pc.getTransceivers().find((x) => x.receiver.track && x.receiver.track.kind === kind); if (t) t.sender.replaceTrack(local[kind]).catch(() => {}); }
  }
  /* What the interviewer says is written to the interview's record. It uses the browser's own speech recogniser, so it only runs while the microphone is on. */
  const Hear = {
    rec: null, want: false,
    start() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition; if (!SR || this.want) return; this.want = true; this.run(SR);
    },
    run(SR) {
      if (!this.want || this.rec) return;
      const r = new SR(); r.lang = 'en-IN'; r.continuous = true; r.interimResults = false; this.rec = r;
      r.onresult = (e) => { for (let i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) { const text = e.results[i][0].transcript.trim(); if (text) A(`/api/interviews/${id}/transcript`, { method: 'POST', json: { text: text.slice(0, 600) } }).catch(() => {}); } };
      r.onerror = (e) => { if (e.error === 'not-allowed' || e.error === 'service-not-allowed') this.want = false; };
      r.onend = () => { if (this.rec === r) { this.rec = null; if (this.want) setTimeout(() => this.run(SR), 400); } };
      try { r.start(); } catch { this.rec = null; this.want = false; }
    },
    stop() { this.want = false; const r = this.rec; this.rec = null; if (r) { try { r.stop(); } catch {} } },
  };
  async function toggle(kind) {
    if (local[kind]) { local[kind].stop(); local[kind] = null; if (kind === 'audio') Hear.stop(); }
    else { try { const s = await navigator.mediaDevices.getUserMedia({ [kind]: true }); local[kind] = s.getTracks()[0]; if (kind === 'audio') Hear.start(); } catch { toast(kind === 'audio' ? 'Microphone is blocked in this browser.' : 'Camera is blocked in this browser.'); } }
    applyLocal(); paint(); sendState();
  }
  const sendState = () => { if (candId) send({ type: 'state', to: candId, cam: !!local.video, mic: !!local.audio }); };
  async function onOffer(m) {
    candId = m.from;
    if (!pc) {
      pc = new RTCPeerConnection(RTCCFG);
      pc.onicecandidate = (e) => { if (e.candidate && candId) send({ type: 'ice', to: candId, candidate: e.candidate }); };
      pc.ontrack = (e) => { const sid = e.streams[0] && e.streams[0].id; const isSec = sid && sid === meta.secondary; const v = isSec ? vSec : vMain; if (isSec) { secNote.textContent = 'Live from the candidate\'s phone.'; vSec.hidden = false; } if (e.streams[0] && v.srcObject !== e.streams[0]) v.srcObject = e.streams[0]; v.play().catch(() => {}); };
      pc.onconnectionstatechange = () => { if (pc && pc.connectionState === 'connected') status.textContent = 'Connected. The candidate can see a notice that you joined.'; if (pc && pc.connectionState === 'failed') status.textContent = 'Connection problem. Reload the page to try again.'; };
    }
    await pc.setRemoteDescription({ type: 'offer', sdp: m.sdp });
    pc.getTransceivers().forEach((t) => { if (t.receiver.track) t.direction = 'sendrecv'; });
    applyLocal();
    await pc.setLocalDescription(await pc.createAnswer()); send({ type: 'answer', to: candId, sdp: pc.localDescription.sdp }); sendState();
  }
  function connect() {
    ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/live/${id}/recruiter`);
    ws.onmessage = async (e) => { let m; try { m = JSON.parse(e.data); } catch { return; }
      if (m.type === 'hello') { retries = 0; const c = m.peers.find((p) => p.role === 'candidate'); status.textContent = c ? 'Candidate is here. Connecting video…' : 'Waiting for the candidate to open their interview page…'; }
      else if (m.type === 'peer-joined' && m.role === 'candidate') status.textContent = 'Candidate arrived. Connecting video…';
      else if (m.type === 'peer-left') { if (pc) { pc.close(); pc = null; } vMain.srcObject = vSec.srcObject = null; secNote.textContent = 'The candidate left or reloaded their page.'; status.textContent = 'The candidate left or reloaded their page. Waiting…'; }
      else if (m.type === 'meta') meta = { primary: m.primary, secondary: m.secondary };
      else if (m.type === 'offer') { try { await onOffer(m); } catch (x) { status.textContent = 'Could not connect video: ' + x.message; } }
      else if (m.type === 'ice' && pc) pc.addIceCandidate(m.candidate).catch(() => {}); };
    ws.onclose = (e) => { if (!closed && e.code !== 4401 && e.code !== 4404 && retries++ < 10) setTimeout(connect, 2000); else if (e.code === 4401) status.textContent = 'Your recruiter key was not accepted for the call.'; };
  }
  function leave() { closed = true; clearInterval(poll); Hear.stop(); try { ws && ws.close(); } catch {} if (pc) pc.close(); ['audio', 'video'].forEach((k) => local[k] && local[k].stop()); location.hash = `#/i/${id}`; }
  const live = h('div', { class: 'panel grid' }, h('h2', { class: 'h2s' }, 'Live status'), h('div', { id: 'live-q', class: 'small' }, 'Loading…'), h('div', { id: 'live-ev', class: 'grid small', style: 'gap:6px' }), h('div', { id: 'live-cal', class: 'small muted' }));
  async function refreshStatus() {
    try {
      const st = await A(`/api/interviews/${id}/live-status`); d = { ...d, live: st.live, effective: st.effective, calibration: st.calibration };
      $('#live-q').replaceChildren(st.status === 'in_progress' && st.current ? h('div', {}, h('strong', {}, `${st.candidate}: question ${st.current.number} of ${st.current.total}${st.current.is_followup ? ' (follow-up)' : ''}${st.current.revisiting ? ' (reviewing an earlier answer)' : ''}`), h('p', { style: 'margin-top:4px' }, st.current.question), h('p', { class: 'muted' }, `${fmtTime(st.current.remaining_s)} left in the interview`))
        : h('strong', {}, st.status === 'completed' ? 'The interview has finished.' : 'The candidate has not started yet.'));
      $('#live-ev').replaceChildren(...st.events.map((e) => h('div', {}, h('span', { class: 'tag ' + (e.severity === 'info' ? 'info' : e.severity) }, e.type.replace(/_/g, ' ')), ' ', e.description)));
      $('#live-cal').textContent = calibrationText(st.calibration);
      if (st.status === 'completed') { vSec.srcObject = null; secNote.textContent = 'The interview has ended. The phone camera is off.'; }
      else if (!secLive()) secNote.textContent = st.call && st.call.phone ? 'Phone connected. Waiting for its video…' : 'The candidate has not connected a phone camera.';
      controlsSlot.replaceChildren(controlsPanel(id, d, refreshStatus));
    } catch (e) { if (e.status === 401) leave(); }
  }
  side.append(h('div', { class: 'panel' }, h('h2', { class: 'sub', style: 'margin-bottom:8px' }, 'You'), vSelf), secTile, live);
  app.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, `Live: ${d.candidate.name}`), h('p', { class: 'muted' }, d.job.title)), h('a', { class: 'btn quiet', href: `#/i/${id}` }, 'Interview record')),
    status, h('div', { class: 'grid live-grid' }, h('div', { class: 'grid' }, h('div', { class: 'panel' }, vMain), h('div', { class: 'actions' }, micBtn, camBtn, leaveBtn),
      h('p', { class: 'small muted' }, 'The candidate sees a notice and your camera tile when you join. Audio and video go directly between your browser and theirs.'), controlsSlot), side));
  paint(); connect(); refreshStatus(); poll = setInterval(refreshStatus, 2500);
  window.__leaveLive = leave; window.addEventListener('hashchange', () => { if (!location.hash.startsWith('#/live/')) { closed = true; clearInterval(poll); try { ws && ws.close(); } catch {} if (pc) pc.close(); ['audio', 'video'].forEach((k) => local[k] && local[k].stop()); } }, { once: true });
}

const SAMPLE_JD = `Senior Java Backend Engineer\nPayments Platform Team\n\nWe are looking for a backend engineer with 6+ years of experience to build and run the services behind our payments platform.\n\nResponsibilities:\n- Design and build Spring Boot microservices that process millions of transactions a day\n- Own PostgreSQL schemas, query performance and data consistency across services\n- Build event-driven pipelines with Kafka and make them reliable under failure\n- Deploy and operate services with Docker and Kubernetes on AWS\n- Review code, mentor teammates and improve testing and CI/CD practices\n\nRequirements:\n- Strong Java and Spring Boot\n- Experience with microservices, REST API design and Kafka\n- Solid SQL and PostgreSQL\n- Docker, Kubernetes and AWS\n- Clear communication and ownership`;
const SAMPLE_CV = `Souparna Saha\n26ee3014@rgipt.ac.in\n\nSummary\nBackend engineer with 5 years of experience building payment and inventory systems in Java.\n\nExperience\n- Backend Engineer, Acme Payments (2021 - 2026): Java, Spring Boot microservices, PostgreSQL, Kafka, Docker\n- Software Engineer, Retail Labs (2019 - 2021): REST API development and SQL reporting\n\nProjects\n- Payments gateway: Java Spring Boot services with Kafka events, idempotent retries and Docker deployment\n- Inventory sync: REST API and PostgreSQL batch pipeline handling 2M records per night\n- Fraud rules engine: rule evaluation service with unit and integration testing in CI\n\nEducation\nB.Tech Computer Science, Rajiv Gandhi Institute of Petroleum Technology\n\nCertifications\n- AWS Certified Developer Associate`;

function notFound() {
  app.replaceChildren(h('div', { class: 'panel grid', style: 'max-width:640px' }, h('h1', {}, 'Page not found'), h('p', {}, 'There is no page at this address. It may be mistyped or out of date.'), h('div', { class: 'actions' }, h('a', { class: 'btn', href: '#/' }, 'Back to all interviews'))));
}
async function route() {
  clearInterval(listTimer);
  if (!authed) { try { await api('/api/session'); authed = true; } catch { authed = false; } }
  nav();
  if (!authed) return login();
  const hash = location.hash || '#/';
  try {
    if (hash.startsWith('#/i/')) await report(hash.slice(4));
    else if (hash.startsWith('#/live/')) await livePage(hash.slice(7));
    else if (hash === '#/settings') await settingsPage();
    else if (hash.startsWith('#/new/')) newInterview(await A(`/api/interviews/${hash.slice(6)}`));
    else if (hash === '#/new') newInterview();
    else if (hash === '#/' || hash === '#' || hash === '') await list();
    else notFound();
  } catch (e) {
    if (e.status === 401) { authed = false; nav(); login(); }
    else app.replaceChildren(h('div', { class: 'panel grid' }, h('h2', {}, e.status === 404 || e.status === 422 ? 'Interview not found' : 'Something went wrong'), h('p', {}, e.status === 404 || e.status === 422 ? 'This interview does not exist, or it was deleted.' : e.message), h('a', { class: 'btn', href: '#/' }, 'Back to all interviews')));
  }
}
addEventListener('hashchange', () => { route().then(() => { const t = app.querySelector('h1'); if (t) { t.tabIndex = -1; t.focus({ preventScroll: true }); } }); });
route();
