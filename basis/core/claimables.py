"""What a wallet can claim now, across every source, read from the chain (``client.get_claimables``;
TypeScript SDK: src/core/claimables.ts)."""

from typing import Any, Callable, Dict, List, Optional, Sequence

from web3 import Web3

from ..config.addresses import is_zero_address
from .abi import load_abi
from .amounts import AmountOf, main_amount_of, pair_amount_of
from .errors import ValidationError
from .redact import redact_urls
from .mains import pair_amount
from .multicall import multicall, multicall_settled
from .units import Amount, to_address, to_amount

CLAIMABLE_SECTIONS = ("holder_rewards", "markets", "up_down", "vesting", "vault", "liquidations")

_CLAIM_PERIOD = 365 * 86_400
_LEVERAGE_PAGE = 100
_SCAN_PAGE = 200
_REWARD_PAGE = 200


def _unique_addresses(items: Sequence[str]) -> List[str]:
    """Checksummed addresses without duplicates (case-insensitive), first-seen order."""
    seen: Dict[str, str] = {}
    for a in items:
        seen.setdefault(a.lower(), Web3.to_checksum_address(a))
    return list(seen.values())


class _Lazy:
    """A chain read shared by two sections: run once on first use, its result (or its error) kept."""

    def __init__(self, fn: Callable[[], Any]):
        self._fn = fn
        self._done = False
        self._value: Any = None
        self._error: Optional[BaseException] = None

    def get(self) -> Any:
        if not self._done:
            self._done = True
            try:
                self._value = self._fn()
            except Exception as e:  # noqa: BLE001
                self._error = e
        if self._error is not None:
            raise self._error
        return self._value


def _amount_resolver(client) -> Callable[[str], AmountOf]:
    """Formatter per token by the ``amount_of`` rule of write results (a main: unit ``main``; a main's pair asset:
    ``pairAsset``; anything else: ``token``), from the cached mains list and token metadata, so no ``is_main`` read per
    row. One per call."""
    cache: Dict[str, AmountOf] = {}

    def resolve(token: str) -> AmountOf:
        k = token.lower()
        if k not in cache:
            mains = client.mains.list()
            main = next((m for m in mains if m.main.lower() == k), None)
            owner = None if main else next((m for m in mains if m.pair_asset.lower() == k), None)
            if main is not None:
                cache[k] = main_amount_of(main)
            elif owner is not None:
                cache[k] = pair_amount_of(owner)
            else:
                meta = client.mains.token_meta(token)
                decimals, symbol = int(meta["decimals"]), str(meta["symbol"])
                cache[k] = lambda raw: to_amount(raw, decimals, "token", token, symbol)
        return cache[k]

    return resolve


def _read_leverages(client, who: str) -> List[Dict[str, Any]]:
    """Every leverage position of ``who`` on every main (one multicall for the counts, one per 100 positions)."""
    mains = client.mains.list()
    if not mains:
        return []
    contracts = {m.main: client.mains._contract(m.main, "MainToken") for m in mains}
    counts = multicall(client, [(contracts[m.main].functions.leverageCount(who), "MainToken") for m in mains])
    ids = [(m.main, i) for m, n in zip(mains, counts) for i in range(1, int(n) + 1)]
    out: List[Dict[str, Any]] = []
    for start in range(0, len(ids), _LEVERAGE_PAGE):
        page = ids[start:start + _LEVERAGE_PAGE]
        rows = multicall(client, [(contracts[main].functions.leverages(who, i), "MainToken") for main, i in page])
        for (main, i), r in zip(page, rows):
            out.append({"main": main, "id": i, "token": r[1], "liquidation_claim": r[7], "is_liquidated": r[8],
                        "time_of_closure": r[11]})
    return out


def _given_list(v: Any, field: str, what: str) -> Optional[List[Any]]:
    """A list argument (``tokens``, ``markets``, ``up_down_rounds``, ``loan_hubs``): None = left out (None back), a list
    or a tuple is copied as a list, anything else is refused with the field's name as the rule, before any read: a
    string is never read character by character, nor a set or a dict item by item."""
    if v is None:
        return None
    if not isinstance(v, (list, tuple)):
        raise ValidationError(field, f"{field} must be an array of {what}.")
    return list(v)


def _market_ref(m: Any, i: int) -> Any:
    """A market the caller listed, checked as ``get_redeemables`` checks its entries, before any read: a market address
    (``to_address``, rule ``markets``), or a ``{"market", "host"}`` dict whose ``market`` is one and whose ``host`` is
    left out (or None: detected), "public", "private" or an address (``to_address``, rule ``host``). Anything else
    (None, a number, a list, a set) is refused naming its index, rule ``markets`` (the list's name, as for ``tokens``
    and ``loan_hubs``). TS: ``marketRef``."""
    if isinstance(m, str):
        return to_address(m, "markets")
    if not isinstance(m, dict):
        shown = repr(m) if m is None or isinstance(m, (bool, int, float)) else f"a {type(m).__name__}"
        raise ValidationError("markets", f"markets[{i}] must be a market address or a {{'market', 'host'}} dict, got {shown}.")
    market = to_address(m.get("market"), "markets")
    h = m.get("host")
    if h is None:
        return {"market": market}
    return {"market": market, "host": h if h in ("public", "private") else to_address(h, "host")}


def _api_reward_tokens(client, who: str) -> List[str]:
    """Holder-reward tokens the API knows for ``who`` (API key): portfolio holdings, traded tokens and loan tokens."""
    p = client.api.get_wallet_portfolio(who) or {}
    return ([h["address"] for h in p.get("holdings") or [] if h.get("address") and not h.get("isMain")]
            + [t["token"] for t in (p.get("costBasis") or {}).get("tokens") or [] if t.get("token")]
            + [ln["token"] for ln in p.get("loans") or [] if ln.get("token")])


def _chain_reward_tokens(client) -> List[str]:
    """Every factory token, read in multicalls of ``_SCAN_PAGE``."""
    factory = client.mains._contract(client.addresses["factory"], "Factory")
    n = factory.functions.allPairsLength().call()
    out: List[str] = []
    for start in range(0, n, _SCAN_PAGE):
        out.extend(multicall(client, [(factory.functions.allPairs(i), "Factory")
                                      for i in range(start, min(start + _SCAN_PAGE, n))]))
    return out


def get_claimables(client, user: Optional[str] = None, tokens: Optional[Sequence[str]] = None,
                   markets: Optional[Sequence[Any]] = None,
                   up_down_rounds: Optional[Sequence[Any]] = None,
                   loan_hubs: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Implementation of ``BasisClient.get_claimables`` (see there)."""
    if user is not None:
        who = to_address(user, "user")
    elif client.account:
        who = client.account.address
    else:
        raise ValidationError("user", "Pass the wallet to read: the client has no signer.")
    tokens = _given_list(tokens, "tokens", "token addresses")
    tokens = None if tokens is None else [to_address(t, "tokens") for t in tokens]
    markets = _given_list(markets, "markets", "markets")
    markets = None if markets is None else [_market_ref(m, i) for i, m in enumerate(markets)]
    up_down_rounds = _given_list(up_down_rounds, "upDownRounds", "rounds")
    hubs = []
    for h in _given_list(loan_hubs, "loanHubs", "loan hub addresses") or []:
        a = to_address(h, "loanHubs")
        if is_zero_address(a):
            raise ValidationError("loanHubs", f"Not a loan hub address: {a}.")
        hubs.append(a)
    extra_hubs = [h for h in _unique_addresses(hubs) if h.lower() != client.addresses["loanHub"].lower()]
    block = client.web3.eth.get_block("latest")
    block_number = block["number"]
    block_time = block["timestamp"]
    skipped: List[str] = []
    failed: List[Dict[str, Any]] = []

    def fail(section: str, source: Optional[str], error: BaseException) -> None:
        failed.append({"section": section, "source": source, "message": redact_urls(error), "error": error})

    def section(name: str, deployed: bool, empty: Any, given: bool, fn: Callable[[], Any]) -> Any:
        """Runs a section; an error is listed in ``failed`` (a caller's own bad input still raises)."""
        if not deployed:
            skipped.append(name)
            return empty
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if given and isinstance(e, ValidationError):
                raise
            fail(name, None, e)
            return empty

    me = (not client.api_key and bool(client._sync_auth()["session_cookie"]) and client.account is not None
          and client.account.address.lower() == who.lower())
    formatter_of = _amount_resolver(client)

    def read_hub_loans() -> List[Dict[str, Any]]:
        rows = client.loans.get_user_loans(who) if client.loans.deployed else []
        for h in extra_hubs:
            rows = rows + client.loans.at_hub(h).get_user_loans(who)
        return rows

    hub_loans = _Lazy(read_hub_loans)
    leverages = _Lazy(lambda: _read_leverages(client, who))
    vestings = _Lazy(lambda: client.vesting.get_vestings_of(who, role="beneficiary", include_loan=True)
                     if client.vesting.deployed else [])

    def holder_rewards_job() -> Dict[str, List[Dict[str, Any]]]:
        if tokens is not None:
            found = list(tokens)
        else:
            found = []
            first = (("portfolio", lambda: _api_reward_tokens(client, who)) if client.api_key
                     else ("chain", lambda: _chain_reward_tokens(client)))
            for name, fn in (first,
                             ("leverage", lambda: [p["token"] for p in leverages.get()]),
                             ("loans", lambda: [ln["collateral_token"] for ln in hub_loans.get()]),
                             ("vesting", lambda: [v["token"] for v in vestings.get()])):
                try:
                    found.extend(fn())
                except Exception as e:  # noqa: BLE001
                    fail("holder_rewards", name, e)
        listed = _unique_addresses([t for t in found if isinstance(t, str) and Web3.is_address(t)])
        abi = load_abi("FactoryToken")
        reads = []
        for start in range(0, len(listed), _REWARD_PAGE):
            calls = []
            for token in listed[start:start + _REWARD_PAGE]:
                fns = client.web3.eth.contract(address=token, abi=abi).functions
                calls += [(fns.rewardsEnabled(), "FactoryToken"), (fns.getClaimableRewards(who), "FactoryToken"),
                          (fns.quoteToken(), "FactoryToken")]
            reads.extend(multicall_settled(client, calls))
        rows = []
        skipped_tokens = []
        for i, token in enumerate(listed):
            (on_ok, on), (raw_ok, raw), (main_ok, main) = reads[i * 3:i * 3 + 3]
            if not (on_ok and on is True and raw_ok and raw > 0 and main_ok):
                continue
            try:
                info = client.mains.get(main)
            except Exception:
                try:
                    known = client.mains.is_main(main)
                except Exception:  # noqa: BLE001
                    known = True
                if known:
                    raise
                skipped_tokens.append({"token": token, "quote_token": main, "reason": "unknown main"})
                continue
            rows.append({"token": token, "amount": pair_amount(raw, info)})
        return {"rows": rows, "skipped": skipped_tokens}

    def markets_job() -> Dict[str, List[Dict[str, Any]]]:
        if markets is not None:
            refs = list(markets)
        else:
            refs = []
            for status in ("resolved", "invalidated"):
                for m in client.api.paginate_pages(lambda **p: client.api.get_wallet_markets(who, **p),
                                                   status=status, limit=100):
                    refs.append({"market": m["marketToken"], "host": m["host"]} if m.get("host") else m["marketToken"])
        seen = set()
        unique = []
        for r in refs:
            k = (r["market"] if isinstance(r, dict) else r).lower()
            if k not in seen:
                seen.add(k)
                unique.append(r)
        got = client.market_reader.get_redeemables(unique, who)
        return {"rows": [r for r in got if r["skipped"] is None and r["token_payout"] > 0],
                "skipped": [{"market": r["market"], "host": r["host"], "reason": r["skipped"]}
                            for r in got if r["skipped"] is not None]}

    def up_down_job() -> Dict[str, Any]:
        rounds = up_down_rounds
        if rounds is None:
            record = (client.api.get_my_updown() if me else client.api.get_wallet_updown(who)) or {}
            ud = client.addresses["upDown"].lower()
            rounds = [{"asset": b["asset"], "tf": b["tf"], "round_id": int(b["roundId"])}
                      for b in record.get("claimableBets") or []
                      if b.get("asset") is not None and (b.get("contract") or "").lower() == ud]
        return client.updown.get_claimable(who, rounds)

    def vesting_job() -> List[Dict[str, Any]]:
        rows = []
        for v in vestings.get():
            if v["claimable"] > 0:
                rows.append({"id": v["id"], "token": v["token"], "claimable": formatter_of(v["token"])(v["claimable"])})
        return rows

    def vault_job() -> List[Dict[str, Any]]:
        rows = []
        for p in client.vault.get_positions(who):
            e = p["earned"]
            raw = min(e.raw, p["available_stasis"].raw)
            if raw > 0:
                rows.append({"main": p["main"], "vault": p["vault"], "earned": e,
                             "claimable": to_amount(raw, e.decimals, e.unit, e.asset, e.symbol)})
        return rows

    def liquidations_job() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []

        def row(kind: str, main: str, hub: Optional[str], id_: int, loan_id: Optional[int], token: str, raw: int,
                closed_at: int) -> None:
            claim_deadline = closed_at + _CLAIM_PERIOD
            out.append({"kind": kind, "main": main, "hub": hub, "id": id_, "loan_id": loan_id, "token": token,
                        "amount": formatter_of(token)(raw),
                        "liquidated_at": closed_at, "claim_deadline": claim_deadline,
                        "claim_expired": raw > 0 and block_time > claim_deadline})

        def hub_part() -> None:
            for ln in hub_loans.get():
                if ln["is_liquidated"] and ln["liquidation_claim"] > 0:
                    row("hubLoan", ln["ecosystem"], ln["hub"], ln["hub_id"], None, ln["collateral_token"],
                        ln["liquidation_claim"], ln["time_of_closure"])

        def leverage_part() -> None:
            for p in leverages.get():
                if p["is_liquidated"] and p["liquidation_claim"] > 0:
                    row("leverage", p["main"], None, p["id"], None, p["token"], p["liquidation_claim"],
                        p["time_of_closure"])

        def vesting_part() -> None:
            waiting = [v for v in vestings.get() if v["loan"] is not None and not v["loan"]["details"]["active"]]
            if not waiting:
                return
            closure = multicall(client, [(client.mains._contract(v["loan"]["details"]["ecosystem"], "MainToken").functions
                                          .loans(v["loan"]["hub"], v["loan"]["details"]["core_loan_id"]), "MainToken")
                                         for v in waiting])
            for v, c in zip(waiting, closure):
                d = v["loan"]["details"]
                row("vestingLoan", d["ecosystem"], v["loan"]["hub"], v["id"], v["loan"]["loan_id"], d["collateral_token"],
                    d["liquidation_claim"], c[11])

        for source, part in (("loans", hub_part), ("leverage", leverage_part), ("vesting", vesting_part)):
            try:
                part()
            except Exception as e:  # noqa: BLE001
                fail("liquidations", source, e)
        return out

    rewards_res = section("holder_rewards", client.factory.deployed, {"rows": [], "skipped": []}, tokens is not None,
                          holder_rewards_job)
    holder_rewards = rewards_res["rows"]
    markets_res = section("markets", client.market_reader.deployed, {"rows": [], "skipped": []}, markets is not None,
                          markets_job)
    up_down = section("up_down", client.updown.deployed,
                      {"user": who, "block_number": block_number, "items": [], "totals": []},
                      up_down_rounds is not None, up_down_job)
    vesting = section("vesting", client.vesting.deployed, [], False, vesting_job)
    vault = section("vault", client.vault.deployed, [], False, vault_job)
    liquidations_deployed = (client.loans.deployed or bool(extra_hubs) or client.vesting.deployed
                             or not is_zero_address(client.addresses["swap"]))
    liquidations = section("liquidations", liquidations_deployed, [], False, liquidations_job)

    sums: Dict[str, Amount] = {}

    def add(a: Amount) -> None:
        if a.raw == 0:
            return
        k = a.asset.lower()
        prev = sums.get(k)
        sums[k] = to_amount(prev.raw + a.raw, prev.decimals, prev.unit, prev.asset, prev.symbol) if prev else a

    for r in holder_rewards:
        add(r["amount"])
    for r in markets_res["rows"]:
        add(r["pair_payout"])
    for r in up_down["items"]:
        add(r["payout"])
    for r in vesting:
        add(r["claimable"])
    for r in vault:
        add(r["claimable"])
    for r in liquidations:
        if r["kind"] != "vestingLoan":
            add(r["amount"])
    return {
        "user": who, "block_number": block_number, "block_time": block_time, "holder_rewards": holder_rewards,
        "skipped_tokens": rewards_res["skipped"], "markets": markets_res["rows"],
        "skipped_markets": markets_res["skipped"], "up_down": up_down,
        "vesting": vesting, "vault": vault, "liquidations": liquidations,
        "totals": [{"asset": a.asset, "amount": a} for a in sums.values()],
        "skipped": sorted(skipped, key=CLAIMABLE_SECTIONS.index),
        "failed": sorted(failed, key=lambda f: CLAIMABLE_SECTIONS.index(f["section"])),
    }
