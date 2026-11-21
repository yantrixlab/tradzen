"""The terms of use, the risk warning and the refund policy: editable by the admin, shown to visitors on the sign-in page, accepted by the person when they create an account and when they pay.

The texts below are TEMPLATES in plain language so there is something honest to start from. They are not legal advice and do not know where you or your users are: have them read by someone
who does (selling signals, charts or access to a broker's prices is restricted or banned in some countries, and taking payments brings tax and refund duties). Saving a change raises the
version; accepting records the version, so you can tell who agreed to which wording.
"""
from __future__ import annotations

import time

from .control import get_setting, set_setting

KEY = "legal.texts"
VERSION_KEY = "legal.version"
MAX_LEN = 20000
SECTIONS = [("terms", "Terms of use"), ("risk", "Risk warning"), ("refund", "Refund policy")]

DEFAULTS = {
    "terms": """These are the terms for using this service. By creating an account or paying for a plan you agree to them.

1. What this service is. It shows charts, market data and analysis tools. It does not place trades for you, does not manage your money and gives no personal advice. Nothing here is a recommendation to buy or sell anything.

2. Your account. Keep your password private. One paid plan is for one person; sharing it, or reselling access, can lead to the account being closed without a refund.

3. Data. Prices and candles come from third-party sources and can be late, incomplete or different from the price you get at your broker. Do not rely on them alone.

4. Availability. We try to keep the service running but do not promise it will always be available or error free. Features, prices and plans can change.

5. Responsibility. You decide whether and how to trade, and you are responsible for what you do. To the extent the law allows, we are not liable for losses that follow from using the service.

6. Rules where you live. You are responsible for checking that using this service and trading are allowed where you live. Do not use it where they are not.

7. Ending. You can stop using the service at any time. We may close an account that breaks these terms.""",
    "risk": """Trading carries a high risk of losing money, including all of the money you put in. Short-term binary and digital options are especially risky: most people who trade them lose money over time.

Charts, signals and statistics describe the past or a possibility. They do not tell you what will happen next. Strategies that looked good on past data often fail on new data, and a pattern is not a promise.

Never trade with money you cannot afford to lose. Do not borrow to trade. Set limits for yourself and keep to them. If trading is causing you stress or harm, stop and talk to someone you trust.

We are not a broker, not a financial adviser and not connected to any broker you may use. Nothing in this service is financial advice.""",
    "refund": """Plans are paid in advance for a fixed number of days and switched on after we have checked your payment.

If a plan could not be switched on because of a mistake on our side, or you paid twice for the same period, tell us within 7 days and we will refund the payment or add the days.

A plan that has been switched on and used is not refunded because you changed your mind or because trades did not go the way you hoped. We do not refund losses from trading.

Refunds are made the same way you paid, less any fees charged to us. Contact the administrator with your username and the payment reference.""",
}


def _stored(db) -> dict:
    v = get_setting(db, KEY, None)
    return v if isinstance(v, dict) else {}


def version(db) -> int:
    return int(get_setting(db, VERSION_KEY, 1) or 1)


def texts(db) -> dict:
    s = _stored(db)
    return {k: (s.get(k) if isinstance(s.get(k), str) and s.get(k).strip() else DEFAULTS[k]) for k, _ in SECTIONS}


def public(db) -> dict:
    t = texts(db)
    return {"version": version(db), "sections": [{"key": k, "title": title, "text": t[k]} for k, title in SECTIONS], "edited": bool(_stored(db))}


def save(db, new: dict) -> dict:
    """Replace the texts (an empty one goes back to the template). The version goes up when anything changed."""
    clean = {}
    for k, _ in SECTIONS:
        v = new.get(k, "")
        if not isinstance(v, str) or len(v) > MAX_LEN:
            raise ValueError(f"the {k} text must be text of at most {MAX_LEN} characters")
        clean[k] = v.strip()
    before = texts(db)
    set_setting(db, KEY, clean)
    if texts(db) != before:
        set_setting(db, VERSION_KEY, version(db) + 1)
        set_setting(db, "legal.updated", time.time())
    return public(db)
