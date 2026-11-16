// Offline test for the account handling in background.js: private items wait until the Quotex account is known, only a SHA-256 of it is sent, and a server that
// does not tie accounts (a local one) gets everything at once. Run: node test_background.js
const vm = require('vm');
const fs = require('fs');
const crypto = require('crypto');
const assert = require('assert');

function makeStore() {
  const d = {};
  return {
    d,
    get: async (k) => (k == null ? { ...d } : Array.isArray(k) ? Object.fromEntries(k.filter((x) => x in d).map((x) => [x, d[x]])) : (k in d ? { [k]: d[k] } : {})),
    set: async (o) => { Object.assign(d, o); },
    remove: async (k) => { [].concat(k).forEach((x) => delete d[x]); },
  };
}

function boot(serverAnswer) {
  const sent = [];
  const local = makeStore(), session = makeStore();
  let onMessage = null;
  const delivered = [];
  const opened = [];
  const noop = () => {};
  const chrome = {
    storage: { local, session },
    runtime: { onMessage: { addListener: (f) => { onMessage = f; } }, getContexts: async () => [], sendMessage: async () => {} },
    tabs: { onRemoved: { addListener: noop }, sendMessage: async (id, msg) => { delivered.push(msg); }, get: async () => ({}) },
    notifications: { onClicked: { addListener: noop }, create: noop, clear: noop },
    action: { setBadgeBackgroundColor: noop, setBadgeText: noop },
    windows: { get: async () => ({}) },
    sidePanel: { open: async (o) => { opened.push(o); } },
  };
  const fetch = async (url, init) => { const body = JSON.parse(init.body); sent.push({ url, body }); return { ok: true, status: 200, json: async () => serverAnswer(sent.length, body) }; };
  const ctx = vm.createContext({ chrome, fetch, crypto: crypto.webcrypto, TextEncoder, Uint8Array, AbortController, setTimeout, clearTimeout, Date, Promise, Object, JSON, Array, Math, Number, Map, String, console });
  vm.runInContext(fs.readFileSync(__dirname + '/background.js', 'utf8'), ctx);
  const send = (items, tabId = 7) => onMessage({ __qxs: 2, items }, { tab: { id: tabId }, frameId: 0 });
  const until = async (n) => { for (let i = 0; i < 200 && sent.length < n; i++) await new Promise((r) => setTimeout(r, 5)); assert(sent.length >= n, `expected ${n} requests, saw ${sent.length}`); };
  return { sent, send, until, session, delivered, local, opened, message: (m, tabId = 7) => onMessage(m, { tab: { id: tabId }, frameId: 0 }) };
}

const order = (id) => ({ k: 'o', n: 's_orders/close', dir: 'in', d: { deals: [{ id }] }, ts: 1 });
const bal = { k: 'b', n: 's_balance', dir: 'in', d: { liveBalance: 5 }, ts: 1 };
const tick = { k: 't', d: [['EURUSD', 1, 1.1, 0]] };
const kinds = (req) => req.body.items.map((i) => i.k);

(async () => {
  // ---- a server that ties accounts (answers with the link state)
  let h = boot(() => ({ ok: true, seq: 1, events: [], feeder: false, qx: { state: 'unknown', hint: null }, as: { username: 'anna', role: 'trader' } }));
  h.send([{ k: 'ev', e: 'hook-start', d: 'visible' }, order('D1'), bal, tick]);
  await h.until(1);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(kinds(h.sent[0]))), ['ev', 't'], 'orders and balance wait for the account (the first batch still carries prices: the server has not said yet that this is not the price feed)');
  assert.strictEqual(h.sent[0].body.qx, undefined, 'no account known yet');
  h.send([order('D2'), { k: 'a', u: '4711234' }]);
  await h.until(2);
  const r2 = h.sent[1].body;
  assert.deepStrictEqual(JSON.parse(JSON.stringify(r2.items.map((i) => (i.d && i.d.deals ? i.d.deals[0].id : i.k)))), ['D1', 'b', 'D2'], 'the held items go first, in order, then the new one');
  assert.strictEqual(r2.qx.h, crypto.createHash('sha256').update('tradzen-qx-v1:4711234').digest('hex'), 'only a SHA-256 of the number is sent');
  assert.strictEqual(r2.qx.hint, '234');
  assert(!JSON.stringify(h.sent).includes('4711234'), 'the account number itself never leaves the extension');
  assert(!r2.items.some((i) => i.k === 'a'), 'the account item itself is not forwarded');
  // a new page load forgets the account until it is seen again
  h.send([{ k: 'ev', e: 'hook-start', d: 'visible' }, order('D3')]);
  await h.until(3);
  assert.strictEqual(h.sent[2].body.qx, undefined);
  assert(!kinds(h.sent[2]).includes('o'), 'D3 waits again');
  // another tab has its own account
  h.send([order('T9'), { k: 'a', u: '777888' }], 8);
  await h.until(4);
  assert.strictEqual(h.sent[3].body.qx.hint, '888');

  // ---- a server that does not tie accounts (local): nothing is held after the first answer
  h = boot(() => ({ ok: true, seq: 1, events: [], feeder: true }));
  h.send([tick]);
  await h.until(1);
  h.send([order('L1'), bal]);
  await h.until(2);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(kinds(h.sent[1]))), ['o', 'b'], 'a server without account ties gets orders at once');
  // ---- shared candles: an ordinary account offers its chart history, once, when the Quotex account is known
  const hist = (asset, t0, n = 300) => ({ k: 'h', asset, rows: Array.from({ length: n }, (_, i) => [t0 + i * 60, 1.1, 1.1, 1.1, 1.1, 5, t0 + i * 60 + 59]) });
  const histOf = (req) => req.body.items.filter((i) => i.k === 'h');
  const answers = [];                                                      // scripted contrib answers, one per request
  h = boot((n) => ({ ok: true, seq: n, events: [], feeder: false, qx: { state: 'ok', hint: '234' }, contrib: answers[n - 1] || { state: 'on', results: [], want: [] } }));
  answers.push({ state: 'wait' });
  h.send([{ k: 'ev', e: 'hook-start', d: 'visible' }, hist('EURUSD', 1000000)]);
  await h.until(1);
  assert.deepStrictEqual(histOf(h.sent[0]).length, 0, 'before the server has said what this account is, nothing but the account items go (the history waits)');
  answers.push({ state: 'on', want: [{ asset: 'GBPUSD', tf: 60, kind: 'gap', from: 1, to: 2, missing: 3 }],
                 results: [{ asset: 'EURUSD', received: 300, new: 280, dup: 20, conflict: 0, rejected: 0, done: true }] });
  h.send([{ k: 'a', u: '4711234' }]);
  await h.until(2);
  assert.strictEqual(histOf(h.sent[1]).length, 1, 'once the Quotex account is known the held history is offered');
  assert.strictEqual(histOf(h.sent[1])[0].rows.length, 300);
  assert(h.sent[1].body.qx && h.sent[1].body.qx.hint === '234');
  await new Promise((r) => setTimeout(r, 20));
  let st = (await h.session.get('contrib')).contrib;
  assert.strictEqual(st.state, 'on');
  assert.strictEqual(st.totals.new, 280);
  assert.strictEqual(st.totals.dup, 20);
  assert.strictEqual(st.want[0].asset, 'GBPUSD', 'the wanted ranges are kept for the popup');
  // the same history again (a page reload) is not sent twice; a different one is
  answers.push({ state: 'on', results: [] });                              // (the answer to request 3)
  h.send([order('D9'), hist('EURUSD', 1000000)]);
  await h.until(3);
  assert.strictEqual(histOf(h.sent[2]).length, 0, 'the server has this range');
  answers.push({ state: 'busy', retry_after: 30, results: [] });          // (request 4)
  h.send([order('D10'), hist('GBPUSD', 2000000)]);
  await h.until(4);
  assert.strictEqual(histOf(h.sent[3]).length, 1);
  await new Promise((r) => setTimeout(r, 20));
  st = (await h.session.get('contrib')).contrib;
  assert.strictEqual(st.state, 'busy');
  assert.strictEqual(st.queued, 1, 'the refused item waits, it is not lost');
  assert(st.retryAt > Date.now() + 20000);
  h.send([order('D11')]);
  await h.until(5);
  assert.strictEqual(histOf(h.sent[4]).length, 0, 'told to slow down: nothing goes out until the time is up (even though the next answer says "on")');
  // paused by the admin: whatever waits is dropped and new history is not even kept
  h = boot((n) => ({ ok: true, seq: n, events: [], feeder: false, qx: { state: 'ok', hint: '234' }, contrib: { state: 'paused' } }));
  h.send([{ k: 'a', u: '4711234' }, order('P1')]);
  await h.until(1);
  await new Promise((r) => setTimeout(r, 20));
  h.send([order('P2'), hist('EURUSD', 1000000)]);
  await h.until(2);
  h.send([order('P3'), hist('EURUSD', 3000000)]);
  await h.until(3);
  assert.strictEqual(histOf(h.sent[1]).length + histOf(h.sent[2]).length, 0);
  assert.strictEqual((await h.session.get('contrib')).contrib.state, 'paused');

  // ---- the price feed's history is never held: only until the server has said it is the feed
  h = boot((n) => ({ ok: true, seq: n, events: [], feeder: true }));
  h.send([{ k: 'ev', e: 'hook-start', d: 'visible' }, hist('EURUSD', 1000000, 10), tick]);
  await h.until(1);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(kinds(h.sent[0]))), ['ev', 't'], 'the very first batch: the role is not known yet, history waits one answer');
  h.send([tick]);
  await h.until(2);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(kinds(h.sent[1]))), ['h', 't'], 'told it is the price feed: the held history goes out as it is');
  h.send([hist('EURUSD', 4000000, 10)]);
  await h.until(3);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(kinds(h.sent[2]))), ['h'], 'and from then on it is sent at once');

  // ---- a server that does not know about contributions (older): the extension stops offering
  h = boot((n) => ({ ok: true, seq: n, events: [], feeder: false, qx: { state: 'ok', hint: '234' } }));
  h.send([{ k: 'a', u: '4711234' }, hist('EURUSD', 1000000)]);
  await h.until(1);
  await new Promise((r) => setTimeout(r, 20));
  h.send([order('O1'), hist('GBPUSD', 1000000)]);
  await h.until(2);
  assert.strictEqual(histOf(h.sent[1]).length, 0, 'an old server ignores history from ordinary accounts, so it is not sent');
  assert.strictEqual((await h.session.get('contrib')).contrib.state, 'off');
  // ---- live prices: an ordinary account says which pairs it sees and sends the ticks of the pairs the server asks for (and only those), about once a second
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const tickOf = (sym, ts, p = 1.1) => ({ k: 't', d: [[sym, ts, p, 0]] });
  const ticksOf = (req) => req.body.items.filter((i) => i.k === 't');
  const feedAnswers = [];
  h = boot((n) => ({ ok: true, seq: n, events: [], feeder: false, qx: { state: 'ok', hint: '234' }, feed: feedAnswers[n - 1] || { state: 'on', send: ['EURUSD'], hold: [], applied: 0 } }));
  h.send([{ k: 'ev', e: 'hook-start', d: 'visible' }, tickOf('EURUSD', 1)]);
  await h.until(1);
  await sleep(20);
  h.send([{ k: 't', d: [['EURUSD', 2, 1.1, 0], ['GBPUSD', 2, 1.3, 0]] }]);
  await h.until(2);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(kinds(h.sent[1]))), ['tp', 't'], 'a presence item, and the ticks of the pair the server asked for');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(h.sent[1].body.items[0].syms)), ['EURUSD', 'GBPUSD'], 'presence lists every pair this tab sees');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(ticksOf(h.sent[1])[0].d.map((r) => r[0]))), ['EURUSD'], 'a pair nobody asked for is not sent');
  h.send([tickOf('EURUSD', 3)]);
  await sleep(60);
  assert.strictEqual(h.sent.length, 2, 'ticks are batched: nothing goes out again within a second');
  await sleep(1000);
  h.send([tickOf('EURUSD', 4)]);
  await h.until(3);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(kinds(h.sent[2]))), ['t'], 'presence is repeated only every few seconds');
  assert.strictEqual(ticksOf(h.sent[2])[0].d.length, 2, 'the buffered tick and the new one go together');
  assert.strictEqual((await h.session.get('feed')).feed.state, 'on');
  // told to slow down: nothing is sent, and nothing piles up
  feedAnswers[3] = { state: 'busy', retry_after: 30, send: ['EURUSD'], hold: [] };
  await sleep(1100);
  h.send([tickOf('EURUSD', 5)]);
  await h.until(4);
  await sleep(30);
  assert.strictEqual((await h.session.get('feed')).feed.state, 'busy');
  assert(JSON.parse(JSON.stringify((await h.session.get('feed')).feed.retryAt)) > Date.now() + 20000);
  h.send([tickOf('EURUSD', 6)]);
  await sleep(1100);
  h.send([tickOf('EURUSD', 7)]);
  await sleep(30);
  assert(h.sent.slice(4).every((r) => !ticksOf(r).length), 'no ticks while the server said to wait');
  // an old server (no live-price answer): the extension stops offering
  h = boot((n) => ({ ok: true, seq: n, events: [], feeder: false, qx: { state: 'ok', hint: '234' } }));
  h.send([{ k: 'ev', e: 'hook-start', d: 'visible' }, tickOf('EURUSD', 1)]);
  await h.until(1);
  await sleep(20);
  h.send([tickOf('EURUSD', 2)]);
  await h.until(2);
  await sleep(30);
  assert.strictEqual((await h.session.get('feed')).feed.state, 'off');
  const n0 = h.sent.length;
  h.send([tickOf('EURUSD', 3)]);
  await sleep(60);
  assert.strictEqual(h.sent.length, n0, 'an old server gets no more presence or ticks');
  // which pair is open on the account's own Quotex tab goes to the server (its chart follows it); it is not held back like orders
  h = boot((n) => ({ ok: true, seq: n, events: [], feeder: false, qx: { state: 'ok', hint: '234' } }));
  h.send([{ k: 'a', u: '4711234' }, { k: 'sel', asset: 'EURUSD_otc', period: 60 }]);
  await h.until(1);
  assert(h.sent[0].body.items.some((i) => i.k === 'sel' && i.asset === 'EURUSD_otc' && i.period === 60), 'the open pair is sent by an ordinary account');
  // the price feed itself is untouched: every tick goes out, no presence item
  h = boot((n) => ({ ok: true, seq: n, events: [], feeder: true }));
  h.send([tickOf('EURUSD', 1)]);
  await h.until(1);
  await sleep(20);
  h.send([{ k: 't', d: [['EURUSD', 2, 1.1, 0], ['GBPUSD', 2, 1.3, 0]] }]);
  await h.until(2);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(kinds(h.sent[1]))), ['t']);
  assert.strictEqual(ticksOf(h.sent[1])[0].d.length, 2);
  // ---- live sessions: heads-up / signal / result cards and sounds reach the tab, can be switched off, and an old one is not shown late
  const nowS = Date.now() / 1000;
  const sessEvents = [
    { seq: 1, kind: 'session', aud: 'session', session_id: 3, stage: 'headsup', level: 'warn', title: 'Get ready', message: 'CALL', sound: 'shead', id: 'ss1', t: nowS },
    { seq: 2, kind: 'signal', aud: 'session', session_id: 3, id: 's9', direction: 'CALL', tier: 'A', tf: 60, name: 'EUR/USD', strength: 100, expire_t: nowS + 120, target_t: nowS + 60, t: nowS },
    { seq: 3, kind: 'result', aud: 'session', session_id: 3, id: 's9', status: 'win', direction: 'CALL', t: nowS },
    { seq: 4, kind: 'session', aud: 'session', session_id: 3, stage: 'ended', title: 'Live session ended', message: 'x', sound: 'send', id: 'ss4', t: nowS - 600 },
  ];
  let given = false;
  h = boot((n) => { const evs = given ? [] : sessEvents; given = true; return { ok: true, seq: 9, events: evs, feeder: false, qx: { state: 'ok', hint: '234' } }; });
  h.send([{ k: 'a', u: '4711234' }]);
  await h.until(1);
  await new Promise((r) => setTimeout(r, 60));
  const got = h.delivered.map((m) => m.__qxsNotify.kind + ':' + (m.sound || ''));
  assert.deepStrictEqual(got, ['session:shead', 'signal:call', 'result:win'], 'the session alerts reach the tab with their sounds; the ten-minute-old one is dropped as stale');
  h = boot(() => ({ ok: true, seq: 9, events: sessEvents.slice(0, 3).map((e) => ({ ...e, t: Date.now() / 1000 })), feeder: false, qx: { state: 'ok', hint: '234' } }));
  await h.local.set({ sessions: false });
  h.send([{ k: 'a', u: '4711234' }]);
  await h.until(1);
  await new Promise((r) => setTimeout(r, 60));
  assert.strictEqual(h.delivered.length, 0, 'with live session alerts switched off nothing is shown');
  // ---- the "Join the session" button of the on-page dialog opens the side panel of that tab (only for a message that comes from a tab)
  h = boot(() => ({ ok: true, seq: 1, events: [], feeder: false }));
  h.message({ __qxsOpenPanel: true }, 12);
  await new Promise((r) => setTimeout(r, 20));
  assert.deepStrictEqual(JSON.parse(JSON.stringify(h.opened)), [{ tabId: 12 }], 'the panel opens beside the tab that asked');
  // a started / ended session event is delivered to the tab like any other (the page turns it into a dialog), a running-candle signal too
  const sdone = Date.now() / 1000;
  h = boot(() => ({ ok: true, seq: 9, now: sdone + 5, events: [
    { seq: 1, kind: 'session', aud: 'session', session_id: 4, stage: 'started', level: 'info', title: 'Live session started', message: 'Evening', sound: 'sstart', id: 'ss1', t: sdone, by: 1, session_title: 'Evening' },
    { seq: 2, kind: 'signal', aud: 'session', session_id: 4, id: 's5', direction: 'PUT', entry_mode: 'current', tier: 'A', tf: 60, name: 'EUR/USD', strength: 100, expire_t: sdone + 50, target_t: sdone - 5, t: sdone },
    { seq: 3, kind: 'session', aud: 'session', session_id: 4, stage: 'ended', level: 'info', title: 'Live session ended', message: 'x', sound: 'send', id: 'ss3', t: sdone, result: { wins: 2, losses: 1, total: 3, net: 0.6 } }], feeder: false, qx: { state: 'ok', hint: '234' } }));
  h.send([{ k: 'a', u: '4711234' }]);
  await h.until(1);
  await new Promise((r) => setTimeout(r, 60));
  assert.deepStrictEqual(h.delivered.map((m) => m.__qxsNotify.kind + ':' + (m.__qxsNotify.stage || m.__qxsNotify.entry_mode)), ['session:started', 'signal:current', 'session:ended']);
  assert.deepStrictEqual(h.delivered.map((m) => m.sound), ['sstart', 'put', 'send']);
  assert(h.delivered.every((m) => m.skew > 4 && m.skew < 6), 'every card carries the server-clock skew (the server is 5 s ahead here), so its countdown is on the server clock');
  console.log('all background tests passed');
})().catch((e) => { console.error(e); process.exit(1); });
