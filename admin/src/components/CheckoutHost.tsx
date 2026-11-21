import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { ErrorBox, Spinner } from './ui';
import CheckoutModal from './CheckoutModal';
import type { Catalog, Order } from '../lib/pay';

/** The checkout is a pop-up that can be opened from anywhere (the Plans page, the plan-ending banner, a locked market): `useCheckout().open(planId?)`.
 *  Without a plan id the pop-up lets the person pick the plan inside it. */
const Ctx = createContext<{ open: (planId?: number) => void }>({ open: () => {} });
export const useCheckout = () => useContext(Ctx);

function Shell({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[300] grid place-items-center overflow-y-auto bg-[#02050f]/75 p-4 backdrop-blur-md" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-md rounded-3xl border border-white/10 bg-[#0d1325] p-6 text-center shadow-[0_40px_120px_rgba(0,0,0,.7)]">{children}
        <button type="button" className="mt-4 rounded-xl border border-white/10 bg-white/[.06] px-5 py-2 text-sm font-bold text-white hover:bg-white/[.12]" onClick={onClose}>Close</button></div>
    </div>);
}

function Host({ planId, onClose }: { planId: number | null; onClose: () => void }) {
  const cat = useQuery({ queryKey: ['pay-plans'], queryFn: () => api<Catalog>('/api/pay/plans') });
  const orders = useQuery({ queryKey: ['pay-orders'], queryFn: () => api<Order[]>('/api/pay/orders'), refetchInterval: 15000 });
  if (cat.isError) return <Shell onClose={onClose}><ErrorBox error={cat.error} /></Shell>;
  if (!cat.data) return <Shell onClose={onClose}><Spinner label="Loading the plans…" /></Shell>;
  if (cat.data.plans.length === 0) return <Shell onClose={onClose}><p className="text-slate-300">No plans are on sale yet. Ask the administrator.</p></Shell>;
  const pending = (orders.data ?? []).find((o) => o.status === 'pending') ?? null;
  return <CheckoutModal plans={cat.data.plans} initialPlanId={planId} cat={cat.data} pending={pending} onClose={onClose} />;
}

export function CheckoutProvider({ children }: { children: ReactNode }) {
  const [req, setReq] = useState<{ planId: number | null } | null>(null);
  const open = useCallback((planId?: number) => setReq({ planId: planId ?? null }), []);
  const close = useCallback(() => setReq(null), []);
  const value = useMemo(() => ({ open }), [open]);
  return <Ctx.Provider value={value}>{children}{req && <Host planId={req.planId} onClose={close} />}</Ctx.Provider>;
}
