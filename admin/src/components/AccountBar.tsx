import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { ago } from '../format';
import { useAccount, useAmountsVisible, useMe, useMoney } from '../hooks';
import { theme, type MoneyMgmt } from '../lib/moneyMgmt';

const Eye = ({ off }: { off: boolean }) => (
  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" />
    {off && <path d="M3 3l18 18" />}
  </svg>
);

const GUARD_CHIP: Record<string, [string, string]> = {
  ok: ['border-line bg-panel2 text-mut', '🛡 Guard on'], target: ['border-up/50 bg-up/10 text-up', '🎯 Target reached'], stop: ['border-down/50 bg-down/10 text-down', '⛔ Stop for today'],
  cooldown: ['border-sky-400/50 bg-sky-400/10 text-sky-300', '⏸ Cooling down'], overtrading: ['border-down/50 bg-down/10 text-down', '⚠ Overtrading'], caution: ['border-warn/50 bg-warn/10 text-warn', '! Be careful'],
};

/** Where the trading guard stands today, on every page (opens the Trading guard page). */
function GuardChip() {
  const { data } = useQuery({ queryKey: ['guard'], queryFn: () => api<{ state: string; headline: string; settings: { enabled: boolean } }>(`/api/guard?tz=${-new Date().getTimezoneOffset()}`), refetchInterval: 5000 });
  if (!data || !data.settings.enabled) return null;
  const [cls, label] = GUARD_CHIP[data.state] ?? GUARD_CHIP.ok;
  return <Link to="/guard" className={`chip ${cls} hover:brightness-125`} title={`${data.headline} - open the Trading guard`}>{label}</Link>;
}

/** The stake the Money management page advises for the next trade, on every page (opens that page). */
function StakeChip() {
  const money = useMoney();
  const { data } = useQuery({ queryKey: ['moneymgmt'], queryFn: () => api<MoneyMgmt>(`/api/moneymgmt?tz=${-new Date().getTimezoneOffset()}`), refetchInterval: 5000 });
  if (!data || data.balance.value === null) return null;
  const a = data.advice, th = theme(a.status, a.kind), cls = { up: 'border-up/50 bg-up/10 text-up', warn: 'border-warn/50 bg-warn/10 text-warn', down: 'border-down/50 bg-down/10 text-down', mut: 'border-line bg-panel2 text-mut' }[th.tone];
  return <Link to="/moneymgmt" className={`chip ${cls} hover:brightness-125`} title={`${a.headline}: ${a.reason}`}>{a.status === 'stop' && a.kind !== 'target' ? '⛔ Stop for today' : a.status === 'stop' ? '🎯 Target reached' : `Next stake ${money(a.stake)}`}</Link>;
}

/** Download of the Chrome extension for this server (the server packages it; hidden where it is not offered, e.g. a local server). */
export function useExtension() {
  return useQuery({ queryKey: ['extension-info'], queryFn: () => api<{ available: boolean; version: string | null }>('/download/extension.json'), staleTime: 600000 });
}

function GetExtension() {
  const { data } = useExtension();
  if (!data?.available) return null;
  return (
    <a href="/download/extension.zip" download className="chip border-acc/50 bg-acc/10 text-acc hover:brightness-125" title="Download the Chrome extension for this server (unzip it, then Load unpacked on chrome://extensions)">
      ⬇ Get extension{data.version ? ` v${data.version}` : ''}
    </a>
  );
}

/** Slim bar on top of every page: the Quotex account balances in the account's own currency. */
export default function AccountBar() {
  const { data } = useAccount();
  const me = useMe();
  const stakeOn = me.data?.features?.['menu.moneymgmt'] !== false;
  const money = useMoney();
  const [visible, toggle] = useAmountsVisible();
  const seen = !!data && (data.live !== null || data.demo !== null);
  const code = data?.currency?.code;
  const eye = (
    <button type="button" onClick={toggle} aria-pressed={visible} aria-label={visible ? 'Hide amounts' : 'Show amounts'} title={visible ? 'Hide balance and amounts' : 'Show balance and amounts'}
      className="-my-1.5 grid h-11 w-11 place-items-center self-center rounded-md text-mut transition hover:bg-panel2 hover:text-fg lg:my-0 lg:h-auto lg:w-auto lg:p-1"><Eye off={!visible} /></button>
  );
  const at = Math.max(data?.live_at ?? 0, data?.demo_at ?? 0) || null;
  return (
    <div className="z-20 border-b border-line bg-bg/95 px-3 py-2 backdrop-blur sm:px-4 lg:sticky lg:top-0 lg:px-6">
      <div className="mx-auto flex max-w-[1280px] flex-wrap items-center gap-x-4 gap-y-1 text-sm lg:gap-x-6">
        {seen ? (
          <>
            <span className="flex items-baseline gap-2" title="Your real Quotex account"><span className="text-[11px] font-semibold uppercase tracking-wide text-mut">Real balance</span>
              <b className="text-lg tabular-nums">{money(data!.live)}</b>
              {eye}</span>
            <span className="flex items-baseline gap-2" title="Your Quotex demo account"><span className="text-[11px] font-semibold uppercase tracking-wide text-mut">Demo balance</span>
              <b className="tabular-nums text-mut">{money(data!.demo)}</b></span>
            {code && <span className="chip border-line bg-panel2 text-mut" title="Currency of the account">{code}</span>}
            {stakeOn && <StakeChip />}
            <GuardChip />
            <span className="note ml-auto">seen {ago(at)}</span>
            <GetExtension />
          </>
        ) : (
          <><span className="note">Account balance not seen yet - reload the Quotex tab once (the extension has been updated) and it appears here.</span>{eye}<GuardChip /><span className="ml-auto"><GetExtension /></span></>
        )}
      </div>
    </div>
  );
}
