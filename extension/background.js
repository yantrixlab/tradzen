// Background worker: keeps snapshots for the popup, forwards market data to the local backend, and routes the
// backend's signal/result events to the on-page cards, the alert sounds (offscreen document) and desktop notifications.
const DEFAULT_BACKEND = 'http://127.0.0.1:8787';
const DEFAULTS = { forward: true, alerts: true, guard: true, sessions: true, sound: true, resultSound: true, volume: 0.7, tiers: 'AB', desktop: true, position: 'top-center' };
const TFN = { 60: '1 min', 300: '5 min', 900: '15 min' };

const getSettings = async () => ({ ...DEFAULTS, ...(await chrome.storage.local.get(null)) });

// ------------------------------------------------------------------ forwarding (serialised so event cursors never race)
let chain = Promise.resolve();
let lastSeq = null, seqLoaded = false;
let queued = 0;
// On a hosted server only the admin's price-feed token sends prices; any other account sends just its own orders and balance (the server says which one this is).
// It still asks for alerts about every 1.5 s, because alerts ride on the answer.
let isFeeder = null, lastPoll = 0;               // null = the server has not said yet
function forward(items, tabId) {
  if (isFeeder !== true) items = queueHistory(items, tabId);        // not (known to be) the price feed: its chart history is offered to the shared candle store instead
  if (isFeeder === false) {
    items = noteTicks(items);                                        // live prices: only the pairs the server asks for are kept (see "live prices")
    items = items.filter((i) => i && (i.k === 'o' || i.k === 'b' || i.k === 'a' || i.k === 'sel' || (i.k === 'ev' && i.e === 'hook-start')));      // own orders / balance, which Quotex account this is, and which pair is open (the chart follows it)
    if (!items.length && !histReady(tabId) && !feedDue() && Date.now() - lastPoll < 1500) return;
  }
  lastPoll = Date.now();
  if (queued > 40) return;                                    // backend is down: drop instead of building an ever-growing backlog
  queued++;
  chain = chain.then(() => doForward(items, tabId)).catch(() => {}).finally(() => { queued--; });
}

// ------------------------------------------------------------------ shared candles
// An ordinary (non price-feed) extension offers the chart history its Quotex page loads anyway to the server's shared candle store. The server only ever ADDS candles it does not
// have (it never changes a stored one), answers per item what was new, and says when to slow down. History waits per tab until the Quotex account of that tab is known
// (the server takes it only from the linked account), goes out a few items per request, and is not sent again once the server has it.
const HIST_MAX = 40, HIST_MS = 10 * 60 * 1000, HIST_PER_REQUEST = 3, SENT_MS = 60 * 60 * 1000, SENT_MAX = 200;
const histByTab = new Map();                       // tabId -> [{ item, key, at }]
const sentH = new Map();                           // key -> when the server answered for it
let contribState = null, contribRetryAt = 0, contribWant = [];
const contribTotals = { items: 0, rows: 0, new: 0, dup: 0, conflict: 0, rejected: 0 };
const contribOff = () => contribState === 'off' || contribState === 'paused';
const histKey = (i) => { const r = i.rows; return `${i.asset}|${r[0] && r[0][0]}|${r[r.length - 1] && r[r.length - 1][0]}|${r.length}`; };

function queueHistory(items, tabId) {
  const rest = [];
  for (const i of items) {
    if (!i || i.k !== 'h') {
      if (i && i.k === 'ev' && i.e === 'hook-start') histByTab.delete(tabId);          // a new page load: what was waiting belongs to the old one
      rest.push(i);
      continue;
    }
    if (contribOff() || typeof i.asset !== 'string' || !Array.isArray(i.rows) || i.rows.length < 2) continue;
    const key = histKey(i);
    if (Date.now() - (sentH.get(key) || 0) < SENT_MS) continue;                        // the server has this range already
    const q = histByTab.get(tabId) || [];
    if (!q.some((x) => x.key === key)) { q.push({ item: { k: 'h', asset: i.asset, rows: i.rows }, key, at: Date.now() }); histByTab.set(tabId, q.slice(-HIST_MAX)); }
  }
  return rest;
}
const histReady = (tabId) => { const q = histByTab.get(tabId); return !!(q && q.length) && !contribOff() && Date.now() >= contribRetryAt && (needsLink === false || acctByTab.has(tabId)); };
function takeHistory(tabId, all) {
  const q = (histByTab.get(tabId) || []).filter((x) => Date.now() - x.at < HIST_MS);
  const take = all ? q : q.slice(0, HIST_PER_REQUEST), left = all ? [] : q.slice(HIST_PER_REQUEST);
  if (left.length) histByTab.set(tabId, left); else histByTab.delete(tabId);
  return take;
}
function requeueHistory(tabId, e) { const q = histByTab.get(tabId) || []; q.unshift(e); histByTab.set(tabId, q.slice(0, HIST_MAX)); }
function markSent(e) {
  sentH.set(e.key, Date.now());
  if (sentH.size > SENT_MAX) for (const k of [...sentH.keys()].slice(0, sentH.size - SENT_MAX)) sentH.delete(k);
}
function saveContrib() {
  const queued = [...histByTab.values()].reduce((a, q) => a + q.length, 0);
  chrome.storage.session.set({ contrib: { state: contribState, retryAt: contribRetryAt, want: contribWant, totals: contribTotals, queued, at: Date.now() }, sentH: Object.fromEntries(sentH) });
}
/** What the server said about the history that went out with this request (`sending`, in the order it was sent). */
function afterContrib(j, sending, tabId) {
  const c = j.contrib;
  if (j.feeder === true) sending.forEach(markSent);                                    // the price feed's own history path took them
  else if (!c) { if (sending.length) { contribState = 'off'; histByTab.clear(); } }     // an older server ignores history from ordinary accounts
  else {
    contribState = c.state;
    if (c.state === 'busy') contribRetryAt = Date.now() + Math.max(5, Math.min(c.retry_after || 30, 600)) * 1000;
    if (Array.isArray(c.want)) contribWant = c.want;
    const res = c.results || [];
    sending.forEach((e, n) => {
      if (res[n]) {
        markSent(e);
        contribTotals.items++; contribTotals.rows += res[n].received || 0; contribTotals.new += res[n].new || 0;
        contribTotals.dup += res[n].dup || 0; contribTotals.conflict += res[n].conflict || 0; contribTotals.rejected += res[n].rejected || 0;
      } else if (c.state === 'on' || c.state === 'busy' || c.state === 'wait') requeueHistory(tabId, e);
    });
    if (contribOff()) histByTab.clear();
  }
  saveContrib();
}

// ------------------------------------------------------------------ live prices
// Only the admin's price-feed tab streams every tick. An ordinary account can also stream the live prices of the pairs open in its tab, but the SERVER decides which: it holds one
// account per pair (a lease), so this browser only tells it which pairs it sees ("presence") and sends the ticks of the pairs the answer lists (`send`: pairs it holds, free
// pairs, and a short spot-check now and then). A pair the admin's feed streams is never asked for. Ticks are batched about once a second and are never queued: an old tick is useless.
const SEEN_MS = 15000, PRESENCE_MS = 5000, SEND_MS = 20000, TICKS_MAX = 600, TICK_EVERY = 1000;
const seenSyms = new Map();                        // pair -> when a tick for it was last seen (any tab)
let tickBuf = [], lastPresence = 0, lastTicks = 0;
let feedState = null, feedRetryAt = 0, feedSend = new Set(), feedSendAt = 0, feedHold = [], fedNow = false;
const feedTotals = { applied: 0 };
const feedOff = () => feedState === 'off' || feedState === 'paused' || feedState === 'wait';
const feedWanted = (sym) => feedSend.has(sym) && Date.now() - feedSendAt < SEND_MS && !feedOff() && Date.now() >= feedRetryAt;

/** Take the ticks out of a batch: remember which pairs are seen, keep the ticks of the pairs the server asked for. */
function noteTicks(items) {
  const rest = [], now = Date.now();
  for (const i of items) {
    if (!i || i.k !== 't' || !Array.isArray(i.d)) { rest.push(i); continue; }
    for (const r of i.d) {
      if (!Array.isArray(r) || typeof r[0] !== 'string') continue;
      seenSyms.set(r[0], now);
      if (feedWanted(r[0])) tickBuf.push(r);
    }
  }
  if (tickBuf.length > TICKS_MAX) tickBuf = tickBuf.slice(-TICKS_MAX);
  if (seenSyms.size > 60) for (const [s, t] of seenSyms) if (now - t > SEEN_MS) seenSyms.delete(s);
  return rest;
}
/** Is there something to say about live prices right now (a presence update, or ticks that have waited a second)? */
function feedDue() {
  if (feedState === 'off' || feedState === 'paused') return false;
  const now = Date.now();
  if (now < feedRetryAt) return false;
  const seen = [...seenSyms.values()].some((t) => now - t < SEEN_MS);
  return (seen && now - lastPresence >= PRESENCE_MS) || (tickBuf.length > 0 && now - lastTicks >= TICK_EVERY);
}
/** The presence item and the buffered ticks that go with the next request. */
function takeFeed() {
  fedNow = false;
  const now = Date.now(), out = [];
  if (feedState === 'off' || feedState === 'paused') { tickBuf = []; return out; }
  if (now < feedRetryAt) { tickBuf = []; return out; }
  const syms = [...seenSyms].filter(([, t]) => now - t < SEEN_MS).map(([s]) => s).slice(0, 24);
  if (syms.length && (now - lastPresence >= PRESENCE_MS || feedState === null)) { out.push({ k: 'tp', syms }); lastPresence = now; }
  if (tickBuf.length && now - lastTicks >= TICK_EVERY && feedState === 'on') { out.push({ k: 't', d: tickBuf }); tickBuf = []; lastTicks = now; }
  fedNow = out.length > 0;
  return out;
}
function saveFeed() {
  chrome.storage.session.set({ feed: { state: feedState, retryAt: feedRetryAt, hold: feedHold, send: [...feedSend], totals: feedTotals, at: Date.now() } });
}
/** What the server said about live prices (`j.feed`). An older server does not answer: the extension stops offering. */
function afterFeed(j) {
  const f = j.feed;
  if (!f) { if (fedNow) { feedState = 'off'; feedSend = new Set(); tickBuf = []; saveFeed(); } return; }
  feedState = f.state;
  feedSend = new Set(Array.isArray(f.send) ? f.send : []);
  feedSendAt = Date.now();
  feedHold = Array.isArray(f.hold) ? f.hold : [];
  if (f.state === 'busy') feedRetryAt = Date.now() + Math.max(2, Math.min(f.retry_after || 5, 120)) * 1000;
  feedTotals.applied += f.applied || 0;
  if (feedOff()) tickBuf = [];
  saveFeed();
}

// ------------------------------------------------------------------ which Quotex account this tab is logged into
// hook.js reports the account number it sees in the account's own messages ({k:'a', u}). It is turned into a SHA-256 here and only that leaves the browser; the server ties
// it to the Tradzen account. Orders and balance are held back until the account is known (a hosted server refuses them from an unknown or a different Quotex account).
const ACCT_SALT = 'tradzen-qx-v1:';
const acctByTab = new Map();                       // tabId -> { h, hint }
const held = new Map();                            // tabId -> private items waiting for the account number
let acctLoaded = false, needsLink = null;          // needsLink: null = not known yet (the first answer says), true / false
const isPrivate = (i) => i && (i.k === 'o' || i.k === 'b');
const HOLD_MAX = 300, HOLD_MS = 10 * 60 * 1000;

async function loadAcct() {
  if (acctLoaded) return;
  acctLoaded = true;
  const s = await chrome.storage.session.get(['acctByTab', 'needsLink', 'feeder', 'contrib', 'sentH', 'feed']);
  if (s.feed) { feedState = s.feed.state || null; feedRetryAt = s.feed.retryAt || 0; feedHold = s.feed.hold || []; Object.assign(feedTotals, s.feed.totals || {}); }
  for (const [k, v] of Object.entries(s.acctByTab || {})) acctByTab.set(Number(k), v);
  if (typeof s.needsLink === 'boolean') needsLink = s.needsLink;
  if (isFeeder === null && typeof s.feeder === 'boolean') isFeeder = s.feeder;        // the worker was restarted: what the server said last time still holds
  if (s.contrib) { contribState = s.contrib.state || null; contribRetryAt = s.contrib.retryAt || 0; contribWant = s.contrib.want || []; Object.assign(contribTotals, s.contrib.totals || {}); }
  for (const [k, v] of Object.entries(s.sentH || {})) sentH.set(k, v);
}
function saveAcct() { chrome.storage.session.set({ acctByTab: Object.fromEntries(acctByTab) }); }

async function sha256hex(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Take the account-number items out of a batch and remember the account of this tab; a new page load forgets it until it is seen again. */
async function applyAcct(items, tabId) {
  const rest = [];
  for (const i of items) {
    if (i && i.k === 'a') {
      if (i.u == null) acctByTab.delete(tabId);
      else { const u = String(i.u); acctByTab.set(tabId, { h: await sha256hex(ACCT_SALT + u), hint: u.slice(-3) }); }
      saveAcct();
    } else {
      if (i && i.k === 'ev' && i.e === 'hook-start') { acctByTab.delete(tabId); held.delete(tabId); saveAcct(); }
      rest.push(i);
    }
  }
  return rest;
}

/** Private items wait here while the account is unknown; once it is known they go out first. */
function sortPrivate(items, tabId, known) {
  const mine = held.get(tabId) || [];
  const fresh = mine.filter((x) => Date.now() - x.at < HOLD_MS);
  const priv = items.filter(isPrivate), pub = items.filter((i) => !isPrivate(i));
  if (needsLink === false || known) { held.delete(tabId); return [...fresh.map((x) => x.item), ...items]; }
  if (priv.length) held.set(tabId, [...fresh, ...priv.map((item) => ({ item, at: Date.now() }))].slice(-HOLD_MAX));
  return pub;
}

async function doForward(items, tabId) {
  const cfg = await getSettings();
  if (cfg.forward === false) return;
  await loadAcct();
  items = await applyAcct(items, tabId);
  const acct = acctByTab.get(tabId) || null;
  items = sortPrivate(items, tabId, !!acct);
  let sending = [];                                            // chart history that goes out with this request (see "shared candles")
  if (isFeeder === true) items = [...takeHistory(tabId, true).map((x) => x.item), ...items];      // the server said this is the price feed: what was held goes out as it is
  else if (histReady(tabId)) { sending = takeHistory(tabId); items = [...items, ...sending.map((x) => x.item)]; }
  fedNow = false;
  if (isFeeder === false) items = [...items, ...takeFeed()];       // live prices: presence, and the ticks of the pairs the server asked for
  if (!seqLoaded) { const s = await chrome.storage.session.get('seq'); lastSeq = s.seq ?? null; seqLoaded = true; }
  const conn = await chrome.storage.local.get(['backendUrl', 'token']);
  const url = (conn.backendUrl || DEFAULT_BACKEND).replace(/\/$/, '');
  const headers = { 'Content-Type': 'application/json' };
  if (conn.token) headers.Authorization = 'Bearer ' + conn.token;      // a hosted server needs the personal access token made on the "My account" page
  let ok = false, err = '', j = null;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 8000);          // a hung backend must never stall the sender queue
  const t0 = Date.now();
  try {
    const r = await fetch(url + '/ingest', { method: 'POST', headers, signal: ctl.signal,
                                             body: JSON.stringify({ items, after_seq: lastSeq, qx: acct || undefined }) });
    ok = r.ok; if (!ok) err = r.status === 401 ? 'HTTP 401 - the access token is missing, wrong or revoked' : 'HTTP ' + r.status; else j = await r.json();
  } catch (e) { err = ctl.signal.aborted ? 'timeout after 8 s' : String((e && e.message) || e); }
  finally { clearTimeout(timer); }
  chrome.storage.session.set({ backend: { ok, err, url, at: Date.now() } });
  if (!j) { sending.forEach((e) => requeueHistory(tabId, e)); return; }       // the server could not be reached: try again with the next request
  if (typeof j.seq === 'number') { lastSeq = j.seq; chrome.storage.session.set({ seq: j.seq }); }
  if (typeof j.now === 'number') {                                       // the server's clock against this computer's (at the middle of the round trip): countdowns are shown on the server's clock
    const s = j.now - (t0 + Date.now()) / 2000;
    clockSkew = clockSkew === null ? s : clockSkew * 0.7 + s * 0.3;
  }
  if (typeof j.feeder === 'boolean') isFeeder = j.feeder;
  if (isFeeder === false || sending.length) afterContrib(j, sending, tabId);
  if (isFeeder === false) afterFeed(j);
  needsLink = !!j.qx;                                                    // only a server that ties accounts answers with the state
  chrome.storage.session.set({ needsLink, qx: j.qx ? { ...j.qx, at: Date.now() } : null, who: j.as || null, feeder: !!j.feeder });
  if (j.qx && (j.qx.state === 'mismatch' || j.qx.state === 'taken')) held.delete(tabId);
  if (j.events && j.events.length) await handleEvents(j.events, tabId, cfg);
}

// ------------------------------------------------------------------ alerts
function soundFor(ev) {
  if (ev.kind === 'session') return ev.sound === false ? null : (ev.sound || 'ginfo');            // live session: started / get ready / cancelled / ended
  if (ev.kind === 'guard') return ev.sound === false ? null : ({ success: 'gwin', info: 'ginfo', warn: 'gwarn', danger: 'gstop' }[ev.level] || 'gwarn');
  if (ev.kind === 'signal') return (ev.direction === 'CALL' ? 'call' : 'put') + (ev.tier === 'B' ? 'B' : '');
  return ev.status === 'win' ? 'win' : ev.status === 'loss' ? 'loss' : null;
}

async function handleEvents(events, tabId, cfg) {
  const now = Date.now() / 1000;
  for (const ev of events) {
    if (ev.kind === 'guard' && cfg.guard === false) continue;
    if ((ev.kind === 'session' || ev.session_id != null) && cfg.sessions === false) continue;      // live-session alerts switched off in the popup
    if (ev.tier === 'B' && cfg.tiers === 'A') continue;
    const age = now - ev.t;
    if ((ev.kind === 'signal' && age > 20) || (ev.kind === 'result' && age > 90) || (ev.kind === 'guard' && age > 180) || (ev.kind === 'session' && age > 60)) continue;   // stale (worker was asleep)
    await deliver(ev, tabId, cfg);
  }
}

let clockSkew = null;                       // seconds the server's clock is ahead of this computer's (null until the first answer)
async function deliver(ev, tabId, cfg) {
  const sound = soundFor(ev);
  const wantSound = sound && cfg.sound && (ev.kind === 'signal' || ev.kind === 'guard' || ev.kind === 'session' || cfg.resultSound);
  const playedHere = wantSound ? await playSound(sound, cfg.volume) : true;
  if (tabId != null) {
    chrome.tabs.sendMessage(tabId, { __qxsNotify: ev, settings: cfg, sound, playHere: wantSound && !playedHere, skew: clockSkew }, { frameId: 0 }).catch(() => {});
  }
  if (cfg.desktop && ev.kind === 'signal') desktopNotify(ev, tabId);
  if (cfg.desktop && (ev.kind === 'guard' || ev.kind === 'session')) guardNotify(ev, tabId);
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
    message: ev.session_id != null
      ? `Live session · ${TFN[ev.tf] || ev.tf + 's'}${ev.area ? ' · area ' + ev.area.lo + ' - ' + ev.area.hi : ''}\n${ev.entry_mode === 'current' ? 'Take it NOW (running candle)' : 'Enter at ' + new Date((ev.entry_t || ev.target_t) * 1000).toLocaleTimeString()}${ev.note ? ' · ' + ev.note : ''}`
      : `Tier ${ev.tier} · ${TFN[ev.tf] || ev.tf + 's'} · strength ${ev.strength}\nEnter now — expires ${new Date(ev.expire_t * 1000).toLocaleTimeString()}`,
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
  if (msg.__qxsOpenPanel) {                         // "Join the session" in the on-page dialog: open the side panel beside this tab (the click is the user gesture)
    Promise.resolve().then(() => chrome.sidePanel.open({ tabId: sender.tab.id })).catch(() => { /* no side panel on this browser: the toolbar button opens the board */ });
    return;
  }
  if (msg.__qxs === 2) { forward(msg.items, sender.tab.id); return; }
  if (msg.__qxs !== 1) return;
  const key = `snap:${sender.tab.id}:${sender.frameId}`;
  chrome.storage.session.set({ [key]: msg.snap });
  const n = Object.keys(msg.snap.assets || {}).length;
  chrome.action.setBadgeBackgroundColor({ color: n ? '#16a34a' : '#6b7280', tabId: sender.tab.id });
  chrome.action.setBadgeText({ text: String(n), tabId: sender.tab.id });
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  histByTab.delete(tabId);
  const all = await chrome.storage.session.get(null);
  const dead = Object.keys(all).filter((k) => k.startsWith(`snap:${tabId}:`));
  if (dead.length) chrome.storage.session.remove(dead);
});
