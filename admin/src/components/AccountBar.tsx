import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { ago } from '../format';
import { useAccount, useAmountsVisible, useMoney } from '../hooks';

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
  const money = useMoney();
  const [visible, toggle] = useAmountsVisible();
  const seen = !!data && (data.live !== null || data.demo !== null);
  const code = data?.currency?.code;
  const eye = (
    <button type="button" onClick={toggle} aria-pressed={visible} aria-label={visible ? 'Hide amounts' : 'Show amounts'} title={visible ? 'Hide balance and amounts' : 'Show balance and amounts'}
      className="self-center rounded-md p-1 text-mut transition hover:bg-panel2 hover:text-fg"><Eye off={!visible} /></button>
  );
  const at = Math.max(data?.live_at ?? 0, data?.demo_at ?? 0) || null;
  return (
    <div className="sticky top-0 z-20 border-b border-line bg-bg/95 px-6 py-2 backdrop-blur">
      <div className="mx-auto flex max-w-[1280px] flex-wrap items-center gap-x-6 gap-y-1 text-sm">
        {seen ? (
          <>
            <span className="flex items-baseline gap-2" title="Your real Quotex account"><span className="text-[11px] font-semibold uppercase tracking-wide text-mut">Real balance</span>
              <b className="text-lg tabular-nums">{money(data!.live)}</b>
              {eye}</span>
            <span className="flex items-baseline gap-2" title="Your Quotex demo account"><span className="text-[11px] font-semibold uppercase tracking-wide text-mut">Demo balance</span>
              <b className="tabular-nums text-mut">{money(data!.demo)}</b></span>
            {code && <span className="chip border-line bg-panel2 text-mut" title="Currency of the account">{code}</span>}
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
