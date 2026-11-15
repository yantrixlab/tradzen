// Offline test for hook.js: feeds socket.io-style frames through a mock WebSocket. Run: node test_hook.js
const vm = require('vm');
const fs = require('fs');
const assert = require('assert');

class MockWS {
  constructor(url) { this.url = url; this.l = {}; this.sent = []; }
  addEventListener(t, f) { (this.l[t] = this.l[t] || []).push(f); }
  send(d) { this.sent.push(d); }
  emit(t, ev) { (this.l[t] || []).forEach((f) => f(ev)); }
}

class MockWorker { constructor(url) { this.url = url; } }
const win = { WebSocket: MockWS, Worker: MockWorker, location: { origin: 'https://qxbroker.com', pathname: '/en/trade', href: 'https://qxbroker.com/en/trade' } };
win.top = win;
const docL = {};
win.document = { visibilityState: 'visible', addEventListener(t, f) { (docL[t] = docL[t] || []).push(f); } };
const ctx = vm.createContext({
  window: win, globalThis: win, TextDecoder, URL, Uint8Array, ArrayBuffer, Blob, setInterval: () => 0, console,
});
ctx.globalThis = win;
vm.runInContext(fs.readFileSync(__dirname + '/hook.js', 'utf8'), ctx);

const WS = win.WebSocket;
const ws = new WS('wss://ws2.qxbroker.com/socket.io/?token=SECRETSECRETSECRETSECRETSECRETSECRET123');
assert(ws instanceof MockWS, 'patched socket must still be an instance of the native class');

// Real Quotex binary frames are 0x04 + JSON (seen in the live sample: "045b5b..." = 0x04 "[[").
const enc = (o) => Uint8Array.from([0x04, ...new TextEncoder().encode(JSON.stringify(o))]);
const text = (s) => ws.emit('message', { data: s });
const bin = (o) => ws.emit('message', { data: enc(o) });

// engine/socket.io housekeeping
text('0{"sid":"abc","pingInterval":25000}');
text('2'); text('40');
// plain text event with a token inside (must be redacted)
text('42["s_authorization",{"session":"SECRETSECRETSECRETSECRETSECRETSECRET123"}]');
text('42["settings/list",{"email":"me@example.com","balance":1234.5,"assets":[1,2]}]');
// binary event: header then payload
text('451-["quotes/stream",{"_placeholder":true,"num":0}]');
bin([['EURUSD_otc', 1700000000.5, 1.0834, 0]]);
text('451-["quotes/stream",{"_placeholder":true,"num":0}]');
bin([['EURUSD_otc', 1700000001.0, 1.0835, 1]]);
text('451-["quotes/stream",{"_placeholder":true,"num":0}]');
bin([['USDBRL_otc', 1700000001.2, 5.1, 0]]);
// bare tick as text event
text('42["tick",["GBPJPY",1700000002,190.12,0]]');
// instruments list (rows of >=10 columns)
const row = (id, sym, name) => [id, sym, name, 0, 0, 85, 0, 0, 0, 0, 0, 0, 0, 0, true];
text('451-["instruments/list",{"_placeholder":true,"num":0}]');
bin([row(1, 'EURUSD', 'EUR/USD'), row(2, 'EURUSD_otc', 'EUR/USD (OTC)'), row(3, 'USDBRL_otc', 'USD/BRL (OTC)')]);
// balance message arrives as a headerless-looking binary frame in real traffic: must be redacted
text('451-["s_balance",{"_placeholder":true,"num":0}]');
bin({ liveBalance: 0, demoBalance: 98765.43 });
// outgoing: auth must be omitted, subscribe kept
ws.send('42["authorization",{"session":"SECRETSECRETSECRETSECRETSECRETSECRET123","isDemo":1}]');
ws.send('42["instruments/follow",{"asset":"EURUSD_otc","period":60}]');
assert.strictEqual(ws.sent.length, 2, 'send must pass through untouched');
// history (candle rows are [time, open, close, high, low, ticks, lastTick]) and sentiment
text('451-["history/list/v2",{"_placeholder":true,"num":0}]');
bin({ asset: 'EURUSD_otc', candles: [[1700000040, 1.0, 1.1, 1.2, 0.9, 5, 1700000099.5], [1700000100, 1.1, 1.05, 1.15, 1.0, 7, 1700000150.2]] });
text('451-["history/load",{"_placeholder":true,"num":0}]');
bin({ asset: 'EURUSD_otc', data: [{ time: 1700000000, open: 1.0, close: 1.01, high: 1.02, low: 0.99, ticks: 9, last_tick: 1700000059.1, symbol_id: 70 }] });
text('451-["depth/change",{"_placeholder":true,"num":0}]');
bin([['EURUSD_otc', 61]]);
ws.send('42["instruments/update",{"asset":"EURUSD_otc","period":300}]');
ws.send('42["depth/follow","AUDNZD_otc"]');
win.document.visibilityState = 'hidden'; (docL.visibilitychange || []).forEach((f) => f());
(docL.freeze || []).forEach((f) => f());
// unparseable binary
ws.emit('message', { data: new Uint8Array([0xde, 0xad, 0xbe, 0xef]) });
ws.emit('open', {});

const snap = win.__qxs.snapshot();
const json = JSON.stringify(snap);

assert.strictEqual(snap.sockets[0].url, 'wss://ws2.qxbroker.com/socket.io/', 'query string (token) must be dropped');
assert.strictEqual(snap.sockets[0].state, 'open');
assert(!json.includes('SECRETSECRET'), 'no secrets in snapshot');
assert(!json.includes('me@example.com') && !json.includes('1234.5'), 'email/balance redacted');
assert(!json.includes('98765'), 'binary balance redacted');
assert(snap.events['in:[binary-prefix-0x04]'].n >= 5, 'prefix byte is recognised');
assert.strictEqual(snap.assets.EURUSD_otc.n, 2);
assert.strictEqual(snap.assets.USDBRL_otc.n, 1);
assert.strictEqual(snap.assets.GBPJPY.n, 1);
assert.strictEqual(snap.assets.EURUSD_otc.lastPrice, 1.0835);
assert.strictEqual(snap.instruments.count, 3);
assert.strictEqual(snap.instruments.otc, 2);
assert.strictEqual(snap.instruments.live, 1);
assert.strictEqual(JSON.stringify(snap.instruments.list[1]), '["EURUSD_otc","EUR/USD (OTC)",85,true]');
assert.strictEqual(snap.events['in:quotes/stream'].n, 3);
assert.strictEqual(snap.events['out:authorization'].n, 1);
assert.strictEqual(snap.samples['out:authorization'][0], '[omitted: auth event]');
assert.strictEqual(snap.samples['out:instruments/follow'][0][0].asset, 'EURUSD_otc');
assert(snap.events['in:[binary-unparsed]'], 'unparseable binary is counted, not fatal');
assert.strictEqual(snap.tickRing.length, 4);
assert.strictEqual(snap.parseErrors, 0);

// feed for the backend: market data only
const feedAll = JSON.parse(JSON.stringify(win.__qxs.drainFeed()));
// the balance message is forwarded as live / demo numbers only (for the admin balance bar), nothing else of it
const bal = feedAll.filter((i) => i.k === 'b');
assert.strictEqual(bal.length, 1); assert.strictEqual(bal[0].d.demoBalance, 98765.43); assert.strictEqual(bal[0].d.liveBalance, 0);
const feed = feedAll.filter((i) => i.k !== 'b');
const ticks = feed.filter((i) => i.k === 't').flatMap((i) => i.d);
assert.strictEqual(ticks.length, 4);
assert.strictEqual(JSON.stringify(ticks[0]), '["EURUSD_otc",1700000000.5,1.0834,0]');
const inst = feed.find((i) => i.k === 'i');
assert.strictEqual(inst.list.length, 3);
assert.strictEqual(JSON.stringify(inst.list[1]), '["EURUSD_otc","EUR/USD (OTC)",0,85,true]'.replace(',0,85', ',0,85'));
const hist = feed.filter((i) => i.k === 'h');
assert.strictEqual(hist.length, 2);
assert.strictEqual(hist[0].rows.length, 2);
assert.strictEqual(JSON.stringify(hist[1].rows[0]), '[1700000000,1,1.01,1.02,0.99,9,1700000059.1]');
assert.strictEqual(JSON.stringify(feed.find((i) => i.k === 's').d), '[["EURUSD_otc",61]]');
assert.strictEqual(JSON.stringify(feed.find((i) => i.k === 'sel')), '{"k":"sel","asset":"EURUSD_otc","period":300}');
const evs = feed.filter((i) => i.k === 'ev').map((i) => i.e + (i.d ? ':' + i.d : ''));
['hook-start:visible', 'ws-open:1', 'out:instruments/update:EURUSD_otc/300', 'out:depth/follow:AUDNZD_otc', 'visibility:hidden', 'freeze']
  .forEach((x) => assert(evs.includes(x), 'missing lifecycle event ' + x + ' in ' + evs));
assert(!JSON.stringify(feed).match(/SECRET|session|balance|98765|me@example/i), 'feed must carry market data only');

win.__qxs.reset();
assert.strictEqual(Object.keys(win.__qxs.snapshot().assets).length, 0);

// your own trades: order events are forwarded with a whitelist of fields only (no balance / account / session data)
win.__qxs.drainFeed();
ws.send('42["orders/open",{"asset":"AUDUSD_otc","amount":1,"time":1700000060,"action":"call","isDemo":0,"requestId":7,"tournamentId":0}]');
text('451-["s_orders/open",{"_placeholder":true,"num":0}]');
bin({ id: 'abc-1', asset: 'AUDUSD_otc', amount: 1, command: 0, openTimestamp: 1700000000, closeTimestamp: 1700000060, isDemo: 0, balance: 4242.42, uid: 99887, email: 'me@example.com' });
text('451-["s_orders/close",{"_placeholder":true,"num":0}]');
bin({ deals: [{ id: 'abc-1', profit: 0.83, openPrice: 0.6774, closePrice: 0.6776, liveBalance: 5555.55, nickname: 'zed' }] });
ws.send('42["authorization",{"session":"SECRETSECRETSECRETSECRETSECRETSECRET123","isDemo":1}]');
const drained = JSON.parse(JSON.stringify(win.__qxs.drainFeed()));
const orders = drained.filter((i) => i.k === 'o');
assert.strictEqual(orders.length, 3, 'two answers and one request, nothing from the auth event');
assert.strictEqual(orders[0].dir, 'out'); assert.strictEqual(orders[0].d.requestId, 7); assert.strictEqual(orders[0].d.action, 'call');
assert.strictEqual(orders[1].n, 's_orders/open'); assert.strictEqual(orders[1].d.id, 'abc-1'); assert.strictEqual(orders[1].d.isDemo, 0);
assert.strictEqual(orders[2].d.deals[0].profit, 0.83);
assert(!/99887|zed|me@example|SECRET/.test(JSON.stringify(orders)), 'no balance, account or session values in the order feed');

// which Quotex account this is: the number is read from the account's own order / balance messages only, reported once, and a different one must repeat before it counts
assert.deepStrictEqual(drained.filter((i) => i.k === 'a'), [{ k: 'a', u: '99887' }], 'the first account number is reported once, as its own item');
const snapA = JSON.parse(JSON.stringify(win.__qxs.snapshot().account));
assert.strictEqual(snapA.known, true); assert.strictEqual(snapA.hint, '887');
assert(snapA.sources['s_orders/open'] >= 1, 'the popup learns which messages carried it');
assert(!JSON.stringify(win.__qxs.snapshot()).includes('99887'), 'the page snapshot never holds the whole number');
text('451-["leaderboard/list",{"_placeholder":true,"num":0}]');
bin({ top: [{ uid: 111222, nickname: 'someone else' }] });
text('451-["s_orders/close",{"_placeholder":true,"num":0}]');
bin({ deals: [{ id: 'abc-2', uid: 99887, profit: 0.5 }] });
assert.strictEqual(win.__qxs.drainFeed().filter((i) => i.k === 'a').length, 0, 'other traders\' numbers are ignored and the same number is not reported again');
for (let n = 0; n < 2; n++) { text('451-["s_balance",{"_placeholder":true,"num":0}]'); bin({ uid: 555666, liveBalance: 1 }); }
assert.strictEqual(win.__qxs.drainFeed().filter((i) => i.k === 'a').length, 0, 'two sightings of another number are not enough to call it a switch');
text('451-["s_balance",{"_placeholder":true,"num":0}]'); bin({ uid: 555666, liveBalance: 1 });
assert.strictEqual(JSON.stringify(win.__qxs.drainFeed().filter((i) => i.k === 'a')), '[{"k":"a","u":"555666"}]', 'the third sighting switches the account');

// heartbeat with no sockets + worker tracking
const w = new win.Worker('https://qxbroker.com/js/chart.worker.js?v=1');
assert(w instanceof MockWorker && w.url.includes('?v=1'), 'worker must be created normally');
const s2 = win.__qxs.snapshot();
assert.strictEqual(s2.sockets.length, 0);
assert.strictEqual(s2.workers.length, 1);
assert.strictEqual(s2.workers[0].url, 'https://qxbroker.com/js/chart.worker.js');
assert(s2.installedAt > 0);
console.log('all hook tests passed');
