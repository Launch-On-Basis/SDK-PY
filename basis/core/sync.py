"""Post-transaction backend sync with retries and a pending queue (TypeScript SDK: src/core/sync.ts).

The dapp's database is filled from these posts. So a sync that fails is retried with exponential backoff, and one that
still fails is kept in ``client.pending_syncs`` (and appended to ``queue_file`` when set) for
``flush_pending_syncs()`` / ``flush_sync_queue_file()``.

A sync failure NEVER raises: by the time we sync, the transaction is confirmed on chain. Raising would make the
caller think the write failed and retry it, which sends the transaction a second time (double spend).

Answers of POST /api/v1/sync and what the engine does with them::

  2xx synced | already_synced | partial | ignored      -> delivered (partial is logged), when the body acknowledges the
                                                            post: success true, one of these statuses, an integer
                                                            chainId and the posted txHash (any case)
  2xx without that acknowledgement (empty, not JSON, another hash, ...) -> retried, then queued (code unacknowledged)
  422 code pending (RPC behind) | sync_failed (write)    -> retried with backoff
  422 code reverted                                      -> permanent: logged, not queued
  429 rate_limited (60/min per IP)                       -> retried after X-RateLimit-Reset (epoch s; Retry-After or
                                                            backoff when missing), each wait capped by max_delay_ms
  503 code chain_mismatch (API on another chain)         -> not retried, logged as an error, queued for a flush once fixed
  5xx, 408, 425, network error, timeout                  -> retried with backoff (a body that times out or breaks off
                                                            after the headers counts as a network error, whatever the status)
  400 bad_body (malformed hash: an SDK bug)              -> not retried, not queued, logged as an error
  any other 4xx (401/403 from a proxy, 404, ...)         -> not retried, queued for a later flush

An ERC-8004 agent registration needs one more post: the tx sync stores it as ``ignored`` (the registry is an outside
contract, not decoded by /api/v1/sync), and the dapp's Agent row is written only by POST /api/agents (key or session;
the server checks the registry on chain). It goes through the same retries and queue (``kind: "agent"`` entries)::

  2xx {success: true, agent: {wallet, agentId}} echoing the posted wallet (any case) and agentId -> delivered (status
                                                            ``registered``); any other 2xx -> retried, then queued (unacknowledged)
  400 bad_body | invalid_address, 409 conflict            -> permanent: logged as an error, not queued
  403 wallet_in_account | forbidden | not_branded         -> permanent: logged as an error, not queued
  403 wallet_not_linked (the key or session of a wallet that cannot sign in yet) -> sent again as the signed first
                                                            sync below (``kind: "agentSigned"``)
  429 rate_limited (10 agent syncs an hour per wallet)    -> not retried (the window is an hour), queued
  503 code not_deployed (no registry on that API)        -> not retried, logged as an error, queued
  no API key and no session on the client                -> not sent, queued (authenticate, then flush)
  an entry of another wallet than the key's (``api_key_wallet``) or the session's (``session_wallets``), or a key /
  session whose wallet is not known (no ``api_key_wallet`` / no ``session_wallets``) -> sent without that credential;
                                                            nothing left: not sent, queued (auth_required). The API
                                                            would answer 403 forbidden, which is final.
  in a flush: an entry whose tx entry (same hash) is kept as not seen mined (code not_confirmed / pending) -> not
                                                            sent, kept ("waiting for its tx"): the API would record
                                                            the registration as it was before that tx (a tx entry
                                                            kept unfound for over 24 h is dropped, and this one is
                                                            then posted: see the chain check below)
  5xx (503 unavailable: registry read failed), 408, 425, network error, timeout -> retried, but at most twice
                                                            (AGENT_SYNC_MAX_ATTEMPTS: every post counts against the
                                                            hourly quota), AGENT_RETRY_DELAY_FACTOR times further
                                                            apart than a tx sync's (5 s, then 10 s by default)
  any other 4xx (401, another 403, ...)                  -> not retried, queued for a later flush

The signed first sync of an agent wallet that cannot sign in yet (``kind: "agentSigned"``, no credentials: a message
the agent wallet signs) uses the same retries (the same cap of 3 attempts). The message is signed again, with a new Issued time, for every attempt
and at every flush (``auth["sign_agent_sync"]``), so a queued entry keeps only the wallet and the agentId::

  2xx echoing the wallet and agentId                      -> delivered (status ``registered``)
  400, 401 (invalid_signature), 403, 409                  -> permanent: logged as an error, not queued
  429                                                     -> not retried, queued
  no signer for the wallet (``sign_agent_sync`` missing, or another wallet's) -> not sent, queued (code signer_required)
  the signing step fails: ValidationError (no Terms version, the API takes no signed sync) -> permanent; another
                                                            failure (the config read) -> retried like a network error
  503 not_deployed, other 4xx, 5xx, 408, 425, network     -> as for ``kind: "agent"``

Every queued entry carries the API it belongs to (``api_domain``; ``apiDomain`` in the file): a flush against another
API keeps it untouched.

The standalone helpers (``deliver_sync``, ``deliver_agent_sync``, ``deliver_agent_signed_sync``,
``flush_sync_queue_file``) check their ``api_domain`` with the client's rule (:func:`check_api_domain`: https, plain
http only for localhost / 127.0.0.1) and raise ``ValidationError("apiDomain")`` before sending anything; the client's
engine posts to its own, already checked.
"""

import contextlib
import email.utils
import json
import logging
import os
import random
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import requests

from .redact import redact_urls

logger = logging.getLogger("basis")

SYNC_ROUTE = "/api/v1/sync"
SYNC_SOURCE = "sdk"
AGENT_SYNC_ROUTE = "/api/agents"

AGENT_SYNC_MAX_ATTEMPTS = 3
AGENT_RETRY_DELAY_FACTOR = 5

_MAX_AGENT_ID = 2147483647
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")

DEFAULT_SYNC_OPTIONS: Dict[str, Any] = {"attempts": 6, "base_delay_ms": 1000, "max_delay_ms": 60000, "timeout_ms": 15000}

_OPTION_KEYS = ("attempts", "base_delay_ms", "max_delay_ms", "timeout_ms", "queue_file")
_HASH = re.compile(r"0x[0-9a-fA-F]{64}")


MAX_SYNC_MS = 2147483647
_CAMEL = {"attempts": "attempts", "base_delay_ms": "baseDelayMs", "max_delay_ms": "maxDelayMs",
          "timeout_ms": "timeoutMs", "queue_file": "queueFile"}


def resolve_sync_options(options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Defaults + ``options`` (snake_case keys; ``None`` values are ignored), validated: an unknown key raises
    ``ValidationError("sync")``, a bad value ``ValidationError("sync.<option>")`` (camelCase option name, as TS)."""
    from .errors import ValidationError
    from .opts import did_you_mean
    if options is not None and not isinstance(options, dict):
        raise ValidationError("sync", f"sync options must be a dict, got {type(options).__name__}")
    o = dict(DEFAULT_SYNC_OPTIONS)
    for k, v in (options or {}).items():
        if k not in _OPTION_KEYS:
            raise ValidationError("sync", f"unknown sync option {k!r} (expected one of {', '.join(_OPTION_KEYS)})"
                                          f"{did_you_mean([k], _OPTION_KEYS)}")
        if v is not None:
            o[k] = v
    a = o["attempts"]
    if isinstance(a, bool) or not isinstance(a, int) or a < 1:
        raise ValidationError("sync.attempts", f"sync.attempts must be an integer >= 1, got {a!r}")
    for k in ("base_delay_ms", "max_delay_ms", "timeout_ms"):
        v = o[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0 <= v <= MAX_SYNC_MS):
            raise ValidationError(f"sync.{_CAMEL[k]}", f"sync.{k} must be a number of ms from 0 to {MAX_SYNC_MS}, got {v!r}")
    qf = o.get("queue_file")
    if qf is not None and (not isinstance(qf, (str, os.PathLike)) or not str(qf)):
        raise ValidationError("sync.queueFile", f"sync.queue_file must be a non-empty path, got {qf!r}")
    if qf is not None:
        o["queue_file"] = os.fspath(qf)
    return o


def _short(s: str) -> str:
    return s if len(s) <= 300 else s[:300] + "…"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _applied_of(v: Any) -> Optional[Dict[str, Any]]:
    """The ``applied`` of an agent sync's answer (``{"username": {"applied", ...}, "photo": {"applied", ...}}``), or
    None (TS: ``appliedOf``)."""
    def part(x: Any) -> Optional[Dict[str, Any]]:
        return x if isinstance(x, dict) and isinstance(x.get("applied"), bool) else None
    if not isinstance(v, dict):
        return None
    username, photo = part(v.get("username")), part(v.get("photo"))
    return {"username": username, "photo": photo} if username and photo else None


def _str(v: Any) -> Optional[str]:
    return v if isinstance(v, str) else None


def _http_date_ms(h: Optional[str]) -> Optional[float]:
    if not h:
        return None
    try:
        return email.utils.parsedate_to_datetime(h).timestamp() * 1000
    except (TypeError, ValueError, IndexError):
        return None


def _retry_after_ms(h: Optional[str]) -> Optional[float]:
    """``Retry-After`` in ms (delta-seconds or an HTTP date), or None."""
    if not h:
        return None
    try:
        return max(0.0, float(h) * 1000)
    except ValueError:
        pass
    t = _http_date_ms(h)
    return None if t is None else max(0.0, t - time.time() * 1000)


def _rate_limit_wait_ms(headers) -> Optional[float]:
    """A 429's wait in ms: until ``X-RateLimit-Reset`` (epoch seconds, measured from the answer's ``Date`` header when
    present so a skewed local clock does not matter), else ``Retry-After``; None when neither is usable."""
    try:
        reset = float(headers.get("X-RateLimit-Reset") or "nan")
    except ValueError:
        reset = float("nan")
    if reset == reset and reset > 0:
        now = _http_date_ms(headers.get("Date"))
        return max(0.0, reset * 1000 - (now if now is not None else time.time() * 1000))
    return _retry_after_ms(headers.get("Retry-After"))


_SYNC_OK_STATUSES = ("synced", "already_synced", "partial", "ignored")

_FINAL_AGENT_403 = ("wallet_in_account", "forbidden", "not_branded")


_LOCAL_HTTP_HOSTS = ("localhost", "127.0.0.1")


def check_api_domain(raw: Any) -> str:
    """``raw`` as an ``api_domain``, without trailing slashes: an absolute https URL with no path, query, fragment or
    user info (plain http only for localhost and 127.0.0.1: an API key or session would go out in clear text), else
    ``ValidationError("apiDomain")``. The client's rule (``BasisClient`` checks its own with it) and the standalone sync
    helpers'. Same rule as TS (``checkApiDomain``)."""
    from .errors import ValidationError
    s = str(raw).rstrip("/")

    def bad(why: str) -> ValidationError:
        return ValidationError("apiDomain", f"apiDomain {json.dumps(raw, default=str)} {why}. Pass the API's origin, "
                                            "e.g. 'https://paper.launchonbasis.com'.")
    if not isinstance(raw, str) or any(c in s for c in "?#\\") or any(c.isspace() for c in s):
        raise bad("must be a bare origin (no spaces, query or fragment)")
    p = urlparse(s)
    try:
        p.port  # noqa: B018
        port_ok = True
    except ValueError:
        port_ok = False
    if not p.scheme or not p.netloc or not port_ok:
        raise bad("is not an absolute URL")
    scheme = p.scheme.lower()
    if scheme not in ("http", "https"):
        raise bad("must start with https://")
    if not p.hostname:
        raise bad("is not an absolute URL")
    if scheme == "http" and p.hostname not in _LOCAL_HTTP_HOSTS:
        raise bad("must use https (plain http only for localhost and 127.0.0.1: an API key or session would go out in "
                  "clear text)")
    if p.username is not None or p.password is not None:
        raise bad("must not carry a user name or password")
    if p.path not in ("", "/"):
        raise bad("must have no path")
    return s


def same_api_domain(a: str, b: str) -> bool:
    """Lower case, no trailing slash: how two ``api_domain`` values are compared."""
    return str(a).rstrip("/").lower() == str(b).rstrip("/").lower()


class SignerUnavailableError(Exception):
    """Raised by a ``sign_agent_sync`` that holds no signer for the wallet asked for: the entry stays queued
    (``signer_required``)."""

    code = "signer_required"


def _ack_problem(route: str, text: str, parsed: Optional[Dict[str, Any]], body: Dict[str, Any]) -> Optional[str]:
    """Why a 2xx answer is not an acknowledgement of ``body``, or None when it is one.
    tx route: ``{success: true, status: _SYNC_OK_STATUSES, chainId: integer, txHash: the posted hash (any case)}``.
    agent route: ``{success: true, agent: {wallet: the posted wallet (any case), agentId: the posted id}}``."""
    if not text.strip():
        return "empty body"
    if parsed is None:
        return "body is not a JSON object"
    if parsed.get("success") is not True:
        return "success is not true"
    if route == "tx":
        st = parsed.get("status")
        if not isinstance(st, str) or st not in _SYNC_OK_STATUSES:
            return f"unknown status {json.dumps(st, default=str)}"
        cid = parsed.get("chainId")
        if isinstance(cid, bool) or not isinstance(cid, int):
            return "no chainId"
        h = parsed.get("txHash")
        if not isinstance(h, str) or h.lower() != str(body.get("txHash")).lower():
            return f"txHash {json.dumps(h, default=str)} is not the posted one"
        return None
    a = parsed.get("agent")
    if not isinstance(a, dict):
        return "no agent"
    w = a.get("wallet")
    if not isinstance(w, str) or w.lower() != str(body.get("wallet")).lower():
        return f"agent.wallet {json.dumps(w, default=str)} is not the posted one"
    aid = a.get("agentId")
    if isinstance(aid, bool) or str(aid) != str(body.get("agentId")):
        return f"agent.agentId {json.dumps(aid, default=str)} is not the posted one"
    return None


def _response_socket(res: Any) -> Optional[socket.socket]:
    """The socket under a streamed ``requests`` response (urllib3 1.x / 2.x), or None."""
    raw = getattr(res, "raw", None)
    for path in (("_connection", "sock"), ("_fp", "fp", "raw", "_sock"), ("_fp", "fp", "_sock")):
        cur: Any = raw
        for attr in path:
            cur = getattr(cur, attr, None)
            if cur is None:
                break
        if isinstance(cur, socket.socket):
            return cur
    return None


def _read_body(res: Any, deadline: Optional[float]) -> str:
    """The whole body of a streamed response, within ``deadline`` (``time.monotonic()``; None: no limit). A watchdog
    shuts the socket down at the deadline, so a body trickling in under the read timeout cannot hold the call; raises
    ``TimeoutError`` past it."""
    if deadline is None:
        _ = res.content
        return res.text or ""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("deadline passed before the body")
    sock = _response_socket(res)
    fired = threading.Event()

    def _kill() -> None:
        fired.set()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = threading.Timer(remaining, _kill)
    timer.daemon = True
    timer.start()
    chunks: List[bytes] = []
    try:
        for chunk in res.iter_content(8192):
            chunks.append(chunk)
            if fired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("deadline passed while reading the body")
    except Exception:
        if fired.is_set():
            raise TimeoutError("deadline passed while reading the body")
        raise
    finally:
        timer.cancel()
    if fired.is_set():
        raise TimeoutError("deadline passed while reading the body")
    res._content = b"".join(chunks)  # noqa: SLF001
    res._content_consumed = True  # noqa: SLF001
    return res.text or ""


def _attempt_once(route: str, url: str, body: Dict[str, Any], headers: Dict[str, str], timeout_ms: float,
                  expect: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One POST, classified (see the tables in the module docstring; ``route``: tx | agent | agentSigned). ``kind``:
    ok | retry | stop; plus ``status``, ``code``, ``http_status``, ``error``; ``wait_ms`` on a 429, ``queue`` on a stop.
    ``expect``: what a 2xx must echo (default ``body``)."""
    timeout = (timeout_ms / 1000) if timeout_ms > 0 else None
    deadline = time.monotonic() + timeout if timeout else None
    try:
        res = requests.post(url, json=body, headers=headers or None, timeout=timeout, stream=True)
    except requests.exceptions.Timeout:
        return {"kind": "retry", "status": None, "code": None, "http_status": 0, "error": f"timeout after {timeout_ms:g}ms"}
    except requests.exceptions.RequestException as e:
        return {"kind": "retry", "status": None, "code": None, "http_status": 0, "error": f"network error: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"kind": "retry", "status": None, "code": None, "http_status": 0, "error": f"network error: {e}"}
    s = res.status_code
    try:
        text = _read_body(res, deadline)
    except Exception as e:  # noqa: BLE001
        timed_out = (isinstance(e, (requests.exceptions.Timeout, TimeoutError)) or "timed out" in str(e).lower()
                     or (deadline is not None and time.monotonic() >= deadline))
        error = (f"timeout after {timeout_ms:g}ms reading the body of an HTTP {s} answer" if timed_out
                 else f"network error reading the body of an HTTP {s} answer: {e}")
        return {"kind": "retry", "status": None, "code": None, "http_status": 0, "error": error}
    finally:
        res.close()
    parsed: Optional[Dict[str, Any]] = None
    try:
        j = json.loads(text)
        if isinstance(j, dict):
            parsed = j
    except ValueError:
        pass
    status, code = _str((parsed or {}).get("status")), _str((parsed or {}).get("code"))
    if 200 <= s < 300:
        why = _ack_problem(route, text, parsed, body if expect is None else expect)
        if why:
            return {"kind": "retry", "status": None, "code": "unacknowledged", "http_status": s,
                    "error": f"HTTP {s} without an acknowledgement ({why})" + (f": {_short(text)}" if text else "")}
        if route != "tx":
            return {"kind": "ok", "status": "registered", "code": None, "http_status": s, "error": "",
                    "applied": _applied_of(parsed.get("applied"))}
        partial = f"partial: {_str(parsed.get('projectError')) or 'some events were not stored'}" if status == "partial" else ""
        return {"kind": "ok", "status": status, "code": None, "http_status": s, "error": partial}
    a = {"status": status, "code": code, "http_status": s, "error": f"HTTP {s}: {_short(text)}" if text else f"HTTP {s}"}
    if s == 400:
        return {"kind": "stop", "queue": False, **a}
    if route != "tx":
        if s == 409:
            return {"kind": "stop", "queue": False, **a}
        if route == "agentSigned" and s in (401, 403):
            return {"kind": "stop", "queue": False, **a}
        if s == 403 and code in _FINAL_AGENT_403:
            return {"kind": "stop", "queue": False, **a}
        if s == 403 and code == "wallet_not_linked":
            return {"kind": "stop", "queue": False, **a}
        if s == 429:
            return {"kind": "stop", "queue": True, **a}
        if s == 503 and code == "not_deployed":
            return {"kind": "stop", "queue": True, **a}
    elif s == 422:
        kind = code or ("sync_failed" if status == "error" else status)
        if kind == "reverted":
            return {"kind": "stop", "queue": False, **a}
        return {"kind": "retry", **a}
    if s == 429:
        return {"kind": "retry", "wait_ms": _rate_limit_wait_ms(res.headers), **a}
    if s == 503 and code == "chain_mismatch":
        return {"kind": "stop", "queue": True, **a}
    if s >= 500 or s in (408, 425):
        return {"kind": "retry", **a}
    return {"kind": "stop", "queue": True, **a}


def _agent_refusal(a: Dict[str, Any]) -> str:
    """Why an agent post was refused for good, for the log."""
    if a["http_status"] == 409:
        return "that agentId is synced to another wallet that still controls it"
    code = a.get("code")
    if code == "wallet_in_account":
        return "this wallet belongs to a BASIS account (X, Google or email), and an agent needs a wallet of its own"
    if code == "forbidden":
        return "this wallet is neither the owner nor the agent wallet of that agentId on the registry"
    if code == "not_branded":
        return "the agent carries no BASIS mark"
    if code == "invalid_signature":
        return "the signed message was refused"
    return "refused"


def _sign_failure(e: Exception) -> Dict[str, Any]:
    """A signing step's failure (``auth["sign_agent_sync"]``) as an attempt: no signer is queued, a ValidationError is
    final, an API answer by its status, anything else retried."""
    from .errors import ValidationError
    error = f"could not sign the agent sync: {redact_urls(e)}"
    if getattr(e, "code", None) == "signer_required":
        return {"kind": "stop", "queue": True, "status": None, "code": "signer_required", "http_status": 0, "error": error}
    if isinstance(e, ValidationError):
        return {"kind": "stop", "queue": False, "status": None, "code": e.rule, "http_status": 0, "error": error}
    s = getattr(e, "status", None)
    s = s if isinstance(s, int) and not isinstance(s, bool) else 0
    code = getattr(e, "code", None)
    code = code if isinstance(code, str) else None
    if s == 0 or s >= 500 or s in (408, 425, 429):
        return {"kind": "retry", "status": None, "code": code, "http_status": 0, "error": error}
    return {"kind": "stop", "queue": True, "status": None, "code": code, "http_status": 0, "error": error}


def _deliver(route: str, base: str, path: str, body: Any, headers: Dict[str, str], what: str,
             o: Dict[str, Any], expect: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The retry loop shared by the routes: POSTs ``body`` to ``base + path`` until delivered, stopped or out of
    attempts (an agent route: at most :data:`AGENT_SYNC_MAX_ATTEMPTS`, :data:`AGENT_RETRY_DELAY_FACTOR` times further
    apart: its quota counts every post). ``body`` may be a callable, called for every attempt (the signed first sync
    signs afresh each time); ``expect`` is what a 2xx must echo (default the body). Never raises; does not queue.
    ``what`` names the item in the logs (``sync of 0x...``)."""
    url = base + path
    agent_route = route != "tx"
    attempts = min(o["attempts"], AGENT_SYNC_MAX_ATTEMPTS) if agent_route else o["attempts"]
    delay_factor = AGENT_RETRY_DELAY_FACTOR if agent_route else 1
    last: Dict[str, Any] = {"status": None, "code": None, "http_status": 0, "error": ""}
    for i in range(1, attempts + 1):
        a: Optional[Dict[str, Any]] = None
        b: Dict[str, Any] = {}
        if callable(body):
            try:
                b = body()
            except Exception as e:  # noqa: BLE001
                a = _sign_failure(e)
        else:
            b = body
        if a is None:
            a = _attempt_once(route, url, b, headers, o["timeout_ms"], expect)
        if a["kind"] == "ok":
            if a["error"]:
                logger.warning("[basis-sdk] %s is %s", what, a["error"])
            applied = {} if route == "tx" else {"applied": a.get("applied")}
            return {"ok": True, "status": a["status"], "code": None, "http_status": a["http_status"], "attempts": i,
                    "last_error": "", "queue": False, **applied}
        last = a
        if a["kind"] == "stop":
            if a["code"] == "chain_mismatch" or (route != "tx" and a["code"] == "not_deployed"):
                why = "is on another chain" if a["code"] == "chain_mismatch" else "has no ERC-8004 registry"
                logger.error(
                    "[basis-sdk] %s: the API at %s %s (%s: %s). "
                    "Not retried; check api_domain / the chain, then call client.flush_pending_syncs().",
                    what, base, why, a["code"], a["error"])
            elif route != "tx" and a["http_status"] == 429:
                logger.warning(
                    "[basis-sdk] %s rate limited (%s): the API takes 10 agent syncs an hour per wallet. Queued: call "
                    "client.flush_pending_syncs() after the hour.", what, a["error"])
            elif route != "tx" and a["code"] == "wallet_not_linked":
                logger.warning("[basis-sdk] %s: this wallet cannot sign in yet (%s); its agent needs the signed first "
                               "sync.", what, a["error"])
            elif not a["queue"] and a["http_status"] == 0 and route == "agentSigned":
                logger.error("[basis-sdk] %s not sent: %s (not retried, not queued)", what, a["error"])
            elif not a["queue"]:
                bad = a["http_status"] == 400
                if bad:
                    why = " (malformed request: SDK bug, please report)"
                elif route != "tx":
                    why = f" ({_agent_refusal(a)}: not retried, not queued)"
                else:
                    why = " (reverted: not retried, not queued)"
                (logger.error if bad or route != "tx" else logger.warning)(
                    "[basis-sdk] %s rejected: %s%s", what, a["error"], why)
            return {"ok": False, "status": a["status"], "code": a["code"], "http_status": a["http_status"],
                    "attempts": i, "last_error": a["error"], "queue": a["queue"]}
        if i < attempts:
            backoff = min(o["base_delay_ms"] * delay_factor * 2 ** (i - 1), o["max_delay_ms"])
            w = a.get("wait_ms")
            time.sleep((min(w, o["max_delay_ms"]) if w else backoff) / 1000)
    return {"ok": False, "status": last["status"], "code": last["code"], "http_status": last["http_status"],
            "attempts": attempts, "last_error": last["error"], "queue": True}


def deliver_sync(api_domain: str, tx_hash: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """POSTs ``{txHash, source: "sdk"}`` to ``api_domain + SYNC_ROUTE`` with retries and backoff. Never raises on a
    delivery failure; does not queue (the engine does). Logs permanent rejections, a chain mismatch and partial syncs.
    An ``api_domain`` the client would refuse (:func:`check_api_domain`: https, plain http only for localhost) or bad
    options raise ValidationError before anything is sent.

    Returns ``{"ok", "status", "code", "http_status", "attempts", "last_error", "queue"}``; ``queue`` is False for a
    permanent rejection (400, 422 reverted, 409, malformed hash or body): flushing it later cannot help.
    """
    return _post_sync(check_api_domain(api_domain), tx_hash, options)


def _post_sync(api_domain: str, tx_hash: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """:func:`deliver_sync` without the ``api_domain`` check: the engine posts to its client's (checked when the client
    was made)."""
    o = resolve_sync_options(options)
    if not isinstance(tx_hash, str) or not _HASH.fullmatch(tx_hash):
        last_error = f"invalid txHash {json.dumps(tx_hash, default=str)}: expected 0x + 64 hex characters"
        logger.error("[basis-sdk] sync not sent: %s (SDK bug, please report)", last_error)
        return {"ok": False, "status": None, "code": None, "http_status": 0, "attempts": 0, "last_error": last_error,
                "queue": False}
    body = {"txHash": tx_hash, "source": SYNC_SOURCE}
    return _deliver("tx", api_domain.rstrip("/"), SYNC_ROUTE, body, {}, f"sync of {tx_hash}", o)


def _agent_body(agent: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """``agent`` (``wallet``, ``agent_id``, ``name``, ``description``) as a POST /api/agents body, or why it cannot
    be one. ``agent_id``: a whole number (:func:`~basis.core.units.to_uint`: a whole float such as 2.0 is taken and
    sent as 2, as TS takes it; a fraction, bool or string is refused) from 0 to 2147483647."""
    from .errors import ValidationError
    from .units import to_uint
    wallet = agent.get("wallet") if isinstance(agent, dict) else None
    if not isinstance(wallet, str) or not _ADDRESS.fullmatch(wallet):
        return None, f"invalid wallet {json.dumps(wallet, default=str)}: expected 0x + 40 hex characters"
    raw_id = agent.get("agent_id")
    try:
        agent_id: Optional[int] = to_uint(raw_id, "agentId")
    except ValidationError:
        agent_id = None
    if agent_id is None or agent_id > _MAX_AGENT_ID:
        return None, f"agentId {raw_id} is outside 0..{_MAX_AGENT_ID} (the API stores an Int)"
    body: Dict[str, Any] = {"wallet": wallet, "agentId": agent_id}
    if agent.get("name"):
        body["name"] = agent["name"]
    if agent.get("description"):
        body["description"] = agent["description"]
    return body, ""


def deliver_agent_sync(api_domain: str, agent: Dict[str, Any], auth: Optional[Dict[str, Any]] = None,
                       options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """POSTs ``agent`` (``{"wallet", "agent_id", "name"?, "description"?}``) to ``api_domain + AGENT_SYNC_ROUTE`` with
    ``auth`` (``{"api_key", "session_cookie"}``: ``X-API-Key`` and / or the session ``Cookie`` with
    ``X-Wallet-Address``), retried like a tx sync. The session goes only with an entry of a wallet it holds
    (``session_wallets``, any case), the key only with an entry of its own wallet (``api_key_wallet``): a credential
    whose wallet is not known never goes along. Never raises on a delivery failure; does not queue. Without credentials
    (``auth`` None or not a dict included) nothing is sent and the result asks to be queued (code ``auth_required``);
    a malformed body is logged and not queued. An ``api_domain`` the client would refuse (:func:`check_api_domain`) or
    bad options raise ValidationError before anything is sent.

    Returns the :func:`deliver_sync` shape (``status`` ``registered`` when delivered).
    """
    return _post_agent_sync(check_api_domain(api_domain), agent, auth, options)


def _holds(wallets: Any, wallet: str) -> bool:
    """True when ``wallet`` is one of ``wallets`` (any case)."""
    return isinstance(wallets, (list, tuple, set, frozenset)) and any(
        isinstance(w, str) and w.lower() == wallet.lower() for w in wallets)


def _post_agent_sync(api_domain: str, agent: Dict[str, Any], auth: Optional[Dict[str, Any]] = None,
                     options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """:func:`deliver_agent_sync` without the ``api_domain`` check (the engine's own API)."""
    o = resolve_sync_options(options)
    a = auth if isinstance(auth, dict) else {}
    what = f"agent registration of {agent.get('wallet') if isinstance(agent, dict) else agent} " \
           f"(agentId {agent.get('agent_id') if isinstance(agent, dict) else None})"
    body, err = _agent_body(agent)
    if body is None:
        logger.error("[basis-sdk] %s not sent: %s", what, err)
        return {"ok": False, "status": None, "code": None, "http_status": 0, "attempts": 0, "last_error": err,
                "queue": False}
    key, cookie = a.get("api_key"), a.get("session_cookie")
    if cookie and not _holds(a.get("session_wallets"), body["wallet"]):
        cookie = None
    key_wallet = a.get("api_key_wallet")
    if key and not (isinstance(key_wallet, str) and key_wallet.lower() == body["wallet"].lower()):
        key = None
    if not key and not cookie:
        last_error = (f"POST {AGENT_SYNC_ROUTE} needs an API key or a SIWE session of {body['wallet']}: authenticate, "
                      "then call client.flush_pending_syncs()")
        return {"ok": False, "status": None, "code": "auth_required", "http_status": 0, "attempts": 0,
                "last_error": last_error, "queue": True}
    headers: Dict[str, str] = {}
    if key:
        headers["X-API-Key"] = key
    if cookie:
        headers["Cookie"] = cookie
        headers["X-Wallet-Address"] = body["wallet"]
    return _deliver("agent", api_domain.rstrip("/"), AGENT_SYNC_ROUTE, body, headers, what, o)


def deliver_agent_signed_sync(api_domain: str, agent: Dict[str, Any], auth: Optional[Dict[str, Any]] = None,
                              options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """POSTs the signed first sync of ``agent["wallet"]``'s agent ``agent["agent_id"]`` to ``api_domain +
    AGENT_SYNC_ROUTE`` without credentials, retried like a tx sync, with the message signed afresh for every attempt by
    ``auth["sign_agent_sync"](wallet, agent_id)`` (a new Issued time; an old message is never sent again). A 2xx
    counts only when it echoes the wallet and agentId. Never raises on a delivery failure; does not queue. Without
    ``sign_agent_sync`` (``auth`` None or not a dict included) nothing is sent and the result asks to be queued (code
    ``signer_required``); a malformed wallet or agentId is logged and not queued. An ``api_domain`` the client would
    refuse (:func:`check_api_domain`) or bad options raise ValidationError before anything is signed or sent. Returns
    the :func:`deliver_sync` shape."""
    return _post_agent_signed_sync(check_api_domain(api_domain), agent, auth, options)


def _post_agent_signed_sync(api_domain: str, agent: Dict[str, Any], auth: Optional[Dict[str, Any]] = None,
                            options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """:func:`deliver_agent_signed_sync` without the ``api_domain`` check (the engine's own API)."""
    o = resolve_sync_options(options)
    a = auth if isinstance(auth, dict) else {}
    wallet = agent.get("wallet") if isinstance(agent, dict) else None
    agent_id = agent.get("agent_id") if isinstance(agent, dict) else None
    what = f"signed agent sync of {wallet} (agentId {agent_id})"
    checked, err = _agent_body({"wallet": wallet, "agent_id": agent_id})
    if checked is None:
        logger.error("[basis-sdk] %s not sent: %s", what, err)
        return {"ok": False, "status": None, "code": None, "http_status": 0, "attempts": 0, "last_error": err,
                "queue": False}
    agent_id = checked["agentId"]
    sign = a.get("sign_agent_sync")
    if not callable(sign):
        last_error = (f"the signed first sync of {wallet} needs that wallet's signer: call client.flush_pending_syncs() "
                      "from a client with it")
        return {"ok": False, "status": None, "code": "signer_required", "http_status": 0, "attempts": 0,
                "last_error": last_error, "queue": True}

    def body() -> Dict[str, Any]:
        s = sign(wallet, agent_id)
        return {"agentId": s["agentId"], "message": s["message"], "signature": s["signature"]}

    return _deliver("agentSigned", api_domain.rstrip("/"), AGENT_SYNC_ROUTE, body, {}, what, o,
                    {"wallet": wallet, "agentId": agent_id})



def _entry_key(e: Dict[str, Any]) -> str:
    return f"{e.get('kind') or 'tx'}:{str(e['hash']).lower()}"


def _dedupe(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge entries by kind and hash (the later one wins, attempts as the max, the sender / target / nonce / block it
    lacks and the earliest ``queued_at`` taken from the earlier one)."""
    m: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        prev = m.get(_entry_key(e))
        if prev:
            merged = {**e, "attempts": max(prev["attempts"], e["attempts"])}
            for k in ("from", "to", "nonce", "block"):
                if merged.get(k) is None and prev.get(k) is not None:
                    merged[k] = prev[k]
            qa = [x for x in (prev.get("queued_at"), e.get("queued_at")) if x]
            if qa:
                merged["queued_at"] = min(qa)
            m[_entry_key(e)] = merged
        else:
            m[_entry_key(e)] = e
    return list(m.values())


def _to_file(e: Dict[str, Any]) -> Dict[str, Any]:
    """The queue file is shared with the TS SDK, so its lines use camelCase (``lastError``, ``agentId``,
    ``apiDomain``); every line names its ``kind`` (``tx`` for a tx sync)."""
    out: Dict[str, Any] = {"hash": e["hash"], "attempts": e["attempts"], "lastError": e.get("last_error", ""),
                           "code": e.get("code"), "at": e.get("at", "")}
    for k, fk in (("from", "from"), ("to", "to"), ("nonce", "nonce"), ("block", "block"), ("queued_at", "queuedAt"),
                  ("api_domain", "apiDomain")):
        if e.get(k) is not None:
            out[fk] = e[k]
    kind = e.get("kind")
    if kind in ("agent", "agentSigned"):
        ag = e["agent"]
        a: Dict[str, Any] = {"wallet": ag["wallet"], "agentId": ag["agent_id"]}
        for k in ("name", "description"):
            if ag.get(k) is not None:
                a[k] = ag[k]
        return {"kind": kind, **out, "agent": a}
    return {"kind": "tx", **out}


def _to_line(e: Dict[str, Any]) -> str:
    return json.dumps(_to_file(e))


def _from_file(e: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(e.get("hash"), str):
        raise ValueError("no hash")
    try:
        attempts = int(e.get("attempts") or 0)
    except (TypeError, ValueError, OverflowError):
        attempts = 0
    out = {"hash": e["hash"], "attempts": attempts, "last_error": str(e.get("lastError") or ""),
           "code": _str(e.get("code")), "at": str(e.get("at") or "")}
    for k in ("from", "to"):
        if isinstance(e.get(k), str) and _ADDRESS.fullmatch(e[k]):
            out[k] = e[k]
    for k in ("nonce", "block"):
        v = e.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            out[k] = v
    if isinstance(e.get("queuedAt"), str) and e["queuedAt"]:
        out["queued_at"] = e["queuedAt"]
    if isinstance(e.get("apiDomain"), str) and e["apiDomain"]:
        out["api_domain"] = e["apiDomain"]
    kind = e.get("kind")
    if kind is None or kind == "tx":
        return out
    if kind not in ("agent", "agentSigned"):
        raise ValueError(f"unknown kind {kind!r}")
    a = e.get("agent")
    if not isinstance(a, dict) or not isinstance(a.get("wallet"), str):
        raise ValueError("no agent")
    agent: Dict[str, Any] = {"wallet": a["wallet"], "agent_id": a.get("agentId")}
    for k in ("name", "description"):
        if isinstance(a.get(k), str):
            agent[k] = a[k]
    return {"kind": kind, **out, "agent": agent}


def _with_meta(entry: Dict[str, Any], meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``entry`` with the known fields of ``meta`` (``from``, ``to``, ``nonce``, ``block``; None: not known, never
    written). TS: withMeta."""
    for k in ("from", "to", "nonce", "block"):
        v = (meta or {}).get(k)
        if v is not None:
            entry[k] = v
    return entry


def _deliver_entry(api_domain: str, e: Dict[str, Any], options: Dict[str, Any],
                   auth: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Delivers one queued entry by its kind (to an ``api_domain`` already checked)."""
    if e.get("kind") == "agent":
        return _post_agent_sync(api_domain, e["agent"], auth, options)
    if e.get("kind") == "agentSigned":
        return _post_agent_signed_sync(api_domain, e["agent"], auth, options)
    return _post_sync(api_domain, e["hash"], options)



UNKNOWN_TX_MAX_AGE_MS = 24 * 60 * 60 * 1000
CHAIN_CHECKED_CODES = ("not_confirmed", "pending")


def _hex_str(v: Any) -> str:
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()
    s = str(v or "").lower()
    return s if s.startswith("0x") else "0x" + s


def _iso_ms(s: Any) -> Optional[float]:
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def _chain_receipt(web3: Any, h: str) -> Optional[Any]:
    from web3.exceptions import TransactionNotFound
    try:
        return web3.eth.get_transaction_receipt(h)
    except TransactionNotFound:
        return None


def _chain_tx(web3: Any, h: str) -> Optional[Any]:
    from web3.exceptions import TransactionNotFound
    try:
        return web3.eth.get_transaction(h)
    except TransactionNotFound:
        return None


def _find_nonce_tx(web3: Any, sender: str, nonce: int, start_block: Optional[int]) -> Optional[Any]:
    """The mined tx of ``sender`` with ``nonce``: the receipt wait's bisect of the sender's nonce (:func:`nonce_block`)
    from ``start_block``, which moves up past state the node no longer keeps. None when the lookup cannot finish: the tx
    sits in blocks the node has no state for, the node is behind, or the RPC failed. It never means "no such tx": the
    caller saw the nonce used."""
    from web3 import Web3
    from .replacement import nonce_block
    try:
        first = max(int(start_block or 0), 0)
        at = nonce_block(web3, Web3.to_checksum_address(sender), nonce, first - 1, int(web3.eth.block_number))
        block = web3.eth.get_block(at, full_transactions=True)
    except Exception:  # noqa: BLE001
        return None
    me = sender.lower()
    for t in block["transactions"]:
        if hasattr(t, "get") and str(t.get("from") or "").lower() == me and int(t["nonce"]) == nonce:
            return t
    return None


def check_queued_tx(web3: Any, e: Dict[str, Any]) -> Tuple[str, Optional[str], str]:
    """What a flush does with tx entry ``e`` (see the table above): ``(action, new_hash, why)``, action ``post`` |
    ``keep`` | ``drop`` | ``rewrite`` (``new_hash``: the replacement to post instead). Never raises (a failed check is
    ``post``)."""
    h = e["hash"]
    queued = _iso_ms(e.get("queued_at") or e.get("at"))
    too_old = queued is not None and time.time() * 1000 - queued > UNKNOWN_TX_MAX_AGE_MS
    hours = UNKNOWN_TX_MAX_AGE_MS // 3600000
    try:
        if _chain_receipt(web3, h) is not None:
            return "post", None, ""
        if _chain_tx(web3, h) is not None:
            return "keep", None, "still pending (in the mempool): not posted yet"
        sender, nonce = e.get("from"), e.get("nonce")
        if isinstance(sender, str) and isinstance(nonce, int):
            from web3 import Web3
            if web3.eth.get_transaction_count(Web3.to_checksum_address(sender), "latest") > nonce:
                if _chain_receipt(web3, h) is not None:
                    return "post", None, ""
                t = _find_nonce_tx(web3, sender, nonce, e.get("block"))
                if t is None and too_old:
                    return "drop", None, (f"nonce {nonce} of {sender} was used, but the transaction was not found for over "
                                          f"{hours} h (no old state on the RPC)")
                if t is None:
                    return "keep", None, (f"nonce {nonce} of {sender} was used, but this node cannot find the transaction "
                                          "(no old state, or behind): not posted yet; an archive RPC settles it")
                th = _hex_str(t["hash"])
                if th == h.lower():
                    return "post", None, ""
                if str(t.get("to") or "").lower() == sender.lower() and int(t.get("value") or 0) == 0:
                    return "drop", None, f"never mined: cancelled in the wallet by {th} (same nonce)"
                rr = _chain_receipt(web3, th)
                if rr is None:
                    return "keep", None, f"replaced by {th} (same nonce), whose receipt is not readable yet"
                if rr.get("status") != 1:
                    return "drop", None, f"never mined: replaced by {th} (same nonce), which reverted"
                return "rewrite", th, f"never mined: replaced by {th} (same nonce); syncing that one instead"
        if too_old:
            return "drop", None, f"unknown to the chain (no receipt, not pending) for over {hours} h"
        return "keep", None, "not on chain (no receipt, not pending): not posted yet"
    except Exception as ex:  # noqa: BLE001
        logger.debug("[basis-sdk] chain check of queued tx %s failed (%s); posting it", h, redact_urls(ex))
        return "post", None, ""


def _retry_entries(api_domain: str, entries: List[Dict[str, Any]], options: Dict[str, Any],
                   auth: Optional[Dict[str, Any]] = None, web3: Any = None):
    """Retries ``entries``; returns ``(synced, dropped, keep, outcomes, skipped)``: the counts, the entries still worth
    keeping, what happened to each (by ``_entry_key``): ``("delivered", None)``, ``("dropped", None)``,
    ``("kept", entry)`` or ``("skipped", None)`` (another API's entry, left as it is), and the skipped entries.
    With ``web3``, tx entries of :data:`CHAIN_CHECKED_CODES` are checked on the chain first (:func:`check_queued_tx`).
    Every tx entry goes before the agent entries, and an agent entry whose tx entry (same hash) is kept as not seen
    mined (a :data:`CHAIN_CHECKED_CODES` code: still pending, not on chain, the API's RPC behind) is kept unposted,
    "waiting for its tx": the API would record the registration as it was before that tx and the entry would be
    dropped as delivered."""
    synced = dropped = 0
    keep: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    outcomes: Dict[str, Tuple[str, Optional[Dict[str, Any]]]] = {}
    unmined: Dict[str, str] = {}
    ordered = [x for x in entries if x.get("kind") is None] + [x for x in entries if x.get("kind") is not None]
    for e in ordered:
        key = _entry_key(e)
        tx_hash = str(e["hash"]).lower()
        if e.get("api_domain") and not same_api_domain(e["api_domain"], api_domain):
            skipped.append(e)
            outcomes[key] = ("skipped", None)
            continue
        if e.get("kind") is not None and tx_hash in unmined:
            kept = {**e, "last_error": f"waiting for its tx {e['hash']}: {unmined[tx_hash]}", "at": _now_iso()}
            keep.append(kept)
            outcomes[key] = ("kept", kept)
            continue
        if web3 is not None and e.get("kind") is None and e.get("code") in CHAIN_CHECKED_CODES:
            action, new_hash, why = check_queued_tx(web3, e)
            if action == "keep":
                kept = {**e, "last_error": why, "at": _now_iso()}
                keep.append(kept)
                outcomes[key] = ("kept", kept)
                unmined[tx_hash] = why
                continue
            if action == "drop":
                logger.warning("[basis-sdk] dropped the queued sync of %s: %s", e["hash"], why)
                dropped += 1
                outcomes[key] = ("dropped", None)
                continue
            if action == "rewrite":
                logger.warning("[basis-sdk] queued sync of %s: %s", e["hash"], why)
                e = {k: v for k, v in e.items() if k != "to"}
                e = {**e, "hash": new_hash, "code": None, "last_error": why}
        r = _deliver_entry(api_domain, e, options, auth)
        if e.get("kind") == "agent" and not r["ok"] and r["code"] == "wallet_not_linked":
            e = {**e, "kind": "agentSigned", "agent": {"wallet": e["agent"]["wallet"], "agent_id": e["agent"]["agent_id"]}}
            s = _post_agent_signed_sync(api_domain, e["agent"], auth, options)
            r = {**s, "attempts": r["attempts"] + s["attempts"]}
        if r["ok"]:
            synced += 1
            outcomes[key] = ("delivered", None)
        elif r["queue"]:
            kept = {**e, "attempts": e["attempts"] + r["attempts"], "last_error": r["last_error"], "code": r["code"],
                    "at": _now_iso()}
            keep.append(kept)
            outcomes[key] = ("kept", kept)
            if e.get("kind") is None and r["code"] in CHAIN_CHECKED_CODES:
                unmined[tx_hash] = r["last_error"]
        else:
            dropped += 1
            outcomes[key] = ("dropped", None)
    if skipped:
        apis = ", ".join(dict.fromkeys(str(x.get("api_domain")) for x in skipped))
        logger.warning("[basis-sdk] kept %d queued sync(s) of another API (%s) untouched: flush them with a client of "
                       "that API.", len(skipped), apis)
    return synced, dropped, keep, outcomes, skipped



_LOCK_STALE_S = 10.0
_LOCK_WAIT_S = 15.0


def _random_id() -> str:
    return f"{int(time.time() * 1000):x}{secrets.token_hex(5)}"


def _lock_queue_file(path: str) -> str:
    """Takes ``<path>.lock``; returns the token to release it with. Raises ``TimeoutError`` after 15 s."""
    lock = path + ".lock"
    token = _random_id()
    deadline = time.time() + _LOCK_WAIT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, json.dumps({"pid": os.getpid(), "lang": "py", "at": _now_iso(), "token": token}).encode("utf-8"))
            finally:
                os.close(fd)
            return token
        except FileExistsError:
            pass
        except PermissionError:
            pass
        _break_stale_lock(lock)
        if time.time() > deadline:
            raise TimeoutError(f"sync queue {path} is locked ({lock}); gave up after {_LOCK_WAIT_S * 1000:g} ms")
        time.sleep(0.005 + random.random() * 0.02)


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _lock_age_s(content: str, lock: str) -> Optional[float]:
    """Age of the lock whose content is ``content``: from its own ``at`` (ISO time written by its holder); when that is
    unreadable (empty: the holder has not written it yet; or foreign), from the file's mtime read AFTER the content
    (a newer lock only makes it younger). None when the file is gone."""
    try:
        at = json.loads(content).get("at") if content.strip() else None
    except (ValueError, AttributeError):
        at = None
    t = _iso_ms(at)
    if t is not None:
        return time.time() - t / 1000
    try:
        return time.time() - os.stat(lock).st_mtime
    except OSError:
        return None


def _break_stale_lock(lock: str, _before_rename: Optional[Any] = None) -> None:
    """Removes ``lock`` when it is older than 10 s (its holder died), without removing a fresh one: staleness is judged
    from the same content that is compared after the rename (``_before_rename``: test hook, called just before it)."""
    try:
        seen = _read_text(lock)
    except OSError:
        return
    age = _lock_age_s(seen, lock)
    if age is None or age <= _LOCK_STALE_S:
        return
    aside = f"{lock}.stale-{_random_id()}"
    if _before_rename is not None:
        _before_rename()
    try:
        os.rename(lock, aside)
    except OSError:
        return
    try:
        now = _read_text(aside)
        fresh = now != seen or (not now.strip() and (_lock_age_s(now, aside) or 0) <= _LOCK_STALE_S)
        if fresh:
            try:
                os.link(aside, lock)
            except OSError:
                pass
        else:
            logger.warning("[basis-sdk] removed a stale sync queue lock %s", lock)
    except OSError:
        pass
    finally:
        try:
            os.unlink(aside)
        except OSError:
            pass


_UNLOCK_RETRY_S = 5.0


def _unlock_queue_file(path: str, token: str) -> None:
    """Removes the lock if it is still ours. On Windows a waiter reading the lock (the stale check) makes the delete
    fail for a moment; retried, since a lock left behind looks held for 10 s and is then broken as stale while other
    writers race for it (appends were lost that way)."""
    lock = path + ".lock"
    deadline = time.time() + _UNLOCK_RETRY_S
    while True:
        try:
            if token in _read_text(lock):
                os.unlink(lock)
            return
        except PermissionError:
            if time.time() > deadline:
                logger.warning("[basis-sdk] could not remove the sync queue lock %s (it is broken as stale in 10 s)",
                               lock)
                return
            time.sleep(0.002 + random.random() * 0.008)
        except OSError:
            return


@contextlib.contextmanager
def _queue_lock(path: str):
    """Holds the queue file's lock for the ``with`` block."""
    token = _lock_queue_file(path)
    try:
        yield
    finally:
        _unlock_queue_file(path, token)


def _read_queue_lines_bad(path: str) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
    """The file's lines (stripped, non-empty) with their entries, and the lines that cannot be read (malformed, a kind
    this version does not know, or anything else that fails to parse, e.g. nesting too deep), logged and left out. A
    write moves those to ``<file>.bad`` (:func:`_move_bad_lines`), never drops them: one bad line never blocks a
    write."""
    try:
        text = _read_text(path)
    except FileNotFoundError:
        return [], []
    out: List[Tuple[str, Dict[str, Any]]] = []
    bad: List[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            if not isinstance(e, dict):
                raise ValueError("not an object")
            out.append((line, _from_file(e)))
        except Exception:  # noqa: BLE001
            bad.append(line)
            logger.warning("[basis-sdk] skipping an unreadable line in sync queue %s: %s", path, _short(line))
    return out, bad


def _read_queue_lines(path: str) -> List[Tuple[str, Dict[str, Any]]]:
    """The file's readable lines with their entries (see :func:`_read_queue_lines_bad`)."""
    return _read_queue_lines_bad(path)[0]


def _move_bad_lines(path: str, bad: List[str]) -> List[str]:
    """Appends the unreadable ``bad`` lines of ``path`` to ``<path>.bad`` (call under the lock, before rewriting the
    file). Returns the lines to keep in the file: none once they are moved, all of them when the move failed (never
    lost)."""
    if not bad:
        return []
    try:
        with open(path + ".bad", "a", encoding="utf-8", newline="\n") as f:
            f.write("".join(line + "\n" for line in bad))
        logger.warning("[basis-sdk] moved %d unreadable line(s) of sync queue %s to %s.bad", len(bad), path, path)
        return []
    except OSError as e:
        logger.warning("[basis-sdk] could not move unreadable lines of sync queue %s to %s.bad (kept in place): %s",
                       path, path, e)
        return list(bad)


def read_sync_queue_file(path: str) -> List[Dict[str, Any]]:
    """Entries of a JSON-lines sync queue (``[]`` when the file does not exist; unreadable lines are left out here, and
    moved to ``<file>.bad`` by the next write)."""
    return [e for _, e in _read_queue_lines(path)]


def _replace_queue_file(path: str, lines: List[str]) -> None:
    """Replaces the file by ``lines`` with a temp file + rename, so a crash never leaves half a file. Call under the
    lock."""
    tmp = f"{path}.tmp-{os.getpid()}-{_random_id()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(line + "\n" for line in lines))
            f.flush()
            os.fsync(f.fileno())
        for i in range(51):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if i >= 50:
                    raise
                time.sleep(0.02)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        d = os.path.dirname(os.path.abspath(path))
        prefix = os.path.basename(path) + ".tmp-"
        for name in os.listdir(d):
            if name.startswith(prefix):
                p = os.path.join(d, name)
                try:
                    if time.time() - os.stat(p).st_mtime > _LOCK_STALE_S:
                        os.unlink(p)
                except OSError:
                    pass
    except OSError:
        pass


def _append_queue_file(path: str, entry: Dict[str, Any]) -> bool:
    """Appends ``entry`` to the queue file (under the lock, temp file + rename). Logs instead of raising; returns
    whether the line was written."""
    try:
        with _queue_lock(path):
            read, bad = _read_queue_lines_bad(path)
            kept = _move_bad_lines(path, bad)
            _replace_queue_file(path, kept + [line for line, _ in read] + [_to_line(entry)])
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[basis-sdk] could not append to sync queue %s: %s", path, e)
        return False


def _snapshot_queue_file(path: str) -> Tuple[List[Dict[str, Any]], Set[str]]:
    """A flush's view of the file: its entries and the exact lines they came from."""
    with _queue_lock(path):
        read = _read_queue_lines(path)
    return [e for _, e in read], {line for line, _ in read}


def _commit_queue_file(path: str, taken: Set[str], outcomes: Dict[str, Tuple[str, Optional[Dict[str, Any]]]]) -> None:
    """Writes a flush's result into the file as it is NOW (re-read under the lock): the ``taken`` lines with an
    outcome are removed (delivered, dropped) or replaced once by their updated entry (kept); every other line, appended
    since the snapshot included, stays. A kept entry no longer in the file (memory only, or removed by another flusher)
    is added."""
    with _queue_lock(path):
        read, bad = _read_queue_lines_bad(path)
        out: List[str] = _move_bad_lines(path, bad)
        present: Set[str] = set()
        for line, entry in read:
            k = _entry_key(entry)
            o = outcomes.get(k)
            if o and o[0] != "skipped" and line in taken:
                if o[0] == "kept" and k not in present:
                    out.append(_to_line(o[1]))
                    present.add(k)
                continue
            out.append(line)
            present.add(k)
        for k, (kind, entry) in outcomes.items():
            if kind == "kept" and k not in present:
                out.append(_to_line(entry))
        _replace_queue_file(path, out)


def flush_sync_queue_file(path: str, api_domain: str, options: Optional[Dict[str, Any]] = None,
                          auth: Optional[Dict[str, Any]] = None, web3: Any = None) -> Dict[str, int]:
    """Standalone flush of a sync queue file (e.g. from a cron job or another process): retries every entry against
    ``api_domain``, then removes the entries it delivered (or that were rejected for good) and updates the ones still
    failing. Entries appended meanwhile, by this process or another (PY or TS), are kept, and so are entries of
    another API (their ``apiDomain``), untouched. Agent registrations need ``auth``: ``{"api_key", "api_key_wallet"}``
    (the wallet the key belongs to: it goes only with that wallet's entries) or ``{"session_cookie",
    "session_wallets"}`` (the wallets the session holds); a credential without its wallet goes with no entry; a signed
    first sync needs ``{"sign_agent_sync": fn(wallet, agent_id)}`` returning the signed body. Without them (``auth``
    None or not a dict included) the entries stay queued (auth_required / signer_required). With ``web3``
    (a ``Web3`` on the chain of the entries), sent-but-unconfirmed entries are checked on the chain first
    (:func:`check_queued_tx`: posted once mined, dropped or rewritten when their nonce went to another tx). An
    ``api_domain`` the client would refuse (:func:`check_api_domain`: https, plain http only for localhost) raises
    ``ValidationError("apiDomain")`` before the file is read. Returns ``{"synced", "failed"}``."""
    api = check_api_domain(api_domain)
    o = resolve_sync_options(options)
    entries, taken = _snapshot_queue_file(path)
    synced, dropped, keep, outcomes, _ = _retry_entries(api, _dedupe(entries), o, auth, web3)
    try:
        _commit_queue_file(path, taken, outcomes)
    except Exception as e:  # noqa: BLE001
        logger.warning("[basis-sdk] could not rewrite sync queue %s: %s", path, e)
    return {"synced": synced, "failed": len(keep) + dropped}



class SyncEngine:
    """The client's sync engine: ``client.sync_tx`` and ``client.sync_agent_registration`` go through it.

    Queue entries: ``hash``, ``attempts``, ``last_error``, ``code``, ``at``, ``queued_at`` (first queued),
    ``api_domain`` (the API it belongs to); a tx line of the write pipeline adds ``from`` (the sender) and ``to`` (the
    contract called; ``agent.register_and_sync`` reads on chain only a line of its wallet to the identity registry or
    without either), and a tx sent but not seen confirmed also ``nonce`` and ``block`` (latest block before sending)
    for the flush's chain check; agent registrations add ``kind: "agent"`` and ``agent``
    (``wallet``, ``agent_id``, ``name``, ``description``), signed first syncs ``kind: "agentSigned"`` and ``agent``
    (``wallet``, ``agent_id`` only: no signed message is ever kept). The queue file, shared with the TS SDK, writes
    ``lastError``, ``queuedAt``, ``apiDomain``, ``agentId`` and a ``kind`` on every line (``tx`` for a tx sync).
    """

    def __init__(self, api_domain, options: Optional[Dict[str, Any]] = None, auth=None, chain=None):
        self._api_domain = api_domain
        self._auth = auth or (lambda: {})
        self._chain = chain or (lambda: None)
        self.options = resolve_sync_options(options)
        self.pending: List[Dict[str, Any]] = []

    def sync(self, tx_hash: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Delivers one sync with retries; on a failure worth retrying later, logs and queues it (with ``meta``: the
        tx's sender and target when known, ``{"from", "to"}``). Never raises.

        Returns ``{"ok", "status", "code", "http_status", "attempts", "queued", "last_error"}``."""
        r = _post_sync(self._api_domain(), tx_hash, self.options)
        queue = r.pop("queue")
        if r["ok"] or not queue:
            return {**r, "queued": False}
        where = self._enqueue(_with_meta({"hash": tx_hash, "attempts": r["attempts"], "last_error": r["last_error"],
                                          "code": r["code"], "at": _now_iso()}, meta))
        logger.warning(
            "[basis-sdk] sync of confirmed tx %s failed after %d attempt(s): %s. Queued in client.pending_syncs%s; "
            "call client.flush_pending_syncs() to retry.",
            tx_hash, r["attempts"], r["last_error"], where,
        )
        return {**r, "queued": True}

    def sync_agent(self, tx_hash: str, agent: Dict[str, Any]) -> Dict[str, Any]:
        """Delivers the ``POST /api/agents`` of a registration made by tx ``tx_hash``, with retries; on a failure worth
        retrying later (no credentials included), logs and queues it as a ``kind: "agent"`` entry. Never raises.
        Returns the :meth:`sync` shape."""
        r = _post_agent_sync(self._api_domain(), agent, self._auth(), self.options)
        queue = r.pop("queue")
        if not r["ok"] and r["code"] == "wallet_not_linked":
            s = self.sync_agent_signed(tx_hash, {"wallet": agent.get("wallet"), "agent_id": agent.get("agent_id")})
            return {**s, "attempts": r["attempts"] + s["attempts"]}
        if r["ok"] or not queue:
            return {**r, "queued": False}
        where = self._enqueue({"kind": "agent", "hash": tx_hash, "agent": dict(agent), "attempts": r["attempts"],
                               "last_error": r["last_error"], "code": r["code"], "at": _now_iso()})
        logger.warning(
            "[basis-sdk] agent registration of %s (agentId %s, tx %s) failed after %d attempt(s): %s. Queued in "
            "client.pending_syncs%s; call client.flush_pending_syncs() to retry.",
            agent.get("wallet"), agent.get("agent_id"), tx_hash, r["attempts"], r["last_error"],
            where,
        )
        return {**r, "queued": True}

    def sync_agent_signed(self, tx_hash: str, agent: Dict[str, Any]) -> Dict[str, Any]:
        """Delivers the signed first sync of ``agent["wallet"]``'s agent ``agent["agent_id"]`` (registered by tx
        ``tx_hash``), signed afresh for every attempt; on a failure worth retrying later, logs and queues it as a
        ``kind: "agentSigned"`` entry (the wallet and agentId only: a flush signs it again). Never raises. Returns the
        :meth:`sync` shape."""
        intent = {"wallet": agent.get("wallet"), "agent_id": agent.get("agent_id")}
        r = _post_agent_signed_sync(self._api_domain(), intent, self._auth(), self.options)
        queue = r.pop("queue")
        if r["ok"] or not queue:
            return {**r, "queued": False}
        where = self._enqueue({"kind": "agentSigned", "hash": tx_hash, "agent": intent, "attempts": r["attempts"],
                               "last_error": r["last_error"], "code": r["code"], "at": _now_iso()})
        logger.warning(
            "[basis-sdk] signed agent sync of %s (agentId %s, tx %s) failed after %d attempt(s): %s. Queued in "
            "client.pending_syncs%s (a flush signs it again); call client.flush_pending_syncs() to retry.",
            intent["wallet"], intent["agent_id"], tx_hash, r["attempts"], r["last_error"], where,
        )
        return {**r, "queued": True}

    def queue_agent_signed(self, tx_hash: str, agent: Dict[str, Any], last_error: str,
                           code: str = "not_confirmed") -> Dict[str, Any]:
        """Queues the signed first sync of ``agent`` (tx ``tx_hash``) as a ``kind: "agentSigned"`` entry without
        posting it (e.g. an agent.update whose receipt could not be read). :meth:`flush` signs and delivers it after the
        tx entry. Never raises. Returns the :meth:`sync` shape."""
        try:
            intent = {"wallet": agent.get("wallet"), "agent_id": agent.get("agent_id")}
            where = self._enqueue({"kind": "agentSigned", "hash": tx_hash, "agent": intent, "attempts": 0,
                                   "last_error": last_error, "code": code, "at": _now_iso()})
            logger.warning(
                "[basis-sdk] signed agent sync of %s (agentId %s, tx %s) queued without posting (%s: %s). Kept in "
                "client.pending_syncs%s; call client.flush_pending_syncs() once it is mined.",
                intent["wallet"], intent["agent_id"], tx_hash, code, last_error, where,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[basis-sdk] could not queue the signed agent sync of %s: %s", tx_hash, e)
        return {"ok": False, "status": None, "code": code, "http_status": 0, "attempts": 0, "queued": True,
                "last_error": last_error}

    def queue_tx(self, tx_hash: str, last_error: str, code: str = "not_confirmed",
                 meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Queues the tx sync of ``tx_hash`` without posting it (e.g. a sent tx whose receipt could not be read: the
        API would answer 422 pending). :meth:`flush` delivers it later. ``meta``: ``{"from", "to", "nonce", "block"}``
        of the sent tx when known (the flush's chain check uses them). Never raises. Returns the :meth:`sync` shape."""
        try:
            entry = {"hash": tx_hash, "attempts": 0, "last_error": last_error, "code": code, "at": _now_iso()}
            where = self._enqueue(_with_meta(entry, meta))
            logger.warning(
                "[basis-sdk] sync of tx %s queued without posting (%s: %s). Kept in client.pending_syncs%s; call "
                "client.flush_pending_syncs() once it is mined.", tx_hash, code, last_error, where,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[basis-sdk] could not queue the sync of %s: %s", tx_hash, e)
        return {"ok": False, "status": None, "code": code, "http_status": 0, "attempts": 0, "queued": True,
                "last_error": last_error}

    def queue_agent(self, tx_hash: str, agent: Dict[str, Any], last_error: str,
                    code: str = "not_confirmed") -> Dict[str, Any]:
        """Queues the ``POST /api/agents`` of ``agent`` (tx ``tx_hash``) as a ``kind: "agent"`` entry without posting
        it (e.g. an agent.update whose receipt could not be read). :meth:`flush` delivers it after the tx entry. Never
        raises. Returns the :meth:`sync` shape."""
        try:
            where = self._enqueue({"kind": "agent", "hash": tx_hash, "agent": dict(agent), "attempts": 0,
                                   "last_error": last_error, "code": code, "at": _now_iso()})
            logger.warning(
                "[basis-sdk] agent registration of %s (agentId %s, tx %s) queued without posting (%s: %s). Kept in "
                "client.pending_syncs%s; call client.flush_pending_syncs() once it is mined.",
                agent.get("wallet"), agent.get("agent_id"), tx_hash, code, last_error, where,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[basis-sdk] could not queue the agent registration of %s: %s", tx_hash, e)
        return {"ok": False, "status": None, "code": code, "http_status": 0, "attempts": 0, "queued": True,
                "last_error": last_error}

    def _enqueue(self, entry: Dict[str, Any]) -> str:
        """Keeps ``entry`` (replacing one of the same kind and hash) and appends it to ``queue_file``, tagged with the
        API it belongs to (``api_domain``). Returns where it is kept, for the logs after ``client.pending_syncs``:
        ``""``, ``" and <file>"``, or ``" only (<file> could not be written)"`` when the append failed."""
        entry.setdefault("api_domain", str(self._api_domain()).rstrip("/"))
        prev = [e for e in self.pending if _entry_key(e) == _entry_key(entry)]
        entry.setdefault("queued_at", (prev[0].get("queued_at") if prev else None) or entry.get("at") or _now_iso())
        self.pending[:] = [e for e in self.pending if _entry_key(e) != _entry_key(entry)]
        self.pending.append(entry)
        qf = self.options.get("queue_file")
        if not qf:
            return ""
        return f" and {qf}" if _append_queue_file(qf, entry) else f" only ({qf} could not be written)"

    def flush(self, options: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
        """Retries every queued entry (memory + ``queue_file``); keeps what still fails. The file is updated under its
        lock: only the entries this flush delivered (or that were rejected for good) leave it; entries appended
        meanwhile stay."""
        resolve_sync_options(options)
        opts = resolve_sync_options({**self.options, **{k: v for k, v in (options or {}).items() if v is not None}})
        qf = self.options.get("queue_file")
        taken = self.pending[:]
        del self.pending[:]
        from_file: List[Dict[str, Any]] = []
        lines: Set[str] = set()
        if qf:
            try:
                from_file, lines = _snapshot_queue_file(qf)
            except Exception as e:  # noqa: BLE001
                logger.warning("[basis-sdk] could not read sync queue %s: %s", qf, e)
        entries = _dedupe(taken + from_file)
        try:
            synced, dropped, keep, outcomes, skipped = _retry_entries(self._api_domain(), entries, opts, self._auth(),
                                                                      self._chain())
        except BaseException:
            self.pending[:] = _dedupe(taken + self.pending)
            raise
        from_memory = {_entry_key(e) for e in taken}
        mine = [e for e in skipped if _entry_key(e) in from_memory]
        self.pending[:] = _dedupe(mine + keep + self.pending)
        if qf:
            try:
                _commit_queue_file(qf, lines, outcomes)
            except Exception as e:  # noqa: BLE001
                logger.warning("[basis-sdk] could not rewrite sync queue %s: %s", qf, e)
        return {"synced": synced, "failed": len(keep) + dropped}
