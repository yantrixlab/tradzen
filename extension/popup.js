const $ = (id) => document.getElementById(id);
try { $('ver').textContent = 'v' + chrome.runtime.getManifest().version + ' · read-only'; } catch (_) { /* keeps the plain text */ }          // the real version, never a number typed into the page
let tabId = null;
let last = [];

function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
const DEFAULT_URL = 'http://127.0.0.1:8787';
const isLocalHost = (h) => h === '127.0.0.1' || h === 'localhost';
const serverUrl = async () => ((await chrome.storage.local.get('backendUrl')).backendUrl || DEFAULT_URL).replace(/\/$/, '');
const setMsg = (el, kind, text) => { el.className = 'msg ' + (kind || 'mut'); el.textContent = text || ''; };

// ------------------------------------------------------------------ tabs
document.querySelectorAll('nav button').forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll('nav button').forEach((x) => x.classList.toggle('on', x === b));
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('on', t.id === 'tab-' + b.dataset.tab));
    try { sessionStorage.setItem('tab', b.dataset.tab); } catch (_) { /* ignore */ }
  };
});
try { const t = sessionStorage.getItem('tab'); if (t) document.querySelector(`nav button[data-tab="${t}"]`)?.click(); } catch (_) { /* ignore */ }

// ------------------------------------------------------------------ the status of this tab and of the connection
function setStat(key, tone, title, sub) {
  $('i-' + key).className = 'ic ' + (tone || '');
  $('t-' + key).textContent = title;
  $('s-' + key).textContent = sub;
}

function pill(kind, text) { const p = $('status'); p.className = 'pill ' + kind; p.lastElementChild.textContent = text; }

function renderOverview(all, conn, snap) {
  const b = all.backend, qx = all.qx, who = all.who || conn.who || null;
  let host = '';
  try { host = new URL(conn.backendUrl || DEFAULT_URL).host; } catch (_) { host = ''; }
  const local = isLocalHost((host || '').split(':')[0]);
  const signedIn = !!conn.token;
  const banners = [];

  // ---- server
  if (!signedIn && !local) {
    setStat('srv', 'warn', 'Not signed in', 'Sign in under Connection to link this browser to your account.');
  } else if (!b) {
    setStat('srv', '', 'Connecting…', host);
  } else if (!b.ok) {
    setStat('srv', 'bad', 'Cannot reach the server', `${host} · ${b.err || ''}`);
    if (/401/.test(b.err || '')) banners.push(['bad', '<b>Your sign-in expired or was revoked.</b> Sign in again under Connection.']);
  } else {
    setStat('srv', 'ok', 'Connected', local ? host + ' · local server' : `${who ? who.username + ' · ' + who.role + ' · ' : ''}${host}`);
  }

  // ---- Quotex account
  const seen = snap && snap.account && snap.account.known ? snap.account.hint : null;
  const st = qx && qx.state;
  if (st === 'mismatch') {
    setStat('acct', 'bad', 'Wrong Quotex account', `This Tradzen account is linked to account ···${qx.hint || '?'}${seen ? `. This tab is ···${seen}` : ''}.`);
    banners.push(['bad', `<b>This Quotex tab is on a different account.</b> Nothing of this account is saved or shown, and you get no alerts, until you log into the Quotex account that ends in <b>···${esc(qx.hint || '?')}</b>.`]);
  } else if (st === 'taken') {
    setStat('acct', 'bad', 'Quotex account already in use', 'That Quotex account is linked to another Tradzen user. Ask your admin.');
    banners.push(['bad', '<b>This Quotex account belongs to another Tradzen user.</b> Ask your admin to unlink it first.']);
  } else if (st === 'ok' || st === 'bound') {
    setStat('acct', 'ok', `Quotex account ···${qx.hint || seen || '?'}`, st === 'bound' ? 'Linked to your Tradzen account just now.' : 'Linked to your Tradzen account.');
  } else if (qx) {
    setStat('acct', 'warn', seen ? `Detected ···${seen}` : 'Looking for your Quotex account', seen ? 'Linking to your Tradzen account…' : 'Your trades wait until it is seen. Open the Quotex trading page and place or open any order.');
  } else if (seen) {
    setStat('acct', 'ok', `Quotex account ···${seen}`, local ? 'Not linked: this is a local server.' : 'Detected.');
  } else {
    setStat('acct', snap ? 'warn' : '', snap ? 'Looking for your Quotex account' : 'Quotex account', snap ? 'It shows up with your first order or balance message.' : 'Open Quotex in this tab.');
  }

  // ---- role
  if (all.feeder) setStat('role', 'ok', 'Price feed', 'This browser streams Quotex prices for everyone. Keep this tab open.');
  else setStat('role', '', signedIn || !local ? 'Alerts and my trades' : 'Local mode', 'Gets the signals for your account and keeps your own trade history.');

  renderContrib(all, local || !signedIn, banners);
  renderFeed(all, local || !signedIn, banners);
  $('banner').innerHTML = banners.map(([k, h]) => `<div class="banner ${k}">${h}</div>`).join('');

  // ---- the pill
  if (!snap) pill('', 'no Quotex tab');
  else if (b && !b.ok) pill('off', 'offline');
  else if (st === 'mismatch' || st === 'taken') pill('off', 'blocked');
  else { const age = (Date.now() - snap.now) / 1000; if (age < 6) pill('live', 'live'); else pill('off', 'stale ' + age.toFixed(0) + 's'); }
}

// ---- shared candles: what this browser added to the server's candle store, and the ranges the server would like to have
const fmtT = (t) => new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
function renderContrib(all, hide, banners) {
  const c = all.contrib, t = (c && c.totals) || {}, n = (x) => Number(x || 0).toLocaleString();
  $('r-cd').hidden = hide || !!all.feeder;                      // the price feed has its own path; a local server has no shared store
  $('c-want').hidden = true;
  if ($('r-cd').hidden) return;
  const added = t.new ? `${n(t.new)} new candles added this session` : 'Nothing new to add yet';
  if (!c || !c.state) setStat('cd', '', 'Shared candles', 'Waiting for your Quotex page to load a chart…');
  else if (c.state === 'wait') setStat('cd', 'warn', 'Waiting for your Quotex account', 'Candles are taken once your Quotex account is known and linked.');
  else if (c.state === 'busy' || Date.now() < (c.retryAt || 0)) setStat('cd', 'warn', 'Slowing down', 'The server is busy; your history is kept and sent a little later.');
  else if (c.state === 'paused') {
    setStat('cd', 'bad', 'Paused by the admin', 'Your candle contributions are switched off. Your trades and alerts are not affected.');
    banners.push(['warn', '<b>Candle sharing is paused for your account.</b> Ask your admin if you want it back.']);
  } else if (c.state === 'off') setStat('cd', '', 'Not shared', 'Switched off on this server or for your plan.');
  else setStat('cd', 'ok', 'Sharing candles', `${added}${t.dup ? ` · ${n(t.dup)} the server already had` : ''}${c.queued ? ` · ${c.queued} waiting` : ''}.`);
  const want = c.state === 'on' && Array.isArray(c.want) ? c.want.slice(0, 5) : [];
  $('c-want').hidden = !want.length;
  $('want-list').innerHTML = want.map((w) => `<div class="row"><label>${esc(w.name || w.asset)}<small>${w.kind === 'gap' ? 'A hole' : 'Older history'} · ${esc(fmtT(w.from))} – ${esc(fmtT(w.to))}${w.missing ? ` · about ${n(w.missing)} candles` : ''}</small></label></div>`).join('');
}

// ---- live prices: the pairs this browser streams for everybody (the server holds one account per pair)
function renderFeed(all, hide, banners) {
  const f = all.feed, n = (x) => Number(x || 0).toLocaleString();
  $('r-lf').hidden = hide || !!all.feeder;                      // the price feed streams everything itself; a local server has no leases
  if ($('r-lf').hidden) return;
  const hold = (f && f.hold) || [];
  if (!f || !f.state) setStat('lf', '', 'Live prices', 'Waiting for prices from your Quotex page…');
  else if (f.state === 'wait') setStat('lf', 'warn', 'Waiting for your Quotex account', 'Prices are taken once your Quotex account is known and linked.');
  else if (f.state === 'paused') {
    setStat('lf', 'bad', 'Paused by the admin', 'Your live prices are switched off. Your trades and alerts are not affected.');
    banners.push(['warn', '<b>Sharing live prices is paused for your account.</b> Ask your admin if you want it back.']);
  } else if (f.state === 'off') setStat('lf', '', 'Not shared', 'Switched off on this server or for your plan.');
  else if (f.state === 'busy' || Date.now() < (f.retryAt || 0)) setStat('lf', 'warn', 'Slowing down', 'The server is busy; prices go out again in a moment.');
  else if (hold.length) setStat('lf', 'ok', `Streaming ${hold.length} ${hold.length === 1 ? 'pair' : 'pairs'}`, `${hold.map(esc).join(', ')} · ${n((f.totals || {}).applied)} prices used this session. Keep this tab open.`);
  else setStat('lf', 'ok', 'Ready to stream', 'Nobody else streams the pair you have open: it is used when you open one the feed does not have, and then nobody has to ask you.');
}

async function load() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  tabId = tab.id;
  const all = await chrome.storage.session.get(null);
  const conn = await chrome.storage.local.get(['backendUrl', 'token', 'who']);
  last = Object.entries(all).filter(([k]) => k.startsWith(`snap:${tabId}:`)).map(([, v]) => v);
  const weight = (x) => x.frames.in + x.frames.out + x.sockets.length * 1000 + x.workers.length;
  const snap = last.length ? last.slice().sort((a, b) => weight(b) - weight(a))[0] : null;

  const b = all.backend;
  $('backend').innerHTML = !b ? 'No data sent yet.' :
    `<span class="${b.ok && Date.now() - b.at < 8000 ? 'ok' : 'bad'}">${b.ok ? 'connected' : 'unreachable'}</span> ${esc(b.url)}` + (b.ok ? '' : ` <span class="mut">${esc(b.err)}</span>`);
  renderOverview(all, conn, snap);
  renderSigned(all, conn);
  renderDiag(snap);
}

function renderSigned(all, conn) {
  let local = true;
  try { local = isLocalHost(new URL(conn.backendUrl || DEFAULT_URL).hostname); } catch (_) { /* ignore */ }
  const w = all.who || conn.who;
  const on = !!conn.token;
  $('c-out').hidden = !on;
  $('c-in').hidden = on && !!w;
  if (on) {
    $('o-who').textContent = w ? `${w.username} (${w.role})` : 'Signed in with an access token';
    $('o-srv').textContent = (conn.backendUrl || DEFAULT_URL) + (all.feeder ? ' · price feed' : '');
  }
  $('l-feed-row').hidden = local;
  // On a hosted server nothing here works before signing in: the buttons wait for it (the local server needs no account).
  const wait = !local && !on;
  for (const id of ['tab', 'panel', 't-alert']) { $(id).disabled = wait; $(id).title = wait ? 'Sign in on the Connection tab first' : ''; }
}

// ------------------------------------------------------------------ diagnostics (what the page hook sees)
function renderDiag(s) {
  if (!s) {
    $('sockets').innerHTML = 'No heartbeat from this page. Open this popup on your Quotex tab, or reload the extension on chrome://extensions and hard-reload the tab (Ctrl+Shift+R).';
    $('acct-diag').textContent = '–';
    return;
  }
  $('sockets').innerHTML = s.sockets.map((k) =>
    `${esc(k.url)} <span class="${k.state === 'open' ? 'ok' : 'bad'}">${k.state}${k.closeCode ? ' (' + k.closeCode + ')' : ''}</span>
     <span class="mut">in ${k.msgsIn} / out ${k.msgsOut}</span>`).join('<br>') ||
    `<span class="mut">Hook alive (${last.length} frame${last.length > 1 ? 's' : ''}) but no WebSocket opened in the page thread.</span>`;
  if (s.workers.length) $('sockets').innerHTML += '<br><span class="mut">Workers started by the page:</span><br>' + s.workers.map((w) => `${esc(w.kind)} ${esc(w.url)}`).join('<br>');

  const secs = Math.max(1, (s.now - s.startedAt) / 1000);
  $('traffic').innerHTML = `frames in ${s.frames.in} (${(s.frames.in / secs).toFixed(1)}/s), out ${s.frames.out} · bytes in ${s.bytes.in} · parse errors ${s.parseErrors} · page ${esc(s.page)}`;
  const ins = s.instruments;
  $('instruments').innerHTML = ins ? `${ins.count} total · <b>${ins.otc}</b> OTC · <b>${ins.live}</b> live` : 'not received yet';

  const assets = Object.entries(s.assets).sort((a, b) => b[1].n - a[1].n);
  $('acount').textContent = assets.length;
  $('assets').tBodies[0].innerHTML = assets.map(([k, a]) =>
    `<tr><td>${esc(k)}</td><td>${a.n}</td><td>${a.perMin}</td><td>${a.lastPrice}</td><td class="${a.ageS > 10 ? 'bad' : ''}">${a.ageS}</td><td>${a.maxGapS}</td><td>${a.lagMeanS}</td></tr>`).join('');
  const ev = Object.entries(s.events).sort((a, b) => b[1].n - a[1].n);
  $('events').tBodies[0].innerHTML = ev.map(([k, e]) => `<tr><td>${esc(k)}</td><td>${e.n}</td></tr>`).join('');

  const a = s.account;
  $('acct-diag').innerHTML = !a ? 'This build of the page hook does not report it: reload the Quotex tab.' :
    (a.known ? `<span class="ok">account number seen</span> (ends ···${esc(a.hint)})` : '<span class="warn">no account number seen yet</span>') +
    '<br>seen in: ' + (Object.entries(a.sources || {}).map(([k, n]) => `${esc(k)} ×${n}`).join(', ') || 'no message yet');
}

$('save').onclick = () => {
  const blob = new Blob([JSON.stringify({ savedAt: new Date().toISOString(), frames: last }, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `quotex-sniffer-sample-${Date.now()}.json`;
  a.click();
};
$('reset').onclick = async () => { if (tabId !== null) chrome.tabs.sendMessage(tabId, { __qxsCmd: 'reset' }).catch(() => {}); };

async function openBoardWindow(url) {
  try {
    const s = await chrome.storage.session.get('boardWin');
    if (s.boardWin) {
      try { await chrome.windows.update(s.boardWin, { focused: true }); return; } catch (_) { /* it was closed: open a new one */ }
    }
  } catch (_) { /* no session storage: just open one */ }
  const w = 460, sc = window.screen;
  const left = (sc.availLeft || 0) + Math.max(0, sc.availWidth - w), top = sc.availTop || 0;
  try {
    const win = await chrome.windows.create({ url, type: 'popup', width: w, height: sc.availHeight || 800, left, top });
    try { await chrome.storage.session.set({ boardWin: win.id }); } catch (_) { /* ignore */ }
  } catch (_) { chrome.tabs.create({ url }); }
}

$('panel').onclick = async () => {
  // Opens the side panel beside the Quotex tab (called first, while the click still counts as a user gesture). The panel page signs itself in from the extension's token.
  try {
    if (tabId === null) throw new Error('no tab');
    await chrome.sidePanel.open({ tabId });
    window.close();
    return;
  } catch (_) { /* fall through to a window / tab */ }
  const url = await serverUrl();
  let host = '';
  try { host = new URL(url).hostname; } catch (_) { /* ignore */ }
  if (!isLocalHost(host)) { await openBoardWindow(url + '/admin/live?embed=1'); window.close(); return; }      // no side panel on this browser: a narrow window instead
  chrome.tabs.create({ url: url + '/' });
};
$('tab').onclick = async () => chrome.tabs.create({ url: (await serverUrl()) + '/' });

// ------------------------------------------------------------------ connection: sign in with the Tradzen username and password
async function deviceId() {
  const c = await chrome.storage.local.get('device');
  if (c.device) return c.device;
  const id = Array.from(crypto.getRandomValues(new Uint8Array(6))).map((b) => b.toString(16).padStart(2, '0')).join('');
  await chrome.storage.local.set({ device: id });
  return id;
}

function parseServer() {
  let raw = $('c-url').value.trim().replace(/\/+$/, '') || DEFAULT_URL;
  if (!/^https?:\/\//i.test(raw)) raw = 'https://' + raw;
  let u;
  try { u = new URL(raw); } catch (e) { return { error: 'that is not a web address' }; }
  const local = isLocalHost(u.hostname);
  if (!local && u.protocol !== 'https:') return { error: 'a remote server must use https://' };
  return { u, local };
}

chrome.storage.local.get('backendUrl').then((c) => { $('c-url').value = c.backendUrl || (DEFAULT_URL === 'http://127.0.0.1:8787' ? '' : DEFAULT_URL); });

$('l-go').onclick = async () => {
  const msg = $('l-msg');
  setMsg(msg, 'mut', '');
  const s = parseServer();
  if (s.error) return setMsg(msg, 'bad', s.error);
  const user = $('l-user').value.trim(), pass = $('l-pass').value;
  if (s.local && !user) {                                             // a local server needs no account
    await chrome.storage.local.set({ backendUrl: s.u.origin });
    await chrome.storage.local.remove(['token', 'who']);
    return setMsg(msg, 'ok', 'Using the local server: nothing to sign in to. Reload the Quotex tab once.');
  }
  if (!user || !pass) return setMsg(msg, 'bad', 'Enter your username and password.');
  if (!s.local) {                                                     // ask Chrome for access to this one server only (must run inside the click)
    let granted = false;
    try { granted = await chrome.permissions.request({ origins: [s.u.origin + '/*'] }); } catch (e) { granted = false; }
    if (!granted) return setMsg(msg, 'bad', 'Chrome needs your OK to reach that server.');
  }
  $('l-go').disabled = true;
  setMsg(msg, 'mut', 'Signing in…');
  try {
    const r = await fetch(s.u.origin + '/api/auth/extension-login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: user, password: pass, device: await deviceId(), feeder: $('l-feed').checked }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'HTTP ' + r.status);
    await chrome.storage.local.set({ backendUrl: s.u.origin, token: j.token, who: { username: j.user.username, role: j.user.role, feeder: !!j.feeder } });
    await chrome.storage.session.remove(['seq', 'qx', 'who', 'backend', 'contrib', 'sentH', 'feeder', 'feed']);
    $('l-pass').value = '';
    setMsg(msg, 'ok', `Signed in as ${j.user.username}. Reload the Quotex tab once.`);
    load();
  } catch (e) {
    setMsg(msg, 'bad', String((e && e.message) || e));
  } finally { $('l-go').disabled = false; }
};
$('l-pass').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('l-go').click(); });

$('l-out').onclick = async () => {
  await chrome.storage.local.remove(['token', 'who']);
  await chrome.storage.session.remove(['seq', 'qx', 'who', 'backend', 'contrib', 'sentH', 'feeder', 'feed']);
  $('c-token').value = '';
  setMsg($('o-msg'), 'mut', 'Signed out in this browser. To disconnect it for good, also revoke it under My account on the website.');
  load();
};

// ---- access token fallback (the original way)
$('c-save').onclick = async () => {
  const msg = $('c-msg');
  const token = $('c-token').value.trim();
  const s = parseServer();
  if (s.error) { msg.className = 'bad'; msg.textContent = ' ' + s.error; return; }
  if (!s.local && !token) { msg.className = 'bad'; msg.textContent = ' paste the access token from My account'; return; }
  if (!s.local) {
    let granted = false;
    try { granted = await chrome.permissions.request({ origins: [s.u.origin + '/*'] }); } catch (e) { granted = false; }
    if (!granted) { msg.className = 'bad'; msg.textContent = ' Chrome needs your OK to reach that server'; return; }
  }
  await chrome.storage.local.set({ backendUrl: s.u.origin, token });
  await chrome.storage.local.remove('who');
  await chrome.storage.session.remove(['seq', 'qx', 'who', 'contrib', 'sentH', 'feeder', 'feed']);
  msg.className = 'ok'; msg.textContent = ' saved - reload the Quotex tab once';
};

chrome.storage.local.get('forward').then((c) => { $('fwd').checked = c.forward !== false; });
$('fwd').onchange = (e) => chrome.storage.local.set({ forward: e.target.checked });

// ------------------------------------------------------------------ alert settings (defaults mirror background.js)
const DEF = { alerts: true, guard: true, sessions: true, sound: true, resultSound: true, desktop: true, tiers: 'AB', position: 'bottom-left', volume: 0.7 };
const bound = [];
const bind = (id, key, kind) => {
  const el = $(id);
  bound.push([el, key, kind]);
  el.onchange = el.oninput = () => chrome.storage.local.set({ [key]: kind === 'check' ? el.checked : kind === 'num' ? +el.value : el.value });
};
async function syncSettings() {
  const c = await chrome.storage.local.get(null);
  for (const [el, key, kind] of bound) {
    const v = c[key] === undefined ? DEF[key] : c[key];
    if (kind === 'check') el.checked = v; else if (document.activeElement !== el) el.value = v;
  }
}
bind('s-alerts', 'alerts', 'check'); bind('q-alerts', 'alerts', 'check'); bind('s-guard', 'guard', 'check'); bind('s-sessions', 'sessions', 'check'); bind('s-sound', 'sound', 'check'); bind('q-sound', 'sound', 'check');
bind('s-rsound', 'resultSound', 'check'); bind('s-desktop', 'desktop', 'check'); bind('s-tiers', 'tiers'); bind('s-pos', 'position'); bind('s-vol', 'volume', 'num');
chrome.storage.onChanged.addListener((_, area) => { if (area === 'local') syncSettings(); });
syncSettings();

const vol = () => +$('s-vol').value;
$('t-call').onclick = () => window.QXSound.play('call', vol());
$('t-put').onclick = () => window.QXSound.play('put', vol());
$('t-win').onclick = () => window.QXSound.play('win', vol());
$('t-alert').onclick = () => { if (tabId !== null) chrome.runtime.sendMessage({ __qxsTest: true, tabId }); };

load();
setInterval(load, 1500);
