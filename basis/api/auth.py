"""Auth domain: /api/auth/*, /api/v1/auth/keys*, /api/v1/me/profile, /api/v1/me/daily-caps, /api/v1/me/socials/unlink."""

from typing import Any, Dict, List, Literal, Optional

import re

from ..core.errors import ValidationError
from ..core.opts import check_keys
from .core import ApiCore, AuthRequiredError, api_error_from, seg
from .types import TypedDict

OnboardingStatus = Literal["pending", "skipped", "completed"]
TourChoice = Literal["skipped", "completed"]
RemovableSocial = Literal["x", "discord", "telegram", "tiktok"]
"""Platforms ``removeSocial`` accepts."""
UnlinkablePlatform = Literal["twitter", "google", "discord", "github", "privy-google", "privy-twitter"]
"""Social identities :meth:`AuthApi.unlink_social` accepts (a base platform also matches its ``privy-`` sibling)."""
ProfileAction = Literal["username_set", "social_set", "social_removed", "avatar_set", "avatar_cleared",
                        "social_toggled"]
DailyCapPointCategory = Literal["trading", "prediction", "creator", "positions"]
DailyCapCountCategory = Literal["social_x"]


class OnboardingState(TypedDict):
    status: OnboardingStatus
    firstSignInAt: str


class OnboardingInfo(TypedDict, total=False):
    """``GET /api/auth/onboarding``: only ``signedIn: False`` when this session does not hold the wallet.
    ``termsAccepted``: the wallet (or the BASIS account it belongs to) accepted the current Terms of Service."""

    signedIn: bool
    status: OnboardingStatus
    firstSignInAt: str
    termsAccepted: bool


class OnboardingSaved(OnboardingState):
    """``POST /api/auth/onboarding`` answer (``signedIn`` is always True)."""

    signedIn: bool




class VerifyResult(TypedDict):
    """``POST /api/auth/verify`` answer plus ``sessionCookie``: it holds every wallet of the session."""

    ok: bool
    address: str
    onboarding: Optional[OnboardingState]
    sessionCookie: Optional[str]


class LogoutResult(TypedDict):
    """``DELETE /api/auth/me`` answer; ``sessionCookie`` is the reissued session (the remaining wallets under a new
    session id), None after a whole-session logout."""

    ok: bool
    message: str
    sessionCookie: Optional[str]


class TwitterAuthUrl(TypedDict):
    """``GET /api/auth/twitter`` answer; ``sessionCookie`` carries the PKCE verifier the OAuth callback needs."""

    url: str
    sessionCookie: Optional[str]


class SessionInfo(TypedDict, total=False):
    """``GET /api/auth/me``: ``{isLoggedIn: False, addresses}`` without an address, else
    ``{isLoggedIn, address, allAddresses}``. Addresses are lower-case."""

    isLoggedIn: bool
    addresses: List[str]
    address: str
    allAddresses: List[str]


class ApiKeyInfo(TypedDict):
    """An API key as listed (the full key is only returned at creation)."""

    id: str
    keyHint: Optional[str]
    label: Optional[str]
    createdAt: str
    lastUsedAt: Optional[str]


class NewApiKey(TypedDict):
    """A freshly created API key: the only time the plain key is returned."""

    id: str
    key: str
    label: Optional[str]
    createdAt: str


class TwitterChallenge(TypedDict):
    code: str
    expiresAt: str
    expiresIn: int
    tweetTemplate: str


class TwitterLinkResult(TypedDict):
    success: bool
    method: Literal["tweet-verification"]
    username: str
    displayName: str
    tweetId: str


class MySocial(TypedDict):
    platform: str
    handle: str
    url: Optional[str]
    isPublic: bool


class MyXAccount(TypedDict):
    username: str
    displayName: str
    profileImage: str
    isPublic: bool


class MyProfile(TypedDict):
    """``GET /api/v1/me/profile``. tier, tierEmoji and rank are the wallet's final testing-phase standing: they never
    change."""

    wallet: str
    username: Optional[str]
    avatarUrl: Optional[str]
    tier: Optional[str]
    tierEmoji: Optional[str]
    rank: int
    rankDelta: int
    pointsStale: bool
    socials: List[MySocial]
    xAccount: Optional[MyXAccount]
    stale: bool
    lastUpdated: Optional[str]


class SocialLink(TypedDict):
    """``UpdateProfilePayload.social``: links X; ``handle`` is the full X profile URL."""

    platform: Literal["x"]
    handle: str


class UpdateProfilePayload(TypedDict, total=False):
    """``POST /api/v1/me/profile`` body. One action per request; when several keys are set the server takes the first
    in this order: username, social, removeSocial, avatar, toggleSocialPublic."""

    username: str
    social: SocialLink
    removeSocial: RemovableSocial
    toggleSocialPublic: Literal["x"]
    avatar: Optional[str]


_UPDATE_PROFILE_KEYS = ("username", "social", "removeSocial", "toggleSocialPublic", "avatar")
_SOCIAL_LINK_KEYS = ("platform", "handle")


class UpdateProfileResult(TypedDict, total=False):
    success: bool
    action: ProfileAction
    username: str
    platform: str
    handle: str
    avatarUrl: str
    isPublic: bool


class DailyCapEntry(TypedDict):
    category: str
    percent: float


class _MyDailyCapsBase(TypedDict):
    date: str
    resetsInSeconds: int
    pointCaps: List[DailyCapEntry]
    countCaps: List[DailyCapEntry]


class MyDailyCaps(_MyDailyCapsBase, total=False):
    frozen: bool


class _UnlinkSocialBase(TypedDict):
    success: bool
    platform: UnlinkablePlatform
    availableAt: str


class UnlinkSocialResult(_UnlinkSocialBase, total=False):
    username: str


def _session_cookie_of(res: Any) -> Optional[str]:
    """The ``name=value`` pairs of a response's live Set-Cookie headers, ``; ``-joined; None when there are none.
    ``res.cookies`` leaves out cookies the answer deletes (Max-Age <= 0 or Expires in the past)."""
    pairs = [f"{c.name}={c.value}" for c in res.cookies]
    return "; ".join(pairs) if pairs else None


def _with_session_cookie(res: Any, what: str) -> Any:
    """Parses a JSON answer and adds its ``sessionCookie``; non-2xx raises BasisApiError."""
    if not res.ok:
        raise api_error_from(res, what)
    out = res.json()
    out["sessionCookie"] = _session_cookie_of(res)
    return out


_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")


def _siwe_address_of(message: str) -> Optional[str]:
    """The wallet a SIWE message signs in (its second line), lower-case; None when it has none."""
    lines = str(message).split("\n")
    line = lines[1].strip() if len(lines) > 1 else ""
    return line.lower() if _ADDRESS_RE.fullmatch(line) else None


class AuthApi(ApiCore):
    """Auth routes (mixin of ``BasisAPI``)."""

    def get_nonce(self, address: str) -> Dict[str, str]:
        """``GET /api/auth/nonce``: a SIWE nonce for ``address`` (5 minutes, single use; an opaque string):
        ``{nonce}``. Public."""
        return self._request("GET", "/api/auth/nonce", params={"address": address})

    def verify(self, message: str, signature: str) -> VerifyResult:
        """``POST /api/auth/verify``: verifies a signed SIWE message and adds the wallet to the session (24 h).
        Public; sends the session cookie (not the key) when there is one, so the session keeps its other wallets, as
        in a browser.

        The session cookie lands in ``api.session``; the answer also carries it as ``sessionCookie``. The client records
        the signed-in wallet as the session's confirmed wallet (``client.session_wallets``): session requests then go
        out for that wallet only. The message must name the site (``domain`` its host, ``uri`` its origin) and the
        deployment's chain: 422 ``domain_mismatch`` / ``wrong_chain`` otherwise, ``invalid_nonce`` for a used or old
        nonce.
        """
        res = self._send("POST", "/api/auth/verify", body={"message": message, "signature": signature},
                         include_session=True)
        out = _with_session_cookie(res, "POST /api/auth/verify")
        wallet = out.get("address") if isinstance(out.get("address"), str) and out.get("address") else None
        wallet = wallet or _siwe_address_of(message)
        self._note_session(wallets=[wallet] if wallet else [])
        return out

    def get_me(self, address: Optional[str] = None) -> SessionInfo:
        """``GET /api/auth/me``: this session's wallets; with ``address``, whether that wallet is signed in.

        Public; sends the session cookie when there is one, whichever wallet it holds. The client records the answer's
        wallets as the session's (``client.session_wallets``).
        """
        info = self._request("GET", "/api/auth/me", auth="optional", params={"address": address},
                             include_session=True)
        if isinstance(info, dict):
            wallets = info.get("allAddresses") if "allAddresses" in info else info.get("addresses")
            if isinstance(wallets, list):
                self._note_session(wallets=[w for w in wallets if isinstance(w, str)])
        return info

    def logout(self, address: Optional[str] = None) -> LogoutResult:
        """``DELETE /api/auth/me``: signs ``address`` out of the session, or the whole session without one. Session (any
        wallet of it: no signer check, signing out never acts for a wallet; AuthRequiredError without a session cookie).

        The server revokes the old cookie's session id and reissues the remaining wallets under a new one (or clears
        the cookie): ``api.session`` takes the reissued (or expired) cookie, the answer carries it as ``sessionCookie``
        (None = none left) and ``address`` (or every wallet) leaves ``client.session_wallets``.
        """
        if not self._session_held():
            raise AuthRequiredError("session", "DELETE /api/auth/me")
        res = self._send("DELETE", "/api/auth/me", params={"address": address}, include_session=True)
        out = _with_session_cookie(res, "DELETE /api/auth/me")
        if out.get("sessionCookie") is None:
            self.session.cookies.clear()
        if address:
            self._note_session(remove=address)
        else:
            self._note_session(wallets=[])
        return out

    def get_onboarding(self, address: str) -> OnboardingInfo:
        """``GET /api/auth/onboarding``: onboarding state of ``address`` (created on first call);
        ``{signedIn: False}`` when this session does not hold the wallet. Public; sends the session cookie."""
        return self._request("GET", "/api/auth/onboarding", auth="optionalSession", params={"address": address})

    def set_onboarding(self, address: str, status: TourChoice) -> OnboardingSaved:
        """``POST /api/auth/onboarding``: records that ``address`` skipped or completed the tour. Session holding
        the wallet."""
        return self._request("POST", "/api/auth/onboarding", auth="session",
                             body={"address": address, "status": status})


    def create_api_key(self, label: Optional[str] = None) -> NewApiKey:
        """``POST /api/v1/auth/keys``: creates an API key (one active per wallet); the only answer with the plain
        key. Session."""
        return self._request("POST", "/api/v1/auth/keys", auth="session", body={} if label is None else {"label": label})

    def list_api_keys(self) -> Dict[str, List[ApiKeyInfo]]:
        """``GET /api/v1/auth/keys``: the session wallet's active keys (masked ``keyHint``, never the key):
        ``{keys}``. Session."""
        return self._request("GET", "/api/v1/auth/keys", auth="session")

    def delete_api_key(self, key_id: str) -> Dict[str, Any]:
        """``DELETE /api/v1/auth/keys/{id}``: deletes an API key (invalid at once): ``{ok, message}``. Session."""
        return self._request("DELETE", f"/api/v1/auth/keys/{seg(key_id)}", auth="session")


    def get_twitter_auth_url(self, wallet: Optional[str] = None) -> TwitterAuthUrl:
        """``GET /api/auth/twitter`` (with ``Accept: application/json``): the X OAuth authorization URL for ``wallet``
        (default: the signer), sent as ``X-Wallet-Address``; it must be in the session (403 ``forbidden`` otherwise).
        Session. The URL only completes in a browser holding the returned ``sessionCookie`` (it carries the PKCE
        verifier); SDK and agent callers link X with :meth:`request_twitter_challenge` + :meth:`verify_twitter`."""
        headers = {"Accept": "application/json"}
        if wallet:
            headers["X-Wallet-Address"] = wallet
        res = self._send("GET", "/api/auth/twitter", auth="session", headers=headers)
        return _with_session_cookie(res, "GET /api/auth/twitter")

    def request_twitter_challenge(self) -> TwitterChallenge:
        """``POST /api/auth/twitter/challenge``: a short-lived code to tweet, then pass the tweet to
        :meth:`verify_twitter`. Key or session."""
        return self._request("POST", "/api/auth/twitter/challenge", auth="keyOrSession")

    def verify_twitter(self, tweet_url: str) -> TwitterLinkResult:
        """``POST /api/auth/twitter/verify-tweet``: verifies a public tweet with the challenge code and links the
        X account. Key or session."""
        return self._request("POST", "/api/auth/twitter/verify-tweet", auth="keyOrSession",
                             body={"tweetUrl": tweet_url})


    def get_my_profile(self) -> MyProfile:
        """``GET /api/v1/me/profile``: the wallet's full profile, private socials included. Key or session."""
        return self._request("GET", "/api/v1/me/profile", auth="keyOrSession")

    def update_my_profile(self, payload: UpdateProfilePayload) -> UpdateProfileResult:
        """``POST /api/v1/me/profile``: one profile action per call (see :class:`UpdateProfilePayload`). A None field
        is left out of the body, except ``avatar``, where None is a value (a random bundled avatar): so
        ``{"username": None, "avatar": url}`` sets the avatar. ``payload`` None is ``{}``; anything but a dict, or a key
        it does not have (also inside ``social``: platform, handle), raises ``ValidationError("options")`` /
        ``ValidationError("social")`` before any request (TS: the same). Key or session."""
        if payload is None:
            payload = {}  # type: ignore[assignment]
        if not isinstance(payload, dict):
            shown = f"'{payload}'" if isinstance(payload, str) else ("an array" if isinstance(payload, (list, tuple))
                                                                     else repr(payload))
            raise ValidationError("options", f"options must be an options object like {{ amount: ... }} (or left out), "
                                             f"got {shown}.")
        check_keys(payload, _UPDATE_PROFILE_KEYS, "options")
        check_keys(payload.get("social"), _SOCIAL_LINK_KEYS, "social")
        body = {k: v for k, v in payload.items() if v is not None or k == "avatar"}
        return self._request("POST", "/api/v1/me/profile", auth="keyOrSession", body=body)

    def get_my_daily_caps(self) -> MyDailyCaps:
        """``GET /api/v1/me/daily-caps``: kept for old clients: points were removed, so both lists are empty. Key or
        session."""
        return self._request("GET", "/api/v1/me/daily-caps", auth="keyOrSession")

    def unlink_social(self, platform: UnlinkablePlatform) -> UnlinkSocialResult:
        """``POST /api/v1/me/socials/unlink``: unlinks an OAuth-verified social; the handle then has a 7-day
        cooldown. No undo. Key or session."""
        return self._request("POST", "/api/v1/me/socials/unlink", auth="keyOrSession", body={"platform": platform})
