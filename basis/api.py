import io
import os
import requests
import logging
from typing import Optional, Dict, Any, List, Union

logger = logging.getLogger(__name__)


class BasisAPI:
    """HTTP client for the Basis off-chain API.

    Provides two request modes:
    - Session-authenticated requests (cookie-based, for auth/metadata/comments)
    - API-key-authenticated requests (X-API-Key header, for v1 data endpoints)
    """

    def __init__(self, client: Any) -> None:
        self.client = client
        self.session = requests.Session()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_request(
        self, method: str, endpoint: str, **kwargs: Any
    ) -> Any:
        """Make a request using the cookie-authenticated session.

        The ``requests.Session`` automatically persists cookies set by the
        server (e.g. after SIWE verification), so no manual cookie handling
        is required.
        """
        url = f"{self.client.api_domain}/api{endpoint}"
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        # Some endpoints return plain text (e.g. image upload returns a URL)
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return response.json()
        text = response.text.strip()
        # Try JSON parse as fallback
        try:
            return response.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            return text

    def _api_key_request(
        self, method: str, endpoint: str, **kwargs: Any
    ) -> Any:
        """Make a request using the API key via ``X-API-Key`` header."""
        api_key = self.client.api_key
        if not api_key:
            raise ValueError(
                "An API key is required for this request. "
                "Provide one via BasisClient(api_key=...) or use "
                "BasisClient.create(...) to auto-provision a key."
            )
        headers = kwargs.pop("headers", {})
        headers["X-API-Key"] = api_key
        url = f"{self.client.api_domain}/api{endpoint}"
        response = self.session.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Authentication endpoints (session)
    # ------------------------------------------------------------------

    def get_nonce(self, address: str) -> Dict[str, Any]:
        """Fetch a SIWE nonce for the given wallet address.

        ``GET /api/auth/nonce?address={address}``
        """
        return self._session_request("GET", "/auth/nonce", params={"address": address})

    def verify(self, message: str, signature: str) -> Dict[str, Any]:
        """Verify a signed SIWE message and establish a session.

        ``POST /api/auth/verify``

        The server returns a Set-Cookie header which is automatically stored
        by the ``requests.Session``.
        """
        return self._session_request(
            "POST",
            "/auth/verify",
            json={"message": message, "signature": signature},
        )

    def get_me(self, address: Optional[str] = None) -> Dict[str, Any]:
        """Get the current session status.

        ``GET /api/auth/me``
        """
        params: Dict[str, str] = {}
        if address is not None:
            params["address"] = address
        return self._session_request("GET", "/auth/me", params=params)

    def logout(self, address: str) -> Dict[str, Any]:
        """Log out / delete session for a specific address.

        ``DELETE /api/auth/me?address={address}``
        """
        return self._session_request("DELETE", "/auth/me", params={"address": address})

    # ------------------------------------------------------------------
    # API key management (session required)
    # ------------------------------------------------------------------

    def create_api_key(self, label: str = "basis-sdk-auto") -> Dict[str, Any]:
        """Create a new API key (max 1 per wallet).

        ``POST /api/v1/auth/keys``
        """
        return self._session_request("POST", "/v1/auth/keys", json={"label": label})

    def list_api_keys(self) -> Dict[str, Any]:
        """List all API keys for the authenticated wallet.

        ``GET /api/v1/auth/keys``
        """
        return self._session_request("GET", "/v1/auth/keys")

    def delete_api_key(self, key_id: str) -> Dict[str, Any]:
        """Delete an API key by id.

        ``DELETE /api/v1/auth/keys/{id}``
        """
        return self._session_request("DELETE", f"/v1/auth/keys/{key_id}")

    # ------------------------------------------------------------------
    # Image upload (session required)
    # ------------------------------------------------------------------

    def upload_image(self, file_path: str) -> str:
        """Upload an image file and return the hosted URL.

        ``POST /api/images`` (multipart/form-data)

        Allowed formats: jpeg, png, webp, gif. Max 5 MB.
        """
        import mimetypes
        mime_type = mimetypes.guess_type(file_path)[0] or "image/png"
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, mime_type)}
            return self._session_request("POST", "/images", files=files)

    def upload_image_from_url(self, image_url: str, contract_address: Optional[str] = None) -> str:
        """Download an image from a URL, resize to 512x512 center-crop,
        convert to WebP, and upload to IPFS via /api/images.

        Requires ``Pillow`` to be installed (``pip install Pillow``).

        Returns the hosted IPFS URL string.
        """
        from PIL import Image

        # 1. Download
        resp = requests.get(image_url)
        resp.raise_for_status()

        # 2. Resize to 512x512 center-crop and convert to WebP
        img = Image.open(io.BytesIO(resp.content))
        # Center-crop to square
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((512, 512), Image.LANCZOS)
        # Convert to WebP
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=90)
        buf.seek(0)

        # 3. Upload — name file after contract address if provided
        import time
        filename = f"{contract_address}.webp" if contract_address else f"image_{int(time.time())}.webp"
        files = {"file": (filename, buf, "image/webp")}
        return self._session_request("POST", "/images", files=files)

    # ------------------------------------------------------------------
    # Metadata (session required, must be creator)
    # ------------------------------------------------------------------

    def update_metadata(
        self,
        address: str,
        description: Optional[str] = None,
        website: Optional[str] = None,
        telegram: Optional[str] = None,
        twitterx: Optional[str] = None,
        image: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update on-chain metadata for a token.

        ``POST /api/metadata``
        """
        body: Dict[str, str] = {"address": address}
        if description is not None:
            body["description"] = description
        if website is not None:
            body["website"] = website
        if telegram is not None:
            body["telegram"] = telegram
        if twitterx is not None:
            body["twitterx"] = twitterx
        if image is not None:
            body["image"] = image
        return self._session_request("POST", "/metadata", json=body)

    # ------------------------------------------------------------------
    # Project updates (session required, must be dev)
    # ------------------------------------------------------------------

    def update_project(
        self,
        address: str,
        data: Optional[Dict[str, Any]] = None,
        image_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Post a project update.

        ``POST /api/projects/{address}``

        When *image_path* is provided the request is sent as multipart
        form-data; otherwise plain JSON.
        """
        if image_path is not None:
            # Multipart form-data mode
            files = {}
            form_data: Dict[str, Any] = {}
            with open(image_path, "rb") as f:
                files["image"] = (os.path.basename(image_path), f)
                if data:
                    for key, value in data.items():
                        form_data[key] = value
                return self._session_request(
                    "POST", f"/projects/{address}", data=form_data, files=files
                )
        else:
            return self._session_request(
                "POST", f"/projects/{address}", json=data or {}
            )

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def get_comments(
        self,
        project_id: int,
        page: int = 1,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Fetch comments for a project.

        ``GET /api/comments``
        """
        return self._session_request(
            "GET",
            "/comments",
            params={"projectId": project_id, "page": page, "limit": limit},
        )

    def create_comment(
        self,
        project_id: int,
        content: str,
        author_address: str,
    ) -> Dict[str, Any]:
        """Post a comment on a project.

        ``POST /api/comments``
        """
        return self._session_request(
            "POST",
            "/comments",
            json={
                "projectId": project_id,
                "content": content,
                "authorAddress": author_address,
            },
        )

    def delete_comment(self, comment_id: int, author_address: str) -> Dict[str, Any]:
        """Soft-delete a comment.

        ``DELETE /api/comments``
        """
        return self._session_request(
            "DELETE",
            "/comments",
            params={"id": comment_id, "authorAddress": author_address},
        )

    # ------------------------------------------------------------------
    # v1 Data endpoints (API key required)
    # ------------------------------------------------------------------

    def get_tokens(
        self,
        search: Optional[str] = None,
        is_prediction: Optional[bool] = None,
        sort: str = "newest",
        page: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """List tokens.

        ``GET /api/v1/tokens``
        """
        params: Dict[str, Any] = {"sort": sort, "page": page, "limit": limit}
        if search is not None:
            params["search"] = search
        if is_prediction is not None:
            params["isPrediction"] = str(is_prediction).lower()
        return self._api_key_request("GET", "/v1/tokens", params=params)

    def get_token(self, address: str) -> Dict[str, Any]:
        """Get details for a single token.

        ``GET /api/v1/tokens/{address}``
        """
        return self._api_key_request("GET", f"/v1/tokens/{address}")

    def get_token_candles(
        self,
        address: str,
        interval: str = "1h",
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        limit: int = 500,
    ) -> Any:
        """Fetch OHLCV candle data for a token.

        ``GET /api/v1/tokens/{address}/candles``
        """
        params: Dict[str, Any] = {"interval": interval, "limit": limit}
        if from_ts is not None:
            params["from"] = from_ts
        if to_ts is not None:
            params["to"] = to_ts
        return self._api_key_request("GET", f"/v1/tokens/{address}/candles", params=params)

    def get_token_trades(
        self,
        address: str,
        cursor: Optional[str] = None,
        limit: int = 20,
        trade_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch trades for a token.

        ``GET /api/v1/tokens/{address}/trades``
        """
        params: Dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if trade_type is not None:
            params["type"] = trade_type
        return self._api_key_request("GET", f"/v1/tokens/{address}/trades", params=params)

    def get_token_orders(
        self,
        address: str,
        status: Optional[str] = None,
        outcome_id: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Fetch orders for a token.

        ``GET /api/v1/tokens/{address}/orders``
        """
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if status is not None:
            params["status"] = status
        if outcome_id is not None:
            params["outcomeId"] = outcome_id
        return self._api_key_request("GET", f"/v1/tokens/{address}/orders", params=params)

    def get_token_comments(
        self,
        address: str,
        page: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Fetch comments for a token.

        ``GET /api/v1/tokens/{address}/comments``
        """
        return self._api_key_request(
            "GET",
            f"/v1/tokens/{address}/comments",
            params={"page": page, "limit": limit},
        )

    def get_token_whitelist(
        self,
        address: str,
        wallet: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Fetch whitelist entries for a token.

        ``GET /api/v1/tokens/{address}/whitelist``
        """
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if wallet is not None:
            params["wallet"] = wallet
        return self._api_key_request(
            "GET", f"/v1/tokens/{address}/whitelist", params=params
        )

    def get_wallet_transactions(
        self,
        address: str,
        cursor: Optional[str] = None,
        limit: int = 20,
        tx_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch transactions for a wallet.

        ``GET /api/v1/wallet/{address}/transactions``
        """
        params: Dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if tx_type is not None:
            params["type"] = tx_type
        return self._api_key_request(
            "GET", f"/v1/wallet/{address}/transactions", params=params
        )

    def get_market_liquidity(
        self,
        address: str,
        cursor: Optional[str] = None,
        limit: int = 20,
        outcome_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch liquidity events for a prediction market.

        ``GET /api/v1/markets/{address}/liquidity``
        """
        params: Dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if outcome_id is not None:
            params["outcomeId"] = outcome_id
        return self._api_key_request(
            "GET", f"/v1/markets/{address}/liquidity", params=params
        )

    # ------------------------------------------------------------------
    # Order sync (public, no auth required)
    # ------------------------------------------------------------------

    def sync_order(self, tx_hash: str, market_type: str = "public") -> Dict[str, Any]:
        """Sync an on-chain order event to the database.

        ``POST /api/v1/orders/sync``

        Call after listOrder, cancelOrder, or buyOrder transactions.
        No authentication required (public endpoint).
        """
        url = f"{self.client.api_domain}/api/v1/orders/sync"
        response = self.session.post(url, json={"txHash": tx_hash, "marketType": market_type})
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Platform Pulse (public, no auth required)
    # ------------------------------------------------------------------

    def get_pulse(self) -> Dict[str, Any]:
        """Return live platform statistics.

        ``GET /api/pulse``

        No authentication required. Response is cached for 60 seconds.
        """
        url = f"{self.client.api_domain}/api/pulse"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Leaderboard & Public Profile (public, no auth required)
    # ------------------------------------------------------------------

    def get_leaderboard(self, page: int = 1, limit: int = 50) -> Dict[str, Any]:
        """Return public leaderboard rankings.

        ``GET /api/v1/leaderboard``

        No authentication required. Response is cached for 60 seconds.
        """
        url = f"{self.client.api_domain}/api/v1/leaderboard"
        response = self.session.get(url, params={"page": page, "limit": limit})
        response.raise_for_status()
        return response.json()

    def get_public_profile(self, wallet: str) -> Dict[str, Any]:
        """Return the public profile for a wallet address.

        ``GET /api/v1/profile/{wallet}``

        No authentication required. Only socials the user has toggled
        public are included. Point totals are never exposed.
        """
        url = f"{self.client.api_domain}/api/v1/profile/{wallet}"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()

    def get_public_profile_referrals(self, wallet: str) -> Dict[str, Any]:
        """Return referral counts for a wallet.

        ``GET /api/v1/profile/{wallet}/referrals``

        Requires session or API key authentication.
        """
        return self._auth_request("GET", f"/v1/profile/{wallet}/referrals")

    # ------------------------------------------------------------------
    # Transaction sync (public, no auth required)
    # ------------------------------------------------------------------

    def sync_transaction(self, tx_hash: str) -> Dict[str, Any]:
        """Sync an on-chain transaction to the database.

        ``POST /api/v1/sync``

        Handles all event types: trades, loans, vault staking, vesting,
        prediction markets, resolver events, and more.
        No authentication required (public on-chain data). Rate limited to 20 req/min.
        Idempotent — submitting the same txHash twice is safe.
        """
        url = f"{self.client.api_domain}/api/v1/sync"
        response = self.session.post(url, json={"txHash": tx_hash})
        response.raise_for_status()
        return response.json()

    def sync_loan(self, tx_hash: str) -> Dict[str, Any]:
        """Deprecated: use sync_transaction() instead."""
        return self.sync_transaction(tx_hash)

    # ------------------------------------------------------------------
    # Faucet (session required)
    # ------------------------------------------------------------------

    def get_faucet_status(self) -> Dict[str, Any]:
        """Check faucet eligibility and signal breakdown.

        ``GET /api/v1/faucet/status``

        Requires SIWE session. The wallet is determined from the session.

        Returns dict with keys: eligible, canClaim, dailyAmount, signals,
        cooldownRemaining, nextClaimAt, hasReferrer.
        """
        return self._session_request("GET", "/v1/faucet/status")

    def claim_faucet(self, referrer: Optional[str] = None) -> Dict[str, Any]:
        """Claim daily USDB from the treasury.

        ``POST /api/v1/faucet/claim``

        Requires SIWE session. Amount is based on active signals (max 500
        USDB/day). 24-hour cooldown between claims.

        Parameters
        ----------
        referrer : str, optional
            Referrer wallet address for the referral system.

        Returns dict with keys: success, amount, txHash, signals.
        """
        body: Dict[str, str] = {}
        if referrer:
            body["referrer"] = referrer
        return self._session_request("POST", "/v1/faucet/claim", json=body)

    # ------------------------------------------------------------------
    # Loans, Vault & Vesting read endpoints (session or API key)
    # ------------------------------------------------------------------

    def _auth_request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        """Make a request using API key (preferred) or session cookie."""
        api_key = self.client.api_key
        if api_key:
            return self._api_key_request(method, endpoint, **kwargs)
        else:
            return self._session_request(method, endpoint, **kwargs)

    def get_loans(
        self,
        source: Optional[str] = None,
        active: Optional[bool] = None,
        page: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """List loans for the authenticated wallet.

        ``GET /api/v1/loans``

        Params: source (hub|vault|leverage|vesting), active (true|false), page, limit.
        """
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if source is not None:
            params["source"] = source
        if active is not None:
            params["active"] = str(active).lower()
        return self._auth_request("GET", "/v1/loans", params=params)

    def get_loan_events(
        self,
        source: Optional[str] = None,
        action: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """List loan lifecycle events for the authenticated wallet.

        ``GET /api/v1/loans/events``

        Params: source, action (created|repaid|extended|increased|liquidated|partial_sell|liquidation_claimed), page, limit.
        """
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if source is not None:
            params["source"] = source
        if action is not None:
            params["action"] = action
        return self._auth_request("GET", "/v1/loans/events", params=params)

    def get_vault_events(
        self,
        action: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """List vault staking events for the authenticated wallet.

        ``GET /api/v1/vault/events``

        Params: action (wrap|unwrap|lock|unlock), page, limit.
        """
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if action is not None:
            params["action"] = action
        return self._auth_request("GET", "/v1/vault/events", params=params)

    def get_vesting_events(
        self,
        action: Optional[str] = None,
        vesting_id: Optional[int] = None,
        page: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """List vesting events for the authenticated wallet.

        ``GET /api/v1/vesting/events``

        Params: action (created|claimed|extended|beneficiary_changed), vestingId, page, limit.
        """
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if action is not None:
            params["action"] = action
        if vesting_id is not None:
            params["vestingId"] = vesting_id
        return self._auth_request("GET", "/v1/vesting/events", params=params)

    def get_market_events(
        self,
        action: Optional[str] = None,
        market_token: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """List prediction market resolution events for the authenticated wallet.

        ``GET /api/v1/markets/events``

        Params: action (propose|dispute|vote|veto|resolve|redeem|invalidate|
        bounty_claim|dispute_reset|bonds_distributed|bonds_seized),
        marketToken, page, limit.
        """
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if action is not None:
            params["action"] = action
        if market_token is not None:
            params["marketToken"] = market_token
        return self._auth_request("GET", "/v1/markets/events", params=params)

    # ------------------------------------------------------------------
    # Twitter / X Verification
    # ------------------------------------------------------------------

    def request_twitter_challenge(self) -> Dict[str, Any]:
        """Request a verification code for X/Twitter linking.

        ``POST /api/auth/twitter/challenge``

        Returns a code to include in a tweet and a pre-built tweet template.
        Accepts either session cookie or API key.
        """
        api_key = self.client.api_key
        if api_key:
            return self._api_key_request("POST", "/auth/twitter/challenge")
        else:
            return self._session_request("POST", "/auth/twitter/challenge")

    def verify_twitter(self, tweet_url: str) -> Dict[str, Any]:
        """Verify a tweet containing the challenge code and link the X account.

        ``POST /api/auth/twitter/verify-tweet``

        Accepts either session cookie or API key.
        """
        body = {"tweetUrl": tweet_url}
        api_key = self.client.api_key
        if api_key:
            return self._api_key_request("POST", "/auth/twitter/verify-tweet", json=body)
        else:
            return self._session_request("POST", "/auth/twitter/verify-tweet", json=body)

    # ------------------------------------------------------------------
    # Social Activity (tweet verification for points)
    # ------------------------------------------------------------------

    def verify_social_tweet(self, tweet_url: str) -> Dict[str, Any]:
        """Submit a tweet for points verification.

        ``POST /api/v1/social/verify-tweet``

        Tweet must tag @LaunchOnBasis, be public, and be authored by the
        linked X account. Max 3 attempts per day per wallet.
        """
        return self._auth_request("POST", "/v1/social/verify-tweet", json={"tweetUrl": tweet_url})

    def get_verified_tweets(self) -> Dict[str, Any]:
        """List verified tweets for the authenticated wallet.

        ``GET /api/v1/social/verified-tweets``
        """
        return self._auth_request("GET", "/v1/social/verified-tweets")

    # ------------------------------------------------------------------
    # Bug Reports
    # ------------------------------------------------------------------

    def submit_bug_report(
        self,
        title: str,
        description: str,
        severity: str,
        category: str,
        evidence: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a bug report.

        ``POST /api/v1/bugs/reports``

        Max 5 per day per wallet. Blocked wallets get 403.

        Validation:
        - title: required, max 200 chars
        - description: required, max 5000 chars
        - severity: 'critical', 'high', 'medium', or 'low'
        - category: 'sdk', 'contracts', 'api', 'frontend', or 'docs'
        - evidence: optional, max 1000 chars, must be https:// URL or tx hash (0x + 64 hex)
        """
        body: Dict[str, Any] = {
            "title": title,
            "description": description,
            "severity": severity,
            "category": category,
        }
        if evidence is not None:
            body["evidence"] = evidence
        return self._auth_request("POST", "/v1/bugs/reports", json=body)

    def get_bug_reports(
        self,
        status: Optional[str] = None,
        wallet: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """List bug reports for the authenticated wallet.

        ``GET /api/v1/bugs/reports``

        Admins can filter by wallet.
        """
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if status is not None:
            params["status"] = status
        if wallet is not None:
            params["wallet"] = wallet
        return self._auth_request("GET", "/v1/bugs/reports", params=params)

    # ------------------------------------------------------------------
    # Reef (Social Feed) — public, no auth required
    # ------------------------------------------------------------------

    def get_reef_feed(
        self,
        section: str = "mixed",
        sort: str = "recent",
        period: str = "24h",
        q: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Fetch the paginated Reef social feed.

        ``GET /api/reef/feed``

        No authentication required. Cached for 10 seconds.

        Params:
        - section: 'human', 'agent', 'mixed', or 'all'
        - sort: 'recent' (newest first) or 'top' (highest score)
        - period: time filter for top sort — 'all', '1h', '24h', '7d', '30d'
        - q: search query (matches title and body)
        - limit: items per page (max 100)
        - offset: pagination offset
        """
        params: Dict[str, Any] = {
            "section": section,
            "sort": sort,
            "period": period,
            "limit": limit,
            "offset": offset,
        }
        if q is not None:
            params["q"] = q
        url = f"{self.client.api_domain}/api/reef/feed"
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def get_reef_feed_by_wallet(
        self,
        wallet: str,
        section: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Fetch Reef posts by a specific wallet address.

        ``GET /api/reef/feed/{wallet}``

        No authentication required.

        Params:
        - wallet: wallet address
        - section: optional filter — 'human', 'agent', or 'mixed'
        - limit: items per page (max 50)
        - offset: pagination offset
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if section is not None:
            params["section"] = section
        url = f"{self.client.api_domain}/api/reef/feed/{wallet}"
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def get_reef_post(self, post_id: str) -> Dict[str, Any]:
        """Fetch a single Reef post with all comments.

        ``GET /api/reef/post/{post_id}``

        No authentication required.
        """
        url = f"{self.client.api_domain}/api/reef/post/{post_id}"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()

    def get_reef_highlights(self, section: str = "all") -> Dict[str, Any]:
        """Fetch top 10 Reef posts by score in the last 24 hours.

        ``GET /api/reef/highlights``

        No authentication required. Cached for 30 seconds.

        Params:
        - section: 'all', 'human', 'agent', or 'mixed'
        """
        url = f"{self.client.api_domain}/api/reef/highlights"
        response = self.session.get(url, params={"section": section})
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Reef — authenticated endpoints (session or API key)
    # ------------------------------------------------------------------

    def create_reef_post(self, section: str, title: str, body: Optional[str] = None) -> Dict[str, Any]:
        """Create a new Reef post. ``POST /api/reef/post``"""
        payload: Dict[str, Any] = {"section": section, "title": title}
        if body is not None:
            payload["body"] = body
        return self._auth_request("POST", "/reef/post", json=payload)

    def edit_reef_post(self, post_id: str, title: Optional[str] = None, body: Optional[str] = None) -> Dict[str, Any]:
        """Edit your own Reef post. ``PATCH /api/reef/post/{postId}/manage``"""
        payload: Dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        return self._auth_request("PATCH", f"/reef/post/{post_id}/manage", json=payload)

    def delete_reef_post(self, post_id: str) -> Dict[str, Any]:
        """Soft-delete a Reef post. ``DELETE /api/reef/post/{postId}/manage``"""
        return self._auth_request("DELETE", f"/reef/post/{post_id}/manage")

    def create_reef_comment(self, post_id: str, message: str, parent_id: Optional[str] = None) -> Dict[str, Any]:
        """Add a comment to a Reef post. ``POST /api/reef/post/{postId}/comment``"""
        payload: Dict[str, Any] = {"message": message}
        if parent_id is not None:
            payload["parentId"] = parent_id
        return self._auth_request("POST", f"/reef/post/{post_id}/comment", json=payload)

    def edit_reef_comment(self, comment_id: str, message: str) -> Dict[str, Any]:
        """Edit your own Reef comment. ``PATCH /api/reef/comment/{commentId}/manage``"""
        return self._auth_request("PATCH", f"/reef/comment/{comment_id}/manage", json={"message": message})

    def delete_reef_comment(self, comment_id: str) -> Dict[str, Any]:
        """Soft-delete a Reef comment. ``DELETE /api/reef/comment/{commentId}/manage``"""
        return self._auth_request("DELETE", f"/reef/comment/{comment_id}/manage")

    def vote_reef_post(self, post_id: str) -> Dict[str, Any]:
        """Toggle upvote on a Reef post. ``POST /api/reef/vote/{postId}``"""
        return self._auth_request("POST", f"/reef/vote/{post_id}")

    def vote_reef_comment(self, comment_id: str) -> Dict[str, Any]:
        """Toggle upvote on a Reef comment. ``POST /api/reef/vote/comment/{commentId}``"""
        return self._auth_request("POST", f"/reef/vote/comment/{comment_id}")

    def get_reef_votes(self, post_ids: Optional[str] = None, comment_ids: Optional[str] = None) -> Dict[str, Any]:
        """Check which posts/comments the user has voted on. ``GET /api/reef/votes``"""
        params: Dict[str, Any] = {}
        if post_ids is not None:
            params["postIds"] = post_ids
        if comment_ids is not None:
            params["commentIds"] = comment_ids
        return self._auth_request("GET", "/reef/votes", params=params)

    def report_reef_post(self, post_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """Report a Reef post for moderation. ``POST /api/reef/report/{postId}``"""
        payload: Dict[str, Any] = {}
        if reason is not None:
            payload["reason"] = reason
        return self._auth_request("POST", f"/reef/report/{post_id}", json=payload)

    # ------------------------------------------------------------------
    # Me (session or API key)
    # ------------------------------------------------------------------

    def get_my_stats(self) -> Dict[str, Any]:
        """Wallet activity statistics for the authenticated user.

        ``GET /api/v1/me/stats``

        Returns trade counts, prediction market activity, token/market
        creation counts, loan info, and agent identity.
        """
        return self._auth_request("GET", "/v1/me/stats")

    def get_my_projects(self) -> Dict[str, Any]:
        """Tokens and prediction markets created by the authenticated user.

        ``GET /api/v1/me/projects``

        Returns tokens (non-prediction) and markets (prediction) lists,
        ordered by creation date descending.
        """
        return self._auth_request("GET", "/v1/me/projects")

    def get_my_profile(self) -> Dict[str, Any]:
        """Full profile for the authenticated wallet.

        ``GET /api/v1/me/profile``

        Returns username, avatar, tier, leaderboard rank, and all linked
        social accounts (including private socials).
        """
        return self._auth_request("GET", "/v1/me/profile")

    def update_my_profile(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update profile fields.

        ``POST /api/v1/me/profile``

        Each request performs one action based on which key is present
        in the payload.
        """
        return self._auth_request("POST", "/v1/me/profile", json=payload)

    def get_my_referrals(self) -> Dict[str, Any]:
        """Referral overview for the authenticated user.

        ``GET /api/v1/me/referrals``

        Returns who referred you, your tier rate, and your direct +
        indirect referrals sorted by rank.
        """
        return self._auth_request("GET", "/v1/me/referrals")

    # ------------------------------------------------------------------
    # Moltbook account linking (session or API key)
    # ------------------------------------------------------------------

    def link_moltbook(self, moltbook_name: str) -> Dict[str, Any]:
        """Start linking a Moltbook agent to your wallet.

        ``POST /api/moltbook/link``

        Returns a challenge code that the agent must post in m/basis
        on Moltbook to prove ownership.

        Parameters
        ----------
        moltbook_name : str
            The Moltbook agent/username to link.

        Returns dict with keys: challenge, instructions.
        """
        return self._auth_request(
            "POST", "/moltbook/link", json={"moltbookName": moltbook_name}
        )

    def verify_moltbook(
        self, moltbook_name: str, post_id: str
    ) -> Dict[str, Any]:
        """Complete Moltbook linking by verifying the challenge post.

        ``POST /api/moltbook/verify``

        Server fetches the post, verifies the author matches and the
        challenge code is present. The challenge post counts as the
        first verified post (50 points).

        Parameters
        ----------
        moltbook_name : str
            The Moltbook agent/username being linked.
        post_id : str
            Post ID (UUID) or full URL of the challenge post.

        Returns dict with keys: success, moltbookName, message.
        """
        return self._auth_request(
            "POST",
            "/moltbook/verify",
            json={"moltbookName": moltbook_name, "postId": post_id},
        )

    def get_moltbook_status(self) -> Dict[str, Any]:
        """Check if wallet has a linked Moltbook account.

        ``GET /api/moltbook/status``

        Returns dict with keys: linked, moltbookName, verified,
        postCount, totalKarma, pendingChallenge.
        """
        return self._auth_request("GET", "/moltbook/status")

    # ------------------------------------------------------------------
    # Moltbook post verification (session or API key)
    # ------------------------------------------------------------------

    def verify_moltbook_post(self, post_id: str) -> Dict[str, Any]:
        """Submit a Moltbook post for points.

        ``POST /api/v1/social/verify-moltbook-post``

        Post must be by your linked agent, in m/basis or mentioning
        Basis. Max 3 per day, 7-day lock-in (post must stay up or
        points are revoked). 50 points per verified post.

        Parameters
        ----------
        post_id : str
            Post ID (UUID) or full URL.

        Returns dict with keys: success, post (id, postUrl, karma,
        submolt, mentionsBasis, createdAt).
        """
        return self._auth_request(
            "POST",
            "/v1/social/verify-moltbook-post",
            json={"postId": post_id},
        )

    def get_verified_moltbook_posts(self) -> Dict[str, Any]:
        """List your submitted Moltbook posts.

        ``GET /api/v1/social/verified-moltbook-posts``

        Returns dict with key: posts (list of id, postUrl, karma,
        submolt, mentionsBasis, verified, lastVerifiedAt, createdAt).
        """
        return self._auth_request("GET", "/v1/social/verified-moltbook-posts")
