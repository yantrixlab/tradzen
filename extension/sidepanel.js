// The side panel: the live board beside the Quotex tab.
//  - a local server (no accounts): the classic page, loaded with a unique address every time so Chrome never shows an old cached copy;
//  - a hosted server: the panel asks the server for a one-time ticket with the extension's own access token (the same sign-in as the popup) and the live board page
//    trades it for a limited session of its own, so nobody types a password twice. The ticket travels in the address fragment, which is never sent to a server.
const DEFAULT_URL = 'http://127.0.0.1:8787';
const frame = document.getElementById('dash');
const msg = document.getElementById('msg');
const tabLink = document.getElementById('tab');
const isLocalHost = (h) => h === '127.0.0.1' || h === 'localhost';
let lastOpen = 0, opening = false;

function show(text, retry) {
  frame.hidden = true; frame.removeAttribute('src');
  msg.hidden = false; msg.textContent = text;
  if (retry) { const b = document.createElement('button'); b.textContent = 'Try again'; b.onclick = () => open(); msg.append(document.createElement('br'), b); }
}

async function open() {
  if (opening) return;
  opening = true; lastOpen = Date.now();
  try {
    const c = await chrome.storage.local.get(['backendUrl', 'token', 'who']);
    const who = c.who;
    const url = (c.backendUrl || DEFAULT_URL).replace(/\/+$/, '');
    let host = '';
    try { host = new URL(url).hostname; } catch (_) { /* ignore */ }
    tabLink.href = url + '/';
    if (isLocalHost(host)) { msg.hidden = true; frame.hidden = false; frame.src = url + '/?embed=1&t=' + Date.now(); return; }
    if (!c.token) { show('Sign in first: open the Tradzen popup, Connection tab, and sign in with your Tradzen username and password.'); return; }
    show('Opening the live board…');
    let r;
    try { r = await fetch(url + '/api/auth/panel-ticket', { method: 'POST', headers: { Authorization: 'Bearer ' + c.token } }); }
    catch (_) { show('Cannot reach ' + url + '.', true); return; }
    if (r.status === 401) { show('The extension is signed out or its access was removed. Sign in again in the Tradzen popup (Connection tab).'); return; }
    if (!r.ok) { show('The server refused (' + r.status + ').', true); return; }
    const j = await r.json();
    msg.hidden = true; frame.hidden = false;
    const page = who && who.role === 'admin' ? 'live' : 'sessions';       // the admin gets the full live page, everybody else the live session (its chart and signals; their own pair's chart when none is live)
    frame.src = url + '/admin/' + page + '?embed=1&panel=1&t=' + Date.now() + '#ticket=' + encodeURIComponent(j.ticket);
  } finally { opening = false; }
}

// the page inside says its session ended (12 hours): ask for a new ticket, at most once every 10 seconds
window.addEventListener('message', (e) => {
  if (e.source === frame.contentWindow && e.data && e.data.qxs === 'panel-expired' && Date.now() - lastOpen > 10000) open();
});
// signing in or out in the popup reopens the panel
chrome.storage.onChanged.addListener((ch, area) => { if (area === 'local' && (ch.token || ch.backendUrl || ch.who)) open(); });
open();
