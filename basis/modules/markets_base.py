"""Shared logic of the two prediction-market hosts, public and private (TypeScript SDK: src/modules/MarketsBase.ts).

The calls both hosts share use one ABI; host-only calls use each module's own ABI.

Subclasses set ``market_type`` ("public" / "private") and ``_contract_name``, and implement the hooks
``_host_entry`` (property), ``_min_seed_units(private_event)``, ``_precheck_create_host(host)``,
``_precheck_buy_host(market, md, buyer, closed)``, ``_send_create(c)`` and ``get_final_tie_mask(market)``.
"""

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from web3 import Web3

from ..core.redact import redact_urls
from ..api.content import IMAGE_MAX_BYTES, METADATA_MAX_LENGTH, js_len, sniff_image
from ..api.core import AuthRequiredError, BasisApiError, request_with_deadline
from ..config.addresses import is_zero_address
from ..core.base import BaseModule
from ..core.errors import BasisError, NotDeployedError, TxNotConfirmedError, ValidationError
from ..core.amounts import AmountOf, amount_of, opt_amount, pair_amount_of
from ..core.events import add_missing_event_warning
from ..core.loans import (check_deadline, is_asset_paused, pair_asset_paused, pair_asset_paused_message,
                          refuse_paused_asset)
from ..core.mains import MainInfo, pair_amount
from ..core.market_approval import verify_market_approval
from ..core.ranking import OUTCOME_INVALID, OUTCOME_UNRESOLVED, normalize_ranking, whole_number
from ..core.revert import BasisContractError
from ..core.units import MAX_UINT256, Amount, check_text, opt_uint, parse_amount, to_address, to_amount, to_bool, to_uint
from .factory import _ASCII_WS, _check_metadata_fields, _js_trim

logger = logging.getLogger("basis")

HOST_ABI = "TrancheMarket"

MAX_TRANCHES = 10
HARD_MAX_OUTCOMES = 255
MAX_SLICES = 25
BUY_DEADLINE_SECONDS = 600
_BET_PAUSED = "no bet that pays in or converts through it can be placed"


def _input_sale_paused(token_in: str, factory_token: bool) -> str:
    """What a bet paid in ``token_in``, a BASIS token of another pair, cannot do while that pair's asset is paused: the
    bet sells it for that asset first, and selling a factory token also pays out the bettor's pending holder rewards in
    it. Same text in TS (and in Up/Down)."""
    return (f"no bet paid in {token_in} can be placed: the bet sells it for this asset"
            + (", and every sale pays out your pending holder rewards on it in this asset" if factory_token else ""))


def _input_sale_asset(module: Any, token_in: str, main: str, pair: str) -> Optional[Dict[str, Any]]:
    """The other pair's asset a bet paid in ``token_in`` is first sold for, when ``token_in`` is a BASIS token of a pair
    other than the market's own ``pair`` (checked on its own); None for any other input. ``factory_token``: that sale
    also pays out the bettor's pending holder rewards in that asset. Same rule in TS and in Up/Down."""
    mains = module.client.mains
    root = token_in
    factory_token = False
    if not mains.is_main(token_in):
        factory = module.client.addresses.get("factory")
        if is_zero_address(factory):
            return None
        if not module._contract(factory, "Factory").functions.isEcosystemToken(Web3.to_checksum_address(token_in)).call():
            return None
        root = Web3.to_checksum_address(module._contract(token_in, "EcosystemToken").functions.quoteToken().call())
        factory_token = True
        if not mains.is_main(root):
            return None
    if _eq(root, main):
        return None
    info = mains.get(root)
    if _eq(info.pair_asset, pair):
        return None
    return {"asset": info.pair_asset, "symbol": info.pair_symbol, "factory_token": factory_token}
_MAX_PRESALE_TOKENS = MAX_UINT256 // 10 ** 18
_IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}

_GATE_MAX_NAME = 200
_GATE_MAX_DESCRIPTION = 2000
_GATE_MAX_OUTCOMES = 150
_GATE_MAX_OUTCOME_LEN = 120
_GATE_MIN_END_AHEAD_S = 60 * 60 + 60
_GATE_MAX_END_AHEAD_S = 1825 * 86_400
_GATE_SYMBOL = re.compile(r"[A-Za-z0-9]{1,16}")

_ART_ID_RE = re.compile(r"(answers|coins|crypto|money|politics|flags|sports|weather|science|tech|entertainment|time|"
                        r"animals|shapes|glyphs)/[a-z0-9][a-z0-9-]{0,47}")
_MAX_ART_ENTRIES = 200
_ART_SVG_SETS = frozenset(("coins", "flags", "glyphs"))
_ART_PROBE_ID = "answers/yes"
_ART_CHECK_TIMEOUT_MS = 10_000
_ART_CHECK_CONCURRENCY = 8

HumanAmount = Union[int, str, float, Decimal]

BUY_BLOCK_REASONS = (
    "host paused",
    "market resolved",
    "Betting closed",
    "Outcome proposed",
    "voting started",
    "outcome",
    "not whitelisted",
    "not invited",
    "convert closed",
    "pair not active",
    "Trading is not enabled",
    "pair asset paused",
)


def _main_arg(m: Any) -> Any:
    """A caller's ``main``: an address (strict: :func:`to_address`) or an address-book key, passed on as given."""
    return to_address(m, "main") if isinstance(m, str) and m[:2].lower() == "0x" else m


def _is_int(v: Any) -> bool:
    """The SDK's strict whole-number rule (``core.ranking.whole_number``): an int (not bool) or a whole float."""
    return whole_number(v) is not None


def _kind_of(v: Any) -> str:
    """What a value is in a refusal message, in the words of TS ``typeof`` (as the API's outcomes check words it):
    None is "null"."""
    if v is None:
        return "null"
    return {str: "string", bool: "boolean", int: "number", float: "number"}.get(type(v), "object")


def _check_market_text(name: Any, symbol: Any, outcomes: Any, required: bool = False) -> None:
    """The market's strings go on chain and into the approval hash, checked in this order: ``name``, then ``symbol``,
    each a str (None passes unless ``required``, then "<field> is required.", as does one that strips to nothing), then
    ``outcomes``, a list/tuple of str (the check and message of the API's ``approve_market``). Anything else is
    refused, never converted to text ("5", "None", "True" on chain): the rule is the field ('name', 'symbol',
    'outcomes'). Each must also be well-formed text (:func:`check_text`): a lone surrogate cannot be encoded as UTF-8
    (TS would send it as U+FFFD, another string than the one typed). Checked before any read, review or approval. Same
    rules and messages in TS."""
    for v, field in ((name, "name"), (symbol, "symbol")):
        if v is not None and not isinstance(v, str):
            raise ValidationError(field, f"{field} must be a string, got {_kind_of(v)}.")
        check_text(v, field)
        if required and (not isinstance(v, str) or not v.strip(_ASCII_WS)):
            raise ValidationError(field, f"{field} is required.")
    if not isinstance(outcomes, (list, tuple)) or any(not isinstance(o, str) for o in outcomes):
        got = "an array with a non-string entry" if isinstance(outcomes, (list, tuple)) else _kind_of(outcomes)
        raise ValidationError("outcomes", f"outcomes must be an array of answer strings, got {got}.")
    for o in outcomes:
        check_text(o, "outcomes")


def _check_gate_inputs(name: str, symbol: str, outcomes: Sequence[str], description: Any, end_time: int,
                       now: int) -> None:
    """The approval request's input rules, on the strings as the API trims them (JS ``trim()``), with ``now`` its clock
    (unix seconds). Only checked when an approval is requested."""
    n = _js_trim(name)
    if not n or js_len(n) > _GATE_MAX_NAME:
        raise ValidationError("name", f"The market approval takes a name of 1-{_GATE_MAX_NAME} characters; got {js_len(n)}.")
    if not _GATE_SYMBOL.fullmatch(_js_trim(symbol)):
        raise ValidationError("symbol", f"The market approval takes a symbol of 1-16 letters or digits; got {json.dumps(symbol)}.")
    d = _js_trim(description) if isinstance(description, str) else ""
    if js_len(d) > _GATE_MAX_DESCRIPTION:
        raise ValidationError("description", f"The market approval takes a description of at most {_GATE_MAX_DESCRIPTION} "
                                             f"characters; got {js_len(d)}.")
    if len(outcomes) > _GATE_MAX_OUTCOMES:
        raise ValidationError("outcomes", f"The market approval takes at most {_GATE_MAX_OUTCOMES} outcomes; got {len(outcomes)}.")
    for i, o in enumerate(outcomes):
        t = _js_trim(o)
        if not t or js_len(t) > _GATE_MAX_OUTCOME_LEN:
            raise ValidationError("outcomes", f"The market approval takes outcome labels of 1-{_GATE_MAX_OUTCOME_LEN} "
                                              f"characters; outcome {i} has {js_len(t)}.")
    if end_time != 0 and end_time < now + _GATE_MIN_END_AHEAD_S:
        raise ValidationError("endTime", f"The market approval needs endTime 0 (open-ended) or at least "
                                         f"{_GATE_MIN_END_AHEAD_S} s ahead ({now + _GATE_MIN_END_AHEAD_S}); got {end_time}.")
    if end_time > now + _GATE_MAX_END_AHEAD_S:
        raise ValidationError("endTime", f"The market approval needs endTime within 1825 days "
                                         f"({now + _GATE_MAX_END_AHEAD_S}); got {end_time}.")


def _check_outcome_art(art: Any, labels: Optional[Sequence[str]]) -> None:
    """``outcome_art`` as POST /api/metadata reads it: a dict of {<outcome label>: <picture id>} strings, at most 200
    entries, each id well formed (``<category>/<name>``), each picture once, each key an outcome of ``labels``
    (compared trimmed with JS ``trim()``, as the site matches them; None = the shape only). The API drops a bad pick
    silently (that outcome just gets no picture) and the metadata is set once, so a bad pick is refused before any
    approval request or tx: its shape here, and an id the library does not have in ``_check_art_library``."""
    if not isinstance(art, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in art.items()):
        raise ValidationError("outcomeArt", "outcome_art must be a dict of {<outcome label>: <picture id>} strings.")
    if len(art) > _MAX_ART_ENTRIES:
        raise ValidationError("outcomeArt", f"outcome_art takes at most {_MAX_ART_ENTRIES} pictures; got {len(art)}.")
    names = {_js_trim(str(lb)) for lb in labels} if labels is not None else None
    seen_names, seen_ids = set(), set()
    for key, pid in art.items():
        if not _ART_ID_RE.fullmatch(pid):
            raise ValidationError("outcomeArt", f"outcome_art[{json.dumps(key)}] is {json.dumps(pid)}, not a picture id of "
                                                "the library (like 'answers/yes').")
        if pid in seen_ids:
            raise ValidationError("outcomeArt", f"Picture {pid} is picked twice: each picture can be used once per market.")
        seen_ids.add(pid)
        n = _js_trim(key)
        if names is not None and n not in names:
            raise ValidationError("outcomeArt", f"outcome_art names {json.dumps(key)}, which is not an outcome of this market.")
        if n in seen_names:
            raise ValidationError("outcomeArt", f"outcome_art names outcome {json.dumps(n)} twice.")
        seen_names.add(n)


def _upload_error_of(stage: str, e: BaseException) -> Dict[str, Any]:
    """A failed upload as an ``upload_error`` dict (the original exception kept as ``cause``)."""
    message = str(e)
    if message.startswith("[basis-sdk] "):
        message = message[len("[basis-sdk] "):]
    if isinstance(e, BasisApiError):
        return {"stage": stage, "code": e.code, "status": e.status, "message": message, "cause": e}
    if isinstance(e, AuthRequiredError):
        return {"stage": stage, "code": "auth_required", "status": None, "message": message, "cause": e}
    return {"stage": stage, "code": None, "status": None, "message": message, "cause": e}


def _refuse_above_quote(explicit: Optional[int], quoted: int, field: str, what: str) -> None:
    """Refuses an explicit floor (``explicit``, raw; None = not given) above ``quoted``, what the SDK's exact quote shows
    the write gets now (``what`` names it): the host would refuse it with no reason, so it is
    ``ValidationError(field)`` before any approval. A floor at or under the quote passes. Same text in TS."""
    if explicit is not None and explicit > quoted:
        raise ValidationError(field, f"{field} {explicit} is above {what} now ({quoted}), so it would be refused: lower "
                                     "it, or omit it to derive it from the quote.")


_DEAD_APPROVAL_REASONS = ("Approval expired", "Approval already used")


def _dead_approval(e: BaseException) -> bool:
    """A refusal that says the approval itself can no longer be used: it expired, or its signed endTime is too close now
    (the SDK's own checks), or the host reverted the create with 'Approval expired' / 'Approval already used'. Such an
    error carries no approval. TS: deadApproval."""
    if isinstance(e, ValidationError):
        return e.rule in ("Approval expired", "endTime")
    return isinstance(e, BasisContractError) and e.reason in _DEAD_APPROVAL_REASONS


def _hand_back(e: BaseException, approval: Optional[Dict[str, Any]]) -> None:
    """Attaches ``approval`` to ``e`` as ``e.approval`` (TS: the error's ``approval``): the valid approval a failed
    create did not use up, for a retry (``create_market(..., approval=e.approval)``)."""
    if approval is not None:
        try:
            e.approval = approval  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass


def _eq(a: str, b: str) -> bool:
    return a.lower() == b.lower()


class MarketsBase(BaseModule):
    """Shared logic of the two prediction-market hosts, public and private."""

    market_type: str = ""
    _contract_name: str = ""


    @property
    def _host_entry(self) -> Optional[str]:
        """The host address from the address book (may be zero)."""
        raise NotImplementedError

    def _min_seed_units(self, private_event: bool) -> int:
        """The host's seed floor, before it is sized to the main."""
        raise NotImplementedError

    def _precheck_create_host(self, host: str) -> None:
        """Host-specific create pre-checks."""
        raise NotImplementedError

    def _precheck_buy_host(self, market: str, md: Dict[str, Any], buyer: str,
                           closed: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        """Host-specific buy rules in the host's own order, with ``closed`` (the shared end_time rule, None while a dated
        market is open and on an open-ended one) placed where the host checks it: the first rule that refuses
        ``buyer`` as {'reason', 'message'}, or None."""
        raise NotImplementedError

    def _send_create(self, c: Dict[str, Any]) -> Dict[str, Any]:
        """Send ``createMarket`` with the host's own struct. ``c`` keys: name, symbol, outcomes, end_time, tranche_bp,
        main, frozen, presale_tokens, seed, private_event, deadline, signature, value."""
        raise NotImplementedError

    def get_final_tie_mask(self, market: str) -> int:
        """Tie bits of the result (bit i: ranking entry i tied with entry i-1). 0 when unresolved, INVALID or untied."""
        raise NotImplementedError


    @property
    def deployed(self) -> bool:
        """True when this host has an address on this chain."""
        return not is_zero_address(self._host_entry)

    @property
    def address(self) -> str:
        """The host contract address (raises NotDeployedError when zero)."""
        return self._require(self._host_entry, self._contract_name)

    @property
    def _host(self):
        return self._contract(self.address, "TrancheMarket")

    @property
    def _reader(self):
        r = getattr(self.client, "market_reader", None)
        if r is None:
            raise BasisError("[basis-sdk] client.market_reader is not available; it is needed for market sizes and quotes.")
        return r

    def _chain_time(self) -> int:
        return self.web3.eth.get_block("latest")["timestamp"]

    def _pair_active(self, main: str, swap: Optional[str] = None) -> bool:
        """Is ``main``'s pair active, read now? A paused pair refuses bets, seeds and redeems ("unknown pair" on
        chain)."""
        s = swap if swap is not None else self._host.functions.ecosystems(main).call()[1]
        if is_zero_address(s):
            return False
        return bool(self._contract(s, "Swap").functions.isMain(main).call())


    def _create_market_internal(
        self,
        private_event: bool,
        name: str,
        symbol: str,
        outcomes: Sequence[str],
        end_time: int,
        tranche_bp: Optional[Sequence[Union[int, float]]] = None,
        main: Optional[str] = None,
        frozen: bool = False,
        presale_tokens: int = 0,
        seed: Optional[HumanAmount] = None,
        description: Optional[str] = None,
        approval: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a market. Pre-checks every host rule, then (gating on) gets an approval from the API unless one is
        passed, verifies it locally and sends the approved strings verbatim; approves the seed in the pair asset and
        sends the exact creation fee."""
        host = self.address
        sender = self._account
        h = self._contract(host, "TrancheMarket")

        _check_market_text(name, symbol, outcomes, True)
        name = name.strip(_ASCII_WS)
        symbol = symbol.strip(_ASCII_WS)
        outcomes = [o.strip(_ASCII_WS) for o in outcomes]
        if any(not o for o in outcomes):
            raise ValidationError("outcomes", "Outcome labels must not be empty.")
        end_time = to_uint(end_time, "endTime")
        if tranche_bp is not None and not isinstance(tranche_bp, (list, tuple)):
            raise ValidationError("trancheBP", "tranche_bp must be a list of whole numbers of bps.")
        tranche_bp = list(tranche_bp) if tranche_bp is not None else [10000]
        frozen = to_bool(frozen, "frozen", False)
        presale_tokens = to_uint(presale_tokens if presale_tokens is not None else 0, "presaleTokens")
        if presale_tokens > _MAX_PRESALE_TOKENS:
            raise ValidationError("presaleTokens", f"presaleTokens counts whole tokens and is at most {_MAX_PRESALE_TOKENS}; "
                                                   f"got {presale_tokens}.")

        main = _main_arg(main) if main else None
        try:
            info: MainInfo = self.client.mains.get(main) if main else self.client.mains.default()
        except BasisError as e:
            if not main or isinstance(e, NotDeployedError):
                raise
            msg = str(e)
            if msg.startswith("[basis-sdk] "):
                msg = msg[len("[basis-sdk] "):]
            raise ValidationError("unknown main", f"{main} is not a main: {msg}") from e
        main_addr = info.main

        paused = h.functions.paused().call()
        gating = h.functions.gatingEnabled().call()
        signer = h.functions.moderatorSigner().call()
        max_outcomes = h.functions.MAX_OUTCOMES().call()
        eco_factory, eco_swap, eco_asset, eco_active = h.functions.ecosystems(main_addr).call()
        min_units = self._min_seed_units(private_event)
        now = self._chain_time()

        if paused:
            raise ValidationError("host paused", f"The {self.market_type} market host is paused: no new markets.")
        if not eco_active or is_zero_address(eco_factory):
            raise ValidationError(
                "ecosystem inactive",
                f"Main {info.symbol} ({main_addr}) is not an active ecosystem on the {self.market_type} market host.",
            )
        self._precheck_create_host(host)
        quote_token = self._contract(main_addr, "EcosystemToken").functions.quoteToken().call()
        if not _eq(quote_token, eco_asset):
            raise ValidationError(
                "asset mismatch",
                f"The host registered {eco_asset} as {info.symbol}'s pair asset, but the main pairs with {quote_token}.",
            )

        def check_end_time(t: int) -> None:
            if end_time != 0 and end_time < t + 61:
                raise ValidationError(
                    "endTime",
                    f"endTime must be 0 (open-ended) or at least 60 s ahead of the next block ({t + 61}); got {end_time}.",
                )

        check_end_time(now)
        outcome_cap = min(max_outcomes, HARD_MAX_OUTCOMES)
        tranche_bp = self._check_outcomes_and_tranches(len(outcomes), tranche_bp, outcome_cap)
        if gating and approval is None:
            _check_gate_inputs(name, symbol, outcomes, description, end_time, max(now, int(time.time())))

        sizes = self._reader.market_sizes(host, main_addr, len(outcomes))
        floor = min_units * sizes["unit"]
        step = sizes["seed_step"]
        if isinstance(seed, bool):
            raise ValidationError("seed", "seed must be an amount, not a boolean.")
        seed_raw = floor if seed is None else parse_amount(seed, info.pair_decimals, "seed")
        if seed_raw < 0:
            raise ValidationError("seed", "seed must not be negative.")
        if seed_raw < floor or (seed_raw != 0 and step > 0 and seed_raw % step != 0):
            f = pair_amount(floor, info)
            st = pair_amount(step, info)
            raise ValidationError(
                "seed",
                f"seed must be at least {f.formatted} {f.symbol} and a multiple of {st.formatted} {st.symbol}"
                f"{' (or 0)' if floor == 0 else ''}; got {pair_amount(seed_raw, info).formatted}.",
            )
        if not self._pair_active(main_addr, eco_swap):
            raise ValidationError(
                "pair not active",
                f"Main {info.symbol} ({main_addr}) is on a paused pair: no market can be created on it until the pair is "
                "active again.",
            )
        if frozen and presale_tokens == 0:
            raise ValidationError("frozen needs presale", "A frozen (invite-only) market token needs presaleTokens > 0.")
        taxes = self.client.addresses.get("taxes")
        if (not is_zero_address(taxes) and seed_raw > 0
                and not self._contract(taxes, "Taxes").functions.isPrediction(host).call()):
            raise ValidationError(
                "convert closed",
                f"The {self.market_type} host {host} cannot convert a seed right now: no market can be seeded.",
            )
        if seed_raw > 0 and not self._contract(main_addr, "MainToken").functions.tradingEnabled().call():
            raise ValidationError(
                "Trading is not enabled",
                f"Trading on main {info.symbol} ({main_addr}) has not opened yet: no seeded market can be created on it "
                "until it does.",
            )
        if seed_raw > 0:
            refuse_paused_asset(self.client, eco_asset, info.pair_symbol, "no seed can be paid in")

        fac = self._contract(eco_factory, "Factory")
        fee_whitelisted = fac.functions.feeWhitelist(host).call()
        fee_amount = fac.functions.feeAmount().call()
        value = 0 if fee_whitelisted else fee_amount
        seed_balance = self._balance_of(eco_asset, sender) if seed_raw > 0 else 0
        native_balance = self.web3.eth.get_balance(sender) if value > 0 else 0
        if seed_balance < seed_raw:
            raise ValidationError(
                "insufficient balance",
                f"The seed needs {pair_amount(seed_raw, info).formatted} {info.pair_symbol}; "
                f"the wallet holds {pair_amount(seed_balance, info).formatted}.",
            )
        if native_balance < value:
            raise ValidationError(
                "insufficient balance",
                f"The creation fee is {value} wei of the native coin; the wallet holds {native_balance}.",
            )

        used: Optional[Dict[str, Any]] = None
        sent: Dict[str, Any] = {"name": name, "symbol": symbol, "outcomes": outcomes}
        if gating:
            if is_zero_address(signer):
                raise ValidationError(
                    "Signer unset",
                    f"The {self.market_type} market host cannot check market approvals yet: no market can be created yet.",
                )
            content_hash: Optional[str] = None
            if approval is not None:
                used = approval
            else:
                res = self.client.api.approve_market(
                    market_type=self.market_type,
                    name=name,
                    symbol=symbol,
                    description=description,
                    outcomes=outcomes,
                    end_time=end_time,
                    main=main_addr,
                    seed_amount=seed_raw,
                    private_event=private_event if self.market_type == "private" else None,
                )
                if not res.get("approved"):
                    cat = f" ({res['category']})" if res.get("category") else ""
                    raise ValidationError(
                        "market rejected",
                        f"BASIS did not approve the market{cat}: {res.get('reason') or 'no reason given'}.",
                    )
                if not _eq(res["verifying_contract"], host):
                    raise ValidationError(
                        "Invalid approval",
                        f"The approval is for host {res['verifying_contract']}, not the {self.market_type} host {host}.",
                    )
                if res.get("chain_id") is not None and res["chain_id"] != self.client.chain_id:
                    raise ValidationError(
                        "Invalid approval", f"The approval is for chain {res['chain_id']}, not {self.client.chain_id}."
                    )
                used = {"signature": res["signature"], "deadline": res["deadline"], "signed": res["signed"]}
                content_hash = res.get("content_hash")
            signed = used["signed"]
            sent = {"name": signed["name"], "symbol": signed["symbol"], "outcomes": list(signed["outcomes"])}
            if signed.get("endTime") is not None and int(signed["endTime"]) != end_time:
                raise ValidationError(
                    "Invalid approval", f"The approval signs endTime {signed['endTime']}, but {end_time} was requested."
                )
            if used["deadline"] <= now:
                raise ValidationError(
                    "Approval expired", f"The approval expired at {used['deadline']} (chain time {now}). Request a new one."
                )
            self._check_outcomes_and_tranches(len(sent["outcomes"]), tranche_bp, outcome_cap)
            if len(sent["outcomes"]) != len(outcomes):
                raise ValidationError(
                    "Invalid approval",
                    f"The approval signs {len(sent['outcomes'])} outcomes, but {len(outcomes)} were requested.",
                )
            verify_market_approval(
                host=host,
                chain_id=self.client.chain_id,
                creator=sender,
                name=sent["name"],
                symbol=sent["symbol"],
                outcomes=sent["outcomes"],
                deadline=used["deadline"],
                signature=used["signature"],
                expected_signer=signer,
                content_hash=content_hash,
                end_time=end_time,
            )

        try:
            if used is not None or end_time != 0:
                t = self._chain_time()
                check_end_time(t)
                if used is not None and used["deadline"] <= t:
                    raise ValidationError(
                        "Approval expired", f"The approval expired at {used['deadline']} (chain time {t}). Request a new one."
                    )
            if seed_raw > 0:
                self._ensure_allowance(eco_asset, host, seed_raw)
        except Exception as e:
            if not _dead_approval(e):
                _hand_back(e, used)
            raise
        pair_of = pair_amount_of(info)

        try:
            tx = self._send_create({
                "name": sent["name"],
                "symbol": sent["symbol"],
                "outcomes": sent["outcomes"],
                "end_time": end_time,
                "tranche_bp": tranche_bp,
                "main": main_addr,
                "frozen": frozen,
                "presale_tokens": presale_tokens,
                "seed": seed_raw,
                "private_event": private_event,
                "deadline": used["deadline"] if used is not None else 0,
                "signature": used["signature"] if used is not None else "0x",
                "value": value,
            })
        except TxNotConfirmedError:
            raise
        except Exception as e:
            if not _dead_approval(e):
                _hand_back(e, used)
            raise

        created = next((e for e in self._events(tx, h, "MarketCreated", host) if _eq(e["creator"], sender)), None)
        if created is None:
            add_missing_event_warning(tx, self._contract_name, "MarketCreated", ("market", "frozen"))
        market = Web3.to_checksum_address(created["marketToken"]) if created else None
        seeded = (next((e for e in self._events(tx, h, "MarketSeeded", host) if _eq(e["marketToken"], market)), None)
                  if market else None)
        if market and seeded is None and seed_raw > 0:
            add_missing_event_warning(tx, self._contract_name, "MarketSeeded", ("seed_amount", "seed_tokens", "shares_per_outcome"))
        no_seed = bool(market) and seeded is None and seed_raw == 0
        frozen_now: Optional[bool] = None
        if market:
            try:
                frozen_now = self._contract(market, "FactoryToken").functions.frozen().call()
            except Exception as e:  # noqa: BLE001
                msg = (f"Could not read frozen() of the new market {market} after tx {tx['hash']}: {redact_urls(e)}. "
                       "The transaction is confirmed: do not resend it.")
                tx.setdefault("warnings", []).append(msg)
                logger.warning("[basis-sdk] %s", msg)

        def seeded_field(name: str) -> Optional[int]:
            return seeded[name] if seeded else (0 if no_seed else None)

        def tokens_of(raw: int) -> Amount:
            return to_amount(raw, 18, "token", market, sent["symbol"])

        return {
            **tx, "market": market, "approval": used, "sent": sent,
            "seed_amount": opt_amount(pair_of, seeded_field("seedAmount")),
            "seed_tokens": opt_amount(tokens_of, seeded_field("seedToken")),
            "shares_per_outcome": seeded_field("sharesPerOutcome"), "frozen": frozen_now,
        }

    def _create_market_with_metadata_internal(
        self,
        private_event: bool,
        image_url: Optional[str] = None,
        image_file: Optional[Any] = None,
        website: Optional[str] = None,
        telegram: Optional[str] = None,
        twitterx: Optional[str] = None,
        outcome_art: Optional[Dict[str, str]] = None,
        **params: Any,
    ) -> Dict[str, Any]:
        """create_market, then the image upload and metadata registration. Checked before any approval request or gas:
        the image argument, the metadata fields (description 5000 characters; website an https link, telegram a t.me
        link or handle, twitterx an X handle, as the API checks them), ``outcome_art`` against the outcome labels and the
        site's picture library, the API credentials (an API key or a SIWE session), the image itself (an ``image_url`` is
        downloaded and resized to 512x512 WebP here; an ``image_file`` (a local path or the bytes) must be png, jpeg, gif
        or webp by its bytes and at most 5 MB), then every create rule. ``outcome_art``: outcome pictures from the site's
        library, {<outcome label>: <picture id>} (e.g. {"Yes": "answers/yes"}), sent as ``outcomeArt``: each key an
        outcome label, each id a library id, each picture once, and each id one the site's library has (its picture file
        is looked up on the API's site: an id it does not have is refused, ``ValidationError('outcomeArt')`` naming them;
        when that lookup gets no answer, or the site serves no picture library, nothing is sent:
        ``ValidationError('outcomeArtUnreachable')``, try again); set once with the metadata. Never raises after the
        create tx: a failed upload comes back in
        ``upload_error`` ({stage: 'market' | 'image' | 'metadata', code, status, message, cause}, with a warning);
        finish it with ``retry_market_metadata(market, ...)``. Calling this again would create a second market. A
        create whose receipt cannot be read raises ``TxNotConfirmedError`` whose ``note`` points to
        ``retry_market_metadata`` (no upload was made); an unconfirmed seed approval created nothing, so its exception
        has no note (and carries the approval as ``e.approval``)."""
        if not image_url and image_file is None:
            raise ValidationError("imageRequired", "Either image_url or image_file is required.")
        if image_url and image_file is not None:
            raise ValidationError("imageRequired", "Give image_url or image_file, not both.")
        fields = {"description": params.get("description"), "website": website, "telegram": telegram, "twitterx": twitterx}
        _check_metadata_fields(fields)
        _check_market_text(params.get("name"), params.get("symbol"), params.get("outcomes"))
        if outcome_art is not None:
            _check_outcome_art(outcome_art, list(params["outcomes"]))
        self.client.api.check_upload_auth()
        if outcome_art is not None:
            self._check_art_library(outcome_art)
        image = self._prepare_image(image_url, image_file)

        try:
            created = self._create_market_internal(private_event, **params)
        except TxNotConfirmedError as e:
            if e.function_name != "createMarket":
                raise
            raise e.with_note(
                "The image and metadata were NOT uploaded (they need the market address). Once the tx is mined, read the "
                "market (its MarketCreated log, or client.api.get_wallet_markets) and call retry_market_metadata(market, "
                "...); do not create the market again.") from e
        market = created["market"]
        if not market:
            err = {"stage": "market", "code": None, "status": None,
                   "message": f"the market address is unknown (no MarketCreated log in {created['hash']}): find it with "
                              "client.api.get_wallet_markets or the host's events, then call retry_market_metadata."}
            created["warnings"].append(f"Market created (tx {created['hash']}) but {err['message']} Do not create it again.")
            return {**created, "image_url": None, "metadata": None, "upload_error": err}
        uploaded = self._upload_market_metadata(market, image, None, fields, outcome_art)
        if uploaded["upload_error"]:
            e = uploaded["upload_error"]
            created["warnings"].append(
                f"Market {market} was created (tx {created['hash']}) but the {e['stage']} upload failed: {e['message']} "
                "Finish it with retry_market_metadata(market, ...); do not create the market again."
            )
        else:
            rc = created.get("receipt")
            rc = rc if hasattr(rc, "get") else {}
            self.client.sync_tx(created["hash"], meta={"from": rc.get("from"), "to": rc.get("to")})
        return {**created, **uploaded}

    def retry_market_metadata(self, market: str, image_url: Optional[str] = None, image_file: Optional[Any] = None,
                              hosted_image_url: Optional[str] = None, description: Optional[str] = None,
                              website: Optional[str] = None, telegram: Optional[str] = None,
                              twitterx: Optional[str] = None,
                              outcome_art: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Finish the uploads of a market whose ``create_market_with_metadata`` returned an ``upload_error`` (or any
        market the signer created on this host). Give exactly one image source: ``image_url`` / ``image_file``
        (uploaded as in create_market_with_metadata) or ``hosted_image_url`` (an image already hosted for it). Same
        pre-checks (``outcome_art`` against the market's outcome names on chain and the site's picture library: a lookup
        that gets no answer refuses with 'outcomeArtUnreachable' before any upload); sends no tx. Upload failures come
        back in ``upload_error`` (no raise). Returns {'image_url', 'metadata', 'upload_error'}."""
        market = to_address(market, "market")
        sources = [v for v in (image_url, image_file, hosted_image_url) if v is not None and v != ""]
        if len(sources) != 1:
            raise ValidationError("imageRequired", "Give exactly one of image_url, image_file or hosted_image_url.")
        fields = {"description": description, "website": website, "telegram": telegram, "twitterx": twitterx}
        _check_metadata_fields(fields)
        if hosted_image_url and js_len(hosted_image_url) > METADATA_MAX_LENGTH["image"]:
            raise ValidationError(
                "metadataTooLong",
                f"hostedImageUrl is {js_len(hosted_image_url)} characters; the API takes at most {METADATA_MAX_LENGTH['image']}.",
            )
        if outcome_art is not None:
            _check_outcome_art(outcome_art, None)
        self._require_creator(market)
        if outcome_art is not None:
            _check_outcome_art(outcome_art, self.get_option_names(market))
        self.client.api.check_upload_auth()
        if outcome_art is not None:
            self._check_art_library(outcome_art)
        image = None if hosted_image_url else self._prepare_image(image_url, image_file)
        return self._upload_market_metadata(market, image, hosted_image_url, fields, outcome_art)

    def _check_art_library(self, art: Dict[str, str]) -> None:
        """Each picked picture id against the site's library: a HEAD of its file on the API's site
        (``/outcome-art/<id>.svg`` or ``.webp``: the format the site serves that set in first, the other one after a
        404), a few at a time. Runs after ``_check_outcome_art`` (ids well formed, each once) and before any approval
        request, upload or tx; refuses, never warns:

        - ids answered 404 / 410 in both formats: ``ValidationError('outcomeArt')`` naming them. When no picked id was
          found, a picture every library has (``answers/yes``) is looked up too: not found as well means the site serves
          no library (or did not answer), which is refused as below;
        - ids that got no clear answer (network error, timeout, another status), or a site without the library:
          ``ValidationError('outcomeArtUnreachable')`` naming them. Nothing was sent: try again."""
        ids = list(art.values())

        def head(path: str) -> Tuple[str, str]:
            """One file: ('ok', ''), ('missing', '') for 404 / 410, or ('unreachable', why)."""
            try:
                res = request_with_deadline(None, "HEAD", f"{self.client.api_domain}{path}", _ART_CHECK_TIMEOUT_MS,
                                            f"HEAD {path}", allow_redirects=True)
            except Exception as e:  # noqa: BLE001
                msg = redact_urls(e)
                return "unreachable", msg[len("[basis-sdk] "):] if msg.startswith("[basis-sdk] ") else msg
            if 200 <= res.status_code < 300:
                return "ok", ""
            if res.status_code in (404, 410):
                return "missing", ""
            return "unreachable", f"HTTP {res.status_code}"

        def one(pid: str) -> Tuple[str, str]:
            """One id: its set's format first, the other format after a 404."""
            exts = ("svg", "webp") if pid.split("/")[0] in _ART_SVG_SETS else ("webp", "svg")
            first = head(f"/outcome-art/{pid}.{exts[0]}")
            return head(f"/outcome-art/{pid}.{exts[1]}") if first[0] == "missing" else first

        with ThreadPoolExecutor(max_workers=max(1, min(_ART_CHECK_CONCURRENCY, len(ids)))) as pool:
            answers = dict(zip(ids, pool.map(one, ids)))

        def unreachable(which: List[str], why: str) -> ValidationError:
            return ValidationError(
                "outcomeArtUnreachable",
                f"Could not reach the site's picture library to check outcome picture{'' if len(which) == 1 else 's'} "
                f"{', '.join(which)} ({why}). Nothing was sent: try again.",
            )

        missing = [pid for pid in ids if answers[pid][0] == "missing"]
        if missing and not any(answers[pid][0] == "ok" for pid in ids):
            probe = answers.get(_ART_PROBE_ID) or one(_ART_PROBE_ID)
            if probe[0] != "ok":
                raise unreachable(ids, f"{self.client.api_domain} serves no picture library at /outcome-art"
                                       if probe[0] == "missing" else probe[1])
        if missing:
            what = "a picture" if len(missing) == 1 else "pictures"
            raise ValidationError("outcomeArt", f"outcomeArt picks {what} the site's library does not have: "
                                                f"{', '.join(missing)}. Nothing was sent.")
        unchecked = [pid for pid in ids if answers[pid][0] != "ok"]
        if unchecked:
            raise unreachable(unchecked, answers[unchecked[0]][1])

    def _prepare_image(self, image_url: Optional[str], image_file: Optional[Any]) -> Tuple[bytes, str]:
        """The image bytes to upload and their type, checked before any tx: downloaded and resized (``image_url``) or
        as given (``image_file``: a local path or the bytes)."""
        if image_file is not None:
            if isinstance(image_file, (str, os.PathLike)):
                with open(os.fspath(image_file), "rb") as f:
                    data = f.read()
            elif isinstance(image_file, (bytes, bytearray, memoryview)):
                data = bytes(image_file)
            else:
                raise ValidationError("imageType", f"image_file must be a file path or bytes (got {type(image_file).__name__}).")
        else:
            data = self.client.api.download_image(image_url)
        mime = sniff_image(data[:12])
        if not mime:
            raise ValidationError("imageType", "The image must be png, jpeg, gif or webp (told by its bytes).")
        if len(data) > IMAGE_MAX_BYTES:
            raise ValidationError(
                "imageTooLarge", f"The image is {len(data)} bytes; the API takes at most 5 MB ({IMAGE_MAX_BYTES} bytes)."
            )
        return data, mime

    def _upload_market_metadata(self, market: str, image: Optional[Tuple[bytes, str]], hosted: Optional[str],
                                fields: Dict[str, Optional[str]],
                                outcome_art: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Upload the image (unless already hosted) and the metadata of ``market``. Never raises: failures go in
        ``upload_error``."""
        image_url = hosted
        try:
            if image is not None:
                data, mime = image
                image_url = self.client.api.upload_image(data, filename=f"{market}.{_IMAGE_EXT[mime]}", purpose="token",
                                                         address=market)
        except Exception as e:  # noqa: BLE001
            return {"image_url": None, "metadata": None, "upload_error": _upload_error_of("image", e)}
        try:
            art = {"outcome_art": outcome_art} if outcome_art is not None else {}
            metadata = self.client.api.update_metadata(address=market, image=image_url, **fields, **art)
            return {"image_url": image_url, "metadata": metadata, "upload_error": None}
        except Exception as e:  # noqa: BLE001
            return {"image_url": image_url, "metadata": None, "upload_error": _upload_error_of("metadata", e)}

    def _check_outcomes_and_tranches(self, n: int, bp: Sequence[Any], cap: int) -> List[int]:
        """2..cap outcomes; tranche_bp: 1-10 entries, <= outcomes, each at least n bps, non-increasing, sum 10000.
        Returns the slices as ints."""
        if n < 2 or n > cap:
            raise ValidationError("outcomes", f"A market needs 2-{cap} outcomes; got {n}.")
        if len(bp) < 1 or len(bp) > MAX_TRANCHES:
            raise ValidationError("trancheBP", f"trancheBP needs 1-{MAX_TRANCHES} entries; got {len(bp)}.")
        if len(bp) > n:
            raise ValidationError("trancheBP", f"trancheBP has {len(bp)} slices but the market has only {n} outcomes.")
        out: List[int] = []
        total = 0
        for i, b in enumerate(bp):
            whole = _is_int(b) or (isinstance(b, float) and b.is_integer())
            if not whole or b <= 0 or b > 10000:
                raise ValidationError("trancheBP", f"trancheBP[{i}] must be a whole number of bps above 0; got {b}.")
            b = int(b)
            if b < n:
                raise ValidationError("trancheBP", f"Every slice must be at least {n} bps on a market of {n} outcomes; trancheBP[{i}] is {b}.")
            if i > 0 and b > out[i - 1]:
                raise ValidationError(
                    "trancheBP", f"trancheBP must never increase: entry {i} ({b}) is above entry {i - 1} ({out[i - 1]})."
                )
            out.append(b)
            total += b
        if total != 10000:
            raise ValidationError("trancheBP", f"trancheBP must sum to 10000; got {total}.")
        return out


    def buy(
        self,
        market: str,
        outcome: int,
        amount: HumanAmount,
        input: Optional[str] = None,  # noqa: A002
        min_shares: Optional[int] = None,
        min_tokens: Optional[int] = None,
        slippage_bps: Optional[int] = None,
        deadline: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Bet on ``outcome``. ``amount``: int = raw input-token units, str/float/Decimal = human amount in the input's
        decimals. ``input``: the token paid in (default the pair asset; also the market's main, the market token, any
        BASIS token or a routed outside token). Raises ``ValidationError(reason)`` when ``check_buy`` refuses the bet. The
        minimums come from ``market_reader.quote_bet`` (never 0); explicit ``min_shares`` / ``min_tokens`` must be whole
        numbers above 0 (``ValidationError('minShares' / 'minTokens')``, checked before any read). The quote always runs,
        so the minimum-bet and fee-precision rules are checked even when both minimums are given. On a direct route (the
        input is the pair asset, the main or the market token) an explicit floor above what the quote shows the bet gets
        now is refused under the same rule, before the approval (the host would refuse it with no reason). The input is
        approved to the host. ``deadline``: unix seconds
        (chain time) after which the bet reverts "Deadline" (default latest block + 600 s); an explicit one before the
        latest block time is refused (``ValidationError("Deadline")``, as ``check_deadline``: the host takes a deadline up
        to and including the time of the block that runs it). Returns {'hash','receipt',
        'result','warnings','input','amount','min_shares','min_tokens','deadline','shares','tokens','net_tokens','fee'}:
        shares minted (raw, 18 decimals), market tokens the input converted to (before the fee), market tokens into the
        pot and the fee, from ``SharesTraded`` / ``BetFunded`` (None when the event is missing). ``amount`` is an
        ``Amount`` in the input token; ``min_tokens``, ``tokens``, ``net_tokens`` and ``fee`` are ``Amount``s in the market
        token (unit ``token``); ``min_shares`` and ``shares`` stay raw ints."""
        market = to_address(market, "market")
        input_in = None if input is None else to_address(input, "input")
        min_shares_in = opt_uint(min_shares, "minShares")
        min_tokens_in = opt_uint(min_tokens, "minTokens")
        if min_shares_in == 0:
            raise ValidationError("minShares", "A zero minShares is never sent; omit it to derive it from a quote.")
        if min_tokens_in == 0:
            raise ValidationError("minTokens", "A zero minTokens is never sent; omit it to derive it from a quote.")
        host = self.address
        buyer = self._account
        md = self._require_market(market)
        token_in = input_in if input_in is not None else md["pair_asset"]
        if isinstance(amount, bool):
            raise ValidationError("amount", "amount must be a number, not a boolean.")
        raw = amount if isinstance(amount, int) else parse_amount(amount, self.client.mains.token_meta(token_in)["decimals"])

        h = self._host
        block = self._buy_block(market, md, outcome, buyer, token_in)
        if block:
            raise ValidationError(block["reason"], block["message"])
        outcome = int(outcome)
        if raw <= 0:
            raise ValidationError("amount", "amount must be above 0.")
        explicit_deadline = None if deadline is None else to_uint(deadline, "deadline")
        if explicit_deadline is not None:
            check_deadline(explicit_deadline, self._chain_time())
        balance = self._balance_of(token_in, buyer)
        if balance < raw:
            raise ValidationError(
                "insufficient balance", f"The bet needs {raw} raw units of {token_in}; the wallet holds {balance}."
            )

        q = self._reader.quote_bet(
            market=market, outcome=outcome, input=token_in, amount=raw, host=host, bettor=buyer,
            slippage_bps=slippage_bps,
        )
        min_shares = min_shares_in if min_shares_in is not None else q["min_shares"]
        min_tokens = min_tokens_in if min_tokens_in is not None else q["min_tokens"]
        if min_shares <= 0:
            raise ValidationError("minShares", "The quote gives 0 shares: the bet is too small.")
        if min_tokens <= 0:
            raise ValidationError("minTokens", "The quote gives 0 market tokens: the bet is too small.")
        if q.get("route") == "direct":
            _refuse_above_quote(min_shares_in, q["shares"], "minShares", "the shares this bet buys")
            f = int(q["fee_bps"])
            tokens_max = (q["tokens"] if _eq(q["converted_token"], market) or f <= 0 or f >= 10_000
                          else (q["net_token"] * 10_000) // (10_000 - f))
            _refuse_above_quote(min_tokens_in, tokens_max, "minTokens", "the market tokens this bet converts to")

        self._ensure_allowance(token_in, host, raw)
        input_of, tokens_of = self._market_formatters(market, md, token_in)
        dl = explicit_deadline if explicit_deadline is not None else self._block_deadline(BUY_DEADLINE_SECONDS)
        tx = self._send(h.functions.buy(market, outcome, token_in, raw, min_tokens, min_shares, dl), self._contract_name)
        traded = next((e for e in self._events(tx, h, "SharesTraded", host)
                       if _eq(e["marketToken"], market) and _eq(e["buyer"], buyer) and e["outcomeId"] == outcome), None)
        funded = next((e for e in self._events(tx, h, "BetFunded", host)
                       if _eq(e["marketToken"], market) and _eq(e["buyer"], buyer)), None)
        if traded is None:
            add_missing_event_warning(tx, self._contract_name, "SharesTraded", ("shares", "net_tokens", "fee"))
        if funded is None:
            add_missing_event_warning(tx, self._contract_name, "BetFunded", ("tokens",))
        return {
            **tx, "input": token_in, "amount": input_of(raw), "min_shares": min_shares, "min_tokens": tokens_of(min_tokens),
            "deadline": dl, "shares": traded["shares"] if traded else None,
            "tokens": opt_amount(tokens_of, funded["tokens"] if funded else None),
            "net_tokens": opt_amount(tokens_of, traded["usdcSpent"] if traded else None),
            "fee": opt_amount(tokens_of, traded["fee"] if traded else None),
        }

    def _market_formatters(self, market: str, md: Dict[str, Any], token_in: Optional[str] = None) -> Tuple[AmountOf, AmountOf]:
        """Formatters for a market's write results, resolved before sending: ``token_in`` (default the market's pair
        asset) and the market token (unit ``token``). The pair asset is the market's own (``md['pair_asset']``)."""
        info = self.client.mains.get(md["main"])
        return (amount_of(self.client, token_in or md["pair_asset"], info), amount_of(self.client, market, info))

    def check_buy(self, market: str, outcome: int, buyer: Optional[str] = None,
                  input: Optional[str] = None) -> Dict[str, Any]:  # noqa: A002
        """Read-only bet eligibility: the rules ``buy`` checks before sending, in order. Host paused, market resolved,
        outcome range; then on the public host end_time and a live proposal (dated or open-ended), on the private host
        the invite list, end_time and the first vote (open-ended); then the frozen whitelist, whether bets can be paid in
        right now ('convert closed') and the market's main is on an active pair ('pair not active'; a bet paid in the
        market token itself with no fee is not affected), and, for a bet paid in anything but the market token or the
        main (or with a bet fee), whether the main's trading has opened ('Trading is not enabled') and whether an issuer
        has paused an asset the bet moves ('pair asset paused'): the pair asset of another pair that a BASIS token paid
        in is first sold for (selling a factory token also pays out the bettor's pending holder rewards in it), then the
        market's own pair asset. ``buyer`` defaults to the signer, ``input`` to
        the market's pair asset. Amount, balance and minimums are not checked. Returns
        {'can_bet', 'reason', 'message'}: ``reason`` is the first rule that refuses, named as ``buy`` would raise it (one
        of ``BUY_BLOCK_REASONS``); ``reason`` and ``message`` are None when the bet is allowed."""
        market = to_address(market, "market")
        buyer_in = None if buyer is None else to_address(buyer, "buyer")
        input_in = None if input is None else to_address(input, "input")
        md = self._require_market(market)
        who = buyer_in if buyer_in is not None else self._account
        token_in = input_in if input_in is not None else md["pair_asset"]
        block = self._buy_block(market, md, outcome, who, token_in)
        if block:
            return {"can_bet": False, "reason": block["reason"], "message": block["message"]}
        return {"can_bet": True, "reason": None, "message": None}

    def _buy_block(self, market: str, md: Dict[str, Any], outcome: int, buyer: str,
                   token_in: str) -> Optional[Dict[str, str]]:
        """The first rule that refuses a bet of ``buyer`` paying in ``token_in``, or None (the host's rules, then
        conversion)."""
        host = self.address
        paused = self._host.functions.paused().call()
        n = self.get_num_outcomes(market)
        now = self._chain_time()
        frozen = self._contract(market, "FactoryToken").functions.frozen().call()
        if paused:
            return {"reason": "host paused", "message": f"The {self.market_type} market host is paused: no bets."}
        if md["resolved"]:
            return {"reason": "market resolved", "message": f"Market {market} is resolved: betting is over."}
        if not _is_int(outcome) or outcome < 0 or outcome >= n:
            return {"reason": "outcome", "message": f"outcome must be 0-{n - 1}; got {outcome}."}
        closed = None
        if md["end_time"] != 0 and now + 1 >= md["end_time"]:
            closed = {"reason": "Betting closed", "message": f"Betting closed at {md['end_time']} (chain time {now})."}
        host_block = self._precheck_buy_host(market, md, buyer, closed)
        if host_block:
            return host_block
        if frozen:
            wl = self._contract(market, "FactoryToken").functions.whitelisted(buyer).call()
            if not wl:
                return {"reason": "not whitelisted",
                        "message": f"Market token {market} is frozen and {buyer} is not on its whitelist."}
        taxes = self.client.addresses.get("taxes")
        in_market = _eq(token_in, market)
        in_main = _eq(token_in, md["main"])
        fee = ((in_market or in_main) and not is_zero_address(taxes)
               and self.client.taxes.get_tax_rate(market, buyer) > 0)
        if in_market and not fee:
            return None
        if not is_zero_address(taxes):
            registered = self._contract(taxes, "Taxes").functions.isPrediction(host).call()
            if not registered:
                return {"reason": "convert closed",
                        "message": f"The {self.market_type} host {host} cannot convert a bet right now: no bets."}
        if not self._pair_active(md["main"]):
            return {"reason": "pair not active",
                    "message": f"Market {market} settles through main {md['main']}, which is on a paused pair: no bets "
                               "that convert through it until the pair is active again."}
        trades_main = (not in_market and not in_main) or fee
        if trades_main and not (self.client.mains.get(md["main"]).trading_enabled
                                or self._contract(md["main"], "MainToken").functions.tradingEnabled().call()):
            return {"reason": "Trading is not enabled",
                    "message": f"Trading on main {md['main']} has not opened yet: no bets on market {market} that buy or "
                               "sell it until it does."}
        if not in_market and not in_main and not _eq(token_in, md["pair_asset"]):
            sale = _input_sale_asset(self, token_in, md["main"], md["pair_asset"])
            if sale is not None and is_asset_paused(self.client, sale["asset"]):
                return {"reason": "pair asset paused",
                        "message": pair_asset_paused_message(sale["asset"], sale["symbol"],
                                                             _input_sale_paused(token_in, sale["factory_token"]))}
        if trades_main and is_asset_paused(self.client, md["pair_asset"]):
            return {"reason": "pair asset paused",
                    "message": pair_asset_paused_message(md["pair_asset"], self.client.mains.get(md["main"]).pair_symbol,
                                                         _BET_PAUSED)}
        return None

    def redeem(
        self,
        market: str,
        fraction_bps: Optional[int] = None,
        slices: Optional[int] = None,
        min_out: Optional[int] = None,
        slippage_bps: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Redeem a resolved market (winnings, or the cost-basis refund on INVALID) into the pair asset.
        Sells ``fraction_bps`` (1-10000) of the remaining entitlement in ``slices`` (1-25); defaults and ``min_out``
        (raw pair asset) come from ``market_reader.redeem_plan``. The plan always runs, also when all three are given
        (they are then sent as given): it refuses a main on a paused pair ('pair not active') and slices that pay out 0
        ('dust slice'), which the host reverts with no reason; then 'pair asset paused' (its issuer paused the pair
        asset the payout is made in). An explicit ``min_out`` is a whole number above 0
        (``ValidationError('minOut')``, checked before any read, as ``buy``'s minimums).
        Returns {'hash','receipt','result','warnings','fraction_bps','slices','min_out','payout','tokens','remaining'}:
        the floor sent and the pair asset received (``Amount``s in the market's pair asset), market tokens sold and market
        tokens still owed (``Amount``s in the market token), from ``SharesRedeemed`` (None when the event is missing)."""
        market = to_address(market, "market")
        min_out_in = opt_uint(min_out, "minOut")
        if min_out_in == 0:
            raise ValidationError("minOut", "A zero minOut is never sent; omit it to derive it from a quote.")
        host = self.address
        user = self._account
        md = self._require_market(market)
        if not md["resolved"]:
            raise ValidationError("market unresolved", f"Market {market} is not resolved yet.")
        if fraction_bps is not None and (not _is_int(fraction_bps) or fraction_bps < 1 or fraction_bps > 10000):
            raise ValidationError("fraction 1-10000", f"fractionBps must be 1-10000; got {fraction_bps}.")
        if slices is not None and (not _is_int(slices) or slices < 1 or slices > MAX_SLICES):
            raise ValidationError("slices 1-25", f"slices must be 1-{MAX_SLICES}; got {slices}.")
        fraction_bps = None if fraction_bps is None else int(fraction_bps)
        slices = None if slices is None else int(slices)
        owed = self.quote_redeem(market, user)
        if owed == 0:
            raise ValidationError("Nothing to redeem", f"{user} has nothing to redeem on market {market}.")

        plan = self._reader.redeem_plan(
            market=market, user=user, fraction_bps=fraction_bps, slices=slices, slippage_bps=slippage_bps,
            min_out=min_out_in, host=host,
        )
        fraction_bps = int(plan["fraction_bps"])
        slices = int(plan["slices"])
        min_out = plan["min_out"]
        if is_asset_paused(self.client, md["pair_asset"]):
            raise pair_asset_paused(md["pair_asset"], self.client.mains.get(md["main"]).pair_symbol,
                                    "no redemption can pay it out")
        pair_of, tokens_of = self._market_formatters(market, md)

        tx = self._send(self._host.functions.redeem(market, fraction_bps, slices, min_out), self._contract_name)
        ev = next((e for e in self._events(tx, self._host, "SharesRedeemed", host)
                   if _eq(e["marketToken"], market) and _eq(e["user"], user)), None)
        if ev is None:
            add_missing_event_warning(tx, self._contract_name, "SharesRedeemed", ("payout", "tokens", "remaining"))
        return {**tx, "fraction_bps": fraction_bps, "slices": slices, "min_out": pair_of(min_out),
                "payout": opt_amount(pair_of, ev["payout"] if ev else None),
                "tokens": opt_amount(tokens_of, ev["tokens"] if ev else None),
                "remaining": opt_amount(tokens_of, ev["remaining"] if ev else None)}

    def disable_freeze(self, market: str) -> Dict[str, Any]:
        """End the market token's whitelist (frozen) phase. Creator only. Returns the tx plus 'frozen': False once
        the token logged ``FreezeLifted`` (None when the event is missing)."""
        market = to_address(market, "market")
        self._require_creator(market)
        tx = self._send(self._host.functions.disableFreeze(market), self._contract_name)
        lifted = bool(self._events(tx, self._contract(market, "FactoryToken"), "FreezeLifted", market))
        if not lifted:
            add_missing_event_warning(tx, "FactoryToken", "FreezeLifted", ("frozen",))
        return {**tx, "frozen": False if lifted else None}

    @staticmethod
    def _whitelist_tag(tag: Any) -> str:
        """A whitelist tag (a label stored on chain with the entries): None = "" (as TS null), else a str
        (``ValidationError('tag')``) of well-formed text (:func:`check_text`: a lone surrogate can't go on chain)."""
        if tag is None:
            return ""
        if not isinstance(tag, str):
            raise ValidationError("tag", f"tag must be a string, got {tag!r}.")
        check_text(tag, "tag")
        return tag

    @staticmethod
    def _wallet_list(v: Any, field: str) -> List[str]:
        """The wallets of a whitelist write, checked before any read, as in TS: a non-empty list/tuple ('no wallets
        provided' otherwise: None, empty, one address as a str, a set) of strict addresses (:func:`to_address`, the rule
        is ``field``)."""
        if not isinstance(v, (list, tuple)) or len(v) == 0:
            got = "an empty array" if isinstance(v, (list, tuple)) else _kind_of(v)
            raise ValidationError("no wallets provided", f"{field} must be a non-empty array of addresses, got {got}.")
        return [to_address(w, field) for w in v]

    def _whitelist_result(self, tx: Dict[str, Any], market: str, requested: Sequence[str], adding: bool) -> Dict[str, Any]:
        """What the market token logged for a whitelist write: 'listed' (``WhiteListedWallet``), 'cap_unchanged' (the
        requested wallets it skipped because they were listed already: their cap is unchanged) and 'removed'
        (``WhitelistRemoved``)."""
        tok = self._contract(market, "FactoryToken")
        listed = [e["user"] for e in self._events(tx, tok, "WhiteListedWallet", market)]
        removed = [e["user"] for e in self._events(tx, tok, "WhitelistRemoved", market)]
        cap_unchanged: List[str] = []
        if adding:
            for w in requested:
                if not any(_eq(x, w) for x in listed) and not any(_eq(x, w) for x in cap_unchanged):
                    cap_unchanged.append(w)
        return {**tx, "listed": listed, "cap_unchanged": cap_unchanged, "removed": removed}


    def get_market_data(self, market: str) -> Dict[str, Any]:
        """The market's data with ``final_outcome`` decoded (254 = INVALID, 255 = unresolved). ``main`` is the
        market's main, ``pair_asset`` its pair asset; ``status`` is "unresolved" / "resolved" / "invalid"; ``winner``
        the first-placed outcome or None. A zero ``market_token`` means "not on this host". Token amounts are 18-decimal
        market tokens."""
        d = self._host.functions.getMarketData(to_address(market, "market")).call()
        (market_token, creator, ecosystem, usdc, market_name, creation_time, end_time, final_outcome, resolved,
         general_pot, total_virtual_reserve, is_private) = d
        fo = int(final_outcome)
        if not resolved or fo == OUTCOME_UNRESOLVED:
            status = "unresolved"
        elif fo == OUTCOME_INVALID:
            status = "invalid"
        else:
            status = "resolved"
        return {
            "market_token": market_token,
            "creator": creator,
            "main": ecosystem,
            "pair_asset": usdc,
            "market_name": market_name,
            "creation_time": creation_time,
            "end_time": end_time,
            "final_outcome": fo,
            "resolved": resolved,
            "status": status,
            "winner": fo if status == "resolved" else None,
            "general_pot": general_pot,
            "total_odds_weight": total_virtual_reserve,
            "is_private": is_private,
        }

    def get_outcome(self, market: str, outcome: int) -> Dict[str, int]:
        """{'odds_weight', 'total_cost', 'circulating_shares'} (18 decimals). An outcome's share of all outcomes'
        ``odds_weight`` is its odds."""
        vr, tc, cs = self._host.functions.getOutcome(to_address(market, "market"), outcome).call()
        return {"odds_weight": vr, "total_cost": tc, "circulating_shares": cs}

    def get_user_shares(self, market: str, user: str, outcome: int) -> int:
        """Shares (18 decimals) ``user`` holds on ``outcome``."""
        market = to_address(market, "market")
        user = to_address(user, "user")
        return self._host.functions.getUserShares(market, user, outcome).call()

    def get_opening_pool(self, num_outcomes: int) -> Dict[str, int]:
        """The reference opening pool for ``num_outcomes`` (market tokens). A market's own sizes:
        ``market_reader.market_sizes``."""
        per, total = self._host.functions.getInitialReserves(to_uint(num_outcomes, "numOutcomes")).call()
        return {"per_outcome": per, "total_pool": total}

    def get_num_outcomes(self, market: str) -> int:
        return int(self._host.functions.getNumOutcomes(to_address(market, "market")).call())

    def get_option_names(self, market: str) -> List[str]:
        return list(self._host.functions.getOptionNames(to_address(market, "market")).call())

    def has_betted_on_market(self, market: str, user: str) -> bool:
        market = to_address(market, "market")
        user = to_address(user, "user")
        return self._host.functions.hasBettedOnMarket(market, user).call()

    def get_tranche_bp(self, market: str) -> List[int]:
        """Payout slices in bps per finishing position."""
        return [int(b) for b in self._host.functions.getTrancheBP(to_address(market, "market")).call()]

    def get_final_ranking(self, market: str) -> List[int]:
        """The result in finish order (empty when unresolved or INVALID)."""
        return [int(b) for b in self._host.functions.getFinalRanking(to_address(market, "market")).call()]

    def quote_scenario(self, market: str, ranking: Sequence[int], tie_mask: Union[int, float] = 0,
                       user: Optional[str] = None) -> int:
        """Market tokens (18 decimals) ``user`` (default the signer) would receive if the market resolved now with
        (``ranking``, ``tie_mask``). 0 for an invalid ranking, and for ``[]`` (INVALID): the host quotes only rankings
        here. For INVALID use ``quote_invalid_refund``. Checked before any read: ``ranking`` a list/tuple of whole
        numbers 0-255 ('ranking' / 'rankingEntry'), ``tie_mask`` a whole number ('tieMask', as
        ``market_reader.quote_scenario``: None is refused; left out = 0)."""
        market = to_address(market, "market")
        picks = normalize_ranking(ranking)
        mask = to_uint(tie_mask, "tieMask")
        who = self._account if user is None else to_address(user, "user")
        return self._host.functions.quoteScenario(market, picks, mask, who).call()

    def quote_invalid_refund(self, market: str, user: Optional[str] = None) -> int:
        """Market tokens (18 decimals) ``user`` (default the signer) would get back if the market resolved INVALID now.
        It is based on an average cost, so it can differ from what was paid. ``market_reader.quote_invalid_refund`` also
        gives its value in the pair asset. Rules 'unknown market', 'market resolved' (then ``quote_redeem`` gives what is
        owed)."""
        market = to_address(market, "market")
        who = self._account if user is None else to_address(user, "user")
        return self._reader.quote_invalid_refund(self.address, market, who)["token_payout"]

    def quote_redeem(self, market: str, user: Optional[str] = None) -> int:
        """Market tokens (18 decimals) ``user`` (default the signer) can still redeem on a resolved market
        (0 when unresolved)."""
        market = to_address(market, "market")
        who = self._account if user is None else to_address(user, "user")
        return self._host.functions.quoteRedeem(market, who).call()

    def get_pending_payout(self, market: str, user: Optional[str] = None) -> int:
        """Market tokens (18 decimals) left after a partial redeem (0 before the first redeem or when done)."""
        market = to_address(market, "market")
        who = self._account if user is None else to_address(user, "user")
        return self._host.functions.pendingPayout(market, who).call()

    def get_resolved_at(self, market: str) -> int:
        """Unix seconds the market resolved (any path, INVALID included); 0 = unresolved."""
        return self._host.functions.resolvedAt(to_address(market, "market")).call()

    def _min_seed_amount(self, main: Optional[str], private_event: bool) -> Amount:
        """Seed floor for a new market on ``main`` (default: the default main), in its pair asset."""
        info = self.client.mains.get(_main_arg(main)) if main else self.client.mains.default()
        units = self._min_seed_units(private_event)
        sizes = self._reader.market_sizes(self.address, info.main, 2)
        return pair_amount(units * sizes["unit"], info)

    def is_gating_enabled(self) -> bool:
        return self._host.functions.gatingEnabled().call()

    def get_creation_fee(self, main: Optional[str] = None) -> int:
        """The exact ``msg.value`` (native wei) ``create_market`` needs on ``main`` (0 when no creation fee
        applies)."""
        host = self.address
        m = self.client.mains.address_of(_main_arg(main)) if main else self.client.mains.default_address()
        factory = self._contract(host, "TrancheMarket").functions.ecosystems(m).call()[0]
        if is_zero_address(factory):
            raise ValidationError("ecosystem inactive", f"Main {m} is not registered on the {self.market_type} market host.")
        fac = self._contract(factory, "Factory")
        whitelisted = fac.functions.feeWhitelist(host).call()
        fee = fac.functions.feeAmount().call()
        return 0 if whitelisted else fee

    def is_paused(self) -> bool:
        return self._host.functions.paused().call()


    def _require_market(self, market: str) -> Dict[str, Any]:
        """The market's data; raises when the market is not on this host."""
        md = self.get_market_data(market)
        if is_zero_address(md["market_token"]) or not _eq(md["market_token"], market):
            raise ValidationError("unknown market", f"{market} is not a market on the {self.market_type} host {self.address}.")
        return md

    def _require_creator(self, market: str) -> Dict[str, Any]:
        """The market's data; raises unless the signer created it."""
        md = self._require_market(market)
        if not _eq(md["creator"], self._account):
            raise ValidationError("not creator", f"Only the market's creator ({md['creator']}) can do this.")
        return md
