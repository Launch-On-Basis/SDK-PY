"""BasisAPI: the off-chain API client, composed from one mixin per domain (``basis/api/<domain>.py``).

Every mixin subclasses :class:`ApiCore` and holds methods only. ``client.api.<method>`` stays flat. Every domain's types
are re-exported here, like TS ``export * from './<domain>'``.
TypeScript SDK: ``src/api/index.ts``.
"""

from .core import AUTH_MODES, ApiCore, AuthRequiredError, BasisApiError
from .types import ApiErrorBody, Cursor, CursorPage, E18, Pagination, PublicPersonRef, RawAmount, UsdE18, UsdSource
from .auth import (
    AuthApi, OnboardingStatus, TourChoice, RemovableSocial, UnlinkablePlatform, ProfileAction, DailyCapPointCategory,
    DailyCapCountCategory, OnboardingState, OnboardingInfo, OnboardingSaved, VerifyResult, LogoutResult, TwitterAuthUrl,
    SessionInfo, ApiKeyInfo, NewApiKey, TwitterChallenge, TwitterLinkResult, MySocial, MyXAccount, MyProfile,
    SocialLink, UpdateProfilePayload, UpdateProfileResult, DailyCapEntry, MyDailyCaps, UnlinkSocialResult
)
from .content import (
    ContentApi, ImagePurpose, BugSeverity, BugCategory, BugStatus, ImageSource, MetadataResult, ProjectUpdateResult,
    CommentRow, DeleteCommentResult, Agent, AgentList, AgentLookup, AgentRegistration, AgentApplied, AgentAppliedUsername, AgentAppliedPhoto, BugReportSummary,
    BugReportSubmission, BugReport, BugReportList
)
from .tokens import (
    TokensApi, TokenKind, TokenPredictionStatus, TokenSort, TokenTradeType, TradeLegRole, CandleInterval,
    TokenHistoryInterval, TokenRewardKind, TokenEventName, TimeBound, TokenPredictionOption, TokenAutoVest,
    TokenPresale, TokenSummary, TokenDetail, TokenCandle, TokenCandlesQuote, TokenCandles, TokenTrade, SwapTrade,
    TokenOrderFill, TokenComment, TokenWhitelistEntry, TokenWhitelistStatusEntry, TokenWhitelistStatus, TokenHolder,
    TokenHolders, TokenReward, TokenRewardTotal, TokenFee, TokenFeeTotal, TokenHistoryQuote, TokenEvent,
    TokenHistoryPoint, TokensPage, TokenDetailResponse, TokenTradesPage, SwapTradesPage, TokenFillsPage,
    TokenCommentsPage, TokenWhitelistPage, TokenRewards, TokenFees, TokenEvents, TokenHistory
)
from .markets import (
    MarketsApi, MarketType, MarketResolutionStatus, MarketResolutionKind, MarketSort, MarketOddsInterval, OrderStatus,
    MarketEventAction, MarketShape, MarketFinal, MarketOutcomeOdds, MarketSummary,
    MarketsPage, MarketOutcomeDetail, MarketDetail, MarketTrade, MarketTradesPage, MarketOdds, Order, OrdersPage,
    MarketOrderBook, MarketHolders, MarketResolution, MarketEvent, MarketEventsPage,
    WalletMarket, WalletMarketsPage, MarketDraftResult, DraftKind, DraftJobStatus, DraftJob, DraftJobStart,
    DraftJobResponse
)
from .credit import (
    CreditApi, LoanSource, LoanStatus, LoanEventAction, VaultEventAction, VestingEventAction, VestingEventRole,
    VaultHistoryInterval, QuoteCall, LoanClaim, Loan, LoansPage, LoanEvent, LoanEventsPage, VaultEvent, VaultEventsPage,
    VestingEvent, VestingEventsPage, ApiVaultLoan, ApiVaultPosition, VaultPositions,
    VaultLastLiquidation, VaultSummary, VaultsList, VaultHistoryBucket, VaultHistory, LoanExitQuoteFigures,
    LoanExitQuotePosition, LoanExitQuoteData, LoanExitQuote
)
from .wallet import (
    WalletApi, UsdSourceName, WalletTransactionType, WalletLegRole, ActivityRowType, WalletLegRoleFilter,
    ActivityFilterType, IndexedTxStatus, PnlPeriod, TimeArg, WalletUsdTotal, WalletTransaction, WalletTransactionsPage,
    WalletPairBalance, WalletHolding, WalletVaultLine, WalletLoanLine, WalletVestingLine, WalletPredictionShare,
    WalletPredictionLine, WalletUpDownStake, WalletCostBasisToken, WalletCostBasis, WalletPortfolio, WalletFeeRow,
    WalletFees, WalletEarningRow, WalletTokenEarningRow, WalletHolderRewardRow, WalletEarnings, ReferralAssetAmount,
    ReferralEarning, ReferralEarned, ReferralFeesGenerated, ReferralRedirect, OnchainReferral, OnchainReferrals,
    WalletReferralPoints, WalletReferrals, ReferrerRef, ReferredWallet, MyReferrals, ActivityAmount, ActivityRow,
    ActivityPage, IndexedTx,
    FirstSeen, AgentRef, ProfileAgent, WalletSummary, WalletProjectToken, WalletProjectMarket,
    WalletProjectsPagination, WalletProjects, WalletWhitelistRole, WalletPrivateMarketRole, WalletRoles, WalletFollow,
    WalletFollows, WalletPnlPart,
    WalletPnlSpot, WalletPnlPosition, WalletPnlMarket, WalletPnl, ProfileSocial, LeaderboardEntry, Leaderboard,
    PublicProfile, ProfileReferralCounts, OnchainReferralCounts, ProfileReferrals
)
from .updown import (
    UpDownApi, UpDownTfSlug, UpDownTf, UpDownAssetParam, UpDownSideStr, UpDownOutcomeStr, UpDownRoundStatus,
    UpDownTimeframeInfo, UpDownAssetRow, UpDownAssets, UpDownStatsRow, UpDownStats, UpDownRoundRow, UpDownRoundsList,
    UpDownSideBets, UpDownPaid, UpDownRoundBets, UpDownRoundClaims, UpDownBettor, UpDownBettorPage, UpDownCarry,
    UpDownRoundDetail, UpDownPosition, UpDownActiveBet, UpDownClaimableBet, UpDownStakeByAsset, UpDownPayoutBySettle,
    UpDownNetBySettle, UpDownUnclaimed, UpDownHistoryRow, UpDownHistoryPage, UpDownRecord
)
from .discovery import (
    DiscoveryApi, MainsInclude, MainsListMarket, MainsListRow, MainsList, ApiFeatures, ApiConfigContracts,
    ApiConfigMain, ApiConfig, ApiStatus, ContractsJsonMain, ContractsJson, AgentManifest, AiPluginManifest, PriceRow,
    PricesList, TokenPriceRow, TokenPricesList, StatsWindow, StatsByMainRow, StatsVolumeByPairAsset, PlatformStats,
    StatsHeldValue, StatsMainsRow, StatsMains, StatsFeesGroupBy, StatsLeaderboardMetric, StatsPeriod, StatsFeeFlow,
    StatsFeesRow, StatsFees, StatsLeaderboardRow, StatsLeaderboard
)
from .social import (
    SocialApi, FeedSection, FeedPostSection, FeedFilterSection, FeedSort, FeedPeriod, FeedPagination, FeedPost,
    FeedComment, FeedPage, FeedPostThread, FeedVotes, CreateFeedPostResponse,
    EditFeedPostResponse, CreateFeedCommentResponse, EditFeedCommentResponse, FeedDeleteResponse, FeedVoteResponse,
    FeedReportResponse, VerifyTweetResponse, VerifiedTweet
)
from .sync import SyncApi, SyncEvent, SyncTransactionResponse

_DOMAINS = (AuthApi, ContentApi, TokensApi, MarketsApi, CreditApi, WalletApi, UpDownApi, DiscoveryApi, SocialApi,
            SyncApi)


class BasisAPI(AuthApi, ContentApi, TokensApi, MarketsApi, CreditApi, WalletApi, UpDownApi, DiscoveryApi, SocialApi,
               SyncApi, ApiCore):
    """HTTP client for the Basis off-chain API: every domain's methods on one flat object (``client.api.<method>``)."""


def _check_unique() -> None:
    """A public method name may be defined by one domain only (TS raises the same at import)."""
    seen = {}
    for d in _DOMAINS:
        for name in vars(d):
            if name.startswith("_"):
                continue
            if name in seen:
                raise RuntimeError(f"[basis-sdk] BasisAPI: {name} is defined by {seen[name]} and {d.__name__}")
            seen[name] = d.__name__


_check_unique()

__all__ = [
    "BasisAPI",
    "ApiCore",
    "BasisApiError",
    "AuthRequiredError",
    "AUTH_MODES",
    "AuthApi",
    "ContentApi",
    "TokensApi",
    "MarketsApi",
    "CreditApi",
    "WalletApi",
    "UpDownApi",
    "DiscoveryApi",
    "SocialApi",
    "SyncApi",
    "PublicPersonRef",
    "RawAmount",
    "UsdE18",
    "UsdSource",
    "E18",
    "Cursor",
    "Pagination",
    "CursorPage",
    "ApiErrorBody",
    "OnboardingStatus", "TourChoice", "RemovableSocial", "UnlinkablePlatform", "ProfileAction", "DailyCapPointCategory",
    "DailyCapCountCategory", "OnboardingState", "OnboardingInfo", "OnboardingSaved", "VerifyResult", "LogoutResult",
    "TwitterAuthUrl", "SessionInfo", "ApiKeyInfo", "NewApiKey", "TwitterChallenge", "TwitterLinkResult", "MySocial",
    "MyXAccount", "MyProfile", "SocialLink", "UpdateProfilePayload", "UpdateProfileResult", "DailyCapEntry",
    "MyDailyCaps", "UnlinkSocialResult",
    "ImagePurpose", "BugSeverity", "BugCategory", "BugStatus", "ImageSource", "MetadataResult", "ProjectUpdateResult",
    "CommentRow", "DeleteCommentResult", "Agent", "AgentList", "AgentLookup", "AgentRegistration", "AgentApplied", "AgentAppliedUsername", "AgentAppliedPhoto",
    "BugReportSummary",
    "BugReportSubmission", "BugReport", "BugReportList",
    "TokenKind", "TokenPredictionStatus", "TokenSort", "TokenTradeType", "TradeLegRole", "CandleInterval",
    "TokenHistoryInterval", "TokenRewardKind", "TokenEventName", "TimeBound", "TokenPredictionOption", "TokenAutoVest",
    "TokenPresale", "TokenSummary", "TokenDetail", "TokenCandle", "TokenCandlesQuote", "TokenCandles", "TokenTrade",
    "SwapTrade", "TokenOrderFill", "TokenComment", "TokenWhitelistEntry", "TokenWhitelistStatusEntry",
    "TokenWhitelistStatus", "TokenHolder", "TokenHolders", "TokenReward", "TokenRewardTotal", "TokenFee",
    "TokenFeeTotal", "TokenHistoryQuote", "TokenEvent", "TokenHistoryPoint", "TokensPage", "TokenDetailResponse",
    "TokenTradesPage", "SwapTradesPage", "TokenFillsPage", "TokenCommentsPage", "TokenWhitelistPage", "TokenRewards",
    "TokenFees", "TokenEvents", "TokenHistory",
    "MarketType", "MarketResolutionStatus", "MarketResolutionKind", "MarketSort", "MarketOddsInterval", "OrderStatus",
    "MarketEventAction", "MarketShape", "MarketFinal", "MarketOutcomeOdds", "MarketSummary",
    "MarketsPage", "MarketOutcomeDetail", "MarketDetail", "MarketTrade", "MarketTradesPage", "MarketOdds", "Order",
    "OrdersPage", "MarketOrderBook", "MarketHolders", "MarketResolution", "MarketEvent", "MarketEventsPage",
    "WalletMarket", "WalletMarketsPage", "MarketDraftResult", "DraftKind", "DraftJobStatus", "DraftJob", "DraftJobStart",
    "DraftJobResponse",
    "LoanSource", "LoanStatus", "LoanEventAction", "VaultEventAction", "VestingEventAction", "VestingEventRole",
    "VaultHistoryInterval", "QuoteCall", "LoanClaim", "Loan", "LoansPage", "LoanEvent", "LoanEventsPage", "VaultEvent",
    "VaultEventsPage", "VestingEvent", "VestingEventsPage", "ApiVaultLoan", "ApiVaultPosition", "VaultPositions",
    "VaultLastLiquidation", "VaultSummary", "VaultsList", "VaultHistoryBucket",
    "VaultHistory", "LoanExitQuoteFigures", "LoanExitQuotePosition", "LoanExitQuoteData", "LoanExitQuote",
    "UsdSourceName", "WalletTransactionType", "WalletLegRole", "ActivityRowType", "WalletLegRoleFilter",
    "ActivityFilterType", "IndexedTxStatus", "PnlPeriod", "TimeArg", "WalletUsdTotal", "WalletTransaction",
    "WalletTransactionsPage", "WalletPairBalance", "WalletHolding", "WalletVaultLine", "WalletLoanLine",
    "WalletVestingLine", "WalletPredictionShare", "WalletPredictionLine", "WalletUpDownStake", "WalletCostBasisToken",
    "WalletCostBasis", "WalletPortfolio", "WalletFeeRow", "WalletFees", "WalletEarningRow", "WalletTokenEarningRow",
    "WalletHolderRewardRow", "WalletEarnings", "ReferralAssetAmount", "ReferralEarning", "ReferralEarned",
    "ReferralRedirect", "ReferralFeesGenerated", "OnchainReferral", "OnchainReferrals", "WalletReferralPoints",
    "WalletReferrals",
    "ReferrerRef", "ReferredWallet", "MyReferrals", "ActivityAmount", "ActivityRow", "ActivityPage", "IndexedTx",
    "FirstSeen", "AgentRef", "ProfileAgent", "WalletSummary", "WalletProjectToken", "WalletProjectMarket",
    "WalletProjectsPagination", "WalletProjects", "WalletWhitelistRole", "WalletPrivateMarketRole", "WalletRoles",
    "WalletFollow", "WalletFollows",
    "WalletPnlPart", "WalletPnlSpot", "WalletPnlPosition", "WalletPnlMarket", "WalletPnl", "ProfileSocial",
    "LeaderboardEntry", "Leaderboard", "PublicProfile", "ProfileReferralCounts", "OnchainReferralCounts",
    "ProfileReferrals",
    "UpDownTfSlug", "UpDownTf", "UpDownAssetParam", "UpDownSideStr", "UpDownOutcomeStr", "UpDownRoundStatus",
    "UpDownTimeframeInfo", "UpDownAssetRow", "UpDownAssets", "UpDownStatsRow", "UpDownStats", "UpDownRoundRow",
    "UpDownRoundsList", "UpDownSideBets", "UpDownPaid", "UpDownRoundBets", "UpDownRoundClaims", "UpDownBettor",
    "UpDownBettorPage", "UpDownCarry", "UpDownRoundDetail", "UpDownPosition", "UpDownActiveBet", "UpDownClaimableBet",
    "UpDownStakeByAsset", "UpDownPayoutBySettle", "UpDownNetBySettle", "UpDownUnclaimed", "UpDownHistoryRow",
    "UpDownHistoryPage", "UpDownRecord",
    "MainsInclude", "MainsListMarket", "MainsListRow", "MainsList", "ApiFeatures", "ApiConfigContracts",
    "ApiConfigMain", "ApiConfig", "ApiStatus", "ContractsJsonMain", "ContractsJson", "AgentManifest",
    "AiPluginManifest", "PriceRow", "PricesList", "TokenPriceRow", "TokenPricesList", "StatsWindow", "StatsByMainRow",
    "StatsVolumeByPairAsset", "PlatformStats", "StatsHeldValue", "StatsMainsRow", "StatsMains", "StatsFeesGroupBy",
    "StatsLeaderboardMetric", "StatsPeriod", "StatsFeeFlow", "StatsFeesRow", "StatsFees", "StatsLeaderboardRow",
    "StatsLeaderboard",
    "FeedSection", "FeedPostSection", "FeedFilterSection", "FeedSort", "FeedPeriod", "FeedPagination", "FeedPost",
    "FeedComment", "FeedPage", "FeedPostThread", "FeedVotes",
    "CreateFeedPostResponse", "EditFeedPostResponse", "CreateFeedCommentResponse", "EditFeedCommentResponse",
    "FeedDeleteResponse", "FeedVoteResponse", "FeedReportResponse", "VerifyTweetResponse", "VerifiedTweet",
    "SyncEvent", "SyncTransactionResponse",
]
