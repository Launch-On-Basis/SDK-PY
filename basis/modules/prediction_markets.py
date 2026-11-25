"""The public prediction-market host (TypeScript SDK: src/modules/PredictionMarkets.ts).

Ranked / multi-winner markets resolved by proposal, dispute and vote (``client.market_resolver``). Each market keeps
the resolver it was created with. When the seed floor (``get_min_seed``) is 0 a market may open without a seed.
"""

from typing import Any, Dict, Optional, Sequence

from ..config.addresses import is_zero_address
from ..core.errors import ValidationError
from ..core.ranking import normalize_ranking, validate_tie_mask
from ..core.units import Amount, parse_amount, to_address
from .markets_base import HumanAmount, MarketsBase


class PredictionMarketsModule(MarketsBase):
    """The public prediction-market host."""

    market_type = "public"
    _contract_name = "TrancheMarket"

    @property
    def _host_entry(self) -> Optional[str]:
        return self.client.addresses.get("tranche")


    def create_market(
        self,
        name: str,
        symbol: str,
        outcomes: Sequence[str],
        end_time: int,
        tranche_bp: Optional[Sequence[int]] = None,
        main: Optional[str] = None,
        frozen: bool = False,
        presale_tokens: int = 0,
        seed: Optional[HumanAmount] = None,
        description: Optional[str] = None,
        approval: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a public market.

        - outcomes: 2 to the host's outcome limit (at most 255) labels.
        - end_time: unix seconds; 0 = open-ended, otherwise at least 60 s ahead of chain time.
        - tranche_bp: payout slices per finishing position in bps: 1-10 entries, not more than the outcomes, each at
          least the outcome count, never increasing, sum 10000. Default [10000] (winner takes all).
        - main: address or address-book key (default: the default main). Its pair must be active ('pair not active'
          otherwise) and, with a seed above 0, its trading open ('Trading is not enabled') and its pair asset not paused
          by its issuer ('pair asset paused': the seed is paid in it).
        - frozen: invite-only presale on the market token; needs presale_tokens > 0. The seed buys into the presale in
          the creation tx, so a seed that buys the whole presale ends the freeze at once.
        - presale_tokens: presale size in whole tokens, at most (2^256 - 1) / 1e18 ('presaleTokens'); 0 = none.
        - seed: creator seed in the main's pair asset (int raw, or a human str/float/Decimal). At least the floor
          (``get_min_seed``) and a multiple of the seed step; 0 is allowed only when the floor is 0. Default: the floor.
          Risk of a seed of 0: the creator holds no shares, and if no ranked outcome has a bettor the whole pot goes to
          the protocol.
        - description: sent to the approval route.
        - approval: {'signature', 'deadline', 'signed': {'name','symbol','outcomes','endTime'}} you already hold (from
          ``client.api.approve_market``, or the ``approval`` of an error a failed create raised, see below). Omit it to
          request one when gating is on.

        Gating on and no ``approval`` given, the approval route's own rules are checked first: a name of at most 200
        characters, a symbol of 1-16 letters or digits, at most 150 outcomes of at most 120 characters each, a
        description of at most 2000, and end_time 0 or 3660 s to 1825 days ahead (the later of chain time and this
        machine's clock).

        BASIS approves each new market: with gating on, the approval is verified locally and its approved strings are
        sent verbatim. An error raised after the approval that leaves it unused (the chain-clock read after the review,
        the seed approval or the create tx failed or was refused before anything was created; not a
        ``TxNotConfirmedError`` of the create itself, which may still be mined, nor a refusal of the approval itself:
        'Approval expired' or 'endTime' no longer far enough ahead, or the host's revert 'Approval expired' / 'Approval
        already used') carries it as ``e.approval``: an approval stays valid
        until its deadline (about an hour) and is
        single-use, so pass it back (``approval=e.approval``) to try again without another review.
        Returns {'hash','receipt','result','warnings','market','approval','sent','seed_amount','seed_tokens',
        'shares_per_outcome','frozen'}: the market token (``MarketCreated``), the seed paid (an ``Amount`` in the main's
        pair asset), the market tokens it bought (an ``Amount`` in the new market token, unit ``token``) and the creator's
        shares per outcome (raw, 18 decimals; ``MarketSeeded``), and whether the token is still invite-only, read after
        the tx (None when an event or that read is missing).
        """
        return self._create_market_internal(
            False, name=name, symbol=symbol, outcomes=outcomes, end_time=end_time, tranche_bp=tranche_bp, main=main,
            frozen=frozen, presale_tokens=presale_tokens, seed=seed, description=description, approval=approval,
        )

    def create_market_with_metadata(
        self,
        name: str,
        symbol: str,
        outcomes: Sequence[str],
        end_time: int,
        tranche_bp: Optional[Sequence[int]] = None,
        main: Optional[str] = None,
        frozen: bool = False,
        presale_tokens: int = 0,
        seed: Optional[HumanAmount] = None,
        description: Optional[str] = None,
        approval: Optional[Dict[str, Any]] = None,
        *,
        image_url: Optional[str] = None,
        image_file: Optional[Any] = None,
        website: Optional[str] = None,
        telegram: Optional[str] = None,
        twitterx: Optional[str] = None,
        outcome_art: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """``create_market`` (the same arguments, in the same order), then upload the image (``image_url`` or
        ``image_file``, a local path or the bytes) and register the metadata; the metadata arguments are keyword-only.
        The private host takes the same arguments (plus its ``private_event``). Credentials, the metadata fields,
        ``outcome_art`` ({<outcome label>: <picture id>} from the site's library, looked up there; set once) and the
        image are checked before the approval request and the tx; a failed upload after it never raises: it comes back
        in 'upload_error' (finish with ``retry_market_metadata``). Adds 'image_url', 'metadata' and 'upload_error' to the
        result."""
        return self._create_market_with_metadata_internal(
            False, image_url=image_url, image_file=image_file, website=website, telegram=telegram, twitterx=twitterx,
            outcome_art=outcome_art, name=name, symbol=symbol, outcomes=outcomes, end_time=end_time,
            tranche_bp=tranche_bp, main=main, frozen=frozen, presale_tokens=presale_tokens, seed=seed,
            description=description, approval=approval,
        )


    def _min_seed_units(self, private_event: bool = False) -> int:
        return self._contract(self.address, "TrancheMarket").functions.minSeed().call()

    def _precheck_create_host(self, host: str) -> None:
        resolver = self._contract(host, "TrancheMarket").functions.resolver().call()
        if is_zero_address(resolver):
            raise ValidationError("resolver unset", "The public market host has no resolver: no market can be created.")
        approved = self._contract(resolver, "MarketResolver").functions.approvedHost(host).call()
        if not approved:
            raise ValidationError("Not an approved host", f"The resolver {resolver} does not approve the public host {host}.")

    def _send_create(self, c: Dict[str, Any]) -> Dict[str, Any]:
        params = (
            c["name"], c["symbol"], c["end_time"], list(c["outcomes"]), list(c["tranche_bp"]), c["main"], c["frozen"],
            c["presale_tokens"], c["seed"],
        )
        sig = c["signature"]
        sig_bytes = bytes.fromhex(sig[2:] if sig.startswith("0x") else sig) if isinstance(sig, str) else bytes(sig)
        fn = self._contract(self.address, "TrancheMarket").functions.createMarket(params, c["deadline"], sig_bytes)
        return self._send(fn, self._contract_name, value=c["value"])

    def _precheck_buy_host(self, market: str, md: Dict[str, Any], buyer: str,
                           closed: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        """end_time first, as on chain; then every market, dated or open-ended, takes no bets while its resolver holds a
        proposal (a winning EARLY lifts the proposal and reopens betting until end_time)."""
        if closed:
            return closed
        resolver = self.get_resolver_of(market)
        if is_zero_address(resolver):
            return None
        proposed = self._contract(resolver, "MarketResolver").functions.hasProposal(market).call()
        if proposed:
            return {"reason": "Outcome proposed",
                    "message": f"A result has been proposed for market {market}: no bets while the proposal is live."}
        return None


    def set_whitelisted_wallet(self, market: str, wallets: Sequence[str], amount: HumanAmount,
                               tag: Optional[str] = "") -> Dict[str, Any]:
        """Whitelist wallets on the frozen market token. Creator only. The token skips wallets already listed (the
        creator always is, with cap 0): their cap is unchanged (listed in 'cap_unchanged'); to change a cap,
        ``remove_whitelist`` first. For bets the whitelist is a yes/no gate: the cap limits only direct buys of the
        market token.
        ``amount``: presale cap per wallet, int = raw 18-decimal tokens, str/float/Decimal = human. ``tag``: a label stored
        with the entries (default, also for None: ""); well-formed text (rule ``tag``), checked before any read.
        ``wallets``: a non-empty list/tuple ('no wallets provided' otherwise) of addresses (rule ``wallets``), checked
        before any read. Returns the tx plus {'listed', 'cap_unchanged', 'removed'} from the token's logs."""
        market = to_address(market, "market")
        wallets = self._wallet_list(wallets, "wallets")
        tag = self._whitelist_tag(tag)
        self._require_creator(market)
        if isinstance(amount, bool):
            raise ValidationError("amount", "amount must be a number, not a boolean.")
        fn = self._contract(self.address, "TrancheMarket").functions.setWhitelistedWallet(
            wallets, parse_amount(amount, 18), tag, market)
        return self._whitelist_result(self._send(fn, self._contract_name), market, wallets, True)

    def remove_whitelist(self, market: str, wallet: str) -> Dict[str, Any]:
        """Remove a wallet from the market token's whitelist. Creator only. 'removed' lists it once the token logged
        ``WhitelistRemoved``."""
        market = to_address(market, "market")
        wallet = to_address(wallet, "wallet")
        self._require_creator(market)
        fn = self._contract(self.address, "TrancheMarket").functions.removeWhitelist(wallet, market)
        return self._whitelist_result(self._send(fn, self._contract_name), market, [], False)


    def get_min_seed(self, main: Optional[str] = None) -> Amount:
        """Seed floor for a new public market on ``main`` (default: the default main), in its pair asset."""
        return self._min_seed_amount(main, False)

    def get_final_tie_mask(self, market: str) -> int:
        """Tie bits of the result (bit i: ranking entry i tied with entry i-1)."""
        return self._contract(self.address, "TrancheMarket").functions.finalTieMask(to_address(market, "market")).call()

    def get_final_winners_mask(self, market: str) -> int:
        """Bitmask of the outcomes tied for first (one bit for a single winner); 0 when unresolved or INVALID."""
        return self._contract(self.address, "TrancheMarket").functions.finalWinnersMask(to_address(market, "market")).call()

    def is_valid_ranking(self, market: str, ranking: Sequence[int], tie_mask: int = 0) -> bool:
        """Is (``ranking``, ``tie_mask``) a legal result for this market? Picks must be whole numbers 0-255
        ('rankingEntry') and ``tie_mask`` a uint256 int ('tieMask'): nothing is coerced."""
        market = to_address(market, "market")
        picks = normalize_ranking(ranking)
        validate_tie_mask(tie_mask)
        return self._contract(self.address, "TrancheMarket").functions.isValidRanking(market, picks, tie_mask).call()

    def get_resolver_of(self, market: str) -> str:
        """The resolver this market was created with: only it resolves the market."""
        return self._contract(self.address, "TrancheMarket").functions.marketResolver(to_address(market, "market")).call()

    def get_resolver(self) -> str:
        """The resolver new markets are registered with."""
        return self._contract(self.address, "TrancheMarket").functions.resolver().call()
