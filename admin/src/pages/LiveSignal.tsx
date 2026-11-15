import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, qs, Signal, Strategy } from '../api';
import { dateTime, pct, prettyPair, tfName } from '../format';
import { Board, LiveSelection, streamingCount, strategyName, usePersisted, usePresets, useSettings, useStrategies } from '../hooks';
import { Badge, Card, DirBadge, Empty, ErrorBox, ResultBadge, Spinner, StreamBubble, VerdictBadge } from '../components/ui';
import { TrackRecord } from '../components/TrackRecord';
import { FeedBanner } from '../components/FeedBanner';
import { ReadinessBar, Readiness, useReadiness } from '../components/ReadinessBar';
import { SmartChart } from '../components/chart/SmartChart';

// ------------------------------------------------------------------ small building blocks
type Mode = 'paused' | 'paper' | 'live';
const MODES: { key: Mode; label: string; on: string }[] = [
  { key: 'paused', label: 'Off', on: 'bg-warn/20 text-warn border-warn/50' },
  { key: 'paper', label: 'Paper', on: 'bg-acc/25 text-[#c4b5fd] border-acc/60' },
  { key: 'live', label: 'Live', on: 'bg-up/20 text-up border-up/50' },
];

function Segmented({ value, onChange, disabled }: { value: string; onChange: (m: Mode) => void; disabled?: boolean }) {
  return (
    <div className="inline-flex overflow-hidden rounded-lg border border-line bg-panel2 text-xs font-semibold" role="group">
      {MODES.map((m) => (
        <button key={m.key} disabled={disabled} onClick={() => value !== m.key && onChange(m.key)}
          className={`border-r border-line px-3 py-1.5 transition last:border-r-0 ${value === m.key ? m.on : 'text-mut hover:text-fg'}`}>{m.label}</button>
      ))}
    </div>
  );
}

function Switch({ on, onChange, busy }: { on: boolean; onChange: (v: boolean) => void; busy?: boolean }) {
  return (
    <button role="switch" aria-checked={on} disabled={busy} onClick={() => onChange(!on)}
      className={`relative h-7 w-14 rounded-full border transition ${on ? 'border-up/60 bg-up/25' : 'border-down/50 bg-down/15'}`}>
      <span className={`absolute top-0.5 h-5.5 w-5.5 rounded-full transition-all ${on ? 'left-[30px] bg-up' : 'left-0.5 bg-down'}`} style={{ width: 22, height: 22 }} />
    </button>
  );
}

function useClickOutside(onOut: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && onOut();
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [onOut]);
  return ref;
}

// ------------------------------------------------------------------ control bar: master alerts, pause/resume all, presets
function ControlBar({ strategies }: { strategies: Strategy[] }) {
  const qc = useQueryClient();
  const settings = useSettings();
  const presets = usePresets();
  const [menu, setMenu] = useState(false);
  const menuRef = useClickOutside(() => setMenu(false));
  const refresh = () => { qc.invalidateQueries({ queryKey: ['strategies'] }); qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['presets'] }); qc.invalidateQueries({ queryKey: ['live-sel'] }); };
  const alerts = useMutation({ mutationFn: (v: boolean) => api('/api/settings', { body: { alerts_enabled: v } }), onSuccess: refresh });
  const pause = useMutation({ mutationFn: () => api('/api/strategies/pause-all', { method: 'POST', body: {} }), onSuccess: refresh });
  const resume = useMutation({ mutationFn: () => api('/api/strategies/resume-all', { method: 'POST', body: {} }), onSuccess: refresh });
  const apply = useMutation({ mutationFn: (name: string) => api('/api/presets/apply', { body: { name } }), onSuccess: () => { setMenu(false); refresh(); } });
  const save = useMutation({ mutationFn: (name: string) => api('/api/presets', { body: { name } }), onSuccess: () => { setMenu(false); refresh(); } });
  const del = useMutation({ mutationFn: (name: string) => api(`/api/presets/${encodeURIComponent(name)}`, { method: 'DELETE' }), onSuccess: refresh });

  const count = (s: string) => strategies.filter((x) => x.status === s).length;
  const running = count('live') + count('paper');
  const canResume = settings.data?.can_resume;
  const alertsOn = settings.data?.alerts_enabled ?? true;
  const err = alerts.error ?? pause.error ?? resume.error ?? apply.error ?? save.error;

  return (
    <div className="mb-4 rounded-2xl border border-line bg-gradient-to-br from-panel to-panel2 p-4">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex items-center gap-3">
          <Switch on={alertsOn} busy={alerts.isPending} onChange={(v) => alerts.mutate(v)} />
          <div><div className="text-sm font-bold">Alerts {alertsOn ? 'ON' : 'OFF'}</div>
            <div className="note">{alertsOn ? 'live strategies notify you' : 'master switch: nothing will notify'}</div></div>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <Badge tone="up">{count('live')} live</Badge><Badge tone="acc">{count('paper')} paper</Badge><Badge tone="warn">{count('paused')} off</Badge>
          <span className="note">{running} running</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {canResume
            ? <button className="btn btn-primary" disabled={resume.isPending} onClick={() => resume.mutate()}>▶ Resume all (restore previous modes)</button>
            : <button className="btn btn-danger" disabled={pause.isPending || running === 0} onClick={() => confirm(`Pause all ${running} running strategies? Nothing will be recorded or alerted until you resume.`) && pause.mutate()}>⏸ Pause all</button>}
          <div className="relative" ref={menuRef}>
            <button className="btn" onClick={() => setMenu(!menu)}>Presets ▾</button>
            {menu && (
              <div className="absolute right-0 z-30 mt-2 w-80 rounded-xl border border-line bg-bg p-2 shadow-2xl">
                <div className="label px-2">Built-in</div>
                {presets.data?.builtin.map((p) => (
                  <button key={p.name} className="block w-full rounded-lg px-2 py-1.5 text-left hover:bg-panel" onClick={() => confirm(`Apply preset "${p.name}"? ${p.description}`) && apply.mutate(p.name)}>
                    <div className="text-sm font-semibold capitalize">{p.name}</div><div className="note">{p.description}</div></button>))}
                <div className="label mt-2 px-2">Your presets</div>
                {presets.data?.custom.length === 0 && <div className="note px-2 pb-1">None yet.</div>}
                {presets.data?.custom.map((p) => (
                  <div key={p.name} className="flex items-center justify-between rounded-lg px-2 py-1.5 hover:bg-panel">
                    <button className="flex-1 text-left" onClick={() => apply.mutate(p.name)}><span className="text-sm font-semibold">{p.name}</span> <span className="note">{p.count} strategies</span></button>
                    <button className="btn btn-sm btn-danger" onClick={() => del.mutate(p.name)} title="Delete preset">✕</button>
                  </div>))}
                <button className="btn mt-2 w-full" onClick={() => { const n = prompt('Name for the current setup (which strategies are live / paper / off):'); if (n) save.mutate(n); }}>＋ Save current setup as preset…</button>
              </div>
            )}
          </div>
          <a className="btn" href="/" target="_blank" rel="noreferrer" title="The original single-page live dashboard">Classic view ↗</a>
        </div>
      </div>
      {err && <div className="mt-3"><ErrorBox error={err} /></div>}
    </div>
  );
}

// ------------------------------------------------------------------ strategy control grid
function StrategyGrid({ strategies }: { strategies: Strategy[] }) {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [tag, setTag] = useState('');
  const [sel, setSel] = useState<number[]>([]);
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('qxs-admin-strats-open') === '1'; } catch { return false; } });   // collapsed by default
  const toggleOpen = () => { const v = !open; setOpen(v); try { localStorage.setItem('qxs-admin-strats-open', v ? '1' : '0'); } catch { /* storage unavailable */ } };
  const bulk = useMutation({
    mutationFn: (v: { ids: number[]; status: Mode }) => api('/api/strategies/bulk', { body: v }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); qc.invalidateQueries({ queryKey: ['live-sel'] }); },
  });
  const usable = strategies.filter((s) => s.status === 'live' || s.status === 'paper' || s.status === 'paused');
  const tags = useMemo(() => [...new Set(usable.flatMap((s) => s.tags))].sort(), [usable]);
  const order: Record<string, number> = { live: 0, paper: 1, paused: 2 };
  const rows = usable.filter((s) => (!tag || s.tags.includes(tag)) && (s.name + s.key).toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => order[a.status] - order[b.status] || b.stats.settled - a.stats.settled || a.name.localeCompare(b.name));
  const visibleIds = rows.map((r) => r.id);
  const setMode = (ids: number[], status: Mode) => {
    if (status === 'live' && ids.length > 1 && !confirm(`${ids.length} strategies will start sending alerts. Continue?`)) return;
    bulk.mutate({ ids, status });
  };

  const liveNames = usable.filter((s) => s.status === 'live').map((s) => s.name);
  return (
    <div className="rounded-2xl border border-line bg-panel">
      <button onClick={toggleOpen} aria-expanded={open} className="flex w-full items-center gap-3 px-5 py-4 text-left">
        <span className={`text-mut transition ${open ? 'rotate-90' : ''}`}>▸</span>
        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold uppercase tracking-wide text-mut">Strategies - choose what runs</div>
          <div className="note truncate">{usable.length} strategies · {liveNames.length ? `live: ${liveNames.join(', ')}` : 'none live'}{open ? '' : ' · click to expand'}</div>
        </div>
        <span className="note hidden md:block">Off = not running · Paper = recorded &amp; measured, no alerts · Live = alerts on</span>
      </button>
      {open && <div className="border-t border-line p-5">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input className="input !w-56" placeholder="Search strategies…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="flex flex-wrap gap-1.5">
          <button onClick={() => setTag('')} className={`chip ${!tag ? 'border-acc/60 bg-acc/20 text-[#c4b5fd]' : 'border-line text-mut hover:text-fg'}`}>all</button>
          {tags.map((t) => <button key={t} onClick={() => setTag(tag === t ? '' : t)} className={`chip ${tag === t ? 'border-acc/60 bg-acc/20 text-[#c4b5fd]' : 'border-line text-mut hover:text-fg'}`}>{t}</button>)}
        </div>
        <div className="ml-auto flex items-center gap-2 text-sm">
          <button className="btn btn-sm" onClick={() => setSel(visibleIds)}>Select visible</button>
          <button className="btn btn-sm" disabled={!sel.length} onClick={() => setSel([])}>Clear</button>
        </div>
      </div>

      {sel.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-3 rounded-xl border border-acc/50 bg-acc/10 px-4 py-2.5">
          <b>{sel.length} selected</b><span className="note">apply to all selected:</span>
          <Segmented value="" onChange={(m) => { setMode(sel, m); }} disabled={bulk.isPending} />
          <button className="btn btn-sm" onClick={() => setSel([])}>Done</button>
        </div>
      )}
      {bulk.isError && <div className="mb-3"><ErrorBox error={bulk.error} /></div>}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {rows.map((s) => {
          const picked = sel.includes(s.id);
          return (
            <div key={s.id} className={`rounded-xl border p-3 transition ${picked ? 'border-acc bg-acc/10' : s.status === 'live' ? 'border-up/40 bg-up/5' : 'border-line bg-panel2'}`}>
              <div className="flex items-start gap-2.5">
                <input type="checkbox" className="mt-1 h-4 w-4 accent-[#8b5cf6]" checked={picked} onChange={(e) => setSel(e.target.checked ? [...sel, s.id] : sel.filter((x) => x !== s.id))} />
                <div className="min-w-0 flex-1">
                  <Link to={`/strategies/${s.id}`} className="block truncate font-semibold hover:text-acc" title={s.name}>{s.name}</Link>
                  <div className="note truncate">{s.tags.join(' · ') || (s.kind === 'custom' ? 'custom' : '')}</div>
                </div>
                {s.muted && <Badge tone="down" title="auto-muted: recent results clearly below break-even">MUTED</Badge>}
                {s.error && <Badge tone="down" title={s.error.join('; ')}>ERROR</Badge>}
              </div>
              <div className="mt-3 flex items-center justify-between gap-2">
                <Segmented value={s.status} disabled={bulk.isPending} onChange={(m) => setMode([s.id], m)} />
                <div className="text-right text-xs tabular-nums">
                  <div className="font-semibold">{s.stats.total} signals · {pct(s.stats.accuracy, 0)}</div>
                  <div className="mt-0.5"><VerdictBadge settled={s.stats.settled} lo={s.stats.ci_low} hi={s.stats.ci_high} be={s.stats.breakeven} /></div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {rows.length === 0 && <Empty>No strategies match.</Empty>}
      </div>}
    </div>
  );
}

// ------------------------------------------------------------------ pair picker + live view + signal board
function PairPicker({ data, sym, onPick }: { data: LiveSelection; sym: string; onPick: (s: string) => void }) {
  const [market, setMarket] = usePersisted<'OTC' | 'LIVE'>('pair-market', 'OTC');
  const [q, setQ] = useState('');
  const [onlyStream, setOnlyStream] = usePersisted('pair-streaming-only', false);
  const list = data.symbols.filter((s) => s.market === market && (!onlyStream || s.streaming) && (s.name + s.symbol).toLowerCase().includes(q.toLowerCase()));
  return (
    <Card title="Pair">
      <div className="mb-2 flex gap-1.5">
        {(['OTC', 'LIVE'] as const).map((m) => {
          const n = streamingCount(data.symbols, m);
          return (
            <button key={m} onClick={() => setMarket(m)} className={`relative flex-1 rounded-lg border px-3 py-1.5 text-sm font-semibold ${market === m ? 'border-acc bg-acc text-white' : 'border-line bg-panel2 text-mut hover:text-fg'}`}>
              {m === 'OTC' ? 'OTC market' : 'Live market'}
              <StreamBubble n={n} />
            </button>);
        })}
      </div>
      <div className="mb-2 flex items-center gap-2">
        <input className="input" placeholder="Search pairs…" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="flex shrink-0 items-center gap-1.5 text-xs text-mut"><input type="checkbox" checked={onlyStream} onChange={(e) => setOnlyStream(e.target.checked)} />streaming</label>
      </div>
      <div className="grid max-h-56 grid-cols-2 gap-1.5 overflow-y-auto pr-1">
        {list.map((s) => (
          <button key={s.symbol} onClick={() => onPick(s.symbol)}
            className={`flex items-center gap-2 rounded-lg border px-2 py-1.5 text-left text-xs ${s.symbol === sym ? 'border-acc bg-acc/15 text-[#c4b5fd]' : 'border-line bg-panel2 hover:border-acc/60'} ${s.streaming ? '' : 'opacity-55'}`}>
            <span className={`h-2 w-2 shrink-0 rounded-full ${s.streaming ? 'bg-up shadow-[0_0_6px_#22c55e]' : 'bg-[#475069]'}`} />
            <span className="min-w-0 flex-1 truncate font-mono">{s.name.replace(' (OTC)', '')}</span><span className={`shrink-0 font-semibold tabular-nums ${s.payout == null ? 'text-mut' : s.payout >= 90 ? 'text-up' : s.payout >= 80 ? 'text-fg' : 'text-warn'}`} title="Return if the trade wins (payout)">{s.payout == null ? '–' : `${s.payout}%`}</span>
          </button>))}
        {list.length === 0 && <div className="note col-span-2 p-3 text-center">No pairs.</div>}
      </div>
      <p className="note mt-2">Green dot = streaming (open in your Quotex tab). Only streaming pairs produce live signals.</p>
    </Card>
  );
}

function LiveView({ sel, tf, setTf }: { sel: NonNullable<LiveSelection['selected']>; tf: number; setTf: (t: number) => void }) {
  const [, force] = useState(0);
  useEffect(() => { const t = setInterval(() => force((n) => n + 1), 250); return () => clearInterval(t); }, []);
  const rem = sel.forming ? Math.max(0, sel.forming.t + sel.tf - (Date.now() / 1000 + sel.offset)) : null;
  const last = sel.candles[sel.candles.length - 1];
  const prevC = last?.c;
  const price = sel.last_price ?? sel.forming?.c ?? prevC;
  const change = price != null && last ? price - last.o : null;
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-xl font-extrabold">{prettyPair(sel.symbol)}</div>
          <div className="flex items-baseline gap-3"><span className="text-3xl font-bold tabular-nums">{price ?? '–'}</span>
            {change != null && <span className={`text-sm font-semibold tabular-nums ${change >= 0 ? 'text-up' : 'text-down'}`}>{change >= 0 ? '▲' : '▼'} {Math.abs(change).toFixed(5)}</span>}</div>
        </div>
        <div className="flex items-center gap-4">
          {sel.sentiment && <div className="w-36"><div className="note">Trader sentiment</div><div className="h-2 overflow-hidden rounded bg-line"><div className="h-full rounded bg-gradient-to-r from-down via-warn to-up" style={{ width: `${sel.sentiment.pct}%` }} /></div><div className="note text-right">{sel.sentiment.pct}%</div></div>}
          {rem !== null && <div className="text-right"><div className="note">Candle closes in</div><div className="text-2xl font-bold tabular-nums">{Math.floor(rem / 60)}:{String(Math.floor(rem % 60)).padStart(2, '0')}</div></div>}
          <div className="inline-flex overflow-hidden rounded-lg border border-line text-sm font-semibold">
            {[60, 300, 900].map((t) => <button key={t} onClick={() => setTf(t)} className={`border-r border-line px-3 py-1.5 last:border-r-0 ${tf === t ? 'bg-acc text-white' : 'bg-panel2 text-mut hover:text-fg'}`}>{tfName(t)}</button>)}
          </div>
        </div>
      </div>
      <SmartChart symbol={sel.symbol} tf={sel.tf} live={{ candles: sel.candles, forming: sel.forming, lastPrice: sel.last_price }} height={340} initialCount={90} limit={1500} />
      {sel.gap && <div className="note mt-2 text-warn">Data gap: {sel.gap.missing} candle(s) missing before {dateTime(sel.gap.before)} - signals pause for a few candles after a gap.</div>}
    </Card>
  );
}

function BoardItem({ s, open, toggle, ready }: { s: import('../hooks').BoardRow; open: boolean; toggle: () => void; ready?: Readiness }) {
  const active = s.direction !== 'SKIP';
  const strategies = useStrategies();
  const single = s.factors.length <= 1;                    // a one-rule strategy: "strength" would always read 100, which says nothing about the chance of winning
  return (
    <div className={`overflow-hidden rounded-lg border ${s.direction === 'CALL' ? 'border-up/30 bg-up/5' : s.direction === 'PUT' ? 'border-down/30 bg-down/5' : 'border-line bg-panel2'}`}>
      <button onClick={toggle} aria-expanded={open} className="grid w-full grid-cols-[1fr_auto_16px] items-center gap-3 px-3 py-2 text-left">
        <div className="min-w-0">
          <div className="flex items-center gap-2"><span className="truncate font-semibold">{s.name}</span>{s.status === 'live' ? <Badge tone="up">live</Badge> : <Badge tone="acc">paper</Badge>}{s.tier && <Badge>Tier {s.tier}</Badge>}</div>
          {active && !single && <div className="mt-1 h-1.5 w-40 overflow-hidden rounded bg-line"><div className={`h-full ${s.direction === 'CALL' ? 'bg-up' : 'bg-down'}`} style={{ width: `${s.strength}%` }} /></div>}
          <ReadinessBar r={ready} />
        </div>
        <div className="text-right">{active ? <><DirBadge dir={s.direction} /><div className="note">{single ? 'rule matched' : `agreement ${s.strength}`}</div></> : <span className="note">{s.warming ? 'warming up' : 'no signal'}</span>}</div>
        <span className={`text-mut transition ${open ? 'rotate-180' : ''}`}>▾</span>
      </button>
      {open && (
        <div className="border-t border-line px-3 pb-3 pt-2">
          <div className="note mb-1">{active ? (single ? <>One rule, and it matched. A signal like this has no strength to read - what counts is how this strategy has done so far:</> : <>Score <b className="text-fg">{s.score > 0 ? '+' : ''}{s.score}</b> · agreement <b className="text-fg">{s.strength}</b> · {s.agree} factor{s.agree === 1 ? '' : 's'} agree</>) : <><b className="text-fg">Why no signal:</b> {s.warming ? 'still warming up' : s.reason || 'no factor active'}</>}</div>
          {active && <div className="mb-2"><TrackRecord stats={strategies.data?.find((x) => x.id === s.id)?.stats} note={false} /></div>}
          {s.factors.map((f) => (
            <div key={f.key} className="grid grid-cols-[48px_1fr_auto] items-center gap-2 border-t border-line py-1.5 first:border-0">
              <Badge tone="acc">{f.weight}%</Badge><div><div className="font-medium">{f.name}</div><div className="note">{f.note}</div></div>
              <span className={`font-bold tabular-nums ${f.vote > 0 ? 'text-up' : f.vote < 0 ? 'text-down' : 'text-mut'}`}>{f.vote > 0 ? '+' : ''}{f.vote}</span>
            </div>))}
        </div>
      )}
    </div>
  );
}

function SignalBoard({ sym, tf }: { sym: string; tf: number }) {
  const q = useQuery({ queryKey: ['board', sym, tf], queryFn: () => api<Board>(`/api/analyze/all${qs({ symbol: sym, tf })}`), refetchInterval: 2000, enabled: !!sym });
  const [showNeutral, setShowNeutral] = useState(false);
  const [openIds, setOpenIds] = useState<number[]>([]);
  const ready = useReadiness(sym, tf).data?.strategies;                      // live progress bars: how close each strategy is to a signal
  const toggle = (id: number) => setOpenIds((o) => (o.includes(id) ? o.filter((x) => x !== id) : [...o, id]));
  if (!sym) return null;
  const b = q.data;
  const active = b?.strategies.filter((s) => s.direction !== 'SKIP') ?? [];
  const neutral = b?.strategies.filter((s) => s.direction === 'SKIP') ?? [];
  const total = b ? b.strategies.length : 0;
  return (
    <Card title="Signal board - what every running strategy says now" right={<span className="note">last closed candle · {tfName(tf)}</span>}>
      {!b ? <Spinner /> : total === 0 ? <Empty>No strategy is running on this pair/timeframe. Switch some on above.</Empty> : (
        <>
          <div className="mb-3">
            <div className="mb-1 flex justify-between text-xs font-semibold"><span className="text-up">▲ {b.calls} CALL</span><span className="text-mut">{b.neutral} neutral</span><span className="text-down">{b.puts} PUT ▼</span></div>
            <div className="flex h-2.5 overflow-hidden rounded-full bg-line">
              <div className="bg-up" style={{ width: `${(b.calls / total) * 100}%` }} /><div className="bg-[#2a3358]" style={{ width: `${(b.neutral / total) * 100}%` }} /><div className="bg-down" style={{ width: `${(b.puts / total) * 100}%` }} />
            </div>
            {b.candles < 60 && <div className="note mt-2 text-warn">Only {b.candles} candles stored for this pair - strategies are still warming up.</div>}
          </div>
          <div className="space-y-1.5">
            {active.map((s) => <BoardItem key={s.id} s={s} open={openIds.includes(s.id)} toggle={() => toggle(s.id)} ready={ready?.[String(s.id)]} />)}
            {active.length === 0 && <div className="note rounded-lg bg-panel2 p-3 text-center">No strategy has a signal on the last closed candle.</div>}
          </div>
          {neutral.length > 0 && (
            <div className="mt-3">
              <button className="note hover:text-fg" onClick={() => setShowNeutral(!showNeutral)}>{showNeutral ? '▾ Hide' : '▸ Show'} {neutral.length} strategies with no signal</button>
              {showNeutral && <div className="mt-2 space-y-1.5">{neutral.map((s) => <BoardItem key={s.id} s={s} open={openIds.includes(s.id)} toggle={() => toggle(s.id)} ready={ready?.[String(s.id)]} />)}</div>}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function RecentSignals({ sym, strategies }: { sym: string; strategies: Strategy[] }) {
  const [scope, setScope] = useState<'pair' | 'all'>('all');
  const q = useQuery({ queryKey: ['live-recent', scope, sym], queryFn: () => api<{ rows: Signal[] }>(`/api/signals${qs({ limit: 14, symbol: scope === 'pair' ? sym : undefined })}`), refetchInterval: 4000 });
  const right: ReactNode = (
    <div className="inline-flex overflow-hidden rounded-lg border border-line text-xs font-semibold">
      {(['all', 'pair'] as const).map((s) => <button key={s} onClick={() => setScope(s)} className={`px-3 py-1 ${scope === s ? 'bg-acc text-white' : 'bg-panel2 text-mut'}`}>{s === 'all' ? 'All pairs' : 'This pair'}</button>)}
    </div>
  );
  return (
    <Card title="Latest signals" right={right}>
      {!q.data ? <Spinner /> : q.data.rows.length === 0 ? <Empty>No signals yet.</Empty> : (
        <div className="overflow-x-auto"><table className="w-full">
          <thead><tr>{['Candle', 'Pair', 'TF', 'Strategy', 'Signal', 'Tier', 'Str.', 'Mode', 'Result'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
          <tbody>{q.data.rows.map((r) => (
            <tr key={r.id}><td className="td tabular-nums">{dateTime(r.target_t)}</td><td className="td">{prettyPair(r.symbol)}</td><td className="td">{tfName(r.tf)}</td>
              <td className="td note">{strategyName(strategies, r.strategy_id)}</td><td className="td"><DirBadge dir={r.direction} /></td><td className="td">{r.tier}</td><td className="td tabular-nums">{r.strength}</td>
              <td className="td">{r.mode === 'paper' ? <Badge tone="acc">paper</Badge> : <Badge tone="up">live</Badge>}</td><td className="td"><ResultBadge status={r.status} /></td></tr>))}</tbody></table></div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ page
export default function LiveSignal() {
  const strategies = useStrategies();
  const [pick, setPick] = useState('');
  const [tf, setTf] = useState(60);
  const q = useQuery({ queryKey: ['live-sel', pick, tf], queryFn: () => api<LiveSelection>(`/api/state${qs({ symbol: pick, tf })}`), refetchInterval: 1500, placeholderData: keepPreviousData });   // keep the picker mounted while a new pair loads
  const sym = pick || q.data?.selected?.symbol || '';
  useEffect(() => { if (!pick && q.data?.selected?.symbol) setPick(q.data.selected.symbol); }, [pick, q.data]);

  if (!strategies.data) return <Spinner />;
  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-bold">Quotex Signal</h1>
          <p className="mt-1 text-sm text-mut">Run the strategies you want, watch the pair you trade, and see every strategy's opinion in one place. Accuracy is measured from settled signals, never promised.</p></div>
      </div>
      <FeedBanner health={q.data?.health} />
      <ControlBar strategies={strategies.data} />
      <StrategyGrid strategies={strategies.data} />

      <div className="mt-4 grid gap-4 xl:grid-cols-[320px_1fr]">
        <div>{q.data ? <PairPicker data={q.data} sym={sym} onPick={setPick} /> : <Card><Spinner /></Card>}</div>
        <div className="min-w-0 space-y-4">
          {q.isError ? <ErrorBox error={q.error} /> : !q.data ? <Card><Spinner /></Card> : q.data.selected
            ? <LiveView sel={q.data.selected} tf={tf} setTf={setTf} />
            : <Card><Empty>No pair is streaming. Open a chart on Quotex with the extension enabled, or pick a pair on the left to see its stored candles.</Empty></Card>}
          <SignalBoard sym={sym} tf={tf} />
        </div>
      </div>
      <div className="mt-4"><RecentSignals sym={sym} strategies={strategies.data} /></div>
    </>
  );
}
