"""Broadcast and receipt wait of the write pipeline (TypeScript SDK: src/core/replacement.ts).

A wallet can replace a pending transaction with another one using the same nonce: a speed-up of the same call
('repriced'), a cancel (zero-value transfer to self, 'cancelled') or any other call ('replaced'). web3.py waits on the
sent hash only, so a replaced transaction would just time out. :func:`wait_for_receipt` polls the hash and, once the
sender's nonce is used up without it, finds the transaction mined with that nonce and classifies it like viem. It
locates the block by bisecting the sender's nonce (:func:`nonce_block`): Robinhood Chain makes about 10 blocks a second,
so a block-by-block scan since the send would be some 1800 reads in 3 minutes. A transient RPC failure during the wait
(timeout, connection error, an answer cut short or not JSON, HTTP 429 / 5xx, a JSON-RPC answer naming the node's own
timeout or a failure behind it: -32002, "context deadline exceeded", "EOF", ...) does not end it: the poll is retried
until ``timeout_s``. A lookup answered "transaction indexing is in progress" (a node still building its transaction
index; web3's TransactionIndexingInProgress) is "not found yet": the wait polls on and concludes nothing from it.

The SDK signs with a local key, which has no wallet that could speed a transaction up: another transaction mined with
its nonce, the same call included, is a nonce already used (``replacement["reason"] == "nonce_used"``), never this
write.

The broadcast (:func:`broadcast_raw`) knows the hash before sending (the signed transaction's hash) and treats an
answer that leaves it open whether the node took the transaction (timeout, network error, an answer cut short or not
JSON, HTTP 5xx / 429, "already known": anything but the node's own refusal in a JSON-RPC error answer) as "sent,
unknown": the write then waits for that hash instead of raising a plain error (a caller retry would be a double
spend), and while the node does not know it and its nonce is free, the same signed bytes are sent again a few times
(same hash, same nonce: never a second write). "Nonce too low" is waited for too, until the chain shows the
transaction that took the nonce (or up to the receipt timeout). A JSON-RPC error answer in the body of an HTTP 4xx
(other than 408 / 429) is read as the node's answer (:func:`http_error_answer`). The provider's own retry of ``eth_sendRawTransaction`` is switched off
(:func:`no_send_retries`). :func:`next_nonce` keeps a client from reusing a nonce a lagging node still counts as free.
:func:`estimate_fees_per_gas` sets the fees by viem's rule, so both SDKs send the same fee caps.

RPC error text in the logs and in the timeout error has its URLs cut down to scheme and host (``redact_urls``).
"""

import logging
import threading
import time
import weakref
from collections import OrderedDict
from typing import Any, Dict, Optional, Sequence, Tuple

from web3.exceptions import BlockNotFound, TimeExhausted, TransactionNotFound

from ..config.chains import block_time_ms
from .errors import ValidationError
from .redact import redact_urls
from .revert import is_indexing_in_progress, is_nonce_used, is_sent_unknown, is_transient_rpc_error

logger = logging.getLogger("basis")

GAS_MARGIN_PCT = 120
MAX_TX_GAS = 32_000_000


def capped_gas_limit(estimate: int) -> int:
    """The estimate x :data:`GAS_MARGIN_PCT` (rounded down), never above :data:`MAX_TX_GAS`. TS: cappedGasLimit."""
    return min(int(estimate) * GAS_MARGIN_PCT // 100, MAX_TX_GAS)


def gas_limit_for(estimate: int) -> int:
    """The gas limit a write sends for its ``estimate``: :func:`capped_gas_limit`. An estimate above :data:`MAX_TX_GAS`
    raises ``ValidationError('gas')`` (too big for one transaction): nothing is sent. TS: gasLimitFor."""
    if int(estimate) > MAX_TX_GAS:
        raise ValidationError(
            "gas",
            f"This call needs about {int(estimate)} gas, too big for one transaction (the chain allows at most {MAX_TX_GAS}). "
            "Do less in one call: split a batch or use a smaller amount.")
    return capped_gas_limit(estimate)


POLL_INTERVAL_S = 0.5
SEND_METHODS = ("eth_sendRawTransaction", "eth_sendTransaction")
REBROADCAST_AT_S = (1.0, 3.0, 7.0, 15.0)
_NOT_AN_ANSWER_STATUS = (408, 429)

_lock = threading.Lock()
_floors: "weakref.WeakKeyDictionary[Any, Dict[str, Dict[str, Any]]]" = weakref.WeakKeyDictionary()
_sent: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_SENT_KEEP = 256


def _hex(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()
    s = str(v).lower()
    return s if s.startswith("0x") else "0x" + s


def _addr(v: Any) -> str:
    return (v or "").lower() if isinstance(v, str) else _hex(v)


def classify_replacement(original: Dict[str, Any], replacement: Dict[str, Any]) -> str:
    """viem's rule: same ``to``, ``value`` and calldata = 'repriced'; a zero-value transfer to self = 'cancelled'; else
    'replaced'. ``original``: the sent tx (``to``, ``value``, ``data``); ``replacement``: the mined tx (``from``, ``to``,
    ``value``, ``input``)."""
    if (_addr(replacement.get("to")) == _addr(original.get("to"))
            and int(replacement.get("value") or 0) == int(original.get("value") or 0)
            and _hex(replacement.get("input")) == _hex(original.get("data") or original.get("input"))):
        return "repriced"
    if _addr(replacement.get("from")) == _addr(replacement.get("to")) and int(replacement.get("value") or 0) == 0:
        return "cancelled"
    return "replaced"


def _receipt(web3, tx_hash) -> Optional[Any]:
    """The receipt of ``tx_hash``, or None when the node has none (yet: also "transaction indexing is in progress",
    :func:`~basis.core.revert.is_indexing_in_progress`); a transient failure is raised."""
    try:
        return web3.eth.get_transaction_receipt(tx_hash)
    except TransactionNotFound:
        return None
    except Exception as e:  # noqa: BLE001
        if is_indexing_in_progress(e):
            return None
        raise


def _knows(web3, tx_hash) -> Optional[bool]:
    """True when the node knows ``tx_hash`` (pending or mined), False when it does not, None when it cannot say yet
    (it is still building its transaction index: :func:`~basis.core.revert.is_indexing_in_progress`); a transient
    failure is raised."""
    try:
        web3.eth.get_transaction(tx_hash)
        return True
    except TransactionNotFound:
        return False
    except Exception as e:  # noqa: BLE001
        if is_indexing_in_progress(e):
            return None
        raise


def _floor(web3, sender: str) -> Optional[Dict[str, Any]]:
    with _lock:
        try:
            return (_floors.get(web3) or {}).get(sender.lower())
        except TypeError:
            return None


def _note_mined(web3, sender: str, nonce: int, tx_hash: Any) -> None:
    """Raise ``sender``'s floor to ``nonce + 1`` (``tx_hash`` used ``nonce``); never lowers it."""
    if tx_hash is None:
        return
    with _lock:
        try:
            m = _floors.setdefault(web3, {})
        except TypeError:
            return
        cur = m.get(sender.lower())
        if cur is None or cur["nonce"] <= nonce:
            m[sender.lower()] = {"nonce": int(nonce) + 1, "hash": _hex(tx_hash)}


def next_nonce(web3, sender: str, chain_id: Optional[int] = None, block_time_s: Optional[float] = None) -> int:
    """The nonce to sign ``sender``'s next transaction with: the RPC's pending count, or the floor (the nonce after the
    client's last mined transaction) when the RPC counts less while that transaction is mined: a node a block or two
    behind. When neither shows up, it looks again after the chain's block time (``block_time_s``; None: the shared rule
    for ``chain_id``, :func:`~basis.config.chains.block_time_ms`; the client passes its config's,
    :func:`~basis.config.chains.chain_block_time_ms`, as the pinned reads and TS do; twice, not at all on a chain
    without one, as a local node or no chain id), then takes the RPC's count: on a chain rolled back (a local node's
    snapshot) the floor is gone, and a nonce that is used after all is refused at the broadcast, never sent twice. A
    transient failure is raised; so is a node that still answers "transaction indexing is in progress" for the floor's
    transaction after those looks (it cannot say whether that transaction is mined: the write is ``BasisRpcError``
    ``server_error``, nothing sent)."""
    pending = web3.eth.get_transaction_count(sender, "pending")
    floor = _floor(web3, sender)
    if floor is None or pending >= floor["nonce"]:
        return pending
    wait = block_time_ms(chain_id) / 1000 if block_time_s is None else block_time_s
    for i in range(3):
        indexing: Optional[BaseException] = None
        try:
            if web3.eth.get_transaction_receipt(floor["hash"]) is not None:
                return floor["nonce"]
        except TransactionNotFound:
            pass
        except Exception as e:  # noqa: BLE001
            if not is_indexing_in_progress(e):
                raise
            indexing = e
        if i >= 2 or not wait:
            if indexing is not None:
                raise indexing
            return pending
        time.sleep(wait)
        pending = web3.eth.get_transaction_count(sender, "pending")
        if pending >= floor["nonce"]:
            return pending
    return pending


def nonce_block(web3, sender: str, nonce: int, lo: int, hi: int) -> int:
    """The first block in (``lo``, ``hi``] where ``sender``'s transaction count passed ``nonce``: the count at the middle
    block, about log2(hi - lo) reads. A read the node refuses for good (state it no longer keeps: a non-archive node keeps
    seconds of it here) moves the search up, to the recent blocks it can answer for; a transient failure is raised. The
    answer can be off when the count at ``hi`` has not passed yet (a node a block behind): the caller finds no transaction
    in that block and polls again."""
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        try:
            count = web3.eth.get_transaction_count(sender, mid)
        except Exception as e:  # noqa: BLE001
            if is_transient_rpc_error(e):
                raise
            lo = mid
            continue
        if count > nonce:
            hi = mid
        else:
            lo = mid
    return hi


def _nonce_used(nonce: int, tx: Any = None, receipt: Any = None) -> Tuple[Any, Dict[str, Any]]:
    h = _hex(tx["hash"]) if tx is not None else None
    return receipt, {"reason": "nonce_used", "nonce": nonce, "hash": h, "transaction": tx}


def wait_for_receipt(web3, tx_hash: str, sender: str, original: Dict[str, Any], start_block: int,
                     timeout_s: float, poll_s: float = POLL_INTERVAL_S, local: bool = True,
                     nonce_used_on_send: Optional[bool] = None, raw_tx: Optional[bytes] = None,
                     rebroadcast_at_s: Sequence[float] = REBROADCAST_AT_S) -> Tuple[Any, Optional[Dict[str, Any]]]:
    """Wait for ``tx_hash`` (sent by ``sender``; ``original`` is the signed tx dict with ``nonce``, ``to``, ``value``,
    ``data``; ``start_block`` the latest block before sending). Returns ``(receipt, None)`` when it is mined, or
    ``(replacement_receipt, {"reason", "hash", "transaction"})`` when another tx with its nonce was mined instead.
    A transient RPC failure (:func:`~basis.core.revert.is_transient_rpc_error`) is retried at the next poll, and a
    lookup answered "transaction indexing is in progress" is not found yet; raises ``TimeExhausted`` after
    ``timeout_s`` (naming the last such failure), like ``wait_for_transaction_receipt``, and lets other RPC errors
    through.

    ``local`` (the SDK signed, the default): another transaction mined with the nonce, the same call included, or one
    mined at or before ``start_block``, comes back as ``(its receipt or None, {"reason": "nonce_used", "nonce", "hash"
    (None when not located), "transaction"})``: this write never ran and never will. ``nonce_used_on_send`` (the
    broadcast answered "nonce too low"; default: what :func:`broadcast_raw` noted for this hash): any other transaction
    is. Only a transaction found on chain says so: while neither this hash nor another transaction with the nonce is
    found (a node that says it does not know this hash proves nothing: something between the SDK and the node may have
    sent these bytes on), the wait goes on until ``timeout_s``. ``raw_tx`` (default: the signed bytes
    :func:`broadcast_raw` kept after an unclear answer) is sent again at ``rebroadcast_at_s`` while the node does not
    know the hash and the nonce is still free (its own refusals and blips never end the wait). A mined nonce raises the
    client's nonce floor (:func:`next_nonce`)."""
    with _lock:
        note = _sent.pop(_hex(tx_hash), None) or {}
    if nonce_used_on_send is None:
        nonce_used_on_send = bool(note.get("nonce_used"))
    if raw_tx is None:
        raw_tx = note.get("raw")
    nonce = int(original["nonce"])
    me = sender.lower()
    ours = _hex(tx_hash)
    started = time.monotonic()
    deadline = started + timeout_s
    first_block = max(int(start_block), 0)
    resent = 0
    stale_checked = not local
    last_error: Optional[BaseException] = None

    def mined(receipt: Any, h: Any) -> Any:
        if local:
            _note_mined(web3, sender, nonce, h)
        return receipt

    while True:
        try:
            r = _receipt(web3, tx_hash)
            if r is not None:
                return mined(r, tx_hash), None
            count = web3.eth.get_transaction_count(sender, "latest")
            if count > nonce:
                r = _receipt(web3, tx_hash)
                if r is not None:
                    return mined(r, tx_hash), None
                if not stale_checked:
                    before = None
                    try:
                        before = web3.eth.get_transaction_count(sender, first_block)
                    except Exception as e:  # noqa: BLE001
                        if is_transient_rpc_error(e):
                            raise
                    if before is not None:
                        stale_checked = True
                        if before > nonce:
                            return _nonce_used(nonce)
                at = nonce_block(web3, sender, nonce, first_block - 1, int(web3.eth.block_number))
                try:
                    block = web3.eth.get_block(at, full_transactions=True)
                except BlockNotFound:
                    block = None
                found = None if block is None else next(
                    (t for t in block["transactions"] if _addr(t.get("from")) == me and int(t["nonce"]) == nonce), None)
                if found is not None:
                    own_found = _hex(found["hash"]) == ours
                    h = _hex(found["hash"])
                    rr = _receipt(web3, found["hash"])
                    if rr is not None and own_found:
                        return mined(rr, h), None
                    if rr is not None:
                        reason = classify_replacement(original, found)
                        mined(rr, h)
                        if local and (reason == "repriced" or nonce_used_on_send):
                            return _nonce_used(nonce, found, rr)
                        return rr, {"reason": reason, "hash": h, "transaction": found}
            elif raw_tx is not None and resent < len(rebroadcast_at_s) and \
                    time.monotonic() - started >= rebroadcast_at_s[resent]:
                resent += 1
                if _knows(web3, tx_hash) is not True:
                    try:
                        web3.eth.send_raw_transaction(raw_tx)
                    except Exception as e:  # noqa: BLE001
                        logger.debug("[basis-sdk] re-broadcast of %s: %s", ours, redact_urls(e))
            last_error = None
        except Exception as e:  # noqa: BLE001
            if not is_transient_rpc_error(e):
                raise
            last_error = e
            logger.debug("[basis-sdk] receipt wait for %s: RPC error, polling again: %s", ours, redact_urls(e))
        if time.monotonic() >= deadline:
            extra = f" (last RPC error: {redact_urls(last_error)})" if last_error is not None else ""
            raise TimeExhausted(f"Transaction {ours} is not in the chain after {timeout_s} seconds{extra}")
        time.sleep(poll_s)


BASE_FEE_MULTIPLIER = (12, 10)


def _rpc_priority_fee(web3) -> int:
    """``eth_maxPriorityFeePerGas``, without web3's ``eth_feeHistory`` fallback (viem's is gasPrice - baseFee)."""
    read = getattr(web3.eth, "_max_priority_fee", None)
    return int(read() if callable(read) else web3.eth.max_priority_fee)


def estimate_fees_per_gas(web3) -> Dict[str, int]:
    """The fee fields of a write, by viem's ``estimateFeesPerGas`` (2.47, no chain fee config), so the PY and TS SDKs
    send the same caps. On a chain with a base fee (the latest block's ``baseFeePerGas``): ``maxPriorityFeePerGas`` from
    ``eth_maxPriorityFeePerGas`` (when that read fails: ``eth_gasPrice`` - baseFee, at least 0) and ``maxFeePerGas`` =
    baseFee x 1.2 (rounded down) + that tip. Without a base fee: ``gasPrice`` = ``eth_gasPrice`` x 1.2 (rounded down). A
    failed block or gas price read is raised."""
    num, den = BASE_FEE_MULTIPLIER
    block = web3.eth.get_block("latest")
    base = block.get("baseFeePerGas") if hasattr(block, "get") else None
    if isinstance(base, int) and not isinstance(base, bool):
        try:
            tip = _rpc_priority_fee(web3)
        except Exception:  # noqa: BLE001
            tip = max(int(web3.eth.gas_price) - base, 0)
        return {"maxFeePerGas": base * num // den + tip, "maxPriorityFeePerGas": tip}
    return {"gasPrice": int(web3.eth.gas_price) * num // den}


def no_send_retries(provider: Any) -> None:
    """Removes the broadcast methods from the HTTP provider's retry allowlist (web3 7 retries ``eth_sendRawTransaction``
    on a connection error, timeout or HTTP error: a lost answer then comes back as "already known" or "nonce too low"
    instead of the hash, after up to 5 resends). Idempotent; a provider without that configuration is left alone."""
    try:
        cfg = getattr(provider, "exception_retry_configuration", None)
        allow = list(getattr(cfg, "method_allowlist", None) or [])
        if cfg is None or not any(m in allow for m in SEND_METHODS):
            return
        from web3.providers.rpc.utils import ExceptionRetryConfiguration
        provider.exception_retry_configuration = ExceptionRetryConfiguration(
            errors=cfg.errors, retries=cfg.retries, backoff_factor=cfg.backoff_factor,
            method_allowlist=[m for m in allow if m not in SEND_METHODS])
    except Exception as e:  # noqa: BLE001
        logger.debug("[basis-sdk] could not switch off the provider's send retries: %s", e)


def _note_sent(tx_hash: str, raw: Optional[bytes], nonce_used: bool) -> None:
    with _lock:
        _sent[_hex(tx_hash)] = {"raw": raw, "nonce_used": nonce_used}
        while len(_sent) > _SENT_KEEP:
            _sent.popitem(last=False)


def _hash_bytes(got: Any) -> Optional[bytes]:
    """The bytes of a broadcast's answer (web3 HexBytes, bytes, or a hex string), None for anything else."""
    if isinstance(got, (bytes, bytearray)):
        return bytes(got)
    if isinstance(got, str) and len(got) % 2 == 0 and got[:2] in ("0x", "0X"):
        try:
            return bytes.fromhex(got[2:])
        except ValueError:
            return None
    return None


def _is_answer_code(code: Any) -> bool:
    """A JSON-RPC error answer's code: 3 (execution reverted) or the JSON-RPC range -32768..-32000."""
    return isinstance(code, int) and not isinstance(code, bool) and (code == 3 or -32768 <= code <= -32000)


def http_error_answer(err: Any) -> Optional[BaseException]:
    """The node's JSON-RPC error answer carried in an HTTP 4xx answer to a broadcast, as web3 raises the same answer sent
    with HTTP 200 (a ``Web3RPCError`` whose ``rpc_response`` is the body), or None. Some nodes and gateways answer a
    refusal ("insufficient funds", "nonce too low", ...) with HTTP 400 and the JSON-RPC answer in the body; web3 raises
    requests' HTTPError for it, and the HTTP status alone would leave the broadcast "sent, unknown" for the whole
    receipt wait. Only a body whose ``error`` is an object with a JSON-RPC answer code (3 or -32768..-32000) and a
    ``message`` string counts; never 408 or 429 (a timeout, a rate limit) nor 5xx (a proxy may have passed the
    transaction on). The answer is then judged like any other (a timeout or failure behind the node it names stays "sent,
    unknown"). Its BasisRpcError carries the JSON-RPC code, no HTTP status. TS: httpErrorAnswer (replacement.ts)."""
    import json
    import requests
    cur, i = err, 0
    while isinstance(cur, BaseException) and i < 12:
        resp = getattr(cur, "response", None)
        status = getattr(resp, "status_code", None)
        if isinstance(cur, requests.exceptions.HTTPError) or (isinstance(status, int) and not isinstance(status, bool)):
            if not isinstance(status, int) or not 400 <= status <= 499 or status in _NOT_AN_ANSWER_STATUS:
                return None
            try:
                body = json.loads(resp.text)
            except Exception:  # noqa: BLE001
                return None
            error = body.get("error") if isinstance(body, dict) else None
            if not isinstance(error, dict) or not _is_answer_code(error.get("code")) \
                    or not isinstance(error.get("message"), str):
                return None
            from web3.exceptions import Web3RPCError
            return Web3RPCError(repr(error), rpc_response=body)
        cur = cur.__cause__ or cur.__context__
        i += 1
    return None


def broadcast_raw(web3, raw_tx: bytes, tx_hash: str) -> Optional[BaseException]:
    """Sends the signed ``raw_tx`` whose hash is ``tx_hash``. Returns None when the node took it (it answered a 32-byte
    hash), or the error when its answer leaves that open (:func:`~basis.core.revert.is_sent_unknown`: a lost, cut-off
    or non-JSON answer, a JSON-RPC answer naming the node's own timeout or a failure behind it, anything but the node's
    own refusal; also an answer without a 32-byte hash, such as "0x", which web3 passes as empty bytes: the error then
    says so; the caller then waits for ``tx_hash``, and the wait sends the same bytes again while the node does not
    know them) or was "nonce too low" (:func:`~basis.core.revert.is_nonce_used`: the wait reports the nonce as used
    once it finds the transaction that took it). Raises the node's error only when it refused the transaction in a
    JSON-RPC error answer or a revert (nothing was sent); a JSON-RPC answer in the body of an HTTP 4xx is that answer
    (:func:`http_error_answer`: raised as web3 raises it at HTTP 200, so the refusal is one at once). TS: the broadcast
    in BaseModule.send."""
    no_send_retries(getattr(web3, "provider", None))
    refused: Optional[BaseException] = None
    try:
        got = web3.eth.send_raw_transaction(raw_tx)
        raw_hash = _hash_bytes(got)
        if raw_hash is None or len(raw_hash) != 32:
            what = "not a 32-byte hash" if raw_hash is not None or isinstance(got, str) else (
                "null" if got is None else type(got).__name__)
            raise ValueError(f"the node's answer carries no transaction hash ({what})")
    except Exception as thrown:  # noqa: BLE001
        answer = http_error_answer(thrown)
        e = thrown if answer is None else answer
        if is_nonce_used(e):
            _note_sent(tx_hash, None, True)
            logger.info("[basis-sdk] the broadcast of %s was answered nonce too low; checking the chain", tx_hash)
            return e
        if is_sent_unknown(e):
            _note_sent(tx_hash, raw_tx, False)
            logger.warning("[basis-sdk] the broadcast of %s got no clear answer (%s); waiting for that hash", tx_hash,
                           redact_urls(e))
            return e
        if answer is None:
            raise
        refused = answer
    if refused is not None:
        raise refused
    if _hex(got) != _hex(tx_hash):
        logger.warning("[basis-sdk] the node answered hash %s for the broadcast of %s", _hex(got), tx_hash)
    return None
