import { ReactNode, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago, pct } from '../format';
import { currencySymbol, useAccount, useMoney } from '../hooks';
import { Badge, Card, Empty, ErrorBox, PageTitle, Spinner } from '../components/ui';
import { useBoxWidth } from '../lib/useBoxWidth';

// ------------------------------------------------------------------ shapes (see backend/app/guard.py)
interface Lim { on: boolean; value: number; unit: 'amount' | 'percent' }
export interface GuardSettings {
  enabled: boolean; account: 'real' | 'demo' | 'all'; target: Lim; loss: Lim; streak: { on: boolean; max: number }; cooldown_min: number; count_cycles: boolean;
  max_trades: { on: boolean; value: number }; per_hour: { on: boolean; value: number }; after_target: { extra_allowed: number };
  stake: { on: boolean; risk_pct: number; max_pct: number; max_amount: number }; mtg: { on: boolean; mult: number }; warn_at: number; day_start_hour: number; tz_min: number; sound: boolean;
}
interface Rule { key: string; title: string; on: boolean; value: number; limit: number | null; progress: number | null; level: 'ok' | 'near' | 'hit' | 'off' | 'wait'; note: string; unit: 'money' | 'count' }
export interface GuardStatus {
  settings: GuardSettings; day: string; now: number; state: 'ok' | 'target' | 'stop' | 'cooldown' | 'overtrading' | 'caution'; headline: string; advice: string[]; over: boolean; manual_stop: boolean;
  balance: { value: number | null; start: number | null; account: string };
  net: number; peak: number; low: number; target: number | null; loss: number | null; target_hit: boolean; loss_hit: boolean; given_back: number;
  trades: { orders: number; count: number; positions: number; running: number; wins: number; losses: number };
  after_target: number; after_loss: number; extra_allowed: number; streak: number; cooldown: { until: number; why?: string } | null;
  stake: { biggest: number; max: number | null; suggested: { amount: number | null; base?: number; reason: string; worst_cycle?: number; step?: number | null } };
  rules: Rule[]; series: [number, number][];
  days: { day: string; net: number; trades: number; orders: number; target_hit: boolean; loss_hit: boolean; overtraded: boolean; longest_streak: number; peak: number; after_target: number; after_loss: number }[];
  alerts: { ts: number; level: 'success' | 'info' | 'warn' | 'danger'; rule: string; title: string; message: string }[];
}

const UP = '#22c55e', DOWN = '#ef4444', WARN = '#f59e0b', ACC = '#8b5cf6', MUT = '#8a93b2', INFO = '#38bdf8';
const STATE: Record<GuardStatus['state'], { tone: string; color: string; icon: string; label: string; glow: string }> = {
  ok: { tone: 'acc', color: ACC, icon: '✓', label: 'ON TRACK', glow: 'rgba(139,92,246,.18)' },
  target: { tone: 'up', color: UP, icon: '🎯', label: 'TARGET REACHED', glow: 'rgba(34,197,94,.22)' },
  stop: { tone: 'down', color: DOWN, icon: '⛔', label: 'STOP FOR TODAY', glow: 'rgba(239,68,68,.24)' },
  cooldown: { tone: 'warn', color: INFO, icon: '⏸', label: 'COOLING DOWN', glow: 'rgba(56,189,248,.2)' },
  overtrading: { tone: 'down', color: DOWN, icon: '⚠', label: 'OVERTRADING', glow: 'rgba(239,68,68,.24)' },
  caution: { tone: 'warn', color: WARN, icon: '!', label: 'BE CAREFUL', glow: 'rgba(245,158,11,.2)' },
};
const LVL: Record<string, { c: string; icon: string }> = { success: { c: UP, icon: '✓' }, info: { c: INFO, icon: 'ℹ' }, warn: { c: WARN, icon: '!' }, danger: { c: DOWN, icon: '⛔' } };

// ------------------------------------------------------------------ the profit / loss gauge
function Gauge({ g }: { g: GuardStatus }) {
  const money = useMoney();
  const [box, bw] = useBoxWidth(640);
  const { net, target, loss } = g;
  const hi = target ?? Math.max(Math.abs(net) * 1.4, 1), lo = -(loss ?? Math.max(Math.abs(net) * 1.4, 1));
  const W = Math.min(640, bw), H = 120, L = 20, R = 20, y = 66, th = 14;
  const x = (v: number) => L + ((Math.min(hi * 1.12, Math.max(lo * 1.12, v)) - lo * 1.12) / (hi * 1.12 - lo * 1.12)) * (W - L - R);
  const mx = x(net), col = net >= 0 ? UP : DOWN;
  const past = (target && net > target) || (loss && -net > loss);
  return (
    <div ref={box}><svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Today's result between the daily loss limit and the daily target">
      <defs>
        <linearGradient id="gl" x1="0" x2="1"><stop offset="0" stopColor={DOWN} stopOpacity=".9" /><stop offset="1" stopColor={DOWN} stopOpacity=".08" /></linearGradient>
        <linearGradient id="gr" x1="0" x2="1"><stop offset="0" stopColor={UP} stopOpacity=".08" /><stop offset="1" stopColor={UP} stopOpacity=".9" /></linearGradient>
      </defs>
      <rect x={L} y={y} width={x(0) - L} height={th} rx={7} fill="url(#gl)" />
      <rect x={x(0)} y={y} width={W - R - x(0)} height={th} rx={7} fill="url(#gr)" />
      <line x1={x(0)} x2={x(0)} y1={y - 8} y2={y + th + 8} stroke="#fff" strokeOpacity={0.5} />
      {/* filled part from zero to the result */}
      <rect x={Math.min(x(0), mx)} y={y + 3} width={Math.abs(mx - x(0))} height={th - 6} rx={4} fill={col} />
      <circle cx={mx} cy={y + th / 2} r={11} fill="#0a0e1f" stroke={col} strokeWidth={3} />
      <circle cx={mx} cy={y + th / 2} r={4} fill={col} />
      <g transform={`translate(${Math.min(W - 60, Math.max(60, mx))},${y - 20})`}>
        <rect x={-52} y={-18} width={104} height={24} rx={8} fill={col} /><path d="M-6,6 h12 l-6,7 z" fill={col} />
        <text x={0} y={-1.5} textAnchor="middle" fontSize={14} fontWeight={800} fill="#0a0e1f">{money(net, true)}</text>
      </g>
      <text x={L} y={y + th + 28} fontSize={13} fontWeight={700} fill={DOWN}>{loss ? `Loss limit ${money(-loss)}` : 'No loss limit'}</text>
      <text x={x(0)} y={y + th + 28} textAnchor="middle" fontSize={13} fill={MUT}>0</text>
      <text x={W - R} y={y + th + 28} textAnchor="end" fontSize={13} fontWeight={700} fill={UP}>{target ? `Target ${money(target)}` : 'No target'}</text>
      {past && <text x={W / 2} y={12} textAnchor="middle" fontSize={13} fontWeight={700} fill={net > 0 ? UP : DOWN}>{net > 0 ? 'Beyond your target' : 'Beyond your loss limit'}</text>}
    </svg></div>
  );
}

// ------------------------------------------------------------------ small pieces
function Spark({ series }: { series: [number, number][] }) {
  if (series.length < 2) return <div className="grid h-24 place-items-center text-xs text-mut">The running result appears after your second finished trade.</div>;
  const W = 420, H = 96, pad = 6;
  const vals = [0, ...series.map((s) => s[1])];
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const xs = (i: number) => pad + (i / (vals.length - 1)) * (W - pad * 2), ys = (v: number) => H - pad - ((v - lo) / span) * (H - pad * 2);
  const d = vals.map((v, i) => `${i ? 'L' : 'M'}${xs(i).toFixed(1)},${ys(v).toFixed(1)}`).join(' ');
  const last = vals[vals.length - 1], col = last >= 0 ? UP : DOWN;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-24 w-full" role="img" aria-label="Running result today">
      <line x1={pad} x2={W - pad} y1={ys(0)} y2={ys(0)} stroke={MUT} strokeOpacity={0.35} strokeDasharray="4 4" />
      <path d={`${d} L${xs(vals.length - 1)},${ys(0)} L${xs(0)},${ys(0)} Z`} fill={col} opacity={0.12} />
      <path d={d} fill="none" stroke={col} strokeWidth={2.2} strokeLinejoin="round" />
      <circle cx={xs(vals.length - 1)} cy={ys(last)} r={4} fill={col} />
    </svg>
  );
}

const Toggle = ({ on, onChange, label, hint }: { on: boolean; onChange: (v: boolean) => void; label: ReactNode; hint?: ReactNode }) => (
  <label className="flex cursor-pointer items-start gap-3">
    <button type="button" role="switch" aria-checked={on} onClick={() => onChange(!on)}
      className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full border transition ${on ? 'border-acc bg-acc' : 'border-line bg-panel2'}`}>
      <span className={`absolute top-0.5 h-3.5 w-3.5 rounded-full bg-white transition-all ${on ? 'left-[18px]' : 'left-0.5'}`} />
    </button>
    <span className="min-w-0"><span className="block text-sm font-medium text-fg">{label}</span>{hint && <span className="block text-xs text-mut">{hint}</span>}</span>
  </label>
);

function Num({ value, onChange, prefix, suffix, min = 0, max, step = 1, disabled, w = 'w-28' }: {
  value: number; onChange: (v: number) => void; prefix?: string; suffix?: string; min?: number; max?: number; step?: number; disabled?: boolean; w?: string;
}) {
  return (
    <div className={`flex items-center overflow-hidden rounded-lg border border-line bg-panel2 focus-within:border-acc ${disabled ? 'opacity-50' : ''} ${w}`}>
      {prefix && <span className="pl-2 text-sm text-mut">{prefix}</span>}
      <input type="number" className="w-full bg-transparent px-2 py-1.5 text-sm tabular-nums text-fg outline-none" value={Number.isFinite(value) ? value : ''} min={min} max={max} step={step} disabled={disabled}
        onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))} />
      {suffix && <span className="pr-2 text-sm text-mut">{suffix}</span>}
    </div>
  );
}

function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="inline-flex overflow-hidden rounded-lg border border-line" role="group" aria-label={label}>
      {options.map(([k, l]) => <button key={k} type="button" className={`px-2.5 py-1.5 text-xs font-semibold transition ${value === k ? 'bg-acc text-white' : 'bg-panel2 text-mut hover:text-fg'}`} onClick={() => onChange(k)}>{l}</button>)}
    </div>
  );
}

const Row = ({ children, label, hint }: { children: ReactNode; label: ReactNode; hint?: ReactNode }) => (
  <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line py-3 first:border-t-0">
    <div className="min-w-[180px] flex-1"><div className="text-sm font-medium">{label}</div>{hint && <div className="text-xs text-mut">{hint}</div>}</div>
    <div className="flex flex-wrap items-center gap-2">{children}</div>
  </div>
);

// ------------------------------------------------------------------ the page
export default function Guard() {
  const qc = useQueryClient();
  const money = useMoney();
  const acc = useAccount();
  const sym = currencySymbol(acc.data?.currency) || '¤';
  const tz = -new Date().getTimezoneOffset();
  const q = useQuery({ queryKey: ['guard'], queryFn: () => api<GuardStatus>(`/api/guard?tz=${tz}`), refetchInterval: 3000, placeholderData: (p) => p });
  const [draft, setDraft] = useState<GuardSettings | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const saved = q.data?.settings ?? null;
  const dirty = !!draft && !!saved && JSON.stringify(draft) !== JSON.stringify(saved);
  useEffect(() => { if (saved && !draft) setDraft(saved); }, [saved, draft]);
  useEffect(() => { if (saved && draft && !dirty) setDraft(saved); }, [saved]);                  // eslint-disable-line react-hooks/exhaustive-deps

  const put = useMutation({
    mutationFn: (s: Partial<GuardSettings>) => api<GuardStatus>('/api/guard/settings', { method: 'PUT', body: s }),
    onSuccess: (r) => { qc.setQueryData(['guard'], r); setDraft(r.settings); },
  });
  const act = useMutation({ mutationFn: (path: string) => api<GuardStatus>(path, { method: 'POST' }), onSuccess: (r) => qc.setQueryData(['guard'], r) });
  const test = useMutation({
    mutationFn: (level: string) => api(`/api/guard/test?level=${level}`, { method: 'POST' }),
    onSuccess: () => { setSent('Sent - look at your Quotex tab (card, sound and, if the tab is in the background, a desktop notice).'); window.setTimeout(() => setSent(null), 7000); },
  });
  const patch = (p: Partial<GuardSettings>) => setDraft((d) => (d ? { ...d, ...p } : d));
  const nest = <K extends keyof GuardSettings>(k: K, p: Partial<GuardSettings[K] & object>) => setDraft((d) => (d ? { ...d, [k]: { ...(d[k] as object), ...p } } : d));

  const g = q.data;
  const st = g ? STATE[g.state] : STATE.ok;
  const lvlOf = (r: Rule): string => (r.level === 'hit' ? (r.key === 'target' ? UP : DOWN) : r.level === 'near' ? WARN : r.level === 'ok' ? (r.key === 'target' ? ACC : UP) : MUT);

  const hints = useMemo(() => {
    if (!g || !draft) return { t: '', l: '' };
    const sb = g.balance.start;
    const eff = (lim: Lim) => (lim.unit === 'amount' ? lim.value : sb ? (sb * lim.value) / 100 : null);
    const t = eff(draft.target), l = eff(draft.loss);
    return { t: t === null ? 'needs your balance' : `= ${money(t)}`, l: l === null ? 'needs your balance' : `= ${money(l)}` };
  }, [g, draft, money]);

  if (q.isError && !g) return <ErrorBox error={q.error} />;
  if (!g || !draft) return <Spinner />;
  const noBalance = g.balance.value === null;
  const sug = g.stake.suggested;

  return (
    <>
      <PageTitle title="Trading guard" sub="Your daily target, stop-loss and discipline rules, checked against the trades you really place. It warns you on the Quotex page - it never blocks or places a trade."
        right={<Toggle on={draft.enabled} onChange={(v) => { patch({ enabled: v }); put.mutate({ enabled: v }); }} label={draft.enabled ? 'Guard is on' : 'Guard is off'} />} />

      {/* ---- status hero ---- */}
      <section className="relative overflow-hidden rounded-2xl border border-line p-5" style={{ background: `radial-gradient(900px 260px at 12% -20%, ${st.glow}, transparent 60%), #111830` }}>
        <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <div>
            <div className="flex items-center gap-3">
              <span className="grid h-12 w-12 place-items-center rounded-2xl text-2xl" style={{ background: `${st.color}26`, color: st.color, boxShadow: `0 0 0 1px ${st.color}55 inset` }} aria-hidden="true">{st.icon}</span>
              <div>
                <Badge tone={st.tone}>{st.label}</Badge>
                <h2 className="mt-1 text-2xl font-bold tracking-tight">{g.headline}</h2>
              </div>
            </div>
            <ul className="mt-3 space-y-1 text-sm text-mut">{g.advice.map((a) => <li key={a} className="flex gap-2"><span style={{ color: st.color }} aria-hidden="true">›</span>{a}</li>)}</ul>
            {g.target_hit && g.given_back > 0 && <p className="mt-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">You were up {money(g.peak, true)} at the best moment and have given back {money(g.given_back)} since.</p>}
            <div className="mt-4 flex flex-wrap gap-2">
              {g.manual_stop
                ? <button className="btn" onClick={() => act.mutate('/api/guard/resume')}>Resume trading today</button>
                : <button className="btn btn-danger" onClick={() => act.mutate('/api/guard/stop-today')} title="Any trade you open after this is flagged as overtrading">I'm done for today</button>}
              {g.cooldown && <button className="btn" onClick={() => act.mutate('/api/guard/resume')}>End the break</button>}
            </div>
          </div>
          <div>
            <div className="mb-1 flex items-end justify-between">
              <div><div className="text-[10.5px] font-semibold uppercase tracking-wider text-mut">Today's result · {g.day}</div>
                <div className="text-3xl font-bold tabular-nums sm:text-4xl" style={{ color: g.net > 0 ? UP : g.net < 0 ? DOWN : undefined }}>{money(g.net, true)}</div></div>
              <div className="text-right text-xs text-mut">
                <div>{g.trades.count} trade{g.trades.count === 1 ? '' : 's'} · <span className="text-up">{g.trades.wins}W</span> <span className="text-down">{g.trades.losses}L</span>{g.trades.running ? ` · ${g.trades.running} running` : ''}</div>
                <div>{g.balance.start !== null ? `Day started at ${money(g.balance.start)}` : 'Waiting for your balance'}</div>
              </div>
            </div>
            <Gauge g={g} />
          </div>
        </div>
        {noBalance && <p className="mt-3 rounded-lg border border-line bg-panel2 px-3 py-2 text-xs text-mut">Limits set as a % of your balance need the balance: reload the Quotex tab once and it appears. Limits set as an amount work now.</p>}
      </section>

      {/* ---- rules ---- */}
      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {g.rules.map((r) => {
          const c = lvlOf(r);
          const val = r.unit === 'money' ? money(r.value, r.key !== 'stake') : String(r.value);
          const lim = r.limit === null ? '–' : r.unit === 'money' ? money(r.key === 'loss' ? -r.limit : r.limit) : String(r.limit);
          return (
            <div key={r.key} className={`rounded-xl border bg-panel p-4 transition ${r.on ? 'border-line' : 'border-line opacity-60'}`} style={r.level === 'hit' ? { borderColor: `${c}88`, boxShadow: `0 0 0 1px ${c}33, 0 8px 30px ${c}1f` } : undefined}>
              <div className="flex items-start justify-between gap-2">
                <div className="text-[11px] font-semibold uppercase tracking-wider text-mut">{r.title}</div>
                <span className="chip" style={{ color: c, borderColor: `${c}66`, background: `${c}1a` }}>{!r.on ? 'OFF' : r.level === 'hit' ? (r.key === 'target' ? 'REACHED' : 'HIT') : r.level === 'near' ? 'CLOSE' : r.level === 'wait' ? 'WAITING' : 'OK'}</span>
              </div>
              <div className="mt-2 flex items-baseline gap-2"><span className="text-2xl font-bold tabular-nums">{val}</span>{r.limit !== null && <span className="text-sm text-mut">of {lim}</span>}</div>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-panel2" role="progressbar" aria-valuenow={Math.round((r.progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={r.title}>
                <div className="h-full rounded-full transition-all duration-500" style={{ width: `${Math.min(100, (r.progress ?? 0) * 100)}%`, background: c }} />
              </div>
              <div className="mt-2 text-xs text-mut">{r.note}</div>
            </div>
          );
        })}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        {/* ---- suggested stake ---- */}
        <Card title="Suggested stake for your next trade">
          {sug.amount === null ? <Empty>{sug.reason}</Empty> : (
            <>
              <div className="flex items-baseline gap-2"><span className="text-3xl font-bold tabular-nums sm:text-4xl">{sug.amount > 0 ? money(sug.amount) : 'No trade'}</span>{sug.amount > 0 && <span className="text-sm text-mut">first trade</span>}</div>
              <p className="mt-2 text-sm text-mut">{sug.reason}.</p>
              {sug.amount > 0 && (
                <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
                  <div className="rounded-lg border border-line bg-panel2 p-2"><dt className="text-[10px] font-semibold uppercase tracking-wider text-mut">First</dt><dd className="font-semibold tabular-nums">{money(sug.amount)}</dd></div>
                  <div className="rounded-lg border border-line bg-panel2 p-2"><dt className="text-[10px] font-semibold uppercase tracking-wider text-mut">MTG step</dt><dd className="font-semibold tabular-nums">{sug.step ? money(sug.step) : '–'}</dd></div>
                  <div className="rounded-lg border border-line bg-panel2 p-2"><dt className="text-[10px] font-semibold uppercase tracking-wider text-mut">Worst case</dt><dd className="font-semibold tabular-nums text-down">{sug.worst_cycle ? money(-sug.worst_cycle) : '–'}</dd></div>
                </dl>
              )}
              {sug.amount !== null && sug.amount > 0 && sug.amount < 1 && <p className="mt-2 text-xs text-warn">That is below Quotex's smallest stake: your balance is small for these rules - consider a smaller martingale or a higher risk share.</p>}
              <p className="note mt-3">{g.stake.max ? `Largest single stake allowed: ${money(g.stake.max)}. ` : ''}Biggest today: {money(g.stake.biggest)}.</p>
            </>
          )}
        </Card>

        {/* ---- running result ---- */}
        <Card title="Today, trade by trade" className="lg:col-span-2" right={<span className="note">{g.trades.orders} orders · {g.trades.positions} positions</span>}>
          <Spark series={g.series} />
          <div className="mt-2 grid grid-cols-2 gap-2 text-center sm:grid-cols-4">
            {[['Best moment', money(g.peak, true), g.peak > 0 ? 'up' : ''], ['Worst moment', money(g.low, true), g.low < 0 ? 'down' : ''], ['After target', `${g.after_target} trade${g.after_target === 1 ? '' : 's'}`, g.after_target > g.extra_allowed ? 'down' : ''],
              ['Losing streak', `${g.streak}`, g.streak >= (g.settings.streak.max - 1) && g.settings.streak.on ? 'down' : '']].map(([l, v, t]) => (
              <div key={l} className="rounded-lg border border-line bg-panel2 p-2"><div className="text-[10px] font-semibold uppercase tracking-wider text-mut">{l}</div>
                <div className={`font-semibold tabular-nums ${t === 'up' ? 'text-up' : t === 'down' ? 'text-down' : ''}`}>{v}</div></div>))}
          </div>
        </Card>
      </div>

      {/* ---- settings + history ---- */}
      <div className="mt-4 grid gap-4 xl:grid-cols-5">
        <Card title="Your rules" className="xl:col-span-3" right={dirty ? <Badge tone="warn">UNSAVED CHANGES</Badge> : <span className="note">saved</span>}>
          <Row label="Account to watch" hint="Which Quotex account these limits apply to.">
            <Seg label="Account" value={draft.account} onChange={(v) => patch({ account: v })} options={[['real', 'Real'], ['demo', 'Demo'], ['all', 'Both']]} />
          </Row>
          <Row label={<Toggle on={draft.target.on} onChange={(v) => nest('target', { on: v })} label="Daily profit target" hint={`${hints.t} · when reached you are told to stop`} />}>
            <Num value={draft.target.value} onChange={(v) => nest('target', { value: v })} prefix={draft.target.unit === 'amount' ? sym : undefined} suffix={draft.target.unit === 'percent' ? '%' : undefined} disabled={!draft.target.on} step={draft.target.unit === 'percent' ? 0.5 : 1} />
            <Seg label="Target unit" value={draft.target.unit} onChange={(v) => nest('target', { unit: v })} options={[['percent', '% of balance'], ['amount', `${sym} amount`]]} />
          </Row>
          <Row label={<Toggle on={draft.loss.on} onChange={(v) => nest('loss', { on: v })} label="Daily loss limit (stop-loss)" hint={`${hints.l} · reaching it means stop for the day`} />}>
            <Num value={draft.loss.value} onChange={(v) => nest('loss', { value: v })} prefix={draft.loss.unit === 'amount' ? sym : undefined} suffix={draft.loss.unit === 'percent' ? '%' : undefined} disabled={!draft.loss.on} step={draft.loss.unit === 'percent' ? 0.5 : 1} />
            <Seg label="Loss unit" value={draft.loss.unit} onChange={(v) => nest('loss', { unit: v })} options={[['percent', '% of balance'], ['amount', `${sym} amount`]]} />
          </Row>
          <Row label={<Toggle on={draft.streak.on} onChange={(v) => nest('streak', { on: v })} label="Losing streak → take a break" hint="Back-to-back losses are when trades get emotional." />}>
            <Num value={draft.streak.max} onChange={(v) => nest('streak', { max: v })} suffix="in a row" min={2} max={20} disabled={!draft.streak.on} w="w-36" />
            <Num value={draft.cooldown_min} onChange={(v) => patch({ cooldown_min: v })} suffix="min break" min={0} max={240} disabled={!draft.streak.on} w="w-36" />
          </Row>
          <Row label="After the target is reached" hint="Trades you may still take before it is called overtrading.">
            <Num value={draft.after_target.extra_allowed} onChange={(v) => nest('after_target', { extra_allowed: v })} suffix="trades" min={0} max={50} w="w-32" />
          </Row>
          <Row label={<Toggle on={draft.per_hour.on} onChange={(v) => nest('per_hour', { on: v })} label="Speed limit" hint="Too many trades in one hour = chasing." />}>
            <Num value={draft.per_hour.value} onChange={(v) => nest('per_hour', { value: v })} suffix="per hour" min={1} disabled={!draft.per_hour.on} w="w-36" />
          </Row>
          <Row label={<Toggle on={draft.max_trades.on} onChange={(v) => nest('max_trades', { on: v })} label="Daily trade limit" />}>
            <Num value={draft.max_trades.value} onChange={(v) => nest('max_trades', { value: v })} suffix="per day" min={1} disabled={!draft.max_trades.on} w="w-36" />
          </Row>
          <Row label={<Toggle on={draft.stake.on} onChange={(v) => nest('stake', { on: v })} label="Trade size" hint="Risk per trade cycle, and the biggest single stake allowed (both as a share of your balance)." />}>
            <Num value={draft.stake.risk_pct} onChange={(v) => nest('stake', { risk_pct: v })} suffix="% risk" min={0.1} max={50} step={0.5} disabled={!draft.stake.on} w="w-32" />
            <Num value={draft.stake.max_pct} onChange={(v) => nest('stake', { max_pct: v })} suffix="% max stake" min={0.1} max={100} step={0.5} disabled={!draft.stake.on} w="w-40" />
            <Num value={draft.stake.max_amount} onChange={(v) => nest('stake', { max_amount: v })} prefix={sym} min={0} disabled={!draft.stake.on} w="w-28" />
          </Row>
          <Row label={<Toggle on={draft.mtg.on} onChange={(v) => nest('mtg', { on: v })} label="I take one martingale step" hint="Used to size the first trade so a lost cycle stays inside your risk share." />}>
            <Num value={draft.mtg.mult} onChange={(v) => nest('mtg', { mult: v })} prefix="×" min={1} max={10} step={0.1} disabled={!draft.mtg.on} w="w-24" />
          </Row>
          <Row label={<Toggle on={draft.count_cycles} onChange={(v) => patch({ count_cycles: v })} label="A martingale cycle counts as one trade" hint="A lost first trade followed by a lost step is ONE loss in the streak." />}><span /></Row>
          <Row label="Early warning at" hint="Heads-up when this share of a limit is used.">
            <Num value={Math.round(draft.warn_at * 100)} onChange={(v) => patch({ warn_at: v / 100 })} suffix="%" min={50} max={95} step={5} w="w-24" />
          </Row>
          <Row label="My trading day starts at" hint="Local time; the day's numbers reset then.">
            <Num value={draft.day_start_hour} onChange={(v) => patch({ day_start_hour: v })} suffix=":00" min={0} max={23} w="w-24" />
          </Row>
          <Row label={<Toggle on={draft.sound} onChange={(v) => patch({ sound: v })} label="Sound with guard alerts" />}><span /></Row>
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-4">
            <button className="btn btn-primary" disabled={!dirty || put.isPending} onClick={() => put.mutate(draft)}>{put.isPending ? 'Saving…' : 'Save rules'}</button>
            <button className="btn" disabled={!dirty} onClick={() => setDraft(saved)}>Discard</button>
            {put.isError && <span className="text-sm text-down">{(put.error as Error).message}</span>}
          </div>
        </Card>

        <div className="space-y-4 xl:col-span-2">
          <Card title="Alerts" right={<span className="note">on your Quotex page</span>}>
            <p className="note mb-2">Preview how each alert looks and sounds on the Quotex tab:</p>
            <div className="flex flex-wrap gap-2">
              {([['success', 'Target'], ['info', 'Heads-up'], ['warn', 'Warning'], ['danger', 'Stop']] as const).map(([k, l]) => (
                <button key={k} className="btn btn-sm" style={{ borderColor: `${LVL[k].c}66`, color: LVL[k].c }} onClick={() => test.mutate(k)}>{LVL[k].icon} {l}</button>))}
            </div>
            {sent && <p className="mt-2 text-xs text-up" role="status">{sent}</p>}
            <div className="mt-4 max-h-[340px] space-y-2 overflow-y-auto pr-1">
              {g.alerts.length === 0 ? <Empty>No guard alerts yet. They appear here as they happen.</Empty> : g.alerts.map((a) => (
                <div key={a.ts + a.title} className="flex gap-3 rounded-lg border border-line bg-panel2 p-2.5">
                  <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-sm font-bold" style={{ background: `${LVL[a.level].c}26`, color: LVL[a.level].c }} aria-hidden="true">{LVL[a.level].icon}</span>
                  <div className="min-w-0"><div className="flex items-baseline justify-between gap-2"><div className="text-sm font-semibold">{a.title}</div><div className="shrink-0 text-[11px] text-mut" title={new Date(a.ts * 1000).toLocaleString()}>{ago(a.ts, g.now)}</div></div>
                    <div className="text-xs leading-snug text-mut">{a.message}</div></div>
                </div>))}
            </div>
          </Card>

          <Card title="Last 14 days">
            {g.days.length === 0 ? <Empty>Your finished trading days appear here.</Empty> : (
              <table className="w-full text-sm">
                <thead><tr>{['Day', 'Result', 'Trades', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
                <tbody>{g.days.map((d) => (
                  <tr key={d.day}>
                    <td className="td whitespace-nowrap">{d.day.slice(5)}{d.day === g.day && <span className="ml-1 text-[10px] text-acc">today</span>}</td>
                    <td className={`td font-semibold tabular-nums ${d.net > 0 ? 'text-up' : d.net < 0 ? 'text-down' : ''}`}>{money(d.net, true)}</td>
                    <td className="td tabular-nums text-mut">{d.trades}</td>
                    <td className="td"><span className="flex flex-wrap justify-end gap-1">
                      {d.target_hit && <Badge tone="up" title="Daily target reached">🎯</Badge>}
                      {d.loss_hit && <Badge tone="down" title="Daily loss limit hit">⛔</Badge>}
                      {d.overtraded && <Badge tone="warn" title={`Overtraded: ${d.after_target + d.after_loss} trade(s) after a stop`}>⚠ over</Badge>}
                      {d.longest_streak >= g.settings.streak.max && g.settings.streak.on && <Badge tone="mut" title="Longest losing streak">{d.longest_streak}L</Badge>}
                    </span></td>
                  </tr>))}</tbody>
              </table>
            )}
            <p className="note mt-2">Past days are judged with today's rules and a balance worked back from today's, so deposits and withdrawals are not accounted for.</p>
          </Card>
        </div>
      </div>

      <p className="note mt-4">{pct(g.settings.warn_at, 0)} early warnings · {g.settings.count_cycles ? 'martingale cycles count as one trade' : 'every position counts'} · alerts reach the Quotex page through the extension (reload it once after this update).</p>
    </>
  );
}
