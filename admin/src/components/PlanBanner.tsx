import { Link } from 'react-router-dom';
import { useMe } from '../hooks';
import { dateTime } from '../format';
import { planNotice } from '../lib/legal';

/** A thin reminder under the top bar: the paid plan ends in 3 days or less, or already has. It disappears when the plan is renewed (the end moves out of the window). */
export default function PlanBanner() {
  const me = useMe().data;
  if (!me || me.user.role === 'admin') return null;
  const n = planNotice(me.user.plan_id, me.user.plan_until);
  if (!n) return null;
  const ended = n.state === 'ended';
  return (
    <div role="status" className={`flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2 text-sm ${ended ? 'border-down/40 bg-down/10 text-down' : 'border-warn/40 bg-warn/10 text-warn'}`}>
      <span>{ended ? `Your ${me.plan.name} plan ended on ${dateTime(me.user.plan_until)}. You are back on the free plan.` : `Your plan ends in ${n.days} day${n.days === 1 ? '' : 's'} (${dateTime(me.user.plan_until)}). Renew now and the days are added to the end.`}</span>
      <Link to="/plans" className="btn btn-sm">{ended ? 'Get a plan' : 'Renew'}</Link>
    </div>
  );
}
