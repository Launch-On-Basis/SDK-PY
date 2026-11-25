"""The private prediction-market host (TypeScript SDK: src/modules/PrivateMarkets.ts).

Ranked / multi-winner markets resolved by the creator's voter panel (creator + up to 10 voters), plurality after a
voting window that opens with the first vote.

BASIS can void a private market in case of abuse. That only puts the market in refund mode: it can't pick a result.
"""

from typing import Any, Dict, List, Optional, Sequence, Union

from web3 import Web3

from ..config.addresses import is_zero_address
from ..core.errors import ValidationError
from ..core.amounts import pair_amount_of
from ..core.events import add_missing_event_warning
from ..core.ranking import KEY_INVALID, OUTCOME_INVALID, normalize_ranking, ranking_key, validate_ranking
from ..core.units import Amount, parse_amount, to_address, to_bool
from .markets_base import HumanAmount, MarketsBase

MAX_VOTERS = 11
ZERO_KEY = "0x" + "0" * 64


def _key_hex(k: Union[bytes, str]) -> str:
    """bytes32 as a lowercase 0x-prefixed hex string."""
    if isinstance(k, (bytes, bytearray)):
        return "0x" + bytes(k).hex()
    s = k.lower()
    return s if s.startswith("0x") else "0x" + s


def _eq(a: str, b: str) -> bool:
    return a.lower() == b.lower()


class PrivateMarketsModule(MarketsBase):
    """The private prediction-market host."""

    market_type = "private"
    _contract_name = "TrancheMarketPrivate"

    @property
    def _host_entry(self) -> Optional[str]:
        return self.client.addresses.get("tranchePrivate")

    @property
    def _phost(self):
        return self._contract(self.address, "TrancheMarketPrivate")


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
        private_event: bool = False,
    ) -> Dict[str, Any]:
        """Create a private market. ``private_event``: invite-only bettors; uses the private-event seed floor (see
        ``get_min_seed``); not bound by the approval. BASIS approves each new market:
        with gating on, an approval is requested from ``client.api.approve_market`` (market_type "private") unless
        ``approval`` is passed, verified locally, and its approved strings are sent verbatim. ``seed``: int = raw pair
        asset, str/float/Decimal = human; default the floor (may be 0 where the floor is 0). frozen=True needs
        presale_tokens > 0 (at most (2^256 - 1) / 1e18, 'presaleTokens'); the main's pair must be active ('pair not
        active') and, with a seed above 0, its trading open ('Trading is not enabled') and its pair asset not paused by
        its issuer ('pair asset paused'). Gating on and no ``approval``
        given, the approval route's own input rules are checked first (see the public host's create_market). An error
        raised after the approval that leaves it unused carries it as ``e.approval``: pass it back
        (``approval=e.approval``) to try again without another review (see the public host's create_market).
        Returns {'hash','receipt','result','warnings','market','approval','sent','seed_amount','seed_tokens',
        'shares_per_outcome','frozen'} (seed fields 0 for an unseeded market; see the public host's create_market)."""
        return self._create_market_internal(
            to_bool(private_event, "privateEvent", False), name=name, symbol=symbol, outcomes=outcomes,
            end_time=end_time, tranche_bp=tranche_bp,
            main=main, frozen=frozen, presale_tokens=presale_tokens, seed=seed, description=description, approval=approval,
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
        private_event: bool = False,
        *,
        image_url: Optional[str] = None,
        image_file: Optional[Any] = None,
        website: Optional[str] = None,
        telegram: Optional[str] = None,
        twitterx: Optional[str] = None,
        outcome_art: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """``create_market`` (the same arguments, in the same order), then upload the image (``image_url`` or
        ``image_file``, checked first) and register the metadata; the metadata arguments are keyword-only, as on the
        public host. ``outcome_art``: {<outcome label>: <picture id>} from the site's library, checked (and looked up
        there) before the approval request and the tx, and set once. A failed upload after the tx never raises (see
        'upload_error'; finish with ``retry_market_metadata``). Returns the create result plus {'image_url', 'metadata',
        'upload_error'}."""
        return self._create_market_with_metadata_internal(
            to_bool(private_event, "privateEvent", False), image_url=image_url, image_file=image_file, website=website,
            telegram=telegram, twitterx=twitterx, outcome_art=outcome_art, name=name, symbol=symbol, outcomes=outcomes,
            end_time=end_time, tranche_bp=tranche_bp, main=main, frozen=frozen, presale_tokens=presale_tokens, seed=seed,
            description=description, approval=approval,
        )

    def _min_seed_units(self, private_event: bool) -> int:
        fns = self._contract(self.address, "TrancheMarketPrivate").functions
        return fns.minSeedPrivate().call() if private_event else fns.minSeedPublic().call()

    def _precheck_create_host(self, host: str) -> None:
        return None

    def _send_create(self, c: Dict[str, Any]) -> Dict[str, Any]:
        p = (
            c["name"], c["symbol"], c["end_time"], list(c["outcomes"]), list(c["tranche_bp"]),
            Web3.to_checksum_address(c["main"]), c["private_event"], c["frozen"], c["presale_tokens"], c["seed"],
        )
        sig = c["signature"]
        sig_bytes = bytes.fromhex(sig[2:] if sig.startswith("0x") else sig) if isinstance(sig, str) else bytes(sig)
        fn = self._contract(self.address, "TrancheMarketPrivate").functions.createMarket(p, c["deadline"], sig_bytes)
        return self._send(fn, self._contract_name, value=c["value"])

    def _precheck_buy_host(self, market: str, md: Dict[str, Any], buyer: str,
                           closed: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        """As on chain: invite-only markets need the bettor invited (checked first), then a dated market closes at
        end_time and an open-ended one at the first vote. A proposal plays no part here: the panel resolves."""
        can_buy = self.can_user_buy(market, buyer) if md["is_private"] else True
        first_vote = self.get_first_vote_time(market) if md["end_time"] == 0 else 0
        if not can_buy:
            return {"reason": "not invited", "message": f"Market {market} is invite-only and {buyer} is not on its buyer list."}
        if closed:
            return closed
        if first_vote != 0:
            return {"reason": "voting started", "message": f"Voting has started on open-ended market {market}: betting is closed."}
        return None


    def vote(self, market: str, ranking: Sequence[int], tie_mask: int = 0) -> Dict[str, Any]:
        """Cast your ranked vote: ``ranking`` lists outcomes in finish order (every outcome in a slot below K = the
        number of payout slices, ties included); ``tie_mask`` bit i ties entry i with entry i-1. An empty ranking votes
        INVALID. Voters only, once, after ``end_time`` (open-ended: any time after creation). Checked first, before any
        read: ``ranking`` a list/tuple ('ranking') of whole numbers 0-255 ('rankingEntry', nothing truncated), and with
        picks ``tie_mask`` a uint256 int ('tieMask'; an empty ranking ignores it).
        Returns the tx plus {'key' (``RankedVote``; KEY_INVALID for an empty ranking; None when the event is missing)}."""
        market = to_address(market, "market")
        voter = self._account
        ranking = normalize_ranking(ranking)
        if ranking:
            ranking_key(ranking, tie_mask)
        md = self._require_market(market)
        fns = self._phost.functions
        is_voter = self.is_market_voter(market, voter)
        key = _key_hex(fns.voterKey(market, voter).call())
        now = self._chain_time()
        if not is_voter:
            raise ValidationError("not a voter", f"{voter} is not a voter on market {market}.")
        if key != ZERO_KEY:
            raise ValidationError("already voted", f"{voter} has already voted on market {market}.")
        if md["resolved"]:
            raise ValidationError("market resolved", f"Market {market} is already resolved.")
        start = md["creation_time"] if md["end_time"] == 0 else md["end_time"]
        if now < start:
            raise ValidationError("voting not open", f"Voting opens at {start} (chain time {now}).")
        if len(ranking) == 0:
            tie_mask = 0
        else:
            n = self.get_num_outcomes(market)
            bp = self.get_tranche_bp(market)
            validate_ranking(ranking, tie_mask, n, len(bp))
        tx = self._send(fns.vote(market, ranking, tie_mask), self._contract_name)
        ev = next((e for e in self._events(tx, self._phost, "RankedVote")
                   if _eq(e["marketToken"], market) and _eq(e["voter"], voter)), None)
        if ev is None:
            add_missing_event_warning(tx, self._contract_name, "RankedVote", ("key",))
        return {**tx, "key": _key_hex(ev["key"]) if ev else None}

    def vote_outcome(self, market: str, outcome: int) -> Dict[str, Any]:
        """Vote a single winner: ``ranking = [outcome]`` (valid only on a winner-takes-all market, one payout slice)."""
        return self.vote(market, [outcome], 0)

    def vote_invalid(self, market: str) -> Dict[str, Any]:
        """Vote INVALID (empty ranking): a win for INVALID refunds every bettor's cost basis."""
        return self.vote(market, [], 0)

    def finalize(self, market: str) -> Dict[str, Any]:
        """Tally the votes and resolve. Anyone may call it once the voting window (``get_voting_window``) has passed
        since the first vote.
        Returns the tx plus {'status' ('resolved' | 'invalid'), 'final_outcome', 'ranking', 'tie_mask', 'swept'} from the
        host's ``Resolved`` / ``RankedResolved`` / ``NoWinnerSwept`` logs ('swept': an ``Amount`` in the market's
        pair asset the protocol kept because every ranked outcome had no backers, 0 when none; the others None when
        ``Resolved`` is missing)."""
        market = to_address(market, "market")
        md = self._require_market(market)
        if md["resolved"]:
            raise ValidationError("market resolved", f"Market {market} is already resolved.")
        first = self.get_first_vote_time(market)
        window = self.get_voting_window()
        now = self._chain_time()
        if first == 0:
            raise ValidationError("no votes", f"Nobody has voted on market {market} yet.")
        if now < first + window:
            raise ValidationError("voting window open", f"The voting window closes at {first + window} (chain time {now}).")
        self._precheck_tally(market, md["creator"])
        pair_of = pair_amount_of(self.client.mains.get(md["main"]))
        tx = self._send(self._phost.functions.finalize(market), self._contract_name)

        def mine(name: str) -> list:
            return [e for e in self._events(tx, self._phost, name) if _eq(e["marketToken"], market)]

        resolved = next(iter(mine("Resolved")), None)
        ranked = next(iter(mine("RankedResolved")), None)
        swept = sum(e["usdcToTaxes"] for e in mine("NoWinnerSwept"))
        if resolved is None:
            add_missing_event_warning(tx, self._contract_name, "Resolved", ("status", "final_outcome", "ranking", "tie_mask"))
        fo = int(resolved["finalOutcome"]) if resolved else None
        invalid = fo == OUTCOME_INVALID
        return {
            **tx,
            "status": None if fo is None else ("invalid" if invalid else "resolved"),
            "final_outcome": fo,
            "ranking": None if fo is None else ([] if invalid else ([int(r) for r in ranked["ranking"]] if ranked else None)),
            "tie_mask": None if fo is None else (0 if invalid else (ranked["tieMask"] if ranked else None)),
            "swept": pair_of(swept),
        }


    def manage_voter(self, market: str, voter: str, status: bool) -> Dict[str, Any]:
        """Add (``status`` True) or remove a voter. Creator only; locked after the first vote; at most 11 voters
        including the creator. ``status`` must be a real bool (``ValidationError('status')`` otherwise, before any read).
        Returns the tx plus {'voter', 'status' (from ``MarketVoterSet``; None when the event is missing)}."""
        status = to_bool(status, "status")
        market = to_address(market, "market")
        voter = to_address(voter, "voter")
        md = self._require_creator(market)
        if is_zero_address(voter):
            raise ValidationError("voter", "voter must not be the zero address.")
        first = self.get_first_vote_time(market)
        voters = self.get_voters(market)
        is_voter = self.is_market_voter(market, voter)
        if first != 0:
            raise ValidationError("voters locked", "Voters cannot change after the first vote.")
        if status:
            if is_voter:
                raise ValidationError("already a voter", f"{voter} is already a voter.")
            if len(voters) >= MAX_VOTERS:
                raise ValidationError("too many voters", f"A market has at most {MAX_VOTERS} voters (the creator included).")
        else:
            if not is_voter:
                raise ValidationError("not a voter", f"{voter} is not a voter.")
            if _eq(voter, md["creator"]):
                raise ValidationError("creator", "The creator cannot be removed from the panel.")
        tx = self._send(self._phost.functions.manageVoter(market, voter, status), self._contract_name)
        ev = next((e for e in self._events(tx, self._phost, "MarketVoterSet")
                   if _eq(e["marketToken"], market) and _eq(e["voter"], voter)), None)
        if ev is None:
            add_missing_event_warning(tx, self._contract_name, "MarketVoterSet", ("status",))
        return {**tx, "voter": voter, "status": ev["status"] if ev else None}

    def toggle_private_event_buyers(self, market: str, buyers: Sequence[str], status: bool) -> Dict[str, Any]:
        """Allow (``status`` True) or disallow wallets to bet on an invite-only (``private_event``) market. Creator only.
        Allowing also whitelists them on the market token with an unlimited cap, but the token skips wallets already
        listed (their cap stays, e.g. a lower one set by ``manage_whitelist``: see 'cap_unchanged'). The creator is
        always allowed and skipped. ``status`` must be a real bool (``ValidationError('status')``); ``buyers`` a
        non-empty list/tuple ('no wallets provided' otherwise) of addresses (rule ``buyers``), checked before any read.
        Returns the tx plus {'buyers_set' (``PrivateBuyerSet``), 'listed', 'cap_unchanged', 'removed'}."""
        status = to_bool(status, "status")
        market = to_address(market, "market")
        buyers = self._wallet_list(buyers, "buyers")
        md = self._require_creator(market)
        if not md["is_private"]:
            raise ValidationError("not invite-only", f"Market {market} is not an invite-only (privateEvent) market.")
        if not status:
            self._require_whitelisted(market, [b for b in buyers if not _eq(b, md["creator"])])
        tx = self._send(self._phost.functions.togglePrivateEventBuyers(market, buyers, status), self._contract_name)
        requested = [b for b in buyers if not _eq(b, md["creator"])] if status else []
        return self._buyers_result(tx, market, requested, status)

    def manage_whitelist(self, market: str, wallets: Sequence[str], amount: HumanAmount, tag: Optional[str],
                         status: bool) -> Dict[str, Any]:
        """Whitelist (``status`` True, with a presale cap per wallet) or de-list wallets on the market token, and set
        their bet right. Creator only. The token skips wallets already listed, so their cap does not change
        ('cap_unchanged' in the result; the creator is always listed, with cap 0): to change a cap, de-list first, then
        list again. For bets the whitelist is a yes/no gate; the cap limits only direct buys of the market token.
        ``amount``: presale cap per wallet, int = raw 18-decimal tokens, str/float/Decimal = human (ignored when
        removing); ``tag`` a label stored with the entries ("" for none; None too), well-formed text (rule ``tag``),
        checked before any read; ``status`` a real bool (``ValidationError('status')``); ``wallets`` a non-empty
        list/tuple ('no wallets provided' otherwise) of addresses (rule ``wallets``), checked before any read. Returns
        the tx plus {'buyers_set', 'listed', 'cap_unchanged', 'removed'}."""
        status = to_bool(status, "status")
        market = to_address(market, "market")
        wallets = self._wallet_list(wallets, "wallets")
        tag = self._whitelist_tag(tag)
        self._require_creator(market)
        if not status:
            self._require_whitelisted(market, wallets)
        tx = self._send(
            self._phost.functions.manageWhitelist(market, wallets, parse_amount(amount, 18), tag, status),
            self._contract_name,
        )
        return self._buyers_result(tx, market, wallets if status else [], status)

    def _buyers_result(self, tx: Dict[str, Any], market: str, requested: Sequence[str], status: bool) -> Dict[str, Any]:
        """The ``PrivateBuyerSet`` logs of ``market`` with this status ('buyers_set'), plus the token's whitelist logs."""
        buyers_set = [e["user"] for e in self._events(tx, self._phost, "PrivateBuyerSet")
                      if _eq(e["marketToken"], market) and e["status"] == status]
        return {**self._whitelist_result(tx, market, requested, status), "buyers_set": buyers_set}

    def _require_whitelisted(self, market: str, wallets: List[str]) -> None:
        """De-listing: every wallet must be on the market token's whitelist, once (the token reverts
        "Wallet is not whitelisted")."""
        seen = set()
        for w in wallets:
            if w.lower() in seen:
                raise ValidationError("duplicate wallet", f"{w} is listed twice: the second removal would revert.")
            seen.add(w.lower())
        tok = self._contract(market, "FactoryToken").functions
        missing = [w for w in wallets if not tok.whitelisted(w).call()]
        if missing:
            raise ValidationError(
                "not whitelisted",
                f"Not on market token {market}'s whitelist, so they cannot be removed: {', '.join(missing)}.",
            )

    def _precheck_tally(self, market: str, creator: str) -> None:
        """The tally reverts with no reason when no panel voter has voted, and on a tie for the lead while the creator
        has not voted. Once the creator has voted, a tie goes to their choice if it is one of the tied leaders; if not,
        the market resolves INVALID."""
        voters = self.get_voters(market)
        fns = self._phost.functions
        keys = [_key_hex(fns.voterKey(market, v).call()) for v in voters]
        counts: Dict[str, int] = {}
        for k in keys:
            if k != ZERO_KEY:
                counts[k] = counts.get(k, 0) + 1
        if not counts:
            raise ValidationError("no votes", f"No panel voter has voted on market {market} yet.")
        top = max(counts.values())
        leaders = sum(1 for c in counts.values() if c == top)
        idx = next((i for i, v in enumerate(voters) if _eq(v, creator)), -1)
        creator_key = keys[idx] if idx >= 0 else ZERO_KEY
        if leaders > 1 and creator_key == ZERO_KEY:
            raise ValidationError(
                "creator must break the tie",
                f"The vote on market {market} is tied and the creator ({creator}) has not voted: only their vote can break it.",
            )


    def get_min_seed(self, main: Optional[str] = None, private_event: bool = False) -> Amount:
        """Seed floor for a new private market on ``main`` (default: the default main), in its pair asset: the
        invite-only floor with ``private_event``, else the floor of other markets. May be 0."""
        return self._min_seed_amount(main, to_bool(private_event, "privateEvent", False))

    def get_voters(self, market: str) -> List[str]:
        return list(self._phost.functions.getVoters(to_address(market, "market")).call())

    def is_market_voter(self, market: str, voter: str) -> bool:
        market = to_address(market, "market")
        voter = to_address(voter, "voter")
        return self._phost.functions.isMarketVoter(market, voter).call()

    def get_first_vote_time(self, market: str) -> int:
        """Unix seconds of the first vote; 0 = nobody voted."""
        return self._phost.functions.firstVoteTime(to_address(market, "market")).call()

    def get_voter_key(self, market: str, voter: str) -> Dict[str, Any]:
        """A voter's vote: {'key', 'voted', 'invalid', 'ranking', 'tie_mask'} (zero key = not voted; INVALID key =
        voted INVALID). ``key`` is a 0x-prefixed bytes32 hex string."""
        market = to_address(market, "market")
        voter = to_address(voter, "voter")
        key = _key_hex(self._phost.functions.voterKey(market, voter).call())
        if key == ZERO_KEY:
            return {"key": key, "voted": False, "invalid": False, "ranking": [], "tie_mask": 0}
        if key == KEY_INVALID.lower():
            return {"key": key, "voted": True, "invalid": True, "ranking": [], "tie_mask": 0}
        return {"key": key, "voted": True, "invalid": False, **self.get_ranking(market, key)}

    def get_final_key(self, market: str) -> str:
        """Key of the winning ranking (zero when unresolved or INVALID), as a 0x-prefixed bytes32 hex string."""
        return _key_hex(self._phost.functions.finalKey(to_address(market, "market")).call())

    def get_ranking(self, market: str, key: Union[str, bytes]) -> Dict[str, Any]:
        """The ranking and tie bits stored under ``key`` (a ``voterKey`` or the ``finalKey``):
        {'ranking': [...], 'tie_mask': int}."""
        market = to_address(market, "market")
        k = bytes.fromhex(_key_hex(key)[2:])
        ranking, tie_mask = self._phost.functions.getRanking(market, k).call()
        return {"ranking": [int(r) for r in ranking], "tie_mask": tie_mask}

    def get_final_tie_mask(self, market: str) -> int:
        """Tie bits of the result, via ``get_ranking(market, final_key)``."""
        market = to_address(market, "market")
        key = self.get_final_key(market)
        if key == ZERO_KEY:
            return 0
        return self.get_ranking(market, key)["tie_mask"]

    def can_user_buy(self, market: str, user: str) -> bool:
        """May ``user`` bet on this invite-only market? The creator is always allowed (set at
        creation, with no ``PrivateBuyerSet`` event). The whole invite list is in the API:
        ``client.api.get_market(market)["invited"]`` / ``["invitedCount"]`` (built from ``PrivateBuyerSet``, so it never
        lists the creator); a wallet's voter and buyer roles: ``client.api.get_wallet_roles(wallet)["privateMarkets"]``."""
        market = to_address(market, "market")
        user = to_address(user, "user")
        return self._phost.functions.userCanBuyEvent(market, user).call()

    def get_voter_panel(self, market: str) -> Dict[str, Any]:
        """The voter panel with every voter's vote (key, ranking, INVALID), the first vote time, the window and its
        end, and the final key. Reads only. The invite list is in the API (``client.api.get_market(market)["invited"]``;
        the creator is always allowed but not listed there); ``can_user_buy`` answers one wallet; which panels a wallet
        sits on: ``client.api.get_wallet_roles(wallet)["privateMarkets"]``. Returns {'market', 'creator', 'voters': [{'voter', 'is_creator', 'key', 'voted', 'invalid',
        'ranking', 'tie_mask'}], 'voted_count', 'first_vote_time', 'voting_window', 'voting_ends_at' (0 before the
        first vote), 'voting_over' (chain time >= voting_ends_at: finalize is open unless the tally needs the
        creator's tie-break), 'resolved', 'final_key'}."""
        market = to_address(market, "market")
        md = self._require_market(market)
        voters = self.get_voters(market)
        first = self.get_first_vote_time(market)
        window = self.get_voting_window()
        final_key = self.get_final_key(market)
        now = self._chain_time()
        panel = [{"voter": v, "is_creator": _eq(v, md["creator"]), **self.get_voter_key(market, v)} for v in voters]
        ends = 0 if first == 0 else first + window
        return {
            "market": market, "creator": md["creator"], "voters": panel,
            "voted_count": sum(1 for v in panel if v["voted"]),
            "first_vote_time": first, "voting_window": window, "voting_ends_at": ends,
            "voting_over": first != 0 and now >= ends, "resolved": md["resolved"], "final_key": final_key,
        }

    def get_voting_window(self) -> int:
        """Minimum voting time in seconds (15 min): finalize opens this long after the first vote."""
        return self._phost.functions.VOTING_WINDOW().call()


__all__ = ["PrivateMarketsModule", "MAX_VOTERS"]
