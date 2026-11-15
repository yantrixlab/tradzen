const $ = (id) => document.getElementById(id);
let tabId = null;
let last = [];

function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

async function load() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  tabId = tab.id;
  const all = await chrome.storage.session.get(null);
  const b = all.backend;
  $('backend').innerHTML = !b ? 'No data sent yet.' :
    `<span class="${b.ok && Date.now() - b.at < 8000 ? 'ok' : 'bad'}">${b.ok ? 'connected' : 'unreachable'}</span> ${esc(b.url)}` +
    (b.ok ? '' : ` <span class="mut">${esc(b.err)} — start the backend</span>`);
  last = Object.entries(all).filter(([k]) => k.startsWith(`snap:${tabId}:`)).map(([, v]) => v);
  render();
}

function render() {
  if (!last.length) {
    $('status').innerHTML = '<span class="bad">hook not running</span>';
    $('sockets').innerHTML = 'No heartbeat from the page. Reload the extension on chrome://extensions, then hard-reload this tab (Ctrl+Shift+R).';
    return;
  }
  // The frame that actually carries the data (page may have several frames).
  const weight = (x) => x.frames.in + x.frames.out + x.sockets.length * 1000 + x.workers.length;
  const s = last.slice().sort((a, b) => weight(b) - weight(a))[0];
  const ageS = ((Date.now() - s.now) / 1000).toFixed(0);
  $('status').innerHTML = `<span class="${ageS < 6 ? 'ok' : 'bad'}">${ageS < 6 ? 'live' : 'stale ' + ageS + 's'}</span>`;

  $('sockets').innerHTML = s.sockets.map((k) =>
    `${esc(k.url)} <span class="${k.state === 'open' ? 'ok' : 'bad'}">${k.state}${k.closeCode ? ' (' + k.closeCode + ')' : ''}</span>
     <span class="mut">in ${k.msgsIn} / out ${k.msgsOut}</span>`).join('<br>') ||
    `<span class="mut">Hook alive (${last.length} frame${last.length > 1 ? 's' : ''}) but no WebSocket opened in the page thread.</span>`;
  if (s.workers.length) {
    $('sockets').innerHTML += '<br><span class="mut">Workers started by the page:</span><br>' +
      s.workers.map((w) => `${esc(w.kind)} ${esc(w.url)}`).join('<br>');
  }

  const secs = Math.max(1, (s.now - s.startedAt) / 1000);
  $('traffic').innerHTML = `frames in ${s.frames.in} (${(s.frames.in / secs).toFixed(1)}/s), out ${s.frames.out} · ` +
    `bytes in ${s.bytes.in} · parse errors ${s.parseErrors} · page ${esc(s.page)}`;

  const ins = s.instruments;
  $('instruments').innerHTML = ins ? `${ins.count} total · <b>${ins.otc}</b> OTC · <b>${ins.live}</b> live` : 'not received yet';

  const assets = Object.entries(s.assets).sort((a, b) => b[1].n - a[1].n);
  $('acount').textContent = assets.length;
  $('assets').tBodies[0].innerHTML = assets.map(([k, a]) =>
    `<tr><td>${esc(k)}</td><td>${a.n}</td><td>${a.perMin}</td><td>${a.lastPrice}</td>` +
    `<td class="${a.ageS > 10 ? 'bad' : ''}">${a.ageS}</td><td>${a.maxGapS}</td><td>${a.lagMeanS}</td></tr>`).join('');

  const ev = Object.entries(s.events).sort((a, b) => b[1].n - a[1].n);
  $('events').tBodies[0].innerHTML = ev.map(([k, e]) => `<tr><td>${esc(k)}</td><td>${e.n}</td></tr>`).join('');
}

$('save').onclick = () => {
  const blob = new Blob([JSON.stringify({ savedAt: new Date().toISOString(), frames: last }, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `quotex-sniffer-sample-${Date.now()}.json`;
  a.click();
};
$('reset').onclick = async () => {
  if (tabId !== null) chrome.tabs.sendMessage(tabId, { __qxsCmd: 'reset' }).catch(() => {});
};

const DEFAULT_URL = 'http://127.0.0.1:8787';
const serverUrl = async () => ((await chrome.storage.local.get('backendUrl')).backendUrl || DEFAULT_URL).replace(/\/$/, '');
$('panel').onclick = async () => {
  if (tabId === null) return;
  try { await chrome.sidePanel.open({ tabId }); window.close(); } catch (e) { chrome.tabs.create({ url: (await serverUrl()) + '/' }); }
};
$('tab').onclick = async () => chrome.tabs.create({ url: (await serverUrl()) + '/' });

// ---- connection: which server, and the personal access token (hosted server; a local one needs neither) ----
chrome.storage.local.get(['backendUrl', 'token']).then((c) => { $('c-url').value = c.backendUrl || ''; $('c-token').value = c.token || ''; });
$('c-save').onclick = async () => {
  const msg = $('c-msg');
  let raw = $('c-url').value.trim().replace(/\/+$/, '');
  const token = $('c-token').value.trim();
  if (!raw) { await chrome.storage.local.remove(['backendUrl', 'token']); msg.className = 'ok'; msg.textContent = ' using the local server'; return; }
  if (!/^https?:\/\//i.test(raw)) raw = 'https://' + raw;
  let u;
  try { u = new URL(raw); } catch (e) { msg.className = 'bad'; msg.textContent = ' that is not a web address'; return; }
  const local = ['127.0.0.1', 'localhost'].includes(u.hostname);
  if (!local && u.protocol !== 'https:') { msg.className = 'bad'; msg.textContent = ' a remote server must use https://'; return; }
  if (!local && !token) { msg.className = 'bad'; msg.textContent = ' paste the access token from My account'; return; }
  if (!local) {                                                       // ask Chrome for access to this one server only (must run inside the click)
    let granted = false;
    try { granted = await chrome.permissions.request({ origins: [u.origin + '/*'] }); } catch (e) { granted = false; }
    if (!granted) { msg.className = 'bad'; msg.textContent = ' Chrome needs your OK to reach that server'; return; }
  }
  await chrome.storage.local.set({ backendUrl: u.origin, token });
  await chrome.storage.session.remove('seq');
  msg.className = 'ok'; msg.textContent = ' saved - reload the Quotex tab once';
};
chrome.storage.local.get('forward').then((c) => { $('fwd').checked = c.forward !== false; });
$('fwd').onchange = (e) => chrome.storage.local.set({ forward: e.target.checked });

// ---- alert settings (defaults mirror background.js) ----
const DEF = { alerts: true, guard: true, sound: true, resultSound: true, desktop: true, tiers: 'AB', position: 'top-center', volume: 0.7 };
const bind = (id, key, kind) => {
  const el = $(id);
  chrome.storage.local.get(key).then((c) => {
    const v = c[key] === undefined ? DEF[key] : c[key];
    if (kind === 'check') el.checked = v; else el.value = v;
  });
  el.onchange = el.oninput = () => chrome.storage.local.set({ [key]: kind === 'check' ? el.checked : kind === 'num' ? +el.value : el.value });
};
bind('s-alerts', 'alerts', 'check'); bind('s-guard', 'guard', 'check'); bind('s-sound', 'sound', 'check'); bind('s-rsound', 'resultSound', 'check'); bind('s-desktop', 'desktop', 'check');
bind('s-tiers', 'tiers'); bind('s-pos', 'position'); bind('s-vol', 'volume', 'num');
const vol = () => +$('s-vol').value;
$('t-call').onclick = () => window.QXSound.play('call', vol());
$('t-put').onclick = () => window.QXSound.play('put', vol());
$('t-win').onclick = () => window.QXSound.play('win', vol());
$('t-alert').onclick = () => { if (tabId !== null) chrome.runtime.sendMessage({ __qxsTest: true, tabId }); };

load();
setInterval(load, 1500);
