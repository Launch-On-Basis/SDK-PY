"""Up/Down (BTC/ETH/BNB) prediction market module.

Mirrors the TS ``UpDownModule`` / ``UpDownAssetModule`` pair. ``client.updown``
is a namespace holder; ``client.updown.btc`` (and ``.eth`` / ``.bnb`` once
deployed) are per-asset modules. Assets without a deployment are ``None``.
"""

import logging
from typing import Optional, Dict, Any, List

from web3 import Web3
from .factory import load_abi

logger = logging.getLogger(__name__)


# --- Enums (mirror TS Timeframe / Side / Outcome consts) ---------------------

class Timeframe:
    """Timeframe enum: 0=5m, 1=15m, 2=1h, 3=4h, 4=24h."""
    FIVE_MIN = 0
    FIFTEEN_MIN = 1
    ONE_HOUR = 2
    FOUR_HOUR = 3
    ONE_DAY = 4


class Side:
    """Side enum: 0=None, 1=Bull, 2=Bear. Bets always pass 1 or 2."""
    NONE = 0
    BULL = 1
    BEAR = 2


class Outcome:
    """Outcome enum: 0=Pending, 1=BullWins, 2=BearWins, 3=Canceled."""
    PENDING = 0
    BULL_WINS = 1
    BEAR_WINS = 2
    CANCELED = 3


_ZERO_ADDRESS = '0x0000000000000000000000000000000000000000'


class OracleNotReadyError(Exception):
    """Raised by ``settle_current_round`` when the contract reverts because
    Chainlink has not yet published a price update past ``round.endTime``.
    Transient — wait for the oracle to tick and retry, or use
    ``advance_round(tf)`` which polls the oracle automatically.

    The two on-chain reverts that map to this error:
      - ``NoUpdateAfterEndTime`` -- ``latestRoundData.updatedAt < round.endTime``
      - ``NoValidPriceInWindow`` -- no Chainlink round in the lookback window
        had ``updatedAt >= round.endTime``

    Attributes:
        tf: timeframe the settle attempt was for
        end_time: round.endTime (unix seconds)
        contract_error: ``"NoUpdateAfterEndTime"`` or ``"NoValidPriceInWindow"``
    """
    def __init__(self, message: str, tf: int, end_time: int, contract_error: str):
        super().__init__(message)
        self.tf = tf
        self.end_time = end_time
        self.contract_error = contract_error


# --- Per-asset module --------------------------------------------------------

class UpDownAssetModule:
    """One instance per deployed asset (see ``UpDownModule.KNOWN_ASSETS``)."""

    def __init__(self, client, address: str, asset: str):
        self.client = client
        self.address = Web3.to_checksum_address(address)
        self.asset = asset  # 'btc' | 'eth' | 'bnb'
        self.updown_abi = load_abi('AUpDown.json')
        self.erc20_abi = load_abi('IERC20.json')
        self._contract = self.client.web3.eth.contract(address=self.address, abi=self.updown_abi)

    # --- Internals ---

    def _sync_tx(self, tx_hash: str):
        if not tx_hash.startswith('0x'):
            tx_hash = '0x' + tx_hash
        self.client.api.sync_transaction(tx_hash)

    def _approve_usdb_if_needed(self, amount: int):
        if not self.client.account:
            raise ValueError("Wallet account is required for approval.")
        usdb = self.client.web3.eth.contract(
            address=Web3.to_checksum_address(self.client.usdb_address), abi=self.erc20_abi
        )
        allowance = usdb.functions.allowance(self.client.account.address, self.address).call()
        if allowance < amount:
            self.client.send_transaction(usdb.functions.approve(self.address, amount))

    # --- Reads ---

    def current_round_id(self, tf: int) -> int:
        """Current/active round id for a timeframe. 0 = no rounds opened yet."""
        return self._contract.functions.currentRoundId(tf).call()

    def get_round(self, tf: int, round_id: int) -> Dict[str, Any]:
        """Full Round struct (14 fields). web3.py returns it as a positional tuple;
        this method maps it into a named dict matching the TS UpDownRound interface.
        """
        r = self._contract.functions.getRound(tf, round_id).call()
        return {
            'startTime': r[0], 'endTime': r[1], 'settledAt': r[2],
            'startPrice': r[3], 'endPrice': r[4], 'endPriceRoundId': r[5],
            'virtBull': r[6], 'virtBear': r[7],
            'bullPool': r[8], 'bearPool': r[9],
            'sharesBull': r[10], 'sharesBear': r[11],
            'seedBonus': r[12], 'outcome': r[13],
        }

    def get_current_round(self, tf: int) -> Optional[Dict[str, Any]]:
        """Convenience: combines current_round_id + get_round.
        Returns ``None`` if no rounds have opened for this timeframe."""
        round_id = self.current_round_id(tf)
        if round_id == 0:
            return None
        return {'roundId': round_id, 'round': self.get_round(tf, round_id)}

    def get_user_bet(self, tf: int, round_id: int, user: str) -> Dict[str, Any]:
        """User's bet on a specific round. amount=0 means no bet placed."""
        b = self._contract.functions.getUserBet(tf, round_id, Web3.to_checksum_address(user)).call()
        return {'side': b[0], 'amount': b[1], 'shares': b[2], 'claimed': b[3]}

    def quote_shares(self, tf: int, side: int, amount: int) -> int:
        """Preview the shares a hypothetical bet would mint right now.
        Returns 0 if no active round, amount=0, or side=None.
        """
        return self._contract.functions.quoteShares(tf, side, amount).call()

    def quote_current_payout(self, tf: int, user: str) -> int:
        """Projected payout if the current round were settled with current pool sizes."""
        return self._contract.functions.quoteCurrentPayout(tf, Web3.to_checksum_address(user)).call()

    def quote_claim_payout(self, tf: int, round_id: int, user: str) -> int:
        """Exact USDB the user can claim right now from a settled round.
        Returns 0 in every "nothing to claim" case (lost, already claimed, pending, no bet).
        Use this to gate the Claim button: show iff > 0.
        """
        return self._contract.functions.quoteClaimPayout(tf, round_id, Web3.to_checksum_address(user)).call()

    def current_bull_probability(self, tf: int) -> int:
        """Implied bull-side probability scaled by USD_UNIT (1e18). Divide by 1e18 for fraction."""
        return self._contract.functions.currentBullProbability(tf).call()

    def current_slippage_threshold(self, tf: int) -> int:
        """Current slippage threshold in BPS. Decays from 9500 (95%) to 5500 (55%) over the round."""
        return self._contract.functions.currentSlippageThreshold(tf).call()

    def tf_duration(self, tf: int) -> int:
        """Round duration for a timeframe, in seconds."""
        return self._contract.functions.tfDuration(tf).call()

    def min_bet(self) -> int:
        """Minimum bet size, USDB 18-dec."""
        return self._contract.functions.minBet().call()

    def pending_carryover(self, tf: int) -> int:
        """Carryover queued from panicCancel, waiting for the next round to seed."""
        return self._contract.functions.pendingCarryover(tf).call()

    def protocol_virt_base(self, tf: int) -> int:
        """Current virtual base reserve for a timeframe, USDB 18-dec."""
        return self._contract.functions.protocolVirtBase(tf).call()

    def price_feed(self) -> str:
        """Chainlink AggregatorV3 address used by this contract."""
        return self._contract.functions.priceFeed().call()

    def usdb(self) -> str:
        """USDB token address — should match client.usdb_address for live deployments."""
        return self._contract.functions.usdb().call()

    def swap(self) -> str:
        """Configured swap contract address."""
        return self._contract.functions.swap().call()

    def wash_token(self) -> str:
        """Configured wash-trade detection token."""
        return self._contract.functions.washToken().call()

    def paused(self) -> bool:
        """True if the contract is paused — bet/settle/claim writes will revert."""
        return self._contract.functions.paused().call()

    def ceo(self) -> str:
        """Admin address — only this address can call admin write functions."""
        return self._contract.functions.CEO().call()

    # --- Writes (user) ---

    def bet_bull(self, tf: int, amount: int, min_shares: int = 0) -> Dict[str, Any]:
        """Place a bullish bet on the current round of ``tf``. Auto-approves USDB.

        Pre-checks: ``amount >= min_bet()`` and ``usdb.balanceOf(user) >= amount``.

        Args:
            min_shares: optional slippage protection. If non-zero, throws
                client-side when ``quote_shares(tf, BULL, amount) < min_shares``.
        """
        return self._bet(tf, Side.BULL, amount, min_shares, 'betBull')

    def bet_bear(self, tf: int, amount: int, min_shares: int = 0) -> Dict[str, Any]:
        """Place a bearish bet. See ``bet_bull`` for slippage protection details."""
        return self._bet(tf, Side.BEAR, amount, min_shares, 'betBear')

    def _bet(self, tf: int, side: int, amount: int, min_shares: int, fn_name: str) -> Dict[str, Any]:
        if not self.client.account:
            raise ValueError("Stateful initialization (private_key) is required for write methods.")
        user = self.client.account.address

        # Pre-check minBet
        min_bet_amount = self.min_bet()
        if amount < min_bet_amount:
            raise ValueError(
                f"Bet amount ({amount}) is below minBet ({min_bet_amount} wei = {min_bet_amount / 1e18} USDB)."
            )

        # Pre-check USDB balance
        usdb = self.client.web3.eth.contract(
            address=Web3.to_checksum_address(self.client.usdb_address), abi=self.erc20_abi
        )
        balance = usdb.functions.balanceOf(user).call()
        if balance < amount:
            raise ValueError(
                f"Insufficient USDB. Have: {balance} wei ({balance / 1e18}), "
                f"want: {amount} wei ({amount / 1e18})."
            )

        # Pre-check projected shares. The contract reverts ``ZeroShares`` if
        # slippage crushes shares to 0 (happens late in heavily-skewed rounds
        # when betting the dominant side). Folding the optional min_shares
        # slippage check into the same read so we only do one quote_shares call.
        projected = self.quote_shares(tf, side, amount)
        if projected == 0:
            raise ValueError(
                "Bet would mint 0 shares due to pool skew + slippage. "
                "Consider betting the underdog side or waiting for the round to balance."
            )
        if min_shares > 0 and projected < min_shares:
            raise ValueError(
                f"Slippage: quote_shares would mint {projected} shares, below min_shares ({min_shares})."
            )

        self._approve_usdb_if_needed(amount)
        func = getattr(self._contract.functions, fn_name)(tf, amount)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def claim(self, tf: int, round_id: int) -> Dict[str, Any]:
        """Claim winnings or refund for a settled round. Pre-checks via
        ``quote_claim_payout`` and throws "Nothing to claim" client-side if 0.
        """
        if not self.client.account:
            raise ValueError("Stateful initialization (private_key) is required for write methods.")
        user = self.client.account.address

        claimable = self.quote_claim_payout(tf, round_id, user)
        if claimable == 0:
            raise ValueError(
                f"Nothing to claim on tf={tf} round_id={round_id} for {user}. "
                f"(Already claimed, lost, or round not settled.)"
            )

        func = self._contract.functions.claim(tf, round_id)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def settle_current_round(self, tf: int) -> Dict[str, Any]:
        """Fire-and-forget settle for the current round of ``tf``.

        Public -- anyone can call. ONE attempt, no polling, no waiting. If the
        Chainlink oracle has not ticked past ``round.endTime`` yet, this raises
        :class:`OracleNotReadyError` (a typed, catchable exception -- no need
        to parse revert strings) so the caller can retry on their own schedule.
        For an automatic poll-and-settle flow, use :meth:`advance_round` instead.

        Pre-checks:
          - No active round (``start_prediction`` never called)
          - Round already settled
          - ``now <= endTime`` (still active) -- ``TooEarlyToSettle``
          - ``now > endTime + FINALIZE_WINDOW`` -- points caller to
            :meth:`cancel_current_round_and_start_next` instead

        :param tf: Timeframe enum (0=5m, 1=15m, 2=1h, 3=4h, 4=24h)
        :returns: ``{"hash": "0x...", "receipt": {...}}``
        :raises OracleNotReadyError: if Chainlink hasn't updated past ``round.endTime`` yet
        :raises ValueError: for pre-check failures (no active round, already settled, etc.)

        Example::

            from basis import OracleNotReadyError
            import time

            while True:
                try:
                    btc.settle_current_round(0)
                    break
                except OracleNotReadyError:
                    time.sleep(15)
        """
        if not self.client.account:
            raise ValueError("Stateful initialization (private_key) is required for write methods.")
        self._pre_check_round_timing(tf, "settle")
        try:
            func = self._contract.functions.settleCurrentRound(tf)
            result = self.client.send_transaction(func)
        except Exception as e:
            msg = str(e)
            for name in ("NoUpdateAfterEndTime", "NoValidPriceInWindow"):
                if name in msg:
                    round_id = self.current_round_id(tf)
                    r = self.get_round(tf, round_id)
                    raise OracleNotReadyError(
                        f"Chainlink oracle has not updated past round {round_id} endTime "
                        f"yet ({name}). Wait ~30s and retry settle_current_round, or use "
                        f"advance_round(tf) which polls the oracle automatically.",
                        tf, r['endTime'], name,
                    ) from e
            raise
        self._sync_tx(result['hash'])
        return result

    def cancel_current_round_and_start_next(self, tf: int) -> Dict[str, Any]:
        """Cancel the current round and open the next one. Public -- callable
        once endTime + FINALIZE_WINDOW has passed.

        Pre-checks:
          - No active round
          - Round already settled
          - ``now <= endTime + FINALIZE_WINDOW`` -- settle still possible;
            points caller to ``settle_current_round`` instead
        """
        if not self.client.account:
            raise ValueError("Stateful initialization (private_key) is required for write methods.")
        self._pre_check_round_timing(tf, "cancel")
        func = self._contract.functions.cancelCurrentRoundAndStartNext(tf)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def advance_round(self, tf: int, poll_interval_s: float = 8.0, max_wait_s: float = 360.0) -> Dict[str, Any]:
        """Settle-or-cancel the current round, with built-in oracle wait.

        High-level helper that auto-routes to settle vs cancel based on round
        timing AND polls the Chainlink price feed before submitting a settle
        tx. Use this when you want a "just make it work" call -- no manual
        retry loops, no error-string parsing, no oracle-lag handling.

        Routing logic:
          - Round still in progress (``now <= endTime``) -- raises
            "still in progress".
          - In the settle window (``endTime < now <= endTime + 20min``) --
            polls the price feed every ``poll_interval_s`` until
            ``updatedAt > endTime``, then calls :meth:`settle_current_round`
            once. Returns ``mode='settle'``.
          - Past the settle window -- calls
            :meth:`cancel_current_round_and_start_next`. Returns ``mode='cancel'``.

        **NOTE: this can take several minutes.** On less-active Chainlink feeds
        (ETH/CAKE/DOGE on BSC) the oracle can lag 30s-2min past
        ``round.endTime``. Default ``max_wait_s`` is 6min, leaving 14min of
        the contract's 20min settle window as headroom. If the oracle stays
        stuck for ``max_wait_s``, raises -- at that point a fallback to cancel
        is the only option (will happen automatically once you re-call
        ``advance_round`` after the 20min mark).

        :param tf: Timeframe enum (0=5m, 1=15m, 2=1h, 3=4h, 4=24h)
        :param poll_interval_s: How often to re-read the price feed when waiting.
            Default ``8.0`` seconds -- matches the dApp's UI poll cadence.
        :param max_wait_s: Max time to wait for the oracle before giving up.
            Default ``360.0`` (6 minutes).
        :returns: ``{"hash": ..., "receipt": ..., "mode": "settle"|"cancel"}``
        :raises ValueError: if no active round, the round is already settled,
            the round is still in progress, or the oracle stays stuck longer
            than ``max_wait_s``.

        Example::

            # Standard bot loop -- handles oracle lag automatically. May take several minutes.
            client.updown.eth.advance_round(0)
        """
        if not self.client.account:
            raise ValueError("Stateful initialization (private_key) is required for write methods.")
        import time
        cur = self.get_current_round(tf)
        if cur is None:
            raise ValueError(f"No active round for tf={tf} -- start_prediction has not been called.")
        round_id = cur['roundId']
        r = cur['round']
        if r['outcome'] != 0:  # not Pending
            raise ValueError(f"Round {round_id} is already settled (outcome={r['outcome']}). Nothing to advance.")
        FINALIZE_WINDOW = 1200
        now = int(time.time())
        end_time = r['endTime']
        if now <= end_time:
            secs = end_time - now
            raise ValueError(
                f"Round {round_id} is still in progress -- {secs}s remaining (ends at unix {end_time})."
            )
        deadline = end_time + FINALIZE_WINDOW
        if now > deadline:
            result = self.cancel_current_round_and_start_next(tf)
            return {**result, 'mode': 'cancel'}

        # Settle path -- wait for oracle, then settle.
        self._wait_for_oracle(end_time, poll_interval_s, max_wait_s)
        result = self.settle_current_round(tf)
        return {**result, 'mode': 'settle'}

    def _wait_for_oracle(self, end_time: int, poll_interval_s: float, max_wait_s: float):
        """Poll the Chainlink price feed every ``poll_interval_s`` seconds until
        ``latestRoundData.updatedAt > end_time``. Raises if ``max_wait_s``
        elapses without the oracle ticking. Used by ``advance_round`` to bridge
        the settle path's oracle dependency.
        """
        import time
        from web3 import Web3
        agg_abi = load_abi('AggregatorV3Interface.json')
        price_feed_addr = Web3.to_checksum_address(self._contract.functions.priceFeed().call())
        feed = self.client.web3.eth.contract(address=price_feed_addr, abi=agg_abi)
        deadline = time.monotonic() + max_wait_s
        while time.monotonic() < deadline:
            data = feed.functions.latestRoundData().call()
            updated_at = data[3]  # (roundId, answer, startedAt, updatedAt, answeredInRound)
            if updated_at > end_time:
                return
            time.sleep(poll_interval_s)
        raise ValueError(
            f"[advance_round] Chainlink price feed ({price_feed_addr}) has not updated past "
            f"round.endTime ({end_time}) after {int(max_wait_s)}s of polling. "
            "The oracle may be stalled. If we're past round.endTime + 20min, retry -- "
            "advance_round will fall back to cancel."
        )

    def _pre_check_round_timing(self, tf: int, mode: str):
        """Validate timing for settle / cancel. mode='settle' requires the
        round to be in [endTime, endTime+FINALIZE_WINDOW]; mode='cancel'
        requires past that window.
        """
        import time
        cur = self.get_current_round(tf)
        if cur is None:
            raise ValueError(f"No active round for tf={tf} -- start_prediction has not been called.")
        round_id = cur['roundId']
        r = cur['round']
        if r['outcome'] != 0:  # not Pending
            raise ValueError(f"Round {round_id} is already settled (outcome={r['outcome']}). Wait for the next round.")
        FINALIZE_WINDOW = 1200  # 20 minutes, matches contract constant
        now = int(time.time())
        end_time = r['endTime']
        deadline = end_time + FINALIZE_WINDOW
        if mode == "settle":
            if now <= end_time:
                secs = end_time - now
                raise ValueError(
                    f"Round {round_id} has not ended yet -- {secs}s remaining (ends at unix {end_time})."
                )
            if now > deadline:
                raise ValueError(
                    f"Settle window expired (deadline was unix {deadline}). "
                    f"Call cancel_current_round_and_start_next instead to refund and start the next round."
                )
        else:  # cancel
            if now <= deadline:
                secs = deadline - now
                raise ValueError(
                    f"Settle window not yet expired -- {secs}s remaining. "
                    f"Call settle_current_round instead until the window closes."
                )

    # --- Writes (admin / CEO-only) ---

    def start_prediction(self) -> Dict[str, Any]:
        """ADMIN. Open round 1 on every timeframe. Idempotent across timeframes."""
        return self._admin_write('startPrediction', [])

    def set_paused(self, paused: bool) -> Dict[str, Any]:
        """ADMIN. Toggle the pause flag."""
        return self._admin_write('setPaused', [paused])

    def panic_cancel(self) -> Dict[str, Any]:
        """ADMIN. Emergency cancel + pause."""
        return self._admin_write('panicCancel', [])

    def resume_prediction(self) -> Dict[str, Any]:
        """ADMIN. Counterpart to panic_cancel — unpause and open the next round."""
        return self._admin_write('resumePrediction', [])

    def set_min_bet(self, amount: int) -> Dict[str, Any]:
        """ADMIN. Update the minimum bet size, USDB 18-dec."""
        return self._admin_write('setMinBet', [amount])

    def set_price_feed(self, new_feed: str) -> Dict[str, Any]:
        """ADMIN. Update the Chainlink price feed address."""
        return self._admin_write('setPriceFeed', [Web3.to_checksum_address(new_feed)])

    def set_usdb(self, new_usdb: str) -> Dict[str, Any]:
        """ADMIN. Update the USDB token address."""
        return self._admin_write('setUsdb', [Web3.to_checksum_address(new_usdb)])

    def set_swap(self, new_swap: str) -> Dict[str, Any]:
        """ADMIN. Update the swap contract address."""
        return self._admin_write('setSwap', [Web3.to_checksum_address(new_swap)])

    def set_wash_token(self, new_token: str) -> Dict[str, Any]:
        """ADMIN. Update the wash-trade detection token."""
        return self._admin_write('setWashToken', [Web3.to_checksum_address(new_token)])

    def set_ceo(self, new_ceo: str) -> Dict[str, Any]:
        """ADMIN. Transfer the CEO role."""
        return self._admin_write('setCEO', [Web3.to_checksum_address(new_ceo)])

    def emergency_withdraw(self, amount: int) -> Dict[str, Any]:
        """ADMIN -- DANGER. Pull USDB from the contract to CEO. Reverts NotCEO if caller != CEO."""
        return self._admin_write('emergencyWithdraw', [amount])

    def _admin_write(self, fn_name: str, args: list) -> Dict[str, Any]:
        if not self.client.account:
            raise ValueError("Stateful initialization (private_key) is required for write methods.")
        func = getattr(self._contract.functions, fn_name)(*args)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result


# --- Namespace holder --------------------------------------------------------

class UpDownModule:
    """Namespace exposed as ``client.updown``. Per-asset attributes
    (``btc`` / ``eth`` / ``bnb`` / ``cake`` / ``doge``) are
    ``UpDownAssetModule`` for deployed assets and ``None`` for zero-address
    (not-yet-deployed) assets.
    """

    #: All asset keys the SDK knows about. Hardcoded -- adding a new asset is
    #: a deliberate SDK release, even if the contract appears in contracts.json.
    KNOWN_ASSETS = ("btc", "eth", "bnb", "cake", "doge")

    def __init__(self, client, addresses: Dict[str, Optional[str]]):
        # Initialize all known asset slots to None first so attribute access never AttributeErrors.
        for asset in self.KNOWN_ASSETS:
            setattr(self, asset, None)
        # Then instantiate any non-zero-address ones.
        for asset in self.KNOWN_ASSETS:
            mod = self._make(client, addresses.get(asset), asset)
            if mod is not None:
                setattr(self, asset, mod)

    @staticmethod
    def _make(client, addr: Optional[str], asset: str) -> Optional[UpDownAssetModule]:
        if not addr or addr.lower() == _ZERO_ADDRESS:
            return None
        return UpDownAssetModule(client, addr, asset)

    @property
    def all(self) -> List[UpDownAssetModule]:
        """All deployed per-asset modules, in declaration order."""
        return [m for m in (getattr(self, a) for a in self.KNOWN_ASSETS) if m is not None]

    def by_asset(self, asset: str) -> Optional[UpDownAssetModule]:
        """Convenience lookup: ``client.updown.by_asset('btc')``."""
        if asset not in self.KNOWN_ASSETS:
            return None
        return getattr(self, asset, None)
