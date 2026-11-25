"""Shared revert decoder: turns web3.py / node errors into BasisContractError with a human hint.
Mirrors src/core/revert.ts."""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from eth_abi import decode as abi_decode
from eth_utils import function_signature_to_4byte_selector

from .errors import BasisError
from .redact import redact_urls
from .error_map import ERROR_MAP, EXTRA_ERRORS, GENERIC_REVERT, NO_REASON_HINTS, PANIC_MESSAGES, SDK_ABI_NAMES

ERROR_SELECTOR = "0x08c379a0"
PANIC_SELECTOR = "0x4e487b71"

PANIC_NAMES = {
    0x00: "generic panic",
    0x01: "assert failed",
    0x11: "arithmetic overflow/underflow",
    0x12: "division or modulo by zero",
    0x21: "invalid enum value",
    0x22: "invalid storage byte array",
    0x31: "pop on empty array",
    0x32: "array index out of bounds",
    0x41: "out of memory",
    0x51: "zero-initialized function pointer",
}

_ABI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "abis")
_HEX_RE = re.compile(r"0x[0-9a-fA-F]*")


class BasisContractError(BasisError):
    """A contract call reverted. `hint` is the human explanation when one is known.

    kind: 'reason' | 'customError' | 'panic' | 'noReason' | 'unknown'
    """

    def __init__(self, contract: Optional[str] = None, function_name: Optional[str] = None, kind: str = "unknown",
                 reason: Optional[str] = None, args: Optional[List[Any]] = None, hint: Optional[str] = None,
                 cause: Any = None):
        self.contract = contract
        self.function_name = function_name
        self.kind = kind
        self.reason = reason
        self.error_args = list(args or [])
        self.hint = hint
        self.cause = cause
        self.message = f"{contract or 'contract'}.{function_name or 'call'} reverted: {hint or reason or 'unknown error'}"
        super().__init__(self.message)
        self.__cause__ = cause if isinstance(cause, BaseException) else None

    def __str__(self) -> str:
        return self.message



_INDEX: Optional[Tuple[Dict[str, List[Tuple[Optional[str], dict]]], Dict[str, List[dict]]]] = None


def _abi_type(inp: dict) -> str:
    t = inp["type"]
    if t.startswith("tuple"):
        return "(" + ",".join(_abi_type(c) for c in inp.get("components", [])) + ")" + t[5:]
    return t


def _selector(item: dict) -> str:
    sig = f"{item['name']}({','.join(_abi_type(i) for i in item.get('inputs', []))})"
    return "0x" + function_signature_to_4byte_selector(sig).hex()


def _load_abi(name: str) -> list:
    try:
        with open(os.path.join(_ABI_DIR, f"{name}.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data["abi"] if isinstance(data, dict) else data
    except Exception:
        return []


def _index():
    """selector -> [(contract or None, error item)], plus contract -> its error items."""
    global _INDEX
    if _INDEX is None:
        by_sel: Dict[str, List[Tuple[Optional[str], dict]]] = {}
        by_contract: Dict[str, List[dict]] = {}
        for name in SDK_ABI_NAMES:
            errs = [i for i in _load_abi(name) if i.get("type") == "error"]
            by_contract[name] = errs
            for e in errs:
                by_sel.setdefault(_selector(e), []).append((name, e))
        for e in EXTRA_ERRORS:
            by_sel.setdefault(_selector(e), []).append((None, e))
        _INDEX = (by_sel, by_contract)
    return _INDEX


def _try_decode_custom(errors: list, data: bytes) -> Optional[Tuple[str, List[Any]]]:
    sel = "0x" + data[:4].hex()
    for e in errors:
        if e.get("type") != "error" or _selector(e) != sel:
            continue
        try:
            vals = abi_decode([_abi_type(i) for i in e.get("inputs", [])], data[4:])
            return e["name"], list(vals)
        except Exception:
            continue
    return None



def lookup_hint(contract: Optional[str], function_name: Optional[str], reason: str) -> Optional[str]:
    """Message for a reason / custom error name: (contract, fn) -> (contract, '*') -> global -> any contract."""
    if contract:
        c = ERROR_MAP.get(contract, {})
        m = (function_name and c.get(function_name, {}).get(reason)) or c.get("*", {}).get(reason)
        if m:
            return m
    g = ERROR_MAP.get("*", {}).get("*", {}).get(reason)
    if g:
        return g
    for fns in ERROR_MAP.values():
        for reasons in fns.values():
            if reason in reasons:
                return reasons[reason]
    return None


def no_reason_hint(contract: Optional[str], function_name: Optional[str]) -> str:
    """Rules behind a bare revert of (contract, fn), joined as 'one of: ...', or the generic message."""
    lst = (contract and function_name and NO_REASON_HINTS.get(contract, {}).get(function_name)) or []
    if not lst:
        return GENERIC_REVERT
    return lst[0] if len(lst) == 1 else "one of: " + " | ".join(lst)


def _panic_reason(code: int) -> str:
    return f"Panic(0x{code:02x})"


def _panic_hint(code: int) -> str:
    key = f"0x{code:02x}"
    return PANIC_MESSAGES.get(key) or PANIC_NAMES.get(code) or f"panic {key}"



def _to_bytes(data: Any) -> Optional[bytes]:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str) and _HEX_RE.fullmatch(data) and len(data) % 2 == 0:
        return bytes.fromhex(data[2:])
    return None


def decode_revert_data(data: Any, contract: Optional[str] = None, abi: Optional[list] = None) -> dict:
    """Decode raw revert data (Error(string), Panic(uint256) or a custom error).
    Returns {'kind', 'reason', 'args', 'contract'}."""
    raw = _to_bytes(data)
    if not raw:
        return {"kind": "noReason", "reason": None, "args": [], "contract": None}
    sel = "0x" + raw[:4].hex()
    try:
        if sel == ERROR_SELECTOR:
            (msg,) = abi_decode(["string"], raw[4:])
            if msg:
                return {"kind": "reason", "reason": msg, "args": [], "contract": None}
            return {"kind": "noReason", "reason": None, "args": [], "contract": None}
        if sel == PANIC_SELECTOR:
            (code,) = abi_decode(["uint256"], raw[4:])
            return {"kind": "panic", "reason": _panic_reason(code), "args": [code], "contract": None}
    except Exception:
        return {"kind": "unknown", "reason": sel, "args": [], "contract": None}
    by_sel, by_contract = _index()
    hit = (abi and _try_decode_custom(abi, raw)) or (contract and _try_decode_custom(by_contract.get(contract, []), raw))
    if not hit:
        hit = _try_decode_custom([e for _, e in by_sel.get(sel, [])], raw)
    if not hit:
        return {"kind": "unknown", "reason": sel, "args": [], "contract": None}
    owners = sorted({o for o, e in by_sel.get(sel, []) if o and e["name"] == hit[0]})
    return {"kind": "customError", "reason": hit[0], "args": hit[1], "contract": owners[0] if len(owners) == 1 else None}


def _decode_message(msg: str) -> Optional[dict]:
    """Parse a revert out of a plain message ("reverted with reason string '...'", "execution reverted: ...")."""
    m = re.search(r"reverted with reason string '([^']*)'", msg)
    if m:
        return {"kind": "reason", "reason": m.group(1), "args": []} if m.group(1) else {"kind": "noReason", "reason": None, "args": []}
    m = re.search(r"reverted with custom error '([A-Za-z_][A-Za-z0-9_]*)\(", msg)
    if m:
        return {"kind": "customError", "reason": m.group(1), "args": []}
    m = re.search(r"reverted with panic code (0x[0-9a-fA-F]+)", msg)
    if m:
        code = int(m.group(1), 16)
        return {"kind": "panic", "reason": _panic_reason(code), "args": [code]}
    m = re.search(r"reverted with the following reason:\s*\n?\s*([^\n]+)", msg)
    if m and m.group(1).strip() != "execution reverted":
        return {"kind": "reason", "reason": m.group(1).strip(), "args": []}
    m = re.search(r"execution reverted:\s*([^\n]+)", msg)
    if m:
        r = m.group(1).strip().strip("\"'")
        if _HEX_RE.fullmatch(r) and len(r) >= 10:
            return None
        return {"kind": "reason", "reason": r, "args": []} if r else {"kind": "noReason", "reason": None, "args": []}
    if re.search(r"execution reverted|reverted\.|\brevert(ed)?\b", msg, re.I):
        return {"kind": "noReason", "reason": None, "args": []}
    return None


def _find_hex(err: Any, depth: int = 0) -> Optional[str]:
    """First hex revert payload on the error: `.data`, dict 'data', exception args, `__cause__`. Never from a transport
    failure (:func:`_transport_kind`): the bytes of a body cut short are not revert data."""
    if err is None or depth > 8:
        return None
    if isinstance(err, BaseException) and _transport_kind(err):
        return None
    if isinstance(err, (bytes, bytearray)):
        return "0x" + bytes(err).hex()
    if isinstance(err, str):
        return err if _HEX_RE.fullmatch(err) and (len(err) >= 10 or err == "0x") else None
    if isinstance(err, dict):
        for k in ("data", "error", "originalError"):
            h = _find_hex(err.get(k), depth + 1)
            if h and h != "0x":
                return h
        return None
    if isinstance(err, BaseException):
        resp = getattr(err, "rpc_response", None)
        answers = (resp.get("error") if isinstance(resp, dict) else None, _bad_format_error(err))
        for v in (getattr(err, "data", None), *(a for a in getattr(err, "args", ()) if not isinstance(a, str) or _HEX_RE.fullmatch(a)),
                  *(a for a in answers if isinstance(a, dict)), err.__cause__, err.__context__):
            h = _find_hex(v, depth + 1)
            if h and h != "0x":
                return h
    return None


def _messages(err: Any) -> List[str]:
    out: List[str] = []
    cur, i = err, 0
    while cur is not None and i < 10:
        i += 1
        if isinstance(cur, str):
            out.append(cur)
            break
        if isinstance(cur, dict):
            if isinstance(cur.get("message"), str):
                out.append(cur["message"])
            cur = cur.get("error")
            continue
        if isinstance(cur, BaseException):
            node = _answer_error(cur)
            if node is not None and node["message"]:
                out.append(node["message"])
            m = getattr(cur, "message", None)
            if isinstance(m, str) and m:
                out.append(m)
            for a in cur.args:
                if isinstance(a, str) and a:
                    out.append(a)
                elif isinstance(a, dict) and isinstance(a.get("message"), str):
                    out.append(a["message"])
            cur = cur.__cause__ or cur.__context__
            continue
        break
    return out


def decode_revert(error: Any, contract: Optional[str] = None, function_name: Optional[str] = None,
                  abi: Optional[list] = None) -> BasisContractError:
    """Decode any revert-ish error (ContractLogicError / ContractCustomError / ContractPanicError,
    node error dicts, raw hex) into BasisContractError. Never raises."""
    if isinstance(error, BasisContractError):
        return error
    try:
        dec = None
        h = _find_hex(error)
        if h:
            dec = decode_revert_data(h, contract, abi)
        if dec is None or dec["kind"] == "noReason":
            for m in _messages(error):
                d = _decode_message(m)
                if d and (dec is None or d["kind"] != "noReason"):
                    dec = d
                    break
        if dec is None:
            msgs = _messages(error)
            return BasisContractError(contract, function_name, "unknown", redact_urls(msgs[0] if msgs else repr(error)),
                                      cause=error)
        contract = contract or dec.get("contract")
        if dec["kind"] == "noReason":
            hint = no_reason_hint(contract, function_name)
        elif dec["kind"] == "panic":
            hint = _panic_hint(int(dec["args"][0]) if dec["args"] else 0)
        else:
            hint = lookup_hint(contract, function_name, dec["reason"]) if dec["reason"] else None
        return BasisContractError(contract, function_name, dec["kind"], dec["reason"], dec["args"], hint, error)
    except Exception:
        try:
            msg = redact_urls(error)
        except Exception:
            msg = "unknown error"
        return BasisContractError(contract, function_name, "unknown", msg, cause=error)



_INSUFFICIENT_FUNDS_RE = re.compile(r"insufficient funds|exceeds transaction sender account balance|"
                                    r"sender doesn't have enough funds|insufficient balance for transfer", re.I)
_RATE_LIMIT_RE = re.compile(r"rate.?limit|too many requests|\b429\b", re.I)
_NETWORK_RE = re.compile(r"fetch failed|failed to fetch|ECONNREFUSED|ECONNRESET|ENOTFOUND|EAI_AGAIN|EPIPE|socket hang up|"
                         r"network ?error|other side closed|socket (has been )?closed|"
                         r"connection (refused|reset|aborted|closed)|remote end closed|max retries exceeded|"
                         r"failed to establish a new connection", re.I)
_TIMEOUT_TEXT_RE = re.compile(r"\bETIMEDOUT\b|took too long to respond|read timed out|request timed out|"
                              r"context deadline exceeded|i/o timeout|execution aborted \(timeout", re.I)
_NODE_FAILURE_RE = re.compile(r"\bEOF\b|forwarding|upstream", re.I)
_INDEXING_RE = re.compile(r"transaction indexing is in progress", re.I)
RPC_TIMEOUT_CODE = -32002
_RAW_RESPONSE = "The raw response is: "
_SENT_BEFORE_RE = re.compile(r"already known|known transaction|already imported|alreadyknown|transaction already exists|"
                             r"already in (the )?mempool|tx already in", re.I)
_NONCE_USED_RE = re.compile(r"nonce too low|nonce_too_low|nonce is too low|oldnonce|nonce has already been used|"
                            r"nonce expired", re.I)


def _chain_of(err: Any) -> List[Any]:
    """Every exception on the chain (``__cause__`` / ``__context__``), outermost first."""
    out: List[Any] = []
    cur, i = err, 0
    while isinstance(cur, BaseException) and i < 12 and cur not in out:
        out.append(cur)
        cur = cur.__cause__ or cur.__context__
        i += 1
    return out


def _bad_format_error(e: Any) -> Optional[dict]:
    """The ``error`` object of an answer web3 refused for its shape (``BadResponseFormat``: no ``id``, both ``result``
    and ``error``, no ``jsonrpc``), read from the raw answer its message quotes; None for anything else. viem reads such
    an answer's ``error`` as the node's answer (TS), so the SDK does here too."""
    try:
        from web3.exceptions import BadResponseFormat
    except ImportError:  # pragma: no cover
        return None
    if not isinstance(e, BadResponseFormat):
        return None
    text = str(e)
    i = text.rfind(_RAW_RESPONSE)
    if i < 0:
        return None
    try:
        import ast
        raw = ast.literal_eval(text[i + len(_RAW_RESPONSE):].strip())
    except Exception:  # noqa: BLE001
        return None
    err = raw.get("error") if isinstance(raw, dict) else None
    return err if isinstance(err, dict) else None


def _error_dicts(e: Any) -> List[Any]:
    """The JSON-RPC error objects ``e`` (one exception) may carry: ``rpc_response`` (web3 Web3RPCError), a dict
    argument, the quoted answer of a BadResponseFormat (:func:`_bad_format_error`)."""
    resp = getattr(e, "rpc_response", None)
    cands = [resp.get("error") if isinstance(resp, dict) else None]
    cands += [a for a in getattr(e, "args", ()) if isinstance(a, dict)]
    cands.append(_bad_format_error(e))
    return cands


def _rpc_code(e: Any) -> Optional[int]:
    """The JSON-RPC error code of a web3 error (``rpc_response``, a dict argument or a BadResponseFormat's quoted
    answer), or None."""
    for c in _error_dicts(e):
        if isinstance(c, dict) and isinstance(c.get("code"), int) and not isinstance(c.get("code"), bool):
            return c["code"]
    return None


def _http_status(e: Any) -> Optional[int]:
    r = getattr(e, "response", None)
    s = getattr(r, "status_code", None)
    return s if isinstance(s, int) else None


def _answer_error(e: Any) -> Optional[dict]:
    """The JSON-RPC error answer ``e`` (one exception) carries: the error dict the node sent (an int ``code`` and a str
    ``message``), in ``rpc_response`` (web3 Web3RPCError), as an argument, or quoted by a BadResponseFormat (an answer
    without ``id``, or with both ``result`` and ``error``: TS reads its ``error`` the same way). None otherwise."""
    for c in _error_dicts(e):
        if isinstance(c, dict) and isinstance(c.get("code"), int) and not isinstance(c.get("code"), bool) \
                and isinstance(c.get("message"), str):
            return c
    return None


def _rpc_error_answer(e: Any) -> bool:
    """True when ``e`` (one exception) carries a JSON-RPC error answer (:func:`_answer_error`)."""
    return _answer_error(e) is not None


def _transport_kind(e: BaseException) -> Optional[str]:
    """``timeout`` / ``network`` when ``e`` (one exception) is a transport failure that left no answer to read: a
    timeout; a connection that failed or broke off; a body cut short (requests ChunkedEncodingError, urllib3
    ProtocolError, http.client IncompleteRead) or not JSON (json.JSONDecodeError from web3's decode). None for an HTTP
    status (the answer arrived) and anything else."""
    import http.client
    import requests
    try:
        import urllib3.exceptions as u3
    except ImportError:  # pragma: no cover
        u3 = None  # type: ignore
    try:
        from web3.exceptions import ProviderConnectionError
    except ImportError:  # pragma: no cover
        ProviderConnectionError = ()  # type: ignore
    if isinstance(e, requests.exceptions.HTTPError) or _http_status(e) is not None:
        return None
    if u3 is not None and isinstance(e, u3.NewConnectionError):
        return "network"
    if isinstance(e, (requests.exceptions.Timeout, TimeoutError)) or (u3 is not None and isinstance(e, u3.TimeoutError)):
        return "timeout"
    if isinstance(e, (requests.exceptions.RequestException, ConnectionError, ProviderConnectionError,
                      http.client.HTTPException, json.JSONDecodeError)) \
            or (u3 is not None and isinstance(e, u3.HTTPError)):
        return "network"
    return None


def _no_answer_kind(err: Any) -> Optional[str]:
    """``timeout`` / ``network`` when no answer came whole: a transport failure on the chain (:func:`_transport_kind`)
    and no JSON-RPC error answer. Then the text that rides along is not the node's answer (never "insufficient funds"
    or a revert read out of a body cut short). None otherwise. TS: ``noAnswerKind``."""
    chain = _chain_of(err)
    if any(_rpc_error_answer(e) for e in chain):
        return None
    for e in chain:
        k = _transport_kind(e)
        if k:
            return k
    return None


def _has_strong_revert(err: Any) -> bool:
    """True when the error carries a decodable revert (web3 ContractLogicError family or revert data)."""
    try:
        from web3.exceptions import ContractLogicError
    except ImportError:  # pragma: no cover
        ContractLogicError = ()  # type: ignore
    if any(isinstance(e, ContractLogicError) for e in _chain_of(err)):
        return True
    h = _find_hex(err)
    return bool(h) and h != "0x"


def _carries_revert(err: Any) -> bool:
    """True when the error carries a revert: revert data, or the node's own words for one (:func:`_decode_message`).
    web3 raises ContractLogicError for any eth_call / eth_estimateGas answer with code 3 or ``data: null``, whatever the
    node said: that alone is not one. TS: ``carriesRevert``."""
    h = _find_hex(err)
    if h and h != "0x":
        return True
    return any(_decode_message(m) is not None for m in _messages(err))


def _node_failure_kind(err: Any, chain: Optional[List[Any]] = None, msgs: Optional[List[str]] = None) -> Optional[str]:
    """``network`` / ``timeout`` / ``server_error`` when the answer names a failure on the way, not a refusal: a
    connection that failed or broke off (``_NETWORK_RE``), a timeout (JSON-RPC -32002, ``_TIMEOUT_TEXT_RE``: the
    node's own included), a node that could not reach what is behind it (``_NODE_FAILURE_RE``) or cannot answer yet
    while it builds its transaction index (``_INDEXING_RE``, web3's TransactionIndexingInProgress). None when it names
    none, says insufficient funds, or carries a revert (:func:`_carries_revert`): then the answer is the node's refusal.
    TS: ``nodeFailureKind``."""
    chain = _chain_of(err) if chain is None else chain
    msgs = _messages(err) if msgs is None else msgs
    if any(_NETWORK_RE.search(m) for m in msgs):
        kind = "network"
    elif any(_rpc_code(e) == RPC_TIMEOUT_CODE for e in chain) or any(_TIMEOUT_TEXT_RE.search(m) for m in msgs):
        kind = "timeout"
    elif any(_NODE_FAILURE_RE.search(m) or _INDEXING_RE.search(m) for m in msgs) or _indexing_error(chain):
        kind = "server_error"
    else:
        return None
    if any(_INSUFFICIENT_FUNDS_RE.search(m) for m in msgs) or _carries_revert(err):
        return None
    return kind


def _indexing_error(chain: List[Any]) -> bool:
    """True when web3 raised TransactionIndexingInProgress on the chain (its own reading of the answer)."""
    try:
        from web3.exceptions import TransactionIndexingInProgress
    except ImportError:  # pragma: no cover
        return False
    return any(isinstance(e, TransactionIndexingInProgress) for e in chain)


def is_indexing_in_progress(err: Any) -> bool:
    """True when a lookup by hash (receipt, transaction) was answered "transaction indexing is in progress"
    (``_INDEXING_RE``, or web3's TransactionIndexingInProgress): the node is still building its transaction index and
    cannot say yet whether the hash is mined. Not found YET, never "not on chain": the receipt wait polls on, and
    nothing is concluded from it. TS: ``isIndexingInProgress``."""
    return _indexing_error(_chain_of(err)) or any(_INDEXING_RE.search(m) for m in _messages(err))


def rpc_error_kind(err: Any) -> Optional[str]:
    """The RPC / transport failure behind ``err`` (a ``BasisRpcError.kind``), or None when it is something else (a
    revert, a bug, an unknown node answer). First an answer that never came whole (:func:`_no_answer_kind`):
    ``timeout``, or ``network`` (a connection failure, a body that broke off or is not JSON: requests
    RequestException, urllib3 ProtocolError, http.client HTTPException, json.JSONDecodeError), whatever text it
    carries. Then ``insufficient_funds`` from the node's message; ``rate_limited`` for HTTP 429 / JSON-RPC -32005;
    ``network`` for another HTTP status below 500, ``server_error`` for HTTP 5xx / a JSON answer that is not JSON-RPC
    (one that quotes a JSON-RPC error object, an answer without ``id`` or with both ``result`` and ``error``, is read as
    that answer, as TS does); then a failure on the way named in the answer (:func:`_node_failure_kind`: a connection
    that failed, the node's own timeout or JSON-RPC -32002, a node that could not reach its backend or forward the
    transaction, or that is still building its transaction index), unless the answer carries a revert; last
    ``server_error`` for JSON-RPC -32603 (internal error)."""
    import requests
    lost = _no_answer_kind(err)
    if lost:
        return lost
    msgs = _messages(err)
    if any(_INSUFFICIENT_FUNDS_RE.search(m) for m in msgs):
        return "insufficient_funds"
    chain = _chain_of(err)
    try:
        from web3.exceptions import BadResponseFormat
    except ImportError:  # pragma: no cover
        BadResponseFormat = ()  # type: ignore
    for e in chain:
        if isinstance(e, requests.exceptions.HTTPError) or _http_status(e) is not None:
            s = _http_status(e)
            if s == 429:
                return "rate_limited"
            if s is not None and s >= 500:
                return "server_error"
            return "network"
        if isinstance(e, BadResponseFormat) and _answer_error(e) is None:
            return "server_error"
        if _rpc_code(e) == -32005:
            return "rate_limited"
    if any(_RATE_LIMIT_RE.search(m) for m in msgs):
        return "rate_limited"
    failed = _node_failure_kind(err, chain, msgs)
    if failed:
        return failed
    if any(_rpc_code(e) == -32603 for e in chain):
        return "server_error"
    return None


def _status_and_code(err: Any) -> Tuple[Optional[int], Optional[int]]:
    status = code = None
    for e in _chain_of(err):
        if status is None:
            status = _http_status(e)
        if code is None:
            c = _rpc_code(e)
            code = c if c is not None and c < 0 else None
    return status, code


def is_transient_rpc_error(err: Any) -> bool:
    """True for an RPC failure worth retrying in a moment (``network``, ``timeout``, ``rate_limited``,
    ``server_error``): the receipt wait keeps polling through these."""
    return rpc_error_kind(err) in ("network", "timeout", "rate_limited", "server_error")


def is_sent_unknown(err: Any) -> bool:
    """True when a failed broadcast (``eth_sendRawTransaction``) may still have reached the node: a transport failure or
    timeout (an answer cut short or not JSON included), an HTTP error status, a JSON-RPC answer naming a failure on the
    way and no revert (the node's own timeout or -32002, "context deadline exceeded", "EOF", a connection refused /
    reset / closed behind it, a failed forward, an upstream error: :func:`_node_failure_kind`), an answer meaning this
    exact transaction is already known (a lost first answer, then a retry), or anything that is not the node's own refusal
    (:func:`_node_refusal`: a JSON answer that is not JSON-RPC, a failure inside the transport). The write then waits for
    the hash it computed before sending. "Nonce too low" is not one of them (:func:`is_nonce_used`, checked first)."""
    if is_transient_rpc_error(err):
        return True
    if any(_SENT_BEFORE_RE.search(m) for m in _messages(err)):
        return True
    return not _node_refusal(err)


def _node_refusal(err: Any) -> bool:
    """True when the node refused a broadcast in its own words: a JSON-RPC error answer (an error dict with an int
    ``code`` and a ``message``) or a revert (web3's ContractLogicError, revert data, a node's revert message). Only that
    says nothing was sent."""
    if _no_answer_kind(err):
        return False
    if any(_rpc_error_answer(e) for e in _chain_of(err)):
        return True
    return _has_strong_revert(err) or any(_decode_message(m) is not None for m in _messages(err))


def is_nonce_used(err: Any) -> bool:
    """True when a broadcast was answered "nonce too low" (or a node's other words for it): the signed nonce is already
    used. The SDK sends a signed transaction once, so that is another transaction's, unless something between the SDK
    and the node sent these bytes on and lost the answer: the write checks the chain for its own hash before it reports
    ``BasisRpcError`` ``nonce_used``."""
    return not is_transient_rpc_error(err) and any(_NONCE_USED_RE.search(m) for m in _messages(err))


def _rpc_error(err: Any, kind: str, contract: Optional[str], function_name: Optional[str],
               detail: Optional[str] = None):
    """``BasisRpcError`` of ``kind`` for ``err`` (its HTTP status and JSON-RPC code; ``detail``: the text after "nothing
    was sent.", default what ``err`` says). Internal: the SDK's own errors (:func:`classify_error`, pinned reads)."""
    from .errors import BasisRpcError
    status, code = _status_and_code(err)
    return BasisRpcError(kind, contract, function_name, http_status=status, rpc_code=code, cause=err, detail=detail)


def classify_error(error: Any, contract: Optional[str] = None, function_name: Optional[str] = None,
                   abi: Optional[list] = None) -> BasisError:
    """A failure before sending (simulation, gas estimate) as an SDK error: a revert stays ``BasisContractError``
    (:func:`decode_revert`); an RPC / transport failure or insufficient funds becomes ``BasisRpcError`` (nothing was
    sent); an SDK error is returned as is. An answer that never came whole (a timeout, a body cut short or not JSON) is
    always a transport failure, never a revert read out of what arrived; so is a JSON-RPC answer that names a failure
    on the way and carries no revert (:func:`_node_failure_kind`: the node's own timeout, "EOF", an upstream error,
    ...), also when web3 raised ContractLogicError for it (it does that for every answer with code 3 or ``data: null``).
    Never raises."""
    if isinstance(error, BasisError):
        return error
    try:
        lost = _no_answer_kind(error)
        if lost:
            return _rpc_error(error, lost, contract, function_name)
        if _has_strong_revert(error):
            failed = _node_failure_kind(error)
            if failed:
                msgs = _messages(error)
                return _rpc_error(error, failed, contract, function_name, detail=redact_urls(msgs[0]) if msgs else "")
            return decode_revert(error, contract, function_name, abi)
        kind = rpc_error_kind(error)
        if kind == "insufficient_funds":
            return _rpc_error(error, kind, contract, function_name)
        if kind and not any(_decode_message(m) is not None for m in _messages(error)):
            return _rpc_error(error, kind, contract, function_name)
    except Exception:  # noqa: BLE001
        pass
    return decode_revert(error, contract, function_name, abi)


def send_rejection(error: Any, contract: Optional[str] = None, function_name: Optional[str] = None,
                   abi: Optional[list] = None) -> BasisError:
    """A broadcast the node refused for good (not :func:`is_sent_unknown`: a JSON-RPC error answer or a revert): a
    revert (a node that executes on send, e.g. Hardhat) is decoded into ``BasisContractError``; anything else is
    ``BasisRpcError`` (``insufficient_funds``, ``nonce_used`` for a "nonce too low", else ``rejected``)."""
    if _has_strong_revert(error) or any(_decode_message(m) is not None for m in _messages(error)):
        return decode_revert(error, contract, function_name, abi)
    kind = ("insufficient_funds" if rpc_error_kind(error) == "insufficient_funds"
            else "nonce_used" if is_nonce_used(error) else "rejected")
    return _rpc_error(error, kind, contract, function_name)


def is_revert_named(err: Any, name: str) -> bool:
    """True if the error's revert reason or custom error name equals `name`."""
    try:
        return decode_revert(err).reason == name
    except Exception:
        return False


def zero_unless_rpc(e: BaseException, contract: Optional[str] = None, function_name: Optional[str] = None) -> int:
    """A quote step that failed, as 0 when the chain refused the quote (a revert, or an SDK refusal of it such as 'Zero
    sold': it cannot be priced right now). An RPC failure is no answer: raised as
    ``BasisRpcError`` (:func:`classify_error`; ``contract`` / ``function_name`` name the step in it), also when the step
    had decoded it as a revert (its cause is classified again), so a node that did not answer never reads as "Bet
    more", "Use fewer slices" or 'quote'. Used by the Up/Down quotes and both ``min_bet_input`` probes (Up/Down and
    markets). Internal: not exported from the package root. TS: ``zeroUnlessRpc`` (src/core/revert.ts)."""
    from .errors import BasisRpcError
    if isinstance(e, BasisContractError) and e.cause is not None:
        err = classify_error(e.cause, e.contract, e.function_name)
    else:
        err = classify_error(e, contract, function_name)
    if isinstance(err, BasisRpcError):
        if err is e:
            raise err
        raise err from e
    return 0
