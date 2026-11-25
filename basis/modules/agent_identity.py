import json
import base64
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import unquote_to_bytes, urlparse
from eth_account.messages import encode_defunct
from typing import Optional, Dict, Any, List, Tuple, Union
from web3 import Web3
from ..api.content import js_len as _js_len
from ..api.core import BasisApiError
from ..config.addresses import is_zero_address
from ..core.base import BaseModule
from ..core.errors import BasisError, TxNotConfirmedError, ValidationError, WalletRequiredError
from ..core.events import decode_events
from ..core.pinned import PIN_POLL_S, PIN_WAIT_S, retry_pinned_at
from ..core.sync import SignerUnavailableError
from ..core.opts import check_keys
from ..core.units import check_text, to_address, to_uint

logger = logging.getLogger(__name__)

IDENTITY_ABI = [
    {"inputs":[{"name":"agentURI","type":"string"}],"name":"register","outputs":[{"name":"agentId","type":"uint256"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"register","outputs":[{"name":"agentId","type":"uint256"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"tokenId","type":"uint256"}],"name":"ownerOf","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"agentId","type":"uint256"}],"name":"getAgentWallet","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"agentId","type":"uint256"},{"name":"metadataKey","type":"string"}],"name":"getMetadata","outputs":[{"name":"","type":"bytes"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"agentId","type":"uint256"},{"name":"metadataKey","type":"string"},{"name":"metadataValue","type":"bytes"}],"name":"setMetadata","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"agentId","type":"uint256"},{"name":"newURI","type":"string"}],"name":"setAgentURI","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"spender","type":"address"},{"name":"agentId","type":"uint256"}],"name":"isAuthorizedOrOwner","outputs":[{"name":"","type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"agentId","type":"uint256"}],"name":"tokenURI","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
    {"anonymous":False,"inputs":[{"indexed":True,"name":"agentId","type":"uint256"},{"indexed":False,"name":"agentURI","type":"string"},{"indexed":True,"name":"owner","type":"address"}],"name":"Registered","type":"event"},
    {"anonymous":False,"inputs":[{"indexed":True,"name":"agentId","type":"uint256"},{"indexed":False,"name":"newURI","type":"string"},{"indexed":True,"name":"updatedBy","type":"address"}],"name":"URIUpdated","type":"event"},
    {"anonymous":False,"inputs":[{"indexed":True,"name":"agentId","type":"uint256"},{"indexed":True,"name":"indexedMetadataKey","type":"string"},{"indexed":False,"name":"metadataKey","type":"string"},{"indexed":False,"name":"metadataValue","type":"bytes"}],"name":"MetadataSet","type":"event"},
    {"inputs":[{"name":"tokenId","type":"uint256"}],"name":"ERC721NonexistentToken","type":"error"},
]


_MAX_NAME = 100
_MAX_DESCRIPTION = 500
_MAX_AGENT_ID = 2147483647
_APPLIED_ON_PURPOSE = ("no_name", "no_image", "chosen_name_kept", "chosen_photo_kept")
_RESERVED_METADATA_KEY = "agentWallet"
BASIS_METADATA_KEY = "basis"
_URI_PREFIX = "data:application/json;base64,"
_MAX_SAFE_INTEGER = 2 ** 53 - 1
_DATA_URI = re.compile(r"^data:application/json(?:;charset=[A-Za-z0-9_-]+)?(;base64)?,(.*)$", re.IGNORECASE | re.DOTALL)
_REMOTE_URI = re.compile(r"^(https|ipfs)://", re.IGNORECASE)
_CLOCK_SKEW_MS = 60_000
_TX_HASH = re.compile(r"0x[0-9a-fA-F]{64}")
_REGISTER_SELECTORS = frozenset(bytes(Web3.keccak(text=sig))[:4].hex() for sig in (
    "register(string,(string,bytes)[])", "register(string)", "register()"))
_JS_SPACE = "\t\n\v\f\r                  　﻿"


class AgentOnboardingError(BasisError):
    """``create(agent=...)`` registered the agent (or tried to) but could not finish onboarding: its first sync with
    BASIS failed, or the sign-in after it did. The tx is confirmed: never register again (it would mint a second
    identity). ``agent_id`` and ``tx_hash`` say what is on chain (None when unknown); ``registration`` is the failed
    first sync (None when the failure came after it); the message says what to do next. TS: ``AgentOnboardingError``."""

    def __init__(self, message: str, agent_id: Optional[int], tx_hash: Optional[str],
                 registration: Optional[Dict[str, Any]]):
        super().__init__(message)
        self.agent_id = agent_id
        self.tx_hash = tx_hash
        self.registration = registration


def _js_trim(s: str) -> str:
    return s.strip(_JS_SPACE)


def _well_formed(v: Any) -> bool:
    """True when ``v`` is not a str, or one UTF-8 can encode (no lone surrogate: :func:`check_text`)."""
    try:
        check_text(v, "text")
        return True
    except ValidationError:
        return False


_AGENT_CONFIG_KEYS = ("name", "description", "image", "capabilities", "services")
_AGENT_SERVICE_KEYS = ("name", "endpoint", "version")
_AGENT_REGISTRATION_KEYS = ("agent_id", "chain_id", "registry")


def _check_services(services: Any) -> None:
    """``services`` (None: none given) checked: a list of ``{name, endpoint, version?}`` strings, each well-formed text
    (the registration file goes on chain)."""
    if services is None:
        return
    if not isinstance(services, (list, tuple)):
        raise ValidationError("agentServiceInvalid", "services must be a list of { name, endpoint, version? }.")
    for i, s in enumerate(services):
        ok = (isinstance(s, dict) and isinstance(s.get("name"), str) and s.get("name") != ""
              and isinstance(s.get("endpoint"), str) and s.get("endpoint") != ""
              and (s.get("version") is None or isinstance(s.get("version"), str)))
        if not ok:
            raise ValidationError("agentServiceInvalid", f"services[{i}] needs a non-empty string name and endpoint "
                                                         "(version: an optional string).")
        check_keys(s, _AGENT_SERVICE_KEYS, "agentServiceInvalid", f"services[{i}]")
        for k in ("name", "endpoint", "version"):
            if not _well_formed(s.get(k)):
                raise ValidationError("agentServiceInvalid", f"services[{i}].{k} is not valid UTF-8 text.")


def _normalize_services(services: Any, home: str) -> List[Dict[str, str]]:
    if services is None:
        return [{"name": "web", "endpoint": home}]
    _check_services(services)
    out = []
    for s in services:
        entry = {"name": s["name"], "endpoint": s["endpoint"]}
        if s.get("version"):
            entry["version"] = s["version"]
        out.append(entry)
    return out


def _is_register_call(data: Any) -> bool:
    """True when ``data`` (a tx's calldata: bytes or a 0x hex string) calls one of the registry's ``register``
    overloads."""
    if isinstance(data, (bytes, bytearray)):
        head = bytes(data[:4]).hex()
    elif isinstance(data, str):
        head = (data[2:] if data[:2].lower() == "0x" else data)[:8].lower()
    else:
        return False
    return head in _REGISTER_SELECTORS


def _or_not_found(read: Any, *args: Any) -> Optional[Any]:
    """``read(*args)``, or None when the node does not know the tx (web3's TransactionNotFound); else it raises."""
    from web3.exceptions import TransactionNotFound
    try:
        return read(*args)
    except TransactionNotFound:
        return None


def _shown_value(v: Any) -> str:
    """What a value is, for a refusal message (TS: ``shownValue``)."""
    if v is None:
        return "null"
    if isinstance(v, (list, tuple)):
        return "a list"
    if isinstance(v, str):
        return f"'{v}'"
    if isinstance(v, bool):
        return "true" if v else "false"
    return "an object" if isinstance(v, dict) else str(v)


def _check_agent_config(config: Any) -> None:
    """``config`` checked as an agent config before anything is read or sent: None (left out), or a dict whose
    ``name``, ``description`` and ``image`` are str, ``capabilities`` a list of str and ``services`` a list of
    ``{name, endpoint, version?}`` str; each field may be left out (None). Every str must be text UTF-8 can encode (no
    lone surrogate: the registration file goes on chain). Else ValidationError naming the field: ``agent`` (not a
    dict, or a key it does not have), ``name``, ``description``, ``image``, ``capabilities``, ``agentServiceInvalid``
    (services, also an entry with another key). Same rule as TS ``checkAgentConfig`` (undefined and null there are
    "left out")."""
    if config is None:
        return
    if not isinstance(config, dict):
        raise ValidationError("agent", "the agent config must be an object { name?, description?, image?, capabilities?, "
                                       f"services? }}, got {_shown_value(config)}.")
    check_keys(config, _AGENT_CONFIG_KEYS, "agent")
    for field in ("name", "description", "image"):
        v = config.get(field)
        if v is not None and not isinstance(v, str):
            raise ValidationError(field, f"agent {field} must be a string, got {_shown_value(v)}.")
        check_text(v, field)
    caps = config.get("capabilities")
    if caps is not None and (not isinstance(caps, (list, tuple)) or not all(isinstance(x, str) for x in caps)):
        got = "a list with a non-string" if isinstance(caps, (list, tuple)) else _shown_value(caps)
        raise ValidationError("capabilities", f"agent capabilities must be a list of strings, e.g. ['trading'], got {got}.")
    for x in caps or ():
        check_text(x, "capabilities")
    _check_services(config.get("services"))


def _site_origin(site: Any) -> str:
    """The origin of ``site`` (an absolute http(s) URL: lower-case host, no default port, no path), as TS
    ``new URL(site).origin``; else ``ValidationError("site")``."""
    from ..client import siwe_domain
    p = urlparse(site) if isinstance(site, str) else None
    try:
        ok = p is not None and p.scheme.lower() in ("http", "https") and bool(p.hostname)
        if ok:
            p.port  # noqa: B018
    except ValueError:
        ok = False
    if not ok:
        raise ValidationError("site", "site must be the BASIS site the agent belongs to, an absolute http(s) URL (the "
                                      f"client's api_domain), got {json.dumps(site, default=str)}.")
    return f"{p.scheme.lower()}://{siwe_domain(site)}"


def build_agent_metadata_uri(site: str, config: Optional[Dict[str, Any]] = None,
                             registration: Optional[Dict[str, Any]] = None) -> str:
    """The ERC-8004 registration URI of an agent of the BASIS site ``site`` (the client's ``api_domain``, e.g.
    ``https://paper.launchonbasis.com``): ``data:application/json;base64,<compact JSON, UTF-8>`` in the registration-v1
    shape plus the BASIS fields (``website``: the site, ``protocol: "basis"``, ``capabilities``). No wallet goes in a
    link: the default ``services`` entry is ``{"name": "web", "endpoint": <the site>}``. Unset ``description`` /
    ``image`` are left out (never null). ``registration`` (``{"agent_id", "chain_id", "registry"}``, known once the
    agent is minted; None = not given) adds ``registrations: [{agentId, agentRegistry: "eip155:<chainId>:<registry>"}]``.
    Same bytes as TS ``buildAgentMetadataUri`` (falsy fields fall back like JS ``||``). Raises ValidationError (``site``
    for a site that is not an http(s) URL; ``agentId`` for a registration without a valid one; for ``config`` the
    rules of ``agent.register``: ``agent`` not a dict, ``name`` / ``description`` / ``image`` not a str,
    ``capabilities`` not a list of str, ``agentServiceInvalid`` for a malformed ``services`` entry; the field's rule
    for text with a lone surrogate: "<field> is not valid UTF-8 text.")."""
    home = _site_origin(site)
    _check_agent_config(config)
    config = config or {}
    caps = config.get("capabilities")
    metadata: Dict[str, Any] = {
        "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
        "name": config.get("name") or "Basis Agent",
    }
    if config.get("description"):
        metadata["description"] = config["description"]
    if config.get("image"):
        metadata["image"] = config["image"]
    metadata["services"] = _normalize_services(config.get("services"), home)
    metadata["website"] = home
    metadata["protocol"] = "basis"
    metadata["active"] = True
    if registration is not None:
        check_keys(registration, _AGENT_REGISTRATION_KEYS, "registration")
        agent_id = to_uint(registration.get("agent_id") if isinstance(registration, dict) else None, "agentId")
        metadata["registrations"] = [{
            "agentId": agent_id if agent_id <= _MAX_SAFE_INTEGER else str(agent_id),
            "agentRegistry": f"eip155:{registration['chain_id']}:{Web3.to_checksum_address(registration['registry'])}",
        }]
    metadata["capabilities"] = caps if caps is not None else ["trading"]
    metadata["supportedTrust"] = ["reputation"]
    json_str = json.dumps(metadata, separators=(",", ":"), ensure_ascii=False)
    b64 = base64.b64encode(json_str.encode("utf-8")).decode()
    return f"{_URI_PREFIX}{b64}"


_B64_VALUES = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}
_B64_VALUES["-"] = 62
_B64_VALUES["_"] = 63


def _base64_to_utf8(text: str) -> str:
    """UTF-8 text of base64 ``text``, decoded as leniently as the API (Node ``Buffer.from(s, "base64")``) and TS
    ``base64ToUtf8``: the standard and the base64url alphabets, padding optional, decoding stops at the first ``=``,
    any other character (whitespace included) is skipped; invalid UTF-8 becomes U+FFFD."""
    out = bytearray()
    acc = 0
    bits = 0
    for ch in text:
        if ch == "=":
            break
        v = _B64_VALUES.get(ch)
        if v is None:
            continue
        acc = ((acc << 6) | v) & 0xFFFF
        bits += 6
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return out.decode("utf-8", errors="replace")


def _decode_uri_component(s: str) -> str:
    """JS ``decodeURIComponent``: every ``%`` must start a valid escape and the bytes must be UTF-8 (ValueError
    otherwise)."""
    if re.search(r"%(?![0-9A-Fa-f]{2})", s):
        raise ValueError("malformed URI escape")
    try:
        return unquote_to_bytes(s).decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(str(e)) from e


def parse_agent_metadata_uri(uri: str) -> Optional[Dict[str, Any]]:
    """The JSON object of a ``data:application/json`` token URI (base64 or not, an optional charset; as the BASIS API
    reads it), or None for any other URI (https, ipfs, ...) or undecodable content. Same result as TS
    ``parseAgentMetadataUri``."""
    if not isinstance(uri, str):
        return None
    m = _DATA_URI.match(_js_trim(uri))
    if not m:
        return None
    try:
        md = json.loads(_base64_to_utf8(m.group(2)) if m.group(1) else _decode_uri_component(m.group(2)))
    except ValueError:
        return None
    return md if isinstance(md, dict) else None


def _is_basis_host(endpoint: str) -> bool:
    """True when ``endpoint`` is on launchonbasis.com or one of its subdomains (paper.launchonbasis.com)."""
    try:
        p = urlparse(endpoint)
        host = (p.hostname or "").lower() if p.scheme else ""
        p.port  # noqa: B018
    except ValueError:
        return False
    return host == "launchonbasis.com" or host.endswith(".launchonbasis.com")


def is_basis_registration(md: Optional[Dict[str, Any]]) -> bool:
    """True when a registration file carries the BASIS mark, by the API's rule: ``protocol: "basis"`` (any case), or a
    ``services`` entry named BASIS (any case) or pointing at launchonbasis.com (or a subdomain). The registry's
    ``basis`` metadata key is the other mark (``agent.is_basis_agent`` reads both). Same rule as TS
    ``isBasisRegistration``."""
    if not isinstance(md, dict):
        return False
    protocol = _js_trim(md["protocol"])[:32] if isinstance(md.get("protocol"), str) else ""
    if protocol.lower() == "basis":
        return True
    services = md.get("services")
    if not isinstance(services, list):
        return False
    for e in services[:32]:
        if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not isinstance(e.get("endpoint"), str):
            continue
        if _js_trim(e["name"][:64]).lower() == "basis" or _is_basis_host(e["endpoint"][:512]):
            return True
    return False


def is_basis_agent_uri(uri: str) -> bool:
    """True when ``uri`` is a ``data:`` registration file carrying the BASIS mark (:func:`is_basis_registration`).
    False for an https or ipfs file (the SDK does not fetch it) and for anything unreadable."""
    return is_basis_registration(parse_agent_metadata_uri(uri))


AGENT_SYNC_TITLE = "BASIS agent sync"


def build_agent_sync_message(site: str, wallet: str, agent_id: int, terms_version: str,
                             issued: Optional[datetime] = None) -> str:
    """The text an agent wallet signs for its FIRST ``POST /api/agents`` (``api.register_agent_signed``), before it
    can sign in. The API checks every line: ``site`` is the API's host, ``issued`` within 10 minutes of the server's
    clock, ``terms_version`` the current one (``GET /api/v1/config`` ``terms.version``). Signing it accepts the BASIS
    Terms. Same text as TS ``buildAgentSyncMessage``."""
    when = (issued or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z"
    return "\n".join([
        AGENT_SYNC_TITLE,
        f"Site: {site}",
        f"Wallet: {Web3.to_checksum_address(wallet)}",
        f"Agent: {to_uint(agent_id, 'agentId')}",
        f"Issued: {stamp}",
        f"Terms: I accept the BASIS Terms of Service, version {terms_version}.",
    ])


class AgentIdentityModule(BaseModule):
    """ERC-8004 agent identity (external Identity Registry) plus the backend agent API. ``deployed`` is False on a chain
    whose address book has no registry, and every call that needs it raises ``NotDeployedError`` there.
    A wallet signs in to the API only when it is linked to a BASIS account or is a registered agent, so a new agent's
    first ``POST /api/agents`` is a signed message (``sync_signed``); ``register`` / ``register_and_sync`` / ``update``
    send it when the client has neither a session nor an API key of the signer's wallet (``client.api_key_wallet``),
    or when the signer's last sign-in answered ``wallet_not_linked``. One agent, one wallet: the API never records a wallet
    linked to a person's BASIS account as an agent."""

    _PENDING_NONCE_RECHECK = {"reads": 5, "delay_ms": 500}

    _MINED_BLOCK_WAIT = {"wait_ms": int(PIN_WAIT_S * 1000), "poll_ms": int(PIN_POLL_S * 1000)}

    def __init__(self, client):
        super().__init__(client)
        self._registry_cache = None
        self._earlier_entries: List[Dict[str, Any]] = []
        self._finish_queued = False

    @property
    def deployed(self) -> bool:
        """True when the address book has an ERC-8004 registry for this chain."""
        return not is_zero_address(self.client.addresses["erc8004Registry"])

    @property
    def registry_address(self) -> str:
        """ERC-8004 Identity Registry: a per-chain address from the address book (NotDeployedError when zero)."""
        return self._require(self.client.addresses["erc8004Registry"], "ERC8004Registry")

    @property
    def _registry(self):
        addr = Web3.to_checksum_address(self.registry_address)
        if self._registry_cache is None or self._registry_cache.address != addr:
            self._registry_cache = self.web3.eth.contract(address=addr, abi=IDENTITY_ABI)
        return self._registry_cache

    def has_identity(self, wallet: str) -> bool:
        """True when ``wallet`` owns any ERC-8004 identity on the registry (any protocol: the registry is shared).
        ``wallet``: a 0x address (a mixed-case one must carry its EIP-55 checksum; ``ValidationError("wallet")``
        otherwise)."""
        w = to_address(wallet, "wallet")
        return self._registry.functions.balanceOf(w).call() > 0

    def is_basis_agent(self, agent_id: int) -> bool:
        """True when agent ``agent_id`` carries the BASIS mark, by the API's rule: the registry's ``basis`` metadata
        key, or a registration file with ``protocol: "basis"`` or a BASIS services entry (:func:`is_basis_registration`).
        An https or ipfs file is not fetched: without the metadata key it reads False here (``is_registered`` then
        trusts the API's record)."""
        return self._basis_mark(to_uint(agent_id, "agentId")) == "yes"

    def _basis_mark(self, agent_id: int) -> str:
        """``yes`` | ``no`` | ``unknown`` (an https / ipfs registration file without the metadata key)."""
        uri = self.get_agent_uri(agent_id)
        if is_basis_agent_uri(uri):
            return "yes"
        mark = self.get_metadata(agent_id, BASIS_METADATA_KEY)
        if mark:
            return "yes"
        return "unknown" if _REMOTE_URI.match(_js_trim(str(uri))) else "no"

    def _controls(self, wallet: str, agent_id: int) -> Tuple[bool, str]:
        """Whether ``wallet`` may act as agent ``agent_id`` (its owner or the agent wallet declared on the registry),
        and the owner."""
        owner = self.owner_of(agent_id)
        if owner.lower() == wallet.lower():
            return True, owner
        declared = self.get_agent_wallet(agent_id)
        return (not is_zero_address(declared) and declared.lower() == wallet.lower()), owner

    def _api_row(self, wallet: str) -> Optional[Dict[str, Any]]:
        """The API's agent row of ``wallet`` (``GET /api/agents/{wallet}``), or None when it has none. API errors are
        raised."""
        lookup = self.client.api.get_agent(wallet) or {}
        row = lookup.get("agent") if lookup.get("isAgent") else None
        if isinstance(row, dict) and isinstance(row.get("wallet"), str) and row["wallet"].lower() == wallet.lower():
            return row
        return None

    def is_registered(self, wallet: str, agent_id: Optional[int] = None) -> bool:
        """True when ``wallet`` acts as a BASIS agent: it owns the agent, or is the agent wallet the owner declared on
        the registry, and the agent carries the BASIS mark (identities of other protocols do not count). With
        ``agent_id``: that agent; a registration file the SDK does not read (https, ipfs) counts when the API records
        this agent for ``wallet``. Without: the registry cannot list a wallet's identities, so the API's row for
        ``wallet`` (``GET /api/agents/{wallet}``) names the id, checked on chain the same way (an https / ipfs file then
        counts: the API checked it); False when the API has none. API errors are raised. ``wallet``: a 0x address
        (``ValidationError("wallet")`` otherwise, before any read). Never guard a ``register()`` with it: right after a
        register whose ``POST /api/agents`` still waits in ``client.pending_syncs`` it is False although the wallet
        holds a BASIS identity, and a register would mint a second one. Use ``register_and_sync(config)`` for that."""
        wallet = to_address(wallet, "wallet")
        self.registry_address
        if agent_id is not None:
            aid = to_uint(agent_id, "agentId")
            if not self._controls(wallet, aid)[0]:
                return False
            mark = self._basis_mark(aid)
            if mark != "unknown":
                return mark == "yes"
            row = self._api_row(wallet)
            return row is not None and int(row["agentId"]) == aid
        row = self._api_row(wallet)
        if not row:
            return False
        aid = int(row["agentId"])
        return self._controls(wallet, aid)[0] and self._basis_mark(aid) != "no"

    def get_agent_id_from_tx(self, tx_hash: str) -> Optional[int]:
        """Extract the agentId from a registration transaction receipt: the first ``Registered`` event emitted by the
        registry, or None."""
        registry = self._registry
        receipt = self.client.web3.eth.get_transaction_receipt(tx_hash)
        found = decode_events(registry, receipt, "Registered")
        return int(found[0]["agentId"]) if found else None

    def _check_config(self, config: Any) -> None:
        """Checks ``config`` as an agent config before anything is read or sent: its types (``ValidationError``
        ``agent`` not a dict, ``name`` / ``description`` / ``image`` not a str, ``capabilities`` not a list of str,
        ``agentServiceInvalid`` for services) and the API's limits of ``POST /api/agents`` (``agentNameTooLong`` above
        100 characters, ``agentDescriptionTooLong`` above 500), the checks ``register`` makes before its tx.
        ``create(agent=...)`` runs it up front, so a bad config stops before the sign-in and the API key. TS:
        ``_checkConfig``."""
        self._agent_fields(config)

    def _agent_fields(self, config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """The ``POST /api/agents`` body fields from ``config`` (name defaults to "Basis Agent"); checks the config's
        types and the API's limits."""
        _check_agent_config(config)
        config = config or {}
        name = config.get("name") or "Basis Agent"
        description = config.get("description") or None
        if _js_len(name) > _MAX_NAME:
            raise ValidationError("agentNameTooLong",
                                  f"agent name is {_js_len(name)} characters; the API takes at most {_MAX_NAME}.")
        if description and _js_len(description) > _MAX_DESCRIPTION:
            raise ValidationError("agentDescriptionTooLong", f"agent description is {_js_len(description)} characters; "
                                                             f"the API takes at most {_MAX_DESCRIPTION}.")
        out: Dict[str, Any] = {"name": name}
        if description:
            out["description"] = description
        return out

    def _uses_signed_sync(self) -> bool:
        """True when this client records its agent with the signed first sync: it has neither a session nor an API
        key of the signer's wallet (``client.api_key_wallet``: another wallet's key, or one whose wallet is not known,
        never goes with the signer's record), or the current signer's last sign-in answered ``wallet_not_linked`` (a key
        made before the sign-in rule would be refused the same way; after a signer switch the new signer is not taken
        for the old one). TS: ``usesSignedSync``."""
        account = getattr(self.client, "account", None)
        signer = account.address.lower() if account else None
        key_wallet = getattr(self.client, "api_key_wallet", None)
        own_key = (bool(self.client.api_key) and signer is not None and isinstance(key_wallet, str)
                   and key_wallet.lower() == signer)
        return bool(getattr(self.client, "_wallet_not_linked", False)) or (
            not own_key and not self.client.api._has_session())  # noqa: SLF001

    def _require_api_records_agents(self, action: str) -> None:
        """Refuses (ValidationError, nothing sent, no gas spent) when the API cannot record an agent:
        ``GET /api/v1/status`` (else ``/api/v1/config``) must report ``features.agents: true`` (an ERC-8004 registry
        is configured there) for this client's chain. API errors are raised as they are."""
        status = self.client.api.get_status() or {}
        features = status.get("features")
        if features is None:
            features = (self.client.api.get_config() or {}).get("features")
        chain = status.get("chainId")
        if isinstance(chain, int) and not isinstance(chain, bool) and chain != self.client.chain_id:
            raise ValidationError(
                "agentApiWrongChain",
                f"{action}: the API at {self.client.api_domain} serves chain {chain}, this client is on chain "
                f"{self.client.chain_id}, so it cannot record this agent. Nothing was sent.")
        if not features or features.get("agents") is not True:
            shown = ("true" if features.get("agents") is True else "false" if features.get("agents") is False
                     else "undefined") if features else "missing"
            raise ValidationError(
                "agentsNotSupported",
                f"{action}: the API at {self.client.api_domain} cannot record agents on chain {self.client.chain_id} "
                f"(features.agents is {shown}: no ERC-8004 registry configured there). Nothing was sent, no gas spent.")

    def _signed_sync_refusal(self, action: str, config: Any) -> Optional[ValidationError]:
        """Why the API in ``config`` takes no signed first sync, or None when it does: it must name the Terms version,
        and the real site (``paper: false``) records an agent only for a signed-in wallet today."""
        config = config if isinstance(config, dict) else {}
        if not (config.get("terms") or {}).get("version"):
            return ValidationError(
                "termsVersionUnknown",
                f"{action}: {self.client.api_domain}/api/v1/config names no terms.version, so this API takes no signed "
                "agent sync. Nothing was sent, no gas spent.")
        if config.get("paper") is False:
            if getattr(self.client, "_wallet_not_linked", False):
                msg = (f"{action}: this wallet cannot sign in at {self.client.api_domain} yet (wallet_not_linked), and "
                       "that site takes no agent sync without a sign-in. Agents onboard on the paper site for now "
                       "(chain=\"robinhoodPaper\"). Nothing was sent, no gas spent.")
            else:
                msg = (f"{action}: the real site ({self.client.api_domain}) records an agent only for a signed-in "
                       "wallet: sign in first (BasisClient.create() with this signer, or client.authenticate()), then "
                       "register. Nothing was sent, no gas spent.")
            return ValidationError("agentSignedSyncUnavailable", msg)
        return None

    def _require_signed_sync(self, action: str) -> None:
        """Raises (before a tx) the :meth:`_signed_sync_refusal` of the API's current config. API errors are raised as
        they are."""
        refusal = self._signed_sync_refusal(action, self.client.api.get_config())
        if refusal is not None:
            raise refusal

    def _refuse_if_person_wallet(self, action: str) -> None:
        """One agent, one wallet: refuses (``ValidationError("wallet_in_account")``, nothing sent) a signed-in wallet
        that the API let in through a person's BASIS account. The paper site signs in only a person's linked wallet or
        an agent, so a session there whose wallet has no agent row is a person's. Other APIs (``paper`` not true in
        ``/api/v1/config``), clients without a session (before a registry tx the checks sign in first, a key-only client
        too) and wallets that cannot sign in are not checked. API errors are raised. TS: ``_refuseIfPersonWallet``."""
        if not self.client.api._has_session() or getattr(self.client, "_wallet_not_linked", False):  # noqa: SLF001
            return
        if (self.client.api.get_config() or {}).get("paper") is not True:
            return
        wallet = self._account
        if self._api_row(wallet):
            return
        raise ValidationError(
            "wallet_in_account",
            f"{action}: {wallet} signs in through a BASIS account (X, Google or email), and the API never records such "
            "a wallet as an agent (403 wallet_in_account). An agent needs a wallet of its own: make a new wallet for the "
            "agent and use its key. Nothing was sent, no gas spent.")

    def _sign_sync_body(self, wallet: str, agent_id: int) -> Dict[str, Any]:
        """The signed first sync body ``{"agentId", "message", "signature"}`` of ``wallet``'s agent ``agent_id``, signed
        now: the Terms version from ``GET /api/v1/config``, the Issued time from the API's clock (its Date header) when
        the local clock is more than 60 s off. Raises SignerUnavailableError when the client has no signer for
        ``wallet`` (a queued entry then waits for one), ValidationError when the API takes no signed sync, and API
        errors of the config read. TS: ``_signSyncBody``."""
        from ..client import siwe_domain
        account = self.client.account
        if not account or account.address.lower() != str(wallet).lower():
            raise SignerUnavailableError(f"the signed sync of agent {agent_id} needs the signer of {wallet}, which this "
                                         "client does not hold")
        aid = to_uint(agent_id, "agentId")
        if aid > _MAX_AGENT_ID:
            raise ValidationError("agentId", f"agentId must be an integer 0..{_MAX_AGENT_ID} (the API stores an Int), "
                                             f"got {aid}.")
        config, skew = self.client.api._get_config_with_clock()  # noqa: SLF001
        refusal = self._signed_sync_refusal("agent sync", config)
        if refusal is not None:
            raise refusal
        issued = None
        if skew is not None and abs(skew) > _CLOCK_SKEW_MS:
            issued = datetime.fromtimestamp(time.time() + skew / 1000, tz=timezone.utc)
        message = build_agent_sync_message(siwe_domain(self.client.api_domain), account.address, aid,
                                           config["terms"]["version"], issued)
        signed = account.sign_message(encode_defunct(text=message))
        return {"agentId": aid, "message": message, "signature": "0x" + signed.signature.hex().removeprefix("0x")}

    def sync_signed(self, agent_id: int) -> Dict[str, Any]:
        """The first sync of this signer's agent ``agent_id`` without a sign-in, sent once: signs
        :func:`build_agent_sync_message` (the Terms version from ``GET /api/v1/config``, the Issued time from the API's
        clock when the local one is more than 60 s off) and posts it (``api.register_agent_signed``). Signing accepts
        the BASIS Terms. For a wallet that cannot sign in yet (BasisApiError code ``wallet_not_linked``); afterwards
        ``client.authenticate()`` works. ``register``, ``register_and_sync`` and ``update`` send it on their own
        (retried, and queued for ``client.flush_pending_syncs()``, which signs it again). Also the way to sync an agent
        registered elsewhere (mark it first: ``set_metadata(agent_id, "basis", "1")``). Raises WalletRequiredError,
        ValidationError (``termsVersionUnknown``, ``agentSignedSyncUnavailable`` on the real site) and API refusals
        (BasisApiError: 401 ``invalid_signature``, 403 ``not_branded`` / ``wallet_in_account`` / ``forbidden``, 429)."""
        if not self.client.account:
            raise WalletRequiredError()
        body = self._sign_sync_body(self.client.account.address, to_uint(agent_id, "agentId"))
        return self.client.api.register_agent_signed(body["agentId"], body["message"], body["signature"])

    def _applied(self, wallet: str, registration: Optional[Dict[str, Any]],
                 config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """``registration["applied"]`` for the result; logs one warning when the registration name or image was not
        used for a reason the caller can fix (a taken or reserved name, an unreadable image, ...). The name part only
        when ``config`` names the agent: an unnamed one is "Basis Agent", whose username is soon taken. TS:
        ``appliedFrom``."""
        a = registration.get("applied") if registration and registration.get("ok") else None
        if not a:
            return None
        nope = []
        named = bool((config or {}).get("name"))
        if named and not a["username"]["applied"] and a["username"].get("reason") not in _APPLIED_ON_PURPOSE:
            nope.append(f"the name was not used as the username ({a['username'].get('reason') or 'no reason given'})")
        if not a["photo"]["applied"] and a["photo"].get("reason") not in _APPLIED_ON_PURPOSE:
            nope.append(f"the image was not used as the avatar ({a['photo'].get('reason') or 'no reason given'})")
        if nope:
            logging.getLogger("basis").warning(
                "[basis-sdk] agent of %s is synced, but %s. agent.update(agent_id, config) with another name or image "
                "tries again.", wallet, " and ".join(nope))
        return a

    def _record_agent(self, tx_hash: str, wallet: str, agent_id: Optional[int], fields: Dict[str, Any]) -> Dict[str, Any]:
        """The ``POST /api/agents`` of ``agent_id`` after a confirmed tx: ``client.sync_agent_registration`` (a session
        or the signer's API key; a 403 ``wallet_not_linked`` answer turns into the signed sync), else the signed first
        sync (retried, queued). A missing id is reported (and logged), never raised."""
        if agent_id is None:
            last_error = f"no Registered event from {self.registry_address} in tx {tx_hash}: POST /api/agents not sent"
            logging.getLogger("basis").error("[basis-sdk] agent registration of %s: %s", wallet, last_error)
            return {"ok": False, "status": None, "code": None, "http_status": 0, "attempts": 0, "queued": False,
                    "last_error": last_error}
        if self._uses_signed_sync():
            return self.client._sync_agent_signed(tx_hash, {"wallet": wallet, "agent_id": int(agent_id)})  # noqa: SLF001
        return self.client.sync_agent_registration(tx_hash, {"wallet": wallet, "agent_id": int(agent_id), **fields})

    def _tx_sync_queued(self, tx_hash: str) -> bool:
        return any(not e.get("kind") and e["hash"].lower() == tx_hash.lower() for e in self.client.pending_syncs)

    def _pre_tx_checks(self, action: str) -> None:
        """The checks before a registry tx whose agent the API must record: ``features.agents``; without a session (an
        API key alone too), a sign-in first (:meth:`_try_sign_in`) unless the signer's wallet is known to have no door
        yet (``wallet_not_linked``, noted for that wallet only); then for the signed first sync the Terms version (and
        not the real site), else (signed in) not a person's wallet."""
        self._require_api_records_agents(action)
        if not self.client.api._has_session() and not getattr(self.client, "_wallet_not_linked", False):  # noqa: SLF001
            self._try_sign_in()
        if self._uses_signed_sync():
            self._require_signed_sync(action)
        else:
            self._refuse_if_person_wallet(action)

    def _try_sign_in(self) -> None:
        """A sign-in before the tx of a client without a session (``auth=False``, with or without an API key): a wallet
        linked to a person's BASIS account signs in, and :meth:`_refuse_if_person_wallet` then refuses it before any gas
        is spent (its record would get 403 ``wallet_in_account``, final, after the mint: an orphaned identity). Signed
        in, the client keeps the session. ``wallet_not_linked`` (a new wallet, noted by ``authenticate``) goes on with
        the signed first sync. Any other failure (429, 5xx, no answer) stops the write before the tx: its error is
        raised as it is, nothing was sent (without the sign-in the SDK cannot tell a person's wallet from a new one).
        TS: ``trySignIn`` (in browser cookie mode, which PY does not have, it also takes a session the browser holds
        first, and skips the sign-in on a page that gets no answer from the API)."""
        try:
            self.client.authenticate()
        except BasisApiError as e:
            if e.code == "wallet_not_linked":
                return
            raise

    def register(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Register the signer as an ERC-8004 agent. The registration file links the client's BASIS site
        (``build_agent_metadata_uri(client.api_domain, config)``). The tx goes through the standard write pipeline (its
        ``/api/v1/sync`` is awaited), then the registration is recorded with ``POST /api/agents``
        (``client.sync_agent_registration``: retried, queued for ``client.flush_pending_syncs()`` on failure; without a
        session or an API key of the signer's wallet, or after a sign-in that answered ``wallet_not_linked``: the signed
        first sync, signed again for every attempt).

        Without a session (an API key alone too) it signs in first: a wallet that can (a person's linked wallet) is
        refused before the tx (``wallet_in_account``) and keeps the session; a new wallet (``wallet_not_linked``) goes
        on with the signed first sync; any other failure of that sign-in (429, 5xx, no answer) stops before the tx, its
        error raised as it is (nothing sent).

        Never raises after the tx is confirmed. Returns ``{"hash", "agent_id", "uri", "tx_sync_queued",
        "registration", "applied", "warnings"}``: ``agent_id`` and ``uri`` from the ``Registered`` event (None, with a
        warning, when the receipt has none); ``tx_sync_queued`` True when the tx sync failed and waits in
        ``client.pending_syncs`` (when delivered, the route answers ``ignored``: the registry is not a BASIS contract);
        ``registration`` the ``POST /api/agents`` result (status ``registered``, or ``queued``); ``applied`` what that
        post applied to the wallet's profile (``registration["applied"]``: ``{"username": {"applied", "username",
        "reason"}, "photo": {"applied", "reason"}}``, ``reason`` when not: ``taken``, ``reserved``, ``unusable``,
        ``chosen_name_kept``, ``unreadable``, ``pin_failed``, ...), None when nothing was delivered or the API did not
        say; a warning is logged when the name (when ``config`` gives one) or the image was not used for a reason the
        caller can fix.
        Before the tx it raises ValidationError (the config's types: ``agent`` not a dict, ``name`` / ``description`` /
        ``image`` not a str, ``capabilities`` not a list of str, ``agentServiceInvalid``; ``agentNameTooLong``,
        ``agentDescriptionTooLong``, ``agentsNotSupported`` / ``agentApiWrongChain`` when the API cannot record agents,
        ``termsVersionUnknown`` / ``agentSignedSyncUnavailable`` when the signed sync cannot work there,
        ``wallet_in_account`` for a signed-in wallet of a person's BASIS account) and API errors from those checks. A
        receipt that cannot be read after sending raises ``TxNotConfirmedError`` whose ``note`` says to call
        ``register_and_sync(config, tx_hash)`` once the tx is mined (the agentId, and so ``POST /api/agents``, comes
        from the receipt); calling ``register`` again would mint a second identity."""
        wallet = self._account
        fields = self._agent_fields(config)
        uri = build_agent_metadata_uri(self.client.api_domain, config)
        registry = self._registry
        self._pre_tx_checks("agent.register")
        try:
            tx = self._send(registry.functions.register(uri), "ERC8004Registry")
        except TxNotConfirmedError as e:
            raise e.with_note(
                "The agent is NOT recorded with the API yet (POST /api/agents needs the agentId from the mined tx). Once "
                f"the tx is mined, call agent.register_and_sync(config, '{e.tx_hash}'); do not call register() again (it "
                "would mint a second identity).") from e
        h = tx["hash"]
        ev = self._expect_event(tx, registry, "Registered", "ERC8004Registry", ("agent_id", "uri"))
        agent_id = int(ev["agentId"]) if ev else None
        tx_sync_queued = self._tx_sync_queued(h)
        registration = self._record_agent(h, wallet, agent_id, fields)
        return {"hash": h, "agent_id": agent_id, "uri": ev["agentURI"] if ev else None,
                "tx_sync_queued": tx_sync_queued, "registration": registration,
                "applied": self._applied(wallet, registration, config), "warnings": tx["warnings"]}

    def _owner_or_none(self, agent_id: int) -> Optional[str]:
        """The owner of agent ``agent_id``, or None when the registry reverts the read (no such token: on a node a block
        or two behind, an identity minted just now). Other errors (RPC) are raised. TS: ``ownerOrNull``."""
        from web3.exceptions import ContractLogicError
        try:
            return self.owner_of(agent_id)
        except ContractLogicError:
            return None

    def _refuse_if_registration_pending(self, action: str, wallet: str) -> Tuple[Any, ...]:
        """Refuses (``ValidationError("agentTxPending")``, nothing sent) a new registration of ``wallet`` while one of
        its transactions may still be an earlier registration:

        - its pending nonce stays above its latest one (a tx from it is still pending): read again over about 2 s
          (:attr:`_PENDING_NONCE_RECHECK`) and refused only when the gap persists, since a load-balanced RPC can answer
          the two counts from nodes a block apart;
        - or the sync queue (``pending_syncs``, the ``queue_file``, and the lines ``create(agent=...)`` read before its
          onboarding flush) holds a tx sync line of it (any code: sent but not seen confirmed, or mined with its sync
          post failed; a line naming another sender, or a target other than the registry, is skipped unread: only a
          line to the registry or with no target recorded (an older line), is read) whose tx, read
          on chain, is a ``register`` call of it to the registry that is still pending, or mined without a revert while
          the agent id of its ``Registered`` event is still owned by ``wallet`` (or not visible yet: the identity read
          may lag behind it). A tx to the registry that is not a
          ``register`` call (setAgentURI, setMetadata), a mined one whose identity the wallet no longer owns, and a
          queued tx the node does not know at all are not counted.

        Returns what the identity read must cover (:meth:`_identity_once_mined`): ``(count, gap_closed)``, ``count`` the
        wallet's nonce count the reads saw (the highest pending count, or the last latest one when higher), and
        ``gap_closed`` True when a gap was seen and closed during the re-reads (a pending tx of ``wallet`` mined
        meanwhile, maybe an earlier registration). While ``_finish_queued`` is set (``create(agent=...)``), a queued
        register that is mined without a revert and whose agent the registry shows owned by ``wallet`` is not refused
        but returned as a third item, ``mined_hash`` (the first one; any other queued register still pending or not
        visible yet is refused as before): the caller records it with its tx hash. RPC errors are raised. TS:
        ``_refuseIfRegistrationPending`` (an object ``{count, gapClosed, minedHash?}``)."""
        eth = self.web3.eth
        addr = Web3.to_checksum_address(wallet)
        reads = int(self._PENDING_NONCE_RECHECK["reads"])
        delay = float(self._PENDING_NONCE_RECHECK["delay_ms"]) / 1000
        highest = 0
        gap = False
        i = 0
        while True:
            pending = eth.get_transaction_count(addr, "pending")
            latest = eth.get_transaction_count(addr, "latest")
            i += 1
            highest = max(highest, pending)
            if pending > latest:
                gap = True
            if pending <= latest or i >= reads:
                break
            time.sleep(delay)
        if pending > latest:
            n = pending - latest
            raise ValidationError(
                "agentTxPending",
                f"{action}: {wallet} has {n} transaction{'s' if n > 1 else ''} still pending (nonce {latest}"
                f"{f' to {pending - 1}' if n > 1 else ''}): one may be an earlier registration, and registering now could "
                "mint a second identity. Nothing was sent. Wait until it is mined, then call this again; for a "
                "registration of yours, pass its tx_hash: register_and_sync(config, tx_hash).")
        registry = self.registry_address.lower()
        me = wallet.lower()
        seen = set()
        mined_hash: Optional[str] = None
        for e in [*self.client._queued_tx_entries(), *self._earlier_entries]:  # noqa: SLF001
            h = e.get("hash") if isinstance(e, dict) else None
            if not isinstance(h, str) or e.get("kind") is not None or h.lower() in seen:
                continue
            seen.add(h.lower())
            sender = e.get("from")
            if isinstance(sender, str) and sender.lower() != me:
                continue
            target = e.get("to")
            if isinstance(target, str) and target.lower() != registry:
                continue
            tx = _or_not_found(eth.get_transaction, h)
            if not tx or str(tx.get("from") or "").lower() != me or str(tx.get("to") or "").lower() != registry:
                continue
            if not _is_register_call(tx.get("input")):
                continue
            mined = tx.get("blockNumber") is not None
            agent_id = None
            if mined:
                receipt = _or_not_found(eth.get_transaction_receipt, h)
                if receipt is not None:
                    if receipt.get("status") != 1:
                        continue
                    found = decode_events(self._registry, receipt, "Registered")
                    if not found:
                        continue
                    agent_id = int(found[0]["agentId"])
                    owner = self._owner_or_none(agent_id)
                    if owner is not None and str(owner).lower() != me:
                        continue
                    if owner is not None and self._finish_queued:
                        if mined_hash is None:
                            mined_hash = h
                        continue
            owned = f": agent {agent_id} is this wallet's" if agent_id is not None else ""
            line = ("a registration sent but not seen confirmed" if e.get("code") == "not_confirmed"
                    else "a registration whose sync is still queued")
            raise ValidationError(
                "agentTxPending",
                f"{action}: the sync queue holds tx {h} from {wallet} to the identity registry, {line} "
                f"({'mined since' if mined else 'still pending'}){owned}. Registering now could mint "
                "a second identity. Nothing was sent. "
                f"{'Pass it' if mined else 'Once it is mined, pass it'}: register_and_sync(config, '{h}'). "
                "client.flush_pending_syncs() clears the queued line once the tx is mined (it posts the tx's sync).")
        if mined_hash is not None:
            return max(highest, latest), gap, mined_hash
        return max(highest, latest), gap

    def _identity_once_mined(self, action: str, wallet: str, count: int, gap_closed: bool = True) -> bool:
        """Whether ``wallet`` holds an ERC-8004 identity at a block where all ``count`` of its txs are mined (``count``:
        the nonce count :meth:`_refuse_if_registration_pending` saw): the latest block, read again every ``poll_ms`` up
        to ``wait_ms`` (:attr:`_MINED_BLOCK_WAIT`) while its nonce count there is below ``count`` (a node a block behind
        can answer first), then ``balanceOf`` pinned to that block (:func:`~basis.core.pinned.retry_pinned_at`). The
        only identity read before a mint: a balance read at latest from a lagging node can miss an identity another
        process minted in the last block; this one cannot (two reads more, and no wait once the node is caught up).
        When no block shows those txs mined in time: ``ValidationError("agentTxPending")``, nothing sent
        (``gap_closed``: one was pending a moment ago, else a node behind). RPC errors are raised (a node that still
        does not know the block: BasisRpcError). TS: ``identityOnceMined``."""
        eth = self.web3.eth
        addr = Web3.to_checksum_address(wallet)
        wait = float(self._MINED_BLOCK_WAIT["wait_ms"]) / 1000
        poll = float(self._MINED_BLOCK_WAIT["poll_ms"]) / 1000
        until = time.monotonic() + wait
        while True:
            block = int(eth.block_number)
            mined = retry_pinned_at(lambda b=block: eth.get_transaction_count(addr, b), block)
            if mined >= count:
                fn = self._registry.functions.balanceOf(addr)
                return retry_pinned_at(lambda b=block: fn.call(block_identifier=b), block) > 0
            if time.monotonic() >= until:
                if gap_closed:
                    what = (f"{wallet} had a transaction pending a moment ago (nonce {count - 1}), and no block shows it "
                            "mined yet")
                    then = "Wait until it is mined, then call this again"
                else:
                    what = (f"{wallet}'s last transaction (nonce {count - 1}) shows in no block the RPC serves yet (a "
                            "node behind)")
                    then = "Call this again in a moment"
                raise ValidationError(
                    "agentTxPending",
                    f"{action}: {what}: it may be an earlier registration, and registering now could mint a second "
                    f"identity. Nothing was sent. {then}; for a registration of yours, pass its tx_hash: "
                    "register_and_sync(config, tx_hash).")
            time.sleep(poll)

    def register_and_sync(self, config: Optional[Dict[str, Any]] = None, tx_hash: Optional[str] = None) -> Dict[str, Any]:
        """Registers the signer unless it already acts as a BASIS agent (same result shape as :meth:`register`). It asks
        the API first:

        - the API has the signer's agent and the signer is its owner or its declared agent wallet: returns it (no tx,
          no post; ``hash``, ``uri``, ``tx_sync_queued``, ``registration`` None). The API has it but the signer
          controls it no more: ValidationError ``agentNotControlled`` (nothing sent; ``register(config)`` mints a new
          identity), unless ``tx_hash`` names the one to record;
        - the API has none and the signer owns no ERC-8004 identity: ``register(config)``, unless a transaction of the
          signer may still be an earlier registration: ValidationError ``agentTxPending`` (nothing sent) while its
          pending nonce stays above its latest one for about 2 s (a tx from it is still pending), or while the sync
          queue (``pending_syncs`` and the ``queue_file``) holds a ``register`` call of it whose tx sync is queued,
          whatever its code (still pending, or mined with an identity the signer still owns; in
          ``create(agent=...)`` a mined one whose identity the registry shows the signer's is recorded with its tx hash
          instead, as below). These checks
          come first and the identity is read after them, always at a block that has every tx of the signer they saw
          mined (never at latest, which a node behind can answer); when a pending tx of the signer was mined during the
          re-reads and the signer now holds an identity, it is refused (``agentTxPending``: it may be that earlier
          registration; pass its tx_hash);
        - otherwise records the registration tx ``tx_hash`` (``POST /api/agents``, retried and queued like
          ``register``). Raises ValidationError (nothing is sent) when ``tx_hash`` is missing (``agentTxHashRequired``:
          the registry cannot list a wallet's identities), holds no ``Registered`` event (``agentIdNotInTx``), names an
          agent the signer neither owns nor is the agent wallet of (``agentNotOwned``), or one without the BASIS mark
          (``agentNotBasis``; an https / ipfs file the SDK does not read is left to the API), plus the ``register``
          pre-checks. ``config`` is type-checked first (the ``register`` rules), then ``tx_hash`` when given (None =
          not given): 0x + 64 hex digits, else ValidationError ``txHash`` (``""`` included), before any read. API lookup
          errors (429, 5xx, network) and RPC errors are raised, never read as "not in the API" or "nothing pending".
        """
        _check_agent_config(config)
        if tx_hash is not None and (not isinstance(tx_hash, str) or not _TX_HASH.fullmatch(tx_hash)):
            shown = json.dumps(tx_hash) if isinstance(tx_hash, str) else str(tx_hash)
            raise ValidationError("txHash", f"txHash must be a transaction hash (0x + 64 hex characters), got {shown}.")
        wallet = self._account
        self.registry_address
        row = self._api_row(wallet)
        if row:
            aid = int(row["agentId"])
            if self._controls(wallet, aid)[0]:
                return {"hash": None, "agent_id": aid, "uri": None, "tx_sync_queued": None,
                        "registration": None, "applied": None, "warnings": []}
            if not tx_hash:
                raise ValidationError(
                    "agentNotControlled",
                    f"the API has agent {aid} for {wallet}, but this wallet is neither its owner nor its agent wallet on "
                    "the registry any more. Nothing was sent. register(config) mints a new identity for it; "
                    "register_and_sync(config, tx_hash) records one you minted.")
        if not tx_hash:
            seen = self._refuse_if_registration_pending("agent.register_and_sync", wallet)
            count, gap_closed = seen[0], seen[1]
            mined_hash = seen[2] if len(seen) > 2 else None
            if mined_hash:
                return self._record_from_tx(config, wallet, mined_hash)
            owns = self._identity_once_mined("agent.register_and_sync", wallet, count, gap_closed)
            if not owns:
                return self.register(config)
            if gap_closed:
                raise ValidationError(
                    "agentTxPending",
                    f"agent.register_and_sync: {wallet} had a transaction pending a moment ago (nonce {count - 1}) that "
                    "is mined now, and the wallet holds an ERC-8004 identity: it may be an earlier registration whose "
                    "sync with BASIS is still on its way. Registering now could mint a second identity. Nothing was "
                    "sent. For a registration of yours, pass its tx_hash: register_and_sync(config, tx_hash).")
            raise ValidationError(
                "agentTxHashRequired",
                f"{wallet} owns an ERC-8004 identity but the API has no BASIS agent for it, and the registry cannot "
                "list a wallet's identities. Nothing was sent. For a BASIS registration, pass its tx_hash: "
                "register_and_sync(config, tx_hash). For an identity registered elsewhere, mark it first "
                "(set_metadata(agent_id, 'basis', '1')), then sync_signed(agent_id); register(config) mints a new BASIS "
                "identity instead.",
            )
        return self._record_from_tx(config, wallet, tx_hash)

    def _record_from_tx(self, config: Optional[Dict[str, Any]], wallet: str, tx_hash: str) -> Dict[str, Any]:
        """:meth:`register_and_sync`'s tx-hash path: records registration tx ``tx_hash`` of ``wallet`` (``POST
        /api/agents``, retried and queued like ``register``) after its checks (``agentIdNotInTx``, ``agentNotOwned``,
        ``agentNotBasis``, the API's ``features.agents`` and, for the signed first sync, the Terms version). Also how
        ``create(agent=...)`` finishes a mined registration it found in the sync queue. TS: ``recordFromTx``."""
        fields = self._agent_fields(config)
        agent_id = self.get_agent_id_from_tx(tx_hash)
        if agent_id is None:
            raise ValidationError("agentIdNotInTx", f"tx {tx_hash} has no Registered event from the registry.")
        ok, owner = self._controls(wallet, agent_id)
        if not ok:
            raise ValidationError(
                "agentNotOwned",
                f"agent {agent_id} (from tx {tx_hash}) belongs to {owner}, and {wallet} is neither its owner nor its "
                "agent wallet: the API would refuse it (403 forbidden). Nothing was sent.")
        uri = self.get_agent_uri(agent_id)
        if self._basis_mark(agent_id) == "no":
            raise ValidationError(
                "agentNotBasis",
                f"agent {agent_id} carries no BASIS mark (protocol \"basis\" or a BASIS services entry in its "
                "registration file, or the registry's \"basis\" metadata key): the API would refuse it (403 "
                f"not_branded). set_metadata({agent_id}, 'basis', '1') marks it; update({agent_id}, config) rewrites "
                "its file as a BASIS registration. Nothing was sent.")
        self._require_api_records_agents("agent.register_and_sync")
        if self._uses_signed_sync():
            self._require_signed_sync("agent.register_and_sync")
        registration = self._record_agent(tx_hash, wallet, agent_id, fields)
        return {"hash": tx_hash, "agent_id": agent_id, "uri": uri, "tx_sync_queued": None,
                "registration": registration, "applied": self._applied(wallet, registration, config), "warnings": []}

    def update(self, agent_id: int, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Rewrites agent ``agent_id``'s registration (name, description, image, capabilities, services) on chain with
        ``setAgentURI``, the URI now carrying ``registrations`` (this agentId on this registry) and linking the
        client's BASIS site, then re-posts it with ``POST /api/agents`` (the signed first sync for a wallet that cannot
        sign in yet, as in :meth:`register`) so the API row follows. Also turns a non-BASIS identity of the signer into
        a BASIS one. Never raises after the tx is confirmed; same result shape as :meth:`register` (``uri`` from the
        ``URIUpdated`` event). Before it: ValidationError (``agentNotOwned`` when the signer is not the owner, the
        ``register`` checks). A receipt that cannot be read after sending raises ``TxNotConfirmedError`` with both the
        tx sync and the ``POST /api/agents`` queued: ``client.flush_pending_syncs()`` finishes it."""
        wallet = self._account
        aid = to_uint(agent_id, "agentId")
        fields = self._agent_fields(config)
        registry = self._registry
        uri = build_agent_metadata_uri(self.client.api_domain, config, {"agent_id": aid, "chain_id": self.client.chain_id,
                                                                        "registry": registry.address})
        owner = self.owner_of(aid)
        if owner.lower() != wallet.lower():
            raise ValidationError("agentNotOwned", f"agent {aid} belongs to {owner}, not the signer {wallet}: only its "
                                                   "owner rewrites its registration here. Nothing was sent.")
        self._pre_tx_checks("agent.update")
        signed = self._uses_signed_sync()
        try:
            tx = self._send(registry.functions.setAgentURI(aid, uri), "ERC8004Registry")
        except TxNotConfirmedError as e:
            reason = "agent.update: receipt not read after sending"
            if signed:
                self.client._queue_agent_signed(e.tx_hash, {"wallet": wallet, "agent_id": int(aid)}, reason)  # noqa: SLF001
            else:
                self.client.queue_agent_sync(e.tx_hash, {"wallet": wallet, "agent_id": int(aid), **fields}, reason)
            raise e.with_note("The agent record (POST /api/agents) is queued too: client.flush_pending_syncs() posts both "
                              "once the tx is mined.") from e
        h = tx["hash"]
        ev = self._expect_event(tx, registry, "URIUpdated", "ERC8004Registry", ("uri",))
        tx_sync_queued = self._tx_sync_queued(h)
        registration = self._record_agent(h, wallet, aid, fields)
        return {"hash": h, "agent_id": aid, "uri": ev["newURI"] if ev else None, "tx_sync_queued": tx_sync_queued,
                "registration": registration, "applied": self._applied(wallet, registration, config), "warnings": tx["warnings"]}

    def sync_to_api(self, wallet: str, agent_id: int, config: Optional[Dict[str, Any]] = None):
        """Save registration to the backend API (``client.api.register_agent``; ``agent_id`` is passed unchanged, so a
        non-integer is refused there like in TS).
        Raises AuthRequiredError (before any request) when the client has neither an API key nor a session,
        ValidationError for a ``config`` of the wrong types (the ``register`` rules), and BasisApiError on non-2xx."""
        _check_agent_config(config)
        config = config or {}
        self.client.api.register_agent(
            wallet,
            agent_id,
            name=config.get("name") or "Basis Agent",
            description=config.get("description") or None,
        )

    def lookup_from_api(self, wallet: str) -> Dict[str, Any]:
        """Look up an agent by wallet via the API (``client.api.get_agent``): ``{"isAgent": False, "agent": None}``
        when the API has none. Errors (429, 5xx, network) are raised."""
        return self.client.api.get_agent(wallet)

    def list_agents(self, page: int = 1, limit: int = 20) -> Dict[str, Any]:
        """List all registered agents via the API (``client.api.get_agents``)."""
        return self.client.api.get_agents(page=page, limit=limit)

    def owner_of(self, agent_id: int) -> str:
        """Owner of agent ``agent_id`` (the registry's ``ownerOf``; reverts ``ERC721NonexistentToken`` for an unknown
        id)."""
        return self._registry.functions.ownerOf(to_uint(agent_id, "agentId")).call()

    def is_authorized_or_owner(self, spender: str, agent_id: int) -> bool:
        """True when ``spender`` may act for agent ``agent_id`` (owner, approved, or operator): who may call
        ``set_agent_uri`` / ``set_metadata``. ``spender``: a 0x address (``ValidationError("spender")`` otherwise,
        before the read)."""
        s = to_address(spender, "spender")
        return self._registry.functions.isAuthorizedOrOwner(s, to_uint(agent_id, "agentId")).call()

    def get_agent_uri(self, agent_id: int) -> str:
        """Get the tokenURI for a registered agent (on-chain). ``agent_id``: a whole number
        (``ValidationError('agentId')``)."""
        return self._registry.functions.tokenURI(to_uint(agent_id, "agentId")).call()

    def get_agent_wallet(self, agent_id: int) -> str:
        """Get the agent wallet of an agent ID (on-chain): the owner at registration, a wallet the owner declared later
        with a signed ``setAgentWallet``, zero for an unknown id. That wallet may sync the agent with BASIS too.
        ``agent_id``: a whole number (``ValidationError('agentId')``)."""
        return self._registry.functions.getAgentWallet(to_uint(agent_id, "agentId")).call()

    def get_metadata(self, agent_id: int, key: str) -> bytes:
        """Get metadata for an agent by key (on-chain): the raw bytes (``b""`` when unset). ``agent_id``: a whole
        number (``ValidationError('agentId')``); ``key``: well-formed text (``ValidationError('metadataKey')``). Any
        value under the key ``basis`` marks the agent as a BASIS one."""
        aid = to_uint(agent_id, "agentId")
        if not isinstance(key, str):
            raise ValidationError("metadataKey", "metadata key must be a string.")
        check_text(key, "metadataKey")
        return self._registry.functions.getMetadata(aid, key).call()

    def set_agent_uri(self, agent_id: int, new_uri: str) -> Dict[str, Any]:
        """Update the agent's URI (on-chain). Owner or approved only (else the simulation reverts "Not authorized").
        The tx dict adds ``agent_id`` and ``uri`` from the ``URIUpdated`` event (None with a warning when missing). A
        readable registration file without the BASIS mark adds a warning: unless the agent has the registry's
        ``basis`` metadata key, the API would then refuse to (re-)record it. ``update(agent_id, config)`` builds the URI
        and refreshes the API row too. ``new_uri`` with a lone surrogate (not text the chain can store):
        ``ValidationError("newURI")`` before the tx."""
        check_text(new_uri, "newURI")
        registry = self._registry
        tx = self._send(registry.functions.setAgentURI(to_uint(agent_id, "agentId"), new_uri), "ERC8004Registry")
        ev = self._expect_event(tx, registry, "URIUpdated", "ERC8004Registry", ("agent_id", "uri"))
        if not is_basis_agent_uri(new_uri) and not _REMOTE_URI.match(_js_trim(str(new_uri))):
            msg = (f"agent {agent_id}: the new registration file carries no BASIS mark (protocol \"basis\" or a BASIS "
                   "services entry), so unless the agent has the registry's \"basis\" metadata key the BASIS API will "
                   "refuse to record it (403 not_branded). Use update(agentId, config) to write a BASIS one, or "
                   "set_metadata(agent_id, 'basis', '1').")
            tx["warnings"].append(msg)
            logging.getLogger("basis").warning("[basis-sdk] %s", msg)
        return {**tx, "agent_id": int(ev["agentId"]) if ev else None, "uri": ev["newURI"] if ev else None}

    def set_metadata(self, agent_id: int, key: str, value: Union[bytes, bytearray, str]) -> Dict[str, Any]:
        """Set on-chain metadata ``key`` of agent ``agent_id`` (owner or approved). ``value``: ``bytes`` or a ``0x``
        hex string; any other string is stored as its UTF-8 bytes. The reserved key ``agentWallet`` is refused before
        the tx (ValidationError ``agentWalletReservedKey``): the registry sets it at registration and only a signed
        ``setAgentWallet`` changes it. Any value under the key ``basis`` marks an agent registered elsewhere as a BASIS
        one. A key or a text value with a lone surrogate is refused before the tx (``metadataKey`` / ``metadataValue``:
        "is not valid UTF-8 text"). The tx dict adds ``agent_id``, ``key`` and ``value`` (bytes) from the
        ``MetadataSet`` event."""
        aid = to_uint(agent_id, "agentId")
        if not isinstance(key, str):
            raise ValidationError("metadataKey", "metadata key must be a string.")
        check_text(key, "metadataKey")
        if key == _RESERVED_METADATA_KEY:
            raise ValidationError(
                "agentWalletReservedKey",
                f"\"{_RESERVED_METADATA_KEY}\" is a reserved metadata key: the registry sets it to the owner at "
                "registration and only a signed setAgentWallet changes it. Nothing was sent.")
        if isinstance(value, (bytes, bytearray)):
            data = bytes(value)
        elif isinstance(value, str):
            hexpart = value[2:]
            is_hex = value.startswith("0x") and len(hexpart) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in hexpart)
            if not is_hex:
                check_text(value, "metadataValue")
            data = bytes.fromhex(hexpart) if is_hex else value.encode("utf-8")
        else:
            raise ValidationError("metadataValue", "metadata value must be bytes (bytes or 0x hex) or a string.")
        registry = self._registry
        tx = self._send(registry.functions.setMetadata(aid, key, data), "ERC8004Registry")
        ev = self._expect_event(tx, registry, "MetadataSet", "ERC8004Registry", ("agent_id", "key", "value"))
        return {**tx, "agent_id": int(ev["agentId"]) if ev else None, "key": ev["metadataKey"] if ev else None,
                "value": bytes(ev["metadataValue"]) if ev else None}
