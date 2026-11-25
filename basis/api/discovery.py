"""Discovery domain: /api/v1/mains, /api/v1/config, /api/v1/status, /contracts.json, /.well-known/*, /api/v1/prices*,
/api/v1/stats*. TypeScript SDK: src/api/discovery.ts (response keys are camelCase, as on the wire)."""

import email.utils
import json as _json
import time
from typing import Dict, List, Literal, Optional, Sequence, Tuple, Union

from ..core.errors import ValidationError
from .core import ApiCore, api_error_from
from .types import E18, Pagination, RawAmount, TypedDict, UsdE18, UsdSource


MainsInclude = Literal["cashHeld", "market"]
"""Extra blocks of ``GET /api/v1/mains`` (live chain reads, cached 15 s)."""


class MainsListMarket(TypedDict, total=False):
    """``market`` block of a main (``include="market"``). Prices in whole pair asset per whole main x 1e18; chain
    values are None when their read failed (``depth`` is None unless ``priceFrom`` is ``"live"``)."""

    priceE18: Optional[E18]
    priceRaw: Optional[RawAmount]
    priceUsdE18: UsdE18
    usdSource: UsdSource
    liquidity: Optional[RawAmount]
    depth: Optional[RawAmount]
    liquidityUsdE18: UsdE18
    depthUsdE18: UsdE18
    circulatingSupply: Optional[RawAmount]
    mcapUsdE18: UsdE18
    change24hBps: Optional[int]
    volume24hUsdE18: UsdE18
    volume24hUsdCovered: int
    trades24h: Optional[int]
    priceFrom: Optional[Literal["live", "sync"]]
    priceBlock: Optional[int]


class MainsListRow(TypedDict, total=False):
    """One main of ``GET /api/v1/mains`` (addresses lower-case; ``tradingEnabledAt`` ISO)."""

    key: Optional[str]
    name: Optional[str]
    symbol: Optional[str]
    label: Optional[str]
    main: str
    pairAsset: str
    pairSymbol: Optional[str]
    pairDecimals: int
    vault: str
    usdKind: UsdSource
    configured: bool
    live: bool
    indexed: bool
    active: Optional[bool]
    tradingEnabledAt: Optional[str]
    cashHeld: Optional[RawAmount]
    market: Optional[MainsListMarket]


class MainsList(TypedDict, total=False):
    network: str
    chainId: int
    uiDefaultMain: str
    data: List[MainsListRow]




class ApiFeatures(TypedDict):
    """Feature switches (``/api/v1/config``, ``/api/v1/status``)."""

    trading: bool
    loans: bool
    leverage: bool
    liquidations: bool
    crossSwap: bool
    vesting: bool
    markets: bool
    privateMarkets: bool
    resolver: bool
    updown: bool
    referrals: bool
    agents: bool
    vault: bool
    mainLens: bool


class ApiConfigContracts(TypedDict, total=False):
    """Contract name to EIP-55 address; None when not deployed on this network, or not published (the sites publish
    none of the BASIS contracts today: the SDK carries its own book; the outside ERC-8004 registry is listed)."""

    SWAP: Optional[str]
    TAXES: Optional[str]
    FACTORY: Optional[str]
    LOANS: Optional[str]
    LEVERAGE: Optional[str]
    LIQUIDATOR: Optional[str]
    HOP_EXECUTOR: Optional[str]
    VESTING: Optional[str]
    PREDICTION: Optional[str]
    PRIVATE: Optional[str]
    RESOLVER: Optional[str]
    READER: Optional[str]
    REDEMPTION_READER: Optional[str]
    UPDOWN: Optional[str]
    MAINLENS: Optional[str]
    STAKING: Optional[str]
    ERC8004_REGISTRY: Optional[str]
    WRAPPED_NATIVE: Optional[str]
    BOND_TOKEN: Optional[str]


class ApiConfigMain(TypedDict, total=False):
    key: str
    label: Optional[str]
    main: Optional[str]
    pairAsset: Optional[str]
    pairSymbol: str
    pairDecimals: int
    mainDecimals: int
    vault: Optional[str]
    maxSupplyMultiplier: int
    usdSource: str
    live: bool


class ApiConfig(TypedDict, total=False):
    """``GET /api/v1/config``: network, contracts, mains, path rules (``pathRules``), create limits
    (``createLimits``), EIP-712 domain of the market approval (``eip712``), feature switches, discovery paths."""

    apiVersion: str
    network: str
    paper: bool
    chainId: int
    chainName: str
    nativeSymbol: str
    deploymentBlock: Optional[int]
    contracts: ApiConfigContracts
    legacyLoanHubs: List[str]
    defaultMain: str
    mains: List[ApiConfigMain]
    bond: Dict[str, object]
    pathRules: Dict[str, object]
    createLimits: Dict[str, object]
    eip712: Dict[str, object]
    features: ApiFeatures
    terms: Dict[str, str]
    discovery: Dict[str, str]


class ApiStatus(TypedDict, total=False):
    """``GET /api/v1/status``: chain head vs synced data. Times are unix seconds."""

    network: str
    chainId: int
    headBlock: Optional[int]
    headTime: Optional[int]
    lastSyncedBlock: Optional[int]
    lastSyncedTime: Optional[int]
    lastBackfilledBlock: Optional[int]
    lastBackfilledTime: Optional[int]
    lagBlocks: Optional[int]
    lagSeconds: Optional[int]
    partialTxs: int
    rawTxs: int
    unconfirmedTxs: int
    features: ApiFeatures
    checkedAt: int




class ContractsJsonMain(TypedDict, total=False):
    key: str
    label: Optional[str]
    name: Optional[str]
    symbol: Optional[str]
    main: str
    pairAsset: str
    vault: str
    pairSymbol: str
    pairDecimals: int


class ContractsJson(TypedDict, total=False):
    """``GET /contracts.json``: network and chain id, plus EIP-55 addresses when the site publishes them (keys follow
    the SDK address book; ``tranche`` / ``tranchePrivate`` / ``trancheReader`` repeat ``marketTrading`` /
    ``privateMarket`` / ``reader``). The sites publish no addresses today: the file is then ``{chain, chainId, network,
    live, note}``. ``usdb``, ``mainToken`` and ``staking`` are deprecated single-main keys (use ``mains[]``)."""

    chain: str
    chainId: int
    network: str
    live: bool
    note: str
    deploymentBlock: int
    swap: str
    factory: str
    taxes: str
    leverage: str
    mainLens: str
    loanHub: str
    vesting: str
    liquidator: str
    hopExecutor: str
    marketTrading: str
    privateMarket: str
    tranche: str
    tranchePrivate: str
    resolver: str
    reader: str
    trancheReader: str
    redemptionReader: str
    upDown: str
    erc8004Registry: str
    bondToken: str
    bondDecimals: int
    wrappedNative: str
    mains: List[ContractsJsonMain]
    usdb: str
    mainToken: str
    staking: str
    config: str
    status: str


class AgentManifest(TypedDict, total=False):
    """``GET /.well-known/agent.json``."""

    schema_version: str
    name: str
    description: str
    url: str
    logo: str
    docs: Dict[str, str]
    api: Dict[str, str]
    chain: Dict[str, object]
    agent_registration: Dict[str, str]
    integrations: Dict[str, str]
    social: Dict[str, str]


class AiPluginManifest(TypedDict, total=False):
    """``GET /.well-known/ai-plugin.json``."""

    schema_version: str
    name_for_human: str
    name_for_model: str
    description_for_human: str
    description_for_model: str
    auth: Dict[str, str]
    api: Dict[str, str]
    logo_url: str
    contact_email: str
    legal_info_url: str




class PriceRow(TypedDict, total=False):
    """One pair asset, bond asset or native coin of ``GET /api/v1/prices`` (``role`` items: pair | bond | native;
    ``updatedAt`` ISO, None when there is no fresh price)."""

    asset: str
    symbol: Optional[str]
    decimals: Optional[int]
    role: List[Literal["pair", "bond", "native"]]
    mains: List[str]
    priceUsdE18: UsdE18
    source: UsdSource
    usdSource: UsdSource
    feed: Optional[str]
    maxAgeSec: Optional[int]
    updatedAt: Optional[str]
    sourceRef: Optional[str]
    stale: bool


class PricesList(TypedDict, total=False):
    data: List[PriceRow]
    network: str
    chainId: int
    asOf: str


class TokenPriceRow(TypedDict, total=False):
    """One token of ``GET /api/v1/prices/tokens``: ``priceE18`` pair per whole token, ``priceMainE18`` main per
    whole token (x 1e18), None when not found; ``time`` ISO."""

    address: str
    found: bool
    main: Optional[str]
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    priceE18: Optional[E18]
    priceMainE18: Optional[E18]
    priceUsdE18: UsdE18
    usdSource: UsdSource
    block: Optional[int]
    time: Optional[str]


class TokenPricesList(TypedDict, total=False):
    data: List[TokenPriceRow]
    chainId: int




class StatsWindow(TypedDict, total=False):
    """Trades in a window: count, USD volume and rows with a USD value. ``volumeUsdE18`` sums the priced rows: a lower
    bound when ``partial`` is True, None only when the window has rows and none is priced."""

    n: int
    volumeUsdE18: UsdE18
    usdCovered: int
    partial: bool


class StatsByMainRow(TypedDict, total=False):
    main: Optional[str]
    pairAsset: Optional[str]
    pairSymbol: Optional[str]
    pairDecimals: Optional[int]
    total: StatsWindow
    h24: StatsWindow
    valuePair: RawAmount
    valuePair24h: RawAmount
    usdSource: UsdSource


class StatsVolumeByPairAsset(TypedDict, total=False):
    pairAsset: str
    pairSymbol: Optional[str]
    pairDecimals: Optional[int]
    valuePair: RawAmount
    valuePair24h: RawAmount


class PlatformStats(TypedDict, total=False):
    """``GET /api/v1/stats``: platform activity (``counts``, ``txns``, ``trades`` per window, ``uniqueTraders``,
    ``byMain``, ``volumeByPairAsset``; ``timestamp`` ISO). No TVL: see ``get_stats_mains``."""

    network: str
    chainId: int
    chainName: str
    counts: Dict[str, int]
    txns: Dict[str, int]
    trades: Dict[str, StatsWindow]
    uniqueTraders: Dict[str, int]
    byMain: List[StatsByMainRow]
    volumeByPairAsset: List[StatsVolumeByPairAsset]
    timestamp: str


class StatsHeldValue(TypedDict, total=False):
    """One component of value held by a main, in ``unit`` (``pair`` or ``main``) raw units. Pools (``marketPots``,
    ``updownPools``) add ``count``, ``priced``, ``failed`` (``amount`` None when ``failed`` > 0); ``openInterest``
    adds ``positions``. ``amount`` is None when the chain read failed (never a fake 0)."""

    amount: Optional[RawAmount]
    unit: Literal["pair", "main"]
    usdE18: UsdE18
    usdSource: UsdSource
    count: int
    priced: int
    failed: int
    positions: Optional[int]


class StatsMainsRow(TypedDict, total=False):
    """One main of ``GET /api/v1/stats/mains``. Components overlap: never sum them."""

    key: str
    main: str
    pairAsset: Optional[str]
    pairSymbol: str
    pairDecimals: int
    vault: Optional[str]
    priceUsdE18: UsdE18
    usdSource: UsdSource
    liquidity: StatsHeldValue
    depth: StatsHeldValue
    cashHeld: StatsHeldValue
    openInterest: StatsHeldValue
    vaultStaked: StatsHeldValue
    vaultPledged: StatsHeldValue
    vaultRewardsHeld: StatsHeldValue
    marketPots: StatsHeldValue
    updownPools: StatsHeldValue


class StatsMains(TypedDict, total=False):
    network: str
    chainId: int
    note: str
    asOf: str
    data: List[StatsMainsRow]


StatsFeesGroupBy = Literal["main", "token", "category"]
StatsLeaderboardMetric = Literal["volume", "fees", "updown", "pnl"]
StatsPeriod = Literal["24h", "7d", "30d", "all"]


class StatsFeeFlow(TypedDict, total=False):
    """A fee flow in the row's asset (raw units); USD at the time of each event. ``fees`` adds ``basePart``,
    ``creatorPaid``, ``creatorRedirected``, ``retained``; ``vaultYield`` adds ``stasisAdded``."""

    amount: RawAmount
    usdE18: UsdE18
    usdCovered: int
    n: int
    basePart: RawAmount
    creatorPaid: RawAmount
    creatorRedirected: RawAmount
    retained: RawAmount
    stasisAdded: RawAmount


class StatsFeesRow(TypedDict, total=False):
    """One group x asset of ``GET /api/v1/stats/fees`` (the zero address = the native coin). ``referralPaid`` and
    ``buyback`` are None for groupBy=category, ``vaultYield`` for anything but groupBy=main."""

    group: Optional[str]
    asset: Optional[str]
    symbol: Optional[str]
    decimals: Optional[int]
    usdSource: UsdSource
    fees: StatsFeeFlow
    referralPaid: Optional[StatsFeeFlow]
    buyback: Optional[StatsFeeFlow]
    vaultYield: Optional[StatsFeeFlow]


StatsFees = TypedDict("StatsFees", {
    "data": List[StatsFeesRow],
    "pagination": Pagination,
    "groupBy": StatsFeesGroupBy,
    "from": int,
    "to": int,
}, total=False)


class StatsLeaderboardRow(TypedDict, total=False):
    """One wallet of ``GET /api/v1/stats/leaderboard``. ``viaRelay`` is metric=fees only; ``realizedUsdE18`` ..
    ``updownUsdE18``, ``trades``, ``tokens``, ``items`` are metric=pnl only (a P&L may be negative)."""

    rank: int
    wallet: str
    valueUsdE18: UsdE18
    pricedUsdE18: UsdE18
    usdCovered: int
    n: int
    viaRelay: Optional[int]
    realizedUsdE18: UsdE18
    unrealizedUsdE18: UsdE18
    trades: int
    tokens: int
    items: int
    feesUsdE18: UsdE18
    spotUsdE18: UsdE18
    predictionsUsdE18: UsdE18
    updownUsdE18: UsdE18
    wallets: int


class StatsLeaderboard(TypedDict, total=False):
    data: List[StatsLeaderboardRow]
    pagination: Pagination
    metric: StatsLeaderboardMetric
    period: StatsPeriod
    since: Optional[int]



MAX_PRICE_ADDRESSES = 100


class DiscoveryApi(ApiCore):
    """Discovery routes (mixin of ``BasisAPI``)."""

    def get_mains(self, include: Optional[Union[MainsInclude, Sequence[MainsInclude]]] = None) -> MainsList:
        """The mains, their pair assets and indexed state. ``GET /api/v1/mains``

        ``include``: ``"cashHeld"`` and/or ``"market"`` (one value or a list) adds live blocks (one chain read
        each). No auth.
        """
        if include is not None and not isinstance(include, str):
            include = ",".join(include) or None
        return self._request("GET", "/api/v1/mains", params={"include": include})

    def get_config(self) -> ApiConfig:
        """Everything needed before a first call: network, contracts, mains, limits, EIP-712. ``GET /api/v1/config``

        No auth.
        """
        return self._request("GET", "/api/v1/config")

    def _get_config_with_clock(self) -> Tuple[ApiConfig, Optional[float]]:
        """``GET /api/v1/config`` with the API's clock: ``(config, server_skew_ms)``, the skew being the answer's
        ``Date`` header (plus its ``Age``, for an answer a cache kept) minus the local clock, None without a usable
        ``Date``. Non-2xx raises BasisApiError. TS: ``_getConfigWithClock``."""
        res = self._send("GET", "/api/v1/config")
        if not res.ok:
            raise api_error_from(res, "GET /api/v1/config")
        config: ApiConfig = {}
        try:
            j = _json.loads(res.text) if res.text else {}
            if isinstance(j, dict):
                config = j  # type: ignore[assignment]
        except ValueError:
            pass
        skew: Optional[float] = None
        try:
            date = email.utils.parsedate_to_datetime(res.headers.get("Date") or "").timestamp() * 1000
        except (TypeError, ValueError, IndexError):
            date = None
        if date is not None:
            try:
                age = float(res.headers.get("Age") or 0)
            except ValueError:
                age = 0.0
            skew = date + (age * 1000 if age > 0 else 0) - time.time() * 1000
        return config, skew

    def get_status(self) -> ApiStatus:
        """Chain head, sync position and lag (unix seconds), feature switches. ``GET /api/v1/status``

        No auth.
        """
        return self._request("GET", "/api/v1/status")

    def get_contracts_json(self) -> ContractsJson:
        """Network and chain id of the deployment, plus its EIP-55 contract addresses when the site publishes them
        (none today). ``GET /contracts.json``

        No auth.
        """
        return self._request("GET", "/contracts.json")

    def get_agent_manifest(self) -> AgentManifest:
        """Agent discovery manifest. ``GET /.well-known/agent.json``

        No auth.
        """
        return self._request("GET", "/.well-known/agent.json")

    def get_ai_plugin_manifest(self) -> AiPluginManifest:
        """AI plugin manifest. ``GET /.well-known/ai-plugin.json``

        No auth.
        """
        return self._request("GET", "/.well-known/ai-plugin.json")

    def get_prices(self) -> PricesList:
        """USD price and source of every pair asset, bond asset and the native coin. ``GET /api/v1/prices``

        API key.
        """
        return self._request("GET", "/api/v1/prices", auth="key")

    def get_token_prices(self, addresses: Union[str, Sequence[str]]) -> TokenPricesList:
        """Current prices of 1 to 100 BASIS tokens (``found: False`` for unknown ones and for invite-only markets the
        caller may not see). ``GET /api/v1/prices/tokens``

        API key. Counted as the server counts them: split on commas, trimmed, lower-cased, without blanks and repeats
        (one row per distinct address, in the order given); anything other than strings raises
        ``ValidationError("addresses")``.
        """
        given = [addresses] if isinstance(addresses, str) else (
            list(addresses) if isinstance(addresses, (list, tuple)) else None)
        if given is None or any(not isinstance(a, str) for a in given):
            raise ValidationError("addresses", "addresses must be an address string or an array of address strings")
        items = list(dict.fromkeys(p.strip().lower() for a in given for p in a.split(",") if p.strip()))
        if not 1 <= len(items) <= MAX_PRICE_ADDRESSES:
            raise ValidationError("addresses", f"addresses must hold 1 to {MAX_PRICE_ADDRESSES} addresses (got {len(items)})")
        return self._request("GET", "/api/v1/prices/tokens", auth="key", params={"addresses": ",".join(items)})

    def get_stats(self, main: Optional[str] = None) -> PlatformStats:
        """Platform activity: counts, txns, trades and volume per window, per-main volume. ``GET /api/v1/stats``

        ``main`` (key or address) keeps only that main's ``byMain`` row. No auth.
        """
        return self._request("GET", "/api/v1/stats", params={"main": main})

    def get_stats_mains(self, main: Optional[str] = None) -> StatsMains:
        """Per main, each component of value held with its USD value; never summed. ``GET /api/v1/stats/mains``

        ``main`` (key or address) keeps one row. No auth.
        """
        return self._request("GET", "/api/v1/stats/mains", params={"main": main})

    def get_stats_fees(
        self,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        group_by: Optional[StatsFeesGroupBy] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> StatsFees:
        """Platform fee flows in [from_ts, to_ts) (unix seconds; default the last 30 days), one row per group and
        asset, largest USD first. ``GET /api/v1/stats/fees``

        ``group_by``: main | token | category. Offset paged (``page``, ``limit`` 1..100). API key or session.
        """
        return self._request("GET", "/api/v1/stats/fees", auth="anyKeyOrSession",
                             params={"from": from_ts, "to": to_ts, "groupBy": group_by, "page": page, "limit": limit})

    def get_stats_leaderboard(
        self,
        metric: Optional[StatsLeaderboardMetric] = None,
        period: Optional[StatsPeriod] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> StatsLeaderboard:
        """Wallets ranked by ``metric`` (volume | fees | updown | pnl) over ``period`` (24h | 7d | 30d | all; pnl takes
        7d, 30d or all). ``GET /api/v1/stats/leaderboard``

        Offset paged (``page``, ``limit`` 1..100). API key or session.
        """
        return self._request("GET", "/api/v1/stats/leaderboard", auth="anyKeyOrSession",
                             params={"metric": metric, "period": period, "page": page, "limit": limit})
