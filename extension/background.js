// Background worker: keeps snapshots for the popup, forwards market data to the local backend, and routes the
// backend's signal/result events to the on-page cards, the alert sounds (offscreen document) and desktop notifications.
const DEFAULT_BACKEND = 'http://127.0.0.1:8787';
const DEFAULTS = { forward: true, alerts: true, guard: true, sound: true, resultSound: true, volume: 0.7, tiers: 'AB', desktop: true, position: 'top-center' };
const TFN = { 60: '1 min', 300: '5 min', 900: '15 min' };

const getSettings = async () => ({ ...DEFAULTS, ...(await chrome.storage.local.get(null)) });

// ------------------------------------------------------------------ forwarding (serialised so event cursors never race)
let chain = Promise.resolve();
let lastSeq = null, seqLoaded = false;
let queued = 0;
// On a hosted server only the admin's price-feed token sends prices; any other account sends just its own orders and balance (the server says which one this is).
// It still asks for alerts about every 1.5 s, because alerts ride on the answer.
let isFeeder = true, lastPoll = 0;
function forward(items, tabId) {
  if (!isFeeder) {
    items = items.filter((i) => i && (i.k === 'o' || i.k === 'b'));
    if (!items.length && Date.now() - lastPoll < 1500) return;
  }
  lastPoll = Date.now();
  if (queued > 40) return;                                    // backend is down: drop instead of building an ever-growing backlog
  queued++;
  chain = chain.then(() => doForward(items, tabId)).catch(() => {}).finally(() => { queued--; });
}

async function doForward(items, tabId) {
  const cfg = await getSettings();
  if (cfg.forward === false) return;
  if (!seqLoaded) { const s = await chrome.storage.session.get('seq'); lastSeq = s.seq ?? null; seqLoaded = true; }
  const conn = await chrome.storage.local.get(['backendUrl', 'token']);
  const url = (conn.backendUrl || DEFAULT_BACKEND).replace(/\/$/, '');
  const headers = { 'Content-Type': 'application/json' };
  if (conn.token) headers.Authorization = 'Bearer ' + conn.token;      // a hosted server needs the personal access token made on the "My account" page
  let ok = false, err = '', j = null;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 8000);          // a hung backend must never stall the sender queue
  try {
    const r = await fetch(url + '/ingest', { method: 'POST', headers, signal: ctl.signal,
                                             body: JSON.stringify({ items, after_seq: lastSeq }) });
    ok = r.ok; if (!ok) err = r.status === 401 ? 'HTTP 401 - the access token is missing, wrong or revoked' : 'HTTP ' + r.status; else j = await r.json();
  } catch (e) { err = ctl.signal.aborted ? 'timeout after 8 s' : String((e && e.message) || e); }
  finally { clearTimeout(timer); }
  chrome.storage.session.set({ backend: { ok, err, url, at: Date.now() } });
  if (!j) return;
  if (typeof j.seq === 'number') { lastSeq = j.seq; chrome.storage.session.set({ seq: j.seq }); }
  if (typeof j.feeder === 'boolean') isFeeder = j.feeder;
  if (j.events && j.events.length) await handleEvents(j.events, tabId, cfg);
}

// ------------------------------------------------------------------ alerts
function soundFor(ev) {
  if (ev.kind === 'guard') return ev.sound === false ? null : ({ success: 'gwin', info: 'ginfo', warn: 'gwarn', danger: 'gstop' }[ev.level] || 'gwarn');
  if (ev.kind === 'signal') return (ev.direction === 'CALL' ? 'call' : 'put') + (ev.tier === 'B' ? 'B' : '');
  return ev.status === 'win' ? 'win' : ev.status === 'loss' ? 'loss' : null;
}

async function handleEvents(events, tabId, cfg) {
  const now = Date.now() / 1000;
  for (const ev of events) {
    if (ev.kind === 'guard' && cfg.guard === false) continue;
    if (ev.tier === 'B' && cfg.tiers === 'A') continue;
    const age = now - ev.t;
    if ((ev.kind === 'signal' && age > 20) || (ev.kind === 'result' && age > 90) || (ev.kind === 'guard' && age > 180)) continue;   // stale (worker was asleep)
    await deliver(ev, tabId, cfg);
  }
}

async function deliver(ev, tabId, cfg) {
  const sound = soundFor(ev);
  const wantSound = sound && cfg.sound && (ev.kind === 'signal' || ev.kind === 'guard' || cfg.resultSound);
  const playedHere = wantSound ? await playSound(sound, cfg.volume) : true;
  if (tabId != null) {
    chrome.tabs.sendMessage(tabId, { __qxsNotify: ev, settings: cfg, sound, playHere: wantSound && !playedHere }, { frameId: 0 }).catch(() => {});
  }
  if (cfg.desktop && ev.kind === 'signal') desktopNotify(ev, tabId);
  if (cfg.desktop && ev.kind === 'guard') guardNotify(ev, tabId);
}

let creating = null;
async function ensureOffscreen() {
  const ctxs = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
  if (ctxs.length) return;
  if (!creating) {
    creating = chrome.offscreen.createDocument({ url: 'offscreen.html', reasons: ['AUDIO_PLAYBACK'],
                                                 justification: 'Play alert sounds for trading signals' }).finally(() => { creating = null; });
  }
  await creating;
  await new Promise((r) => setTimeout(r, 200));       // let the offscreen script register its listener
}

async function playSound(kind, volume) {
  try {
    await ensureOffscreen();
    await chrome.runtime.sendMessage({ __qxsSound: { kind, volume } });
    return true;
  } catch (_) { return false; }                        // content script plays it instead (needs a prior page click)
}

const notifTabs = new Map();
async function desktopNotify(ev, tabId) {
  try {
    if (tabId != null) {
      const tab = await chrome.tabs.get(tabId), win = await chrome.windows.get(tab.windowId);
      if (tab.active && win.focused) return;           // you are looking at the card already
    }
  } catch (_) { /* tab gone: notify anyway */ }
  const id = 'qxs-' + ev.id + '-' + Date.now();
  notifTabs.set(id, tabId);
  const call = ev.direction === 'CALL';
  chrome.notifications.create(id, {
    type: 'basic', iconUrl: 'icons/icon128.png', priority: 2,
    title: `${call ? '▲ BUY / CALL' : '▼ SELL / PUT'}  ·  ${ev.name}`,
    message: `Tier ${ev.tier} · ${TFN[ev.tf] || ev.tf + 's'} · strength ${ev.strength}\nEnter now — expires ${new Date(ev.expire_t * 1000).toLocaleTimeString()}`,
  });
}
// A trading-guard alert as a desktop notification (when the Quotex tab is not in front). Warnings and stops stay until dismissed.
async function guardNotify(ev, tabId) {
  try {
    if (tabId != null) {
      const tab = await chrome.tabs.get(tabId), win = await chrome.windows.get(tab.windowId);
      if (tab.active && win.focused) return;
    }
  } catch (_) { /* tab gone: notify anyway */ }
  const id = 'qxs-guard-' + ev.id + '-' + Date.now();
  notifTabs.set(id, tabId);
  const mark = { success: '✓', info: 'ℹ', warn: '⚠', danger: '⛔' }[ev.level] || '⚠';
  chrome.notifications.create(id, { type: 'basic', iconUrl: 'icons/icon128.png', priority: 2, requireInteraction: ev.level === 'danger' || ev.level === 'warn',
                                     title: `${mark} ${ev.title}`, message: ev.message });
}
chrome.notifications.onClicked.addListener(async (id) => {
  const tabId = notifTabs.get(id);
  chrome.notifications.clear(id);
  if (tabId == null) return;
  try { const tab = await chrome.tabs.update(tabId, { active: true }); await chrome.windows.update(tab.windowId, { focused: true }); } catch (_) { /* closed */ }
});

// Preview from the popup: a fake CALL signal, then a WIN a few seconds later.
async function sendTest(tabId) {
  const cfg = await getSettings();
  const now = Date.now() / 1000, base = Math.floor(now / 60) * 60;
  const ev = { seq: 0, kind: 'signal', t: now, id: -1, symbol: 'AUDJPY_otc', name: 'AUD/JPY (OTC)', market: 'OTC', tf: 60, direction: 'CALL',
               tier: 'A', strength: 62, score: 62, agree: 3, status: 'pending', entry: 108.407, exit: null, basis_t: base, target_t: base + 60,
               expire_t: now + 45, payout: 86, breakeven: 1 / 1.86, price: 108.407, now, sentiment: 61,
               factors: [{ name: 'Bollinger Band Bounce', vote: 1, note: 'closed outside lower band' }, { name: 'Stochastic %K/%D Cross', vote: 1, note: '' },
                         { name: 'CCI Extreme + Reversal', vote: 0.6, note: '' }],
               strategy: 'Confluence 5-Factor', also: [{ name: 'MACD Histogram Cross', tier: 'A', strength: 100 }],
               tier_stats: { n: 84, wins: 47, losses: 37, acc: 0.56, lo: 0.45, hi: 0.66 }, pair_stats: { n: 12, wins: 7, losses: 5, acc: 0.58 } };
  await deliver(ev, tabId, cfg);
  setTimeout(() => deliver({ ...ev, kind: 'result', status: 'win', exit: 108.43, t: Date.now() / 1000 }, tabId, cfg), 7000);
}

// ------------------------------------------------------------------ messages
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg && msg.__qxsTest) { sendTest(msg.tabId); return; }
  if (!msg || !sender.tab) return;
  if (msg.__qxs === 2) { forward(msg.items, sender.tab.id); return; }
  if (msg.__qxs !== 1) return;
  const key = `snap:${sender.tab.id}:${sender.frameId}`;
  chrome.storage.session.set({ [key]: msg.snap });
  const n = Object.keys(msg.snap.assets || {}).length;
  chrome.action.setBadgeBackgroundColor({ color: n ? '#16a34a' : '#6b7280', tabId: sender.tab.id });
  chrome.action.setBadgeText({ text: String(n), tabId: sender.tab.id });
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const all = await chrome.storage.session.get(null);
  const dead = Object.keys(all).filter((k) => k.startsWith(`snap:${tabId}:`));
  if (dead.length) chrome.storage.session.remove(dead);
});
