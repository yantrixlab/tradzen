import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import type { LegalDoc } from '../lib/legal';
import { useLast } from '../lib/useLast';

/** The legal texts, readable before signing in (the server serves them publicly). Kept across the app's cache clears so a form does not change under the reader's hands. */
export function useLegal() {
  const q = useQuery({ queryKey: ['legal'], queryFn: () => api<LegalDoc>('/api/public/legal'), staleTime: 300000, retry: false });
  return useLast(q.data ?? null);
}

/** "I have read and accept the Terms of use, the Risk warning and the Refund policy" with a link that opens the texts. */
export function AcceptTerms({ checked, onChange, id }: { checked: boolean; onChange: (v: boolean) => void; id: string }) {
  const [open, setOpen] = useState(false);
  const doc = useLegal();
  return (
    <div className="text-sm">
      <label className="flex items-start gap-2" htmlFor={id}>
        <input id={id} type="checkbox" className="mt-1" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span>I have read and accept the <button type="button" className="underline" onClick={() => setOpen(!open)} aria-expanded={open}>terms of use, risk warning and refund policy</button>.</span>
      </label>
      {open && doc && <LegalSections doc={doc} />}
    </div>
  );
}

export function LegalSections({ doc }: { doc: LegalDoc }) {
  return (
    <div className="mt-2 max-h-72 space-y-3 overflow-y-auto rounded-lg border border-line bg-panel2/60 p-3 text-xs leading-relaxed" role="region" aria-label="Terms of use, risk warning and refund policy">
      {doc.sections.map((s) => (
        <section key={s.key}><h3 className="mb-1 text-[11px] font-bold uppercase tracking-wider text-mut">{s.title}</h3><div className="whitespace-pre-line text-fg/90">{s.text}</div></section>))}
      <div className="note">Version {doc.version}</div>
    </div>
  );
}
