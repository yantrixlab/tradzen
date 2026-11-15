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
  const noop = () => {};
  const chrome = {
    storage: { local, session },
    runtime: { onMessage: { addListener: (f) => { onMessage = f; } }, getContexts: async () => [], sendMessage: async () => {} },
    tabs: { onRemoved: { addListener: noop }, sendMessage: async () => {}, get: async () => ({}) },
    notifications: { onClicked: { addListener: noop }, create: noop, clear: noop },
    action: { setBadgeBackgroundColor: noop, setBadgeText: noop },
    windows: { get: async () => ({}) },
  };
  const fetch = async (url, init) => { sent.push({ url, body: JSON.parse(init.body) }); return { ok: true, status: 200, json: async () => serverAnswer(sent.length) }; };
  const ctx = vm.createContext({ chrome, fetch, crypto: crypto.webcrypto, TextEncoder, Uint8Array, AbortController, setTimeout, clearTimeout, Date, Promise, Object, JSON, Array, Math, Number, Map, String, console });
  vm.runInContext(fs.readFileSync(__dirname + '/background.js', 'utf8'), ctx);
  const send = (items, tabId = 7) => onMessage({ __qxs: 2, items }, { tab: { id: tabId }, frameId: 0 });
  const until = async (n) => { for (let i = 0; i < 200 && sent.length < n; i++) await new Promise((r) => setTimeout(r, 5)); assert(sent.length >= n, `expected ${n} requests, saw ${sent.length}`); };
  return { sent, send, until, session };
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
  console.log('all background tests passed');
})().catch((e) => { console.error(e); process.exit(1); });
