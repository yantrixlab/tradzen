import { useEffect } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { api } from './api';
import { clearPrefs } from './prefs';
import { SessionDialogs } from './components/SessionDialogs';
import { useSessionStatus } from './sessions';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Me, useLiveState, useMe } from './hooks';
import Overview from './pages/Overview';
import LiveSignal from './pages/LiveSignal';
import TradeGenerator from './pages/TradeGenerator';
import Signals from './pages/Signals';
import Strategies from './pages/Strategies';
import StrategyDetail from './pages/StrategyDetail';
import StrategyBuilder from './pages/StrategyBuilder';
import Backtests from './pages/Backtests';
import BacktestDetail from './pages/BacktestDetail';
import Clinic from './pages/Clinic';
import Sweeps from './pages/Sweeps';
import Research from './pages/Research';
import AI from './pages/AI';
import EdgeLab from './pages/EdgeLab';
import DataPage from './pages/DataPage';
import Alerts from './pages/Alerts';
import System from './pages/System';
import EmbedChart from './pages/EmbedChart';
import MyChart from './pages/MyChart';
import Sessions from './pages/Sessions';
import SessionConsole from './pages/SessionConsole';
import MyTrades from './pages/MyTrades';
import AccountBar from './components/AccountBar';
import Guard from './pages/Guard';
import MyStyle from './pages/MyStyle';
import Login from './pages/Login';
import Account, { PasswordForm } from './pages/Account';
import Users from './pages/Users';
import Features from './pages/Features';
import Library from './pages/Library';
import { Card, ErrorBox, Spinner } from './components/ui';
import { ApiError } from './api';

type Role = Me['user']['role'];
/** [path, label, icon, lowest role that may open it]. The server enforces the same rules on every API call (see backend/app/auth.py): hiding a menu is only a courtesy. */
// [path, label, icon, lowest role, feature key]. The feature key is the switch on the Features page (backend/app/features.py).
const NAV: [string, string, string, 'trader' | 'editor' | 'admin', string][] = [
  ['/', 'Overview', '◈', 'editor', 'menu.overview'], ['/sessions', 'Live sessions', '◉', 'trader', 'menu.sessions'], ['/session', 'Session console', '▶', 'admin', 'menu.sessionconsole'], ['/chart', 'Live chart', '◔', 'trader', 'menu.chart'], ['/live', 'Quotex Signal', '⚡', 'admin', 'menu.live'], ['/generate', 'Trade generator', '✦', 'admin', 'menu.generate'],
  ['/trades', 'My trades', '$', 'trader', 'menu.trades'], ['/guard', 'Trading guard', '🛡', 'trader', 'menu.guard'], ['/style', 'My style', '✎', 'trader', 'menu.style'],
  ['/signals', 'Signal history', '≣', 'trader', 'menu.signals'], ['/library', 'Strategy library', '❖', 'trader', 'menu.library'], ['/strategies', 'Strategies', '⚙', 'trader', 'menu.strategies'],
  ['/backtests', 'Backtest lab', '↺', 'trader', 'menu.backtests'],
  ['/sweeps', 'Sweeps', '⇄', 'editor', 'menu.sweeps'], ['/research', 'Research', '◎', 'admin', 'menu.research'], ['/edgelab', 'Edge Lab', '⌖', 'admin', 'menu.edgelab'],
  ['/ai', 'AI advisor', '✧', 'admin', 'menu.ai'], ['/clinic', 'Clinic', '✚', 'editor', 'menu.clinic'], ['/data', 'Data', '▤', 'editor', 'menu.data'],
  ['/alerts', 'Alerts', '♪', 'editor', 'menu.alerts'], ['/system', 'System', '⌘', 'admin', 'menu.system'], ['/users', 'Users', '👥', 'admin', 'menu.users'],
  ['/features', 'Features', '☰', 'admin', 'menu.features'], ['/account', 'My account', '☺', 'trader', 'menu.account'],
];
const RANK: Record<Role, number> = { trader: 0, editor: 1, admin: 2 };
const may = (role: Role, min: 'trader' | 'editor' | 'admin') => RANK[role] >= RANK[min];
const navFor = (path: string) => NAV.find(([to]) => (to === '/' ? path === '/' : path === to || path.startsWith(to + '/')));
const minFor = (path: string) => navFor(path)?.[3] ?? 'admin';
/** A menu is on unless its switch is explicitly off for this user (an unknown switch counts as on; the server decides anyway). */
const on = (me: Me, key: string) => me.features?.[key] !== false;

function FeedPill() {
  const { data, isError } = useLiveState();
  if (isError) return <span className="chip border-down/40 bg-down/10 text-down">backend unreachable</span>;
  if (!data) return <span className="chip border-line text-mut">connecting…</span>;
  const age = data.ingest.last_recv ? data.now - data.ingest.last_recv : null;
  const streaming = data.symbols.filter((s) => s.streaming).length;
  if (age !== null && age < 10) return <span className="chip border-up/40 bg-up/10 text-up">● live · {streaming} pair{streaming === 1 ? '' : 's'}</span>;
  return <span className="chip border-warn/40 bg-warn/10 text-warn">{age === null ? 'waiting for extension' : `no data ${Math.round(age)}s`}</span>;
}

function NoAccess() {
  return <div className="grid h-full place-items-center p-6 text-center"><div><div className="text-lg font-semibold">Nothing to show here</div><p className="note mt-1">Your plan does not include this page. Ask the administrator if you need it.</p></div></div>;
}

function Shell({ me }: { me: Me }) {
  const loc = useLocation();
  const qc = useQueryClient();
  const role = me.user.role;
  const staff = may(role, 'editor');
  const nav = NAV.filter(([, , , min, key]) => may(role, min) && on(me, key));
  const sessionsOn = on(me, 'menu.sessions');
  const live = useSessionStatus(sessionsOn).data;                  // a session is live: the menu row glows and blinks, a dialog announces the start and the end (SessionDialogs)
  const home = staff ? '/' : nav.find(([to]) => to !== '/account')?.[0] ?? '/account';      // a trader lands on their own trades
  async function signOut() {
    try { await api('/api/auth/logout', { method: 'POST', body: {} }); } catch { /* the session is gone either way */ }
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' });
    clearPrefs();                                                   // the next person on this browser starts from their own saved choices
    window.dispatchEvent(new Event('qxs-unauth'));
  }
  const gate = (min: 'trader' | 'editor' | 'admin', el: JSX.Element) => (may(role, min) ? el : <Navigate to={home} replace />);
  const hidden = (key: string | undefined) => key !== undefined && !on(me, key);
  if (loc.pathname === '/embed-chart') return <EmbedChart />;     // chart only, no sidebar: shown inside the extension's side panel
  const compact = new URLSearchParams(loc.search).has('embed');   // ?embed=1: no sidebar or account bar, for the narrow window the extension opens beside the Quotex tab
  if (!may(role, minFor(loc.pathname)) || hidden(navFor(loc.pathname)?.[4])) return loc.pathname === home ? <NoAccess /> : <Navigate to={home} replace />;
  return (
    <div className="flex h-full">
      <SessionDialogs enabled={sessionsOn} />
      {!compact && <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-panel2">
        <div className="flex items-center gap-2 px-4 py-4">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-acc to-acc2 text-lg">⚡</div>
          <div><div className="font-bold leading-tight">Tradzen</div><div className="text-[10px] font-semibold uppercase tracking-widest text-mut">{role}</div></div>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-2">
          {nav.map(([to, label, icon]) => {
            const isLive = to === '/sessions' && !!live?.live;
            return (
              <NavLink key={to} to={to} end={to === '/'}
                className={({ isActive }) => `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition ${isLive ? `live-nav border border-rose-500/50 bg-rose-500/10 font-semibold ${isActive ? 'text-rose-100' : 'text-rose-200 hover:text-white'}` : isActive ? 'bg-acc/15 text-[#c4b5fd]' : 'text-mut hover:bg-panel hover:text-fg'}`}>
                {isLive ? <span className="grid w-4 place-items-center"><span className="live-dot h-2.5 w-2.5 rounded-full bg-rose-500" /></span> : <span className="w-4 text-center">{icon}</span>}
                <span className="flex-1">{label}</span>
                {isLive && <span className="live-blink rounded bg-rose-500 px-1.5 py-px text-[9px] font-extrabold tracking-widest text-white">LIVE</span>}
              </NavLink>);
          })}
        </nav>
        <div className="space-y-2 border-t border-line p-3">
          {staff && <FeedPill />}
          {!me.auth_enabled && <a className="note block hover:text-fg" href="/" target="_blank" rel="noreferrer">Classic live view ↗</a>}
          {!me.auth_enabled && <a className="note block hover:text-fg" href="/docs" target="_blank" rel="noreferrer">API docs ↗</a>}
          {me.auth_enabled && (
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0"><div className="truncate text-sm font-medium">{me.user.username}</div><div className="note capitalize">{role}</div></div>
              <button className="btn btn-sm" onClick={signOut} title="Sign out of this browser">Sign out</button>
            </div>
          )}
        </div>
      </aside>}
      <main className="min-w-0 flex-1 overflow-y-auto">
        {!compact && on(me, 'menu.trades') && <AccountBar />}
        <div className={`mx-auto max-w-[1280px] ${compact ? 'p-2' : 'p-6'}`}>
          <ErrorBoundary key={loc.pathname}>
          <Routes>
            <Route path="/" element={gate('editor', <Overview />)} />
            <Route path="/sessions" element={gate('trader', <Sessions />)} />
            <Route path="/session" element={gate('admin', <SessionConsole />)} />
            <Route path="/chart" element={gate('trader', <MyChart />)} />
            <Route path="/live" element={gate('admin', <LiveSignal />)} />
            <Route path="/generate" element={gate('admin', <TradeGenerator />)} />
            <Route path="/trades" element={gate('trader', <MyTrades />)} />
            <Route path="/guard" element={gate('trader', <Guard />)} />
            <Route path="/style" element={gate('trader', <MyStyle />)} />
            <Route path="/library" element={gate('trader', <Library />)} />
            <Route path="/signals" element={gate('trader', <Signals />)} />
            <Route path="/strategies" element={gate('trader', <Strategies />)} />
            <Route path="/strategies/new" element={gate('trader', <StrategyBuilder mode="new" />)} />
            <Route path="/strategies/:id" element={gate('trader', <StrategyDetail />)} />
            <Route path="/strategies/:id/edit" element={gate('trader', <StrategyBuilder mode="edit" />)} />
            <Route path="/strategies/:id/clone" element={gate('trader', <StrategyBuilder mode="clone" />)} />
            <Route path="/backtests" element={gate('trader', <Backtests />)} />
            <Route path="/backtests/:id" element={gate('trader', <BacktestDetail />)} />
            <Route path="/sweeps" element={gate('editor', <Sweeps />)} />
            <Route path="/research" element={gate('admin', <Research />)} />
            <Route path="/edgelab" element={gate('admin', <EdgeLab />)} />
            <Route path="/ai" element={gate('admin', <AI />)} />
            <Route path="/clinic" element={gate('editor', <Clinic />)} />
            <Route path="/data" element={gate('editor', <DataPage />)} />
            <Route path="/alerts" element={gate('editor', <Alerts />)} />
            <Route path="/system" element={gate('admin', <System />)} />
            <Route path="/users" element={gate('admin', <Users />)} />
            <Route path="/features" element={gate('admin', <Features />)} />
            <Route path="/account" element={<Account />} />
            <Route path="*" element={<div className="p-10 text-center text-mut">Page not found.</div>} />
          </Routes>
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}

/** Everything sits behind sign-in: the sign-in page when there is no session, the forced password change for a new account, otherwise the app. */
export default function App() {
  const qc = useQueryClient();
  const me = useMe();
  const loc = useLocation();
  useEffect(() => {
    const out = () => { qc.setQueryData(['me'], undefined); qc.invalidateQueries({ queryKey: ['me'] }); };
    window.addEventListener('qxs-unauth', out);
    return () => window.removeEventListener('qxs-unauth', out);
  }, [qc]);
  if (loc.pathname === '/embed-chart' && me.data) return <EmbedChart />;
  if (me.isLoading) return <div className="grid h-full place-items-center"><Spinner label="Loading…" /></div>;
  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) return <Login />;
    return <div className="p-6"><ErrorBox error={me.error} /></div>;
  }
  if (!me.data) return <Login />;
  if (me.data.user.must_change_pw) {
    return (
      <div className="grid h-full place-items-center p-4">
        <Card title="Choose your own password" className="w-full max-w-sm">
          <p className="note mb-3">Welcome, {me.data.user.username}. Before you continue, replace the temporary password you were given.</p>
          <PasswordForm forced />
        </Card>
      </div>
    );
  }
  return <Shell me={me.data} />;
}
