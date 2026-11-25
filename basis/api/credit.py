"""Credit domain: /api/v1/loans*, /api/v1/vault/*, /api/v1/vaults*, /api/v1/vesting/events and the
wallet-scoped loans / vault / vesting routes under /api/v1/wallet/{address}/.

``get_my_*`` read the authenticated wallet (API key or SIWE session); the wallet / list methods need an API key.
TypeScript SDK: ``src/api/credit.ts``.
"""

from typing import Any, Dict, List, Optional

from ..core.errors import ValidationError
from ..core.units import to_address, to_bool, to_uint
from .core import ApiCore, seg
from .types import Pagination, RawAmount, UsdE18, UsdSource

try:
    from typing import Literal, TypedDict
except ImportError:  # pragma: no cover
    from typing_extensions import Literal, TypedDict  # type: ignore

LoanSource = Literal["hub", "vault", "leverage", "vesting"]
LoanStatus = Literal["active", "expired", "liquidated", "repaid", "claimable"]
LoanEventAction = Literal[
    "created", "repaid", "extended", "increased", "liquidated", "partial_sell", "liquidation_claimed",
    "refinance_surplus", "liquidation_settled", "added", "repaid_partial", "seized",
]
VaultEventAction = Literal["stake", "unstake", "claim", "compound", "liquidation_loss"]
VestingEventAction = Literal[
    "created", "updated", "claimed", "extended", "beneficiary_changed", "renounced", "creator_transferred",
    "loan_taken", "loan_repaid", "loan_liquidated",
]
VestingEventRole = Literal["actor", "beneficiary", "creator", "any"]
"""Which side of a vesting the wallet is on; the server default is ``any``."""
VaultHistoryInterval = Literal["1h", "4h", "1d"]


class QuoteCall(TypedDict):
    """A ready contract call (a loan row's ``repay``): send as is, or change the amount in ``args``."""

    to: str
    function: str
    args: List[Any]
    value: RawAmount
    data: str


class LoanClaim(TypedDict):
    """A loan row's ``claim``: a ready call to collect what a liquidation left."""

    contract: str
    function: str
    args: List[Any]


class Loan(TypedDict, total=False):
    """One loan of a wallet. Pair amounts are raw ``pairAsset`` units, collateral amounts token units (18 decimals).
    Times: ``liquidationTime`` / ``creationTime`` / ``closedAt`` / ``seizableAfter`` unix seconds; ``borrowedAmount`` is
    history (never reduced), the debt is ``fullAmount``; ``repay`` is a repay-all call for an open leverage position or
    hub loan (set args[1] for a partial repay)."""

    wallet: str
    source: LoanSource
    main: str
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    pairSymbol: Optional[str]
    loanId: int
    hubId: Optional[int]
    hub: Optional[str]
    hubBorrower: Optional[str]
    coreOwner: str
    isLeverage: bool
    vestingId: Optional[int]
    token: str
    collateralAmount: RawAmount
    borrowedAmount: RawAmount
    fullAmount: RawAmount
    leverageBuyAmount: Optional[RawAmount]
    cashedOut: Optional[RawAmount]
    levCashIn: Optional[RawAmount]
    levCashOut: Optional[RawAmount]
    levCollateralOut: Optional[RawAmount]
    liquidatedAmount: Optional[RawAmount]
    claimable: Optional[RawAmount]
    claimed: Optional[RawAmount]
    liquidationClaim: Optional[RawAmount]
    daysCount: Optional[int]
    liquidationTime: int
    expiresAt: Optional[str]
    creationTime: int
    txHash: str
    createdAt: str
    updatedAt: str
    active: bool
    isLiquidated: bool
    expired: bool
    status: LoanStatus
    secondsToLiquidation: Optional[int]
    collateralValue: Optional[RawAmount]
    collateralUsdE18: UsdE18
    collateralUsdSource: UsdSource
    debtUsdE18: UsdE18
    debtUsdSource: UsdSource
    claim: Optional[LoanClaim]
    seizableAfter: Optional[int]
    repay: Optional[QuoteCall]
    closedAt: Optional[int]
    closeReason: Optional[Literal["repaid", "liquidated", "sold"]]


class LoanExitQuoteFigures(TypedDict):
    """``LoanExitQuoteData.quote``: ``residual`` collateral left to sell once the debt share is settled (token
    units); ``gross`` what the sale brings in, ``fee`` the exit fee, ``net`` gross less fee (pair asset raw units);
    ``grossAtSpot`` the residual at the spot price (no price impact). Decimal strings."""

    residual: RawAmount
    gross: RawAmount
    fee: RawAmount
    net: RawAmount
    grossAtSpot: RawAmount


class LoanExitQuotePosition(TypedDict):
    """``LoanExitQuoteData.position``: ``fullAmount`` the debt now (pair asset raw units), ``collateralAmount`` the
    collateral (token units). Decimal strings."""

    fullAmount: RawAmount
    collateralAmount: RawAmount


class LoanExitQuoteData(TypedDict):
    """``get_loan_exit_quote`` figures, every one read at the same block (``block``): ``minOut`` net less 2% (the
    floor the site sends with the sale), ``mainToBurn`` / ``tokensToBurn`` the main and collateral tokens that settle
    the debt share (the server's field names), ``sellRateBps`` the payer's own sell rate. Decimal strings."""

    quote: LoanExitQuoteFigures
    minOut: RawAmount
    mainToBurn: RawAmount
    tokensToBurn: RawAmount
    sellRateBps: str
    position: LoanExitQuotePosition
    block: str


class LoanExitQuote(TypedDict):
    """``GET /api/v1/loans/exit-quote`` answer (``asOf`` an ISO time)."""

    network: str
    chainId: int
    data: LoanExitQuoteData
    asOf: str


class LoansPage(TypedDict):
    """An offset-paged loan list."""

    data: List[Loan]
    pagination: Pagination


class LoanEvent(TypedDict, total=False):
    """A loan event. ``data`` is a JSON string, ``details`` it parsed (keys depend on source and action);
    ``timestamp`` ISO."""

    wallet: str
    source: str
    action: LoanEventAction
    loanId: Optional[int]
    hubId: Optional[int]
    main: Optional[str]
    hub: Optional[str]
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    pairSymbol: Optional[str]
    txHash: str
    blockNumber: int
    logIndex: int
    usdE18: UsdE18
    usdSource: UsdSource
    data: str
    timestamp: str
    details: Optional[Dict[str, Any]]


class LoanEventsPage(TypedDict):
    data: List[LoanEvent]
    pagination: Pagination


class VaultEvent(TypedDict, total=False):
    """A vault event. ``amount`` main units; ``amountOut`` pair units on unstake, main units on liquidation_loss."""

    wallet: str
    vault: str
    main: str
    action: VaultEventAction
    amount: RawAmount
    amountOut: Optional[RawAmount]
    txHash: str
    logIndex: int
    blockNumber: int
    timestamp: str
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    amountOutUnit: Optional[Literal["pair", "main"]]


class VaultEventsPage(TypedDict):
    data: List[VaultEvent]
    pagination: Pagination


class VestingEvent(TypedDict, total=False):
    """A vesting event (``wallet`` is the actor). ``amount``: totalAmount on created, the claimable on claimed.
    The action-specific fields (additionalDays, hub, loanId, borrowed, recovered, newTotalAmount, old/new beneficiary
    and creator) are present only where they apply."""

    chainId: int
    vestingContract: str
    wallet: str
    action: VestingEventAction
    vestingId: int
    creator: Optional[str]
    beneficiary: Optional[str]
    main: Optional[str]
    token: Optional[str]
    amount: Optional[RawAmount]
    usdE18: UsdE18
    usdSource: UsdSource
    data: Optional[str]
    txHash: str
    logIndex: int
    blockNumber: int
    timestamp: str
    additionalDays: int
    hub: str
    loanId: int
    borrowed: RawAmount
    recovered: RawAmount
    newTotalAmount: RawAmount
    oldBeneficiary: str
    newBeneficiary: str
    oldCreator: str
    newCreator: str
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    walletPending: bool


class VestingEventsPage(TypedDict):
    data: List[VestingEvent]
    pagination: Pagination


class ApiVaultLoan(TypedDict, total=False):
    """The vault loan of an :class:`ApiVaultPosition` (raw pair units; ``liquidationTime`` unix seconds)."""

    hubId: int
    hub: Optional[str]
    borrowed: RawAmount
    fullAmount: RawAmount
    liquidationTime: int
    active: bool
    isLiquidated: bool


class ApiVaultPosition(TypedDict, total=False):
    """A wallet's vault position for one main (spec ``VaultPosition``; database only, main units). Pending rewards,
    freeToUnstake and the exact live position are chain reads: see ``client.vault``."""

    main: str
    vault: str
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    staked: RawAmount
    principal: Optional[RawAmount]
    pledged: RawAmount
    hasActiveLoan: bool
    hubId: Optional[int]
    hub: Optional[str]
    loan: Optional[ApiVaultLoan]


class VaultPositions(TypedDict):
    wallet: str
    data: List[ApiVaultPosition]


class VaultLastLiquidation(TypedDict, total=False):
    """A vault's most recent liquidation loss (``stakeLost`` / ``rewardsLost`` main units; ``time`` ISO)."""

    wallet: str
    hub: Optional[str]
    hubId: Optional[int]
    stakeLost: Optional[RawAmount]
    rewardsLost: Optional[RawAmount]
    txHash: str
    time: str


class VaultSummary(TypedDict, total=False):
    """One row of /vaults per main (main units unless the name says Pair). Live values are cached 15 s and None when
    the read fails."""

    main: str
    vault: str
    pairAsset: str
    pairDecimals: int
    pairSymbol: str
    minStake: Optional[RawAmount]
    totalStaked: Optional[RawAmount]
    totalPledged: Optional[RawAmount]
    yield7d: RawAmount
    yield30d: RawAmount
    yield7dPair: RawAmount
    yield30dPair: RawAmount
    yield7dUsdE18: UsdE18
    usdSource: UsdSource
    yield30dUsdE18: UsdE18
    yield30dUsdSource: UsdSource
    injections7d: int
    injections30d: int
    aprBps: Optional[int]
    aprBasis: Literal["current_total_staked"]
    lastLiquidation: Optional[VaultLastLiquidation]
    rewardsHeld: Optional[RawAmount]


class VaultsList(TypedDict):
    data: List[VaultSummary]


class VaultHistoryBucket(TypedDict, total=False):
    """One bucket of /vaults/history (``time`` = bucket start, unix seconds). Main units, except the ``*Pair`` fields
    (raw pair units); ``totalStaked`` / ``totalYieldMainAdded`` are running totals at the end of the bucket."""

    time: int
    staked: RawAmount
    unstaked: RawAmount
    compounded: RawAmount
    claimed: RawAmount
    liquidationLoss: RawAmount
    yieldPair: RawAmount
    yieldMainAdded: RawAmount
    yieldToOwnerPair: RawAmount
    yieldUsdE18: UsdE18
    totalStaked: RawAmount
    totalYieldMainAdded: RawAmount


VaultHistory = TypedDict("VaultHistory", {
    "main": str,
    "pairAsset": Optional[str],
    "pairDecimals": Optional[int],
    "pairSymbol": Optional[str],
    "interval": str,
    "from": int,
    "to": int,
    "count": int,
    "data": List[VaultHistoryBucket],
}, total=False)
VaultHistory.__doc__ = """Stake flows and yield of one main; ``from`` / ``to`` unix seconds."""


def _loans_params(source, main, hub, token, active, status, page, offset, limit) -> Dict[str, Any]:
    return {"source": source, "main": main, "hub": hub, "token": token, "active": active, "status": status,
            "page": page, "offset": offset, "limit": limit}


class CreditApi(ApiCore):
    """Loans, vault and vesting routes (mixin of ``BasisAPI``).

    ``main`` filters take a main key (e.g. ``stasis-usdg``) or a main address. Cursor-paged methods take ``cursor``
    (``pagination.nextCursor``) and work with :meth:`paginate`."""

    def get_my_loans(
        self,
        source: Optional[LoanSource] = None,
        main: Optional[str] = None,
        hub: Optional[str] = None,
        token: Optional[str] = None,
        active: Optional[bool] = None,
        status: Optional[LoanStatus] = None,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> LoansPage:
        """Loans of the authenticated wallet. Key or session; offset paged (``page`` 1-based, or ``offset``).

        ``GET /api/v1/loans``. ``token``: collateral token (with active=True: the open position of that token).
        """
        return self._request("GET", "/api/v1/loans", auth="keyOrSession",
                             params=_loans_params(source, main, hub, token, active, status, page, offset, limit))

    def get_wallet_loans(
        self,
        address: str,
        source: Optional[LoanSource] = None,
        main: Optional[str] = None,
        hub: Optional[str] = None,
        token: Optional[str] = None,
        active: Optional[bool] = None,
        status: Optional[LoanStatus] = None,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> LoansPage:
        """Loans of a wallet. API key; offset paged (``page`` 1-based, or ``offset``).

        ``GET /api/v1/wallet/{address}/loans``
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/loans", auth="key",
                             params=_loans_params(source, main, hub, token, active, status, page, offset, limit))

    def get_loan_exit_quote(
        self,
        main: str,
        token: str,
        owner: str,
        loan_id: int,
        pct: int,
        leverage: bool,
        payer: Optional[str] = None,
    ) -> LoanExitQuote:
        """What selling ``pct``% of a loan or leverage position pays now: gross, the exit fee, net, and ``minOut``
        (net less 2%), all read at one block. ``GET /api/v1/loans/exit-quote`` (API key).

        Take ``main``, ``token``, ``owner`` (the row's ``coreOwner``, passed as is) and ``loan_id`` (the row's
        ``loanId``, not ``hubId``) from a loans row (:meth:`get_my_loans` / :meth:`get_wallet_loans`); ``leverage``
        True for a leverage position, False for a loan; ``payer`` the wallet whose sell fee applies (a hub loan's borrower; default ``owner``). Checked before any request: ``main``,
        ``token``, ``owner`` and ``payer`` 0x addresses (each its own rule name), ``loan_id`` a whole number above 0
        ('loanId'), ``pct`` 10, 20, ... 100 ('pct'), ``leverage`` True or False ('leverage'). Route errors raise
        BasisApiError: 400 ``bad_param`` (the body's ``param`` names which), 404 ``not_found`` (no such position), 422
        ``refused`` (the sale would be refused now; the reason in the message), 503. The paper site serves this route
        today; the main site answers 404 until it has it. TS: ``getLoanExitQuote``.
        """
        main = to_address(main, "main")
        token = to_address(token, "token")
        owner = to_address(owner, "owner")
        payer = None if payer is None else to_address(payer, "payer")
        lid = to_uint(loan_id, "loanId")
        if lid == 0:
            raise ValidationError("loanId", "loanId must be a whole number above 0 (the loans row's loanId).")
        try:
            p = to_uint(pct, "pct")
        except ValidationError:
            raise ValidationError("pct", f"pct must be 10, 20, ... or 100, got {pct}.") from None
        if not 10 <= p <= 100 or p % 10 != 0:
            raise ValidationError("pct", f"pct must be 10, 20, ... or 100, got {p}.")
        lev = to_bool(leverage, "leverage")
        return self._request("GET", "/api/v1/loans/exit-quote", auth="key", params={
            "main": main, "token": token, "owner": owner, "loanId": str(lid), "pct": p, "leverage": lev,
            "payer": payer})

    def get_my_loan_events(
        self,
        source: Optional[LoanSource] = None,
        action: Optional[LoanEventAction] = None,
        main: Optional[str] = None,
        hub: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> LoanEventsPage:
        """Loan events of the authenticated wallet, newest first. Key or session; cursor paged.

        ``GET /api/v1/loans/events``
        """
        return self._request("GET", "/api/v1/loans/events", auth="keyOrSession",
                             params={"source": source, "action": action, "main": main, "hub": hub,
                                     "cursor": cursor, "limit": limit})

    def get_wallet_loan_events(
        self,
        address: str,
        source: Optional[LoanSource] = None,
        action: Optional[LoanEventAction] = None,
        main: Optional[str] = None,
        hub: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> LoanEventsPage:
        """Loan events of a wallet, newest first. API key; cursor paged. A wallet's liquidations:
        ``action="liquidated"`` (there is no all-wallets liquidation list).

        ``GET /api/v1/wallet/{address}/loans/events``
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/loans/events", auth="key",
                             params={"source": source, "action": action, "main": main, "hub": hub,
                                     "cursor": cursor, "limit": limit})

    def get_my_vault_position(self, main: Optional[str] = None) -> VaultPositions:
        """The authenticated wallet's vault positions, one per main. Key or session.

        ``GET /api/v1/vault/position``
        """
        return self._request("GET", "/api/v1/vault/position", auth="keyOrSession", params={"main": main})

    def get_wallet_vault_position(self, address: str, main: Optional[str] = None) -> VaultPositions:
        """A wallet's vault positions, one per main. API key.

        ``GET /api/v1/wallet/{address}/vault``
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/vault", auth="key", params={"main": main})

    def get_my_vault_events(
        self,
        action: Optional[VaultEventAction] = None,
        main: Optional[str] = None,
        vault: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> VaultEventsPage:
        """Vault events of the authenticated wallet. Key or session; cursor paged.

        ``GET /api/v1/vault/events``
        """
        return self._request("GET", "/api/v1/vault/events", auth="keyOrSession",
                             params={"action": action, "main": main, "vault": vault, "cursor": cursor,
                                     "limit": limit})

    def get_wallet_vault_events(
        self,
        address: str,
        action: Optional[VaultEventAction] = None,
        main: Optional[str] = None,
        vault: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> VaultEventsPage:
        """Vault events of a wallet. API key; cursor paged.

        ``GET /api/v1/wallet/{address}/vault/events``
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/vault/events", auth="key",
                             params={"action": action, "main": main, "vault": vault, "cursor": cursor,
                                     "limit": limit})

    def get_vaults(self, main: Optional[str] = None) -> VaultsList:
        """One row per live main (stake, pledges, yield, APR). API key.

        ``GET /api/v1/vaults``
        """
        return self._request("GET", "/api/v1/vaults", auth="key", params={"main": main})

    def get_vault_history(
        self,
        main: Optional[str] = None,
        interval: Optional[VaultHistoryInterval] = None,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
    ) -> VaultHistory:
        """Stake flows and yield of one main, bucketed (at most 1000 buckets). API key.

        ``GET /api/v1/vaults/history``. ``from_ts`` / ``to_ts`` (query ``from`` / ``to``): unix seconds (ms accepted);
        defaults: 90 intervals before ``to_ts``, now.
        """
        return self._request("GET", "/api/v1/vaults/history", auth="key",
                             params={"main": main, "interval": interval, "from": from_ts, "to": to_ts})

    def get_my_vesting_events(
        self,
        vesting_contract: Optional[str] = None,
        vesting_id: Optional[int] = None,
        action: Optional[VestingEventAction] = None,
        role: Optional[VestingEventRole] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> VestingEventsPage:
        """Vesting events of the authenticated wallet. Key or session; cursor paged.

        ``GET /api/v1/vesting/events``. ``vesting_contract`` defaults to every contract (the deployment's own when
        ``vesting_id`` is given).
        """
        return self._request("GET", "/api/v1/vesting/events", auth="keyOrSession",
                             params={"vestingContract": vesting_contract, "vestingId": vesting_id, "action": action,
                                     "role": role, "cursor": cursor, "limit": limit})

    def get_wallet_vesting_events(
        self,
        address: str,
        vesting_contract: Optional[str] = None,
        vesting_id: Optional[int] = None,
        action: Optional[VestingEventAction] = None,
        role: Optional[VestingEventRole] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> VestingEventsPage:
        """Vesting events of a wallet. API key; cursor paged.

        ``GET /api/v1/wallet/{address}/vesting/events``. ``vesting_contract`` as in :meth:`get_my_vesting_events`.
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/vesting/events", auth="key",
                             params={"vestingContract": vesting_contract, "vestingId": vesting_id, "action": action,
                                     "role": role, "cursor": cursor, "limit": limit})
