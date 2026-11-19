/* Tradzen sniffer - MAIN-world WebSocket observer.
 *
 * READ-ONLY: wraps window.WebSocket so every frame the page already sends/receives is also summarised here.
 * It never modifies, delays or injects frames, never clicks anything, never trades.
 * Auth/session material is stripped before anything leaves this file (see redact()).
 */
(() => {
  'use strict';
  const root = typeof window !== 'undefined' ? window : globalThis;
  if (root.__qxsInstalled) return;
  root.__qxsInstalled = true;
  const NativeWS = root.WebSocket;
  if (!NativeWS) return;

  // ---------------------------------------------------------------- state
  const fresh = () => ({
    started: Date.now(),
    sockets: [],
    frames: { in: 0, out: 0 },
    bytes: { in: 0, out: 0 },
    events: {},          // "in:name" / "out:name" -> {n, lastMs}
    assets: {},          // symbol -> tick stats
    samples: {},         // event key -> first few redacted payloads
    workers: [],         // Worker/SharedWorker scripts the page starts (a socket inside a worker is invisible to us)
    tickRing: [],        // last raw ticks [symbol, serverTs, price, dir, recvMs]
    histories: {},       // symbol -> what the last history answer held (counts and time range, no prices), to see why a minute could not be built
    instruments: null,   // {count, otc, live, list:[[symbol,name,payout,open]], sample}
    parseErrors: 0,
  });
  let S = fresh();
  let socketSeq = 0;

  // ------------------------------------------------------------ redaction
  const KEY_RE = /(session|token|ssid|^sid$|cookie|e-?mail|pass(word)?|secret|auth|balance|phone|^ip$|country|uid|user|nick|avatar|demo|live_?balance)/i;
  const EMAIL_RE = /[\w.+-]+@[\w-]+\.[\w.-]+/g;
  const LONG_RE = /[A-Za-z0-9_\-.=]{40,}/g;
  const AUTH_EVENT_RE = /(authori|login|session|token|ssid)/i;

  function redact(v, d = 0) {
    if (d > 6) return '[deep]';
    if (typeof v === 'string') return v.replace(EMAIL_RE, '[email]').replace(LONG_RE, '[long-string]').slice(0, 300);
    if (Array.isArray(v)) return v.slice(0, 60).map((x) => redact(x, d + 1));
    if (v && typeof v === 'object') {
      const o = {};
      for (const k of Object.keys(v).slice(0, 60)) o[k] = KEY_RE.test(k) ? '[redacted]' : redact(v[k], d + 1);
      return o;
    }
    return v;
  }

  function cleanUrl(u) {
    try {
      const x = new URL(String(u), root.location && root.location.href);
      return x.origin + x.pathname; // drop ?query (may carry tokens) and #hash
    } catch (_) { return '[bad-url]'; }
  }

  // ------------------------------------------------------------- counting
  function bump(key) {
    const e = S.events[key] || (S.events[key] = { n: 0, lastMs: 0 });
    e.n++; e.lastMs = Date.now();
  }
  function sample(key, payload) {
    const arr = S.samples[key] || (S.samples[key] = []);
    if (arr.length < 4) arr.push(payload);
  }

  function noteTick(r, recvMs) {
    const [sym, ts, price, dir] = r;
    const a = S.assets[sym] || (S.assets[sym] = { n: 0, firstMs: recvMs, lastMs: recvMs, lastTs: 0, lastPrice: null,
                                                  maxGapS: 0, lagSum: 0, lagN: 0 });
    const tsS = ts > 1e11 ? ts / 1000 : ts;
    if (a.lastTs && tsS > a.lastTs) a.maxGapS = Math.max(a.maxGapS, +(tsS - a.lastTs).toFixed(3));
    a.n++; a.lastMs = recvMs; a.lastTs = tsS; a.lastPrice = price;
    a.lagSum += recvMs / 1000 - tsS; a.lagN++;   // local clock minus server tick time (includes clock skew)
    S.tickRing.push([sym, ts, price, dir === undefined ? null : dir, recvMs]);
    if (S.tickRing.length > 200) S.tickRing.shift();
  }

  function extractTicks(p) {
    const out = [];
    const one = (r) => {
      if (Array.isArray(r) && r.length >= 3 && typeof r[0] === 'string' && typeof r[1] === 'number' && typeof r[2] === 'number') out.push(r);
    };
    if (Array.isArray(p)) { if (typeof p[0] === 'string') one(p); else p.forEach(one); }
    return out;
  }

  function looksLikeInstruments(p) {
    return Array.isArray(p) && p.length >= 3 && p.every((r) => Array.isArray(r) && r.length >= 10 &&
      typeof r[1] === 'string' && typeof r[2] === 'string') && /^[A-Za-z0-9_/.\-]+$/.test(p[0][1]);
  }

  function noteInstruments(rows) {
    const list = rows.map((r) => [r[1], String(r[2]).replace(/\n/g, ''), r[5], r[14]]);
    const otc = list.filter((r) => /_otc$/.test(r[0])).length;
    S.instruments = { count: rows.length, otc, live: rows.length - otc, list, sample: redact(rows.slice(0, 3)) };
    // forward: [symbol, name, category, payout, open]
    pushFeed({ k: 'i', list: rows.map((r) => [r[1], String(r[2]).replace(/\n/g, ''), r[3], r[5], !!r[14]]) });
  }

  // ---- market-data feed for the local backend (prices/candles/sentiment only - never auth, balances or orders) ----
  const feed = [];
  let flushQueued = false;
  let bridgeReady = false;      // the ISOLATED-world relay announces itself; posting earlier would be lost
  function flushFeed() {
    flushQueued = false;
    if (bridgeReady && feed.length && typeof root.postMessage === 'function') root.postMessage({ __qxs: 2, items: feed.splice(0) }, '*');
  }
  // Flush on a microtask, NOT a timer: Chrome throttles timers in background tabs (down to 1/min), which would
  // deliver ticks to the backend in late bursts. Microtasks run as soon as the WebSocket message is handled.
  function pushFeed(item) {
    if (feed.length < 5000) feed.push(item);
    if (!flushQueued) { flushQueued = true; Promise.resolve().then(flushFeed); }
  }
  // A history answer ({asset, period, candles, history}) holds server-aggregated candles, which trail real time by a few minutes, AND `history`: the raw
  // [time, price] ticks of those last minutes. Quotex's own chart builds its newest candles from them (the same way a live tick stream is turned into candles).
  // Dropping them left a hole of 2-3 candles between the last aggregated candle and the first candle built from live ticks - right before the running one.
  // Only the buckets NEWER than the last aggregated candle are built, and only from ticks Quotex really sent (nothing is invented). Quotex draws these candles from the
  // very same ticks, so the rules are loose on purpose - a quiet market sends a tick every few seconds and still has a candle on Quotex's chart: the only minute that
  // cannot be built is the FIRST one of the batch when its ticks start mid-minute (its open is unknown, TICK_START_SLACK s); later minutes may open up to TICK_OPEN_SLACK s
  // after their start, and a silence longer than TICK_GAP s inside a minute (or at the end of a finished one) still means ticks were lost, so that minute stays a hole.
  const TICK_START_SLACK = 3, TICK_OPEN_SLACK = 20, TICK_GAP = 45, TICK_MAX = 20000, PERIODS = [60, 300, 900];
  function candlesFromTicks(raw, period, afterT) {
    if (!Array.isArray(raw) || raw.length < 2 || PERIODS.indexOf(period) < 0) return [];
    const pts = [];
    for (const e of raw.length > TICK_MAX ? raw.slice(-TICK_MAX) : raw) {
      let t, p;
      if (Array.isArray(e)) { t = e[0]; p = e[1]; } else if (e && typeof e === 'object') { t = e.time !== undefined ? e.time : e.ts; p = e.price; } else continue;
      if (typeof t !== 'number' || typeof p !== 'number' || !isFinite(t) || !isFinite(p)) continue;
      pts.push([t > 1e11 ? t / 1000 : t, p]);
    }
    pts.sort((a, b) => a[0] - b[0]);
    if (!pts.length) return [];
    const newest = pts[pts.length - 1][0];
    const groups = new Map();
    for (const pt of pts) {
      const b = Math.floor(pt[0] / period) * period;
      if (b <= afterT) continue;
      if (!groups.has(b)) groups.set(b, []);
      groups.get(b).push(pt);
    }
    const out = [];
    const firstB = Math.floor(pts[0][0] / period) * period;                           // the minute the whole batch of ticks starts in
    for (const b of [...groups.keys()].sort((x, y) => x - y)) {
      const g = groups.get(b);
      if (g[0][0] - b > (b === firstB ? TICK_START_SLACK : TICK_OPEN_SLACK)) continue;   // the batch starts mid-candle: its open is unknown
      let whole = true;
      for (let i = 1; i < g.length; i++) if (g[i][0] - g[i - 1][0] > TICK_GAP) { whole = false; break; }
      const last = g[g.length - 1][0];
      if (b + period <= newest && b + period - last > TICK_GAP) whole = false;        // a finished candle must have ticks up to its end
      if (!whole) continue;
      let hi = -Infinity, lo = Infinity;
      for (const pt of g) { if (pt[1] > hi) hi = pt[1]; if (pt[1] < lo) lo = pt[1]; }
      out.push([b, g[0][1], g[g.length - 1][1], hi, lo, g.length, last]);              // time, open, close, high, low, ticks, lastTick
    }
    return out;
  }
  function periodOf(arg, rows) {
    if (typeof arg.period === 'number') return arg.period;
    const cnt = {};
    let best = 0, bestN = 0;
    for (let i = 1; i < rows.length; i++) { const d = Math.abs(rows[i][0] - rows[i - 1][0]); if (d > 0) { cnt[d] = (cnt[d] || 0) + 1; if (cnt[d] > bestN) { bestN = cnt[d]; best = d; } } }
    return best;
  }

  function forwardOther(name, arg) {
    if (arg && typeof arg === 'object' && !Array.isArray(arg) && typeof arg.asset === 'string') {
      let rows = null;
      if (Array.isArray(arg.candles) && arg.candles.length && Array.isArray(arg.candles[0])) {
        rows = arg.candles.map((r) => [r[0], r[1], r[2], r[3], r[4], r[5], r[6]]);        // time, open, close, high, low, ticks, lastTick
      } else if (Array.isArray(arg.data) && arg.data.length && typeof arg.data[0] === 'object' && !Array.isArray(arg.data[0])) {
        rows = arg.data.map((d) => [d.time, d.open, d.close, d.high, d.low, d.ticks, d.last_tick]);
      }
      if (Array.isArray(arg.history) && arg.history.length) {
        const have = rows || [];
        const period = periodOf(arg, have);
        // The aggregated list may end with a stub of the minute that is running (one tick, last tick seconds after its open): it must not hide the minutes before it,
        // so "the last aggregated candle" is the newest one that really ran to its end (no last-tick field = taken as whole).
        const whole = (r) => typeof r[0] === 'number' && !(typeof r[6] === 'number' && period > 0 && r[6] < r[0] + period - TICK_GAP);
        const afterT = have.reduce((m, r) => (whole(r) && r[0] > m ? r[0] : m), -Infinity);
        const extra = candlesFromTicks(arg.history, period, afterT);
        S.histories[arg.asset] = { at: Math.round(Date.now() / 1000), period, aggregated: have.length, aggFrom: have.length ? Math.min(...have.map((r) => r[0])) : null, aggTo: Number.isFinite(afterT) ? afterT : null,
                                   ticks: arg.history.length, built: extra.map((r) => r[0]) };
        if (extra.length) {
          const built = new Set(extra.map((r) => r[0]));
          rows = have.filter((r) => !built.has(r[0])).concat(extra);                  // a stub of a minute the ticks cover is replaced by the candle they build
        }
      }
      if (rows) pushFeed({ k: 'h', asset: arg.asset, rows });
    } else if (name === 'depth/change' && Array.isArray(arg) && arg.length &&
               arg.every((r) => Array.isArray(r) && typeof r[0] === 'string' && typeof r[1] === 'number')) {
      pushFeed({ k: 's', d: arg.map((r) => [r[0], r[1]]) });                               // trader sentiment %
    }
  }

  // ---- your own trades (order opened / closed + result), for the Trades page. Only a short whitelist of fields leaves this file:
  // never balances, account ids, e-mail or session data. Nothing is ever sent TO Quotex from here.
  const ORDER_RE = /order|deal|pending|ticket|trader\/history/i;
  const ORDER_KEYS = new Set(['id', 'ticket', 'asset', 'amount', 'time', 'action', 'command', 'openTime', 'closeTime', 'openTimestamp', 'closeTimestamp',
    'openPrice', 'closePrice', 'profit', 'percentProfit', 'profitPercent', 'status', 'isDemo', 'requestId', 'optionType', 'deals', 'result', 'win',
    'expiration', 'closeReason', 'direction', 'type', 'currency', 'code', 'symbol', 'currencySymbol', 'accountBalance']);
  function orderSafe(v, d = 0) {
    if (d > 4) return null;
    if (Array.isArray(v)) return v.slice(0, 60).map((x) => orderSafe(x, d + 1));
    if (v && typeof v === 'object') {
      const o = {};
      for (const k of Object.keys(v)) {
        if (!ORDER_KEYS.has(k)) continue;
        const x = v[k];
        o[k] = x && typeof x === 'object' ? orderSafe(x, d + 1) : typeof x === 'string' ? x.slice(0, 40) : x;
      }
      o.__keys = Object.keys(v).slice(0, 40);          // field NAMES only, so the backend's parser can be corrected from what Quotex really sends
      return o;
    }
    return typeof v === 'string' ? v.slice(0, 40) : v;
  }

  // ---- account balance + currency, for the balance bar of the admin page (local backend only). Whitelist again: no id, e-mail, name or session.
  const BAL_RE = /balance/i;
  const BAL_KEYS = new Set(['liveBalance', 'demoBalance', 'balance', 'isDemo', 'currency', 'code', 'symbol', 'currencySymbol', 'iso']);
  function balSafe(v, d = 0) {
    if (d > 3) return null;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      const o = {};
      for (const k of Object.keys(v)) if (BAL_KEYS.has(k)) o[k] = v[k] && typeof v[k] === 'object' ? balSafe(v[k], d + 1) : typeof v[k] === 'string' ? v[k].slice(0, 12) : v[k];
      o.__keys = Object.keys(v).slice(0, 40);
      return o;
    }
    return typeof v === 'number' ? v : null;
  }

  // ---- which Quotex account this tab is logged into. The account number is read ONLY from messages about the account's own orders and balance (never from lists of
  // other traders) and goes no further than the extension's background worker, which turns it into a hash. Tradzen uses it to keep one account's trades, balance and alerts
  // from ending up on another Tradzen account; nothing is ever sent to Quotex.
  const OWN_ACCOUNT_RE = /^(s_)?(orders|balance|settings|profile)(\/|$)/;
  const uid = { cur: null, cand: null, candN: 0, sources: {} };
  function noteUid(name, arg) {
    if (!OWN_ACCOUNT_RE.test(name)) return;
    const found = [];
    const walk = (v, d) => {
      if (!v || d > 3) return;
      if (Array.isArray(v)) { v.slice(0, 30).forEach((x) => walk(x, d + 1)); return; }
      if (typeof v !== 'object') return;
      if ((typeof v.uid === 'number' || typeof v.uid === 'string') && /^\d{3,20}$/.test(String(v.uid))) found.push(String(v.uid));
      for (const k of Object.keys(v).slice(0, 40)) if (v[k] && typeof v[k] === 'object') walk(v[k], d + 1);
    };
    walk(arg, 0);
    for (const u of found) {
      uid.sources[name] = (uid.sources[name] || 0) + 1;
      if (u === uid.cur) { uid.cand = null; uid.candN = 0; continue; }
      if (u === uid.cand) uid.candN++; else { uid.cand = u; uid.candN = 1; }
      if (uid.cur === null || uid.candN >= 3) {                  // the first number is taken at once; a different one must repeat before the account counts as switched
        uid.cur = u; uid.cand = null; uid.candN = 0;
        pushFeed({ k: 'a', u });
      }
    }
  }

  // ------------------------------------------------------- frame handling
  function handleEvent(dir, name, args) {
    const key = dir + ':' + name;
    bump(key);
    const recvMs = Date.now();
    let isTick = false;
    if (dir === 'in') for (const arg of args) noteUid(name, arg);
    for (const arg of args) {
      const ticks = dir === 'in' ? extractTicks(arg) : [];
      if (!ticks.length && ORDER_RE.test(name) && !AUTH_EVENT_RE.test(name)) {
        pushFeed({ k: 'o', n: name, dir, d: orderSafe(arg), ts: recvMs / 1000 });
      }
      if (!ticks.length && BAL_RE.test(name) && !AUTH_EVENT_RE.test(name)) pushFeed({ k: 'b', n: name, dir, d: balSafe(arg), ts: recvMs / 1000 });
      if (ticks.length) {
        isTick = true;
        ticks.forEach((t) => noteTick(t, recvMs));
        pushFeed({ k: 't', d: ticks.map((t) => [t[0], t[1], t[2], t[3] === undefined ? 0 : t[3]]) });
      } else if (dir === 'in') {
        if (looksLikeInstruments(arg)) noteInstruments(arg); else forwardOther(name, arg);
      } else if (name === 'instruments/update' && arg && typeof arg.asset === 'string') {
        pushFeed({ k: 'sel', asset: arg.asset, period: arg.period });                       // which pair/timeframe is on screen
        pushFeed({ k: 'ev', e: 'out:' + name, d: arg.asset + '/' + arg.period, ts: Date.now() / 1000 });
      } else if (/follow|unfollow/.test(name)) {
        pushFeed({ k: 'ev', e: 'out:' + name, d: typeof arg === 'string' ? arg : (arg && arg.asset) || '', ts: Date.now() / 1000 });
      }
    }
    if (!isTick) {
      if (AUTH_EVENT_RE.test(name)) sample(key, '[omitted: auth event]');
      else sample(key, redact(args));
    }
  }

  function parseSio(text) {
    const eio = text[0];
    if (eio === '2' || eio === '3') return { kind: 'ping' };
    if (eio === '0') return { kind: 'open' };
    if (eio !== '4') return { kind: 'other' };
    const sio = text[1];
    let i = 2;
    let att = 0;
    const am = /^(\d+)-/.exec(text.slice(i));
    if (am) { att = +am[1]; i += am[0].length; }
    const rest = text.slice(i);
    const start = rest.search(/[[{]/);
    let data = null;
    if (start >= 0) { try { data = JSON.parse(rest.slice(start)); } catch (_) { S.parseErrors++; } }
    return { kind: 'msg', sio, att, data };
  }

  function onText(sock, text, dir) {
    const p = parseSio(text);
    if (p.kind === 'ping') { bump(dir + ':[ping]'); return; }
    if (p.kind === 'open' || p.kind === 'other') { bump(dir + ':[engine-' + p.kind + ']'); return; }
    if (p.sio === '2' && Array.isArray(p.data)) { handleEvent(dir, String(p.data[0]), p.data.slice(1)); return; }
    if (p.sio === '5' && Array.isArray(p.data)) {           // binary event: payload arrives in next frame(s)
      sock['pending' + dir] = { name: String(p.data[0]), left: p.att || 1 };
      bump(dir + ':[binary-header]');
      return;
    }
    bump(dir + ':[sio-' + p.sio + ']');
  }

  function onBinary(sock, bytes, dir) {
    // Quotex binary frames are "<0x04><json>" (engine.io message marker, then the payload): skip control bytes.
    let off = 0;
    while (off < bytes.length && bytes[off] < 0x20) off++;
    if (off) bump(dir + ':[binary-prefix-0x' + bytes[0].toString(16).padStart(2, '0') + ']');
    let text = '';
    try { text = new TextDecoder('utf-8', { fatal: false }).decode(bytes.subarray(off)); } catch (_) { /* ignore */ }
    const pend = sock['pending' + dir];
    let payload;
    const t = text.trimStart();
    if (t[0] === '[' || t[0] === '{') { try { payload = JSON.parse(t); } catch (_) { S.parseErrors++; } }
    if (payload === undefined && /^\d[\d-]*[[{]/.test(t)) { onText(sock, t, dir); return; }
    if (payload === undefined) {
      const head = Array.from(bytes.slice(0, 24)).map((b) => b.toString(16).padStart(2, '0')).join('');
      bump(dir + ':[binary-unparsed]');
      sample(dir + ':[binary-unparsed]', { len: bytes.length, headHex: head });
      return;
    }
    const name = pend ? pend.name : '[binary]';
    if (pend && --pend.left <= 0) sock['pending' + dir] = null;
    handleEvent(dir, name, [payload]);
  }

  async function onFrame(sock, data, dir) {
    try {
      S.frames[dir]++;
      if (typeof data === 'string') { S.bytes[dir] += data.length; onText(sock, data, dir); return; }
      let bytes = null;
      if (data instanceof ArrayBuffer) bytes = new Uint8Array(data);
      else if (ArrayBuffer.isView(data)) bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
      else if (typeof Blob !== 'undefined' && data instanceof Blob) bytes = new Uint8Array(await data.arrayBuffer());
      if (bytes) { S.bytes[dir] += bytes.length; onBinary(sock, bytes, dir); }
    } catch (_) { S.parseErrors++; }
  }

  // ----------------------------------------------------------- the hook
  function attach(ws, url) {
    const sock = { id: ++socketSeq, url: cleanUrl(url), openedAt: Date.now(), state: 'connecting', closeCode: null,
                   msgsIn: 0, msgsOut: 0, pendingin: null, pendingout: null };
    S.sockets.push(sock);
    const ev = (e, d) => pushFeed({ k: 'ev', e, d, ts: Date.now() / 1000 });
    ws.addEventListener('open', () => { sock.state = 'open'; ev('ws-open', sock.id); });
    ws.addEventListener('close', (e) => { sock.state = 'closed'; sock.closeCode = e.code; ev('ws-close', sock.id + ' code ' + e.code); });
    ws.addEventListener('error', () => { sock.state = 'error'; ev('ws-error', sock.id); });
    ws.addEventListener('message', (e) => { sock.msgsIn++; onFrame(sock, e.data, 'in'); });
    const origSend = ws.send;
    ws.send = function (d) {
      try { sock.msgsOut++; onFrame(sock, d, 'out'); } catch (_) { /* never break the page */ }
      return origSend.apply(this, arguments);
    };
  }

  class PatchedWebSocket extends NativeWS {
    constructor(...args) {
      super(...args);
      try { attach(this, args[0]); } catch (_) { /* never break the page */ }
    }
  }
  root.WebSocket = PatchedWebSocket;

  // Passive Worker tracking (records script URLs only; behaviour is unchanged).
  for (const kind of ['Worker', 'SharedWorker']) {
    const Native = root[kind];
    if (!Native) continue;
    root[kind] = class extends Native {
      constructor(...args) {
        super(...args);
        try { S.workers.push({ kind, url: cleanUrl(args[0]) }); if (S.workers.length > 20) S.workers.shift(); } catch (_) { /* ignore */ }
      }
    };
  }
  const installedAt = Date.now();
  try { if (root.console) root.console.info('[qxs] sniffer hook installed in', root.location && root.location.href); } catch (_) { /* ignore */ }

  // ------------------------------------------------------------ snapshots
  function snapshot() {
    const assets = {};
    for (const [k, a] of Object.entries(S.assets).slice(0, 300)) {
      const spanS = Math.max(1, (a.lastMs - a.firstMs) / 1000);
      assets[k] = { n: a.n, perMin: +(a.n / spanS * 60).toFixed(1), lastPrice: a.lastPrice, lastTs: a.lastTs,
                    ageS: +((Date.now() - a.lastMs) / 1000).toFixed(1), maxGapS: a.maxGapS,
                    lagMeanS: a.lagN ? +(a.lagSum / a.lagN).toFixed(3) : null };
    }
    return {
      version: 1, page: root.location ? root.location.origin + root.location.pathname : '',
      isTop: root.top === root, startedAt: S.started, installedAt, now: Date.now(), workers: S.workers,
      sockets: S.sockets.slice(-10), frames: S.frames, bytes: S.bytes, events: S.events, assets,
      instruments: S.instruments, samples: S.samples, tickRing: S.tickRing.slice(-100), histories: S.histories, parseErrors: S.parseErrors,
      account: { known: uid.cur !== null, hint: uid.cur ? uid.cur.slice(-3) : null, sources: uid.sources },      // event NAMES the account number was seen in (never the number)
    };
  }

  root.__qxs = { snapshot, reset: () => { S = fresh(); }, _redact: redact, drainFeed: () => feed.splice(0) };

  // Page lifecycle: explains feed pauses (tab hidden / throttled / frozen by Chrome's Memory Saver).
  const doc = root.document;
  if (doc && typeof doc.addEventListener === 'function') {
    pushFeed({ k: 'ev', e: 'hook-start', d: doc.visibilityState, ts: Date.now() / 1000 });
    doc.addEventListener('visibilitychange', () => pushFeed({ k: 'ev', e: 'visibility', d: doc.visibilityState, ts: Date.now() / 1000 }));
    ['freeze', 'resume'].forEach((t) => doc.addEventListener(t, () => pushFeed({ k: 'ev', e: t, ts: Date.now() / 1000 })));
  }

  if (typeof root.addEventListener === 'function') {
    root.addEventListener('message', (e) => {
      if (e.source !== root || !e.data) return;
      if (e.data.__qxsCmd === 'reset') S = fresh();
      if (e.data.__qxsReady) { bridgeReady = true; flushFeed(); }
    });
    if (typeof root.postMessage === 'function') root.postMessage({ __qxsPing: 1 }, '*');   // in case the relay loaded first
  }
  if (typeof setInterval === 'function' && typeof root.postMessage === 'function') {
    // Heartbeat even with zero sockets, so the popup can tell "hook not running" from "no socket opened".
    setInterval(() => { root.postMessage({ __qxs: 1, snap: snapshot() }, '*'); }, 2000);
    setInterval(flushFeed, 2000);                                  // safety net only
  }
})();
