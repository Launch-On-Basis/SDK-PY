"""Tokens domain: /api/v1/tokens*, /api/v1/trades. Every route takes the API key only (no SIWE session).

TypeScript SDK: ``src/api/tokens.ts`` (response shapes are documented there; keys are camelCase as on the wire).
Window bounds (``from_ts`` / ``to_ts``) are unix seconds; the cursor-paged histories also accept ISO dates.
"""

from typing import List, Literal, Optional, TypedDict, Union, overload

from .core import ApiCore, seg
from .types import Cursor, E18, Pagination, RawAmount, UsdE18, UsdSource

TokenKind = Literal["main", "token", "reversed", "market", "marketPrivate", "updownPool"]
TokenPredictionStatus = Literal[
    "active", "awaiting_proposal", "proposed", "proposed_closed", "voting", "veto_window", "disputed", "vetoed",
    "awaiting_finalize", "resolved", "invalidated", "unknown",
]
TokenSort = Literal["newest", "oldest", "volume24h", "change24h", "mcap", "trades24h"]
TokenTradeType = Literal["buy", "sell", "leverage_buy", "leverage_sell"]
TradeLegRole = Literal[
    "trade", "route_hop", "cross_leg", "leverage_open", "leverage_exit", "loan_exit", "liquidation", "protocol",
    "convert",
]
CandleInterval = Literal["1m", "5m", "15m", "1h", "4h", "1d"]
TokenHistoryInterval = Literal["1h", "4h", "1d"]
TokenRewardKind = Literal["distributed", "claimed", "recycled", "creator_payout"]
TokenEventName = Literal["CreatorFeeSet", "RewardsFeeSet", "Bonded", "FreezeLifted", "WhiteListedWallet",
                         "WhitelistRemoved", "DevShareSet"]
TimeBound = Union[int, str]
"""Window bound of a history route: unix seconds (ms above 1e12 and ISO dates are accepted too)."""


class TokenPredictionOption(TypedDict, total=False):
    index: int
    name: str


class TokenAutoVest(TypedDict, total=False):
    enabled: bool
    durationDays: int
    gradual: bool


class TokenPresale(TypedDict, total=False):
    """``target``: raw main-token units; ``completedAt`` ISO."""

    target: RawAmount
    complete: bool
    completedAt: Optional[str]


class TokenSummary(TypedDict, total=False):
    """A token row of ``GET /api/v1/tokens``. Prices are x 1e18 decimal strings; supply and liquidity raw units."""

    id: Optional[int]
    address: str
    name: Optional[str]
    symbol: Optional[str]
    description: Optional[str]
    dev: Optional[str]
    image: Optional[str]
    multiplier: Optional[int]
    isPrediction: bool
    predictionType: Optional[str]
    predictionStatus: Optional[str]
    createdAt: Optional[str]
    lastActivityAt: Optional[str]
    chainId: int
    main: Optional[str]
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    pairSymbol: Optional[str]
    kind: Optional[TokenKind]
    reversed: Optional[bool]
    hybridMultiplier: Optional[int]
    rewardsEnabled: Optional[bool]
    frozen: Optional[bool]
    presaleTarget: Optional[RawAmount]
    presaleComplete: Optional[bool]
    listed: Optional[bool]
    creator: Optional[str]
    host: Optional[str]
    hasMetadata: bool
    createdBlock: Optional[int]
    priceE18: Optional[E18]
    priceMainE18: Optional[E18]
    priceUsdE18: UsdE18
    usdSource: UsdSource
    change24hBps: Optional[int]
    changeUsd24hBps: Optional[int]
    trades24h: int
    volume24hUsdE18: UsdE18
    volumeUsdSource: UsdSource
    volumeUsdCovered: int
    circulatingSupply: Optional[RawAmount]
    mcapUsdE18: UsdE18
    mcapUsdSource: UsdSource
    liquidity: Optional[RawAmount]
    depth: Optional[RawAmount]
    quoteAsset: Optional[str]
    quoteDecimals: Optional[int]


class TokenDetail(TokenSummary, total=False):
    """``GET /api/v1/tokens/{address}``: the summary plus config, supply, holders, floor and liquidity in USD.
    ``floorE18``: price floor, pair per whole token x 1e18, measured in the pair token, which can itself move.
    ``floorMainE18``: price floor, main per whole token x 1e18 (factory tokens only), measured in the main, whose
    price can move."""

    endTime: Optional[str]
    eventType: Optional[str]
    website: Optional[str]
    telegram: Optional[str]
    twitterx: Optional[str]
    creatorFeeBps: int
    rewardsFeeBps: int
    predictionOptions: Optional[List[TokenPredictionOption]]
    projectedVetted: Optional[bool]
    priceSource: Optional[Literal["index"]]
    totalSupply: Optional[RawAmount]
    lockedSupply: Optional[RawAmount]
    supplySource: Optional[Literal["index", "chain"]]
    holdersCount: int
    protocolHolders: int
    floorMainE18: Optional[E18]
    floorE18: Optional[E18]
    floorUsdE18: UsdE18
    floorUsdSource: UsdSource
    liquidityUsdE18: UsdE18
    liquidityUsdSource: UsdSource
    depthUsdE18: UsdE18
    depthUsdSource: UsdSource
    virtualLiquidityUsdE18: UsdE18
    virtualLiquidityUsdSource: UsdSource
    autoVest: Optional[TokenAutoVest]
    presale: Optional[TokenPresale]


class TokenCandle(TypedDict):
    """OHLC in whole units (chart numbers); ``time`` is the bucket start, unix seconds."""

    time: int
    open: float
    high: float
    low: float
    close: float


class TokenCandlesQuote(TypedDict, total=False):
    mode: Literal["token", "pair", "usd"]
    asset: Optional[str]
    symbol: str
    decimals: Optional[int]


class TokenCandles(TypedDict, total=False):
    """``truncated``: the window held more than 50,000 ticks, so the oldest candles may be missing."""

    data: List[TokenCandle]
    interval: str
    count: int
    truncated: bool
    main: Optional[str]
    quote: Optional[TokenCandlesQuote]


class TokenTrade(TypedDict, total=False):
    """One AMM leg of a token. ``amountQuote`` is in units of ``quoteAsset``; ``timestamp`` is ISO.

    ``valueUsdE18``: on a ``trade`` / ``cross_leg`` leg the trade's USD before fees (the figure ``get_trades`` shows and
    the 24 h volume sums); on any other leg the leg's own value. ``legValueUsdE18``: USD of this leg itself (a buy
    after the swap fee, a sell before it)."""

    id: int
    contractAddress: str
    type: TokenTradeType
    amountToken: RawAmount
    amountQuote: RawAmount
    quoteAsset: Optional[str]
    quoteDecimals: Optional[int]
    user: str
    priceE18: Optional[E18]
    main: Optional[str]
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    pairSymbol: Optional[str]
    valueUsdE18: UsdE18
    legValueUsdE18: UsdE18
    usdSource: UsdSource
    legRole: TradeLegRole
    tradeLogIndex: Optional[int]
    isPresale: bool
    userIsProtocol: bool
    viaContract: Optional[str]
    txHash: str
    logIndex: int
    blockNumber: int
    timestamp: str


class SwapTrade(TypedDict, total=False):
    """One user trade of ``GET /api/v1/trades``: a spot buy / sell or a cross-pair swap. ``blockTime`` is ISO."""

    kind: Literal["spot", "cross"]
    trader: str
    traderIsProtocol: bool
    traderPending: bool
    side: Optional[Literal["buy", "sell"]]
    tokenIn: str
    tokenOut: str
    amountIn: RawAmount
    amountOut: RawAmount
    tax: Optional[RawAmount]
    taxToken: Optional[str]
    token: Optional[str]
    main: Optional[str]
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    valuePair: Optional[RawAmount]
    valueUsdE18: UsdE18
    usdSource: UsdSource
    mainOut: Optional[str]
    pairAssetOut: Optional[str]
    pairDecimalsOut: Optional[int]
    valuePairOut: Optional[RawAmount]
    valueUsdOutE18: UsdE18
    usdSourceOut: UsdSource
    sellFee: Optional[RawAmount]
    buyFee: Optional[RawAmount]
    sellOut: Optional[RawAmount]
    buyIn: Optional[RawAmount]
    recipient: Optional[str]
    isPresale: Optional[bool]
    viaContract: Optional[str]
    txHash: str
    logIndex: int
    blockNumber: int
    blockTime: str


class TokenOrderFill(TypedDict, total=False):
    """An order-book fill of a market token. Amounts in units of pairAsset; shares have 18 decimals."""

    orderId: int
    outcomeId: int
    buyer: str
    seller: str
    shares: RawAmount
    basePair: RawAmount
    buyerPaid: RawAmount
    sellerNet: RawAmount
    main: Optional[str]
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    valueUsdE18: UsdE18
    usdSource: UsdSource
    host: str
    txHash: str
    logIndex: int
    blockNumber: int
    blockTime: str


class TokenComment(TypedDict, total=False):
    id: int
    author: str
    content: str
    tradeType: Optional[str]
    txHash: Optional[str]
    createdAt: str


class TokenWhitelistEntry(TypedDict, total=False):
    """``buyAmount`` / ``capTokens``: the most tokens (18 decimals) the wallet may buy while the token is frozen."""

    walletAddress: str
    buyAmount: RawAmount
    note: Optional[str]
    txHash: Optional[str]
    timestamp: str
    auto: bool
    capTokens: RawAmount
    unlimited: bool


class TokenWhitelistStatusEntry(TypedDict, total=False):
    walletAddress: str
    buyAmount: RawAmount
    note: Optional[str]
    txHash: Optional[str]
    timestamp: str
    auto: bool
    removedAt: Optional[str]


class TokenWhitelistStatus(TypedDict, total=False):
    """One wallet's whitelist state (``?wallet=``). ``enforced``: the list gates buys."""

    whitelisted: bool
    entry: Optional[TokenWhitelistStatusEntry]
    isDev: bool
    whitelistPhaseOver: bool
    frozen: Optional[bool]
    presaleComplete: Optional[bool]
    enforced: bool
    capTokens: Optional[RawAmount]
    unlimited: Optional[bool]


class TokenHolder(TypedDict, total=False):
    wallet: str
    balance: RawAmount
    shareBps: Optional[int]
    isProtocol: bool


class TokenHolders(TypedDict, total=False):
    token: str
    main: Optional[str]
    holdersCount: int
    protocolHolders: int
    circulatingSupply: Optional[RawAmount]
    data: List[TokenHolder]
    pagination: Pagination


class TokenReward(TypedDict, total=False):
    """A holder-reward or creator-payout row; ``amount`` in units of pairAsset, ``time`` unix seconds."""

    kind: TokenRewardKind
    account: Optional[str]
    accountIsProtocol: bool
    amount: RawAmount
    pairAsset: Optional[str]
    symbol: Optional[str]
    decimals: Optional[int]
    main: Optional[str]
    amountUsdE18: UsdE18
    usdSource: UsdSource
    perShare: Optional[str]
    txHash: str
    logIndex: int
    blockNumber: int
    time: int


class TokenRewardTotal(TypedDict, total=False):
    """Per kind and pair asset over the window (not the page); ``amount`` in units of pairAsset."""

    kind: TokenRewardKind
    pairAsset: Optional[str]
    symbol: Optional[str]
    decimals: Optional[int]
    amount: RawAmount
    amountUsdE18: UsdE18
    usdSource: UsdSource
    usdCovered: int
    n: int


class TokenFee(TypedDict, total=False):
    """A fee charged on the token; amounts in units of pairAsset, ``time`` unix seconds."""

    category: str
    payer: Optional[str]
    payerIsProtocol: bool
    payerResolved: Optional[str]
    source: str
    sourceRole: str
    trade: bool
    amount: RawAmount
    basePart: RawAmount
    creatorPaid: RawAmount
    creatorRedirected: RawAmount
    retained: RawAmount
    pairAsset: Optional[str]
    symbol: Optional[str]
    decimals: Optional[int]
    main: Optional[str]
    amountUsdE18: UsdE18
    usdSource: UsdSource
    txHash: str
    logIndex: int
    blockNumber: int
    time: int


class TokenFeeTotal(TypedDict, total=False):
    """Per category and pair asset over the window (not the page); ``amount`` in units of pairAsset."""

    category: str
    pairAsset: Optional[str]
    symbol: Optional[str]
    decimals: Optional[int]
    amount: RawAmount
    amountUsdE18: UsdE18
    usdSource: UsdSource
    usdCovered: int
    n: int


class TokenHistoryQuote(TypedDict, total=False):
    asset: Optional[str]
    symbol: Optional[str]
    decimals: Optional[int]


class TokenEvent(TypedDict, total=False):
    """A token config event; ``time`` unix seconds. ``bps``: fee events the new fee, DevShareSet the recipient's share
    of the creator fee (0 = removed); ``wallet``: whitelist events the wallet, DevShareSet the recipient; ``removed``:
    DevShareSet only, True when the share was removed (None otherwise)."""

    event: TokenEventName
    bps: Optional[int]
    wallet: Optional[str]
    removed: Optional[bool]
    capTokens: Optional[RawAmount]
    tag: Optional[str]
    actor: str
    emitter: str
    txHash: str
    logIndex: int
    blockNumber: int
    time: int


class TokenHistoryPoint(TypedDict, total=False):
    """One bucket: the last price and state (None when none fell in it), mints, burns, running supply."""

    time: int
    reserveToken: Optional[RawAmount]
    reserveQuote: Optional[RawAmount]
    priceE18: Optional[E18]
    priceUsdE18: UsdE18
    syncs: int
    minted: RawAmount
    burned: RawAmount
    supply: RawAmount


class TokensPage(TypedDict, total=False):
    data: List[TokenSummary]
    pagination: Pagination


class TokenDetailResponse(TypedDict):
    data: TokenDetail


class TokenTradesPage(TypedDict, total=False):
    data: List[TokenTrade]
    pagination: Pagination


class SwapTradesPage(TypedDict, total=False):
    data: List[SwapTrade]
    pagination: Pagination


class TokenFillsPage(TypedDict, total=False):
    data: List[TokenOrderFill]
    pagination: Pagination


class TokenCommentsPage(TypedDict, total=False):
    data: List[TokenComment]
    pagination: Pagination


class TokenWhitelistPage(TypedDict, total=False):
    data: List[TokenWhitelistEntry]
    pagination: Pagination


TokenRewards = TypedDict("TokenRewards", {
    "token": str, "from": Optional[int], "to": Optional[int], "data": List[TokenReward], "pagination": Pagination,
    "totals": List[TokenRewardTotal],
}, total=False)
TokenFees = TypedDict("TokenFees", {
    "token": str, "from": Optional[int], "to": Optional[int], "data": List[TokenFee], "pagination": Pagination,
    "totals": List[TokenFeeTotal],
}, total=False)
TokenEvents = TypedDict("TokenEvents", {
    "token": str, "from": Optional[int], "to": Optional[int], "data": List[TokenEvent], "pagination": Pagination,
}, total=False)
TokenHistory = TypedDict("TokenHistory", {
    "token": str, "main": Optional[str], "quote": Optional[TokenHistoryQuote], "interval": str, "from": int, "to": int,
    "count": int, "data": List[TokenHistoryPoint],
}, total=False)


def _token_path(address: Union[str, int], sub: str = "") -> str:
    return f"/api/v1/tokens/{seg(address)}{sub}"


class TokensApi(ApiCore):
    """Token routes (mixin of ``BasisAPI``)."""

    def get_tokens(
        self,
        search: Optional[str] = None,
        dev: Optional[str] = None,
        is_prediction: Optional[bool] = None,
        sort: Optional[TokenSort] = None,
        order: Optional[Literal["asc", "desc"]] = None,
        main: Optional[str] = None,
        kind: Optional[TokenKind] = None,
        prediction_status: Optional[TokenPredictionStatus] = None,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> TokensPage:
        """Every registered token, with or without metadata: ``{data: [TokenSummary], pagination}``.

        ``GET /api/v1/tokens``. API key. Offset paging (``page`` / ``offset``, which wins and is rounded down to a page
        start, a multiple of ``limit``). ``dev``: creator address; ``main``: a main key or address; ``order`` applies to
        the figure sorts.
        """
        params = {"search": search, "dev": dev, "isPrediction": is_prediction, "sort": sort, "order": order,
                  "main": main, "kind": kind, "predictionStatus": prediction_status, "page": page, "offset": offset,
                  "limit": limit}
        return self._request("GET", "/api/v1/tokens", auth="key", params=params)

    def get_token(self, address: str) -> TokenDetailResponse:
        """One token's details: ``{data: TokenDetail}``.

        ``GET /api/v1/tokens/{address}``. API key.
        """
        return self._request("GET", _token_path(address), auth="key")

    def get_token_candles(
        self,
        address: str,
        interval: Optional[CandleInterval] = None,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        limit: Optional[int] = None,
        quote: Optional[Literal["token", "pair", "usd"]] = None,
    ) -> TokenCandles:
        """OHLC candles (chart numbers, unix-second buckets).

        ``GET /api/v1/tokens/{address}/candles``. API key. ``quote``: token (default, the main per token), pair or usd.
        """
        params = {"interval": interval, "from": from_ts, "to": to_ts, "limit": limit, "quote": quote}
        return self._request("GET", _token_path(address, "/candles"), auth="key", params=params)

    def get_token_trades(
        self,
        address: str,
        trade_type: Optional[TokenTradeType] = None,
        include: Optional[Literal["protocol"]] = None,
        leg_role: Optional[TradeLegRole] = None,
        from_ts: Optional[TimeBound] = None,
        to_ts: Optional[TimeBound] = None,
        cursor: Optional[Cursor] = None,
        limit: Optional[int] = None,
        legs: Optional[Literal["all"]] = None,
    ) -> TokenTradesPage:
        """The token's AMM legs, newest first: ``{data: [TokenTrade], pagination}``.

        ``GET /api/v1/tokens/{address}/trades``. API key. Cursor paging. ``include="protocol"`` adds protocol legs. The
        hop leg of a routed trade (legRole ``route_hop``) is left out unless ``legs="all"`` or ``leg_role="route_hop"``.
        """
        params = {"type": trade_type, "include": include, "legs": legs, "legRole": leg_role, "from": from_ts,
                  "to": to_ts, "cursor": cursor, "limit": limit}
        return self._request("GET", _token_path(address, "/trades"), auth="key", params=params)

    def get_trades(
        self,
        trader: Optional[str] = None,
        token: Optional[str] = None,
        main: Optional[str] = None,
        kind: Optional[Literal["spot", "cross"]] = None,
        include: Optional[Literal["protocol"]] = None,
        from_ts: Optional[TimeBound] = None,
        to_ts: Optional[TimeBound] = None,
        cursor: Optional[Cursor] = None,
        limit: Optional[int] = None,
    ) -> SwapTradesPage:
        """User trades (spot and cross swaps), newest first; platform-wide without filters:
        ``{data: [SwapTrade], pagination}``.

        ``GET /api/v1/trades``. API key. Cursor paging. ``main``: a main key or address.
        """
        params = {"trader": trader, "token": token, "main": main, "kind": kind, "include": include, "from": from_ts,
                  "to": to_ts, "cursor": cursor, "limit": limit}
        return self._request("GET", "/api/v1/trades", auth="key", params=params)

    def get_token_fills(
        self,
        address: str,
        order_id: Optional[int] = None,
        outcome_id: Optional[int] = None,
        buyer: Optional[str] = None,
        seller: Optional[str] = None,
        cursor: Optional[Cursor] = None,
        limit: Optional[int] = None,
    ) -> TokenFillsPage:
        """Order-book fills of a market token, newest first: ``{data: [TokenOrderFill], pagination}``.

        ``GET /api/v1/tokens/{address}/fills``. API key. Cursor paging.
        """
        params = {"orderId": order_id, "outcomeId": outcome_id, "buyer": buyer, "seller": seller, "cursor": cursor,
                  "limit": limit}
        return self._request("GET", _token_path(address, "/fills"), auth="key", params=params)

    def get_token_comments(
        self,
        address: Union[str, int],
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> TokenCommentsPage:
        """Comments of a token (address or numeric project id): ``{data: [TokenComment], pagination}``.

        ``GET /api/v1/tokens/{address}/comments``. API key. Page paging.
        """
        return self._request("GET", _token_path(address, "/comments"), auth="key",
                             params={"page": page, "limit": limit})

    @overload
    def get_token_whitelist(self, address: str, wallet: str) -> TokenWhitelistStatus: ...

    @overload
    def get_token_whitelist(
        self,
        address: str,
        wallet: None = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> TokenWhitelistPage: ...

    def get_token_whitelist(
        self,
        address: str,
        wallet: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Union[TokenWhitelistPage, TokenWhitelistStatus]:
        """The whitelisted wallets ``{data: [TokenWhitelistEntry], pagination}`` (page paging), or with ``wallet``
        that wallet's state (``TokenWhitelistStatus``).

        ``GET /api/v1/tokens/{address}/whitelist``. API key.
        """
        return self._request("GET", _token_path(address, "/whitelist"), auth="key",
                             params={"wallet": wallet, "page": page, "limit": limit})

    def get_token_holders(
        self,
        address: str,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        protocol: Optional[Literal["include", "exclude"]] = None,
    ) -> TokenHolders:
        """Holders by balance, largest first, with holder counts and circulating supply.

        ``GET /api/v1/tokens/{address}/holders``. API key. Offset paging (``page`` / ``offset``).
        ``protocol="exclude"``: wallets only.
        """
        params = {"page": page, "offset": offset, "limit": limit, "protocol": protocol}
        return self._request("GET", _token_path(address, "/holders"), auth="key", params=params)

    def get_token_rewards(
        self,
        address: str,
        kind: Optional[TokenRewardKind] = None,
        from_ts: Optional[TimeBound] = None,
        to_ts: Optional[TimeBound] = None,
        cursor: Optional[Cursor] = None,
        limit: Optional[int] = None,
    ) -> TokenRewards:
        """Holder rewards and creator payouts, newest first, with per-kind totals over the window.

        ``GET /api/v1/tokens/{address}/rewards``. API key. Cursor paging.
        """
        params = {"kind": kind, "from": from_ts, "to": to_ts, "cursor": cursor, "limit": limit}
        return self._request("GET", _token_path(address, "/rewards"), auth="key", params=params)

    def get_token_fees(
        self,
        address: str,
        from_ts: Optional[TimeBound] = None,
        to_ts: Optional[TimeBound] = None,
        cursor: Optional[Cursor] = None,
        limit: Optional[int] = None,
    ) -> TokenFees:
        """Fees charged on the token, newest first, with per-category totals over the window.

        ``GET /api/v1/tokens/{address}/fees``. API key. Cursor paging.
        """
        params = {"from": from_ts, "to": to_ts, "cursor": cursor, "limit": limit}
        return self._request("GET", _token_path(address, "/fees"), auth="key", params=params)

    def get_token_events(
        self,
        address: str,
        event: Optional[TokenEventName] = None,
        from_ts: Optional[TimeBound] = None,
        to_ts: Optional[TimeBound] = None,
        cursor: Optional[Cursor] = None,
        limit: Optional[int] = None,
    ) -> TokenEvents:
        """Config history (fees, dev shares, bonding, freeze, whitelist), newest first.

        ``GET /api/v1/tokens/{address}/events``. API key. Cursor paging.
        """
        params = {"event": event, "from": from_ts, "to": to_ts, "cursor": cursor, "limit": limit}
        return self._request("GET", _token_path(address, "/events"), auth="key", params=params)

    def get_token_history(
        self,
        address: str,
        interval: Optional[TokenHistoryInterval] = None,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
    ) -> TokenHistory:
        """Price and supply per bucket from stored events.

        ``GET /api/v1/tokens/{address}/history``. API key.
        """
        params = {"interval": interval, "from": from_ts, "to": to_ts}
        return self._request("GET", _token_path(address, "/history"), auth="key", params=params)
