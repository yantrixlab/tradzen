/** Types and helpers shared by the Plans page (buying) and the Payments page (the admin's review). The server (backend/app/payments.py) re-checks everything; these only help early. */

export interface PayMethod { id: number; kind: 'upi' | 'usdt' | 'other'; label: string; currency: string; address: string; instructions: string; qr: string | null; active?: boolean; sort?: number }
export interface PlanOffer { id: number; name: string; description: string; prices: { method_id: number; amount: number; days: number }[] }
export interface Catalog { methods: PayMethod[]; plans: PlanOffer[]; current: { plan_id: number; name: string; paid: boolean; until: number | null } }
export interface Order {
  id: number; user_id?: number; username?: string; plan_id: number; plan: string; method_id: number; method: string; kind: string; amount: number; currency: string; days: number;
  reference: string; network: string | null; note: string | null; proof: string | null; status: 'pending' | 'approved' | 'rejected' | 'cancelled';
  reviewer?: string | null; review_note: string | null; created: number; reviewed: number | null; plan_from: number | null; plan_until: number | null;
}

export const NETWORKS = ['TRC20', 'BEP20', 'ERC20', 'other'] as const;
export const STATUS_TONE: Record<string, string> = { pending: 'warn', approved: 'up', rejected: 'down', cancelled: 'mut' };

/** What is wrong with a payment reference, or null when it looks right. Mirrors payments.check_reference. */
export function refProblem(kind: string, network: string | null, raw: string): string | null {
  const ref = raw.replace(/\s+/g, '');
  if (!ref) return 'Enter the payment reference.';
  if (kind === 'upi') return /^\d{12}$/.test(ref) ? null : 'A UPI reference (UTR) is exactly 12 digits.';
  if (kind === 'usdt') {
    if (network === 'TRC20') return /^[0-9a-fA-F]{64}$/.test(ref) ? null : 'A TRC20 transaction hash is 64 characters (0-9, a-f).';
    if (network === 'BEP20' || network === 'ERC20') return /^0x[0-9a-fA-F]{64}$/.test(ref) ? null : `A ${network} transaction hash starts with 0x and has 64 more characters (0-9, a-f).`;
    return /^[A-Za-z0-9_-]{16,100}$/.test(ref) ? null : 'That transaction ID does not look right.';
  }
  return /^[A-Za-z0-9_-]{6,100}$/.test(ref) ? null : 'The reference must be 6-100 letters, digits, - or _.';
}

/** A picked image file as a data URL, or an error text. Only PNG / JPEG / WEBP up to `maxBytes`. */
export function readImage(file: File, maxBytes: number): Promise<{ url: string } | { error: string }> {
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) return Promise.resolve({ error: 'Choose a PNG, JPEG or WEBP image.' });
  if (file.size > maxBytes) return Promise.resolve({ error: `The image must be under ${Math.round(maxBytes / 1000)} KB.` });
  return new Promise((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve({ url: String(r.result) });
    r.onerror = () => resolve({ error: 'The image could not be read.' });
    r.readAsDataURL(file);
  });
}

export const money = (amount: number, currency: string): string => {
  const sym: Record<string, string> = { INR: '₹', USD: '$', USDT: '$', EUR: '€', GBP: '£', BRL: 'R$' };
  const n = amount % 1 === 0 ? String(amount) : amount.toFixed(2);
  return sym[currency] ? `${sym[currency]}${n}${currency === 'USDT' ? ' USDT' : ''}` : `${n} ${currency}`;
};

export const daysLeft = (until: number | null | undefined, now = Date.now() / 1000): number | null => (until ? Math.max(0, Math.ceil((until - now) / 86400)) : null);
