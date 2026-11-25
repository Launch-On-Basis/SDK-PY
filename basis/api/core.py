"""HTTP core of the off-chain API client: base URL, auth modes, query building, errors, cursor paging.

Domain modules (``basis/api/<domain>.py``) define mixins on :class:`ApiCore`; ``basis/api/__init__.py`` composes them
into ``BasisAPI``. TypeScript SDK: ``src/api/core.ts``.
"""

import copy
import json as _json
import logging
import math
import socket
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from http.cookiejar import DefaultCookiePolicy
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from ..core.errors import BasisError, ValidationError
from ..core.units import to_uint

logger = logging.getLogger("basis")

DEFAULT_API_TIMEOUT_MS = 30_000
DEFAULT_API_UPLOAD_TIMEOUT_MS = 120_000
DEFAULT_API_AI_TIMEOUT_MS = 330_000
MAX_TIMEOUT_MS = 2_147_483_647
SESSION_COOKIE_NAME = "__Host-basis_session"
DEV_SESSION_COOKIE_NAME = "basis_auth_session"
_KIND_ATTR = {"default": ("api_timeout_ms", DEFAULT_API_TIMEOUT_MS),
              "upload": ("api_upload_timeout_ms", DEFAULT_API_UPLOAD_TIMEOUT_MS),
              "ai": ("api_ai_timeout_ms", DEFAULT_API_AI_TIMEOUT_MS)}

AUTH_MODES = ("none", "key", "session", "keyOrSession", "anyKeyOrSession", "optional", "optionalSession")


class BasisApiError(BasisError):
    """A non-2xx API answer, or no complete answer in time. The message carries the server's ``error`` text.

    Attributes: ``status`` (HTTP status; 0 when no complete answer was read), ``code`` (``code`` of the
    ``{error, code}`` body, e.g. ``bad_param``, ``rate_limited``; None when absent; ``timeout`` when the request passed
    its deadline: not retried, it may have reached the server), ``body`` (parsed JSON, or the raw text),
    ``retry_after`` (seconds to wait: from ``Retry-After`` when the answer has one, else from the body's
    ``retryAfterMs``, else (429 and 503 with code ``rate_limited`` or none) from ``X-RateLimit-Reset`` (epoch seconds);
    None otherwise: a daily cap or a busy writer names no wait of its own).
    """

    def __init__(self, status: int, code: Optional[str], message: str, body: Any, retry_after: Optional[int] = None):
        super().__init__(f"[basis-sdk] {message}")
        self.status = status
        self.code = code
        self.body = body
        self.retry_after = retry_after


class AuthRequiredError(BasisError):
    """The request needs credentials the client does not have, or would carry a SIWE session or an API key that is not
    the signer's (``detail`` says why; None otherwise). Raised before any request is sent. ``about``: what ``detail``
    is about, ``session`` (sign in again) or ``apiKey`` (the key belongs to another wallet, or to one the SDK does not
    know)."""

    def __init__(self, mode: str, what: str, detail: Optional[str] = None, about: str = "session"):
        if detail and about == "apiKey":
            msg = (f"[basis-sdk] Authentication required: {what} would go out with an API key that is not the signer's "
                   f"({detail}). Sign the signer in with client.authenticate(), or pass the signer's own key (api_key).")
        elif detail:
            msg = (f"[basis-sdk] Authentication required: {what} would go out with a SIWE session that does not hold "
                   f"the signer's wallet ({detail}). Sign in again with client.authenticate().")
        else:
            if mode == "key":
                need = ("an API key (this route does not accept a SIWE session): pass api_key or use "
                        "BasisClient.create() with a private_key")
            elif mode == "session":
                need = "a SIWE session: use BasisClient.create() with a private_key, or call client.authenticate()"
            else:
                need = "an API key or a SIWE session: use BasisClient.create() with a private_key, or pass api_key"
            msg = f"[basis-sdk] Authentication required: {what} needs {need}."
        super().__init__(msg)
        self.mode = mode
        self.detail = detail or None


def seg(v: Any) -> str:
    """Encodes one path segment. "", "." and ".." raise ``ValidationError("pathSegment")``: requests would drop or
    collapse them and send the request to another route (``get_wallet_loan_events("..")`` to the caller's own
    ``/api/v1/loans/events``)."""
    s = str(v)
    if s in ("", ".", ".."):
        what = "empty" if s == "" else f'"{s}"'
        raise ValidationError("pathSegment",
                              f"a path argument cannot be {what}: pass the address, id or name it asks for.")
    return quote(s, safe="")


def query_value(v: Any) -> str:
    """bool -> true/false, int/Decimal/float -> decimal string, str as is."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def build_query(params: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Query dict with None dropped and values serialised (see :func:`query_value`)."""
    return {k: query_value(v) for k, v in (params or {}).items() if v is not None}


def _header_retry_after(headers: Any) -> Optional[int]:
    """``Retry-After`` in seconds, None when absent or not a finite number."""
    ra = headers.get("Retry-After")
    if ra is None or str(ra).strip() == "":
        return None
    try:
        v = float(ra)
    except ValueError:
        return None
    return max(0, int(v)) if math.isfinite(v) else None


def retry_after_of(headers: Any) -> Optional[int]:
    """Seconds until a retry is allowed: ``Retry-After`` (seconds) or ``X-RateLimit-Reset`` (epoch seconds)."""
    ra = _header_retry_after(headers)
    if ra is not None:
        return ra
    reset = headers.get("X-RateLimit-Reset")
    if reset is not None:
        try:
            r = float(reset)
            if math.isfinite(r) and r > 0:
                return max(0, math.ceil(r - time.time()))
        except ValueError:
            pass
    return None


def error_retry_after(status: int, headers: Any, body: Any = None, code: Optional[str] = None) -> Optional[int]:
    """``retry_after`` of an error answer, seconds: ``Retry-After`` when present, else the body's ``retryAfterMs``
    (rounded up), else ``X-RateLimit-Reset`` for a 429 or 503 whose code is ``rate_limited`` or absent. That header is
    the key's or the session's per-minute bucket, so a refusal by another limit (``daily_wallet_cap``, ``gate_busy``,
    ...) gets no wait from it, and a 400 / 404 none at all (it carries the header too, but waiting does not help it).
    TS: ``errorRetryAfter``."""
    ra = _header_retry_after(headers)
    if ra is not None:
        return ra
    ms = body.get("retryAfterMs") if isinstance(body, dict) else None
    if isinstance(ms, (int, float)) and not isinstance(ms, bool) and 0 <= ms < math.inf:
        return math.ceil(min(ms, MAX_TIMEOUT_MS) / 1000)
    if status in (429, 503) and code in ("rate_limited", None):
        return retry_after_of(headers)
    return None


def api_error_from(res: requests.Response, what: str) -> BasisApiError:
    """Reads a non-2xx response into a :class:`BasisApiError`."""
    text = res.text or ""
    body: Any = text
    try:
        body = _json.loads(text) if text else None
    except ValueError:
        pass
    b = body if isinstance(body, dict) else {}
    msg = b.get("error") if isinstance(b.get("error"), str) else (text or res.reason or "request failed")
    code = b.get("code") if isinstance(b.get("code"), str) else None
    label = f"{res.status_code} {code}" if code else f"{res.status_code}"
    hint = f" {PRELAUNCH_HINT}" if code == "prelaunch" else ""
    return BasisApiError(res.status_code, code, f"{what} failed [{label}]: {msg}{hint}", body,
                         error_retry_after(res.status_code, res.headers, body, code))


PRELAUNCH_HINT = "The real site is not live yet: use chain=\"robinhoodPaper\" (the paper site) for now."


def rate_limit_of(headers: Any, status: int, route: str) -> Optional[Dict[str, Any]]:
    """The ``X-RateLimit-*`` headers of an answer as ``{"limit", "remaining", "reset" (epoch s), "status", "route",
    "at"}`` (a missing header is None), or None when it has none. TS: ``rateLimitOf``."""
    def num(name: str) -> Optional[float]:
        v = headers.get(name)
        try:
            n = float(v) if v is not None and str(v).strip() != "" else None
        except ValueError:
            return None
        if n is None or n != n or n in (float("inf"), float("-inf")):
            return None
        return int(n) if n.is_integer() else n
    limit, remaining, reset = num("X-RateLimit-Limit"), num("X-RateLimit-Remaining"), num("X-RateLimit-Reset")
    if limit is None and remaining is None and reset is None:
        return None
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {"limit": limit, "remaining": remaining, "reset": reset, "status": status, "route": route, "at": at}


def check_timeout_ms(v: Any, rule: str) -> float:
    """Raises ``ValidationError(rule)`` unless ``v`` is a finite number of ms above 0 and at most
    :data:`MAX_TIMEOUT_MS`; returns it."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0 < v <= MAX_TIMEOUT_MS):
        raise ValidationError(rule, f"{rule} must be a number of ms above 0 and at most {MAX_TIMEOUT_MS}, got {v!r}.")
    return v


def _is_timeout(e: BaseException) -> bool:
    """A requests error that is a connect / read timeout (a read timeout while streaming the body comes as a
    ConnectionError wrapping urllib3's ReadTimeoutError)."""
    if isinstance(e, requests.exceptions.Timeout):
        return True
    try:
        from urllib3.exceptions import ReadTimeoutError
    except ImportError:  # pragma: no cover
        return False
    cur: Any = e
    for _ in range(5):
        if isinstance(cur, ReadTimeoutError):
            return True
        nxt = cur.args[0] if getattr(cur, "args", None) else None
        if not isinstance(nxt, BaseException):
            return False
        cur = nxt
    return False


def timeout_error(what: str, timeout_ms: float) -> BasisApiError:
    """The :class:`BasisApiError` of a request past its deadline (status 0, code ``timeout``)."""
    return BasisApiError(0, "timeout", f"{what} timed out after {timeout_ms:g} ms without a complete answer", None)


_watch = threading.local()


class _TrackConns:
    def _get_conn(self, timeout=None):  # noqa: ANN001
        conn = super()._get_conn(timeout)  # type: ignore[misc]
        conns = getattr(_watch, "conns", None)
        if conns is not None:
            conns.append(conn)
        return conn


class _TrackingHTTPPool(_TrackConns, HTTPConnectionPool):
    pass


class _TrackingHTTPSPool(_TrackConns, HTTPSConnectionPool):
    pass


class DeadlineAdapter(HTTPAdapter):
    """An ``HTTPAdapter`` whose connections :func:`request_with_deadline` can shut down at its deadline (headers and
    body included). :func:`deadline_session` mounts it; a plain ``requests.Session`` passed to request_with_deadline
    gets it mounted on first use."""

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _TrackingHTTPPool, "https": _TrackingHTTPSPool}


class _NoCookies(DefaultCookiePolicy):
    """Cookie policy that neither stores nor sends a cookie (sessions for requests that must go without cookies)."""

    def set_ok(self, cookie: Any, request: Any) -> bool:
        return False

    def return_ok(self, cookie: Any, request: Any) -> bool:
        return False


def deadline_session(cookies: bool = True) -> requests.Session:
    """A ``requests.Session`` for :func:`request_with_deadline` (DeadlineAdapter mounted). ``cookies=False``: it never
    stores or sends a cookie."""
    s = requests.Session()
    s.mount("http://", DeadlineAdapter())
    s.mount("https://", DeadlineAdapter())
    if not cookies:
        s.cookies.set_policy(_NoCookies())
    return s


def _ensure_tracking(session: Any) -> None:
    """Mounts DeadlineAdapter on a session that still has requests' stock adapters (custom adapters are left alone:
    their requests keep the per-chunk deadline only)."""
    adapters = getattr(session, "adapters", None)
    if not isinstance(adapters, dict):
        return
    for prefix in ("https://", "http://"):
        if type(adapters.get(prefix)) is HTTPAdapter:  # noqa: E721
            session.mount(prefix, DeadlineAdapter())


def request_with_deadline(session: Any, method: str, url: str, timeout_ms: float, what: str,
                          **kwargs: Any) -> requests.Response:
    """``session.request`` with a deadline covering connect, headers and the whole body: requests' connect / read
    timeouts, plus a watchdog that shuts the request's socket down at the deadline, so a server trickling its headers
    or body cannot hold the call past it. Returns the response with its body read (``.content``, ``.text``,
    ``.json()`` work). Past the deadline: :func:`timeout_error`. Other network errors are raised as requests raises
    them. Never retries.

    ``session``: a ``requests.Session`` (DeadlineAdapter is mounted on a stock one; :func:`deadline_session` makes
    one), or None / the ``requests`` module for a one-off cookie-less session. Reusable by any SDK caller (e.g. the
    sync engine's posts)."""
    own = session is None or session is requests
    if own:
        session = deadline_session(cookies=False)
    _ensure_tracking(session)
    secs = timeout_ms / 1000
    deadline = time.monotonic() + secs
    conns: List[Any] = []
    lock = threading.Lock()
    state = {"done": False, "fired": False}

    def watchdog() -> None:
        with lock:
            if state["done"]:
                return
            state["fired"] = True
            for conn in conns:
                sock = getattr(conn, "sock", None)
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

    timer = threading.Timer(secs, watchdog)
    timer.daemon = True
    res = None
    prev = getattr(_watch, "conns", None)
    _watch.conns = conns
    timer.start()
    try:
        try:
            res = session.request(method, url, timeout=(secs, secs), stream=True, **kwargs)
        finally:
            _watch.conns = prev
        chunks = []
        for chunk in res.iter_content(65536):
            chunks.append(chunk)
            if time.monotonic() > deadline:
                raise timeout_error(what, timeout_ms)
        res._content = b"".join(chunks)  # noqa: SLF001
        res._content_consumed = True  # noqa: SLF001
        return res
    except BasisApiError:
        if res is not None:
            res.close()
        raise
    except Exception as e:  # noqa: BLE001
        if res is not None:
            res.close()
        if state["fired"] or (isinstance(e, requests.RequestException) and _is_timeout(e)):
            raise timeout_error(what, timeout_ms) from e
        raise
    finally:
        with lock:
            state["done"] = True
        timer.cancel()
        if own:
            session.close()


def _whole_from(v: Any, field: str, low: int) -> int:
    """``v`` as an int of at least ``low``: an int, or a whole float as TS takes ``2.0`` (through :func:`to_uint`, so a
    bool, a fraction or a string is refused); else ``ValidationError(field)`` "<field> must be a whole number from
    <low>" (the TS message)."""
    try:
        n = to_uint(v, field)
    except ValidationError:
        n = None
    if n is None or n < low:
        raise ValidationError(field, f"{field} must be a whole number from {low}, got {v!r}.")
    return n


def _json_default(v: Any) -> Any:
    if isinstance(v, Decimal):
        return format(v, "f")
    raise TypeError(f"not JSON serialisable: {type(v).__name__}")


class ApiCore:
    """Base of every API domain mixin: request plumbing only. No retries here (the sync engine has its own); a 429
    raises a :class:`BasisApiError` whose ``retry_after`` says how long to wait. Every request has a deadline (client
    ``api_timeout_ms`` / ``api_upload_timeout_ms`` / ``api_ai_timeout_ms``, per call :meth:`with_options`)."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.session = deadline_session()
        self._plain = deadline_session(cookies=False)
        self._root: "ApiCore" = self
        self._last_rate_limit: Optional[Dict[str, Any]] = None
        self._call_timeout_ms: Optional[float] = None

    @property
    def last_rate_limit(self) -> Optional[Dict[str, Any]]:
        """The ``X-RateLimit-*`` headers (limit, remaining, reset) of the last answer that carried them, 2xx included;
        None until one did (see :func:`rate_limit_of`). Lets a caller slow down before a 429 (60 a minute per key).
        One value per client API object: a :meth:`with_options` view reads and writes the value of the API object it
        came from (live), so answers read through a view land there too. Same in TS (``lastRateLimit``)."""
        return self._root._last_rate_limit  # noqa: SLF001

    @last_rate_limit.setter
    def last_rate_limit(self, value: Optional[Dict[str, Any]]) -> None:
        self._root._last_rate_limit = value  # noqa: SLF001

    def with_options(self, timeout_ms: Optional[float] = None) -> Any:
        """The same API with per-call options: ``client.api.with_options(timeout_ms=5000).get_token(address)``.

        ``timeout_ms`` replaces the client's deadline (``api_timeout_ms``, ``api_upload_timeout_ms``,
        ``api_ai_timeout_ms``) for every request made through the returned object, from connecting to the last body
        byte (a request past it raises :class:`BasisApiError` with code ``timeout``). Cheap: the client, the session
        (cookies) and ``last_rate_limit`` are shared (a view reads and writes its source's, live). TS:
        ``withOptions({ timeoutMs, signal })``.
        """
        view = copy.copy(self)
        if timeout_ms is not None:
            view._call_timeout_ms = check_timeout_ms(timeout_ms, "timeoutMs")
        return view

    def _timeout_for(self, kind: str = "default", timeout_ms: Optional[float] = None) -> float:
        """The deadline of a request, ms: the per-call option, else the request's own, else the client's for ``kind``
        (``default`` | ``upload`` | ``ai``)."""
        if self._call_timeout_ms is not None:
            return self._call_timeout_ms
        if timeout_ms is not None:
            return timeout_ms
        attr, default = _KIND_ATTR[kind]
        v = getattr(self.client, attr, None)
        return default if v is None else v

    @property
    def _base_url(self) -> str:
        return self.client.api_domain.rstrip("/")

    def _session_cookie_pair(self) -> Optional[str]:
        """The SIWE session cookie in the jar as ``name=value`` (``__Host-basis_session``, or ``basis_auth_session`` on a
        dev server), None when there is none. Other cookies (e.g. Cloudflare's ``__cf_bm``) are never a session."""
        for c in self.session.cookies:
            if c.name in (SESSION_COOKIE_NAME, DEV_SESSION_COOKIE_NAME) and c.value:
                return f"{c.name}={c.value}"
        return None

    def _session_held(self) -> bool:
        """True when the jar holds the SIWE session cookie (whichever wallets it holds)."""
        return self._session_cookie_pair() is not None

    def _session_state(self) -> str:
        """``none`` (no session cookie), ``ok`` (the session holds the signer's wallet, or the client tracks no
        wallets) or ``mismatch`` (a session the SDK did not confirm for the signer)."""
        if not self._session_held():
            return "none"
        wallets = getattr(self.client, "_session_wallets", None)
        if wallets is None:
            return "ok"
        address = getattr(getattr(self.client, "account", None), "address", None)
        return "ok" if address and address.lower() in wallets else "mismatch"

    def _has_session(self) -> bool:
        """True when a SIWE session can be sent for the current signer (session cookie present, signer's wallet
        confirmed in it)."""
        return self._session_state() == "ok"

    def _mismatch_detail(self) -> str:
        """Why the session is not the signer's (the AuthRequiredError ``detail``)."""
        address = getattr(getattr(self.client, "account", None), "address", None)
        wallets = list(getattr(self.client, "_session_wallets", None) or [])
        if not address:
            return "the client has no signer to match it against" + (
                f"; session wallets: {', '.join(wallets)}" if wallets else "")
        if not wallets:
            return f"the SDK knows no wallet of this session (signed out, or never confirmed); signer {address}"
        return f"it was confirmed for {', '.join(wallets)}, not for the signer {address}"

    def _key_state(self) -> str:
        """``none`` (no API key), ``ok`` (the key's wallet, ``client.api_key_wallet``, is the signer's, or there is no
        signer) or ``mismatch`` (the key belongs to another wallet, or the SDK does not know whose it is). Decides
        whether ``keyOrSession`` / ``optional`` requests send the key. TS: ``apiKeyStateOf``."""
        if not self.client.api_key:
            return "none"
        address = getattr(getattr(self.client, "account", None), "address", None)
        if not address:
            return "ok"
        owner = getattr(self.client, "api_key_wallet", None)
        return "ok" if isinstance(owner, str) and owner.lower() == address.lower() else "mismatch"

    def _key_mismatch_detail(self) -> str:
        """Why the API key is not the signer's (the AuthRequiredError ``detail``). TS: ``apiKeyMismatchDetail``."""
        address = getattr(getattr(self.client, "account", None), "address", None)
        owner = getattr(self.client, "api_key_wallet", None)
        if isinstance(owner, str) and owner:
            return f"the API key belongs to {owner.lower()}, not to the signer {address}"
        return f"the SDK does not know which wallet the API key belongs to; signer {address}"

    def _session_error(self, what: str) -> Optional[AuthRequiredError]:
        """The AuthRequiredError a ``session`` request would raise (no session, or not the signer's), None when it
        can go out."""
        state = self._session_state()
        if state == "none":
            return AuthRequiredError("session", what)
        if state == "mismatch":
            return AuthRequiredError("session", what, self._mismatch_detail())
        return None

    def _auth(self, mode: str, what: str) -> Tuple[Dict[str, str], bool]:
        """Headers (the API key) for ``mode`` and whether the request carries the session; raises AuthRequiredError
        when the client cannot satisfy ``mode``, or when a ``session`` request (or a ``keyOrSession`` one without the
        signer's key) would carry a session that is not the signer's, or a ``keyOrSession`` one has only a key that is
        not the signer's. The key goes on ``keyOrSession`` / ``optional`` requests only when it is the signer's
        (:meth:`_key_state`); a ``keyOrSession`` request with the signer's key goes with the key alone (the server lets
        a sent key decide). An ``optional`` request carries one of them (the session first), an ``optionalSession``
        one the session only, an ``anyKeyOrSession`` one any key the client holds (the signer's first), else the
        session (a session that is not the signer's and no key: AuthRequiredError with the ``detail``)."""
        if mode not in AUTH_MODES:
            raise ValueError(f"unknown auth mode {mode!r}")
        key = self.client.api_key
        if mode == "none":
            return {}, False
        if mode == "key":
            if not key:
                raise AuthRequiredError(mode, what)
            return {"X-API-Key": key}, False
        if mode == "session":
            err = self._session_error(what)
            if err is not None:
                raise err
            return {}, True
        state = self._session_state()
        key_state = self._key_state()
        if mode == "keyOrSession":
            if key and key_state == "ok":
                return {"X-API-Key": key}, False
            if state == "ok":
                return {}, True
            if key_state == "mismatch":
                session = f"; the SIWE session: {self._mismatch_detail()}" if state == "mismatch" else ""
                raise AuthRequiredError(mode, what, f"{self._key_mismatch_detail()}{session}", about="apiKey")
            if state == "mismatch":
                raise AuthRequiredError(mode, what, self._mismatch_detail())
            raise AuthRequiredError(mode, what)
        if mode == "anyKeyOrSession":
            if key and key_state == "ok":
                return {"X-API-Key": key}, False
            if state == "ok":
                return {}, True
            if key:
                return {"X-API-Key": key}, False
            if state == "mismatch":
                raise AuthRequiredError(mode, what, self._mismatch_detail())
            raise AuthRequiredError(mode, what)
        if mode == "optionalSession":
            return {}, state == "ok"
        if state == "ok":
            return {}, True
        return ({"X-API-Key": key} if key and key_state == "ok" else {}), False

    def _auth_headers(self, mode: str, what: str) -> Dict[str, str]:
        """Auth headers for ``mode``; raises AuthRequiredError when the client cannot satisfy it."""
        return self._auth(mode, what)[0]

    def _note_session(self, wallets: Optional[List[str]] = None, remove: Optional[str] = None) -> None:
        """Records what an auth answer said about the session's wallets on the client (``client._session_wallets``):
        ``wallets`` replaces the list, ``remove`` drops one. Cleared when the jar holds no session cookie."""
        if not hasattr(self.client, "_session_wallets"):
            return
        cur = list(self.client._session_wallets or [])  # noqa: SLF001
        if wallets is not None:
            cur = []
            for w in wallets:
                if isinstance(w, str) and w.lower() not in cur:
                    cur.append(w.lower())
        if remove:
            cur = [w for w in cur if w != str(remove).lower()]
        if not self._session_held():
            cur = []
        self.client._session_wallets = cur  # noqa: SLF001

    def _send(
        self,
        method: str,
        path: str,
        auth: str = "none",
        params: Optional[Dict[str, Any]] = None,
        body: Any = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        kind: str = "default",
        timeout_ms: Optional[float] = None,
        include_session: bool = False,
    ) -> requests.Response:
        """Sends a request and returns the response with its body read (any status). Raises AuthRequiredError before
        sending; :class:`BasisApiError` code ``timeout`` when no complete answer arrives in time.

        ``body`` is sent as JSON; ``data`` / ``files`` as multipart form data. ``kind`` picks the client deadline
        (``default``, ``upload``, ``ai``). Only a request that carries the session goes through the cookie jar
        (``api.session``); it also sends ``X-Wallet-Address`` (the signer), so the server acts for the signer's wallet
        of a multi-wallet session. Public and key-only requests send no cookie and no ``X-Wallet-Address``.
        ``include_session``: carry the jar even when no session is confirmed for the signer (verify, GET
        /api/auth/me, logout); such a request goes without the API key (those routes read only the session).
        """
        what = f"{method} {path}"
        auth_headers, carries = self._auth(auth, what)
        if include_session:
            auth_headers = {}
        h = {**auth_headers, **(headers or {})}
        carry = carries or include_session
        account = getattr(self.client, "account", None)
        address = getattr(account, "address", None)
        if address and carry and self._session_held() and not any(k.lower() == "x-wallet-address" for k in h):
            h["X-Wallet-Address"] = address
        kwargs: Dict[str, Any] = {"params": build_query(params), "headers": h}
        if files is not None or data is not None:
            kwargs["data"] = data
            kwargs["files"] = files
        elif body is not None:
            h["Content-Type"] = "application/json"
            kwargs["data"] = _json.dumps(body, default=_json_default)
        res = request_with_deadline(self.session if carry else self._plain, method, f"{self._base_url}{path}",
                                    self._timeout_for(kind, timeout_ms), what, **kwargs)
        rl = rate_limit_of(res.headers, res.status_code, what)
        if rl is not None:
            self._root.last_rate_limit = rl
        return res

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Sends a request (see :meth:`_send`) and parses the answer: JSON, or the text when the body is not JSON
        (None when empty). Non-2xx raises BasisApiError."""
        res = self._send(method, path, **kwargs)
        if not res.ok:
            raise api_error_from(res, f"{method} {path}")
        text = res.text
        if not text:
            return None
        try:
            return res.json()
        except ValueError:
            return text.strip()

    def _key_wallet(self) -> Optional[str]:
        """The lower-case wallet the client's API key belongs to: ``GET /api/v1/me/profile`` sent with the key alone (no
        session, whoever the signer is), which answers for the key's own wallet. None when the answer names no wallet.
        No key: AuthRequiredError before sending; a non-2xx answer raises BasisApiError (401 ``invalid_key``: the key
        does not work). ``ensure_api_key`` reads it before it takes a key of unknown wallet for the signer's (a
        4-character hint match is 1 in 65,536). TS: ``_keyWallet``."""
        me = self._request("GET", "/api/v1/me/profile", auth="key")
        w = me.get("wallet") if isinstance(me, dict) else None
        if isinstance(w, str) and len(w) == 42 and w[:2] == "0x" and all(c in "0123456789abcdefABCDEF" for c in w[2:]):
            return w.lower()
        return None

    def paginate(self, fn: Callable[..., Dict[str, Any]], **params: Any) -> Iterator[Any]:
        """Iterates every row of a cursor-paged list: calls ``fn(**params)``, yields ``data``, then calls again with
        ``cursor=pagination.nextCursor`` while ``hasMore``. Methods taking path arguments go through a lambda:
        ``api.paginate(lambda **p: api.get_token_trades(token, **p), limit=100)``. Stops (with a warning) when the
        server hands back a cursor it already gave: a loop, never more rows."""
        seen = {params["cursor"]} if params.get("cursor") else set()
        while True:
            page = fn(**params) or {}
            for row in page.get("data") or []:
                yield row
            pag = page.get("pagination") or {}
            nxt = pag.get("nextCursor")
            if not pag.get("hasMore") or not nxt:
                return
            if nxt in seen:
                logger.warning("[basis-sdk] paginate: the server repeated cursor %s; stopping (no new rows would come).",
                               nxt)
                return
            seen.add(nxt)
            params = {**params, "cursor": nxt}

    def paginate_pages(self, fn: Callable[..., Dict[str, Any]], **params: Any) -> Iterator[Any]:
        """Iterates every row of a page-numbered list: calls ``fn(**params)`` from ``page`` (default 1), yields
        ``data``, then asks for the next page while ``pagination.hasMore`` and the page had rows. Methods taking path
        arguments go through a lambda: ``api.paginate_pages(lambda **p: api.get_wallet_markets(wallet, **p), limit=100)``.
        ``offset`` is refused (``ValidationError("offset")``): the server lets it override ``page``, so every request
        would answer the same rows; start from a page instead. ``page`` is a whole number from 1 (a whole float such as
        ``2.0`` too, as TS takes it; ``ValidationError("page")`` otherwise). Stops (with a warning) when a later answer
        is not the page asked for (``pagination.page``) or repeats the previous page's rows."""
        if params.get("offset") is not None:
            raise ValidationError("offset", "paginate_pages walks pages: drop offset (the server lets it override page, "
                                            "so every request would answer the same rows) and pass page to start later.")
        params.pop("offset", None)
        given = params.pop("page", None)
        page = 1 if given is None else _whole_from(given, "page", 1)
        previous: Optional[str] = None
        while True:
            res = fn(**params, page=page) or {}
            rows = res.get("data") or []
            answered = (res.get("pagination") or {}).get("page")
            signature = _json.dumps(rows, sort_keys=True, default=str) if rows else None
            if previous is not None and (
                    (isinstance(answered, int) and not isinstance(answered, bool) and answered != page)
                    or signature == previous):
                logger.warning("[basis-sdk] paginate_pages: asked for page %s, the server answered %s; stopping.", page,
                               f"page {answered}" if isinstance(answered, int) and answered != page
                               else "the previous page again")
                return
            for row in rows:
                yield row
            if not (res.get("pagination") or {}).get("hasMore") or not rows:
                return
            previous = signature
            page += 1

    def paginate_offset(self, fn: Callable[..., Dict[str, Any]], **params: Any) -> Iterator[Any]:
        """Iterates every row of an offset-paged list (the Feed: ``get_feed``, ``get_feed_by_wallet``): calls
        ``fn(**params)`` from ``offset`` (default 0), yields ``data``, then asks for ``offset + len(data)`` while
        ``pagination.hasMore`` and the page had rows. Methods taking path arguments go through a lambda:
        ``api.paginate_offset(lambda **p: api.get_feed_by_wallet(name, **p), limit=50)``. Rows outside ``data`` (a
        post's ``comments``) go through an adapter that returns ``{"data", "pagination"}``. ``page`` is refused
        (``ValidationError("page")``: these routes read only ``offset``). ``offset`` is a whole number from 0 (a whole
        float such as ``2.0`` too, as TS takes it; ``ValidationError("offset")`` otherwise). Stops (with a warning) when
        a later answer is not at the offset asked for (``pagination.offset``) or repeats the previous page's rows."""
        if params.get("page") is not None:
            raise ValidationError("page", "paginate_offset walks offsets: drop page (offset-paged routes do not read it) "
                                          "and pass offset to start later.")
        params.pop("page", None)
        given = params.pop("offset", None)
        offset = 0 if given is None else _whole_from(given, "offset", 0)
        previous: Optional[str] = None
        while True:
            res = fn(**params, offset=offset) or {}
            rows = res.get("data") or []
            answered = (res.get("pagination") or {}).get("offset")
            moved = isinstance(answered, int) and not isinstance(answered, bool) and answered != offset
            signature = _json.dumps(rows, sort_keys=True, default=str) if rows else None
            if previous is not None and (moved or signature == previous):
                logger.warning("[basis-sdk] paginate_offset: asked for offset %s, the server answered %s; stopping.",
                               offset, f"offset {answered}" if moved else "the previous page again")
                return
            for row in rows:
                yield row
            if not (res.get("pagination") or {}).get("hasMore") or not rows:
                return
            previous = signature
            offset += len(rows)
