import { ReactNode, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { pct, prettyPair, tfName, verdict } from '../format';
import { streamingCount, useLiveState, usePersisted, useStrategies } from '../hooks';
import { Badge, Card, Empty, ErrorBox, Spinner, StreamBubble } from '../components/ui';

// ------------------------------------------------------------------ API shapes
interface StratRow {
  id: number; key: string; name: string; status: string; meta: boolean; direction: 'CALL' | 'PUT' | 'SKIP'; tier: string; strength: number; reason: string;
  why: string[]; weight: number; supports: boolean; opposes: boolean;
  track: { settled: number; wins: number; accuracy: number | null; ci_low: number | null; ci_high: number | null };
}
interface CalRow { agree: number; label: string; settled: number; wins: number; accuracy: number | null; ci_low: number | null; ci_high: number | null }
interface AiRule { rule: string; side: 'follow' | 'fade'; n_train: number; n_valid: number; acc_valid: number | null; lo: number | null; hi: number | null; verdict: 'proven' | 'weak' | 'none' }
interface AiInfo {
  model: { rows: number; train: number; valid: number; span: [number, number]; base_follow: number; z: number; breakeven: number; built: number; leaves: AiRule[] } | null;
  read: { origin: { t: number; price: number; direction: 'UP' | 'DOWN'; candles_ago: number }; state: Record<string, number>; leg: 'UP' | 'DOWN'; trade: 'CALL' | 'PUT';
    leaf: AiRule & { wins_valid: number } } | null;
}
const AI_LABEL: Record<string, string> = {
  leg_len: 'Candles since the initial point', move_atr: 'Distance travelled from it (ATR)', eff: 'Move efficiency (net ÷ path)', body_share: 'Share of leg candles going with it',
  pullback_atr: 'Pullback from the leg\'s extreme (ATR)', accel: 'Acceleration, last 3 vs previous 3 candles (ATR)', last_body_atr: 'Last candle body with the leg (ATR)', adx: 'ADX',
};
interface Generated {
  symbol: string; name: string; market: string; tf: number; payout: number | null; breakeven: number | null; now: number; entry_t: number; expiry_t: number;
  seconds_to_entry: number; late: boolean; replay: boolean; price: number; candles: number;
  verdict: 'CALL' | 'PUT' | 'NO TRADE'; grade: 'A' | 'B' | 'C' | null; conviction: number; headline: string; blocked: string | null;
  counts: { calls: number; puts: number; neutral: number; considered: number }; agree: number; against: number;
  mtf: { tf: number; calls: number; puts: number; lean: 'CALL' | 'PUT' | null; note: string }[]; mtf_opposed: boolean; warnings: string[];
  strategies: StratRow[]; calibration: { rows: CalRow[]; breakeven: number | null }; calibration_here: CalRow | null;
  outcome: { status: string; entry: number; exit: number } | null; trade_id: number | null; ai?: AiInfo;
}

function AiPanel({ r, tz }: { r: Generated; tz: number }) {
  const [all, setAll] = useState(false);
  const m = r.ai?.model, rd = r.ai?.read;
  if (!m) return <Card title="AI-built strategy"><Empty>The AI could not build a strategy yet: {r.blocked ?? 'not enough data'}.</Empty></Card>;
  const be = r.breakeven ?? m.breakeven;                       // this pair's own break-even (from its payout), the same number the grade used
  const L = rd?.leaf;
  const tone = L?.verdict === 'proven' ? 'up' : L?.verdict === 'weak' ? 'warn' : 'mut';
  return (
    <div className="space-y-4">
      {rd && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="1 · Where the momentum started">
            <div className="text-lg"><b className={rd.origin.direction === 'UP' ? 'text-up' : 'text-down'}>{rd.origin.direction === 'UP' ? '▲ Up-leg' : '▼ Down-leg'}</b> began <b>{rd.origin.candles_ago}</b> candle{rd.origin.candles_ago === 1 ? '' : 's'} ago</div>
            <div className="note mt-1">at {rd.origin.price} · {clock(rd.origin.t, tz, true)} - the newest confirmed swing {rd.origin.direction === 'UP' ? 'low' : 'high'}. Every measurement below is taken from that point.</div>
            <table className="mt-3 w-full text-sm"><tbody>{Object.entries(rd.state).map(([k, v]) => (
              <tr key={k} className="border-t border-line"><td className="py-1 text-mut">{AI_LABEL[k] ?? k}</td><td className="py-1 text-right font-semibold tabular-nums">{v.toFixed(2)}</td></tr>))}</tbody></table>
          </Card>
          <Card title="2 · The strategy the AI built from it">
            <div className="text-sm">Rule: <b>{L?.rule}</b></div>
            <div className="mt-2 text-lg">→ <b>{L?.side === 'follow' ? 'Follow' : 'Fade'}</b> the {rd.leg === 'UP' ? 'up' : 'down'}-leg: <b className={r.verdict === 'CALL' ? 'text-up' : r.verdict === 'PUT' ? 'text-down' : ''}>{rd.trade}</b></div>
            <div className="note mt-1">Learned from {m.train.toLocaleString()} past momentum situations across all stored pairs (the older 70% of {m.rows.toLocaleString()}). No preset strategy is involved.</div>
            <div className="mt-3 rounded-lg border border-line bg-panel2 p-3 text-sm">
              <div className="flex items-center justify-between"><b>Its own test on {L?.n_valid.toLocaleString()} unseen candles</b><Badge tone={tone}>{L?.verdict === 'proven' ? 'EDGE PROVEN' : L?.verdict === 'weak' ? 'ABOVE BREAK-EVEN, NOT PROVEN' : 'NO EDGE FOUND'}</Badge></div>
              <div className="mt-1">Won <b>{pct(L?.acc_valid ?? null, 1)}</b> <span className="note">(95% range {pct(L?.lo ?? null, 1)}–{pct(L?.hi ?? null, 1)}, widened for the {m.leaves.length} rules tried) · break-even {pct(be, 1)}</span></div>
              <div className="note mt-1">{L?.verdict === 'none' ? 'The test did not beat break-even, so this trade is the AI\'s best lean - not a confirmed edge. Treat it as a coin flip with a cost.' : L?.verdict === 'weak' ? 'Above break-even on the unseen candles, but the range still includes break-even.' : 'Above break-even with the range clear of it, on candles the AI never saw.'}</div>
            </div>
          </Card>
        </div>
      )}
      <Card title={`All ${m.leaves.length} rules the AI learned (tested on the newest ${m.valid.toLocaleString()} candles)`}>
        <button className="note hover:text-fg" onClick={() => setAll(!all)}>{all ? '▾ Hide' : '▸ Show'} the rules</button>
        {all && <table className="mt-2 w-full text-sm"><thead><tr className="text-left text-mut"><th className="py-1">Momentum situation</th><th>Call</th><th>Unseen candles</th><th>Won</th><th /></tr></thead>
          <tbody>{m.leaves.map((x) => (
            <tr key={x.rule} className={`border-t border-line align-top ${rd && x.rule === L?.rule ? 'bg-acc/10' : ''}`}><td className="py-1.5 pr-2">{x.rule}</td><td>{x.side}</td><td className="tabular-nums">{x.n_valid.toLocaleString()}</td>
              <td className="tabular-nums">{pct(x.acc_valid, 1)} <span className="note">({pct(x.lo, 0)}–{pct(x.hi, 0)})</span></td><td><Badge tone={x.verdict === 'proven' ? 'up' : x.verdict === 'weak' ? 'warn' : 'mut'}>{x.verdict}</Badge></td></tr>))}</tbody></table>}
        <p className="note mt-2">Across all stored candles the next candle continued the leg {pct(m.base_follow, 1)} of the time - close to a coin flip. The AI only earns grade A or B for a situation whose test result beats break-even.</p>
      </Card>
    </div>
  );
}
interface SavedTrade {
  id: number; created: number; symbol: string; name: string; tf: number; entry_t: number; direction: 'CALL' | 'PUT'; grade: string | null; conviction: number;
  agree: number; against: number; status: string; entry: number | null; exit: number | null; strategies: string[];
}
interface History {
  rows: SavedTrade[];
  summary: { settled: number; wins: number; draws: number; pending: number; accuracy: number | null; ci_low: number | null; ci_high: number | null; breakeven: number | null;
    by_grade: { grade: string; settled: number; wins: number; accuracy: number }[] };
}

// ------------------------------------------------------------------ time zones (fixed UTC offsets, minutes)
const ZONES: [number, string][] = [
  [-480, 'UTC-8 — Los Angeles'], [-420, 'UTC-7 — Denver'], [-360, 'UTC-6 — Chicago'], [-300, 'UTC-5 — New York'], [-180, 'UTC-3 — São Paulo'], [0, 'UTC+0 — London'],
  [60, 'UTC+1 — Paris / Berlin'], [120, 'UTC+2 — Cairo / Athens'], [180, 'UTC+3 — Istanbul / Moscow'], [240, 'UTC+4 — Dubai'], [300, 'UTC+5 — Karachi'],
  [330, 'UTC+5:30 — India (IST)'], [345, 'UTC+5:45 — Nepal'], [360, 'UTC+6 — Dhaka'], [420, 'UTC+7 — Bangkok / Jakarta'], [480, 'UTC+8 — Singapore / Beijing'],
  [540, 'UTC+9 — Tokyo'], [600, 'UTC+10 — Sydney'],
];
const deviceOffset = (): number => -new Date().getTimezoneOffset();
const pad = (n: number) => String(n).padStart(2, '0');
/** wall-clock text of an epoch in a fixed UTC offset */
const clock = (epoch: number, offMin: number, seconds = false): string => {
  const d = new Date((epoch + offMin * 60) * 1000);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${seconds ? `:${pad(d.getUTCSeconds())}` : ''}`;
};
const dayTime = (epoch: number, offMin: number): string => {
  const d = new Date((epoch + offMin * 60) * 1000);
  return `${d.toLocaleString('en', { month: 'short', day: '2-digit', timeZone: 'UTC' })} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
};
/** epoch of hh:mm "today" in the given offset */
const epochOfToday = (h: number, m: number, offMin: number): number => {
  const now = Date.now() / 1000;
  const dayStart = Math.floor((now + offMin * 60) / 86400) * 86400 - offMin * 60;
  return dayStart + h * 3600 + m * 60;
};

// ------------------------------------------------------------------ small controls
function Section({ icon, title, right, children }: { icon: string; title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <div className="mb-5">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-acc"><span>{icon}</span>{title}</div>{right}
      </div>
      {children}
    </div>
  );
}

function Seg<T extends string | number>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { v: T; label: ReactNode }[] }) {
  return (
    <div className="inline-flex w-full rounded-lg border border-line bg-panel2 text-sm font-semibold">
      {options.map((o) => (
        <button key={String(o.v)} onClick={() => onChange(o.v)}
          className={`relative flex-1 border-r border-line px-3 py-2 transition first:rounded-l-lg last:rounded-r-lg last:border-r-0 ${value === o.v ? 'bg-acc text-white' : 'text-mut hover:text-fg'}`}>{o.label}</button>
      ))}
    </div>
  );
}

function Stepper({ value, max, label, onChange }: { value: number; max: number; label: string; onChange: (v: number) => void }) {
  const wrap = (v: number) => (v + max + 1) % (max + 1);
  return (
    <div className="flex flex-col items-center gap-1">
      <button className="rounded p-1 text-mut hover:text-fg" onClick={() => onChange(wrap(value + 1))} aria-label={`${label} up`}>▲</button>
      <div className="grid h-14 w-16 place-items-center rounded-xl border border-line bg-bg text-3xl font-bold tabular-nums">{pad(value)}</div>
      <button className="rounded p-1 text-mut hover:text-fg" onClick={() => onChange(wrap(value - 1))} aria-label={`${label} down`}>▼</button>
      <div className="text-[10px] font-semibold tracking-widest text-mut">{label}</div>
    </div>
  );
}

// ------------------------------------------------------------------ result
function Countdown({ r, fetchedAt }: { r: Generated; fetchedAt: number }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 250); return () => clearInterval(t); }, []);
  const offset = r.now - fetchedAt / 1000;                    // server clock minus this browser's clock
  const left = r.entry_t - (Date.now() / 1000 + offset);
  if (r.replay) return <span className="note">historical candle</span>;
  if (left > 0) return <span className="font-bold tabular-nums">in {Math.floor(left / 60)}:{pad(Math.floor(left % 60))}</span>;
  const into = -left;
  return <span className={`font-bold tabular-nums ${into > r.tf * 0.2 ? 'text-warn' : 'text-up'}`}>started {Math.floor(into)}s ago</span>;
}

function ConvictionBar({ v }: { v: number }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs"><span className="text-mut">Agreement strength</span><b className="tabular-nums">{v}/100</b></div>
      <div className="h-2.5 overflow-hidden rounded-full bg-line"><div className="h-full rounded-full bg-gradient-to-r from-acc to-acc2" style={{ width: `${v}%` }} /></div>
    </div>
  );
}

function StrategyLine({ s }: { s: StratRow }) {
  const t = s.track;
  return (
    <div className="grid grid-cols-[1fr_auto] items-start gap-3 border-t border-line py-2 first:border-0">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold">{s.name}</span>
          {s.status === 'live' ? <Badge tone="up">live</Badge> : s.status === 'paper' ? <Badge tone="acc">paper</Badge> : <Badge tone="warn">off</Badge>}
          {s.weight !== 1 && <Badge tone={s.weight > 1 ? 'up' : 'down'} title="weight adjusted from this strategy's own measured record">{s.weight > 1 ? 'proven' : 'weak record'}</Badge>}
        </div>
        {s.why.map((w, i) => <div key={i} className="note">• {w}</div>)}
        <div className="note">{t.settled ? <>own record on this timeframe: {t.wins}/{t.settled} = {pct(t.accuracy, 0)}{t.settled < 30 ? ' (small sample)' : ''}</> : 'no settled signals yet on this timeframe'}</div>
      </div>
      <div className="text-right"><span className={`font-bold ${s.direction === 'CALL' ? 'text-up' : 'text-down'}`}>{s.direction === 'CALL' ? '▲ CALL' : '▼ PUT'}</span><div className="note">strength {s.strength}</div></div>
    </div>
  );
}

function Result({ r, fetchedAt, tz }: { r: Generated; fetchedAt: number; tz: number }) {
  const [showAll, setShowAll] = useState(false);
  const tone = r.verdict === 'CALL' ? 'up' : r.verdict === 'PUT' ? 'down' : 'mut';
  const ring = tone === 'up' ? 'border-up/50 from-up/20' : tone === 'down' ? 'border-down/50 from-down/20' : 'border-line from-panel2';
  const supports = r.strategies.filter((s) => s.supports && !s.meta);
  const opposes = r.strategies.filter((s) => s.opposes && !s.meta);
  const idle = r.strategies.filter((s) => s.direction === 'SKIP' && !s.meta);
  const here = r.calibration_here;
  const be = r.calibration.breakeven ?? r.breakeven;
  const v = here ? verdict(here.settled, here.ci_low, here.ci_high, be) : 'none';

  return (
    <div className="space-y-4">
      <div className={`rounded-2xl border bg-gradient-to-br to-panel p-5 ${ring}`}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-sm text-mut">{prettyPair(r.symbol)} · {tfName(r.tf)} expiry · {r.market}</div>
            <div className={`mt-1 text-5xl font-black tracking-tight ${tone === 'up' ? 'text-up' : tone === 'down' ? 'text-down' : 'text-mut'}`}>
              {r.verdict === 'CALL' ? '▲ CALL' : r.verdict === 'PUT' ? '▼ PUT' : 'NO TRADE'}
            </div>
            {r.grade && <div className="mt-2 flex items-center gap-2"><Badge tone={r.grade === 'A' ? 'up' : r.grade === 'B' ? 'acc' : 'warn'}>GRADE {r.grade}</Badge>
              <span className="note">{r.ai ? (r.grade === 'A' ? 'the AI-built rule beat break-even on unseen candles, with proof' : r.grade === 'B' ? 'the AI-built rule beat break-even on unseen candles, not proven' : 'the AI-built rule showed no edge on unseen candles') : r.grade === 'A' ? '3+ strategies agree, none disagree, other timeframes not against' : r.grade === 'B' ? 'clear majority, other timeframes not against' : 'weak agreement - low conviction'}</span></div>}
            {r.blocked && <div className="mt-2 max-w-md text-sm text-mut">{r.blocked}</div>}
          </div>
          {r.verdict !== 'NO TRADE' && (
            <div className="min-w-[230px] rounded-xl border border-line bg-bg/60 p-3 text-sm">
              <div className="flex justify-between"><span className="text-mut">Enter at</span><b className="tabular-nums">{clock(r.entry_t, tz, true)}</b></div>
              <div className="flex justify-between"><span className="text-mut">Expires</span><b className="tabular-nums">{clock(r.expiry_t, tz, true)}</b></div>
              <div className="flex justify-between"><span className="text-mut">Entry candle</span><Countdown r={r} fetchedAt={fetchedAt} /></div>
              <div className="flex justify-between"><span className="text-mut">Price now</span><b className="tabular-nums">{r.price}</b></div>
              <div className="flex justify-between"><span className="text-mut">Payout</span><b>{r.payout ?? '–'}%</b></div>
            </div>
          )}
        </div>
        {r.verdict !== 'NO TRADE' && <div className="mt-4"><ConvictionBar v={r.conviction} /><div className="note mt-1">{r.ai ? 'This is how far the rule beat a coin flip on candles it never saw (100 = +10 points). It is not a promise.' : <>This is how strongly the strategies agree. It is <b>not</b> a probability of winning.</>}</div></div>}
        {r.outcome && (
          <div className={`mt-4 rounded-lg border px-3 py-2 text-sm ${r.outcome.status === 'win' ? 'border-up/40 bg-up/10 text-up' : r.outcome.status === 'loss' ? 'border-down/40 bg-down/10 text-down' : 'border-line text-mut'}`}>
            What actually happened: <b>{r.outcome.status.toUpperCase()}</b> (open {r.outcome.entry} → close {r.outcome.exit})
          </div>
        )}
        {r.warnings.length > 0 && <div className="mt-3 space-y-1">{r.warnings.map((w, i) => <div key={i} className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-1.5 text-sm text-warn">⚠ {w}</div>)}</div>}
        {r.trade_id && <div className="note mt-3">Saved as trade #{r.trade_id}. It settles automatically, so the generator's own track record is measured below.</div>}
      </div>

      {r.ai ? <AiPanel r={r} tz={tz} /> : <>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Other timeframes">
          <div className="space-y-2">
            {r.mtf.map((m) => (
              <div key={m.tf} className="flex items-center justify-between rounded-lg border border-line bg-panel2 px-3 py-2">
                <b>{tfName(m.tf)}</b>
                <span className="note">{m.note || `${m.calls} CALL · ${m.puts} PUT`}</span>
                {m.lean ? <span className={`font-bold ${m.lean === 'CALL' ? 'text-up' : 'text-down'} ${r.verdict !== 'NO TRADE' && m.lean !== r.verdict ? 'underline decoration-warn' : ''}`}>{m.lean === 'CALL' ? '▲ CALL' : '▼ PUT'}</span> : <span className="text-mut">neutral</span>}
              </div>
            ))}
          </div>
          <p className="note mt-2">{r.mtf_opposed ? 'Another timeframe leans against this trade - it lowers the grade.' : 'No other timeframe leans against this trade.'}</p>
        </Card>
        <Card title="Does agreement actually help? (measured)">
          <table className="w-full"><thead><tr>{['Strategies agreeing', 'Past signals', 'Accuracy', '95% range'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{r.calibration.rows.map((c) => (
              <tr key={c.label} className={here?.label === c.label && r.verdict !== 'NO TRADE' ? 'bg-acc/10' : ''}>
                <td className="td font-semibold">{c.label}</td><td className="td tabular-nums">{c.settled}</td><td className="td tabular-nums">{pct(c.accuracy, 0)}</td>
                <td className="td note tabular-nums">{c.settled ? `${pct(c.ci_low, 0)} – ${pct(c.ci_high, 0)}` : '–'}</td></tr>))}</tbody></table>
          <p className="note mt-2">Break-even at this payout ≈ <b className="text-fg">{pct(be, 1)}</b>.{' '}
            {here && r.verdict !== 'NO TRADE' && (v === 'none' ? 'No past signals with this much agreement yet - nothing to compare against.' : v === 'sample' ? `For ${here.label} agreeing strategies only ${here.settled} past signals exist - too few to know.` : v === 'above' ? 'Past signals with this much agreement were above break-even.' : v === 'below' ? 'Past signals with this much agreement were BELOW break-even.' : 'Past signals with this much agreement were around break-even.')}</p>
        </Card>
      </div>

      <Card title={`Strategy votes - ${r.counts.calls} CALL · ${r.counts.puts} PUT · ${r.counts.neutral} neutral`}>
        {supports.length === 0 && opposes.length === 0 && <Empty>No strategy has a signal on the last closed candle.</Empty>}
        {supports.length > 0 && <div className="mb-3"><div className="label mb-1 text-up">Supporting the trade ({supports.length})</div>{supports.map((s) => <StrategyLine key={s.id} s={s} />)}</div>}
        {opposes.length > 0 && <div className="mb-3"><div className="label mb-1 text-down">Against ({opposes.length})</div>{opposes.map((s) => <StrategyLine key={s.id} s={s} />)}</div>}
        {idle.length > 0 && <>
          <button className="note hover:text-fg" onClick={() => setShowAll(!showAll)}>{showAll ? '▾ Hide' : '▸ Show'} {idle.length} strategies with no signal</button>
          {showAll && <div className="mt-2 flex flex-wrap gap-1.5">{idle.map((s) => <span key={s.id} className="chip border-line text-mut">{s.name}</span>)}</div>}
        </>}
      </Card>
      </>}
    </div>
  );
}

// ------------------------------------------------------------------ saved trades
function Track({ h, tz }: { h: History | undefined; tz: number }) {
  const qc = useQueryClient();
  const del = useMutation({ mutationFn: (id: number) => api(`/api/generate/history/${id}`, { method: 'DELETE' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['gen-history'] }) });
  if (!h) return <Card title="Generated trades"><Spinner /></Card>;
  const s = h.summary;
  const v = verdict(s.settled, s.ci_low, s.ci_high, s.breakeven);
  return (
    <Card title="Generated trades - the generator's own track record" right={s.settled ? <Badge tone={v === 'above' ? 'up' : v === 'below' ? 'down' : v === 'sample' ? 'warn' : 'mut'}>{v === 'above' ? 'ABOVE BREAK-EVEN' : v === 'below' ? 'BELOW BREAK-EVEN' : v === 'sample' ? 'SMALL SAMPLE' : 'NEAR BREAK-EVEN'}</Badge> : undefined}>
      <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-5">
        {[['Settled', s.settled], ['Wins', s.wins], ['Accuracy', pct(s.accuracy, 1)], ['95% range', s.settled ? `${pct(s.ci_low, 0)}–${pct(s.ci_high, 0)}` : '–'], ['Break-even', pct(s.breakeven, 1)]].map(([k, val]) => (
          <div key={String(k)} className="rounded-lg border border-line bg-panel2 p-3"><div className="note">{k}</div><div className="text-xl font-bold tabular-nums">{val}</div></div>))}
      </div>
      {s.by_grade.length > 0 && <div className="mb-3 flex flex-wrap gap-2 text-sm">{s.by_grade.map((g) => <Badge key={g.grade} tone={g.grade === 'A' ? 'up' : g.grade === 'B' ? 'acc' : 'warn'}>Grade {g.grade}: {g.wins}/{g.settled} = {pct(g.accuracy, 0)}</Badge>)}</div>}
      {h.rows.length === 0 ? <Empty>No trades generated yet. Generated CALL/PUT trades are saved here and settled automatically.</Empty> : (
        <div className="overflow-x-auto"><table className="w-full"><thead><tr>{['Entry', 'Pair', 'TF', 'Trade', 'Grade', 'Agree', 'Result', ''].map((x) => <th key={x} className="th">{x}</th>)}</tr></thead>
          <tbody>{h.rows.map((t) => (
            <tr key={t.id}><td className="td tabular-nums">{dayTime(t.entry_t, tz)}</td><td className="td">{prettyPair(t.symbol)}</td><td className="td">{tfName(t.tf)}</td>
              <td className="td"><span className={`font-bold ${t.direction === 'CALL' ? 'text-up' : 'text-down'}`}>{t.direction === 'CALL' ? '▲ CALL' : '▼ PUT'}</span></td>
              <td className="td">{t.grade ? <Badge tone={t.grade === 'A' ? 'up' : t.grade === 'B' ? 'acc' : 'warn'}>{t.grade}</Badge> : '–'}</td>
              <td className="td note tabular-nums" title={t.strategies.join(', ')}>{t.agree} for · {t.against} against</td>
              <td className="td"><Badge tone={t.status === 'win' ? 'up' : t.status === 'loss' ? 'down' : t.status === 'pending' ? 'warn' : 'mut'}>{t.status.toUpperCase()}</Badge></td>
              <td className="td text-right"><button className="note hover:text-down" title="Remove from the list (also from the track record)" onClick={() => confirm('Remove this trade from the list and the track record?') && del.mutate(t.id)}>✕</button></td></tr>))}</tbody></table></div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ page
export default function TradeGenerator() {
  const live = useLiveState();
  const strategies = useStrategies();
  const qc = useQueryClient();
  const [market, setMarket] = usePersisted<'OTC' | 'LIVE'>('pair-market', 'OTC');
  const [q, setQ] = useState('');
  const [onlyStream, setOnlyStream] = usePersisted('pair-streaming-only', false);
  const [sym, setSym] = useState('');
  const [tf, setTf] = useState(60);
  const dev = deviceOffset();
  const zones = useMemo<[number, string][]>(() => (ZONES.some(([o]) => o === dev) ? ZONES : [[dev, `UTC${dev >= 0 ? '+' : '-'}${Math.floor(Math.abs(dev) / 60)}:${pad(Math.abs(dev) % 60)} — your device`], ...ZONES]), [dev]);
  const [tz, setTz] = useState(dev);
  const [mode, setMode] = useState<'next' | 'time'>('next');
  const [hh, setHh] = useState(() => new Date().getHours());
  const [mm, setMm] = useState(() => new Date().getMinutes());
  const [scope, setScope] = useState<'running' | 'all' | 'pick' | 'ai'>('running');
  const [picked, setPicked] = useState<number[]>([]);
  const [minGrade, setMinGrade] = useState<'C' | 'B' | 'A'>('C');
  const [result, setResult] = useState<{ r: Generated; at: number } | null>(null);

  const symbols = live.data?.symbols ?? [];
  useEffect(() => { if (!sym && symbols.length) setSym((symbols.find((s) => s.streaming) ?? symbols[0]).symbol); }, [sym, symbols]);
  const list = symbols.filter((s) => s.market === market && (!onlyStream || s.streaming) && (s.name + s.symbol).toLowerCase().includes(q.toLowerCase()));
  const usable = (strategies.data ?? []).filter((s) => ['live', 'paper', 'paused'].includes(s.status));
  const running = usable.filter((s) => s.status !== 'paused').length;
  const history = useQuery({ queryKey: ['gen-history'], queryFn: () => api<History>('/api/generate/history?limit=40'), refetchInterval: 10000 });

  const useNow = () => { const d = new Date(Date.now() + tz * 60000); setHh(d.getUTCHours()); setMm(d.getUTCMinutes()); setMode('next'); };
  const gen = useMutation({
    mutationFn: () => api<Generated>('/api/generate', { body: {
      symbol: sym, tf, at: mode === 'time' ? epochOfToday(hh, mm, tz) : undefined, scope: scope === 'all' ? 'all' : scope === 'ai' ? 'ai' : 'running',
      ids: scope === 'pick' ? picked : undefined, min_grade: minGrade } }),
    onSuccess: (r) => { setResult({ r, at: Date.now() }); qc.invalidateQueries({ queryKey: ['gen-history'] }); },
  });
  const selected = symbols.find((s) => s.symbol === sym);
  const canGo = !!sym && !gen.isPending && (scope !== 'pick' || picked.length > 0);

  return (
    <>
      <div className="mb-4">
        <h1 className="text-xl font-bold sm:text-2xl">Trade generator</h1>
        <p className="mt-1 text-sm text-mut">Pick a pair and an expiry, and every chosen strategy analyses it together. You get one graded trade idea with the reasons, the entry time, and what history says about that much agreement - or an honest NO TRADE.</p>
      </div>

      <div className="grid gap-4 xl:grid-cols-[400px_1fr]">
        <div className="xl:sticky xl:top-0 xl:self-start">
          <div className="rounded-2xl border border-line bg-gradient-to-b from-panel to-panel2 p-5">
            <Section icon="▥" title="Select pair / asset" right={selected && <span className="note">{selected.name} · {selected.payout ?? '–'}%</span>}>
              <div className="mb-2"><Seg value={market} onChange={setMarket} options={[{ v: 'OTC', label: <>OTC market<StreamBubble n={streamingCount(symbols, 'OTC')} /></> }, { v: 'LIVE', label: <>Live market<StreamBubble n={streamingCount(symbols, 'LIVE')} /></> }]} /></div>
              <div className="mb-2 flex items-center gap-2">
                <input className="input" placeholder="Search pairs…" value={q} onChange={(e) => setQ(e.target.value)} />
                <label className="flex shrink-0 items-center gap-1.5 text-xs text-mut"><input type="checkbox" checked={onlyStream} onChange={(e) => setOnlyStream(e.target.checked)} />streaming</label>
              </div>
              <div className="grid max-h-52 grid-cols-2 gap-1.5 overflow-y-auto pr-1">
                {list.map((s) => (
                  <button key={s.symbol} onClick={() => setSym(s.symbol)}
                    className={`flex items-center gap-2 rounded-lg border px-2 py-2 text-left text-xs ${s.symbol === sym ? 'border-acc bg-acc/20 text-[#c4b5fd]' : 'border-line bg-bg/40 hover:border-acc/60'} ${s.streaming ? '' : 'opacity-60'}`}>
                    <span className={`h-2 w-2 shrink-0 rounded-full ${s.streaming ? 'bg-up shadow-[0_0_6px_#22c55e]' : 'bg-[#475069]'}`} />
                    <span className="min-w-0 flex-1 truncate font-mono">{s.name.replace(' (OTC)', '')}</span>
                    <span className={`shrink-0 font-semibold tabular-nums ${s.payout == null ? 'text-mut' : s.payout >= 90 ? 'text-up' : s.payout >= 80 ? 'text-fg' : 'text-warn'}`} title="Return if the trade wins (payout)">{s.payout == null ? '–' : `${s.payout}%`}</span>
                  </button>))}
                {list.length === 0 && <div className="note col-span-2 p-3 text-center">No pairs.</div>}
              </div>
              {selected && !selected.streaming && <p className="mt-2 text-xs text-warn">This pair is not streaming. Open it in your Quotex tab for fresh candles - stored candles may be old.</p>}
            </Section>

            <Section icon="⏱" title="Expiry / timeframe">
              <Seg value={tf} onChange={setTf} options={[60, 300, 900].map((t) => ({ v: t, label: tfName(t) }))} />
            </Section>

            <Section icon="◷" title="Your time zone">
              <select className="input" value={tz} onChange={(e) => setTz(Number(e.target.value))}>{zones.map(([o, l]) => <option key={o} value={o}>{l}</option>)}</select>
            </Section>

            <Section icon="◔" title="Analysis time (your local time)" right={<button className="text-xs font-semibold text-acc hover:underline" onClick={useNow}>↻ Use now</button>}>
              <div className="mb-3"><Seg value={mode} onChange={setMode} options={[{ v: 'next', label: 'Next candle (live)' }, { v: 'time', label: 'Specific time' }]} /></div>
              {mode === 'time' ? (
                <div className="flex items-center justify-center gap-3 rounded-xl border border-line bg-bg/40 py-3">
                  <Stepper value={hh} max={23} label="HOUR" onChange={setHh} /><span className="text-3xl font-bold text-acc">:</span><Stepper value={mm} max={59} label="MIN" onChange={setMm} />
                </div>
              ) : <p className="note">Analyses the last closed candle for the very next entry. Best used in the first seconds of a new candle.</p>}
              {mode === 'time' && <p className="note mt-2">A time in the past replays what the app would have said, with the real result. The future cannot be analysed.</p>}
            </Section>

            <Section icon="⚙" title="Strategies used" right={<span className="note">{running} running · {usable.length} total</span>}>
              <Seg value={scope} onChange={setScope} options={[{ v: 'ai', label: '✦ AI builds it' }, { v: 'running', label: 'Running' }, { v: 'all', label: 'All' }, { v: 'pick', label: 'Choose' }]} />
              {scope === 'ai' && <p className="note mt-2">No preset strategy. The AI finds where the recent momentum started, learns from history what that kind of situation does next, tests the rule on candles it never saw, and only then gives the trade - graded by its own test.</p>}
              {scope === 'pick' && (
                <div className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded-lg border border-line bg-bg/40 p-2">
                  {usable.map((s) => (
                    <label key={s.id} className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[#8b5cf6]" checked={picked.includes(s.id)}
                      onChange={(e) => setPicked(e.target.checked ? [...picked, s.id] : picked.filter((x) => x !== s.id))} />{s.name}</label>))}
                </div>
              )}
            </Section>

            <Section icon="★" title="Minimum grade">
              <Seg value={minGrade} onChange={setMinGrade} options={[{ v: 'C', label: 'Any (C+)' }, { v: 'B', label: 'B or better' }, { v: 'A', label: 'A only' }]} />
            </Section>

            <button disabled={!canGo} onClick={() => gen.mutate()}
              className="flex w-full items-center justify-center gap-3 rounded-xl bg-gradient-to-r from-acc to-acc2 px-4 py-3.5 text-base font-bold text-white shadow-lg shadow-acc/30 transition hover:brightness-110 disabled:opacity-60">
              {gen.isPending ? <><span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />{scope === 'ai' ? 'AI is reading the momentum…' : 'Analyzing market confluence…'}</> : <>✦ Generate trade</>}
            </button>
          </div>
        </div>

        <div className="min-w-0">
          {gen.isError && <div className="mb-4"><ErrorBox error={gen.error} /></div>}
          {result ? <Result r={result.r} fetchedAt={result.at} tz={tz} /> : (
            <div className="grid min-h-[320px] place-items-center rounded-2xl border border-dashed border-line p-8 text-center">
              <div><div className="mb-2 text-4xl">✦</div><div className="text-lg font-bold">Ready when you are</div>
                <p className="mx-auto mt-1 max-w-md text-sm text-mut">Choose a pair and press <b>Generate trade</b>. The app runs the selected strategies on the latest closed candles, checks the other timeframes and tells you the strongest idea - or that there is none.</p></div>
            </div>
          )}
          <p className="note mt-4">Binary options are high-risk. No setup is guaranteed: every number here is measured from this app's own settled signals, and small samples are flagged. Grade A is the strongest <i>agreement</i>, not a promise of profit.</p>
        </div>
      </div>

      <div className="mt-6"><Track h={history.data} tz={tz} /></div>
    </>
  );
}
