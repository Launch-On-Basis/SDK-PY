"""Wallet domain: /api/v1/wallet/{address}/(transactions|portfolio|fees|referrals|earnings|activity|summary|projects|roles|
followers|following|pnl), /api/v1/tx/{hash}, /api/v1/me/(stats|projects|referrals|fees|earnings), /api/v1/leaderboard,
/api/v1/profile/{wallet}*.

Response types follow the spec (keys as on the wire, camelCase); deprecated fields are left out. TypeScript SDK:
src/api/wallet.ts.
"""

from typing import Any, Dict, List, Literal, Optional, Sequence, Union

from ..core.errors import ValidationError
from .core import ApiCore, seg
from .types import E18, Pagination, PublicPersonRef, RawAmount, TypedDict, UsdE18, UsdSource

UsdSourceName = Literal["fixed", "feed", "hop", "offchain"]
WalletTransactionType = Literal["buy", "sell", "leverage_buy", "leverage_sell"]
WalletLegRole = Literal["trade", "route_hop", "cross_leg", "leverage_open", "leverage_exit", "loan_exit", "liquidation",
                        "protocol", "convert"]
ActivityRowType = Literal["trade", "swap", "prediction", "book_fill", "loan", "vault", "vesting", "market", "referral",
                          "updown", "fee", "creator_payout", "reward_claim", "transfer", "create", "liquidation"]
WalletLegRoleFilter = Literal["trade", "route_hop", "cross_leg", "leverage_open", "leverage_exit", "loan_exit",
                              "liquidation", "convert"]
"""``legRole`` filter values: :data:`WalletLegRole` without ``protocol`` (protocol legs are never listed)."""
ActivityFilterType = Literal["trade", "swap", "prediction", "book_fill", "loan", "vault", "vesting", "market", "referral",
                             "updown", "fee", "creator_payout", "reward_claim", "transfer", "create"]
"""Activity ``types`` filter values: :data:`ActivityRowType` without ``liquidation`` (only on ``get_tx``)."""
IndexedTxStatus = Literal["synced", "partial", "reverted", "ignored", "dropped", "not_synced"]
PnlPeriod = Literal["week", "month", "all"]
TimeArg = Union[int, str]
"""Unix seconds (an ISO 8601 string is accepted too)."""


class WalletUsdTotal(TypedDict, total=False):
    """A sum over priced rows: ``totalUsdE18`` is None unless every row is priced."""

    totalUsdE18: UsdE18
    usdCovered: int
    n: int
    usdSources: List[UsdSourceName]



class WalletTransaction(TypedDict, total=False):
    """One AMM leg of a wallet (spec ``Trade`` plus ``userArg``). ``priceE18``: pair-asset units per token x 1e18.

    ``valueUsdE18``: on a ``trade`` / ``cross_leg`` leg the trade's USD before fees (the figure ``get_trades`` shows and
    the 24 h volume sums); on any other leg the leg's own value. ``legValueUsdE18``: USD of this leg itself (a buy
    after the swap fee, a sell before it)."""

    id: int
    contractAddress: str
    type: WalletTransactionType
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
    legRole: WalletLegRole
    tradeLogIndex: Optional[int]
    isPresale: bool
    userIsProtocol: bool
    txHash: str
    logIndex: int
    blockNumber: int
    timestamp: str
    userArg: Optional[str]


class WalletTransactionsPage(TypedDict):
    data: List[WalletTransaction]
    pagination: Pagination



class WalletPairBalance(TypedDict, total=False):
    main: str
    mainKey: str
    pairAsset: str
    pairSymbol: str
    pairDecimals: int
    balanceRaw: RawAmount
    balance: str
    valueUsdE18: UsdE18


class WalletHolding(TypedDict, total=False):
    """``balanceWei`` raw (``decimals``), ``balance`` whole tokens as an exact decimal string. ``heldFromTrades``: bought
    tokens still held, wherever they sit; ``lockedFromTrades``: its part outside the wallet (still vesting, on a hub loan
    or staked in the vault), not in ``balance``. ``costBasisUsdE18``: what the bought tokens still held cost, the locked
    ones included (average cost, after fees); None when a leg has no USD or the history cannot be followed."""

    address: str
    symbol: Optional[str]
    main: Optional[str]
    isMain: bool
    balanceWei: RawAmount
    balance: str
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    pricePairRaw: Optional[RawAmount]
    priceUsdE18: UsdE18
    valueUsdE18: UsdE18
    costBasisUsdE18: UsdE18
    realizedPnlUsdE18: UsdE18
    heldFromTrades: RawAmount
    lockedFromTrades: RawAmount
    decimals: int


class WalletVaultLine(TypedDict, total=False):
    """A main's stored vault position net of its loan (pending rewards not counted)."""

    main: str
    vault: str
    staked: RawAmount
    pledged: RawAmount
    hasActiveLoan: bool
    debtRaw: Optional[RawAmount]
    pairAsset: str
    pairDecimals: int
    valueUsdE18: UsdE18


class WalletLoanLine(TypedDict, total=False):
    id: str
    source: Literal["hub", "leverage"]
    main: str
    token: str
    collateralWei: RawAmount
    fullAmountRaw: RawAmount
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    netUsdE18: UsdE18


class WalletVestingLine(TypedDict, total=False):
    """Unvested tokens of a stored schedule (token units, 18 decimals). ``loanDebtRaw``: the vesting loan's debt, raw
    pair units of the vesting's main (None without a loan, or while the loan is not synced); ``valueUsdE18``: USD of
    ``remaining`` less that debt (floored at 0), None while the debt is unknown."""

    vestingContract: str
    vestingId: int
    token: str
    main: Optional[str]
    totalAmount: RawAmount
    claimedAmount: RawAmount
    remaining: RawAmount
    activeLoanId: int
    loanDebtRaw: Optional[RawAmount]
    valueUsdE18: UsdE18
    usdSource: UsdSource


class WalletPredictionShare(TypedDict, total=False):
    outcomeId: int
    shares: RawAmount
    probabilityE18: Optional[E18]


class WalletPredictionLine(TypedDict, total=False):
    """Prediction shares valued at the stored odds."""

    marketToken: str
    host: str
    hostKind: Optional[Literal["public", "private"]]
    main: Optional[str]
    resolved: bool
    shares: List[WalletPredictionShare]
    valueTokens: Optional[RawAmount]
    valueUsdE18: UsdE18
    usdSource: UsdSource


class WalletUpDownStake(TypedDict, total=False):
    """An open Up/Down stake. ``endTime`` unix seconds."""

    contract: str
    asset: Optional[int]
    tf: int
    roundId: int
    main: Optional[str]
    poolToken: Optional[str]
    settleAsset: Optional[str]
    settleDecimals: Optional[int]
    status: Literal["open"]
    side: Literal["bull", "bear"]
    stake: RawAmount
    shares: RawAmount
    endTime: Optional[int]
    paidUsdE18: UsdE18
    usdSource: UsdSource


class WalletCostBasisToken(TypedDict, total=False):
    token: str
    heldFromTrades: RawAmount
    lockedFromTrades: RawAmount
    costBasisUsdE18: UsdE18
    realizedPnlUsdE18: UsdE18
    buys: int
    sells: int


class WalletCostBasis(TypedDict, total=False):
    tokens: List[WalletCostBasisToken]
    realizedPnlUsdE18: UsdE18
    note: str


class WalletPortfolio(TypedDict, total=False):
    """``vesting``, ``predictions`` and ``updown`` are listed apart and not in ``totalUsdE18``. ``predictions`` covers
    the 50 most recent markets and ``updown`` the 50 most recent open rounds; ``predictionsTruncated`` /
    ``updownTruncated`` are True when the wallet has more."""

    address: str
    deployed: bool
    pairBalances: List[WalletPairBalance]
    holdings: List[WalletHolding]
    vaults: List[WalletVaultLine]
    loans: List[WalletLoanLine]
    vesting: List[WalletVestingLine]
    holdingsCount: int
    totalUsdE18: str
    unpriced: int
    note: str
    predictions: List[WalletPredictionLine]
    predictionsTruncated: bool
    updown: List[WalletUpDownStake]
    updownTruncated: bool
    costBasis: WalletCostBasis



class WalletFeeRow(TypedDict, total=False):
    """``pairAsset`` None while unknown; the zero address is the native coin. ``category`` only with by_category."""

    pairAsset: Optional[str]
    symbol: Optional[str]
    decimals: Optional[int]
    nativeCoin: bool
    category: str
    fees: RawAmount
    baseFees: RawAmount
    retained: RawAmount
    feesUsdE18: UsdE18
    usdSource: UsdSource
    usdCovered: int
    n: int


WalletFees = TypedDict("WalletFees", {
    "wallet": str,
    "from": str,
    "to": str,
    "data": List[WalletFeeRow],
    "total": WalletUsdTotal,
    "relayUnresolved": List[WalletFeeRow],
    "nativeCoinCategories": List[str],
}, total=False)


class WalletEarningRow(TypedDict, total=False):
    pairAsset: Optional[str]
    symbol: Optional[str]
    decimals: Optional[int]
    amount: RawAmount
    amountUsdE18: UsdE18
    usdSource: UsdSource
    usdCovered: int
    n: int


class WalletTokenEarningRow(WalletEarningRow, total=False):
    token: str


class WalletHolderRewardRow(WalletEarningRow, total=False):
    token: str
    main: Optional[str]


WalletEarnings = TypedDict("WalletEarnings", {
    "wallet": str,
    "from": str,
    "to": str,
    "data": List[WalletEarningRow],
    "total": WalletUsdTotal,
    "byToken": List[WalletTokenEarningRow],
    "redirected": List[WalletEarningRow],
    "holderRewards": List[WalletHolderRewardRow],
    "holderRewardsTotal": WalletUsdTotal,
}, total=False)



class ReferralAssetAmount(TypedDict, total=False):
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    symbol: Optional[str]
    amount: RawAmount
    amountUsdE18: UsdE18
    usdSource: UsdSource
    usdCovered: int
    n: int


class ReferralEarning(ReferralAssetAmount, total=False):
    level: int
    wallets: int


class ReferralEarned(ReferralAssetAmount, total=False):
    amountL1: Optional[RawAmount]
    amountL2: Optional[RawAmount]


class ReferralFeesGenerated(ReferralAssetAmount, total=False):
    viaRelay: int


class OnchainReferral(TypedDict, total=False):
    wallet: str
    referrer: str
    level: int
    earned: List[ReferralEarned]
    feesGenerated: List[ReferralFeesGenerated]


class ReferralRedirect(TypedDict, total=False):
    """One redirected referral payout (``onchain.redirects``): raw ``amount`` in ``pairAsset`` units, sent to the
    protocol (``sentTo`` ``"platform"``; its address is not served); ``time`` unix seconds."""

    token: str
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    symbol: Optional[str]
    amount: RawAmount
    amountUsdE18: UsdE18
    usdSource: UsdSource
    sentTo: Literal["platform"]
    txHash: str
    blockNumber: int
    logIndex: int
    time: int


OnchainReferrals = TypedDict("OnchainReferrals", {
    "referrer": Optional[str],
    "directCount": int,
    "indirectCount": int,
    "from": str,
    "to": str,
    "depth": int,
    "earnings": List[ReferralEarning],
    "earningsTotal": WalletUsdTotal,
    "redirected": List[ReferralAssetAmount],
    "redirectedTotal": WalletUsdTotal,
    "redirects": List[ReferralRedirect],
    "redirectsTruncated": bool,
    "referrals": List[OnchainReferral],
    "referralsPagination": Pagination,
}, total=False)
OnchainReferrals.__doc__ = (
    "The on-chain fee referral of a wallet; ``from`` / ``to`` the ISO 8601 window applied. ``redirected`` (per "
    "pair asset) / ``redirectedTotal`` / ``redirects`` (newest 100, ``redirectsTruncated`` when there are more): "
    "referral payouts meant for the wallet that it could not receive, sent to the protocol instead; "
    "not earned, never in ``earnings``.")


class WalletReferralPoints(TypedDict, total=False):
    hasReferrer: bool
    directCount: int
    indirectCount: int
    pointsStale: bool


class WalletReferrals(TypedDict, total=False):
    """``onchain`` is None when on-chain referrals are not live on this network."""

    wallet: str
    onchain: Optional[OnchainReferrals]
    points: WalletReferralPoints


class ReferrerRef(TypedDict, total=False):
    wallet: str
    username: Optional[str]


class ReferredWallet(TypedDict, total=False):
    """``joinedAt`` ISO 8601 (the block time the referrer was set)."""

    wallet: str
    username: Optional[str]
    layer: int
    joinedAt: str


class MyReferrals(TypedDict, total=False):
    wallet: str
    onchain: Optional[OnchainReferrals]
    referrer: Optional[ReferrerRef]
    directCount: int
    indirectCount: int
    referrals: List[ReferredWallet]



class ActivityAmount(TypedDict, total=False):
    role: str
    value: RawAmount
    asset: Optional[str]
    decimals: Optional[int]


class ActivityRow(TypedDict, total=False):
    """One indexed row of any table. ``time`` ISO 8601; ``details`` table-specific, e.g. referral ``referral_paid
    {trader, level}``, ``referral_redirected {sentTo: "platform", feeLogIndex}`` (a payout the wallet could not receive,
    sent to the protocol: not earned), ``referrer_set {referrer}``; create ``{kind}`` (action ``token_created`` |
    ``market_created``; a market's row carries its seed in ``amounts``, role ``seed``, and its USD in ``valueUsdE18``)."""

    type: ActivityRowType
    action: str
    table: str
    txHash: str
    blockNumber: int
    logIndex: int
    time: str
    user: Optional[str]
    token: Optional[str]
    main: Optional[str]
    amounts: List[ActivityAmount]
    valueUsdE18: UsdE18
    usdSource: UsdSource
    details: Optional[Dict[str, Any]]


class ActivityPage(TypedDict):
    data: List[ActivityRow]
    pagination: Pagination


IndexedTx = TypedDict("IndexedTx", {
    "txHash": str,
    "status": IndexedTxStatus,
    "blockNumber": Optional[int],
    "time": Optional[str],
    "from": Optional[str],
    "to": Optional[str],
    "basisLogs": Optional[int],
    "confirmed": Optional[bool],
    "lastSyncedAt": Optional[str],
    "rows": List[ActivityRow],
})
IndexedTx.__doc__ = "What the index holds for one transaction (``time`` / ``lastSyncedAt`` ISO 8601)."



class FirstSeen(TypedDict, total=False):
    time: str
    blockNumber: int
    txHash: str


class AgentRef(TypedDict, total=False):
    agentId: Optional[int]
    name: Optional[str]
    description: Optional[str]


class ProfileAgent(TypedDict, total=False):
    """Agent block of the public profile (``agentId`` is never None there)."""

    agentId: int
    name: Optional[str]
    description: Optional[str]


class WalletSummary(TypedDict, total=False):
    """Counts and totals of a wallet (also ``GET /me/stats``)."""

    wallet: str
    trades: int
    volumeUsdE18: UsdE18
    volumeUsdCovered: int
    volumeN: int
    volumeUsdSources: List[UsdSourceName]
    feesPaidUsdE18: UsdE18
    feesPaidUsdCovered: int
    feesPaidN: int
    feesPaidUsdSources: List[UsdSourceName]
    referralEarnedUsdE18: UsdE18
    referralEarnedUsdCovered: int
    referralEarnedN: int
    referralEarnedUsdSources: List[UsdSourceName]
    tokensCreated: int
    marketsCreated: int
    firstSeen: Optional[FirstSeen]
    agent: Optional[AgentRef]
    totalTrades: int
    buys: int
    sells: int
    crossSwaps: int
    leverageOpens: int
    loanSells: int
    totalPredictions: int
    bookFills: int
    upDownBets: int
    totalLoans: int
    activeLoans: int
    marketWins: int
    daysActive: int


class WalletProjectToken(TypedDict, total=False):
    """``totalSupply`` token wei, from indexed data; None when not indexed."""

    address: str
    name: Optional[str]
    symbol: Optional[str]
    image: Optional[str]
    createdAt: Optional[str]
    indexed: bool
    main: Optional[str]
    kind: Optional[str]
    reversed: Optional[bool]
    hybridMultiplier: Optional[int]
    createdBlock: Optional[int]
    createdTx: Optional[str]
    totalSupply: Optional[RawAmount]
    priceUsdE18: UsdE18
    usdSource: UsdSource
    mcapUsdE18: UsdE18
    mcapUsdSource: UsdSource
    frozen: Optional[bool]
    presaleComplete: Optional[bool]


class WalletProjectMarket(TypedDict, total=False):
    """``endTime`` unix seconds (None until indexed)."""

    address: str
    name: Optional[str]
    symbol: Optional[str]
    image: Optional[str]
    createdAt: Optional[str]
    indexed: bool
    main: Optional[str]
    host: Optional[str]
    isPrivate: Optional[bool]
    endTime: Optional[int]
    numOutcomes: Optional[int]
    createdBlock: Optional[int]
    createdTx: Optional[str]


class WalletProjectsPagination(Pagination, total=False):
    """Paging of one :class:`WalletProjects` list. ``truncated``: the list holds the newest 1,000 rows of its kind and
    the wallet has more (``total`` counts them all)."""

    truncated: bool


class WalletProjects(TypedDict, total=False):
    """Tokens and markets a wallet created; each list offset paged with the same page / offset / limit."""

    wallet: str
    tokens: List[WalletProjectToken]
    markets: List[WalletProjectMarket]
    tokensPagination: WalletProjectsPagination
    marketsPagination: WalletProjectsPagination


class WalletWhitelistRole(TypedDict, total=False):
    token: str
    capTokens: RawAmount
    auto: bool
    note: Optional[str]
    txHash: str
    blockNumber: int


class WalletPrivateMarketRole(TypedDict, total=False):
    marketToken: str
    host: str
    voter: bool
    buyer: bool
    updatedBlock: Optional[int]


class WalletRoles(TypedDict, total=False):
    """Each list holds at most 1000 rows: ``whitelistsTotal`` / ``privateMarketsTotal`` count every row and
    ``whitelistsTruncated`` / ``privateMarketsTruncated`` say a list was cut."""

    wallet: str
    whitelists: List[WalletWhitelistRole]
    whitelistsTotal: int
    whitelistsTruncated: bool
    privateMarkets: List[WalletPrivateMarketRole]
    privateMarketsTotal: int
    privateMarketsTruncated: bool


class WalletFollow(TypedDict, total=False):
    """``followedAt`` unix seconds."""

    wallet: str
    username: Optional[str]
    avatarUrl: Optional[str]
    followedAt: Optional[int]


class WalletFollows(TypedDict, total=False):
    wallet: str
    data: List[WalletFollow]
    pagination: Pagination



class WalletPnlPart(TypedDict, total=False):
    """One part of a wallet's P&L after fees (USD x 1e18). The ``priced*`` fields sum the counted items, never None."""

    totalUsdE18: UsdE18
    realizedUsdE18: UsdE18
    unrealizedUsdE18: UsdE18
    pricedSubtotalUsdE18: UsdE18
    pricedRealizedUsdE18: UsdE18
    pricedUnrealizedUsdE18: UsdE18
    feesUsdE18: UsdE18
    complete: bool
    trades: int
    items: int
    missingItems: int
    missingHistory: int
    missingPrices: int
    estimated: int


class WalletPnlSpot(WalletPnlPart, total=False):
    tokens: int
    missingTokens: int


class WalletPnlPosition(WalletPnlPart, total=False):
    token: str
    main: Optional[str]
    tokens: int
    missingTokens: int
    usdSource: UsdSource


class WalletPnlMarket(WalletPnlPart, total=False):
    marketToken: str


class WalletPnl(WalletPnlPart, total=False):
    """``asOf`` unix seconds of the answer (cached 60 s)."""

    wallet: str
    period: PnlPeriod
    asOf: int
    tokens: int
    missingTokens: int
    spot: WalletPnlSpot
    predictions: WalletPnlPart
    updown: WalletPnlPart
    usdSources: List[UsdSourceName]
    positions: List[WalletPnlPosition]
    markets: List[WalletPnlMarket]



class ProfileSocial(TypedDict, total=False):
    platform: str
    handle: str
    url: Optional[str]


class LeaderboardEntry(TypedDict, total=False):
    """``profileId``: opaque person reference, not a wallet address (:meth:`~WalletApi.get_leaderboard`).
    ``get_feed_by_wallet`` takes it; not for a contract call."""

    rank: int
    profileId: PublicPersonRef
    username: Optional[str]
    avatarUrl: Optional[str]
    tier: str
    tierEmoji: str
    socials: List[ProfileSocial]


class Leaderboard(TypedDict, total=False):
    """The final testing-phase standings: points were removed, so they never change (``frozen`` is always True).
    ``takenAt``: ISO time they were recorded, None when the table is empty."""

    data: List[LeaderboardEntry]
    pagination: Pagination
    frozen: bool
    frozenReason: str
    takenAt: Optional[str]


class PublicProfile(TypedDict, total=False):
    """``GET /api/v1/profile/{wallet}``: your own wallet's identity (owner only). ``profileId``: the opaque person
    reference public lists show in its place. ``tier`` / ``tierEmoji``: reached in the testing phase, None when the wallet
    was not on the leaderboard (``rank`` 0). ``lastUpdated`` ISO 8601; ``stats`` links to the wallet summary route."""

    wallet: str
    profileId: PublicPersonRef
    username: Optional[str]
    avatarUrl: Optional[str]
    tier: Optional[str]
    tierEmoji: Optional[str]
    rank: int
    socials: List[ProfileSocial]
    xHandle: Optional[str]
    lastUpdated: Optional[str]
    agent: Optional[ProfileAgent]
    pointsStale: bool
    stats: str
    followers: int
    following: int


class ProfileReferralCounts(TypedDict, total=False):
    hasReferrer: bool
    directReferrals: int
    indirectReferrals: int
    totalReferrals: int


class OnchainReferralCounts(ProfileReferralCounts, total=False):
    referrer: Optional[str]


class ProfileReferrals(ProfileReferralCounts, total=False):
    """``onchain`` is None when on-chain referrals are not live on this network; ``details`` links to the full on-chain
    block."""

    wallet: str
    onchain: Optional[OnchainReferralCounts]
    pointsStale: bool
    details: str


def _types_list(types: Union[ActivityFilterType, Sequence[ActivityFilterType], None]) -> Optional[str]:
    """Activity ``types`` as the comma list the route takes; None is absent. A filter that names no type (``[]``,
    ``""``, only commas or spaces) would read as every type on the route: ``ValidationError('types')`` before any
    request, as an entry that is not a string. Same rule in TS."""
    if types is None:
        return None
    if isinstance(types, str):
        items: List[Any] = [types]
    elif isinstance(types, (list, tuple, set, frozenset)):
        items = list(types)
    else:
        items = [types]
    if any(not isinstance(x, str) for x in items):
        raise ValidationError("types", "types must be an activity type or a list of them (strings), e.g. ['trade', 'loan'].")
    joined = ",".join(items)
    if not any(x.strip() for x in joined.split(",")):
        raise ValidationError("types", "types names no activity type: give at least one (e.g. ['trade']), or leave it "
                                       "out for every type.")
    return joined


def _page(page: Optional[int], offset: Optional[int], limit: Optional[int]) -> Dict[str, Any]:
    """Offset paging (``pagination.page`` / ``total``): ``offset`` wins over ``page`` and is rounded down to a page
    start (a multiple of ``limit``); limit 1..100. TS: ``WalletPageParams``."""
    return {"page": page, "offset": offset, "limit": limit}


class WalletApi(ApiCore):
    """Wallet, profile and leaderboard routes (mixin of ``BasisAPI``)."""

    def get_wallet_transactions(
        self,
        address: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        trade_type: Optional[WalletTransactionType] = None,
        legs: Optional[Literal["all"]] = None,
        leg_role: Optional[WalletLegRoleFilter] = None,
        main: Optional[str] = None,
    ) -> WalletTransactionsPage:
        """The wallet's AMM legs, newest first. ``GET /api/v1/wallet/{address}/transactions`` (API key).

        ``legs="all"`` adds the hop leg of routed trades; ``leg_role`` keeps one role only; ``main`` is a main key or
        address. Cursor-paged: ``api.paginate(lambda **p: api.get_wallet_transactions(wallet, **p), limit=100)``.
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/transactions", auth="key", params={
            "cursor": cursor, "limit": limit, "type": trade_type, "legs": legs, "legRole": leg_role, "main": main})

    def get_wallet_activity(
        self,
        address: str,
        types: Union[ActivityFilterType, Sequence[ActivityFilterType], None] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> ActivityPage:
        """Every kind of activity of the wallet in one feed, newest first.

        ``GET /api/v1/wallet/{address}/activity`` (API key). ``types``: activity types (list or comma string; default
        all, ``liquidation`` rows are only on :meth:`get_tx`; one that names no type, ``[]`` or ``""``, raises
        ``ValidationError('types')`` before any request). Cursor-paged like :meth:`get_wallet_transactions`.
        ``create`` = the tokens and markets the wallet created; ``referral`` includes payouts redirected to the protocol
        (``referral_redirected``, not earned).
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/activity", auth="key",
                             params={"types": _types_list(types), "cursor": cursor, "limit": limit})

    def get_tx(self, tx_hash: str) -> IndexedTx:
        """What the index holds for one transaction (status and its rows). ``GET /api/v1/tx/{hash}`` (API key)."""
        return self._request("GET", f"/api/v1/tx/{seg(tx_hash)}", auth="key")

    def get_wallet_portfolio(self, address: str) -> WalletPortfolio:
        """Holdings, pair balances, vaults, loans and positions in USD x 1e18.

        ``GET /api/v1/wallet/{address}/portfolio`` (API key).
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/portfolio", auth="key")

    def get_wallet_summary(self, address: str) -> WalletSummary:
        """Counts and totals of a wallet. ``GET /api/v1/wallet/{address}/summary`` (API key). Own wallet:
        :meth:`get_my_stats`."""
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/summary", auth="key")

    def get_wallet_pnl(self, address: str, period: Optional[PnlPeriod] = None) -> WalletPnl:
        """P&L after fees (spot + predictions + Up/Down) for ``period`` (server default ``all``). Counts bought tokens
        only: tokens received or given away (transfers, vestings, vault rewards) are left out.

        ``GET /api/v1/wallet/{address}/pnl`` (API key).
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/pnl", auth="key", params={"period": period})

    def get_wallet_roles(self, address: str) -> WalletRoles:
        """Token whitelists and private-market roles of a wallet. ``GET /api/v1/wallet/{address}/roles`` (API key)."""
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/roles", auth="key")

    def get_wallet_projects(
        self, address: str, page: Optional[int] = None, offset: Optional[int] = None, limit: Optional[int] = None,
    ) -> WalletProjects:
        """Tokens and markets the wallet created. ``GET /api/v1/wallet/{address}/projects`` (API key).

        Offset-paged: both lists take the same page / offset / limit (default 20).
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/projects", auth="key",
                             params=_page(page, offset, limit))

    def get_wallet_fees(
        self,
        address: str,
        from_ts: Optional[TimeArg] = None,
        to_ts: Optional[TimeArg] = None,
        by_category: Optional[bool] = None,
    ) -> WalletFees:
        """Fees the wallet paid per pair asset (and category with ``by_category``) in a window.

        ``GET /api/v1/wallet/{address}/fees`` (API key). ``from_ts`` / ``to_ts``: unix seconds or ISO 8601.
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/fees", auth="key",
                             params={"from": from_ts, "to": to_ts, "byCategory": by_category})

    def get_wallet_earnings(
        self, address: str, from_ts: Optional[TimeArg] = None, to_ts: Optional[TimeArg] = None,
    ) -> WalletEarnings:
        """Creator payouts and holder rewards in a window. ``GET /api/v1/wallet/{address}/earnings`` (API key)."""
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/earnings", auth="key",
                             params={"from": from_ts, "to": to_ts})

    def get_wallet_referrals(
        self,
        address: str,
        from_ts: Optional[TimeArg] = None,
        to_ts: Optional[TimeArg] = None,
        depth: Optional[int] = None,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> WalletReferrals:
        """On-chain referral tree and earnings, plus the referral counts.

        ``GET /api/v1/wallet/{address}/referrals`` (API key). ``depth`` 1..5 (default 2); ``onchain.referrals`` is
        offset-paged (default limit 50).
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/referrals", auth="key", params={
            "from": from_ts, "to": to_ts, "depth": depth, **_page(page, offset, limit)})

    def get_wallet_followers(
        self, address: str, page: Optional[int] = None, offset: Optional[int] = None, limit: Optional[int] = None,
    ) -> WalletFollows:
        """Wallets following this one, newest first; offset-paged. ``GET /api/v1/wallet/{address}/followers``
        (API key). Rows keep the address; ``username`` and ``avatarUrl`` are None (profile privacy)."""
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/followers", auth="key",
                             params=_page(page, offset, limit))

    def get_wallet_following(
        self, address: str, page: Optional[int] = None, offset: Optional[int] = None, limit: Optional[int] = None,
    ) -> WalletFollows:
        """Wallets this one follows, newest first; offset-paged. ``GET /api/v1/wallet/{address}/following``
        (API key). Rows keep the address; ``username`` and ``avatarUrl`` are None (profile privacy)."""
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/following", auth="key",
                             params=_page(page, offset, limit))

    def get_my_stats(self) -> WalletSummary:
        """:meth:`get_wallet_summary` for the authenticated wallet. ``GET /api/v1/me/stats`` (API key or session)."""
        return self._request("GET", "/api/v1/me/stats", auth="keyOrSession")

    def get_my_projects(
        self, page: Optional[int] = None, offset: Optional[int] = None, limit: Optional[int] = None,
    ) -> WalletProjects:
        """:meth:`get_wallet_projects` for the authenticated wallet; offset-paged.

        ``GET /api/v1/me/projects`` (API key or session).
        """
        return self._request("GET", "/api/v1/me/projects", auth="keyOrSession", params=_page(page, offset, limit))

    def get_my_referrals(
        self,
        from_ts: Optional[TimeArg] = None,
        to_ts: Optional[TimeArg] = None,
        depth: Optional[int] = None,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> MyReferrals:
        """Who referred you, the wallets you referred (levels 1-2) and the on-chain block.

        ``GET /api/v1/me/referrals`` (API key or session). ``onchain.referrals`` is offset-paged.
        """
        return self._request("GET", "/api/v1/me/referrals", auth="keyOrSession", params={
            "from": from_ts, "to": to_ts, "depth": depth, **_page(page, offset, limit)})

    def get_my_fees(
        self, from_ts: Optional[TimeArg] = None, to_ts: Optional[TimeArg] = None, by_category: Optional[bool] = None,
    ) -> WalletFees:
        """:meth:`get_wallet_fees` for the authenticated wallet. ``GET /api/v1/me/fees`` (API key or session)."""
        return self._request("GET", "/api/v1/me/fees", auth="keyOrSession",
                             params={"from": from_ts, "to": to_ts, "byCategory": by_category})

    def get_my_earnings(self, from_ts: Optional[TimeArg] = None, to_ts: Optional[TimeArg] = None) -> WalletEarnings:
        """:meth:`get_wallet_earnings` for the authenticated wallet. ``GET /api/v1/me/earnings`` (API key or
        session)."""
        return self._request("GET", "/api/v1/me/earnings", auth="keyOrSession", params={"from": from_ts, "to": to_ts})

    def get_leaderboard(
        self,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        search: Optional[str] = None,
    ) -> Leaderboard:
        """The final testing-phase standings, frozen for good; rows carry ``profileId``; offset-paged.
        ``GET /api/v1/leaderboard`` (no auth).

        ``search`` matches a username or a publicly visible X name. Wallet addresses are not searchable.
        """
        return self._request("GET", "/api/v1/leaderboard", params={**_page(page, offset, limit), "search": search})

    def get_public_profile(self, wallet: str) -> PublicProfile:
        """Identity of your own wallet (public socials, agent, follow counts, final testing-phase standing).
        ``GET /api/v1/profile/{wallet}``. Owner only (API key or session): another wallet answers 404. Public profiles
        are browsed by username."""
        return self._request("GET", f"/api/v1/profile/{seg(wallet)}", auth="keyOrSession")

    def get_public_profile_referrals(self, wallet: str) -> ProfileReferrals:
        """Referral counts of any wallet, off-chain and on-chain. ``GET /api/v1/profile/{wallet}/referrals``
        (API key)."""
        return self._request("GET", f"/api/v1/profile/{seg(wallet)}/referrals", auth="key")
