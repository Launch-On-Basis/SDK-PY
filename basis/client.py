import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Union
from urllib.parse import urlparse

import requests
from web3 import HTTPProvider, Web3
from web3.providers.rpc.utils import ExceptionRetryConfiguration, check_if_retry_on_failure
from eth_account import Account
from eth_account.messages import encode_defunct

from .api import BasisAPI
from .api.core import (DEFAULT_API_AI_TIMEOUT_MS, DEFAULT_API_TIMEOUT_MS, DEFAULT_API_UPLOAD_TIMEOUT_MS,
                       BasisApiError, check_timeout_ms)
from .api.auth import LogoutResult, VerifyResult
from .config.addresses import is_zero_address, merge_address_book
from .config.chains import ChainConfig, chain_address_book, chain_block_time_ms, resolve_chain
from .core.errors import BasisError, TxNotConfirmedError, ValidationError, WalletRequiredError
from .core.redact import redact_urls
from .core.revert import classify_error, rpc_error_kind, send_rejection
from .core.replacement import broadcast_raw, estimate_fees_per_gas, gas_limit_for, next_nonce, wait_for_receipt
from .core.mains import MainsModule
from .core.sync import SyncEngine, read_sync_queue_file
from .core.sync import _retry_after_ms
from .core.sync import check_api_domain as _check_api_domain
from .core.opts import did_you_mean
from .core.units import DEFAULT_SLIPPAGE_BPS, to_bool, validate_slippage_bps
from .core.contracts_json import contracts_flag_mismatch
from .core.claimables import get_claimables as _get_claimables
from .modules.agent_identity import AgentIdentityModule
from .modules.factory import FactoryModule
from .modules.paper import PaperModule
from .modules.referrals import ReferralsModule
from .modules.taxes import TaxesModule
from .modules.trading import TradingModule
from .modules.hop_executor import HopExecutorModule
from .modules.loans import LoansModule
from .modules.liquidation import LiquidationModule
from .modules.leverage_simulator import LeverageSimulatorModule
from .modules.main_lens import MainLensModule
from .modules.vault import VaultModule
from .modules.vesting import VestingModule
from .modules.market_reader import MarketReaderModule
from .modules.market_resolver import MarketResolverModule
from .modules.private_markets import PrivateMarketsModule
from .modules.prediction_markets import PredictionMarketsModule
from .modules.order_book import OrderBookModule
from .modules.up_down import UpDownModule

logger = logging.getLogger(__name__)


DEFAULT_RECEIPT_TIMEOUT_MS = 180_000

DEFAULT_RPC_TIMEOUT_MS = 30_000
RPC_RETRY_COUNT = 3
RPC_RETRY_DELAY_MS = 150
RPC_RETRY_AFTER_MAX_MS = 5_000
_RPC_RETRY_ERRORS = (ConnectionError, requests.ConnectionError, requests.HTTPError, requests.Timeout,
                     requests.exceptions.ChunkedEncodingError)
_RPC_RETRY_STATUSES = frozenset({403, 408, 413, 429, 500, 502, 503, 504})
_RPC_RETRY_CODES = frozenset({-1, -32005, -32603})


def _rpc_failure_retried(e: BaseException, errors: Sequence[type]) -> bool:
    """True when the failed RPC request is worth another try, as viem decides: a failure of ``errors`` (the provider's
    retry configuration) and, for an HTTP error answer, a status in :data:`_RPC_RETRY_STATUSES` (one without a status:
    no answer, tried again)."""
    if not isinstance(e, tuple(errors)):
        return False
    if isinstance(e, requests.HTTPError):
        status = getattr(getattr(e, "response", None), "status_code", None)
        return status is None or status in _RPC_RETRY_STATUSES
    return True


def _rpc_retry_after_s(e: BaseException) -> Optional[float]:
    """The wait the ``Retry-After`` header of a failed RPC answer (an HTTP error) asks for before the next try, s:
    delta-seconds or an HTTP date (from now), at most :data:`RPC_RETRY_AFTER_MAX_MS`. None without one, or when it
    cannot be read: the backoff's wait then."""
    headers = getattr(getattr(e, "response", None), "headers", None)
    h = headers.get("Retry-After") if hasattr(headers, "get") else None
    ms = _retry_after_ms(h.strip()) if isinstance(h, str) else None
    return None if ms is None else min(ms, RPC_RETRY_AFTER_MAX_MS) / 1000


def _rpc_answer_retried(raw: Any) -> bool:
    """True when an RPC answer (HTTP 2xx) is worth another try, as viem decides: a body that is not JSON, or a JSON-RPC
    error whose code is in :data:`_RPC_RETRY_CODES`. An empty body is no answer to retry (viem reads it as ``{}``)."""
    try:
        data = json.loads(raw or b"{}")
    except (TypeError, ValueError):
        return True
    err = data.get("error") if isinstance(data, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    return isinstance(code, int) and not isinstance(code, bool) and code in _RPC_RETRY_CODES


class _RpcProvider(HTTPProvider):
    """web3's HTTP provider with TS's (viem's) retry triggers: a request whose method the retry configuration allows
    (its ``method_allowlist``: the write pipeline takes the broadcast off it, :func:`~basis.core.replacement.no_send_retries`,
    so a lost broadcast answer is never sent again) is tried again ``retries - 1`` times, ``backoff_factor`` seconds x 1,
    2, 4 apart (150 / 300 / 600 ms), after a failure :func:`_rpc_failure_retried` accepts or an answer
    :func:`_rpc_answer_retried` accepts (the last answer is returned as it is: web3 raises it). After an HTTP error
    answer with a ``Retry-After`` header (a 429 or 503 of a rate-limited node), the wait is the one it asks for
    (:func:`_rpc_retry_after_s`: seconds or an HTTP date, at most :data:`RPC_RETRY_AFTER_MAX_MS`), as viem takes the
    header (TS: its whole seconds, as given; an HTTP date is no wait there). 400 / 401 / 404 and other JSON-RPC errors
    (a revert, "nonce too low") are never tried again. web3's own loop retried every HTTP error and no JSON-RPC
    answer."""

    def _make_request(self, method: Any, request_data: bytes) -> bytes:
        cfg = self.exception_retry_configuration
        tries = int(cfg.retries) if cfg is not None and check_if_retry_on_failure(method, cfg.method_allowlist) else 1
        tries = max(1, tries)
        for i in range(tries):
            last = i + 1 >= tries
            try:
                raw = self._request_session_manager.make_post_request(
                    self.endpoint_uri, request_data, **self.get_request_kwargs())
            except Exception as e:  # noqa: BLE001
                if last or not _rpc_failure_retried(e, cfg.errors or ()):
                    raise
                wait = _rpc_retry_after_s(e)
            else:
                if last or not _rpc_answer_retried(raw):
                    return raw
                wait = None
            time.sleep(cfg.backoff_factor * 2 ** i if wait is None else wait)
        raise AssertionError("unreachable")


def _call_target(function_call: Any) -> Optional[str]:
    """The contract a write calls (web3's bound ``ContractFunction.address``), or None when it has none."""
    to = getattr(function_call, "address", None)
    return to if isinstance(to, str) else None


def _names_deployment(remote: Dict[str, Any]) -> bool:
    """True when a ``/contracts.json`` object names what :meth:`BasisClient.check_contracts` compares: a numeric
    ``chainId`` (a JSON number: not True / False), and the deployment (``network`` a non-empty str, or ``paper`` True /
    False). Without them a "strict" check confirmed nothing. TS: ``namesDeployment``."""
    cid = remote.get("chainId")
    network = remote.get("network")
    numeric = isinstance(cid, (int, float)) and not isinstance(cid, bool)
    return numeric and ((isinstance(network, str) and network != "") or isinstance(remote.get("paper"), bool))


_SIWE_NONCE_RE = re.compile(r"[A-Za-z0-9]+")


def _siwe_nonce_of(answer: Any) -> str:
    """The ``nonce`` of a ``GET /api/auth/nonce`` answer, or ``ValidationError("siweNonce")`` before anything is signed
    when it is missing, not a str, empty or holds anything but ASCII letters and digits (a line break would add lines to
    the signed text). Same rule and message as TS ``siweNonceOf``."""
    nonce = answer.get("nonce") if isinstance(answer, dict) else None
    if isinstance(nonce, str) and _SIWE_NONCE_RE.fullmatch(nonce):
        return nonce
    got = ("none" if nonce is None else "not a string" if not isinstance(nonce, str)
           else "an empty string" if nonce == "" else "other characters")
    raise ValidationError("siweNonce", f"The API's sign-in nonce must be letters and digits only, got {got}: nothing was "
                                       "signed. Try again; if it persists, the apiDomain may not be a BASIS API.")


def _rpc_provider(rpc_url: str, timeout_ms: float) -> Any:
    """The HTTP provider of ``rpc_url`` with the client's RPC deadline (requests' connect and read timeouts of
    ``timeout_ms``: a per-read deadline, see the ``rpc_timeout_ms`` option) and the SDK's retry policy (as TS): up to
    :data:`RPC_RETRY_COUNT` retries, 150 / 300 / 600 ms apart (or the wait an answer's ``Retry-After`` asks for, at most
    :data:`RPC_RETRY_AFTER_MAX_MS`), on viem's triggers (:class:`_RpcProvider`), for the reads
    web3 retries (its method allowlist; the write pipeline takes the broadcast off it, so a lost broadcast answer is
    never sent again)."""
    return _RpcProvider(
        rpc_url, request_kwargs={"timeout": timeout_ms / 1000},
        exception_retry_configuration=ExceptionRetryConfiguration(
            errors=_RPC_RETRY_ERRORS, retries=RPC_RETRY_COUNT + 1, backoff_factor=RPC_RETRY_DELAY_MS / 1000))



def siwe_domain(api_domain: str) -> str:
    """The SIWE ``domain`` of ``api_domain``, as TS ``new URL(apiDomain).host`` gives it: the host in lower case
    (IDNA-encoded, IPv6 in brackets), the port only when it is not the scheme's default (http 80, https 443), no user
    info. The server rebuilds the message from its own Host and refuses another spelling (``domain_mismatch``)."""
    p = urlparse(api_domain)
    host = p.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    host = host.lower()
    if ":" in host:
        host = f"[{host}]"
    try:
        port = p.port
    except ValueError:
        port = None
    default = {"http": 80, "https": 443, "ws": 80, "wss": 443}.get(p.scheme.lower())
    return f"{host}:{port}" if port is not None and port != default else host


def mask_api_key(key: str) -> str:
    """A loggable hint of an API key: ``bsk_****`` + its last 4 characters (the server's ``keyHint`` format)."""
    return f"bsk_****{str(key)[-4:]}"


_CONTRACTS_CHECK_MODES = ("off", "warn", "strict")


def _no_door_hint(e: BasisApiError) -> BasisApiError:
    """``create()``'s ``wallet_not_linked`` answer without the ``agent`` option: the same BasisApiError (status, code,
    body, retry_after), its message adding the SDK's ways in (the API's own text names the website's). TS:
    ``noDoorHint``."""
    said = str(e)
    if said.startswith("[basis-sdk] "):
        said = said[len("[basis-sdk] "):]
    return BasisApiError(e.status, e.code,
                         f"{said} With the SDK: for an AI agent's own wallet, pass agent=True "
                         "(BasisClient.create(..., agent=True)) to register it as a BASIS agent and sign it in; "
                         "auth=False runs without the API (contract-only).", e.body, e.retry_after)


def _shown(v: Any) -> str:
    """What a value is, for a refusal message (TS: ``shown``)."""
    if v is None:
        return "null"
    if isinstance(v, (list, tuple)):
        return "a list"
    if isinstance(v, str):
        return f"'{v}'"
    if isinstance(v, bool):
        return "true" if v else "false"
    return "an object" if isinstance(v, dict) else str(v)


def _check_slippage_keys(v: Any) -> Dict[str, Any]:
    """The ``slippage_bps`` option checked: None, or a dict whose keys are ``trade`` / ``updown`` (each value is checked
    by :func:`validate_slippage_bps`), else ``ValidationError("slippageBps")``. A key set to None is left out (its
    default), as inside the ``sync`` option; an unknown key is refused whatever its value. Same rule as TS."""
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValidationError("slippageBps", f"slippageBps must be an object {{ trade?, updown? }} of bps, got {_shown(v)}.")
    unknown = [k for k in v if k not in DEFAULT_SLIPPAGE_BPS]
    if unknown:
        raise ValidationError("slippageBps", f"Unknown slippageBps key{'s' if len(unknown) > 1 else ''} "
                                             f"{', '.join(json.dumps(str(k)) for k in unknown)}. Known: "
                                             f"{', '.join(DEFAULT_SLIPPAGE_BPS)}.{did_you_mean(unknown, list(DEFAULT_SLIPPAGE_BPS))}")
    return {k: x for k, x in v.items() if x is not None}


_CHECKED_KEYS = (
    "swap", "factory", "taxes", "leverage", "mainLens", "loanHub", "vesting", "liquidator", "hopExecutor",
    "tranche", "tranchePrivate", "resolver", "trancheReader", "redemptionReader", "upDown", "bondToken", "wrappedNative",
    "referrals",
)

class BasisClient:
    """Client for the Basis protocol.

    The constructor is synchronous and lightweight: it does **not** perform
    network calls.  Use the :meth:`create` classmethod when you want
    RPC validation, SIWE authentication, and API-key provisioning.

    Parameters
    ----------
    chain:
        Network preset ("robinhoodPaper" default, "robinhood") or a custom :class:`ChainConfig`; any other value
        (an unknown name, "") raises ``ValidationError("chain")``.
    rpc_url:
        RPC URL; defaults to the chain preset's public one, which is fine to try the SDK. Pass your own for production:
        Robinhood Chain's public RPC rate-limits after a few dozen calls, has no SLA, and its load-balanced nodes can
        answer a block or two behind. A custom chain config without ``rpc_url`` needs it here; with neither, or a value
        that is not a string: ``ValidationError("rpcUrl")`` (never a fallback to some local node).
    rpc_timeout_ms:
        Deadline of one RPC request try, ms (default 30000, at most 2147483647; same default in TS), per read:
        requests' connect and read timeouts, so the connection must open within it and the answer may not pause longer
        than it between two reads (an answer that keeps coming can take longer in all; TS's deadline is the time until
        the answer starts instead). A request that fails in a way worth retrying (TS's triggers, viem's list: no answer
        in time, a network error, HTTP 403 / 408 / 413 / 429 / 500 / 502 / 503 / 504, a body that is not JSON, JSON-RPC
        error -1 / -32005 / -32603; never 400 / 401 / 404 or another JSON-RPC error such as a revert) is tried again up
        to 3 times, 150 ms, 300 ms and 600 ms apart (:data:`RPC_RETRY_COUNT`, the same count and waits in TS), each try
        with its own deadline, so an RPC that never answers fails after about 4 x this deadline. An HTTP error answer
        with a ``Retry-After`` header sets the wait before the next try instead: seconds or an HTTP date, at most 5 s
        (:data:`RPC_RETRY_AFTER_MAX_MS`; TS takes viem's rule: the header's whole seconds, as given). A broadcast is
        never tried again. Anything else: ``ValidationError("rpcTimeoutMs")``.
    api_domain:
        The API to sign in to and sync with; defaults to the chain preset's (``robinhood`` https://launchonbasis.com,
        ``robinhoodPaper`` https://paper.launchonbasis.com). There is no global default: a paper client never reaches
        the real API unless this option says so, and a custom chain config without ``api_domain`` needs it here
        (``ValidationError("apiDomain")`` otherwise). It must be the API's origin: an absolute https URL with no path,
        query or fragment (plain http only for localhost and 127.0.0.1, so the API key and the session never go out in
        clear text); a trailing slash is dropped. Anything else raises ``ValidationError("apiDomain")``.
    addresses:
        Address overrides over the chain preset (e.g. a local node's addresses). ``mains`` replaces the list. A custom
        chain config's own ``addresses`` are checked the same way first (an unknown key or a bad checksum there:
        ``ValidationError("chain")``), over an empty book: a key it leaves out is zero (not deployed).
    slippage_bps:
        Default slippage in bps for automatic minimums: ``trade`` (swaps, market buys, redeem) and ``updown`` (bets).
        Each a whole number from 0 to 9999, or None for its default; no other key (``ValidationError("slippageBps")``
        otherwise).
    receipt_timeout_ms:
        How long a write waits for its receipt after sending (default 180000 ms). When the wait fails the write raises
        ``TxNotConfirmedError`` (with ``tx_hash``; do not resend) and queues the hash's sync.
    sync:
        Post-tx backend sync options (snake_case keys): ``attempts`` (6), ``base_delay_ms`` (1000, doubled per retry:
        1s, 2s, 4s, 8s, 16s), ``max_delay_ms`` (60000, caps one wait, also a 429's wait until the rate-limit reset),
        ``timeout_ms`` (15000) and ``queue_file`` (JSON-lines file for syncs that failed for good). A write returns once
        its sync is done or queued.
    api_timeout_ms:
        Deadline of an ordinary API request, ms, from connecting to the last body byte (default 30000). Past it the
        request raises ``BasisApiError`` code ``timeout`` (status 0); nothing is retried. Per call:
        ``client.api.with_options(timeout_ms=...)``.
    api_upload_timeout_ms:
        Deadline of an upload (image, metadata, project image) and of an image download, ms (default 120000).
    api_ai_timeout_ms:
        Deadline of the AI routes (``approve_market``, ``draft_market*``), ms (default 330000: the review can take
        minutes).
    on_api_key_created:
        Called once with the full key when :meth:`ensure_api_key` (or :meth:`create`) creates an API key, to store it
        (the server never shows it again). The SDK itself only logs a masked hint (``bsk_****abcd``); the key is also
        on ``client.api_key``. An error it raises is logged (masked) and does not fail the call.
    """

    def __init__(
        self,
        chain: Union[str, ChainConfig, None] = None,
        rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        api_key: Optional[str] = None,
        api_domain: Optional[str] = None,
        addresses: Optional[Dict[str, Any]] = None,
        slippage_bps: Optional[Dict[str, int]] = None,
        sync: Optional[Dict[str, Any]] = None,
        receipt_timeout_ms: Optional[float] = None,
        api_timeout_ms: Optional[float] = None,
        api_upload_timeout_ms: Optional[float] = None,
        api_ai_timeout_ms: Optional[float] = None,
        on_api_key_created: Optional[Callable[[str], Any]] = None,
        rpc_timeout_ms: Optional[float] = None,
    ):
        self.chain_config = resolve_chain(chain)
        self.chain_id = self.chain_config.chain_id
        self.addresses: Dict[str, Any] = merge_address_book(chain_address_book(self.chain_config), addresses)
        self.slippage_bps: Dict[str, int] = {**DEFAULT_SLIPPAGE_BPS, **_check_slippage_keys(slippage_bps)}
        for k, v in self.slippage_bps.items():
            validate_slippage_bps(v, f"slippageBps.{k}")
        self.receipt_timeout_ms = check_timeout_ms(
            DEFAULT_RECEIPT_TIMEOUT_MS if receipt_timeout_ms is None else receipt_timeout_ms, "receiptTimeoutMs")
        self.rpc_timeout_ms = check_timeout_ms(
            DEFAULT_RPC_TIMEOUT_MS if rpc_timeout_ms is None else rpc_timeout_ms, "rpcTimeoutMs")
        self.api_timeout_ms = check_timeout_ms(
            DEFAULT_API_TIMEOUT_MS if api_timeout_ms is None else api_timeout_ms, "apiTimeoutMs")
        self.api_upload_timeout_ms = check_timeout_ms(
            DEFAULT_API_UPLOAD_TIMEOUT_MS if api_upload_timeout_ms is None else api_upload_timeout_ms,
            "apiUploadTimeoutMs")
        self.api_ai_timeout_ms = check_timeout_ms(
            DEFAULT_API_AI_TIMEOUT_MS if api_ai_timeout_ms is None else api_ai_timeout_ms, "apiAiTimeoutMs")
        if on_api_key_created is not None and not callable(on_api_key_created):
            raise ValidationError("onApiKeyCreated", "on_api_key_created must be callable.")
        self._on_api_key_created = on_api_key_created
        rpc = rpc_url or self.chain_config.rpc_url
        if not isinstance(rpc, str) or not rpc:
            raise ValidationError("rpcUrl", f"rpc_url must be the RPC's URL (a string), got {_shown(rpc)}." if rpc else
                                  f'Chain "{self.chain_config.key}" names no RPC: pass the rpc_url option (or rpc_url '
                                  "in the chain config).")
        self.rpc_url = rpc
        domain = api_domain or self.chain_config.api_domain
        if not domain:
            raise ValidationError("apiDomain", f'Chain "{self.chain_config.key}" names no API: pass the api_domain option '
                                               "(or api_domain in the chain config).")
        self.api_domain = _check_api_domain(domain)
        self._session_wallets: List[str] = []
        self._no_door_wallet: Optional[str] = None
        self._sync = SyncEngine(lambda: self.api_domain, sync, self._sync_auth,
                                lambda: getattr(self, "web3", None))
        self._api_key: Optional[str] = api_key
        self.web3 = Web3(_rpc_provider(self.rpc_url, self.rpc_timeout_ms))
        self.account = None

        if private_key:
            self.account = Account.from_key(private_key)
            logger.info("BasisClient initialized with private key.")
        else:
            logger.info("BasisClient initialized in stateless mode.")
        self._api_key_wallet: Optional[str] = self.account.address.lower() if api_key and self.account else None

        self.api = BasisAPI(self)

        self.mains = MainsModule(self)
        self.factory = FactoryModule(self)
        self.taxes = TaxesModule(self)
        self.referrals = ReferralsModule(self)
        self.paper = PaperModule(self)
        self.agent = AgentIdentityModule(self)
        self.trading = TradingModule(self)
        self.hop_executor = HopExecutorModule(self)
        self.loans = LoansModule(self)
        self.liquidation = LiquidationModule(self)
        self.leverage_simulator = LeverageSimulatorModule(self)
        self.main_lens = MainLensModule(self)
        self.vault = VaultModule(self)
        self.vesting = VestingModule(self)
        self.market_reader = MarketReaderModule(self)
        self.resolver = MarketResolverModule(self)
        self.prediction_markets = PredictionMarketsModule(self)
        self.private_markets = PrivateMarketsModule(self)
        self.order_book = OrderBookModule(self)
        self.updown = UpDownModule(self)
        self.agent_registration: Optional[Dict[str, Any]] = None

    @property
    def _wallet_not_linked(self) -> bool:
        """True when the current signer's last sign-in answered ``wallet_not_linked``: it cannot sign in yet, so its
        agent record goes through the signed first sync whatever credentials the client holds. False for any other
        signer (after a switch the new signer is tried on its own). TS: ``_walletNotLinked``."""
        account = getattr(self, "account", None)
        return account is not None and getattr(self, "_no_door_wallet", None) == account.address.lower()

    @property
    def api_key(self) -> Optional[str]:
        """API key for v1 data endpoints. Setting it (e.g. ``client.api_key = "bsk_..."``) records the current signer as
        the key's wallet (:attr:`api_key_wallet`, None without a signer), as the ``api_key`` option does."""
        return self._api_key

    @api_key.setter
    def api_key(self, key: Optional[str]) -> None:
        if key is not None and not isinstance(key, str):
            raise ValidationError("apiKey", f"api_key must be a string (bsk_...) or None, got {_shown(key)}.")
        self._api_key = key or None
        account = getattr(self, "account", None)
        self._api_key_wallet = account.address.lower() if key and account else None

    def set_api_key(self, key: Optional[str]) -> None:
        """Sets the API key of a running client (e.g. one stored for the signer after a wallet switch), as the
        ``api_key`` option does: the current signer is recorded as the key's wallet (:attr:`api_key_wallet`; None
        without a signer, then :meth:`ensure_api_key` confirms it before it counts as a signer's). None or "" removes the
        key. Anything but a str: ``ValidationError("apiKey")``. Same as ``client.api_key = key``. TS: ``setApiKey``."""
        self.api_key = key

    @property
    def api_key_wallet(self) -> Optional[str]:
        """The lower-case wallet :attr:`api_key` belongs to: the signer's when the key came with one (the ``api_key``
        option, or set on the client), or the signer :meth:`ensure_api_key` made it for; None when not known (a key
        passed without a signer, until :meth:`ensure_api_key` finds its hint among a signer's keys). API requests send
        the key for that wallet's signer only (or without a signer), so after a signer switch the old wallet's key never
        acts for the new one. TS: ``apiKeyWallet``."""
        return self._api_key_wallet

    @property
    def hosts(self) -> Dict[str, str]:
        """Prediction-market hosts: ``public`` (resolved through ``client.resolver``) and ``private`` (resolved by the
        creator)."""
        return {"public": self.addresses["tranche"], "private": self.addresses["tranchePrivate"]}


    def _send_transaction(self, function_call, value: int = 0, contract: Optional[str] = None) -> dict:
        """Internal send step of the write pipeline (``BaseModule._send``), not public: it does no simulation and posts
        no backend sync for a confirmed tx. ``_send`` simulates first and syncs the mined hash once it has classified a
        replacement (TS twin: ``BasisClient._writeContract``, the injected-wallet hand-off).

        Estimate, build, sign and send a transaction, then wait for its receipt (``receipt_timeout_ms``).

        Before sending: a gas-estimate revert is decoded into BasisContractError (``contract`` names the SDK ABI); an
        RPC / transport failure (estimate, nonce, fees) or insufficient funds is BasisRpcError. Nothing was sent then.
        The fees follow viem's rule, as the TS SDK sends them (``estimate_fees_per_gas``: maxFeePerGas = baseFee x 1.2 +
        the node's priority fee; gasPrice x 1.2 on a chain without a base fee).
        The hash is known before the broadcast (the signed tx's hash): a broadcast whose answer is lost or unclear
        (timeout, network error, HTTP 5xx / 429, "already known") is taken as sent and waited for; a definitive refusal
        raises BasisRpcError (``rejected`` / ``insufficient_funds``) or, for a node that executes on send, the decoded
        BasisContractError. "nonce too low" is not taken as sent: after a short check of the chain the write raises
        BasisRpcError kind ``nonce_used`` (nothing was sent; sending again is fine). When the receipt wait fails after
        sending (it rides out RPC blips until the timeout), the hash's sync is queued (with the sender, nonce and block,
        for the flush's chain check) and TxNotConfirmedError is raised (carries ``tx_hash``: do not resend). The wait
        also notices another tx mined with the same nonce instead of this one: the result then carries ``replacement``
        {reason ('repriced' | 'cancelled' | 'replaced' | 'nonce_used'), hash, transaction} and ``receipt`` is that tx's
        (``_send`` decides what that means: with a private key, a call mined under another hash is ``nonce_used`` too,
        never a ``replaced_hash``).

        Returns { 'hash': '0x...' (the sent hash), 'receipt': {...}, 'replacement': None | {...} }
        """
        if not self.account:
            raise WalletRequiredError()
        fn_name = getattr(function_call, "fn_name", None) or "call"
        abi = getattr(function_call, "contract_abi", None)

        def pre_send(e: Exception) -> Exception:
            kind = rpc_error_kind(e)
            return classify_error(e, contract=contract, function_name=fn_name, abi=abi) if kind else e

        try:
            gas = function_call.estimate_gas({'from': self.account.address, 'value': value})
        except Exception as e:  # noqa: BLE001
            raise classify_error(e, contract=contract, function_name=fn_name, abi=abi) from e
        try:
            tx = function_call.build_transaction({
                'from': self.account.address,
                'nonce': next_nonce(self.web3, self.account.address, self.chain_id,
                                    block_time_s=chain_block_time_ms(self.chain_config) / 1000),
                'value': value,
                'chainId': self.chain_id,
                'gas': gas_limit_for(gas),
                **estimate_fees_per_gas(self.web3),
            })
            signed_tx = self.web3.eth.account.sign_transaction(tx, private_key=self.account.key)
            start_block = self.web3.eth.block_number
        except Exception as e:  # noqa: BLE001
            raise pre_send(e) from e
        h = '0x' + bytes(signed_tx.hash).hex()
        try:
            send_error = broadcast_raw(self.web3, signed_tx.raw_transaction, h)
        except Exception as e:  # noqa: BLE001
            raise send_rejection(e, contract=contract, function_name=fn_name, abi=abi) from e
        try:
            receipt, replacement = wait_for_receipt(self.web3, h, self.account.address, tx, start_block,
                                                    self.receipt_timeout_ms / 1000)
        except Exception as e:  # noqa: BLE001
            err = TxNotConfirmedError(h, contract or "contract", fn_name, e)
            lost = f" (the broadcast's answer was lost: {redact_urls(send_error)})" if send_error is not None else ""
            self.queue_sync(h, f"receipt not read after sending{lost}: {redact_urls(e)}",
                            meta={"from": self.account.address, "to": _call_target(function_call),
                                  "nonce": int(tx["nonce"]), "block": int(start_block)})
            raise err from e
        return {'hash': h, 'receipt': receipt, 'replacement': replacement}

    def queue_sync(self, tx_hash: str, reason: Optional[str] = "not confirmed yet",
                   meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Queues the tx sync of ``tx_hash`` without posting it now (``pending_syncs`` + ``queue_file``, code
        ``not_confirmed``), for a tx that was sent but not seen confirmed: the write pipeline does this before raising
        ``TxNotConfirmedError``. ``meta``: ``{"from", "to", "nonce", "block"}`` of the sent tx when known (the sender,
        the contract it called, its nonce and the latest block before sending). :meth:`flush_pending_syncs` checks such an entry on the chain
        first: it posts it once mined, keeps it while pending, and drops it (logged) when its nonce went to a cancel or
        rewrites it to the replacement's hash after a speed-up. Never raises; returns the :meth:`sync_tx` shape."""
        if isinstance(tx_hash, str) and not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        return self._sync.queue_tx(tx_hash, "not confirmed yet" if reason is None else reason, meta=meta)

    def queue_agent_sync(self, tx_hash: str, agent: Dict[str, Any],
                         reason: Optional[str] = "not confirmed yet") -> Dict[str, Any]:
        """Queues the ``POST /api/agents`` of ``agent`` (``{"wallet", "agent_id", "name"?, "description"?}``;
        registration or update tx ``tx_hash``) without posting it now (``pending_syncs`` as a ``kind: "agent"`` entry,
        + ``queue_file``, code ``not_confirmed``): agent.update does this before raising ``TxNotConfirmedError``.
        :meth:`flush_pending_syncs` posts it (after the tx's own sync). Never raises; returns the :meth:`sync_tx`
        shape."""
        if isinstance(tx_hash, str) and not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        return self._sync.queue_agent(tx_hash, agent, "not confirmed yet" if reason is None else reason)

    def sync_tx(self, tx_hash: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Post a confirmed transaction to the backend (``POST /api/v1/sync {txHash, source: "sdk"}``). Every on-chain
        write awaits this, order-book writes included (the route decodes their events).

        Retries network errors, timeouts, 5xx, 408, 425, 422 pending / sync_failed and 429 (waiting until
        ``X-RateLimit-Reset``) with exponential backoff (``sync`` option), so a write takes longer when the API is down.
        A sync that still fails, or that the API refuses as a chain mismatch (503) or with another 4xx, is logged and
        queued in ``pending_syncs`` (+ ``queue_file``); retry it with ``flush_pending_syncs()``. 400 and 422 reverted
        are logged, not queued. ``meta``: the tx's ``{"from", "to"}`` when known (its sender and the contract it
        called), recorded on a queued line: the write pipeline passes both (``agent.register_and_sync`` reads on chain
        only a queued line of its wallet to the identity registry, or one that names neither).

        Never raises: the tx is already confirmed, and an error would invite the caller to retry the write (double
        spend). Returns ``{"ok", "status", "code", "http_status", "attempts", "queued", "last_error"}``.
        """
        if isinstance(tx_hash, str) and not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        try:
            return self._sync.sync(tx_hash, meta)
        except Exception as e:  # noqa: BLE001
            logging.getLogger("basis").warning("[basis-sdk] post-tx sync failed for %s: %s", tx_hash, redact_urls(e))
            return {"ok": False, "status": None, "code": None, "http_status": 0, "attempts": 0, "queued": False,
                    "last_error": redact_urls(e)}

    def _sync_auth(self) -> Dict[str, Any]:
        """The current credentials for agent registrations: the API key with the wallet it belongs to (an entry of
        another wallet goes without it), and the SIWE session cookie as a ``Cookie`` header only when it holds the
        signer's wallet (:attr:`has_session`; other cookies never go along)."""
        api = getattr(self, "api", None)
        cookie = api._session_cookie_pair() if api is not None and api._has_session() else None  # noqa: SLF001
        return {"api_key": self.api_key, "api_key_wallet": self._api_key_wallet, "session_cookie": cookie,
                "session_wallets": list(self._session_wallets),
                "sign_agent_sync": lambda wallet, agent_id: self.agent._sign_sync_body(wallet, agent_id)}  # noqa: SLF001

    def _sync_agent_signed(self, tx_hash: str, agent: Dict[str, Any]) -> Dict[str, Any]:
        """The signed first sync of ``agent["wallet"]``'s agent (no sign-in: a message the signer signs, again for every
        attempt), after registration tx ``tx_hash``: retried like :meth:`sync_tx`; a failure worth retrying later is
        queued in ``pending_syncs`` (``kind: "agentSigned"``, the wallet and agent id only) for
        :meth:`flush_pending_syncs`, which signs it again (it needs this signer). 400, 401, 403 and 409 are final. Never
        raises. TS: ``_syncAgentSigned``."""
        try:
            return self._sync.sync_agent_signed(tx_hash, agent)
        except Exception as e:  # noqa: BLE001
            logging.getLogger("basis").warning("[basis-sdk] signed agent sync failed for %s: %s", tx_hash,
                                               redact_urls(e))
            return {"ok": False, "status": None, "code": None, "http_status": 0, "attempts": 0, "queued": False,
                    "last_error": redact_urls(e)}

    def _queue_agent_signed(self, tx_hash: str, agent: Dict[str, Any],
                            reason: Optional[str] = "not confirmed yet") -> Dict[str, Any]:
        """Queues the signed first sync of ``agent`` (tx ``tx_hash``) without posting it now. Never raises."""
        if isinstance(tx_hash, str) and not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        return self._sync.queue_agent_signed(tx_hash, agent, "not confirmed yet" if reason is None else reason)

    def sync_agent_registration(self, tx_hash: str, agent: Dict[str, Any]) -> Dict[str, Any]:
        """Records an ERC-8004 registration made by tx ``tx_hash`` (``POST /api/agents`` with the client's API key or
        session, after the tx's own sync: the registry is not a BASIS contract, so ``/api/v1/sync`` stores the tx as
        ``ignored``). ``agent``: ``{"wallet", "agent_id", "name"?, "description"?}``.

        Retried like :meth:`sync_tx`; a failure worth retrying later (no credentials, 401, 429, 503 not_deployed,
        retries spent) is logged and queued in ``pending_syncs`` (``kind: "agent"``, + ``queue_file``) for
        :meth:`flush_pending_syncs`; 400, 409 and 403 ``wallet_in_account`` / ``forbidden`` / ``not_branded`` are
        logged, not queued. A 403 ``wallet_not_linked`` (a key or session of a wallet that cannot sign in yet) sends the
        signed first sync instead. ``status`` is ``registered`` when delivered. Never raises; returns the
        :meth:`sync_tx` shape.
        """
        try:
            return self._sync.sync_agent(tx_hash, agent)
        except Exception as e:  # noqa: BLE001
            logging.getLogger("basis").warning("[basis-sdk] agent registration sync failed for %s: %s", tx_hash,
                                               redact_urls(e))
            return {"ok": False, "status": None, "code": None, "http_status": 0, "attempts": 0, "queued": False,
                    "last_error": redact_urls(e)}

    @property
    def pending_syncs(self) -> List[Dict[str, Any]]:
        """Syncs that failed after every retry (live list, oldest first), each
        ``{hash, attempts, last_error, code, at}``; agent registrations add ``kind: "agent"`` and ``agent``.
        Retry them with :meth:`flush_pending_syncs`."""
        return self._sync.pending

    def _queued_tx_entries(self) -> List[Dict[str, Any]]:
        """The queued tx syncs (no ``kind``) of this client: :attr:`pending_syncs` plus the ``queue_file`` lines an
        earlier run left (read without changing the file; a file that cannot be read adds nothing), one per hash.
        Never raises. ``agent.register_and_sync`` looks here for a registration whose sync is still queued (any code).
        TS: ``_queuedTxEntries``."""
        entries = list(self._sync.pending)
        qf = self._sync.options.get("queue_file")
        if qf:
            try:
                entries.extend(read_sync_queue_file(qf))
            except Exception:  # noqa: BLE001
                pass
        seen = set()
        out = []
        for e in entries:
            h = e.get("hash") if isinstance(e, dict) else None
            if not isinstance(h, str) or e.get("kind") is not None or h.lower() in seen:
                continue
            seen.add(h.lower())
            out.append(e)
        return out

    def flush_pending_syncs(self, options: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
        """Retries every queued sync (memory, plus ``queue_file`` when set) with the client's retry policy (override
        with ``options``) and current credentials (agent registrations); what still fails stays queued and the file
        is rewritten with it.
        Returns ``{"synced", "failed"}``."""
        return self._sync.flush(options)


    def get_claimables(self, user: Optional[str] = None, tokens: Optional[Sequence[str]] = None,
                       markets: Optional[Sequence[Any]] = None,
                       up_down_rounds: Optional[Sequence[Any]] = None,
                       loan_hubs: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """What ``user`` (default: the signer) can claim now, read from the chain: holder rewards per token, market
        redemptions, Up/Down payouts, vesting claimables, vault rewards (the part ``claim`` pays) and liquidation
        leftovers (loans, leverage positions, vesting loans awaiting settlement, each with its claim deadline),
        plus the sum per asset.

        The market / round lists come from the API: pass them in, or leave one out (None) to fetch it (API key; the
        signer reading itself with only a session uses the /me routes); pass ``[]`` to skip that section. ``tokens``
        default: with an API key the API's tokens of the wallet (portfolio holdings, every token it traded, its loan
        tokens); without one (session-only or no auth) every factory token (scanned on chain in batches of 200);
        either way plus the tokens of its leverage positions, loans and beneficiary vestings (chain; these earn
        rewards for the wallet too); a list passed replaces all of these. ``markets``: addresses or
        ``{"market", "host"}`` dicts (any other entry:
        ValidationError("markets") naming its index, before any read; default: the wallet's resolved and invalidated
        markets, every page). ``up_down_rounds``: ``{"asset", "tf", "round_id"}``
        dicts or ``(asset, tf, round_id)`` tuples (default: ``claimableBets`` of the wallet's Up/Down record).
        ``loan_hubs``: older loan-hub addresses to read on top of the address book's
        (``loans.at_hub(hub)``): their loans count for the liquidation leftovers and the holder-reward token list
        (default: none).

        Returns {user, block_number (latest block when the read started; sections are read right after, not pinned),
        block_time, holder_rewards: [{token, amount (Amount, pair asset)}] (``rewardsEnabled``, amount > 0),
        skipped_tokens: [{token, quote_token (the token it trades against), reason ('unknown main')}] (tokens with
        rewards that trade against no known main: not valued, the others still read), markets:
        redeemables with token_payout > 0, skipped_markets: [{market, host, reason ('unknown host' | 'unknown
        market')}], up_down (``updown.get_claimable`` over the rounds), vesting: [{id, token, claimable (Amount)}]
        (beneficiary vestings with claimable > 0), vault: [{main, vault, earned, claimable}] (claimable = min(earned,
        available_stasis) > 0), liquidations: [{kind ('hubLoan' | 'leverage' | 'vestingLoan'), main, hub (the loan
        hub the loan is on; None for leverage), id, loan_id, token, amount (Amount, collateral token), liquidated_at,
        claim_deadline (liquidated_at + 365 days), claim_expired}],
        totals: [{asset, amount}] (sum per asset, first-seen order, non-zero only; vesting-loan leftovers not counted:
        they return to their schedule), skipped: sections whose contract is not deployed, failed: [{section, source,
        message, error}] (a section that could not be read, source None, or one of its sources: holder-reward token
        lists 'portfolio' (API key), 'chain' (no API key), 'leverage', 'loans', 'vesting', or liquidation reads
        'loans', 'leverage', 'vesting'; the other sections are still returned)}. Section names:
        'holder_rewards', 'markets', 'up_down', 'vesting', 'vault', 'liquidations'.
        """
        return _get_claimables(self, user, tokens, markets, up_down_rounds, loan_hubs)


    @classmethod
    def create(
        cls,
        *,
        auth: bool = True,
        contracts_check: str = "off",
        agent: Union[bool, Dict[str, Any], None] = None,
        rotate_api_key: bool = False,
        **kwargs: Any,
    ) -> "BasisClient":
        """Create a fully-initialised :class:`BasisClient`.

        Compared to the plain constructor this additionally:

        1. Checks that the RPC serves the configured chain.
        2. When a *private_key* is given and ``auth`` is true: SIWE login, then provisions an
           API key unless one is given. ``auth=False`` runs contract-only, without the API: ``create`` then makes no
           API writes, so the ``agent`` option is skipped (with a warning); the agent methods
           (``client.agent.register_and_sync()`` and the others) still work when called directly.
           ``rotate_api_key=True`` (opt-in): when the wallet already has an API key (the server never shows it again),
           delete it and create a new one, like a first key; anything still using the old key stops working. Default
           False: ``create`` then raises and asks for the key. A new key is on ``client.api_key`` and goes to the
           ``on_api_key_created`` option; only a masked hint is logged.
        3. Optionally compares the client with ``{api_domain}/contracts.json`` (:meth:`check_contracts`: the chain id,
           the deployment, and the addresses when the file publishes them): ``contracts_check`` "off" (default), "warn"
           or "strict" ("strict" also raises when the check cannot be made, e.g. a 429 or 5xx answer, or the file says
           BASIS is not live there, ``live: false``). It runs right after the RPC check, before the sign-in, the API key
           and any agent tx.
        4. ``agent`` (True or an agent config dict, ``{}`` counts as one; else ``ValidationError("agent")``; the
           config's fields are type-checked up front, as in ``agent.register``, and so are the API's limits:
           ``agentNameTooLong`` above 100 characters, ``agentDescriptionTooLong`` above 500, before any request):
           registers the signer as an ERC-8004 agent (or finds the one it has) and syncs it to the API
           (``agent.register_and_sync``); a failure is logged as a warning, not raised. A registration of the signer
           an earlier run sent and saw mined (its line in the sync queue, its identity the signer's) is finished with
           its tx hash (``register_and_sync(config, tx_hash)``), never minted again. Skipped without a signer and with
           ``auth=False``. One agent, one wallet: the API never records a wallet linked to a person's BASIS account as
           an agent, and on an API that tells such wallets
           apart before a tx (``/api/v1/config`` ``paper: true``: the paper site) ``create`` refuses one before the API
           key or any tx (``ValidationError("wallet_in_account")``). While a transaction of the wallet is still
           pending, a new registration is refused (``ValidationError("agentTxPending")``): it may be an earlier one.

        Every parameter is keyword-only (a positional value raises TypeError: a chain passed positionally never lands in
        ``auth``). Checked before any request: ``auth`` / ``rotate_api_key`` True or False only
        (``ValidationError("auth")`` / ``("rotateApiKey")``), ``contracts_check`` "off", "warn" or "strict"
        (``ValidationError("contractsCheck")``), ``agent`` and its config, plus the constructor's checks; an unknown
        keyword raises TypeError. A parameter set to None is left out (its default), as null in TS.

        The API signs in only a wallet linked to a BASIS account (on the website: X, Google or email, then Account >
        Wallets) or a registered agent; any other wallet gets BasisApiError code ``wallet_not_linked`` (without
        ``agent`` its message adds the SDK's ways in: ``agent=True`` for an agent's wallet, ``auth=False`` to run without
        the API). With ``agent`` such a wallet registers on ERC-8004 and sends the signed first sync first (which
        accepts the BASIS Terms), then signs in (retried for about 30 s while the API still answers
        ``wallet_not_linked``). When that first sync fails after the mint, ``create`` raises
        :class:`AgentOnboardingError` with the agent id, the tx hash and what to do next; running ``create`` again
        never mints a second identity.

        ``**kwargs`` are forwarded to :class:`BasisClient.__init__`.
        """
        client = cls(**kwargs)
        if agent is not None and not isinstance(agent, (bool, dict)):
            raise ValidationError("agent", f"agent must be True, False or an agent config dict, got {type(agent).__name__}.")
        auth = to_bool(auth, "auth", True)
        rotate_api_key = to_bool(rotate_api_key, "rotateApiKey", False)
        if contracts_check is None:
            contracts_check = "off"
        if not isinstance(contracts_check, str) or contracts_check not in _CONTRACTS_CHECK_MODES:
            raise ValidationError("contractsCheck",
                                  f"contractsCheck must be 'off', 'warn' or 'strict', got {_shown(contracts_check)}.")
        wants_agent = agent is True or isinstance(agent, dict)
        agent_config = agent if isinstance(agent, dict) else None
        if agent_config is not None:
            client.agent._check_config(agent_config)  # noqa: SLF001

        client.validate_rpc()
        if contracts_check != "off":
            client.check_contracts(contracts_check)

        if wants_agent and client.account and not auth:
            logger.warning("[basis-sdk] create(): the agent option is skipped with auth=False (contract-only: no API "
                           "writes). Call client.agent.register_and_sync(config) yourself to register and sync the agent.")

        agent_done = False
        if client.account and auth:
            try:
                client.authenticate()
            except BasisApiError as e:
                if e.code != "wallet_not_linked":
                    raise
                if not wants_agent:
                    raise _no_door_hint(e) from e
                client._onboard_agent(agent_config)
                agent_done = True
            if wants_agent and not agent_done:
                try:
                    client.agent._refuse_if_person_wallet("create(agent=...)")  # noqa: SLF001
                except ValidationError:
                    raise
                except Exception:  # noqa: BLE001
                    pass
            if not kwargs.get("api_key"):
                client.ensure_api_key(rotate=rotate_api_key)

        if wants_agent and client.account and not agent_done and auth:
            client.agent._finish_queued = True  # noqa: SLF001
            try:
                client.agent_registration = client.agent.register_and_sync(agent_config)
            except Exception as e:  # noqa: BLE001
                logger.warning("Agent registration warning: %s", redact_urls(e))
            finally:
                client.agent._finish_queued = False  # noqa: SLF001

        return client

    _AGENT_SIGN_IN_RETRY = {"window_ms": 30_000, "first_delay_ms": 1_000}

    def _onboard_agent(self, config: Optional[Dict[str, Any]]) -> None:
        """``create(agent=...)`` for a wallet the API does not let in yet (``wallet_not_linked``): finishes a first sync
        an earlier run queued (``queue_file``), registers the agent (or finds it) with the signed first sync, then signs
        in. A failed first sync after the mint raises :class:`AgentOnboardingError` with the agent id, the tx hash and
        the next step."""
        from .modules.agent_identity import AgentOnboardingError
        qf = self._sync.options.get("queue_file")
        earlier: List[Dict[str, Any]] = []
        if self.pending_syncs or qf:
            earlier = self._queued_tx_entries()
            try:
                self.flush_pending_syncs()
            except Exception:  # noqa: BLE001
                pass
        self.agent._earlier_entries = earlier  # noqa: SLF001
        self.agent._finish_queued = True  # noqa: SLF001
        try:
            r = self.agent.register_and_sync(config)
        finally:
            self.agent._earlier_entries = []  # noqa: SLF001
            self.agent._finish_queued = False  # noqa: SLF001
        self.agent_registration = r
        reg = r.get("registration")
        aid = r.get("agent_id")
        if reg and not reg.get("ok"):
            from_client = "from a client made with auth=False"
            if aid is None:
                nxt = (f"Read its agent id with agent.get_agent_id_from_tx('{r.get('hash')}'), then call "
                       f"agent.sync_signed(agent_id) {from_client} and create() again.")
            elif reg.get("queued") and qf:
                nxt = (f"It is queued in client.pending_syncs and {qf}: run create() again (it finishes the queued sync "
                       f"with this signer), or call agent.sync_signed({aid}) first.")
            elif reg.get("queued"):
                nxt = (f"It was queued in memory only (no sync queue_file), and create() returns no client: call "
                       f"agent.sync_signed({aid}) {from_client}, then create() again.")
            else:
                nxt = f"Fix that, then call agent.sync_signed({aid}) {from_client} and create() again."
            raise AgentOnboardingError(
                f"[basis-sdk] Registered agent {aid if aid is not None else '(id unknown)'} on ERC-8004 (tx {r.get('hash')}), "
                f"but BASIS did not take its first sync: {reg.get('last_error')} {nxt} Do not call register() again: it "
                "would mint a second identity.", aid, r.get("hash"), reg)
        self._sign_in_after_agent_sync(aid, r.get("hash"))
        try:
            self.flush_pending_syncs()
        except Exception:  # noqa: BLE001
            pass

    def _sign_in_after_agent_sync(self, agent_id: Optional[int], tx_hash: Optional[str]) -> None:
        """Signs in right after an agent's first sync. The API may still answer ``wallet_not_linked`` for a few seconds
        (each server process remembers a refusal briefly), so that answer is retried with a doubling wait for about
        30 s (:attr:`_AGENT_SIGN_IN_RETRY`); then, or on any other failure, :class:`AgentOnboardingError` says the agent
        is synced and ``create`` can run again."""
        from .modules.agent_identity import AgentOnboardingError
        window = float(self._AGENT_SIGN_IN_RETRY["window_ms"])
        delay = float(self._AGENT_SIGN_IN_RETRY["first_delay_ms"])
        start = time.monotonic()
        while True:
            try:
                self.authenticate()
                return
            except Exception as e:  # noqa: BLE001
                left = window - (time.monotonic() - start) * 1000
                no_door = isinstance(e, BasisApiError) and e.code == "wallet_not_linked"
                if not no_door or left <= 0:
                    what = "still answers wallet_not_linked" if no_door else "failed"
                    raise AgentOnboardingError(
                        f"[basis-sdk] Agent {agent_id if agent_id is not None else '(id unknown)'} is registered on "
                        f"ERC-8004 and synced with BASIS, but the sign-in {what} ({redact_urls(e)}). Run create() again "
                        "in a minute: it signs in and does not register again.", agent_id, tx_hash, None) from e
                time.sleep(min(delay, left) / 1000)
                delay *= 2


    def validate_rpc(self) -> None:
        """Raise when the RPC is unreachable or serves another chain than the configured one."""
        try:
            rpc_chain_id = self.web3.eth.chain_id
        except Exception as e:  # noqa: BLE001
            raise BasisError(f"[basis-sdk] RPC endpoint unreachable: {redact_urls(e)}") from e
        if rpc_chain_id != self.chain_id:
            raise BasisError(
                f"[basis-sdk] RPC returned chainId {rpc_chain_id}, expected {self.chain_id} "
                f"({self.chain_config.key}). Pass a matching rpc_url or chain."
            )

    def check_contracts(self, mode: Optional[str] = "warn") -> List[str]:
        """Compare the client with ``{api_domain}/contracts.json``: the chain id, and the deployment (the file's
        ``network``, ``robinhood`` or ``robinhood-paper``, or an older file's ``paper`` flag, against the preset: real and
        paper share chain 4663, so only these tell them apart). When the file publishes addresses, also every
        single-contract key, the vault and pair-asset keys of the default main, and ``mains``: keys the SDK does not
        know are ignored, a zero on both sides means "not deployed" and matches. The sites publish no addresses today
        (the SDK carries its own book): a warning then says the addresses were not compared. A ``mains`` entry that is
        not an object is skipped. A check that cannot be made (the file unreachable or past 5 s, a non-2xx answer such
        as 429 or 5xx, a body that is not a JSON object, an object that does not name the chain and deployment it
        describes: a numeric ``chainId``, and ``network`` (a non-empty str) or ``paper`` (True / False)) or a file
        that says BASIS is not live there (``live: false``,
        the real site before its launch): "strict" raises BasisError saying why (nothing was confirmed), "warn" logs it
        and goes on. Returns the mismatches; ``mode`` "strict" raises BasisError on any, "warn" logs them (None:
        "warn"). Any other ``mode`` raises ``ValidationError("contractsCheck")`` first.
        """
        if mode is None:
            mode = "warn"
        if not isinstance(mode, str) or mode not in ("warn", "strict"):
            raise ValidationError("contractsCheck", f"checkContracts mode must be 'warn' or 'strict', got {_shown(mode)}.")
        file = f"{self.api_domain}/contracts.json"

        def unchecked(why: str) -> None:
            msg = f"[basis-sdk] contractsCheck: {file} {why}, so the client was not compared with the deployment."
            if mode == "strict":
                raise BasisError(f"{msg} Retry later, or use contracts_check='warn' (or 'off') to go on without the check.")
            logger.warning(msg)

        try:
            res = requests.get(file, timeout=5)
        except Exception as e:  # noqa: BLE001
            unchecked(f"could not be read ({redact_urls(e)})")
            return []
        if not res.ok:
            unchecked(f"answered HTTP {res.status_code}")
            return []
        try:
            remote = res.json()
        except ValueError:
            unchecked("is not JSON")
            return []
        if not isinstance(remote, dict):
            unchecked("is not a JSON object")
            return []
        mismatches = self._compare_contracts(remote, mode)
        if not _names_deployment(remote):
            unchecked("does not say which chain and deployment it describes (it needs a numeric chainId, and network or "
                      "paper)")
        if remote.get("live") is False:
            note = remote.get("note")
            shown_note = f' ("{note}")' if isinstance(note, str) and note else ""
            unchecked(f"says BASIS is not live there{shown_note} (live: false)")
        return mismatches

    def _compare_contracts(self, remote: Dict[str, Any], mode: str) -> List[str]:
        """:meth:`check_contracts`' comparison of a readable ``/contracts.json`` object ``remote`` with the client."""
        mismatches: List[str] = []
        remote_chain = remote.get("chainId")
        if isinstance(remote_chain, (int, float)) and not isinstance(remote_chain, bool) and remote_chain != self.chain_id:
            mismatches.append(f"chainId: SDK {self.chain_id}, contracts.json {remote_chain}")
        flag = contracts_flag_mismatch(remote, self.chain_config)
        if flag:
            mismatches.append(flag)
        published = 0
        for key in _CHECKED_KEYS:
            r = remote.get(key)
            mine = self.addresses[key]
            if isinstance(r, str):
                published += 1
            if isinstance(r, str) and r.lower() != mine.lower():
                mismatches.append(f"{key}: SDK {mine}, contracts.json {r}")
        book = self.addresses
        default = next((m for m in book["mains"] if m.get("key") == book["defaultMain"]), {})
        for rk, label, mine in (("staking", f"mains[{book['defaultMain']}].vault", default.get("vault")),
                                ("usdb", f"mains[{book['defaultMain']}].pairAsset", default.get("pairAsset"))):
            r = remote.get(rk)
            if isinstance(r, str):
                published += 1
            if isinstance(r, str) and not is_zero_address(mine) and mine.lower() != r.lower():
                mismatches.append(f"{rk}: SDK {label} {mine}, contracts.json {r}")
        remote_mains = [rm for rm in remote["mains"] if isinstance(rm, dict)] if isinstance(remote.get("mains"), list) else []
        if remote_mains:
            published += 1
        if not published:
            logger.warning("[basis-sdk] %s/contracts.json publishes no contract addresses (the SDK carries its own book): "
                           "contracts_check compared the chain id and the deployment only, not the addresses.",
                           self.api_domain)
        for rm in remote_mains:
            mine = next((m for m in self.addresses["mains"] if m.get("key") == rm.get("key")), None)
            for field in ("main", "pairAsset", "vault"):
                r = rm.get(field)
                m = mine.get(field) if mine else None
                if isinstance(r, str) and not (is_zero_address(r) and not m) and (m or "").lower() != r.lower():
                    mismatches.append(f"mains[{rm.get('key')}].{field}: SDK {m or '(missing)'}, contracts.json {r}")

        if mismatches:
            msg = (f"[basis-sdk] Mismatch with {self.api_domain}/contracts.json ({len(mismatches)}):\n  "
                   + "\n  ".join(mismatches))
            if mode == "strict":
                raise BasisError(msg)
            logger.warning(msg)
        return mismatches


    def authenticate(self) -> VerifyResult:
        """Signs in with SIWE: fetches a nonce (``api.get_nonce``), signs the message with the signer and verifies it
        (``api.verify``), which adds the signer's wallet to the session. The ``requests.Session`` inside :attr:`api`
        keeps the session cookie (every wallet of the session) for later requests, sent for the signer's wallet
        (:attr:`session_wallets`; after a signer switch, call this again). Called by :meth:`create`; needs a signer
        (WalletRequiredError). A non-2xx answer raises BasisApiError. Returns the verify answer (with
        ``sessionCookie``). The SIWE domain is the API's host as a browser's ``URL.host`` gives it (lower case, no
        default port, no user info), as the TS SDK sends it. A wallet the API does not let in yet raises BasisApiError
        code ``wallet_not_linked``; until a sign-in of that wallet works, the client then records that wallet's agent
        with the signed first sync (``agent.sync_signed``), whatever credentials it holds (another signer switched in is
        not taken for it). A nonce answer that is not a non-empty string of letters and digits (EIP-4361's rule) raises
        ``ValidationError("siweNonce")`` before anything is signed.
        """
        if not self.account:
            raise WalletRequiredError()

        address = self.account.address

        nonce = _siwe_nonce_of(self.api.get_nonce(address))

        domain = siwe_domain(self.api_domain)
        issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        message = (
            f"{domain} wants you to sign in with your Ethereum account:\n"
            f"{address}\n"
            f"\n"
            f"Sign in to Basis API.\n"
            f"\n"
            f"URI: {self.api_domain}\n"
            f"Version: 1\n"
            f"Chain ID: {self.chain_id}\n"
            f"Nonce: {nonce}\n"
            f"Issued At: {issued_at}"
        )

        signable = encode_defunct(text=message)
        signed = self.account.sign_message(signable)
        signature = "0x" + signed.signature.hex().removeprefix("0x")

        try:
            result = self.api.verify(message, signature)
        except BasisApiError as e:
            if e.code == "wallet_not_linked":
                self._no_door_wallet = address.lower()
            raise
        if self._no_door_wallet == address.lower():
            self._no_door_wallet = None
        logger.info("SIWE authentication successful for %s.", address)
        return result


    def ensure_api_key(self, rotate: Optional[bool] = False) -> str:
        """Ensure an API key is available for the signer.

        * A key in hand (the ``api_key`` option, or made by an earlier call) is returned when it belongs to the signer
          (:attr:`api_key_wallet`) or there is no signer. A key whose wallet is not known (passed without a signer, a
          signer set later) becomes the signer's only when the signer's keys (listed with its session) show its hint
          (``bsk_****`` + its last 4 characters) and the key itself names the signer's wallet (``GET /api/v1/me/profile``
          sent with the key alone: a hint matches another wallet's key 1 time in 65,536); a key it names for another
          wallet is recorded as that wallet's. Otherwise, and after a signer switch (the key belongs to the old wallet),
          the signer's key is made or asked for as below (the key in hand stays on the client until a new one replaces
          it, and goes out only for its own wallet). That GET's failure other than 401 / 403 (the key does not work) is
          raised as it is.
        * If the server reports an existing key, the SDK cannot retrieve the plaintext (only a masked hint is
          returned). Without ``rotate`` it raises ``ValidationError("apiKey")`` asking for the key; with ``rotate=True``
          every existing key of the wallet is deleted and a new one created (anything still using the old key stops
          working).
        * If no keys exist yet (or after the rotation), a new one is created. The server returns it **once**: it is
          kept on ``client.api_key``, passed to the ``on_api_key_created`` option when set, and returned. Only a masked
          hint (``bsk_****abcd``) is logged: store the key yourself for future runs.

        Needs a SIWE session holding the signer's wallet (:meth:`authenticate`): ``AuthRequiredError("session")`` up
        front without one. ``rotate``: True or False, None: False (``ValidationError("rotate")`` otherwise). Returns the
        API key string.
        """
        rotate = to_bool(rotate, "rotate", False)
        signer = self.account.address.lower() if self.account else None
        if self._api_key and (signer is None or self._api_key_wallet == signer):
            return self._api_key

        no_session = self.api._session_error("ensure_api_key (GET/POST /api/v1/auth/keys)")  # noqa: SLF001
        if no_session is not None:
            raise no_session

        keys_resp = self.api.list_api_keys()
        keys = keys_resp.get("keys") if isinstance(keys_resp, dict) else None
        keys = keys if isinstance(keys, list) else []

        held = self._api_key
        if held and self._api_key_wallet is None and any(
                isinstance(k, dict) and k.get("keyHint") == mask_api_key(held) for k in keys):
            owner = self._wallet_of_held_key()
            if owner == signer:
                self._api_key_wallet = signer
                return held
            if owner is not None:
                self._api_key_wallet = owner

        if keys:
            if not rotate:
                raise ValidationError(
                    "apiKey",
                    (f"The API key this client holds ({mask_api_key(held)}) is not one of this wallet's keys. "
                     if held else "") +
                    "An API key already exists for this wallet but the full key cannot be "
                    "retrieved (the server only returns a masked hint). Pass your API key "
                    'via the api_key option when creating the client, e.g.: '
                    'BasisClient.create(private_key=..., api_key="bsk_..."), '
                    "or replace it with BasisClient.create(private_key=..., rotate_api_key=True)."
                )
            for k in keys:
                self.api.delete_api_key(k["id"])
                logger.warning("[basis-sdk] Deleted the wallet's API key %s (rotate_api_key).", k.get("keyHint") or k["id"])

        create_resp = self.api.create_api_key(label="basis-sdk-auto")
        self._api_key = create_resp["key"]
        self._api_key_wallet = signer

        new_key = self._api_key
        hint = mask_api_key(new_key)
        if self._on_api_key_created is not None:
            try:
                self._on_api_key_created(new_key)
            except Exception as e:  # noqa: BLE001
                logger.warning("[basis-sdk] on_api_key_created failed for the new API key %s: %s", hint,
                               str(e).replace(new_key, hint))
        logger.warning(
            "[basis-sdk] New API key created (%s). The server shows it only once: read it from client.api_key "
            "(or the on_api_key_created option), store it securely and pass it via the api_key option on future runs.",
            hint,
        )

        return new_key

    def _wallet_of_held_key(self) -> Optional[str]:
        """The lower-case wallet the API key in hand belongs to (``api._key_wallet``: ``GET /api/v1/me/profile`` with the
        key alone), None when the API refuses the key (401 / 403: it does not work, so it is no signer's) or names no
        wallet. Any other failure (no answer, 429, 5xx) is raised: nothing is decided on a guess (a rotation would
        delete keys). TS: ``walletOfHeldKey``."""
        try:
            return self.api._key_wallet()  # noqa: SLF001
        except BasisApiError as e:
            if e.status in (401, 403):
                return None
            raise


    def logout(self, address: Optional[str] = None) -> LogoutResult:
        """Signs ``address`` (default: the signer) out of the session (``api.logout``); with neither, ends the whole
        session. The answer's ``sessionCookie`` (the remaining wallets, reissued) is sent from now on (None: the session
        ended, the cookie jar is cleared) and the wallet leaves :attr:`session_wallets`, so signing the signer out
        makes :attr:`has_session` False even when other wallets stay signed in. The API key is kept: it is not part of
        the session and stays valid (revoke it with ``api.delete_api_key``). Needs a session (AuthRequiredError).
        """
        if address is None and self.account:
            address = self.account.address
        return self.api.logout(address)

    @property
    def session_wallets(self) -> List[str]:
        """Lower-case wallets the SDK confirmed are in its SIWE session: the wallet the verify answer signed in, the
        session's wallets from ``GET /api/auth/me`` (:meth:`refresh_session`, ``api.get_me``), minus wallets signed out
        (:meth:`logout`). Session requests go out only for a signer in this list; otherwise they raise
        AuthRequiredError (sign in again). TS: ``sessionWallets``."""
        return list(self._session_wallets)

    @property
    def has_session(self) -> bool:
        """True when the client can send a SIWE session for its current signer: the jar holds the session cookie
        (``SESSION_COOKIE_NAME``, on a dev server ``DEV_SESSION_COOKIE_NAME``; no other cookie counts) and the signer's
        wallet is one of :attr:`session_wallets`.
        False without a signer, and after a signer switch until :meth:`authenticate` (or :meth:`refresh_session` finds
        the new signer in the session). TS: ``hasSession``."""
        return self.api._has_session()  # noqa: SLF001

    def refresh_session(self) -> bool:
        """Reads the session state from the API (``GET /api/auth/me``, with the signer's address when there is a
        signer): True when the signer's wallet (without a signer: any wallet) is signed in. The answer's wallets become
        :attr:`session_wallets`, so :attr:`has_session` follows it, e.g. after a signer switch when the new signer is
        already in the session. The cookie is unchanged. A non-2xx answer raises BasisApiError. TS: ``refreshSession``."""
        if self.account:
            return self.api.get_me(self.account.address).get("isLoggedIn") is True
        return len(self.api.get_me().get("addresses") or []) > 0
