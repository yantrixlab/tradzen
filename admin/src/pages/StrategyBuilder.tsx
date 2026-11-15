import { ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../api';
import { Catalog, useCatalog, useCoverage, usePersisted } from '../hooks';
import { blankFactor, blankForm, FactorForm, FilterForm, formToSpec, IndicatorForm, SpecForm, specToForm } from '../lib/specModel';
import { Badge, Card, ErrorBox, Field, PageTitle, Spinner, Tabs } from '../components/ui';
import { prettyPair, tfName } from '../format';
import { BuilderChart, indColor, useChartQuery } from '../components/StrategyChart';
import { BuilderProvider, encode, Hl, NameGroup, useBuilder } from '../components/builderContext';
import { ConditionEditor, Toolbox, useDrop } from '../components/ConditionEditor';
import { PatternEditor } from '../components/PatternEditor';
import { mirror, PATTERN_RECIPES, PatternForm } from '../lib/patterns';
import { Clause, INDICATOR_INFO, indicatorTitle, nameText, newId, parse, RECIPES, serialize, suggestId } from '../lib/conditions';

interface Validation { ok: boolean; errors: string[]; warnings: string[] }
interface Preview { warnings: string[]; analysis: { direction: string; score: number; strength: number; agree: number; tier: string; reason: string; candles: number; need: number; factors: { key: string; name: string; weight: number; vote: number; note: string }[] } }

const Grid = ({ children, cols = 2 }: { children: ReactNode; cols?: number }) => <div className={`grid gap-3 ${cols === 3 ? 'md:grid-cols-3' : cols === 4 ? 'md:grid-cols-4' : 'md:grid-cols-2'}`}>{children}</div>;
const NumInput = ({ value, onChange, step = 1 }: { value: number; onChange: (n: number) => void; step?: number }) => (
  <input className="input" type="number" step={step} value={Number.isFinite(value) ? value : ''} onChange={(e) => onChange(e.target.value === '' ? NaN : Number(e.target.value))} />
);

export default function StrategyBuilder({ mode }: { mode: 'new' | 'edit' | 'clone' }) {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const catalog = useCatalog();
  const coverage = useCoverage();
  const src = useQuery({ queryKey: ['strategy', id], enabled: mode !== 'new', queryFn: () => api<{ key: string; name: string; description: string; spec: Record<string, unknown>; kind: string; status: string; version: number }>(`/api/strategies/${id}`) });

  const [form, setForm] = useState<SpecForm>(blankForm());
  const [meta, setMeta] = useState({ key: '', name: '', description: '', status: 'paper', notes: '' });
  const [tab, setTab] = useState('visual');
  const [json, setJson] = useState('');
  const [jsonErr, setJsonErr] = useState('');
  const [loaded, setLoaded] = useState(mode === 'new');
  const [validation, setValidation] = useState<Validation | null>(null);

  useEffect(() => {
    if (src.data && !loaded) {
      setForm(specToForm(src.data.spec));
      setMeta({ key: mode === 'clone' ? `${src.data.key}-copy` : src.data.key, name: mode === 'clone' ? `${src.data.name} (copy)` : src.data.name, description: src.data.description, status: mode === 'clone' ? 'paper' : src.data.status, notes: '' });
      setLoaded(true);
    }
  }, [src.data, loaded, mode]);

  const spec = useMemo(() => formToSpec(form), [form]);

  // chart state: pair / timeframe are shared by the chart and "Evaluate now"
  const [pairSel, setPairSel] = useState('');
  const [tf, setTf] = useState(60);
  const [count, setCount] = useState(120);
  const [active, setActive] = useState(0);
  const [exprMap, setExprMap] = useState<Record<string, Record<string, string>>>({});
  const [patMap, setPatMap] = useState<Record<string, PatternForm>>({});
  const symbols = useMemo(() => {
    const rows = (coverage.data ?? []).filter((c) => c.tf === tf).sort((a, b) => b.candles - a.candles);
    const all = [...new Set((coverage.data ?? []).map((c) => c.symbol))];
    return [...new Set([...rows.map((r) => r.symbol), ...all])];
  }, [coverage.data, tf]);
  const pair = pairSel && symbols.includes(pairSel) ? pairSel : symbols[0] ?? '';
  const onExprs = useCallback((prefix: string, map: Record<string, string> | null) => setExprMap((prev) => {
    if (map === null) { if (!(prefix in prev)) return prev; const { [prefix]: _gone, ...rest } = prev; return rest; }
    return JSON.stringify(prev[prefix]) === JSON.stringify(map) ? prev : { ...prev, [prefix]: map };
  }), []);
  const onPattern = useCallback((prefix: string, p: PatternForm | null) => setPatMap((prev) => {
    if (p === null) { if (!(prefix in prev)) return prev; const { [prefix]: _gone, ...rest } = prev; return rest; }
    return JSON.stringify(prev[prefix]) === JSON.stringify(p) ? prev : { ...prev, [prefix]: p };
  }), []);
  const exprs = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [p, m] of Object.entries(exprMap)) if (m[p]) out[p] = m[p];                          // whole conditions first
    for (const [p, m] of Object.entries(exprMap)) for (const [k, v] of Object.entries(m)) if (k !== p) out[k] = v;
    return Object.fromEntries(Object.entries(out).slice(0, 80));
  }, [exprMap]);
  const chart = useChartQuery(spec, exprs, patMap, form.kind === 'factors' ? pair : '', tf, count);

  // live validation (debounced)
  useEffect(() => {
    const t = setTimeout(() => { api<Validation>('/api/strategies/validate', { body: { spec } }).then(setValidation).catch(() => setValidation(null)); }, 350);
    return () => clearTimeout(t);
  }, [spec]);

  const save = useMutation({
    mutationFn: async () => {
      if (mode === 'edit') {
        if (src.data && (meta.name !== src.data.name || meta.description !== src.data.description)) await api(`/api/strategies/${id}`, { method: 'PUT', body: { name: meta.name, description: meta.description } });
        return api<{ id: number }>(`/api/strategies/${id}/versions`, { body: { spec, notes: meta.notes } });
      }
      return api<{ id: number }>('/api/strategies', { body: { key: meta.key, name: meta.name, description: meta.description, spec, status: meta.status } });
    },
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['strategies'] }); qc.invalidateQueries({ queryKey: ['strategy'] }); nav(`/strategies/${r.id}`); },
  });

  if (!catalog.data || (mode !== 'new' && !loaded)) return src.isError ? <ErrorBox error={src.error} /> : <Spinner />;
  const cat = catalog.data;
  const upd = (patch: Partial<SpecForm>) => setForm((f) => ({ ...f, ...patch }));
  const switchTab = (t: string) => {
    if (t === 'json') { setJson(JSON.stringify(spec, null, 2)); setJsonErr(''); }
    setTab(t);
  };
  const onJson = (text: string) => {
    setJson(text);
    try { setForm(specToForm(JSON.parse(text))); setJsonErr(''); } catch (e) { setJsonErr(e instanceof Error ? e.message : 'invalid JSON'); }
  };

  const title = mode === 'new' ? 'New strategy' : mode === 'clone' ? `Clone: ${src.data?.name}` : `Edit: ${src.data?.name}`;

  // names a condition may use, grouped for the dropdowns and the toolbox
  const names: NameGroup[] = [
    { label: 'Your indicators', items: form.indicators.flatMap((ind, i) => {
      const fields = cat.indicators[ind.type]?.fields;
      return fields ? fields.map((f) => ({ name: `${ind.id}.${f}`, text: `${indicatorTitle(ind)} ${f}`, color: indColor(i) })) : [{ name: ind.id, text: indicatorTitle(ind), color: indColor(i) }];
    }) },
    { label: 'Price & candle', items: ['close', 'open', 'high', 'low', 'body', 'range', 'upper_wick', 'lower_wick', 'hlc3', 'streak', 'hour', 'weekday', 'ticks'].filter((x) => cat.base_names.includes(x)).map((x) => ({ name: x, text: nameText(x, []) })) },
    { label: 'Candle patterns', items: ['green', 'red', 'doji', 'hammer', 'star', 'bull_engulf', 'bear_engulf', 'inside_bar'].filter((x) => cat.base_names.includes(x)).map((x) => ({ name: x, text: nameText(x, []) })) },
  ];
  const groups: Hl[] = form.factors.filter((f) => f.kind !== 'fn').flatMap((f) => (['call', 'put'] as const)
    .filter((sd) => (f.kind === 'pattern' ? (sd === 'call' ? f.callPat : f.putPat)?.steps.length : f[sd].trim()))
    .map((sd) => ({ key: `${f.id}|${sd}`, label: `${f.name} · ${sd.toUpperCase()}`, tone: sd })));

  // a recipe adds the indicators it needs (reusing identical ones) and appends its sentences to one factor
  const applyRecipe = (rid: string, factorIdx: number, side: 'call' | 'put' | 'both') => {
    const r = RECIPES.find((x) => x.id === rid);
    if (!r) return;
    const inds = [...form.indicators], ids: Record<string, string> = {}, taken = new Set(inds.map((i) => i.id)), reserved = new Set(cat.base_names);
    for (const nd of r.needs) {
      const found = inds.find((i) => i.type === nd.type && Object.entries(nd.params).every(([k, v]) => i.params[k] === v));
      if (found) { ids[nd.key] = found.id; continue; }
      const id = suggestId(nd.type, nd.params, taken, reserved);
      taken.add(id);
      inds.push({ id, type: nd.type, params: { ...cat.indicators[nd.type].defaults, ...nd.params } });
      ids[nd.key] = id;
    }
    const built = r.build(ids);
    const append = (text: string, add: Clause[]) => {
      const g = parse(text);
      const base: Clause[] = g.mode === 'or' && g.clauses.length > 1 ? [{ id: newId(), raw: text, left: null, op: '>', right: null }] : g.clauses;
      return serialize({ mode: 'and', clauses: [...base, ...add] });
    };
    const idx = Math.min(factorIdx, form.factors.length - 1);
    upd({
      indicators: inds,
      factors: form.factors.map((f, i) => {
        if (i !== idx) return f;
        const fresh = f.kind === 'fn' ? { ...f, kind: 'expr' as const, fn: '', params: {}, call: '', put: '' } : f;
        return { ...fresh, call: side !== 'put' ? append(fresh.call, built.call) : fresh.call, put: side !== 'call' ? append(fresh.put, built.put) : fresh.put };
      }),
    });
  };
  const applyPatternRecipe = (rid: string, factorIdx: number, side: 'call' | 'put' | 'both') => {
    const r = PATTERN_RECIPES.find((x) => x.id === rid);
    if (!r) return;
    const idx = Math.min(factorIdx, form.factors.length - 1), copy = (p: PatternForm | undefined) => (p ? (JSON.parse(JSON.stringify(p)) as PatternForm) : null);
    upd({
      factors: form.factors.map((f, i) => (i !== idx ? f : {
        ...f, kind: 'pattern' as const, fn: '', params: {}, name: /^Factor \d+$/.test(f.name) ? r.title : f.name,
        callPat: side !== 'put' ? copy(r.call) : f.callPat, putPat: side !== 'call' ? copy(r.put) : f.putPat,
      })),
    });
  };
  const addIndicator = (type: string) => {
    const params = { ...cat.indicators[type].defaults };
    upd({ indicators: [...form.indicators, { id: suggestId(type, params, new Set(form.indicators.map((i) => i.id)), new Set(cat.base_names)), type, params }] });
  };

  const hasCond = form.factors.some((f) => f.kind === 'fn' || f.call.trim() || f.put.trim() || (f.callPat?.steps.length ?? 0) > 0 || (f.putPat?.steps.length ?? 0) > 0);
  const steps = [
    { title: 'Pick a pair', body: 'The chart below shows real candles from your feed. Choose the pair and timeframe where you want to test your idea.', done: !!chart.data },
    { title: 'Add indicators', body: 'Press an indicator in the Indicators card (RSI, EMA, Bollinger…). Each one is drawn on the chart in its own colour. Skip this if you only use price or candle patterns.', done: form.indicators.length > 0 || hasCond },
    { title: 'Write the rules', body: 'In Factors, finish the CALL (buy) and PUT (sell) rules: sentences built from Toolbox chips, or a candle pattern (a row of candle steps). Quick recipes and candle patterns give ready-made starts.', done: hasCond },
    { title: 'Check on the chart', body: 'Hover or click 👁 next to a rule: shaded columns show where it is true. ▲ / ▼ markers show where the whole strategy would have signalled. Adjust until it fires where you expect.', done: (chart.data?.signals.length ?? 0) > 0 },
    { title: 'Name & create', body: 'Give it a name and a key, keep Paper mode (measured without alerts), then press Create strategy. Judge it only after a few hundred settled signals.', done: !!validation?.ok && !!meta.name.trim() && (mode === 'edit' || !!meta.key.trim()) },
  ];

  return (
    <BuilderProvider marks={chart.data?.marks ?? {}} n={chart.data?.candles.length ?? 0} groups={groups} indicators={form.indicators} names={names} active={active} setActive={setActive}>
      <PageTitle title={title} sub={mode === 'edit' ? 'Saving creates a new immutable version; a live strategy returns to paper mode and has to earn alerts again.' : 'New strategies start in paper mode: signals are recorded and settled but no alerts fire until you promote them.'}
        right={<><Link className="btn" to={id ? `/strategies/${id}` : '/strategies'}>Cancel</Link>
          <button className="btn btn-primary" disabled={save.isPending || !validation?.ok || !meta.name.trim() || (mode !== 'edit' && !meta.key.trim())} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : mode === 'edit' ? 'Save new version' : 'Create strategy'}</button></>} />
      {save.isError && <div className="mb-3"><ErrorBox error={save.error instanceof ApiError ? save.error : save.error} /></div>}

      {form.kind === 'factors' && (
        <div className="mb-4 space-y-4">
          <Guide steps={steps} />
          <BuilderChart q={chart} symbols={symbols} pair={pair} setPair={setPairSel} tf={tf} setTf={setTf} count={count} setCount={setCount} />
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[1fr_400px]">
        <div className="min-w-0">
          <Tabs tabs={[{ key: 'visual', label: 'Visual builder' }, { key: 'json', label: 'JSON' }]} value={tab} onChange={switchTab} />
          {tab === 'json' ? (
            <Card title="Strategy spec (JSON)" right={jsonErr ? <span className="text-down">{jsonErr}</span> : <span className="text-up">valid JSON</span>}>
              <textarea className="input h-[620px] font-mono text-xs" spellCheck={false} value={json} onChange={(e) => onJson(e.target.value)} />
              <p className="note mt-2">Edits here update the visual builder as soon as the JSON parses. Conditions use a safe expression language - see the syntax help in the Toolbox.</p>
            </Card>
          ) : (
            <div className="space-y-4">
              <Card title="Basics">
                <Grid>
                  <Field label="Name"><input className="input" value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} /></Field>
                  <Field label="Key" hint={mode === 'edit' ? 'fixed after creation' : 'lowercase letters, digits, dashes'}>
                    <input className="input font-mono" disabled={mode === 'edit'} value={meta.key} onChange={(e) => setMeta({ ...meta, key: e.target.value.toLowerCase() })} /></Field>
                </Grid>
                <div className="mt-3"><Field label="Description"><textarea className="input" rows={2} value={meta.description} onChange={(e) => setMeta({ ...meta, description: e.target.value })} /></Field></div>
                {mode === 'edit' ? <div className="mt-3"><Field label="What changed (version note)"><input className="input" value={meta.notes} onChange={(e) => setMeta({ ...meta, notes: e.target.value })} /></Field></div>
                  : <div className="mt-3 w-56"><Field label="Start in mode"><select className="input" value={meta.status} onChange={(e) => setMeta({ ...meta, status: e.target.value })}><option value="paper">paper (recommended)</option><option value="draft">draft (not running)</option></select></Field></div>}
              </Card>

              <Card title="Where it runs">
                <div className="flex flex-wrap items-end gap-4">
                  <div><span className="label">Timeframes</span><div className="flex gap-3">{cat.timeframes.map((t) => (
                    <label key={t} className="flex items-center gap-1.5"><input type="checkbox" checked={form.timeframes.includes(t)} onChange={(e) => upd({ timeframes: e.target.checked ? [...form.timeframes, t] : form.timeframes.filter((x) => x !== t) })} />{tfName(t)}</label>))}</div></div>
                  <div className="w-40"><Field label="Market"><select className="input" value={form.market} onChange={(e) => upd({ market: e.target.value as SpecForm['market'] })}><option value="">OTC + Live</option><option value="OTC">OTC only</option><option value="LIVE">Live only</option></select></Field></div>
                  <div className="min-w-[220px] flex-1"><Field label="Only these pairs (optional)"><input className="input font-mono" placeholder="EURUSD_otc, GBPJPY_otc" value={form.symbols} onChange={(e) => upd({ symbols: e.target.value })} /></Field></div>
                </div>
              </Card>

              {form.kind === 'meta' ? (
                <Card title="Consensus (meta strategy)">
                  <Grid><Field label="Members" hint="'all_active' or a comma list of strategy keys"><input className="input font-mono" value={form.consensus.members} onChange={(e) => upd({ consensus: { ...form.consensus, members: e.target.value } })} /></Field>
                    <Field label="Minimum agreeing"><NumInput value={form.consensus.min_agree} onChange={(n) => upd({ consensus: { ...form.consensus, min_agree: n } })} /></Field></Grid>
                </Card>
              ) : (
                <>
                  <IndicatorsEditor form={form} cat={cat} upd={upd} add={addIndicator} />
                  <FactorsEditor form={form} cat={cat} upd={upd} onExprs={onExprs} onRecipe={applyRecipe} onPattern={onPattern} onPatternRecipe={applyPatternRecipe} active={active} setActive={setActive} />
                  <GatesEditor form={form} upd={upd} />
                  <FiltersEditor form={form} cat={cat} upd={upd} />
                </>
              )}
            </div>
          )}
        </div>

        <div className="min-w-0 space-y-4">
          <Card title="Validation">
            {!validation ? <div className="note">Checking…</div> : validation.ok ? <Badge tone="up">SPEC IS VALID</Badge> : null}
            {validation?.errors.map((e, i) => <div key={i} className="mt-2 rounded-lg border border-down/40 bg-down/10 p-2 text-xs text-down">{e}</div>)}
            {validation?.warnings.map((e, i) => <div key={i} className="mt-2 rounded-lg border border-warn/40 bg-warn/10 p-2 text-xs text-warn">{e}</div>)}
          </Card>
          {form.kind === 'factors' && <PreviewPanel spec={spec} valid={!!validation?.ok} pair={pair} tf={tf} />}
          {form.kind === 'factors' && (
            <div className="xl:sticky xl:top-2">
              <Card title="Toolbox · drag into your conditions"><div className="max-h-[70vh] overflow-y-auto pr-1">
                <Toolbox onRecipe={(rid) => applyRecipe(rid, active, 'both')} onAddIndicator={addIndicator} onPatternRecipe={(rid) => applyPatternRecipe(rid, active, 'both')} />
              </div></Card>
            </div>
          )}
        </div>
      </div>
    </BuilderProvider>
  );
}

function Guide({ steps }: { steps: { title: string; body: string; done: boolean }[] }) {
  const [open, setOpen] = usePersisted('builder-guide-open', true);
  const current = steps.findIndex((s) => !s.done);
  const doneN = steps.filter((s) => s.done).length;
  return (
    <Card title={<span>Step by step <span className="ml-2 normal-case tracking-normal text-mut">{doneN} of {steps.length} done</span></span>}
      right={<button className="btn btn-sm normal-case tracking-normal" onClick={() => setOpen(!open)}>{open ? 'Hide guide' : 'Show guide'}</button>}>
      {open ? (
        <ol className="grid gap-2 md:grid-cols-5">
          {steps.map((s, i) => (
            <li key={s.title} className={`rounded-lg border p-2.5 ${s.done ? 'border-up/40 bg-up/5' : i === current ? 'border-acc bg-acc/10' : 'border-line bg-panel2'}`}>
              <div className="flex items-center gap-2 text-sm font-semibold">
                <span className={`grid h-5 w-5 place-items-center rounded-full text-[11px] ${s.done ? 'bg-up text-black' : i === current ? 'bg-acc text-white' : 'bg-line text-mut'}`}>{s.done ? '✓' : i + 1}</span>{s.title}
              </div>
              <p className="mt-1 text-[11px] leading-snug text-mut">{s.body}</p>
            </li>))}
        </ol>
      ) : <div className="note">{current === -1 ? 'All steps done. Create the strategy when you are happy with the chart.' : `Next: ${steps[current].title}. ${steps[current].body}`}</div>}
    </Card>
  );
}

// ------------------------------------------------------------------ sections
type Upd = (p: Partial<SpecForm>) => void;

function IndicatorsEditor({ form, cat, upd, add }: { form: SpecForm; cat: Catalog; upd: Upd; add: (type: string) => void }) {
  const { setDrag } = useBuilder();
  const set = (i: number, p: Partial<IndicatorForm>) => upd({ indicators: form.indicators.map((x, j) => (j === i ? { ...x, ...p } : x)) });
  const drop = useDrop((p) => p.t === 'indicator', (p) => p.t === 'indicator' && add(p.type));
  // renaming an indicator keeps the conditions that mention it working
  const rename = (i: number, to: string) => {
    const from = form.indicators[i].id;
    const fix = (t: string) => (from ? t.replace(new RegExp(`(?<![\\w.])${from.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?!\\w)`, 'g'), to) : t);
    upd({ indicators: form.indicators.map((x, j) => (j === i ? { ...x, id: to } : x)), factors: to ? form.factors.map((f) => (f.kind === 'expr' ? { ...f, call: fix(f.call), put: fix(f.put) } : f)) : form.factors });
  };
  const groupsOf: [string, string[]][] = [['Drawn on the price chart', ['sma', 'ema', 'bollinger', 'donchian', 'supertrend', 'psar']], ['Drawn below the chart', ['rsi', 'stoch', 'macd', 'cci', 'williams', 'adx', 'atr', 'roc']], ['Used in conditions only', ['heikin']]];
  return (
    <Card title="Indicators" right={<span className="normal-case tracking-normal text-mut">press one to add it, or drag it in</span>}>
      <div {...drop.props} className={`mb-3 space-y-2 rounded-lg border border-dashed p-2 transition ${drop.over ? 'border-acc bg-acc/20' : drop.can ? 'border-acc bg-acc/10' : 'border-line'}`}>
        {groupsOf.map(([label, types]) => (
          <div key={label} className="flex flex-wrap items-center gap-1.5"><span className="w-44 text-[11px] text-mut">{label}</span>
            {types.filter((t) => cat.indicators[t]).map((t) => (
              <button key={t} draggable title={INDICATOR_INFO[t]?.about} className="btn btn-sm cursor-grab" onClick={() => add(t)}
                onDragStart={(e) => { const p = { t: 'indicator' as const, type: t }; e.dataTransfer.setData('text/plain', encode(p)); e.dataTransfer.effectAllowed = 'copy'; setDrag(p); }} onDragEnd={() => setDrag(null)}>
                + {INDICATOR_INFO[t]?.title ?? t}</button>))}
          </div>))}
      </div>
      {form.indicators.length === 0 && <div className="note">None yet. Add one above, then use it by name in a condition (e.g. <code>rsi14 &lt; 30</code> or <code>bb.lower</code>). It is drawn on the chart in the same colour as its chip.</div>}
      <div className="space-y-2">
        {form.indicators.map((ind, i) => (
          <div key={i} className="rounded-lg bg-panel2 p-2" style={{ borderLeft: `3px solid ${indColor(i)}` }}>
            <div className="flex flex-wrap items-end gap-2">
              <div className="w-28"><span className="label">id (used in conditions)</span><input className="input font-mono" value={ind.id} onChange={(e) => rename(i, e.target.value)} /></div>
              <div className="w-36"><span className="label">type</span>
                <select className="input" value={ind.type} onChange={(e) => set(i, { type: e.target.value, params: { ...cat.indicators[e.target.value].defaults } })}>
                  {Object.keys(cat.indicators).map((t) => <option key={t} value={t}>{INDICATOR_INFO[t]?.title ?? t}</option>)}</select></div>
              {Object.keys(cat.indicators[ind.type]?.defaults ?? {}).map((p) => (
                <div key={p} className="w-24"><span className="label">{p}</span><NumInput step={p === 'k' || p === 'mult' || p === 'step' || p === 'maximum' ? 0.01 : 1} value={ind.params[p]} onChange={(n) => set(i, { params: { ...ind.params, [p]: n } })} /></div>))}
              <button className="btn btn-sm btn-danger" onClick={() => upd({ indicators: form.indicators.filter((_, j) => j !== i) })}>✕</button>
            </div>
            <div className="mt-1 text-[11px] text-mut">{INDICATOR_INFO[ind.type]?.about}{cat.indicators[ind.type]?.fields ? <> Fields: {cat.indicators[ind.type].fields!.map((f) => <code key={f} className="mx-0.5">{ind.id}.{f}</code>)}</> : null}</div>
          </div>))}
      </div>
    </Card>
  );
}

function FactorsEditor({ form, cat, upd, onExprs, onRecipe, onPattern, onPatternRecipe, active, setActive }: {
  form: SpecForm; cat: Catalog; upd: Upd; onExprs: (prefix: string, map: Record<string, string> | null) => void;
  onRecipe: (rid: string, factorIdx: number, side: 'call' | 'put' | 'both') => void;
  onPattern: (prefix: string, p: PatternForm | null) => void; onPatternRecipe: (rid: string, factorIdx: number, side: 'call' | 'put' | 'both') => void;
  active: number; setActive: (i: number) => void;
}) {
  const set = (i: number, p: Partial<FactorForm>) => upd({ factors: form.factors.map((x, j) => (j === i ? { ...x, ...p } : x)) });
  return (
    <Card title="Factors (each votes CALL, PUT or abstains)" right={<button className="btn btn-sm" onClick={() => { upd({ factors: [...form.factors, blankFactor(form.factors.length + 1)] }); setActive(form.factors.length); }}>+ Add factor</button>}>
      <div className="space-y-3">
        {form.factors.map((f, i) => (
          <div key={i} onFocusCapture={() => setActive(i)} onMouseDown={() => setActive(i)}
            className={`rounded-lg border bg-panel2 p-3 ${active === i && form.factors.length > 1 ? 'border-acc/60' : 'border-line'}`}>
            <div className="flex flex-wrap items-end gap-2">
              <div className="w-32"><span className="label">id</span><input className="input font-mono" value={f.id} onChange={(e) => set(i, { id: e.target.value })} /></div>
              <div className="min-w-[160px] flex-1"><span className="label">name</span><input className="input" value={f.name} onChange={(e) => set(i, { name: e.target.value })} /></div>
              <div className="w-24"><span className="label">weight</span><NumInput value={f.weight} onChange={(n) => set(i, { weight: n })} /></div>
              <div className="w-40"><span className="label">kind</span>
                <select className="input" value={f.kind} onChange={(e) => set(i, { kind: e.target.value as FactorForm['kind'], fn: e.target.value === 'fn' ? Object.keys(cat.fn_factors)[0] : '', params: e.target.value === 'fn' ? { ...cat.fn_factors[Object.keys(cat.fn_factors)[0]] } : {} })}>
                  <option value="expr">your conditions</option><option value="pattern">candle pattern</option><option value="fn">built-in factor</option></select></div>
              {form.factors.length > 1 && <button className="btn btn-sm btn-danger" onClick={() => { upd({ factors: form.factors.filter((_, j) => j !== i) }); setActive(0); }}>✕</button>}
            </div>
            {f.kind === 'expr' ? (
              <div className="mt-3 space-y-3">
                {(['call', 'put'] as const).map((sd) => (
                  <ConditionEditor key={sd} side={sd} prefix={`${f.id}|${sd}`} title={`${f.name} · ${sd.toUpperCase()}`} value={f[sd]} onChange={(t) => set(i, { [sd]: t })}
                    onExprs={onExprs} onRecipe={(rid, side) => onRecipe(rid, i, side)} />))}
                <p className="note">Leave one side empty if this factor only votes one way. A factor votes CALL when its CALL condition is true and its PUT condition is not (and the other way round).</p>
              </div>
            ) : f.kind === 'pattern' ? (
              <div className="mt-3 space-y-3">
                {(['call', 'put'] as const).map((sd) => {
                  const key = sd === 'call' ? 'callPat' : 'putPat', other = sd === 'call' ? f.putPat : f.callPat;
                  return (
                    <div key={sd}>
                      <PatternEditor side={sd} prefix={`${f.id}|${sd}`} title={`${f.name} · ${sd.toUpperCase()}`} value={f[key]} onChange={(p) => set(i, { [key]: p })}
                        onPattern={onPattern} onRecipe={(rid, side) => onPatternRecipe(rid, i, side)} />
                      {other && other.steps.length > 0 && <button className="btn btn-sm mt-1" onClick={() => set(i, { [key]: mirror(other) })}>Replace with the mirror image of the {sd === 'call' ? 'PUT' : 'CALL'} pattern</button>}
                    </div>);
                })}
                <p className="note">A candle pattern is a row of steps, oldest first; the last candle of the last step is the candle that just closed, and the signal is for the next one. Leave one side empty if it only signals one way.</p>
              </div>
            ) : (
              <div className="mt-2 flex flex-wrap items-end gap-2">
                <div className="w-52"><span className="label">factor</span>
                  <select className="input" value={f.fn} onChange={(e) => set(i, { fn: e.target.value, params: { ...cat.fn_factors[e.target.value] } })}>{Object.keys(cat.fn_factors).map((k) => <option key={k}>{k}</option>)}</select></div>
                {Object.keys(cat.fn_factors[f.fn] ?? {}).map((p) => <div key={p} className="w-24"><span className="label">{p}</span><NumInput step={0.1} value={f.params[p]} onChange={(n) => set(i, { params: { ...f.params, [p]: n } })} /></div>)}
              </div>
            )}
          </div>))}
      </div>
      <p className="note mt-2">Weights are normalised to 100. With several factors the strategy needs "min agree" of them to point the same way (see Gates).</p>
    </Card>
  );
}

function GatesEditor({ form, upd }: { form: SpecForm; upd: Upd }) {
  const g = form.gates;
  const set = (p: Partial<SpecForm['gates']>) => upd({ gates: { ...g, ...p } });
  return (
    <Card title="Gates (when is a signal issued?)">
      <Grid cols={4}>
        <Field label="Min candles" hint="warm-up before signalling"><NumInput value={g.min_candles} onChange={(n) => set({ min_candles: n })} /></Field>
        <Field label="Min factors agreeing"><NumInput value={g.min_agree} onChange={(n) => set({ min_agree: n })} /></Field>
        <Field label="Max opposing weight" hint="0-100"><NumInput value={g.max_opposing_weight} onChange={(n) => set({ max_opposing_weight: n })} /></Field>
        <Field label="Min strength" hint="0-100"><NumInput value={g.min_strength} onChange={(n) => set({ min_strength: n })} /></Field>
      </Grid>
      <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={g.tierA} onChange={(e) => set({ tierA: e.target.checked })} /> Split into tiers: Tier A when</label>
      {g.tierA && <div className="mt-2 flex items-end gap-3"><div className="w-28"><Field label="≥ factors"><NumInput value={g.tier_a_agree} onChange={(n) => set({ tier_a_agree: n })} /></Field></div>
        <div className="w-28"><Field label="and strength ≥"><NumInput value={g.tier_a_strength} onChange={(n) => set({ tier_a_strength: n })} /></Field></div><span className="note pb-2">otherwise Tier B</span></div>}
    </Card>
  );
}

function FiltersEditor({ form, cat, upd }: { form: SpecForm; cat: Catalog; upd: Upd }) {
  const set = (i: number, p: Partial<FilterForm>) => upd({ filters: form.filters.map((x, j) => (j === i ? { ...x, ...p } : x)) });
  return (
    <Card title="Filters (veto a signal)" right={<button className="btn btn-sm" onClick={() => upd({ filters: [...form.filters, { type: 'adx_max', ...cat.filters.adx_max }] })}>+ Add filter</button>}>
      {form.filters.length === 0 && <div className="note">None. Filters remove signals in situations you do not want to trade (e.g. strong trends, dead volatility, certain hours). The Clinic suggests them from your losses.</div>}
      <div className="space-y-2">
        {form.filters.map((f, i) => {
          const defaults = cat.filters[f.type] ?? {};
          return (
            <div key={i} className="flex flex-wrap items-end gap-2 rounded-lg bg-panel2 p-2">
              <div className="w-44"><span className="label">filter</span>
                <select className="input" value={f.type} onChange={(e) => upd({ filters: form.filters.map((x, j) => (j === i ? { type: e.target.value, ...cat.filters[e.target.value] } : x)) })}>{Object.keys(cat.filters).map((t) => <option key={t}>{t}</option>)}</select></div>
              {Object.entries(defaults).map(([k, dv]) => (
                <div key={k} className="w-44"><span className="label">{k}{Array.isArray(dv) ? (f.type === 'weekdays' ? ' (0=Mon)' : ' (HH:MM-HH:MM UTC)') : ''}</span>
                  {Array.isArray(dv) ? (
                    <input className="input font-mono" value={(f[k] as (string | number)[] | undefined ?? []).join(', ')} placeholder="comma separated"
                      onChange={(e) => set(i, { [k]: e.target.value.split(',').map((x) => x.trim()).filter(Boolean).map((x) => (f.type === 'weekdays' ? Number(x) : x)) })} />
                  ) : f.type === 'trend_alignment' && k === 'value' ? (
                    <select className="input" value={String(f[k])} onChange={(e) => set(i, { [k]: e.target.value })}>{['with', 'against', 'not_against', 'not_with'].map((o) => <option key={o}>{o}</option>)}</select>
                  ) : f.type === 'direction_only' ? (
                    <select className="input" value={String(f[k])} onChange={(e) => set(i, { [k]: e.target.value })}><option>CALL</option><option>PUT</option></select>
                  ) : <NumInput step={f.type === 'one_per_run' ? 1 : 0.1} value={Number(f[k])} onChange={(n) => set(i, { [k]: n })} />}
                </div>))}
              {f.type === 'one_per_run' && <div className="note w-full">Keeps only the <b>first</b> signal of a back-to-back run: a signal is skipped when the same direction already signalled in the previous <b>gap</b> candle(s) (1-5). Cuts repeated, correlated trades; it does not change the win rate of the signals it keeps. Put it last so it sees the signals the other filters let through.</div>}
              <button className="btn btn-sm btn-danger" onClick={() => upd({ filters: form.filters.filter((_, j) => j !== i) })}>✕</button>
            </div>);
        })}
      </div>
    </Card>
  );
}

function PreviewPanel({ spec, valid, pair, tf }: { spec: unknown; valid: boolean; pair: string; tf: number }) {
  const run = useMutation({ mutationFn: () => api<Preview>('/api/strategies/preview', { body: { spec, symbol: pair, tf } }) });
  const a = run.data?.analysis;
  return (
    <Card title="Evaluate on the latest candle">
      <div className="flex flex-wrap items-center gap-2">
        <span className="note flex-1">Runs the full strategy (built-in factors, gates and filters included) on <b className="text-fg">{pair ? prettyPair(pair) : 'no pair'} · {tfName(tf)}</b>, the pair and timeframe chosen on the chart.</span>
        <button className="btn btn-primary" disabled={!valid || run.isPending || !pair} onClick={() => run.mutate()}>Evaluate now</button>
      </div>
      {run.isError && <div className="mt-2"><ErrorBox error={run.error} /></div>}
      {a && (
        <div className="mt-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className={`text-xl font-extrabold ${a.direction === 'CALL' ? 'text-up' : a.direction === 'PUT' ? 'text-down' : 'text-mut'}`}>{a.direction === 'CALL' ? '▲ CALL' : a.direction === 'PUT' ? '▼ PUT' : 'SKIP'}</span>
            {a.tier && <Badge tone="acc">Tier {a.tier}</Badge>}<span className="note">score {a.score} · strength {a.strength} · {a.agree} agree</span>
          </div>
          {a.reason && <div className="note">{a.reason}</div>}
          {a.factors.map((f) => <div key={f.key} className="flex justify-between border-t border-line pt-1 text-xs"><span>{f.name}<span className="note"> {f.note}</span></span><b className={f.vote > 0 ? 'text-up' : f.vote < 0 ? 'text-down' : 'text-mut'}>{f.vote > 0 ? '+' : ''}{f.vote}</b></div>)}
          <div className="note">{a.candles}/{a.need} candles available</div>
        </div>
      )}
    </Card>
  );
}
