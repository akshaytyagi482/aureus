'use strict';

/* AureusRecon web client: hash-routed single page, no build step. */

const store = {
  token: localStorage.getItem('ar_token'),
  user: JSON.parse(localStorage.getItem('ar_user') || 'null'),
  meta: null,
  cache: {},
};
const app = document.getElementById('app');

// ---------------------------------------------------------------- helpers

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const inrFmt = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 });
const inrFmt2 = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2 });
const inr = (n) => inrFmt.format(n || 0);
const inr2 = (n) => inrFmt2.format(n || 0);
function inrShort(n) {
  n = n || 0;
  if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
  if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(2)} L`;
  return inr(n);
}
const fmtDate = (iso) => iso ? new Date(iso + (iso.length === 10 ? 'T00:00:00' : '')).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '';
const periodLabel = (p) => p ? new Date(+p.slice(2), +p.slice(0, 2) - 1, 1).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' }) : 'All periods';
const STATUS_LABEL = { MATCHED_ELIGIBLE: 'Matched', MISMATCH_WARN: 'Mismatch', MISSING_IN_PORTAL: 'Missing in portal' };
const badge = (status, text) => `<span class="badge ${esc(status)}">${esc(text || STATUS_LABEL[status] || status)}</span>`;

function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toast.t);
  toast.t = setTimeout(() => el.classList.remove('show'), 2600);
}

function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

class ApiError extends Error {
  constructor(status, detail, code) { super(detail); this.status = status; this.code = code; }
}

async function api(path, { method = 'GET', body, form, raw } = {}) {
  const headers = {};
  if (store.token) headers.Authorization = `Bearer ${store.token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  const res = await fetch(path, { method, headers, body: payload });
  if (res.status === 401 && store.token) { setSession(null, null); go('#/login'); throw new ApiError(401, 'Please sign in again'); }
  if (!res.ok) {
    let detail = res.statusText, code;
    try { const j = await res.json(); code = j.code; detail = typeof j.detail === 'string' ? j.detail : (j.detail || []).map((d) => d.msg).join('; '); } catch (_) { /* not JSON */ }
    throw new ApiError(res.status, detail, code);
  }
  return raw ? res : res.json();
}

function setSession(token, user) {
  store.token = token; store.user = user; store.cache = {};
  if (token) { localStorage.setItem('ar_token', token); localStorage.setItem('ar_user', JSON.stringify(user)); }
  else { localStorage.removeItem('ar_token'); localStorage.removeItem('ar_user'); }
}

async function meta() {
  if (!store.meta) store.meta = await api('/api/public/meta');
  return store.meta;
}

function formData(form) {
  const out = {};
  for (const [k, v] of new FormData(form).entries()) out[k] = typeof v === 'string' ? v.trim() : v;
  return out;
}
const numOrNull = (v) => (v === '' || v == null ? null : Number(String(v).replace(/,/g, '')));

function download(filename, content, type) {
  const url = URL.createObjectURL(content instanceof Blob ? content : new Blob([content], { type }));
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function modal(html) {
  const root = document.getElementById('modal-root');
  root.innerHTML = `<div class="modal-backdrop" data-action="close-modal-bg"><div class="modal" role="dialog" aria-modal="true">${html}</div></div>`;
  root.querySelector('.modal button, .modal a, .modal textarea')?.focus();
}
function closeModal() { document.getElementById('modal-root').innerHTML = ''; }

function busy(button, on, label) {
  if (!button) return;
  if (on) { button.dataset.label = button.innerHTML; button.disabled = true; button.textContent = label || 'Working…'; }
  else { button.disabled = false; if (button.dataset.label) button.innerHTML = button.dataset.label; }
}

function showError(err, where) {
  const msg = err instanceof ApiError ? err.message : 'Something went wrong. Please try again.';
  if (where) where.innerHTML = `<div class="banner danger">${esc(msg)}</div>`;
  else toast(msg);
  if (!(err instanceof ApiError)) console.error(err);
}

// ---------------------------------------------------------------- navigation chrome

function renderNav() {
  const nav = document.getElementById('nav');
  const sub = document.getElementById('brand-sub');
  sub.textContent = store.user?.role === 'ca' && store.user.firm_name ? `for ${store.user.firm_name}` : 'Fiscal operations & compliance engine';
  if (!store.user) {
    nav.innerHTML = `<a href="#/calculator">Subsidy calculator</a><a href="#/coverage">State coverage</a><a href="#/login">Sign in</a><a class="cta" href="#/signup">Get started</a>`;
    return;
  }
  nav.innerHTML = `${store.user.role === 'ca' ? '<a href="#/console">Client console</a>' : '<a href="#/home">My business</a>'}
    <a href="#/calculator">Calculator</a>
    <a href="#/account">${esc(store.user.name)}</a>
    <button data-action="logout">Sign out</button>`;
}

// ---------------------------------------------------------------- landing & calculator

async function viewLanding() {
  app.innerHTML = `
  <section class="hero">
    <div>
      <span class="pill">For Indian manufacturing MSMEs and their Chartered Accountants</span>
      <h1>Stop losing GST credit. Claim the subsidies you have already earned.</h1>
      <p class="lead">AureusRecon sits on top of Tally, Zoho Books or Busy. It matches every purchase bill against GSTR-2B to
      protect your input tax credit, and scans state industrial policies to turn your machinery purchases into cash-back claims.</p>
      <div class="row" style="margin:1.2rem 0">
        <a class="btn primary" href="#/signup">Start free</a>
        <a class="btn" href="#/signup?role=ca">I'm a CA managing clients</a>
      </div>
      <p class="muted">No GST portal or bank passwords, ever. Data is encrypted with AES-256 and can be deleted with one click.</p>
    </div>
    <div class="card" id="calc-card">${await calculatorForm()}</div>
  </section>
  <section class="features">
    <div class="card feature"><h3><span class="icon">⇄</span>Tax engine</h3>
      <p>Matches every purchase invoice to GSTR-2B on supplier GSTIN and invoice number, down to the paisa. Flags missing uploads,
      value and tax-head mismatches, and supplier typos before your GSTR-3B is due.</p></div>
    <div class="card feature"><h3><span class="icon">₹</span>Policy engine</h3>
      <p>Reads the HSN codes on your capital purchases, checks them against state subsidy rules, applies minimums and caps,
      and pre-fills District Industries Centre applications from verified invoices.</p></div>
    <div class="card feature"><h3><span class="icon">§</span>Notice copilot</h3>
      <p>Drafts section-specific replies to DRC-01C intimations, ASMT-10 scrutiny notices and DRC-01 show-cause notices,
      with the evidence annexures your officer will ask for.</p></div>
    <div class="card feature"><h3><span class="icon">⌂</span>Built for CA firms</h3>
      <p>One console for every client: who is losing credit, who qualifies for subsidies, and whose filing is due next,
      under your firm's name.</p></div>
  </section>
  <footer class="site">AureusRecon · Subsidy figures come from official state and central policy documents, cited on each scheme; estimates are ceilings until the DIC approves.</footer>`;
}

async function viewCoverage() {
  const m = await meta();
  const rows = Object.entries(m.coverage).filter(([code]) => !['IN', 'NE'].includes(code))
    .sort((a, b) => (a[1].state || '').localeCompare(b[1].state || ''));
  const central = ['IN', 'NE'].map((c) => [c, m.coverage[c]]).filter(([, v]) => v);
  const badgeFor = (c) => c.status !== 'current' ? badge('bad', c.status === 'expired' ? 'No current policy' : 'Not found')
    : c.verified_schemes === 0 ? badge('warn', 'Unverified') : c.verified_schemes < c.schemes ? badge('warn', 'Partly verified') : badge('ok', 'Verified');
  const table = (list) => `<table><thead><tr><th>State</th><th>Status</th><th>Policy</th><th class="num">Verified / schemes</th><th>Open questions</th></tr></thead><tbody>
    ${list.map(([code, c]) => `<tr><td><strong>${esc(c.state || code)}</strong></td><td>${badgeFor(c)}</td><td>${esc(c.policy || '—')}${c.valid_to ? `<br><small>valid to ${fmtDate(c.valid_to)}</small>` : ''}</td>
      <td class="num">${c.verified_schemes || 0} / ${c.schemes || 0}</td><td><small>${esc((c.gaps || []).slice(0, 2).map((g) => g.length > 160 ? g.slice(0, 160) + '…' : g).join(' · '))}</small></td></tr>`).join('')}</tbody></table>`;
  app.innerHTML = `<h1>Subsidy rule coverage</h1>
    <p class="muted">Every state's current industrial or MSME incentive policy, checked against the official document where it could be reached.
      Verified figures carry the clause they come from; unverified ones are shown with a warning.</p>
    <div class="card table-wrap">${table(rows)}</div>
    <h2>Central schemes</h2><div class="card table-wrap">${table(central)}</div>`;
}

async function viewCalculator() {
  app.innerHTML = `<div style="max-width:760px;margin:0 auto"><div class="card" id="calc-card">${await calculatorForm()}</div></div>`;
}

async function calculatorForm() {
  const m = await meta();
  const states = m.states.map((s) => `<option value="${s.code}" ${s.code === '27' ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
  return `
    <h2>60-second subsidy calculator</h2>
    <p class="muted">Tell us where your unit is and what you invested recently. No sign-up needed.</p>
    <form data-form="calculator">
      <div class="fields">
        <div class="field"><label>State</label><select name="state_code">${states}</select></div>
        <div class="field"><label>Enterprise size</label><select name="category">
          <option value="micro">Micro (P&amp;M up to ₹2.5 Cr)</option><option value="small" selected>Small (up to ₹25 Cr)</option>
          <option value="medium">Medium (up to ₹125 Cr)</option></select></div>
      </div>
      <div class="fields">
        <div class="field"><label>Plant &amp; machinery bought <span class="hint">(₹, last 2 years)</span></label><input name="plant_machinery" inputmode="numeric" placeholder="50,00,000"></div>
        <div class="field"><label>Testing / quality equipment <span class="hint">(₹)</span></label><input name="testing_equipment" inputmode="numeric" placeholder="0"></div>
        <div class="field"><label>Solar plant <span class="hint">(₹)</span></label><input name="solar" inputmode="numeric" placeholder="0"></div>
        <div class="field"><label>Pollution control equipment <span class="hint">(₹)</span></label><input name="pollution_control" inputmode="numeric" placeholder="0"></div>
        <div class="field"><label>Term loan for machinery <span class="hint">(₹, optional)</span></label><input name="term_loan_amount" inputmode="numeric"></div>
      </div>
      <div class="fields">
        <div class="field"><label>Area / zone category</label><div data-zone-field>${zoneSelect(m, '', '27')}</div></div>
        <div class="field"><label>Monthly electricity duty <span class="hint">(₹, on your bill)</span></label><input name="monthly_electricity_duty" inputmode="numeric"></div>
        <div class="field"><label>Monthly power use <span class="hint">(units / kWh)</span></label><input name="monthly_power_units" inputmode="numeric"></div>
      </div>
      <div data-zone-hint-box>${zoneHint(m, '27')}</div>
      <button class="gold" type="submit">Show my estimate</button>
    </form>
    <div id="calc-result"></div>`;
}

async function submitCalculator(form) {
  const d = formData(form);
  const investments = {};
  for (const k of ['plant_machinery', 'testing_equipment', 'solar', 'pollution_control']) if (numOrNull(d[k])) investments[k] = numOrNull(d[k]);
  const out = document.getElementById('calc-result');
  try {
    const r = await api('/api/public/subsidy-calculator', { method: 'POST', body: {
      state_code: d.state_code, category: d.category, investments, zone: d.zone || null,
      monthly_electricity_duty: numOrNull(d.monthly_electricity_duty), monthly_power_units: numOrNull(d.monthly_power_units),
      term_loan_amount: numOrNull(d.term_loan_amount) } });
    const eligible = r.schemes.filter((s) => s.status === 'eligible' && s.in_total);
    const extra = r.schemes.filter((s) => s.status === 'eligible' && !s.in_total);
    const needs = r.schemes.filter((s) => s.status === 'needs_info');
    out.innerHTML = `<hr style="border:0;border-top:1px solid var(--line);margin:1.2rem 0">
      <div class="muted">Estimated incentives you may be able to claim</div>
      <div class="result-total">${inrShort(r.total_estimated_benefit)}</div>
      ${eligible.length ? `<ul class="tight">${eligible.map((s) => `<li><strong>${inrShort(s.estimated_benefit)}</strong> · ${esc(s.name)} <span class="muted">(${esc(s.basis)})</span></li>`).join('')}</ul>`
        : '<p>No scheme in our directory matches these inputs yet. Try adding your machinery spend.</p>'}
      ${extra.map((s) => `<p class="muted">Possibly also: ${esc(s.name)}, ${inrShort(s.estimated_benefit)} (${esc(s.basis)}). Not added to the total.</p>`).join('')}
      ${needs.map((s) => `<p class="muted">Also check: ${esc(s.name)}. Needs ${esc(s.missing_inputs.join(', '))}.</p>`).join('')}
      <div class="banner warn" style="margin-top:.8rem">${esc(r.disclaimer)}</div>
      <div class="banner info"><strong>Unlock the invoice-level breakdown.</strong> Sync your accounting data and AureusRecon finds the exact qualifying
      purchases and pre-fills the DIC application. <a href="#/signup">Create a free account →</a></div>`;
  } catch (err) { showError(err, out); }
}

// ---------------------------------------------------------------- auth

function viewLogin() {
  app.innerHTML = `<div class="auth card"><h1>Sign in</h1>
    <form data-form="login"><div class="field"><label>Email</label><input name="email" type="email" autocomplete="email" required></div>
    <div class="field"><label>Password</label><input name="password" type="password" autocomplete="current-password" required></div>
    <div id="auth-msg"></div><button class="primary" type="submit">Sign in</button></form>
    <p class="muted" style="margin-top:1rem"><a href="#/forgot">Forgot password?</a> · New here? <a href="#/signup">Create an account</a></p></div>`;
}

function viewForgot() {
  app.innerHTML = `<div class="auth card"><h1>Reset your password</h1>
    <p class="muted">Enter your account email and we'll send a link to set a new password.</p>
    <form data-form="forgot"><div class="field"><label>Email</label><input name="email" type="email" required autocomplete="email"></div>
    <div id="auth-msg"></div><button class="primary" type="submit">Send reset link</button></form>
    <p class="muted" style="margin-top:1rem"><a href="#/login">Back to sign in</a></p></div>`;
}

function viewReset(params) {
  app.innerHTML = `<div class="auth card"><h1>Choose a new password</h1>
    <form data-form="reset"><input type="hidden" name="token" value="${esc(params.get('token') || '')}">
    <div class="field"><label>New password <span class="hint">(8+ characters)</span></label><input name="password" type="password" minlength="8" required autocomplete="new-password"></div>
    <div id="auth-msg"></div><button class="primary" type="submit">Set password</button></form></div>`;
}

function viewAccount() {
  app.innerHTML = `<div class="auth card"><h1>Your account</h1>
    <p>${esc(store.user.name)} · ${esc(store.user.email)}${store.user.firm_name ? ' · ' + esc(store.user.firm_name) : ''}</p>
    <h3 style="margin-top:1rem">Change password</h3>
    <form data-form="change-password">
      <div class="field"><label>Current password</label><input name="current_password" type="password" required autocomplete="current-password"></div>
      <div class="field"><label>New password <span class="hint">(8+ characters)</span></label><input name="new_password" type="password" minlength="8" required autocomplete="new-password"></div>
      <div id="auth-msg"></div><button class="primary" type="submit">Change password</button>
    </form><p class="muted" style="margin-top:.8rem">Changing your password signs you out on every device.</p></div>`;
}

function viewSignup(params) {
  const role = params.get('role') === 'ca' ? 'ca' : 'msme';
  app.innerHTML = `<div class="auth card"><h1>Create your account</h1>
    <form data-form="signup">
      <input type="hidden" name="role" value="${role}">
      <div class="role-toggle">
        <button type="button" data-action="pick-role" data-role="msme" class="${role === 'msme' ? 'selected' : ''}"><strong>Business owner</strong><small>Run my own factory</small></button>
        <button type="button" data-action="pick-role" data-role="ca" class="${role === 'ca' ? 'selected' : ''}"><strong>Chartered Accountant</strong><small>Manage client businesses</small></button>
      </div>
      <div class="field"><label>Your name</label><input name="name" required autocomplete="name"></div>
      <div class="field" id="firm-field" ${role === 'ca' ? '' : 'hidden'}><label>Firm name <span class="hint">(shown to your clients)</span></label><input name="firm_name"></div>
      <div class="field"><label>Email</label><input name="email" type="email" required autocomplete="email"></div>
      <div class="field"><label>Password <span class="hint">(8+ characters)</span></label><input name="password" type="password" minlength="8" required autocomplete="new-password"></div>
      <div id="auth-msg"></div><button class="primary" type="submit">Create account</button>
    </form><p class="muted" style="margin-top:1rem">Already registered? <a href="#/login">Sign in</a></p></div>`;
}

async function submitAuth(form, kind) {
  const d = formData(form);
  const btn = form.querySelector('button[type=submit]');
  busy(btn, true);
  try {
    const body = kind === 'login' ? { email: d.email, password: d.password }
      : { email: d.email, password: d.password, name: d.name, role: d.role, firm_name: d.role === 'ca' ? d.firm_name || null : null };
    const r = await api(`/api/auth/${kind}`, { method: 'POST', body });
    setSession(r.token, r.user);
    renderNav();
    go(r.user.role === 'ca' ? '#/console' : '#/home');
  } catch (err) { showError(err, document.getElementById('auth-msg')); busy(btn, false); }
}

// ---------------------------------------------------------------- home / console

async function viewHome() {
  const orgs = await api('/api/orgs');
  if (store.user.role === 'ca') return go('#/console');
  if (orgs.length === 0) return go('#/onboard');
  if (orgs.length === 1) return go(`#/org/${orgs[0].id}`);
  app.innerHTML = `<div class="row between"><h1>Your businesses</h1><a class="btn primary" href="#/onboard">Add business</a></div>
    <div class="grid two">${orgs.map((o) => `<a class="card" style="text-decoration:none;color:inherit" href="#/org/${o.id}">
      <h3>${esc(o.name)}</h3><div class="muted mono">${esc(o.gstin)}</div><div class="muted">${esc(o.district || '')} ${esc(o.state)}</div></a>`).join('')}</div>`;
}

async function viewConsole() {
  const c = await api('/api/console');
  const t = c.totals;
  app.innerHTML = `
    <div class="row between"><div><h1>${esc(c.firm_name || 'Client console')}</h1>
      <p class="muted">Every client's credit exposure, subsidy potential and filing deadline in one place.</p></div>
      <a class="btn primary" href="#/onboard">Add client</a></div>
    <div class="grid kpis">
      <div class="card kpi"><div class="label">Clients</div><div class="value">${t.clients}</div><div class="sub">${t.awaiting_data} awaiting data</div></div>
      <div class="card kpi red"><div class="label">ITC at risk</div><div class="value">${inrShort(t.itc_at_risk)}</div><div class="sub">across all clients</div></div>
      <div class="card kpi gold"><div class="label">Subsidy potential</div><div class="value">${inrShort(t.subsidy_potential)}</div><div class="sub">advisory opportunity</div></div>
    </div>
    <div class="card">${c.clients.length ? `<div class="table-wrap"><table>
      <thead><tr><th>Client</th><th>GSTIN</th><th class="num">ITC at risk</th><th class="num">Match rate</th><th class="num">Subsidy potential</th><th>GSTR-3B</th><th></th></tr></thead>
      <tbody>${c.clients.map((x) => `<tr class="clickable" data-action="open" data-href="#/org/${x.org.id}">
        <td><strong>${esc(x.org.name)}</strong><br><small>${esc(x.org.district || '')} ${esc(x.org.state)}${x.org.category ? ' · ' + esc(x.org.category) : ''}</small></td>
        <td class="mono">${esc(x.org.gstin)}</td>
        <td class="num">${x.has_data ? `<strong style="color:var(--red)">${inr(x.tax.itc_at_risk)}</strong>` : '—'}</td>
        <td class="num">${x.has_data ? x.tax.match_rate + '%' : '—'}</td>
        <td class="num">${x.subsidy.total_estimated_benefit ? inrShort(x.subsidy.total_estimated_benefit) : '—'}</td>
        <td>${dueBadge(x.filing)}</td>
        <td>${x.has_data ? '' : badge('needs_info', 'Awaiting data')}</td></tr>`).join('')}</tbody></table></div>`
      : `<div class="empty"><h3>No clients yet</h3><p>Add a client, or ask an existing AureusRecon user to invite you by email.</p><a class="btn primary" href="#/onboard">Add your first client</a></div>`}
    </div>`;
}

function dueBadge(f) {
  const cls = f.days_left <= 3 ? 'bad' : f.days_left <= 7 ? 'warn' : 'info';
  return `<span class="badge ${cls}">${f.days_left >= 0 ? `${f.days_left} days` : 'Overdue'}</span> <small>${esc(f.period_label)}</small>`;
}

// ---------------------------------------------------------------- onboarding

function stepper(active) {
  const steps = ['Business profile', 'Consent', 'Connect data'];
  return `<div class="steps">${steps.map((s, i) => `<div class="step ${i === active ? 'active' : i < active ? 'done' : ''}">${i < active ? '✓ ' : `${i + 1}. `}${esc(s)}</div>`).join('')}</div>`;
}

function zoneInfo(m, stateCode) { return (m?.zones_by_state || {})[stateCode]; }

function zoneSelect(m, current, stateCode) {
  const info = zoneInfo(m, stateCode);
  if (!info) return '<select name="zone" disabled><option value="">No zones for this state</option></select>';
  return `<select name="zone"><option value="">Not sure</option>${info.zones.map((z) =>
    `<option value="${esc(z.id)}" ${z.id === current ? 'selected' : ''}>${esc(z.id === z.label ? z.id : `${z.id}: ${z.label}`)}</option>`).join('')}</select>`;
}

function zoneHint(m, stateCode) {
  const info = zoneInfo(m, stateCode);
  if (!info) return '';
  return `<p class="muted" style="font-size:.85rem" data-zone-hint>${esc(info.lookup || 'Incentives depend on the area category of your unit.')}
    ${info.source_url ? `<a href="${esc(info.source_url)}" target="_blank" rel="noopener noreferrer">Official list</a>` : ''}</p>
    ${info.places ? placeFinder(info, stateCode) : ''}`;
}

// District + taluka/block pickers that fill in the zone for states whose list we hold.
function placeFinder(info, stateCode) {
  const kind = info.places_kind || 'area';
  return `<div class="fields" data-place-finder="${esc(stateCode)}">
    <div class="field"><label>District</label><select data-action-change="zone-district"><option value="">Choose district</option>
      ${Object.keys(info.places).sort().map((d) => `<option>${esc(d)}</option>`).join('')}</select></div>
    <div class="field"><label>${esc(kind[0].toUpperCase() + kind.slice(1))}</label><select data-action-change="zone-place" disabled>
      <option value="">Choose ${esc(kind)}</option></select></div>
  </div>`;
}

function onPlaceChange(select) {
  const box = select.closest('[data-place-finder]');
  const info = zoneInfo(store.meta, box.dataset.placeFinder);
  const form = select.closest('form');
  const district = box.querySelector('[data-action-change=zone-district]').value;
  const placeSel = box.querySelector('[data-action-change=zone-place]');
  if (select.dataset.actionChange === 'zone-district') {
    const places = Object.keys(info.places[district] || {}).sort();
    placeSel.innerHTML = `<option value="">Choose ${esc(info.places_kind || 'area')}</option>`
      + places.map((p) => `<option>${esc(p)}</option>`).join('');
    placeSel.disabled = !places.length;
    return;
  }
  const zone = (info.places[district] || {})[placeSel.value];
  const zoneSel = form && form.querySelector('select[name=zone]');
  if (zone && zoneSel) zoneSel.value = zone;
}

function profileFields(o = {}) {
  const v = (k) => esc(o[k] ?? '');
  return `<div class="fields">
    <div class="field"><label>Business name</label><input name="name" required value="${v('name')}"></div>
    ${o.id ? `<div class="field"><label>GSTIN</label><input value="${v('gstin')}" disabled></div>`
      : `<div class="field"><label>GSTIN</label><input name="gstin" required maxlength="15" data-action-input="gstin" style="text-transform:uppercase" placeholder="27AAAAA0000A1Z5"><div id="gstin-msg"></div></div>`}
    <div class="field"><label>District</label><input name="district" value="${v('district')}"></div>
    <div class="field"><label>Industry <span class="hint">(what you make)</span></label><input name="industry" value="${v('industry')}" placeholder="Auto components"></div>
    <div class="field"><label>Udyam registration no.</label><input name="udyam" value="${v('udyam')}" placeholder="UDYAM-MH-00-0000000"></div>
    <div class="field"><label>GSTR-3B filing</label><select name="filing_frequency">
      <option value="monthly" ${o.filing_frequency !== 'quarterly' ? 'selected' : ''}>Monthly</option>
      <option value="quarterly" ${o.filing_frequency === 'quarterly' ? 'selected' : ''}>Quarterly (QRMP)</option></select></div>
  </div>
  <div class="field"><label>Unit address</label><input name="address" value="${v('address')}"></div>
  <h3 style="margin-top:1rem">For subsidy matching</h3>
  <div class="fields">
    <div class="field"><label>Annual turnover <span class="hint">(₹)</span></label><input name="annual_turnover" inputmode="numeric" value="${v('annual_turnover')}"></div>
    <div class="field"><label>Investment in plant &amp; machinery <span class="hint">(₹, total)</span></label><input name="investment_pm" inputmode="numeric" value="${v('investment_pm')}"></div>
    <div class="field"><label>Term loan for machinery <span class="hint">(₹)</span></label><input name="term_loan_amount" inputmode="numeric" value="${v('term_loan_amount')}"></div>
    <div class="field"><label>Area / zone category</label><div data-zone-field>${zoneSelect(store.meta, o.zone, o.state_code || '')}</div></div>
    <div class="field"><label>Monthly electricity duty <span class="hint">(₹, on your bill)</span></label><input name="monthly_electricity_duty" inputmode="numeric" value="${v('monthly_electricity_duty')}"></div>
    <div class="field"><label>Monthly power use <span class="hint">(units / kWh)</span></label><input name="monthly_power_units" inputmode="numeric" value="${v('monthly_power_units')}"></div>
  </div>
  <div data-zone-hint-box>${zoneHint(store.meta, o.state_code || '')}</div>`;
}

function refreshZoneField(form, stateCode) {
  const field = form.querySelector('[data-zone-field]');
  const hint = form.querySelector('[data-zone-hint-box]');
  if (field) field.innerHTML = zoneSelect(store.meta, '', stateCode);
  if (hint) hint.innerHTML = zoneHint(store.meta, stateCode);
  const result = document.getElementById('calc-result');
  if (result && form.dataset.form === 'calculator') result.innerHTML = '';
}

function profilePayload(d) {
  const out = {};
  for (const k of ['name', 'gstin', 'district', 'address', 'industry', 'udyam', 'filing_frequency', 'zone']) if (k in d) out[k] = d[k] || null;
  for (const k of ['annual_turnover', 'investment_pm', 'term_loan_amount', 'monthly_electricity_duty', 'monthly_power_units']) if (k in d) out[k] = numOrNull(d[k]);
  if (out.gstin) out.gstin = out.gstin.toUpperCase();
  return out;
}

async function viewOnboardProfile() {
  await meta();
  app.innerHTML = `<div style="max-width:860px;margin:0 auto">${stepper(0)}<div class="card">
    <h1>${store.user.role === 'ca' ? 'Add a client business' : 'Tell us about your business'}</h1>
    <p class="muted">We read your state from the GSTIN. Turnover and investment decide your MSME category for subsidy rules.</p>
    <form data-form="create-org">${profileFields()}<div id="form-msg"></div><button class="primary" type="submit">Continue</button></form></div></div>`;
}

let gstinTimer;
function onGstinInput(input) {
  clearTimeout(gstinTimer);
  const msg = document.getElementById('gstin-msg');
  const value = input.value.trim().toUpperCase();
  if (value.length < 15) { msg.innerHTML = ''; return; }
  gstinTimer = setTimeout(async () => {
    const r = await api(`/api/public/gstin/${encodeURIComponent(value)}`);
    msg.innerHTML = r.valid ? `<span class="ok">✓ Valid · ${esc(r.state)} · PAN ${esc(r.pan)}</span>` : '<span class="err">This GSTIN fails the format or checksum test</span>';
    if (r.valid) refreshZoneField(input.closest('form'), r.state_code);
  }, 250);
}

async function submitCreateOrg(form) {
  const btn = form.querySelector('button[type=submit]');
  busy(btn, true);
  try {
    const org = await api('/api/orgs', { method: 'POST', body: profilePayload(formData(form)) });
    go(`#/onboard/${org.id}/consent`);
  } catch (err) { showError(err, document.getElementById('form-msg')); busy(btn, false); }
}

async function viewOnboardConsent(orgId) {
  const [m, org] = await Promise.all([meta(), api(`/api/orgs/${orgId}`)]);
  app.innerHTML = `<div style="max-width:860px;margin:0 auto">${stepper(1)}<div class="card">
    <h1>Your consent, your control</h1>
    <p>AureusRecon needs your permission to read two kinds of data for <strong>${esc(org.name)}</strong>. You can revoke either at any
    time, and everything collected under it is deleted immediately.</p>
    <form data-form="consent" data-org="${orgId}">
      ${m.purposes.map((p) => `<div class="card flat"><label class="check"><input type="checkbox" name="${esc(p.id)}" checked>
        <span><strong>${esc(p.label)}</strong><br><small>Data: ${esc(p.data.join(', '))}. Valid for 12 months.</small></span></label></div>`).join('')}
      <div class="banner info">🔒 We never ask for your GST portal, bank or accounting software passwords. Records are encrypted with AES-256 and only
      decrypted in memory while a calculation runs.</div>
      <div id="form-msg"></div><button class="primary" type="submit">I agree, continue</button>
    </form></div></div>`;
}

async function submitConsent(form) {
  const orgId = form.dataset.org;
  const chosen = [...form.querySelectorAll('input[type=checkbox]:checked')].map((i) => i.name);
  if (!chosen.length) { document.getElementById('form-msg').innerHTML = '<div class="banner warn">Select at least one permission to continue.</div>'; return; }
  try {
    for (const purpose of chosen) await api(`/api/orgs/${orgId}/consent`, { method: 'POST', body: { purpose } });
    go(`#/onboard/${orgId}/connect`);
  } catch (err) { showError(err, document.getElementById('form-msg')); }
}

function importPanel(org) {
  const zoho = org.zoho || {};
  return `
    <h3>1. Purchase register</h3>
    <div class="source-grid">
      <div class="source"><strong>Tally Prime</strong><p class="muted">Upload a Day Book XML export, or install the connector to sync automatically.</p>
        <input type="file" accept=".xml" data-upload="books" data-source="tally" data-org="${org.id}"></div>
      <div class="source"><strong>Zoho Books</strong>
        ${zoho.configured ? `<p class="muted">${zoho.connected ? 'Connected. Pull bills for a date range.' : 'Connect in one click with read-only access.'}</p>
          ${zoho.connected ? `<button class="small" data-action="zoho-sync" data-org="${org.id}">Sync last 3 months</button>` : `<button class="small primary" data-action="zoho-connect" data-org="${org.id}">Connect Zoho Books</button>`}`
          : '<p class="muted">Upload the bills JSON export.</p>'}
        <input type="file" accept=".json" data-upload="books" data-source="zoho" data-org="${org.id}" style="margin-top:.5rem"></div>
      <div class="source"><strong>Busy / Excel</strong><p class="muted">Upload a purchase register CSV (one row per item).</p>
        <input type="file" accept=".csv" data-upload="books" data-source="busy" data-org="${org.id}"></div>
      <div class="source"><strong>Any other software</strong><p class="muted">Fill the GSTN purchase register template (from the portal's Matching Offline Tool) and upload the .xlsx or .csv.</p>
        <input type="file" accept=".xlsx,.csv" data-upload="books" data-source="gst_template" data-org="${org.id}"></div>
    </div>
    <h3 style="margin-top:1.2rem">2. From the GST portal</h3>
    <div class="source-grid">
      <div class="source"><strong>GSTR-2B</strong><p class="muted">GST portal → Returns → GSTR-2B → Download JSON. We never ask for your portal login.</p>
        <input type="file" accept=".json" data-upload="gstr2b" data-org="${org.id}"></div>
      <div class="source"><strong>IMS (optional)</strong><p class="muted">GST portal → Invoice Management System → Download JSON. Explains invoices you held back and lets AureusRecon suggest IMS actions.</p>
        <input type="file" accept=".json" data-upload="ims" data-org="${org.id}"></div>
    </div>
    <div id="import-msg" style="margin-top:1rem"></div>`;
}

async function viewOnboardConnect(orgId) {
  const org = await api(`/api/orgs/${orgId}`);
  app.innerHTML = `<div style="max-width:960px;margin:0 auto">${stepper(2)}<div class="card">
    <div class="row between"><h1>Connect your data</h1>
      <button class="gold" data-action="load-sample" data-org="${orgId}">Try with demo data</button></div>
    <p class="muted">Bring in purchases from your accounting software and the matching GSTR-2B. You can add more months later.</p>
    ${importPanel(org)}
    <div class="row" style="margin-top:1rem"><a class="btn primary" href="#/org/${orgId}">Run my reconciliation →</a>
      <span class="muted">Sample files: <a href="/api/public/sample/tally.xml">Tally XML</a> · <a href="/api/public/sample/gstr2b.json">GSTR-2B JSON</a> · <a href="/api/public/sample/ims.json">IMS JSON</a></span></div>
  </div></div>`;
}

async function uploadFile(input) {
  const file = input.files[0];
  if (!file) return;
  const msg = document.getElementById('import-msg');
  const fd = new FormData();
  fd.append('file', file);
  if (input.dataset.source) fd.append('source', input.dataset.source);
  const kind = input.dataset.upload;
  msg.innerHTML = '<div class="banner info">Reading file…</div>';
  try {
    const r = await api(`/api/orgs/${input.dataset.org}/import/${kind}`, { method: 'POST', form: fd });
    msg.innerHTML = `<div class="banner ok">✓ Imported ${r.records} ${({ books: 'purchase invoices', gstr2b: 'GSTR-2B records', ims: 'IMS documents' })[kind]} for ${esc(r.periods.map(periodLabel).join(', '))}.</div>`;
    store.cache = {};
  } catch (err) {
    showError(err, msg);
    if (err.code === 'consent_required') msg.innerHTML += `<p><a href="#/org/${input.dataset.org}/data">Review consent →</a></p>`;
  }
  input.value = '';
}

// ---------------------------------------------------------------- organisation workspace

const TABS = [['', 'Overview'], ['recon', 'Tax reconciliation'], ['ims', 'IMS actions'], ['subsidies', 'Subsidies'], ['notices', 'Notice copilot'], ['ai', 'Ask AI'], ['data', 'Data & consent'], ['settings', 'Profile']];

async function orgShell(orgId, tab) {
  const org = await api(`/api/orgs/${orgId}`);
  store.org = org;
  const advisor = org.advisor && store.user.role !== 'ca' ? `<div class="whitelabel">Managed with ${esc(org.advisor.firm_name || org.advisor.name)} · powered by AureusRecon</div>` : '';
  app.innerHTML = `${advisor}
    <div class="row between" style="margin-top:.5rem"><div>
      ${store.user.role === 'ca' ? '<a href="#/console" class="muted">← All clients</a>' : ''}
      <h1 style="margin-top:.3rem">${esc(org.name)}</h1>
      <div class="muted"><span class="mono">${esc(org.gstin)}</span> · ${esc(org.district ? org.district + ', ' : '')}${esc(org.state)}
        ${org.category ? ` · ${badge('info', org.category + ' enterprise')}` : ''}</div></div></div>
    <nav class="tabs">${TABS.map(([k, label]) => `<a href="#/org/${orgId}${k ? '/' + k : ''}" class="${k === tab ? 'active' : ''}">${label}</a>`).join('')}</nav>
    <div id="tab"><div class="empty">Loading…</div></div>`;
  return { org, el: document.getElementById('tab') };
}

async function getRecon(orgId, period) {
  const key = `recon:${orgId}:${period || ''}`;
  if (!store.cache[key]) store.cache[key] = await api(`/api/orgs/${orgId}/reconciliation${period ? `?period=${period}` : ''}`);
  return store.cache[key];
}
async function getSubsidies(orgId) {
  const key = `subsidy:${orgId}`;
  if (!store.cache[key]) store.cache[key] = await api(`/api/orgs/${orgId}/subsidies`);
  return store.cache[key];
}

function noDataState(orgId, recon) {
  if (recon.has_books && recon.has_portal) return '';
  const missing = [!recon.has_books && 'purchase register', !recon.has_portal && 'GSTR-2B'].filter(Boolean).join(' and ');
  return `<div class="card empty"><h3>Add your ${missing} to start</h3><p>Reconciliation needs both sides: what you booked and what suppliers uploaded.</p>
    <a class="btn primary" href="#/org/${orgId}/data">Connect data</a></div>`;
}

async function viewOrgOverview(orgId) {
  const { el } = await orgShell(orgId, '');
  const [recon, subs] = await Promise.all([getRecon(orgId), getSubsidies(orgId)]);
  const s = recon.summary, f = recon.filing;
  const empty = noDataState(orgId, recon);
  el.innerHTML = `
    <div class="banner ${f.days_left <= 3 ? 'danger' : f.days_left <= 7 ? 'warn' : 'info'}">
      GSTR-3B for <strong>${esc(f.period_label)}</strong> is due on <strong>${fmtDate(f.gstr3b_due_on)}</strong>
      (${f.days_left >= 0 ? `${f.days_left} days left` : 'overdue'}). Fix mismatches before you file to keep your credit.</div>
    ${empty || `<div class="grid kpis">
      <div class="card kpi red"><div class="label">ITC at risk</div><div class="value">${inrShort(s.itc_at_risk)}</div><div class="sub">${s.by_status.MISSING_IN_PORTAL.count + s.by_status.MISMATCH_WARN.count} invoices need action</div></div>
      <div class="card kpi green"><div class="label">ITC ready to claim</div><div class="value">${inrShort(s.itc_claimable)}</div><div class="sub">${s.match_rate}% of invoices matched</div></div>
      <div class="card kpi gold"><div class="label">Unclaimed in GSTR-2B</div><div class="value">${inrShort(s.itc_unclaimed_in_portal)}</div><div class="sub">${s.portal_only} bills not in your books</div></div>
      <div class="card kpi gold"><div class="label">Subsidy potential</div><div class="value">${inrShort(subs.total_estimated_benefit)}</div><div class="sub">${subs.schemes.filter((x) => x.status === 'eligible').length} schemes matched</div></div>
    </div>
    <div class="grid two">
      <div class="card"><h2>Suppliers holding up your credit</h2>
        ${s.suppliers_at_risk.length ? `<table><tbody>${s.suppliers_at_risk.slice(0, 6).map((x) => `<tr>
          <td><strong>${esc(x.supplier_name)}</strong><br><small class="mono">${esc(x.supplier_gstin)}</small></td>
          <td class="num">${inr(x.itc_at_risk)}<br><small>${x.invoices} invoice(s)</small></td>
          <td><button class="small" data-action="reminder" data-org="${orgId}" data-gstin="${esc(x.supplier_gstin)}">Send reminder</button></td></tr>`).join('')}</tbody></table>`
          : '<p class="muted">Every supplier is clean. Nice.</p>'}
        <a href="#/org/${orgId}/recon">See every invoice →</a></div>
      <div class="card"><h2>Subsidy opportunities</h2>
        ${subs.schemes.length ? subs.schemes.slice(0, 4).map((x) => `<div class="row between" style="padding:.5rem 0;border-bottom:1px solid var(--line)">
          <div style="flex:1"><strong>${esc(x.name)}</strong><br><small>${esc(x.basis || x.missing_inputs.join(', '))}</small></div>
          <div class="num"><strong>${x.estimated_benefit ? inrShort(x.estimated_benefit) : '—'}</strong><br>${badge(x.status, x.status === 'eligible' ? 'Eligible' : 'Needs info')}</div></div>`).join('')
          : '<p class="muted">No matching schemes yet. Capital purchases with machinery HSN codes unlock these.</p>'}
        <p style="margin-top:.6rem"><a href="#/org/${orgId}/subsidies">Review and prepare applications →</a></p></div>
    </div>`}`;
}

let reconFilter = { status: '', q: '', open: new Set() };

async function viewOrgRecon(orgId, params) {
  const { el } = await orgShell(orgId, 'recon');
  const period = params.get('period') || '';
  const recon = await getRecon(orgId, period);
  store.recon = recon;
  const s = recon.summary;
  const empty = noDataState(orgId, recon);
  const periodOptions = [''].concat(recon.periods).map((p) => `<option value="${p}" ${p === period ? 'selected' : ''}>${periodLabel(p)}</option>`).join('');
  const chip = (st, label, count) => `<button class="chip ${reconFilter.status === st ? 'active' : ''}" data-action="filter" data-status="${st}">${label} (${count})</button>`;
  el.innerHTML = `
    <div class="row between">
      <div class="row"><label style="margin:0">Period</label><select data-action-change="period" data-org="${orgId}" style="width:auto">${periodOptions}</select></div>
      <div class="row"><button data-action="export-csv">Export CSV</button><a class="btn primary" href="#/org/${orgId}/notices">Draft notice reply</a></div>
    </div>
    ${empty || `
    <div class="card" style="margin-top:1rem">
      <div class="row between">
        <div class="chips">${chip('', 'All', s.invoices)}${chip('MISSING_IN_PORTAL', 'Missing in portal', s.by_status.MISSING_IN_PORTAL.count)}${chip('MISMATCH_WARN', 'Mismatch', s.by_status.MISMATCH_WARN.count)}${chip('MATCHED_ELIGIBLE', 'Matched', s.by_status.MATCHED_ELIGIBLE.count)}</div>
        <input type="search" placeholder="Search supplier or invoice" data-action-input="recon-search" value="${esc(reconFilter.q)}" style="max-width:260px">
      </div>
      <div class="table-wrap" id="recon-table"></div>
    </div>
    <div class="card"><h2>In GSTR-2B but not in your books</h2>
      <p class="muted">Credit of ${inr(s.itc_unclaimed_in_portal)} is waiting. Book these bills (or confirm they are not yours) to claim it.</p>
      ${recon.portal_only.length ? `<div class="table-wrap"><table><thead><tr><th>Supplier</th><th>Invoice</th><th>Date</th><th class="num">Taxable value</th><th class="num">ITC</th></tr></thead><tbody>
        ${recon.portal_only.map((p) => `<tr><td>${esc(p.portal.supplier_name)}<br><small class="mono">${esc(p.portal.supplier_gstin)}</small></td><td class="mono">${esc(p.portal.invoice_no)}</td>
          <td>${fmtDate(p.portal.invoice_date)}</td><td class="num">${inr2(p.portal.taxable_value)}</td><td class="num">${inr2(p.itc_unclaimed)}</td></tr>`).join('')}</tbody></table></div>`
        : '<p class="muted">Nothing outstanding.</p>'}
    </div>`}`;
  if (!empty) renderReconTable(recon);
}

function renderReconTable(recon = store.recon) {
  document.querySelectorAll('.chips .chip').forEach((c) => c.classList.toggle('active', c.dataset.status === reconFilter.status));
  const q = reconFilter.q.toLowerCase();
  const rows = recon.results.filter((r) => (!reconFilter.status || r.status === reconFilter.status)
    && (!q || `${r.books.supplier_name} ${r.books.supplier_gstin} ${r.books.invoice_no}`.toLowerCase().includes(q)));
  const order = { MISSING_IN_PORTAL: 0, MISMATCH_WARN: 1, MATCHED_ELIGIBLE: 2 };
  rows.sort((a, b) => order[a.status] - order[b.status] || b.itc_at_risk - a.itc_at_risk);
  const heads = ['taxable_value', 'igst', 'cgst', 'sgst', 'cess'];
  document.getElementById('recon-table').innerHTML = rows.length ? `<table>
    <thead><tr><th>Status</th><th>Supplier</th><th>Invoice</th><th>Date</th><th class="num">ITC in books</th><th class="num">ITC in 2B</th><th class="num">At risk</th></tr></thead>
    <tbody>${rows.map((r) => {
      const open = reconFilter.open.has(r.id);
      const detail = open ? `<tr class="detail"><td colspan="7"><div class="grid two">
        <div><strong>${esc(r.reason_text)}</strong><p style="margin-top:.4rem">${esc(r.action)}</p>
          ${r.match_basis && r.match_basis !== 'exact' ? `<p class="muted">Paired by ${esc(r.match_basis.replace(/_/g, ' '))}${r.portal ? `: portal shows <span class="mono">${esc(r.portal.invoice_no)}</span> from <span class="mono">${esc(r.portal.supplier_gstin)}</span>` : ''}</p>` : ''}
          ${r.flags.map((f) => `<div class="flag">⚠ ${esc(f.text)}</div>`).join('')}</div>
        <div>${r.portal ? `<table><thead><tr><th></th><th class="num">Books</th><th class="num">GSTR-2B</th><th class="num">Difference</th></tr></thead><tbody>
          ${heads.map((h) => `<tr><td>${h === 'taxable_value' ? 'Taxable value' : h.toUpperCase()}</td><td class="num">${inr2(r.books[h])}</td><td class="num">${inr2(r.portal[h])}</td>
          <td class="num" style="${Math.abs(r.variance[h]) > 0.99 ? 'color:var(--red);font-weight:600' : ''}">${inr2(r.variance[h])}</td></tr>`).join('')}</tbody></table>`
          : '<p class="muted">No matching record in GSTR-2B.</p>'}</div></div></td></tr>` : '';
      return `<tr class="clickable" data-action="toggle-row" data-id="${r.id}">
        <td>${badge(r.status)}<br><small>${esc(r.gstn_category)}</small></td><td>${esc(r.books.supplier_name)}<br><small class="mono">${esc(r.books.supplier_gstin)}</small></td>
        <td class="mono">${esc(r.books.invoice_no)}${r.flags.length ? ' <span class="flag" title="Has flags">⚠</span>' : ''}</td><td>${fmtDate(r.books.invoice_date)}</td>
        <td class="num">${inr2(r.itc_books)}</td><td class="num">${inr2(r.itc_portal)}</td>
        <td class="num" style="${r.itc_at_risk ? 'color:var(--red);font-weight:600' : ''}">${r.itc_at_risk ? inr2(r.itc_at_risk) : '—'}</td></tr>${detail}`;
    }).join('')}</tbody></table>` : '<div class="empty">No invoices match this filter.</div>';
}

function exportCsv() {
  const recon = store.recon;
  if (!recon) return;
  const cols = ['status', 'reason', 'supplier_gstin', 'supplier_name', 'invoice_no', 'invoice_date', 'itc_books', 'itc_portal', 'itc_claimable', 'itc_at_risk', 'action'];
  const line = (vals) => vals.map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',');
  const rows = recon.results.map((r) => line([r.status, r.reason, r.books.supplier_gstin, r.books.supplier_name, r.books.invoice_no, r.books.invoice_date,
    r.itc_books, r.itc_portal, r.itc_claimable, r.itc_at_risk, r.action]));
  download(`reconciliation-${store.org.gstin}.csv`, [line(cols), ...rows].join('\n'), 'text/csv');
}

async function showReminder(orgId, gstin) {
  try {
    const r = await api(`/api/orgs/${orgId}/suppliers/${encodeURIComponent(gstin)}/reminder`);
    modal(`<h2>Reminder to ${esc(r.supplier.supplier_name)}</h2>
      <p class="muted">${r.invoices} invoice(s) · ${inr(r.itc_at_risk)} of your credit is held up.</p>
      <label>Subject</label><input value="${esc(r.subject)}" readonly>
      <label style="margin-top:.8rem">Email</label><pre id="reminder-body">${esc(r.body)}</pre>
      <label>WhatsApp / SMS</label><pre id="reminder-short">${esc(r.short)}</pre>
      <div class="row"><button class="primary" data-action="copy" data-target="reminder-body">Copy email</button>
      <button data-action="copy" data-target="reminder-short">Copy short message</button><span class="spacer"></span><button data-action="close-modal">Close</button></div>`);
  } catch (err) { showError(err); }
}

async function viewOrgSubsidies(orgId) {
  await meta();
  const { el } = await orgShell(orgId, 'subsidies');
  const subs = await getSubsidies(orgId);
  const classes = Object.fromEntries((await meta()).asset_classes.map((a) => [a.id, a.label]));
  el.innerHTML = `
    ${(() => { const c = (store.meta?.coverage || {})[store.org.state_code];
      if (!c) return '';
      if (c.status !== 'current') return `<div class="banner danger">${esc(store.org.state)} has no current industrial incentive policy (${esc(c.status)}). Only central schemes are shown. <a href="#/coverage">See coverage</a></div>`;
      if (!c.verified_schemes) return `<div class="banner warn">${esc(store.org.state)}'s figures could not be checked against the official policy yet. <a href="#/coverage">See coverage</a></div>`;
      return ''; })()}
    <div class="banner ${subs.unverified_in_total.length ? 'warn' : 'info'}">${subs.unverified_in_total.length ? '<strong>Verify before filing.</strong> ' : ''}${esc(subs.disclaimer)}</div>
    ${subs.zone ? '' : `<div class="banner warn">Add your taluka group in <a href="#/org/${orgId}/settings">Profile</a> to see Maharashtra incentives.</div>`}
    <div class="grid kpis">
      <div class="card kpi gold"><div class="label">Estimated incentives</div><div class="value">${inrShort(subs.total_estimated_benefit)}</div><div class="sub">across eligible schemes</div></div>
      ${Object.entries(subs.capital_spend_by_class).map(([k, v]) => `<div class="card kpi"><div class="label">${esc(classes[k] || k)}</div><div class="value">${inrShort(v)}</div><div class="sub">capital spend detected</div></div>`).join('')}
    </div>
    ${subs.schemes.map((x) => `<div class="card scheme ${x.status}">
      <div class="row between"><div style="flex:1;min-width:260px"><h3>${esc(x.name)}</h3><small>${esc(x.authority)} · ${esc(x.level)}</small></div>
        <div class="num"><div class="amount">${x.estimated_benefit ? inrShort(x.estimated_benefit) : '—'}</div>${badge(x.status, x.status === 'eligible' ? 'Eligible' : 'Needs info')}</div></div>
      <p style="margin-top:.4rem">${x.verified ? `${badge('ok', 'Verified ' + fmtDate(x.verified_on))} <small>${esc(x.source_document)}${x.clause ? ', ' + esc(x.clause) : ''}</small>` : badge('warn', 'Unverified figures')}</p>
      ${x.conditions.length ? `<div class="banner warn" style="margin:.5rem 0">Only if: ${esc(x.conditions.join(' · '))}</div>` : ''}
      ${x.in_total ? '' : `<p class="muted">Not added to the total${x.conditions.length ? ' until you confirm the conditions' : ' (see note)'}.</p>`}
      ${x.basis ? `<p style="margin-top:.6rem">${esc(x.basis)}${x.capped_at ? ` · capped at ${inrShort(x.capped_at)}` : ''}${x.disbursal ? ` · ${esc(x.disbursal)}` : ''}</p>` : ''}
      ${x.assumptions.map((a) => `<p class="muted">Assumption: ${esc(a)}</p>`).join('')}
      ${x.missing_inputs.length ? `<p class="flag">Needs: ${esc(x.missing_inputs.join('; '))} · <a href="#/org/${orgId}/settings">update profile</a></p>` : ''}
      ${x.qualifying_assets.length ? `<details><summary>${x.qualifying_assets.length} qualifying purchase(s) · ${inr(x.eligible_cost)}</summary><table style="margin-top:.5rem"><tbody>
        ${x.qualifying_assets.map((a) => `<tr><td>${esc(a.description)}<br><small>HSN ${esc(a.hsn)} · ${esc(classes[a.asset_class] || a.asset_class)}${a.hsn_description ? `<br>${esc(a.hsn_description)}` : ''}</small></td>
          <td>${esc(a.supplier_name)}<br><small class="mono">${esc(a.invoice_no)} · ${fmtDate(a.invoice_date)}</small></td><td class="num">${inr(a.value)}</td></tr>`).join('')}</tbody></table></details>` : ''}
      ${x.notes ? `<p class="muted" style="margin-top:.5rem">${esc(x.notes)}</p>` : ''}
      <div class="row" style="margin-top:.6rem"><button class="primary small" data-action="packet" data-org="${orgId}" data-scheme="${esc(x.scheme_id)}">Prepare application</button>
        <a class="small" href="${esc(x.source_url)}" target="_blank" rel="noopener noreferrer">Official source ↗</a></div>
    </div>`).join('') || '<div class="card empty"><h3>No matching schemes yet</h3><p>Import purchases that include capital equipment (HSN chapters 84, 85, 90) and complete your profile.</p></div>'}
    ${subs.capital_assets.length ? '' : '<p class="muted">No capital purchases detected in your purchase register yet.</p>'}`;
}

async function showPacket(orgId, schemeId) {
  try {
    const p = await api(`/api/orgs/${orgId}/subsidies/${encodeURIComponent(schemeId)}/application`);
    modal(`<h2>${esc(p.title)}</h2><p class="muted">${esc(p.scheme.name)}</p>
      ${p.ready_to_file ? '<div class="banner ok">All fields are filled from your profile and verified invoices.</div>'
        : `<div class="banner warn">Still needed: ${esc(p.missing_fields.join(', '))}. <a href="#/org/${orgId}/settings">Complete profile</a></div>`}
      ${p.sections.map((s) => `<h3 style="margin-top:1rem">${esc(s.title)}</h3><table><tbody>${s.fields.map((f) => `<tr><th style="width:40%">${esc(f.label)}</th>
        <td>${f.missing ? '<span class="flag">To be filled</span>' : typeof f.value === 'number' ? inr2(f.value) : esc(f.value)}<br><small>${esc(f.source)}</small></td></tr>`).join('')}</tbody></table>`).join('')}
      <h3 style="margin-top:1rem">Annexure: ${p.annexure.length} invoice(s)</h3>
      <h3 style="margin-top:1rem">Enclosures</h3><ul class="tight">${p.documents.map((d) => `<li>${esc(d.name)}${d.auto_attached ? ' <span class="badge ok">auto</span>' : ''}</li>`).join('')}</ul>
      <div class="row" style="margin-top:1rem"><button class="primary" data-action="packet-download" data-org="${orgId}" data-scheme="${esc(schemeId)}">Download printable form</button>
        <span class="spacer"></span><button data-action="close-modal">Close</button></div>`);
  } catch (err) { showError(err); }
}

async function downloadPacket(orgId, schemeId) {
  try {
    const res = await api(`/api/orgs/${orgId}/subsidies/${encodeURIComponent(schemeId)}/application.html`, { raw: true });
    download(`${schemeId}-application.html`, await res.blob());
    toast('Saved. Open it in your browser and print to PDF.');
  } catch (err) { showError(err); }
}

async function viewOrgNotices(orgId) {
  const { el } = await orgShell(orgId, 'notices');
  const [recon, m] = await Promise.all([getRecon(orgId), meta()]);
  const rows = recon.results.filter((r) => r.status !== 'MATCHED_ELIGIBLE');
  el.innerHTML = noDataState(orgId, recon) || `${noticeReaderCard(orgId, m.ai)}<div class="grid two">
    <div class="card"><h2>Draft a reply</h2>
      <p class="muted">Pick the notice you received (or expect). The draft cites only provisions and rulings from a vetted list.</p>
      <form data-form="notice" data-org="${orgId}">
        <div class="field"><label>Notice type</label><select name="notice_type">${m.notice_types.map((t) => `<option value="${t.id}">${esc(t.title)}</option>`).join('')}</select></div>
        <div class="fields"><div class="field"><label>Reference no.</label><input name="notice_ref" maxlength="80"></div>
          <div class="field"><label>Notice date</label><input name="notice_date" type="date"></div></div>
        <div class="field"><label>Invoices to address <span class="hint">(${rows.length} with issues)</span></label>
          <div style="max-height:240px;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:.5rem">
          ${rows.map((r) => `<label class="check"><input type="checkbox" name="row" value="${r.id}" checked>
            <span>${badge(r.status)} <span class="mono">${esc(r.books.invoice_no)}</span> · ${esc(r.books.supplier_name)} · ${inr(r.itc_at_risk)}</span></label>`).join('') || '<p class="muted">No mismatches to address.</p>'}
          </div></div>
        <label class="check"><input type="checkbox" name="polish"> <span>Polish wording with AI <small>(keeps every fact and citation; needs an AI key on the server)</small></span></label>
        <div id="form-msg"></div>
        <button class="primary" type="submit" ${rows.length ? '' : 'disabled'}>Generate draft</button>
      </form></div>
    <div class="card" id="notice-out"><div class="empty">Your draft appears here.</div></div></div>`;
}

// ---------------------------------------------------------------- AI features (Gemini)

function aiOffCard() {
  return `<div class="card"><h2>AI features are switched off</h2>
    <p>Add a Gemini API key on the server (environment variable <span class="mono">GEMINI_API_KEY</span>) to turn on:
    the assistant, the notice reader, the HSN fixer and the supplier matcher. Everything else works without it.</p></div>`;
}

function freeTierBanner(info) {
  return info && info.free_tier_notice ? `<div class="banner warn">${esc(info.free_tier_notice)}</div>` : '';
}

function noticeReaderCard(orgId, info) {
  if (!info || !info.enabled) return '';
  return `<div class="card"><h2>Read a notice with AI</h2>
    <p class="muted">Upload the DRC-01C / ASMT-10 / DRC-01 you received (PDF or photo). The app reads the reference, dates,
    amounts and invoices, fills the form below and ticks the invoices the officer lists.</p>
    <form data-form="ai-notice" data-org="${orgId}" class="row" style="flex-wrap:wrap;gap:.6rem;align-items:center">
      <input type="file" name="file" accept="application/pdf,image/png,image/jpeg,image/webp" required>
      ${info.tier === 'free' ? '<label class="check"><input type="checkbox" name="ack"> <span>This is a test or anonymised notice (free AI tier)</span></label>' : ''}
      <button class="primary" type="submit">Read notice</button></form>
    <div id="ai-notice-out"></div></div>`;
}

async function submitAiNotice(form) {
  const btn = form.querySelector('button[type=submit]');
  const out = document.getElementById('ai-notice-out');
  const fd = new FormData();
  fd.append('file', form.querySelector('input[name=file]').files[0]);
  fd.append('acknowledge_free_tier', form.querySelector('input[name=ack]')?.checked ? 'true' : 'false');
  busy(btn, true, 'Reading…');
  try {
    const r = await api(`/api/orgs/${form.dataset.org}/ai/read-notice`, { method: 'POST', form: fd });
    if (!r.is_gst_notice) { out.innerHTML = `<div class="banner warn">${esc(r.warnings.join(' '))} ${esc(r.summary)}</div>`; return; }
    const nf = document.querySelector('form[data-form=notice]');
    if (nf) {
      if (r.notice_type !== 'other') nf.querySelector('[name=notice_type]').value = r.notice_type;
      nf.querySelector('[name=notice_ref]').value = r.reference_no || '';
      if (r.notice_date) nf.querySelector('[name=notice_date]').value = r.notice_date;
      if (r.result_ids.length) nf.querySelectorAll('input[name=row]').forEach((c) => { c.checked = r.result_ids.includes(c.value); });
    }
    const a = r.amounts;
    out.innerHTML = `<div class="banner info" style="margin-top:.8rem"><strong>${esc(r.form || 'Notice')}</strong>
      ${r.reference_no ? ` · ref <span class="mono">${esc(r.reference_no)}</span>` : ''}${r.notice_date ? ` · dated ${fmtDate(r.notice_date)}` : ''}
      ${r.reply_due_date ? ` · <strong>reply by ${fmtDate(r.reply_due_date)}</strong>${r.days_to_reply >= 0 ? ` (${r.days_to_reply} days left)` : ''}` : ''}
      ${r.tax_period ? ` · period ${esc(r.tax_period)}` : ''}<br>${esc(r.summary)}</div>
      ${a.total ? `<p>Amount in the notice: <strong>${inr2(a.total)}</strong> (IGST ${inr2(a.igst)}, CGST ${inr2(a.cgst)}, SGST ${inr2(a.sgst)})</p>` : ''}
      ${r.issues.length ? `<h3>Issues raised</h3><ul class="tight">${r.issues.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>` : ''}
      ${r.sections_cited.length ? `<p class="muted">Sections cited: ${esc(r.sections_cited.join(', '))}</p>` : ''}
      ${r.result_ids.length || r.invoices_not_in_books.length ? `<p>${r.result_ids.length} listed invoice(s) found in your books and ticked below.
      ${r.invoices_not_in_books.length ? `Not found in your books: <span class="mono">${esc(r.invoices_not_in_books.join(', '))}</span>` : ''}</p>`
      : '<p>The notice does not list invoices, so every invoice with an issue stays ticked below.</p>'}
      ${r.warnings.map((w) => `<div class="banner warn">${esc(w)}</div>`).join('')}
      <p class="muted"><small>Read by AI: check each detail against the notice before you reply.</small></p>`;
  } catch (err) { showError(err, out); }
  busy(btn, false);
}

const AI_EXAMPLES = ['Why is my credit at risk this month?', 'Which supplier should I chase first?',
  'Kaunsi subsidy mil sakti hai aur kya chahiye?', 'What do I need to fix before the 3B due date?'];

async function viewOrgAi(orgId) {
  const { el } = await orgShell(orgId, 'ai');
  const m = await meta();
  if (!m.ai || !m.ai.enabled) { el.innerHTML = aiOffCard(); return; }
  const log = (store.aiHistory ||= {})[orgId] || [];
  el.innerHTML = `${freeTierBanner(m.ai)}
    <div class="card"><h2>Ask about this business</h2>
      <p class="muted">Answers come only from this business's reconciliation and subsidy results. Ask in English or Hindi.</p>
      <div id="ai-log">${log.map(chatTurn).join('')}</div>
      <form data-form="ai-ask" data-org="${orgId}" class="row" style="gap:.5rem;margin-top:.6rem">
        <input name="question" maxlength="500" required placeholder="e.g. Why is my credit at risk this month?" style="flex:1">
        <button class="primary" type="submit">Ask</button></form>
      <div class="row" style="flex-wrap:wrap;gap:.4rem;margin-top:.5rem">${AI_EXAMPLES.map((q) =>
        `<button class="small" data-action="ai-example" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>
      <div id="ai-ask-msg"></div></div>
    <div class="grid two">
      <div class="card"><h2>HSN fixer</h2>
        <p class="muted">Finds bill lines with a missing or wrong HSN code and suggests the right 8-digit code from the
        official GST directory. Correct codes stop blocked-credit surprises and reveal machinery that qualifies for subsidies.</p>
        <button class="primary" data-action="ai-hsn" data-org="${orgId}">Check HSN codes</button><div id="ai-hsn-out"></div></div>
      <div class="card"><h2>Supplier matcher</h2>
        <p class="muted">Looks at bills missing from GSTR-2B and invoices in GSTR-2B you haven't booked, and spots pairs that
        are the same bill under a different name, GSTIN or invoice number.</p>
        <button class="primary" data-action="ai-match" data-org="${orgId}">Find hidden matches</button><div id="ai-match-out"></div></div>
    </div>`;
}

function chatTurn(t) {
  return `<div class="chat-q">${esc(t.q)}</div><div class="chat-a">${esc(t.a)}</div>`;
}

async function submitAiAsk(form) {
  const orgId = form.dataset.org;
  const input = form.querySelector('input[name=question]');
  const btn = form.querySelector('button[type=submit]');
  const history = (store.aiHistory ||= {})[orgId] ||= [];
  const q = input.value.trim();
  if (!q) return;
  busy(btn, true, 'Thinking…');
  try {
    const r = await api(`/api/orgs/${orgId}/ai/ask`, { method: 'POST', body: { question: q, history: history.slice(-3) } });
    history.push({ q, a: r.answer });
    document.getElementById('ai-log').insertAdjacentHTML('beforeend', chatTurn({ q, a: r.answer }));
    input.value = '';
    document.getElementById('ai-ask-msg').innerHTML = '';
  } catch (err) { showError(err, document.getElementById('ai-ask-msg')); }
  busy(btn, false);
}

async function runHsnFixer(el) {
  const out = document.getElementById('ai-hsn-out');
  busy(el, true, 'Checking…');
  try {
    const r = await api(`/api/orgs/${el.dataset.org}/ai/hsn-fixer`);
    if (!r.items.length) { out.innerHTML = '<p class="muted" style="margin-top:.6rem">Every HSN code on your bills is valid. Nothing to fix.</p>'; return; }
    out.innerHTML = `<p style="margin-top:.6rem">${r.total_problems} item(s) need a code${r.total_problems > r.items.length ? ` (top ${r.items.length} by value shown)` : ''}.</p>
      <table><thead><tr><th>Item</th><th>In books</th><th>Suggested</th></tr></thead><tbody>${r.items.map((i) => `<tr>
        <td>${esc(i.description)}<br><small class="muted">${i.invoices} line(s) · ${inr(i.value)}</small></td>
        <td class="mono">${esc(i.current_hsn || '—')}<br><small>${esc(i.status)}</small></td>
        <td>${i.suggestion ? `<span class="mono">${esc(i.suggestion.hsn)}</span> ${badge(i.suggestion.confidence === 'high' ? 'MATCHED_ELIGIBLE' : 'MISMATCH_WARN', i.suggestion.confidence)}
          <br><small>${esc(i.suggestion.description)}</small><br><small class="muted">${esc(i.suggestion.reason)}</small>
          ${i.suggestion.subsidy_note ? `<br><small><strong>${esc(i.suggestion.subsidy_note)}</strong></small>` : ''}`
          : '<small class="muted">No confident match; check with the supplier</small>'}</td></tr>`).join('')}</tbody></table>
      <p class="muted"><small>Update the codes in your Tally / Zoho item master, then re-import.</small></p>`;
  } catch (err) { showError(err, out); }
  busy(el, false);
}

async function runSupplierMatcher(el) {
  const out = document.getElementById('ai-match-out');
  busy(el, true, 'Comparing…');
  try {
    const r = await api(`/api/orgs/${el.dataset.org}/ai/supplier-matches`);
    if (!r.matches.length) { out.innerHTML = `<p class="muted" style="margin-top:.6rem">${r.checked ? `Checked ${r.checked} possible pair(s); none look like the same bill.` : 'No unmatched bills with a similar amount in GSTR-2B.'}</p>`; return; }
    out.innerHTML = r.matches.map((x) => `<div class="banner ${x.verdict === 'same' ? 'info' : 'warn'}" style="margin-top:.6rem">
      <strong>${x.verdict === 'same' ? 'Likely the same bill' : 'Possibly the same bill'}</strong> · ITC ${inr2(x.itc)}<br>
      Books: ${esc(x.books.supplier_name)} · <span class="mono">${esc(x.books.invoice_no)}</span> · ${esc(x.books.supplier_gstin)}<br>
      GSTR-2B: ${esc(x.portal.supplier_name)} · <span class="mono">${esc(x.portal.invoice_no)}</span> · ${esc(x.portal.supplier_gstin)}<br>
      <small>${esc(x.reason)}</small><br><strong>${esc(x.fix)}</strong></div>`).join('');
  } catch (err) { showError(err, out); }
  busy(el, false);
}

async function submitNotice(form) {
  const d = new FormData(form);
  const btn = form.querySelector('button[type=submit]');
  const out = document.getElementById('notice-out');
  const date = d.get('notice_date');
  busy(btn, true, d.get('polish') ? 'Drafting and polishing…' : 'Drafting…');
  try {
    const r = await api(`/api/orgs/${form.dataset.org}/notice-copilot`, { method: 'POST', body: {
      notice_type: d.get('notice_type'), notice_ref: d.get('notice_ref') || '',
      notice_date: date ? date.split('-').reverse().join('-') : '', result_ids: d.getAll('row'), polish: !!d.get('polish') } });
    out.innerHTML = `<h2>${esc(r.title)}</h2>
      <p class="muted">${r.invoice_count} invoice(s) · ${inr(r.itc_in_dispute)} in dispute · reply in ${esc(r.reply_form)}${r.polished ? ` · polished by ${esc(r.polished_by || 'AI')}` : ''}</p>
      <div class="banner warn">${esc(r.review_note)}</div>
      ${r.polish_error ? `<div class="banner info">Template text shown: ${esc(r.polish_error)}</div>` : ''}
      <textarea id="notice-text">${esc(r.text)}</textarea>
      <div class="row" style="margin:.6rem 0"><button class="primary" data-action="copy" data-target="notice-text">Copy</button>
        <button data-action="download-text" data-target="notice-text" data-name="reply-${esc(r.notice_type)}.txt">Download .txt</button></div>
      ${r.citations.length ? `<h3>Authorities relied on</h3><ul class="tight">${r.citations.map((c) => `<li><strong>${esc(c.cite)}</strong><br>${esc(c.holding)}<br><small>${esc(c.caveat)}</small></li>`).join('')}</ul>` : ''}`;
  } catch (err) { showError(err, document.getElementById('form-msg')); }
  busy(btn, false);
}

async function viewOrgData(orgId, params) {
  const { org, el } = await orgShell(orgId, 'data');
  if (params.get('zoho') === 'connected') toast('Zoho Books connected');
  const m = await meta();
  const audit = await api(`/api/orgs/${orgId}/audit`);
  el.innerHTML = `
    <div class="card"><h2>Consent</h2>
      <p class="muted">Revoking a consent deletes every record collected under it, immediately and permanently.</p>
      <table><tbody>${m.purposes.map((p) => { const c = org.consents[p.id]; return `<tr>
        <td><strong>${esc(p.label)}</strong><br><small>${esc(p.data.join(', '))}</small></td>
        <td>${c ? `${badge('ok', 'Active')}<br><small>since ${fmtDate(c.granted_at.slice(0, 10))}, until ${fmtDate(c.expires_at.slice(0, 10))}</small>` : badge('warn', 'Not granted')}</td>
        <td class="num">${c ? `<button class="small danger" data-action="revoke" data-org="${orgId}" data-purpose="${p.id}">Revoke &amp; delete</button>`
          : `<button class="small primary" data-action="grant" data-org="${orgId}" data-purpose="${p.id}">Grant</button>`}</td></tr>`; }).join('')}</tbody></table>
      <button class="danger" style="margin-top:.8rem" data-action="revoke" data-org="${orgId}" data-purpose="all">Revoke everything and purge all data</button></div>
    <div class="card"><div class="row between"><h2>Import data</h2><button class="gold small" data-action="load-sample" data-org="${orgId}">Load demo data</button></div>${importPanel(org)}</div>
    <div class="card"><h2>Imported datasets</h2>${org.datasets.length ? `<table><thead><tr><th>Type</th><th>Source</th><th>Periods</th><th class="num">Records</th><th>Imported</th><th></th></tr></thead><tbody>
      ${org.datasets.map((d) => `<tr><td>${d.kind === 'books' ? 'Purchase register' : 'GSTR-2B'}</td><td>${esc(d.source)}</td><td>${esc((d.period || '').split(',').map(periodLabel).join(', '))}</td>
        <td class="num">${d.record_count}</td><td>${esc(d.imported_at)} UTC</td><td class="num"><button class="small danger" data-action="delete-dataset" data-org="${orgId}" data-id="${d.id}">Delete</button></td></tr>`).join('')}</tbody></table>`
      : '<p class="muted">Nothing imported yet.</p>'}</div>
    <div class="grid two">
      <div class="card"><h2>Tally auto-sync</h2><p class="muted">Run the connector on the PC with Tally Prime. It reads purchase vouchers from Tally's local XML port and pushes them here. No Tally password is shared.</p>
        <button data-action="connector-token" data-org="${orgId}">Generate connector key</button><div id="connector-out"></div></div>
      <div class="card"><h2>People with access</h2><table><tbody>${org.members.map((u) => `<tr><td>${esc(u.name)}<br><small>${esc(u.email)}</small></td>
        <td>${badge('info', u.role === 'ca' ? (u.firm_name || 'CA') : 'Owner')}</td></tr>`).join('')}</tbody></table>
        <form data-form="invite" data-org="${orgId}" class="row" style="margin-top:.6rem"><input name="email" type="email" placeholder="${store.user.role === 'ca' ? 'Business owner email' : 'Your CA\'s email'}" required style="flex:1"><button type="submit">Invite</button></form>
        <div id="invite-msg"></div></div>
    </div>
    <div class="card"><h2>Activity log</h2><table><tbody>${audit.map((a) => `<tr><td>${esc(a.at)} UTC</td><td>${esc(a.action.replace(/_/g, ' '))}</td><td>${esc(a.user || 'connector')}</td>
      <td><small>${esc(a.detail ? Object.entries(a.detail).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join(' · ') : '')}</small></td></tr>`).join('')}</tbody></table></div>`;
}

const IMS_LABEL = { A: 'Accept', R: 'Reject', P: 'Pending', N: 'No action' };

async function viewOrgIms(orgId) {
  const { el } = await orgShell(orgId, 'ims');
  const r = await api(`/api/orgs/${orgId}/ims`);
  store.ims = r;
  if (!r.has_ims) {
    el.innerHTML = `<div class="card empty"><h3>Add your IMS download</h3>
      <p>The Invoice Management System decides which supplier invoices reach GSTR-2B. Upload its JSON and AureusRecon will suggest
      what to accept, keep pending or review, based on your books.</p><a class="btn primary" href="#/org/${orgId}/data">Upload IMS file</a></div>`;
    return;
  }
  const opt = (row) => `<select data-ims-override="${row.id}" style="width:auto">${['A', 'P', 'R', 'N'].map((a) =>
    `<option value="${a}" ${a === row.suggested ? 'selected' : ''}>${IMS_LABEL[a]}</option>`).join('')}</select>`;
  const changed = r.rows.filter((x) => x.changes).sort((a, b) => (a.priority !== 'high') - (b.priority !== 'high'));
  const moving = changed.filter((x) => x.priority === 'high').length;
  el.innerHTML = `
    <div class="banner info">Suggestions follow your reconciliation: accept what matches your books, keep doubtful documents Pending,
      and never reject automatically. <strong>Review before uploading.</strong> Upload the file on the GST portal's IMS dashboard,
      the same way as a file from the GSTN IMS Offline Tool, and recompute GSTR-2B before filing GSTR-3B.</div>
    <div class="card"><div class="row between"><h2>${moving} change(s) that move credit <small class="muted">· ${changed.length - moving} optional</small></h2>
      <button class="primary" data-action="ims-download" data-org="${orgId}">Download IMS upload file</button></div>
      <p class="muted">Pre-selected: changes that move credit. Turning <em>No action</em> into <em>Accept</em> is optional, because
      untouched documents are deemed accepted when GSTR-3B is filed.</p>
      <div class="table-wrap"><table><thead><tr><th></th><th>Supplier</th><th>Document</th><th class="num">Tax</th><th>Now</th><th>Suggested</th><th>Why</th></tr></thead><tbody>
      ${changed.map((x) => `<tr><td><input type="checkbox" data-ims-pick="${x.id}" ${x.priority === 'high' ? 'checked' : ''}></td>
        <td>${esc(x.supplier_name)}<br><small class="mono">${esc(x.supplier_gstin)}</small></td>
        <td class="mono">${esc(x.doc_no)}<br><small>${fmtDate(x.doc_date)}</small></td><td class="num">${inr2(x.tax)}</td>
        <td>${esc(IMS_LABEL[x.current] || x.current)}</td><td>${opt(x)}${x.priority === 'high' ? ' ' + badge('warn', 'moves credit') : ''}</td>
        <td><small>${esc(x.reason)}</small></td></tr>`).join('') || '<tr><td colspan="7" class="muted">Nothing to change.</td></tr>'}
      </tbody></table></div><div id="ims-msg"></div></div>
    ${r.rows.filter((x) => !x.changes).length ? `<details class="card"><summary>${r.rows.filter((x) => !x.changes).length} document(s) with no change suggested</summary>
      <table style="margin-top:.6rem"><tbody>${r.rows.filter((x) => !x.changes).map((x) => `<tr><td>${esc(x.supplier_name)}</td><td class="mono">${esc(x.doc_no)}</td>
      <td>${esc(IMS_LABEL[x.current] || x.current)}</td><td><small>${esc(x.reason)}</small></td></tr>`).join('')}</tbody></table></details>` : ''}`;
}

async function downloadImsFile(orgId) {
  const ids = [...document.querySelectorAll('[data-ims-pick]:checked')].map((c) => c.dataset.imsPick);
  const msg = document.getElementById('ims-msg');
  if (!ids.length) { msg.innerHTML = '<div class="banner warn">Select at least one document.</div>'; return; }
  const overrides = {};
  for (const sel of document.querySelectorAll('[data-ims-override]')) {
    const row = store.ims.rows.find((x) => x.id === sel.dataset.imsOverride);
    if (ids.includes(row.id) && sel.value !== row.suggested) overrides[row.id] = sel.value;
  }
  try {
    const res = await api(`/api/orgs/${orgId}/ims/upload-file`, { method: 'POST', body: { ids, overrides }, raw: true });
    download(`ims-actions-${store.org.gstin}.json`, await res.blob());
    msg.innerHTML = `<div class="banner ok">File ready with ${ids.length} document(s). Upload it on the GST portal's IMS dashboard.</div>`;
  } catch (err) { showError(err, msg); }
}

async function viewOrgSettings(orgId) {
  await meta();
  const { org, el } = await orgShell(orgId, 'settings');
  el.innerHTML = `<div class="card"><h2>Business profile</h2>
      <form data-form="update-org" data-org="${orgId}">${profileFields(org)}<div id="form-msg"></div><button class="primary" type="submit">Save</button></form></div>
    <div class="card"><h2>Delete organisation</h2><p class="muted">Removes the business, its consents, connectors and every imported record.</p>
      <button class="danger" data-action="delete-org" data-org="${orgId}">Delete ${esc(org.name)}</button></div>`;
}

// ---------------------------------------------------------------- actions

const actions = {
  'ai-example': (el) => { const i = document.querySelector('form[data-form=ai-ask] input[name=question]'); i.value = el.dataset.q; i.focus(); },
  'ai-hsn': runHsnFixer,
  'ai-match': runSupplierMatcher,
  logout: async () => { try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) { /* ignore */ } setSession(null, null); renderNav(); go('#/'); },
  open: (el) => go(el.dataset.href),
  'pick-role': (el) => {
    document.querySelectorAll('.role-toggle button').forEach((b) => b.classList.toggle('selected', b === el));
    document.querySelector('input[name=role]').value = el.dataset.role;
    document.getElementById('firm-field').hidden = el.dataset.role !== 'ca';
  },
  'close-modal': closeModal,
  'close-modal-bg': (el, e) => { if (e.target === el) closeModal(); },
  copy: async (el) => {
    const t = document.getElementById(el.dataset.target);
    await navigator.clipboard.writeText(t.value ?? t.textContent);
    toast('Copied');
  },
  'download-text': (el) => download(el.dataset.name, document.getElementById(el.dataset.target).value, 'text/plain'),
  filter: (el) => { reconFilter.status = el.dataset.status; renderReconTable(); },
  'toggle-row': (el) => {
    const id = el.dataset.id;
    if (reconFilter.open.has(id)) reconFilter.open.delete(id); else reconFilter.open.add(id);
    renderReconTable();
  },
  'export-csv': exportCsv,
  'ims-download': (el) => downloadImsFile(el.dataset.org),
  reminder: (el) => showReminder(el.dataset.org, el.dataset.gstin),
  packet: (el) => showPacket(el.dataset.org, el.dataset.scheme),
  'packet-download': (el) => downloadPacket(el.dataset.org, el.dataset.scheme),
  'load-sample': async (el) => {
    busy(el, true, 'Loading demo data…');
    try {
      await api(`/api/orgs/${el.dataset.org}/import/sample`, { method: 'POST' });
      store.cache = {};
      toast('Demo data loaded: 21 purchase invoices, GSTR-2B and IMS for Sep 2026');
      go(`#/org/${el.dataset.org}`);
    } catch (err) {
      showError(err, document.getElementById('import-msg'));
      busy(el, false);
    }
  },
  grant: async (el) => {
    try { await api(`/api/orgs/${el.dataset.org}/consent`, { method: 'POST', body: { purpose: el.dataset.purpose } }); route(); } catch (err) { showError(err); }
  },
  revoke: async (el) => {
    const all = el.dataset.purpose === 'all';
    if (!confirm(all ? 'Revoke all consent and permanently delete every imported record for this business?' : 'Revoke this consent and permanently delete the data collected under it?')) return;
    try {
      const r = await api(`/api/orgs/${el.dataset.org}/consent/revoke`, { method: 'POST', body: { purpose: el.dataset.purpose } });
      store.cache = {};
      toast(`Consent revoked. ${r.datasets} dataset(s) deleted.`);
      route();
    } catch (err) { showError(err); }
  },
  'delete-dataset': async (el) => {
    if (!confirm('Delete this import permanently?')) return;
    await api(`/api/orgs/${el.dataset.org}/datasets/${el.dataset.id}`, { method: 'DELETE' });
    store.cache = {};
    route();
  },
  'connector-token': async (el) => {
    const r = await api(`/api/orgs/${el.dataset.org}/connector-token`, { method: 'POST' });
    document.getElementById('connector-out').innerHTML = `<div class="banner warn" style="margin-top:.8rem">Copy this key now; it is shown only once.</div>
      <pre class="mono" id="connector-key" style="white-space:pre-wrap;word-break:break-all">${esc(r.token)}</pre>
      <button class="small" data-action="copy" data-target="connector-key">Copy key</button>
      <p class="muted" style="margin-top:.6rem">On the Tally PC:</p>
      <pre class="mono" style="white-space:pre-wrap">python tally_push.py --server ${esc(location.origin)} --key &lt;key&gt; --from 2026-09-01 --to 2026-09-30</pre>`;
  },
  'zoho-connect': async (el) => {
    try { const r = await api(`/api/orgs/${el.dataset.org}/integrations/zoho/connect`); location.href = r.authorize_url; } catch (err) { showError(err); }
  },
  'zoho-sync': async (el) => {
    busy(el, true, 'Syncing…');
    const to = new Date(), from = new Date(); from.setMonth(from.getMonth() - 3);
    const iso = (d) => d.toISOString().slice(0, 10);
    try {
      const r = await api(`/api/orgs/${el.dataset.org}/integrations/zoho/sync`, { method: 'POST', body: { date_from: iso(from), date_to: iso(to) } });
      store.cache = {};
      toast(`Imported ${r.records} bills from Zoho Books`);
      route();
    } catch (err) { showError(err); busy(el, false); }
  },
  'delete-org': async (el) => {
    if (!confirm('Delete this business and all its data? This cannot be undone.')) return;
    try { await api(`/api/orgs/${el.dataset.org}`, { method: 'DELETE' }); store.cache = {}; go('#/home'); } catch (err) { showError(err); }
  },
};

const forms = {
  'ai-ask': submitAiAsk,
  'ai-notice': submitAiNotice,
  calculator: submitCalculator,
  login: (f) => submitAuth(f, 'login'),
  signup: (f) => submitAuth(f, 'signup'),
  'create-org': submitCreateOrg,
  forgot: async (form) => {
    const msg = document.getElementById('auth-msg');
    try {
      const r = await api('/api/auth/password-reset/request', { method: 'POST', body: { email: formData(form).email } });
      msg.innerHTML = `<div class="banner ok">${esc(r.detail)}</div>`;
    } catch (err) { showError(err, msg); }
  },
  reset: async (form) => {
    const d = formData(form);
    try {
      await api('/api/auth/password-reset/confirm', { method: 'POST', body: { token: d.token, password: d.password } });
      setSession(null, null);
      toast('Password updated. Sign in with your new password.');
      go('#/login');
    } catch (err) { showError(err, document.getElementById('auth-msg')); }
  },
  'change-password': async (form) => {
    const d = formData(form);
    try {
      await api('/api/auth/password', { method: 'POST', body: d });
      setSession(null, null);
      toast('Password changed. Please sign in again.');
      go('#/login');
    } catch (err) { showError(err, document.getElementById('auth-msg')); }
  },
  consent: submitConsent,
  notice: submitNotice,
  'update-org': async (form) => {
    const btn = form.querySelector('button[type=submit]');
    busy(btn, true, 'Saving…');
    try {
      await api(`/api/orgs/${form.dataset.org}`, { method: 'PATCH', body: profilePayload(formData(form)) });
      store.cache = {};
      toast('Profile saved');
      route();
    } catch (err) { showError(err, document.getElementById('form-msg')); busy(btn, false); }
  },
  invite: async (form) => {
    const msg = document.getElementById('invite-msg');
    try {
      const r = await api(`/api/orgs/${form.dataset.org}/members`, { method: 'POST', body: { email: formData(form).email } });
      toast(`${r.name} now has access`);
      route();
    } catch (err) { showError(err, msg); }
  },
};

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (el && actions[el.dataset.action]) { e.preventDefault(); actions[el.dataset.action](el, e); }
});
document.addEventListener('submit', (e) => {
  const form = e.target.closest('[data-form]');
  if (form && forms[form.dataset.form]) { e.preventDefault(); forms[form.dataset.form](form); }
});
document.addEventListener('input', (e) => {
  const kind = e.target.dataset.actionInput;
  if (kind === 'gstin') onGstinInput(e.target);
  if (kind === 'recon-search') {
    reconFilter.q = e.target.value;
    renderReconTable();
  }
});
document.addEventListener('change', (e) => {
  if (e.target.name === 'state_code' && e.target.closest('form[data-form=calculator]')) refreshZoneField(e.target.closest('form'), e.target.value);
  if (e.target.dataset.upload) uploadFile(e.target);
  if (['zone-district', 'zone-place'].includes(e.target.dataset.actionChange)) onPlaceChange(e.target);
  if (e.target.dataset.actionChange === 'period') go(`#/org/${e.target.dataset.org}/recon${e.target.value ? `?period=${e.target.value}` : ''}`);
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

// ---------------------------------------------------------------- router

const routes = [
  [/^\/?$/, () => (store.user ? go(store.user.role === 'ca' ? '#/console' : '#/home') : viewLanding()), false],
  [/^\/calculator$/, viewCalculator, false],
  [/^\/coverage$/, viewCoverage, false],
  [/^\/login$/, viewLogin, false],
  [/^\/forgot$/, viewForgot, false],
  [/^\/reset$/, (m, p) => viewReset(p), false],
  [/^\/account$/, viewAccount, true],
  [/^\/signup$/, (m, p) => viewSignup(p), false],
  [/^\/error$/, (m, p) => { app.innerHTML = `<div class="card"><div class="banner danger">${esc(p.get('message') || 'Something went wrong')}</div><a href="#/home">Back</a></div>`; }, false],
  [/^\/home$/, viewHome, true],
  [/^\/console$/, viewConsole, true],
  [/^\/onboard$/, viewOnboardProfile, true],
  [/^\/onboard\/(\d+)\/consent$/, (m) => viewOnboardConsent(m[1]), true],
  [/^\/onboard\/(\d+)\/connect$/, (m) => viewOnboardConnect(m[1]), true],
  [/^\/org\/(\d+)$/, (m) => viewOrgOverview(m[1]), true],
  [/^\/org\/(\d+)\/recon$/, (m, p) => viewOrgRecon(m[1], p), true],
  [/^\/org\/(\d+)\/ims$/, (m) => viewOrgIms(m[1]), true],
  [/^\/org\/(\d+)\/subsidies$/, (m) => viewOrgSubsidies(m[1]), true],
  [/^\/org\/(\d+)\/notices$/, (m) => viewOrgNotices(m[1]), true],
  [/^\/org\/(\d+)\/ai$/, (m) => viewOrgAi(m[1]), true],
  [/^\/org\/(\d+)\/data$/, (m, p) => viewOrgData(m[1], p), true],
  [/^\/org\/(\d+)\/settings$/, (m) => viewOrgSettings(m[1]), true],
];

let lastOrgPath = '';
async function route() {
  const [path, query] = location.hash.replace(/^#/, '').split('?');
  const params = new URLSearchParams(query || '');
  closeModal();
  renderNav();
  const orgPath = (path.match(/^\/org\/\d+/) || [''])[0];
  if (orgPath !== lastOrgPath) { reconFilter = { status: '', q: '', open: new Set() }; lastOrgPath = orgPath; }
  for (const [re, view, needsAuth] of routes) {
    const m = path.match(re);
    if (!m) continue;
    if (needsAuth && !store.token) return go('#/login');
    try { await view(m, params); } catch (err) {
      if (err instanceof ApiError && err.status === 401) return;
      app.innerHTML = `<div class="card"><div class="banner danger">${esc(err.message || 'Could not load this page')}</div><a href="#/home">Go back</a></div>`;
      if (!(err instanceof ApiError)) console.error(err);
    }
    window.scrollTo(0, 0);
    return;
  }
  app.innerHTML = '<div class="card empty"><h2>Page not found</h2><a href="#/">Home</a></div>';
}

window.addEventListener('hashchange', route);
route();
