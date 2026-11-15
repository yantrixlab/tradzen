/* Tradzen on-page alert cards (ISOLATED content script, top frame, Shadow DOM so Quotex CSS cannot touch it).
 * Receives {__qxsNotify: event, settings} from the background worker. Read-only: it never clicks or trades.
 */
(() => {
  if (window.__qxsNotifyLoaded) return;
  window.__qxsNotifyLoaded = true;

  const CSS = `
  :host { all: initial; }
  * { box-sizing: border-box; }
  .stack { position: fixed; z-index: 2147483647; display: flex; flex-direction: column; gap: 10px; width: 316px; max-width: calc(100vw - 24px);
           pointer-events: none; font: 13px/1.4 "Inter", "Segoe UI", system-ui, -apple-system, sans-serif; color: #e9ecf8; }
  .stack.tc { top: 76px; left: 50%; transform: translateX(-50%); }
  .stack.tr { top: 76px; right: 16px; } .stack.br { bottom: 16px; right: 16px; flex-direction: column-reverse; }
  .stack.bl { bottom: 16px; left: 76px; flex-direction: column-reverse; }
  .card { pointer-events: auto; position: relative; overflow: hidden; border-radius: 18px; --c: #22c55e; --cs: rgba(34,197,94,.16);
          background: linear-gradient(180deg, rgba(24,31,58,.98), rgba(11,15,32,.98)); border: 1px solid rgba(255,255,255,.09);
          box-shadow: 0 22px 60px rgba(0,0,0,.6), 0 0 0 1px rgba(0,0,0,.45), 0 0 46px var(--cs);
          animation: in .42s cubic-bezier(.18,.9,.28,1.15) both; }
  .card.put { --c: #ef4444; --cs: rgba(239,68,68,.16); }
  .card.out { animation: out .28s ease-in both; }
  .card.win { --c: #22c55e; } .card.loss { --c: #ef4444; } .card.draw { --c: #94a3b8; --cs: rgba(148,163,184,.1); }
  .accent { height: 3px; background: linear-gradient(90deg, var(--c), transparent 85%); }
  .pad { padding: 8px 14px 10px; }
  .top { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .brand { font-size: 10px; font-weight: 700; letter-spacing: .12em; color: #9aa3c4; }
  .tier { font-size: 10px; font-weight: 800; letter-spacing: .08em; padding: 2px 7px; border-radius: 999px; border: 1px solid; }
  .tier.A { color: #c4b5fd; border-color: rgba(139,92,246,.55); background: rgba(139,92,246,.16); }
  .tier.B { color: #fbbf24; border-color: rgba(245,158,11,.5); background: rgba(245,158,11,.12); }
  .live { margin-left: auto; display: flex; align-items: center; gap: 6px; font-size: 10.5px; color: #9aa3c4; letter-spacing: .05em; }
  .live i { width: 7px; height: 7px; border-radius: 50%; background: var(--c); box-shadow: 0 0 0 0 var(--c); animation: pulse 1.6s infinite; }
  .x { all: unset; cursor: pointer; width: 22px; height: 22px; border-radius: 6px; display: grid; place-items: center; color: #8089ad; font-size: 16px; line-height: 1; }
  .x:hover { background: rgba(255,255,255,.08); color: #fff; }
  .main { display: flex; align-items: center; gap: 11px; }
  .arrow { flex: none; width: 42px; height: 42px; border-radius: 13px; display: grid; place-items: center; font-size: 20px; font-weight: 900; color: #fff;
           background: linear-gradient(145deg, var(--c), color-mix(in srgb, var(--c) 55%, #000)); box-shadow: 0 8px 24px var(--cs), inset 0 1px 0 rgba(255,255,255,.25); }
  .dirwrap { min-width: 0; flex: 1; }
  .dir { font-size: 20px; font-weight: 900; letter-spacing: .02em; color: var(--c); line-height: 1.1; }
  .sub { margin-top: 2px; color: #aab2d0; font-size: 11px; white-space: nowrap; }
  .sub b { color: #fff; }
  .ring { flex: none; position: relative; width: 50px; height: 50px; }
  .ring svg { width: 50px; height: 50px; transform: rotate(-90deg); }
  .ring .bg { stroke: rgba(255,255,255,.09); } .ring .fg { stroke: var(--c); stroke-linecap: round; transition: stroke-dashoffset .25s linear; }
  .ring .cd { position: absolute; inset: 0; display: grid; place-items: center; font-weight: 800; font-size: 12.5px; font-variant-numeric: tabular-nums; }
  .pair { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin: 9px 0 6px; }
  .pair .name { font-size: 15px; font-weight: 800; letter-spacing: .01em; color: #fff; }
  .meta { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; min-width: 0; }
  .meta .chip.strat { flex: 0 1 auto; min-width: 0; max-width: 170px; }
  .meta .badge { margin-left: auto; }
  .more { all: unset; box-sizing: border-box; cursor: pointer; width: 100%; display: flex; align-items: center; justify-content: center; gap: 6px; padding: 4px 0 2px;
          border-top: 1px solid rgba(255,255,255,.07); color: #8089ad; font-size: 11px; font-weight: 700; letter-spacing: .04em; }
  .more:hover { color: #fff; } .more:focus-visible { outline: 2px solid #8b5cf6; outline-offset: 2px; border-radius: 6px; }
  .more .car { display: inline-block; transition: transform .2s; } .card.open .more .car { transform: rotate(180deg); }
  .details { display: none; padding-top: 8px; } .card.open .details { display: block; }
  .chip { font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 999px; background: rgba(255,255,255,.07); color: #c5cbe6; border: 1px solid rgba(255,255,255,.07); }
  .chip.pay { color: #86efac; background: rgba(34,197,94,.1); border-color: rgba(34,197,94,.25); }
  .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 10px; }
  .m { background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.06); border-radius: 11px; padding: 7px 9px; }
  .m .l { font-size: 9.5px; letter-spacing: .09em; color: #7f88ac; font-weight: 700; }
  .m .v { font-size: 14px; font-weight: 800; margin-top: 1px; font-variant-numeric: tabular-nums; }
  .bar { height: 6px; border-radius: 4px; background: rgba(255,255,255,.08); overflow: hidden; margin-top: 5px; }
  .bar i { display: block; height: 100%; border-radius: 4px; background: linear-gradient(90deg, color-mix(in srgb, var(--c) 60%, #000), var(--c)); }
  .acc { display: flex; align-items: center; gap: 10px; border-radius: 12px; padding: 9px 11px; margin-bottom: 10px; background: rgba(255,255,255,.035); border: 1px solid rgba(255,255,255,.07); }
  .acc .t { flex: 1; min-width: 0; } .acc .l { font-size: 9.5px; letter-spacing: .09em; color: #7f88ac; font-weight: 700; }
  .acc .v { font-size: 13px; font-weight: 700; margin-top: 1px; } .acc .v small { font-weight: 500; color: #9aa3c4; }
  .badge { flex: none; font-size: 10px; font-weight: 800; letter-spacing: .05em; padding: 4px 8px; border-radius: 8px; white-space: nowrap; }
  .badge.good { color: #86efac; background: rgba(34,197,94,.14); } .badge.bad { color: #fca5a5; background: rgba(239,68,68,.14); }
  .badge.mid { color: #cbd5e1; background: rgba(148,163,184,.14); } .badge.wait { color: #fcd34d; background: rgba(245,158,11,.14); }
  .chip.strat { color: #c4b5fd; background: rgba(139,92,246,.12); border-color: rgba(139,92,246,.3); max-width: 190px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .also { font-size: 11px; color: #9aa3c4; margin: -2px 0 10px; } .also b { color: #cbd2ee; font-weight: 700; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
  .chips .f { font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 8px; background: rgba(255,255,255,.05); color: #cbd2ee; }
  .chips .f.up { color: #86efac; } .chips .f.dn { color: #fca5a5; }
  .foot { display: flex; justify-content: space-between; gap: 8px; font-size: 10.5px; color: #6f7899; }
  .result { display: none; align-items: center; gap: 12px; margin-bottom: 12px; padding: 12px 14px; border-radius: 14px; background: color-mix(in srgb, var(--c) 16%, transparent);
            border: 1px solid color-mix(in srgb, var(--c) 45%, transparent); }
  .card.win .result, .card.loss .result, .card.draw .result { display: flex; }
  .card.win .main, .card.loss .main, .card.draw .main, .card.win .grid, .card.loss .grid, .card.draw .grid, .card.win .chips, .card.loss .chips, .card.draw .chips,
  .card.win .meta, .card.loss .meta, .card.draw .meta { display: none; }
  .result .big { font-size: 22px; font-weight: 900; color: var(--c); letter-spacing: .03em; white-space: nowrap; flex: none; }
  .result .det { color: #cbd2ee; font-size: 12px; flex: 1; min-width: 0; }
  /* trading guard card */
  .card.g-success { --c: #22c55e; --cs: rgba(34,197,94,.16); } .card.g-info { --c: #38bdf8; --cs: rgba(56,189,248,.14); }
  .card.g-warn { --c: #f59e0b; --cs: rgba(245,158,11,.16); } .card.g-danger { --c: #ef4444; --cs: rgba(239,68,68,.2); }
  .card.g-danger { animation: in .42s cubic-bezier(.18,.9,.28,1.15) both, shake .5s .45s 1; }
  .lvl { font-size: 10px; font-weight: 800; letter-spacing: .08em; padding: 2px 7px; border-radius: 999px; border: 1px solid color-mix(in srgb, var(--c) 55%, transparent); color: var(--c); background: color-mix(in srgb, var(--c) 14%, transparent); }
  .gmain { display: flex; gap: 11px; align-items: flex-start; margin: 4px 0 8px; }
  .gicon { flex: none; width: 40px; height: 40px; border-radius: 13px; display: grid; place-items: center; font-size: 20px; font-weight: 900; color: #fff;
           background: linear-gradient(145deg, var(--c), color-mix(in srgb, var(--c) 55%, #000)); box-shadow: 0 8px 24px var(--cs), inset 0 1px 0 rgba(255,255,255,.25); }
  .gtitle { font-size: 15px; font-weight: 800; line-height: 1.25; color: #fff; } .gmsg { margin-top: 3px; font-size: 12px; line-height: 1.45; color: #b9c0dc; }
  .gchips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; } .gchips .m { padding: 5px 9px; } .gchips .m .v { font-size: 13px; }
  .life { position: absolute; left: 0; bottom: 0; height: 3px; background: var(--c); opacity: .8; transform-origin: left; }
  @keyframes shake { 20%, 60% { transform: translateX(-4px); } 40%, 80% { transform: translateX(4px); } }
  @keyframes in { from { opacity: 0; transform: translateY(-18px) scale(.94); } to { opacity: 1; transform: none; } }
  @keyframes out { to { opacity: 0; transform: translateY(-12px) scale(.96); } }
  @keyframes pulse { 70% { box-shadow: 0 0 0 7px transparent; } 100% { box-shadow: 0 0 0 0 transparent; } }
  @media (prefers-reduced-motion: reduce) { .card, .live i { animation: none; } }
  `;

  const SHORT = { 'Bollinger Band Bounce': 'Bollinger', 'RSI Divergence': 'RSI divergence', 'Stochastic %K/%D Cross': 'Stochastic',
                  'CCI Extreme + Reversal': 'CCI', 'Candlestick Pattern': 'Candle pattern' };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const hms = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const price = (p) => (p == null ? '–' : Math.abs(p) >= 1000 ? p.toFixed(2) : Math.abs(p) >= 10 ? p.toFixed(3) : p.toFixed(5));
  const pct = (x) => (x * 100).toFixed(1) + '%';
  const TFN = { 60: '1 min', 300: '5 min', 900: '15 min' };
  const C = 2 * Math.PI * 25;

  let host = null, root = null, stack = null, timer = null, settings = {}, detailsOpen = false;
  const cards = new Map();   // signal id -> {el, ev, offset, dismissAt}

  function mount() {
    if (host && host.isConnected) return;
    host = document.createElement('div');
    host.setAttribute('data-qxs', 'notify');
    root = host.attachShadow({ mode: 'closed' });
    try {                                   // constructable stylesheet: not subject to the page's CSP
      const sheet = new CSSStyleSheet(); sheet.replaceSync(CSS); root.adoptedStyleSheets = [sheet];
    } catch (_) { const st = document.createElement('style'); st.textContent = CSS; root.appendChild(st); }
    stack = document.createElement('div'); stack.className = 'stack tc'; stack.setAttribute('aria-live', 'assertive'); root.appendChild(stack);
    (document.documentElement || document.body).appendChild(host);
  }

  function accuracy(ev) {
    const t = ev.tier_stats || { n: 0 }, be = ev.breakeven, p = ev.pair_stats || { n: 0 };
    const pairLine = p.n ? `This pair & timeframe: ${p.wins}W / ${p.losses}L` : 'No settled signals for this pair yet';
    if (!t.n) return { value: 'No settled signals yet', sub: pairLine, badge: 'BUILDING SAMPLE', cls: 'wait' };
    const base = `<b>${pct(t.acc)}</b> <small>· n=${t.n}${t.lo != null ? ` · 95% ${pct(t.lo)}–${pct(t.hi)}` : ''}</small>`;
    let badge = 'BUILDING SAMPLE', cls = 'wait';
    if (t.n >= 30 && be != null) {
      if (t.lo > be) { badge = 'ABOVE BREAK-EVEN'; cls = 'good'; }
      else if (t.hi < be) { badge = 'BELOW BREAK-EVEN'; cls = 'bad'; }
      else { badge = 'NEAR BREAK-EVEN'; cls = 'mid'; }
    }
    return { value: base + (be != null ? ` <small>· break-even ${pct(be)}</small>` : ''), sub: pairLine, badge, cls };
  }

  function signalHtml(ev) {
    const call = ev.direction === 'CALL';
    const acc = accuracy(ev);
    const also = (ev.also && ev.also.length)
      ? `<div class="also">Also agreeing: ${ev.also.slice(0, 3).map((a) => `<b>${esc(a.name)}</b> (${esc(a.tier)}·${a.strength})`).join(' · ')}${ev.also.length > 3 ? ` +${ev.also.length - 3}` : ''}</div>` : '';
    const chips = (ev.factors || []).map((f) => `<span class="f ${f.vote > 0 ? 'up' : 'dn'}" title="${esc(f.note)}">${esc(SHORT[f.name] || f.name)} ${f.vote > 0 ? '▲' : '▼'}</span>`).join('');
    return `
      <div class="accent"></div>
      <div class="pad">
        <div class="top"><span class="brand">⚡ TRADZEN</span><span class="tier ${esc(ev.tier)}">TIER ${esc(ev.tier)}</span>
          <span class="live"><i></i>LIVE</span><button class="x" title="Dismiss" aria-label="Dismiss">×</button></div>
        <div class="main">
          <div class="arrow">${call ? '▲' : '▼'}</div>
          <div class="dirwrap"><div class="dir">${call ? 'BUY / CALL' : 'SELL / PUT'}</div>
            <div class="sub"><b>Enter now</b> · until ${hms(ev.expire_t)}</div></div>
          <div class="ring"><svg viewBox="0 0 56 56"><circle class="bg" cx="28" cy="28" r="25" fill="none" stroke-width="4"/>
            <circle class="fg" cx="28" cy="28" r="25" fill="none" stroke-width="4" stroke-dasharray="${C}" stroke-dashoffset="0"/></svg><div class="cd">–</div></div>
        </div>
        <div class="result"><div class="big"></div><div class="det"></div></div>
        <div class="pair"><span class="name">${esc(ev.name)}</span>${ev.payout ? `<span class="chip pay">${ev.payout}% payout</span>` : ''}
          <span class="chip">${esc(TFN[ev.tf] || ev.tf + 's')}</span></div>
        <div class="meta">${ev.strategy ? `<span class="chip strat" title="Strategy">${esc(ev.strategy)}</span>` : ''}<span class="badge mini ${acc.cls}" title="Measured accuracy of this tier: open Details">${acc.badge}</span></div>
        <button class="more" aria-expanded="false"><span class="lbl">Details</span><span class="car">▾</span></button>
        <div class="details">
        <div class="grid">
          <div class="m"><div class="l">STRENGTH</div><div class="v">${ev.strength}<span style="color:#7f88ac;font-weight:600">/100</span></div><div class="bar"><i style="width:${Math.min(100, ev.strength)}%"></i></div></div>
          <div class="m"><div class="l">CONFLUENCE</div><div class="v">${ev.agree} factors</div></div>
          <div class="m"><div class="l">ENTRY PRICE</div><div class="v">${price(ev.price != null ? ev.price : ev.entry)}</div></div>
        </div>
        <div class="acc"><div class="t"><div class="l">MEASURED ACCURACY · TIER ${esc(ev.tier)}</div><div class="v">${acc.value}</div><div class="v"><small>${esc(acc.sub)}</small></div></div></div>
        <div class="chips">${chips}</div>${also}
        <div class="foot"><span>Strength is a heuristic, not a win probability.</span><span>Signal ${hms(ev.target_t)}</span></div>
        </div>
      </div>
      <div class="life"></div>`;
  }

  // Details (strength, confluence, entry price, measured accuracy, factors) are folded away by default; the choice is remembered.
  function paintDetails(el) {
    el.classList.toggle('open', detailsOpen);
    const b = el.querySelector('.more');
    b.setAttribute('aria-expanded', String(detailsOpen));
    b.querySelector('.lbl').textContent = detailsOpen ? 'Hide details' : 'Details';
  }
  function setDetails(open) {
    detailsOpen = open;
    for (const c of cards.values()) paintDetails(c.el);
    try { chrome.storage.local.set({ cardDetailsOpen: open }); } catch (_) { /* storage unavailable: keep it for this page only */ }
  }
  try { chrome.storage.local.get('cardDetailsOpen').then((c) => { if (c && c.cardDetailsOpen) setDetails(true); }).catch(() => {}); } catch (_) { /* ignore */ }

  function dismiss(id) {
    const c = cards.get(id);
    if (!c) return;
    cards.delete(id);
    c.el.classList.add('out');
    setTimeout(() => c.el.remove(), 280);
  }

  function addSignal(ev) {
    mount();
    stack.className = 'stack ' + ({ 'top-right': 'tr', 'bottom-right': 'br', 'bottom-left': 'bl' }[settings.position] || 'tc');
    if (cards.has(ev.id)) return;
    const el = document.createElement('div');
    el.className = 'card ' + (ev.direction === 'CALL' ? 'call' : 'put');
    el.setAttribute('role', 'alert');
    el.innerHTML = signalHtml(ev);
    el.querySelector('.x').addEventListener('click', () => dismiss(ev.id));
    el.querySelector('.more').addEventListener('click', () => setDetails(!detailsOpen));
    paintDetails(el);
    stack.prepend(el);
    const offset = ev.now ? ev.now - Date.now() / 1000 : 0;
    cards.set(ev.id, { el, ev, offset, dismissAt: ev.expire_t + 25 });
    const sk = [...cards.keys()].filter((k) => !cards.get(k).guard);
    while (sk.length > 3) dismiss(sk.shift());
    start();
    tick();
  }

  function addResult(ev) {
    mount();
    let c = cards.get(ev.id);
    if (!c) {                                         // card was dismissed or never shown: show a compact result
      addSignal({ ...ev, kind: 'signal', price: ev.entry });
      c = cards.get(ev.id);
      if (!c) return;
    }
    const up = ev.exit > ev.entry;
    c.el.classList.remove('call', 'put');
    c.el.classList.add(ev.status);
    const big = ev.status === 'win' ? 'WIN ✓' : ev.status === 'loss' ? 'LOSS ✕' : 'DRAW';
    c.el.querySelector('.result .big').textContent = big;
    const profit = ev.status === 'win' && ev.payout ? ` · +${ev.payout}% on stake` : '';
    c.el.querySelector('.result .det').textContent =
      `${ev.direction === 'CALL' ? 'BUY' : 'SELL'} settled: price ${price(ev.entry)} → ${price(ev.exit)} (${up ? '▲' : '▼'})${profit}`;
    c.el.querySelector('.live').innerHTML = '<i></i>SETTLED';
    const acc = accuracy(ev);
    c.el.querySelector('.acc .v').innerHTML = acc.value;
    c.el.querySelectorAll('.badge').forEach((b) => { b.className = 'badge ' + acc.cls + (b.classList.contains('mini') ? ' mini' : ''); b.textContent = acc.badge; });
    c.dismissAt = Date.now() / 1000 + 10;
    c.settled = true;
  }

  // ---- trading guard alerts (daily target / loss limit / streak / overtrading): a card like the signal cards, staying longer when it matters
  const GL = { success: ['✓', 'TARGET', 22], info: ['ℹ', 'HEADS-UP', 14], warn: ['!', 'WARNING', 30], danger: ['⛔', 'STOP', 60] };
  function addGuard(ev) {
    mount();
    stack.className = 'stack ' + ({ 'top-right': 'tr', 'bottom-right': 'br', 'bottom-left': 'bl' }[settings.position] || 'tc');
    const key = 'g' + ev.id;
    if (cards.has(key)) return;
    const [icon, label, secs] = GL[ev.level] || GL.warn;
    const chips = (ev.chips || []).slice(0, 4).map((x) => `<div class="m"><div class="l">${esc(String(x.l).toUpperCase())}</div><div class="v">${esc(x.v)}</div></div>`).join('');
    const prog = ev.progress != null ? `<div class="bar"><i style="width:${Math.max(3, Math.min(100, ev.progress * 100))}%"></i></div>` : '';
    const el = document.createElement('div');
    el.className = 'card g-' + (GL[ev.level] ? ev.level : 'warn');
    el.setAttribute('role', 'alert');
    el.innerHTML = `<div class="accent"></div><div class="pad">
      <div class="top"><span class="brand">🛡 TRADING GUARD</span><span class="lvl">${label}</span><span class="live"></span><button class="x" title="Dismiss" aria-label="Dismiss">×</button></div>
      <div class="gmain"><div class="gicon">${icon}</div><div><div class="gtitle">${esc(ev.title)}</div><div class="gmsg">${esc(ev.message)}</div></div></div>
      ${chips ? `<div class="gchips">${chips}</div>` : ''}${prog}</div><div class="life"></div>`;
    el.querySelector('.x').addEventListener('click', () => dismiss(key));
    stack.prepend(el);
    cards.set(key, { el, ev, guard: true, dismissAt: Date.now() / 1000 + secs, secs });
    const gk = [...cards.keys()].filter((k) => cards.get(k).guard);
    while (gk.length > 3) dismiss(gk.shift());
    start();
    tick();
  }

  function tick() {
    const now = Date.now() / 1000;
    for (const [id, c] of cards) {
      if (c.guard) {
        const left = Math.max(0, c.dismissAt - now);
        const lf = c.el.querySelector('.life'); lf.style.display = ''; lf.style.width = Math.min(100, (left / c.secs) * 100) + '%';
        if (now > c.dismissAt) dismiss(id);
        continue;
      }
      if (!c.settled) {
        const rem = Math.max(0, c.ev.expire_t - (now + c.offset));
        const mm = Math.floor(rem / 60), ss = Math.floor(rem % 60);
        c.el.querySelector('.cd').textContent = rem <= 0 ? '…' : `${mm}:${String(ss).padStart(2, '0')}`;
        c.el.querySelector('.fg').setAttribute('stroke-dashoffset', String(C * (1 - Math.min(1, rem / c.ev.tf))));
        c.el.querySelector('.life').style.display = 'none';
      } else {
        const left = Math.max(0, c.dismissAt - now);
        const lf = c.el.querySelector('.life'); lf.style.display = ''; lf.style.width = Math.min(100, left * 10) + '%';
      }
      if (now > c.dismissAt) dismiss(id);
    }
    if (!cards.size) stop();
  }
  function start() { if (!timer) timer = setInterval(tick, 250); }
  function stop() { clearInterval(timer); timer = null; }

  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg || !msg.__qxsNotify) return;
    settings = msg.settings || settings;
    if (msg.playHere && window.QXSound && settings.sound !== false) window.QXSound.play(msg.sound || 'call', settings.volume);
    if (settings.alerts === false) return;
    const ev = msg.__qxsNotify;
    if (ev.kind === 'signal') addSignal(ev); else if (ev.kind === 'result') addResult(ev); else if (ev.kind === 'guard' && settings.guard !== false) addGuard(ev);
  });
})();
