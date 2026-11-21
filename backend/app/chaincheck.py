"""An ADVISORY on-chain check of a USDT payment: does the transaction hash a user submitted exist on the network, is it a USDT transfer, to YOUR address, for at least the price, confirmed,
and not old? It looks the hash up on public nodes / explorers and shows the admin a list of ticks and crosses. It never approves anything: it removes the typing-a-fake-hash problem, it does
not replace the admin's decision (a transfer can be real but meant for somebody else's order - the unique-reference rule and your own judgement cover that).

  TRC20 (TRON)   TronGrid  GET {TRON_API}/v1/transactions/{hash}/events?only_confirmed=true        (optional TRONGRID_API_KEY)
  BEP20 / ERC20  JSON-RPC  eth_getTransactionReceipt, eth_blockNumber, eth_getBlockByNumber         (public nodes, QXS_RPC_BEP20 / QXS_RPC_ERC20 to use your own)
Only the transaction hash leaves the server (it is public on the chain anyway). Every answer is read defensively: a shape that is not understood is reported as "unreadable" - never guessed.
The USDT contract addresses and decimals below are the well-known ones; check them against the token's page on the explorer before you trust the amounts.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request

USDT = {
    "TRC20": {"contract": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "decimals": 6, "min_confirmations": 1},
    "BEP20": {"contract": "0x55d398326f99059ff775485246999027b3197955", "decimals": 18, "min_confirmations": 15},
    "ERC20": {"contract": "0xdac17f958d2ee523a2206206994597c13d831ec7", "decimals": 6, "min_confirmations": 12},
}
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TRON_API = os.getenv("QXS_TRON_API", "https://api.trongrid.io")
RPC = {"BEP20": os.getenv("QXS_RPC_BEP20", "https://bsc-dataseed.binance.org"), "ERC20": os.getenv("QXS_RPC_ERC20", "https://ethereum-rpc.publicnode.com")}
MAX_AGE = 7 * 86400
AMOUNT_SLACK = 0.01
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class Fetcher:
    """Plain HTTP with a short timeout. Tests replace it with canned answers."""

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def _open(self, req: urllib.request.Request):
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:                    # noqa: S310 - fixed public explorer / node URLs
                return json.loads(r.read() or b"null")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ConnectionError(str(e)[:150]) from None

    def get(self, url: str, headers: dict | None = None):
        return self._open(urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})}))

    def post(self, url: str, body: dict):
        return self._open(urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"}))


# ---------------------------------------------------------------------------------------------- addresses
def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def b58decode(s: str) -> bytes | None:
    n = 0
    for ch in s:
        i = _B58.find(ch)
        if i < 0:
            return None
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def tron_from_hex20(h: str) -> str:
    b = b"\x41" + bytes.fromhex(h)
    return b58encode(b + hashlib.sha256(hashlib.sha256(b).digest()).digest()[:4])


def canon(addr: str | None) -> str | None:
    """The 20 address bytes as lowercase hex, whatever the form: TRON base58 (T...), 0x + 40 hex, 41 + 40 hex (TRON hex). None when it is not an address."""
    if not isinstance(addr, str):
        return None
    a = addr.strip()
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", a):
        return a[2:].lower()
    if re.fullmatch(r"41[0-9a-fA-F]{40}", a):
        return a[2:].lower()
    if re.fullmatch(r"[0-9a-fA-F]{40}", a):
        return a.lower()
    if a.startswith("T") and 26 <= len(a) <= 36:
        raw = b58decode(a)
        if raw and len(raw) == 25 and raw[0] == 0x41 and hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4] == raw[21:]:
            return raw[1:21].hex()
    return None


# ---------------------------------------------------------------------------------------------- the checks
def _result(status: str, summary: str, checks: list[dict] | None = None, **extra) -> dict:
    return {"status": status, "summary": summary, "checks": checks or [], "checked_at": time.time(), **extra}


def _judge(network: str, transfers: list[dict], expect_address: str, expect_amount: float, order_created: float, confirmations: int | None, confirmed_by_node: bool, tx_time: float | None,
           now: float) -> dict:
    """transfers: [{"to": hex20, "amount": float}] of USDT transfers in the transaction. Builds the list of ticks and crosses."""
    want = canon(expect_address)
    mine = next((t for t in transfers if want and t["to"] == want), None)
    shown = mine or (transfers[0] if transfers else None)
    cfg = USDT[network]
    checks = [{"label": "The transaction exists and moved USDT", "ok": bool(transfers), "detail": f"{len(transfers)} USDT transfer(s) in it" if transfers else "no USDT transfer found in it"}]
    if want is None:
        checks.append({"label": "Sent to your address", "ok": False, "detail": "your payment method has no valid address for this network, so it cannot be compared"})
    else:
        checks.append({"label": "Sent to your address", "ok": mine is not None, "detail": "yes" if mine else (f"it went to {shown['to_display']}" if shown else "no transfer to compare")})
    amount = (mine or shown or {}).get("amount")
    checks.append({"label": f"Amount at least {expect_amount:g} USDT", "ok": amount is not None and amount >= expect_amount - AMOUNT_SLACK, "detail": f"{amount:g} USDT" if amount is not None else "unknown"})
    if confirmations is not None:
        checks.append({"label": f"Confirmed ({cfg['min_confirmations']}+ confirmations)", "ok": confirmations >= cfg["min_confirmations"], "detail": f"{confirmations} confirmation(s)"})
    else:
        checks.append({"label": "Confirmed by the network", "ok": confirmed_by_node, "detail": "the node reports it as confirmed" if confirmed_by_node else "not confirmed yet"})
    if tx_time:
        ok = order_created - MAX_AGE <= tx_time <= now + 300
        checks.append({"label": "Made around the time of the order", "ok": ok, "detail": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(tx_time)) + ("" if ok else " (more than 7 days before the order, or in the future)")})
    ok_all = all(c["ok"] for c in checks)
    return _result("confirmed" if ok_all else "mismatch", "Everything checks out on the chain." if ok_all else "Something does not match: read the crosses before approving.", checks,
                   to=shown["to_display"] if shown else None, amount=amount, confirmations=confirmations, tx_time=tx_time)


def _tron(fetch: Fetcher, tx: str, address: str, amount: float, created: float, now: float) -> dict:
    headers = {"TRON-PRO-API-KEY": os.environ["TRONGRID_API_KEY"]} if os.getenv("TRONGRID_API_KEY") else None
    body = fetch.get(f"{TRON_API}/v1/transactions/{tx}/events?only_confirmed=true", headers)
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return _result("unreadable", "The explorer answered in a form this check does not understand.")
    contract = USDT["TRC20"]["contract"]
    transfers, ts = [], None
    for ev in body["data"]:
        if not isinstance(ev, dict) or ev.get("event_name") != "Transfer":
            continue
        if canon(ev.get("contract_address")) != canon(contract):
            continue
        res = ev.get("result") if isinstance(ev.get("result"), dict) else {}
        to = canon(res.get("to") if res.get("to") is not None else res.get("1"))
        raw = res.get("value") if res.get("value") is not None else res.get("2")
        try:
            val = int(str(raw)) / 10 ** USDT["TRC20"]["decimals"]
        except (TypeError, ValueError):
            continue
        if to is None:
            continue
        transfers.append({"to": to, "to_display": tron_from_hex20(to), "amount": val})
        if isinstance(ev.get("block_timestamp"), (int, float)):
            ts = ev["block_timestamp"] / 1000.0
    if not transfers:
        return _result("not_found", "No confirmed USDT transfer was found for this hash (it may not exist, may not be confirmed yet, or may not be a USDT transfer).")
    return _judge("TRC20", transfers, address, amount, created, None, True, ts, now)


def _rpc(fetch: Fetcher, url: str, method: str, params: list):
    out = fetch.post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if isinstance(out, dict) and out.get("error"):
        raise ConnectionError(str(out["error"])[:150])
    return out.get("result") if isinstance(out, dict) else None


def _evm(network: str, fetch: Fetcher, tx: str, address: str, amount: float, created: float, now: float) -> dict:
    url = RPC[network]
    rc = _rpc(fetch, url, "eth_getTransactionReceipt", [tx])
    if rc is None:
        return _result("not_found", "The network does not know this transaction hash (yet).")
    if not isinstance(rc, dict) or not isinstance(rc.get("logs"), list):
        return _result("unreadable", "The node answered in a form this check does not understand.")
    if str(rc.get("status")) not in ("0x1", "1"):
        return _result("mismatch", "The transaction failed on the chain.", [{"label": "The transaction succeeded", "ok": False, "detail": "status failed"}])
    cfg = USDT[network]
    transfers = []
    for lg in rc["logs"]:
        try:
            if str(lg["address"]).lower() != cfg["contract"] or lg["topics"][0].lower() != TRANSFER_TOPIC or len(lg["topics"]) < 3:
                continue
            to = lg["topics"][2][-40:].lower()
            val = int(lg["data"], 16) / 10 ** cfg["decimals"]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        transfers.append({"to": to, "to_display": "0x" + to, "amount": val})
    conf = tx_time = None
    try:
        block = int(rc["blockNumber"], 16)
        latest = int(_rpc(fetch, url, "eth_blockNumber", []), 16)
        conf = max(0, latest - block + 1)
        blk = _rpc(fetch, url, "eth_getBlockByNumber", [rc["blockNumber"], False])
        tx_time = float(int(blk["timestamp"], 16)) if isinstance(blk, dict) else None
    except (KeyError, TypeError, ValueError):
        pass
    return _judge(network, transfers, address, amount, created, conf, conf is not None and conf >= cfg["min_confirmations"], tx_time, now)


def check(network: str, tx_hash: str, expect_address: str, expect_amount: float, order_created: float, fetch: Fetcher | None = None, now: float | None = None) -> dict:
    """The advisory result for one payment. Never raises: a network problem is a result ('error')."""
    now = now or time.time()
    fetch = fetch or Fetcher()
    if network not in USDT:
        return _result("error", f"on-chain checks cover {', '.join(USDT)} only")
    try:
        if network == "TRC20":
            return _tron(fetch, tx_hash, expect_address, expect_amount, order_created, now)
        return _evm(network, fetch, tx_hash, expect_address, expect_amount, order_created, now)
    except ConnectionError as e:
        return _result("error", f"Could not reach the explorer / node: {e}. Try again in a moment.")
