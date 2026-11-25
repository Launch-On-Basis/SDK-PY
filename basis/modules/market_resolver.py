"""The market resolver: propose / dispute / vote / veto / finalize for ranked markets, plus every view.
TypeScript SDK: src/modules/MarketResolver.ts.

Each market is resolved by the resolver it was created with; the module looks it up per market. Bonds are posted in
the resolver's ``BOND_TOKEN`` (the exact amount is approved per action).

Keys are lowercase 0x-hex strings (see ``core/ranking.py``). Results are dicts with snake_case keys:

- constants: DISPUTE_PERIOD, PROPOSAL_PERIOD, VETO_PERIOD, MIN_QUORUM, EARLY_FAST_PERIOD (seconds / counts),
  PROPOSAL_BOND, BOND_CAP, BOND_MAX, VETO_FLOOR, TRUSTED_BOND (raw BOND_TOKEN), BOND_TOKEN, BOND_UNIT (one whole
  BOND_TOKEN, raw)
- decoded key: key, kind ('outcome' | 'early' | 'invalid' | 'unresolved' | 'ranking'), + outcome / ranking, tie_mask
- dispute: proposer, disputer, vetoer, original_key, disputed_key, veto_key (decoded or None), proposer_bond,
  disputer_bond, veto_bond, dispute_start_time
- tally: leader (decoded or None), leader_count, total_votes, tie
- ranked writes: {'hash','receipt','result','warnings','key','bond'} plus 'bond_posted' (propose / dispute / veto, from
  ``Proposal`` / ``DisputeStarted`` / ``Vetoed``) or 'round' (votes, from ``Vote``); None when the event is missing.
  'bond' (0 for votes) and 'bond_posted' are ``Amount``s in ``BOND_TOKEN``
- finalize writes: {..., 'outcome' ('resolved' | 'reopened' | 'earlyReset' | None), 'key', 'round', 'early_resets',
  'payouts' ([{'to', 'amount', 'redirected'}]), 'seized', 'swept'}; payout 'amount' and 'seized' (0 when none) are
  ``Amount``s in ``BOND_TOKEN``. 'seized' holds what went to insurance: what the losing sides lost (see ``dispute``,
  ``dispute_early``, ``veto`` and ``finalize_market``). 'swept' (``Amount`` in the market's pair asset, 0 when none) is
  the pot the protocol kept because no ranked outcome had a bettor (``NoWinnerSwept``, only possible on a market with no
  seed): nobody can redeem it
"""

from typing import Any, Callable, Dict, Optional, Sequence

from eth_abi import encode as abi_encode
from web3 import Web3

from ..config.addresses import ZERO_ADDRESS, is_zero_address
from ..core.base import BaseModule
from ..core.errors import BasisRpcError, ValidationError
from ..core.amounts import AmountOf, amount_of, pair_amount_of
from ..core.events import add_missing_event_warning
from ..core.loans import refuse_paused_asset
from ..core.ranking import (KEY_EARLY, KEY_INVALID, decode_ranking_key, normalize_ranking, ranking_key, validate_ranking,
                            validate_tie_mask)
from ..core.revert import classify_error
from ..core.units import to_address, to_uint

RULES_RULE = "Rules: not qualified"
PROPOSE_DELAY = 15 * 60

PROPOSAL_RULES_ABI = [{
    "type": "function", "name": "qualifiesForProposal", "stateMutability": "view",
    "inputs": [{"name": "proposer", "type": "address"}, {"name": "marketToken", "type": "address"}],
    "outputs": [{"name": "", "type": "bool"}],
}]

_CONSTANT_NAMES = ("DISPUTE_PERIOD", "PROPOSAL_PERIOD", "VETO_PERIOD", "PROPOSAL_BOND", "MIN_QUORUM",
                   "EARLY_FAST_PERIOD", "BOND_CAP", "BOND_MAX", "VETO_FLOOR", "TRUSTED_BOND")
_INT_CONSTANTS = ("DISPUTE_PERIOD", "PROPOSAL_PERIOD", "VETO_PERIOD", "MIN_QUORUM", "EARLY_FAST_PERIOD")

_FAIL_MESSAGES = {
    "Not a ranked market": "This market is not registered on a ranked host with its resolver.",
    "unknown market": "The market does not exist on its host.",
    "Invalid market": "The market does not exist on its host.",
    "noProposal": "There is no proposal to finalize.",
    "disputed": "The proposal is disputed: use finalize_market after the vote.",
    "notInDispute": "The market is not in dispute.",
    "inVeto": "The market was vetoed: only BASIS can resolve it now.",
}


def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


def _eq(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def _hex32(v: Any) -> str:
    """A bytes32 from web3 (bytes/HexBytes) or a hex string, as lowercase 0x-hex."""
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()
    s = v.lower()
    return s if s.startswith("0x") else "0x" + s


def _picks(ranking: Sequence[Any]) -> list:
    """The picks as ints, checked strictly first (``rankingEntry``: whole numbers 0-255, nothing truncated)."""
    return normalize_ranking(ranking)


def _dispute_bond_for(proposer_bond: int, c: Dict[str, Any], trusted: bool) -> int:
    """The dispute bond of a live proposal (see ``get_dispute_bond``)."""
    if trusted:
        return c["TRUSTED_BOND"]
    return min(max(proposer_bond, c["PROPOSAL_BOND"]), c["BOND_CAP"])


def _veto_bond_for(proposer_bond: int, c: Dict[str, Any]) -> int:
    """The veto bond of a live proposal (see ``get_veto_bond``)."""
    return min(max(proposer_bond, c["VETO_FLOOR"]), c["BOND_CAP"])


class MarketResolverModule(BaseModule):
    """Ranked resolution of prediction markets: writes with pre-checks and bonds, every view, a status helper."""

    def __init__(self, client):
        super().__init__(client)
        self._resolver_cache: Dict[str, str] = {}
        self._host_cache: Dict[str, str] = {}
        self._bond_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def deployed(self) -> bool:
        """Whether the default resolver address is set on this chain."""
        return not is_zero_address(self.client.addresses["resolver"])

    @property
    def _default_resolver(self) -> str:
        return self._require(self.client.addresses["resolver"], "MarketResolver")

    def _r(self, address: str):
        return self._contract(address, "MarketResolver").functions

    @property
    def _signer(self) -> Optional[str]:
        return self.client.account.address if self.client.account else None


    def resolver_of(self, market: str) -> str:
        """The resolver ``market`` was registered with, looked up on every known ranked host (``addresses["tranche"]``,
        then ``addresses["extraRankedHost"]`` when set), else the default resolver (address book). Cached per market
        once found on a host."""
        market = to_address(market, "market")
        hit = self._resolver_cache.get(market.lower())
        if hit:
            return hit
        for host, extra in self._ranked_hosts():
            try:
                r = self._contract(host, "TrancheMarket").functions.marketResolver(_cs(market)).call()
            except Exception:  # noqa: BLE001
                if not extra:
                    raise
                continue
            if not is_zero_address(r):
                r = _cs(r)
                self._resolver_cache[market.lower()] = r
                return r
        return self._default_resolver

    def _ranked_hosts(self) -> list:
        """The ranked hosts the SDK knows, non-zero ones, as (address, extra): ``addresses["tranche"]``, then the
        optional ``addresses["extraRankedHost"]``."""
        a = self.client.addresses
        out = []
        tranche, extra = a.get("tranche"), a.get("extraRankedHost")
        if not is_zero_address(tranche):
            out.append((_cs(tranche), False))
        if not is_zero_address(extra) and not (out and _eq(extra, tranche)):
            out.append((_cs(extra), True))
        return out

    def _resolver_for(self, market: Optional[str] = None) -> str:
        return self._default_resolver if market is None else self.resolver_of(market)

    def host_of(self, market: str) -> str:
        """The ranked host ``market`` is registered on (``hostOf``; zero = not a ranked market)."""
        market = to_address(market, "market")
        hit = self._host_cache.get(market.lower())
        if hit:
            return hit
        resolver = self.resolver_of(market)
        h = self._r(resolver).hostOf(_cs(market)).call()
        if not is_zero_address(h):
            h = _cs(h)
            self._host_cache[market.lower()] = h
        return h

    def is_approved_host(self, host: str, market: Optional[str] = None) -> bool:
        """Whether ``host`` may register markets on the resolver (of ``market``, else the default)."""
        host = to_address(host, "host")
        return self._r(self._resolver_for(market)).approvedHost(host).call()

    def _bond_info(self, resolver: str) -> Dict[str, Any]:
        k = resolver.lower()
        hit = self._bond_cache.get(k)
        if hit is None:
            fns = self._r(resolver)
            hit = {"token": fns.BOND_TOKEN().call(), "unit": fns.BOND_UNIT().call()}
            self._bond_cache[k] = hit
        return hit


    def get_constants(self, market: Optional[str] = None) -> Dict[str, Any]:
        """Resolver settings of ``market``'s resolver (else the default). Read live; never hardcode them."""
        return self._read_constants(self._resolver_for(market))

    def _read_constants(self, address: str) -> Dict[str, Any]:
        fns = self._r(address)
        out: Dict[str, Any] = {}
        for name in _CONSTANT_NAMES:
            v = getattr(fns, name)().call()
            out[name] = int(v)
        bond = self._bond_info(address)
        out["BOND_TOKEN"] = bond["token"]
        out["BOND_UNIT"] = bond["unit"]
        return out


    def get_ranking(self, market: str, key: str) -> Dict[str, Any]:
        """The ranking behind a stored key (``[]``, 0 when the key was never seen on this market).
        Returns ``{'ranking', 'tie_mask'}``."""
        market = to_address(market, "market")
        ranking, tie_mask = self._r(self.resolver_of(market)).getRanking(market, _hex32(key)).call()
        return {"ranking": [int(p) for p in ranking], "tie_mask": tie_mask}

    def ranking_key_on_chain(self, ranking: Sequence[int], tie_mask: int = 0, market: Optional[str] = None) -> str:
        """The resolver's own key for a ranking, to cross-check ``ranking_key()``. ``[]`` -> KEY_INVALID. Checked first,
        nothing coerced: picks are whole numbers 0-255 (``normalize_ranking``, 'rankingEntry'), ``tie_mask`` a uint256
        int ('tieMask')."""
        picks = _picks(ranking)
        validate_tie_mask(tie_mask)
        if len(picks) == 0:
            return KEY_INVALID
        base = bytes(self._r(self._resolver_for(market)).rankingKey(picks).call())
        if tie_mask == 0:
            return _hex32(base)
        return _hex32(bytes(Web3.keccak(abi_encode(["bytes32", "uint256"], [base, tie_mask]))))

    def decode_key(self, market: str, key: Any) -> Optional[Dict[str, Any]]:
        """Decode a key; ranking keys are looked up with ``get_ranking``. bytes32(0) (unset) -> None."""
        market = to_address(market, "market")
        k = _hex32(key)
        if int(k, 16) == 0:
            return None
        d = decode_ranking_key(k)
        out: Dict[str, Any] = {"key": k, **d}
        if d["kind"] == "ranking":
            r = self.get_ranking(market, k)
            if r["ranking"]:
                out["ranking"] = r["ranking"]
                out["tie_mask"] = r["tie_mask"]
        return out


    def get_dispute_data(self, market: str) -> Optional[Dict[str, Any]]:
        """The dispute record with decoded keys, or None when there is no live proposal."""
        market = to_address(market, "market")
        raw = self._r(self.resolver_of(market)).disputes(market).call()
        return self._decode_dispute(market, raw)

    def _decode_dispute(self, market: str, raw: Sequence[Any]) -> Optional[Dict[str, Any]]:
        proposer, disputer, vetoer, ok, dk, vk, pb, db, vb, start = raw
        if is_zero_address(proposer):
            return None
        return {
            "proposer": proposer, "disputer": disputer, "vetoer": vetoer,
            "original_key": self.decode_key(market, ok),
            "disputed_key": self.decode_key(market, dk),
            "veto_key": self.decode_key(market, vk),
            "proposer_bond": pb, "disputer_bond": db, "veto_bond": vb, "dispute_start_time": int(start),
        }

    def is_resolved(self, market: str) -> bool:
        """Whether the market is resolved."""
        market = to_address(market, "market")
        return self._r(self.resolver_of(market)).resolved(market).call()

    def get_final_outcome(self, market: str) -> int:
        """Host ``finalOutcome``: ranking[0] on a ranked win, 254 INVALID, 255 UNRESOLVED."""
        market = to_address(market, "market")
        return int(self._r(self.resolver_of(market)).finalOutcome(market).call())

    def has_proposal(self, market: str) -> bool:
        """True while a proposal is live (proposal, dispute, voting or veto). No bets then, on dated and open-ended
        markets."""
        market = to_address(market, "market")
        return self._r(self.resolver_of(market)).hasProposal(market).call()

    def is_invalid_live(self, market: str) -> bool:
        """True while INVALID is the live proposal, or a panel voter has voted INVALID in the current round. No orders
        fill on the public host then. A dispute or veto naming INVALID does not count on its own, and a reopened round
        starts clean."""
        market = to_address(market, "market")
        return self._r(self.resolver_of(market)).invalidLive(market).call()

    def is_in_dispute(self, market: str) -> bool:
        market = to_address(market, "market")
        return self._r(self.resolver_of(market)).inDispute(market).call()

    def is_in_veto(self, market: str) -> bool:
        market = to_address(market, "market")
        return self._r(self.resolver_of(market)).inVeto(market).call()

    def get_current_round(self, market: str) -> int:
        """The voting round now current (bumps on a reopen and after EARLY)."""
        market = to_address(market, "market")
        return int(self._r(self.resolver_of(market)).currentRound(market).call())

    def get_vote_count(self, market: str, round: int, key: str) -> int:
        """Votes for ``key`` in ``round`` (use ``ranking_key()`` / KEY_EARLY / KEY_INVALID for the key)."""
        market = to_address(market, "market")
        return int(self._r(self.resolver_of(market)).voteCount(market, to_uint(round, "round"), _hex32(key)).call())

    def get_tally(self, market: str, round: Optional[int] = None) -> Dict[str, Any]:
        """Running tally of ``round`` (default: the current round)."""
        market = to_address(market, "market")
        fns = self._r(self.resolver_of(market))
        r = fns.currentRound(market).call() if round is None else to_uint(round, "round")
        leader, leader_count, total_votes, tie = fns.tallies(market, r).call()
        return {"leader": self.decode_key(market, leader), "leader_count": int(leader_count),
                "total_votes": int(total_votes), "tie": tie}

    def has_voted(self, market: str, round: int, voter: str) -> bool:
        market = to_address(market, "market")
        voter = to_address(voter, "voter")
        return self._r(self.resolver_of(market)).hasVoted(market, to_uint(round, "round"), voter).call()

    def get_voter_choice(self, market: str, round: int, voter: str) -> Optional[Dict[str, Any]]:
        """What ``voter`` voted in ``round``, decoded; None when they did not vote."""
        market = to_address(market, "market")
        voter = to_address(voter, "voter")
        key = self._r(self.resolver_of(market)).voterChoice(market, to_uint(round, "round"), voter).call()
        return self.decode_key(market, key)

    def is_voter(self, addr: str, market: Optional[str] = None) -> bool:
        """Whether ``addr`` is on the resolver's voter panel."""
        addr = to_address(addr, "addr")
        return self._r(self._resolver_for(market)).authorizedVoter(addr).call()

    def get_voter_count(self, market: Optional[str] = None) -> int:
        """How many voters are on the panel of ``market``'s resolver (else the default resolver) now. A dispute resolves
        only with at least ``MIN_QUORUM`` votes (each voter votes once a round), so a panel smaller than that reopens
        every round."""
        return int(self._r(self._resolver_for(market)).voterCount().call())

    def is_trusted_proposer(self, addr: str, market: Optional[str] = None) -> bool:
        """Trusted proposers post the flat ``TRUSTED_BOND`` to propose or to dispute, skip the optional proposal rule, and
        may propose INVALID on a dated market before its end_time (a cancelled event, a broken question)."""
        addr = to_address(addr, "addr")
        return self._r(self._resolver_for(market)).trustedProposer(addr).call()

    def get_early_resets(self, market: str) -> int:
        """EARLY cycles on ``market`` (each doubles the next proposal bond, up to ``BOND_MAX``)."""
        market = to_address(market, "market")
        return int(self._r(self.resolver_of(market)).earlyResets(market).call())

    def get_required_bond(self, market: str, proposer: Optional[str] = None) -> int:
        """Proposal bond for ``proposer`` (default: the signer, else the zero address) now, raw ``BOND_TOKEN``."""
        market = to_address(market, "market")
        who = (self._signer or ZERO_ADDRESS) if proposer is None else to_address(proposer, "proposer")
        return self._r(self.resolver_of(market)).requiredBond(market, who).call()

    def get_dispute_bond(self, market: str, disputer: Optional[str] = None) -> Optional[int]:
        """What a dispute of the live proposal costs ``disputer`` (default: the signer, else the zero address), raw
        ``BOND_TOKEN``: about the proposal's bond, within the floor and cap of ``get_constants``; a trusted proposer
        posts ``TRUSTED_BOND``. None with no live proposal."""
        market = to_address(market, "market")
        who = (self._signer or ZERO_ADDRESS) if disputer is None else to_address(disputer, "disputer")
        address = self.resolver_of(market)
        fns = self._r(address)
        dd = fns.disputes(market).call()
        if is_zero_address(dd[0]):
            return None
        return _dispute_bond_for(dd[6], self._read_constants(address), fns.trustedProposer(who).call())

    def get_veto_bond(self, market: str) -> Optional[int]:
        """What a veto costs, raw ``BOND_TOKEN``: about the proposal's bond, within the floor and cap of
        ``get_constants``, the same for every wallet. None with no live proposal."""
        market = to_address(market, "market")
        address = self.resolver_of(market)
        dd = self._r(address).disputes(market).call()
        if is_zero_address(dd[0]):
            return None
        return _veto_bond_for(dd[6], self._read_constants(address))


    def get_status(self, market: str, user: Optional[str] = None) -> Dict[str, Any]:
        """One dict with the whole resolution state of ``market``: flags, the dispute record (keys decoded), the
        current tally, deadlines and, for ``user`` (default: the signer), which actions are possible now.

        Keys: market, resolver, host, now, has_proposal, invalid_live (INVALID is the live proposal, or a panel voter
        voted INVALID in the current round: no order fills), in_dispute, in_veto, is_resolved, final_outcome,
        current_round, dispute, tally, voter_count (voters on the panel now: a dispute resolves only with at least
        MIN_QUORUM votes), early_resets, constants, required_bond, dispute_bond and veto_bond (what a
        dispute by the user or a veto would post now, None with no live proposal), deadlines {propose_from_creator,
        propose_from_others (a trusted proposer may propose INVALID before both), + dispute_until,
        finalize_uncontested_from (undisputed proposal) / vote_until, veto_from, finalize_from, early_fast_from (in
        dispute)}, and with a user: user {address, is_voter, is_trusted_proposer, has_voted, actions {propose (a
        ranking), propose_invalid (as propose, except that a trusted proposer may do it before the propose window
        opens), dispute, finalize_uncontested, vote, veto, finalize_market: {allowed, reason?}}}.
        Key-dependent rules (dispute/veto key) are not checked."""
        market = to_address(market, "market")
        who = self._signer if user is None else to_address(user, "user")
        s = self._snapshot(market)
        r = s["resolver"]
        fns = self._r(r)
        has_proposal = fns.hasProposal(market).call()
        invalid_live = fns.invalidLive(market).call()
        dd = s["dd"]
        raw_dispute = self._decode_dispute(market, [
            dd["proposer"], dd["disputer"], dd["vetoer"], dd["original_key"], dd["disputed_key"], dd["veto_key"],
            dd["proposer_bond"], dd["disputer_bond"], dd["veto_bond"], dd["dispute_start_time"]])
        tally_leader = self.decode_key(market, s["tally"]["leader"])
        voter_count = fns.voterCount().call()
        early_resets = fns.earlyResets(market).call()
        required_bond = fns.requiredBond(market, who or ZERO_ADDRESS).call()
        try:
            final_outcome = fns.finalOutcome(market).call()
        except Exception:  # noqa: BLE001
            final_outcome = 255
        user_info = self._user_flags(r, market, s["round"], who) if who else None

        start = int(dd["dispute_start_time"])
        end = int(s["end_time"])
        c = s["c"]
        deadlines: Dict[str, int] = {
            "propose_from_creator": end,
            "propose_from_others": 0 if end == 0 else end + PROPOSE_DELAY,
        }
        if has_proposal and not s["in_dispute"] and is_zero_address(dd["disputer"]):
            deadlines["dispute_until"] = start + c["PROPOSAL_PERIOD"]
            deadlines["finalize_uncontested_from"] = start + c["PROPOSAL_PERIOD"]
        if s["in_dispute"]:
            deadlines["vote_until"] = start + c["DISPUTE_PERIOD"]
            deadlines["veto_from"] = start + c["DISPUTE_PERIOD"]
            deadlines["finalize_from"] = start + c["DISPUTE_PERIOD"] + c["VETO_PERIOD"]
            deadlines["early_fast_from"] = start + c["EARLY_FAST_PERIOD"]

        live = not is_zero_address(dd["proposer"])
        status: Dict[str, Any] = {
            "market": market, "resolver": r, "host": s["host"], "now": int(s["now"]),
            "has_proposal": has_proposal, "invalid_live": invalid_live, "in_dispute": s["in_dispute"],
            "in_veto": s["in_veto"],
            "is_resolved": s["resolved"], "final_outcome": int(final_outcome), "current_round": int(s["round"]),
            "dispute": raw_dispute,
            "tally": {"leader": tally_leader, "leader_count": int(s["tally"]["leader_count"]),
                      "total_votes": int(s["tally"]["total_votes"]), "tie": s["tally"]["tie"]},
            "voter_count": int(voter_count), "early_resets": int(early_resets), "constants": c,
            "required_bond": required_bond,
            "dispute_bond": _dispute_bond_for(dd["proposer_bond"], c, bool(user_info and user_info["is_trusted_proposer"])) if live else None,
            "veto_bond": _veto_bond_for(dd["proposer_bond"], c) if live else None,
            "deadlines": deadlines,
        }
        if who and user_info:
            def act(reason: Optional[str]) -> Dict[str, Any]:
                return {"allowed": False, "reason": reason} if reason else {"allowed": True}

            trusted = user_info["is_trusted_proposer"]
            propose = self._check_propose(s, who, None, trusted)
            propose_invalid = self._check_propose(s, who, KEY_INVALID, trusted)
            rule = None
            if (propose is None or propose_invalid is None) and self._proposal_rule_refusal(s, market, who, trusted):
                rule = RULES_RULE
            status["user"] = {
                "address": who, **user_info,
                "actions": {
                    "propose": act(propose or rule),
                    "propose_invalid": act(propose_invalid or rule),
                    "dispute": act(self._check_dispute(s, who, None)),
                    "finalize_uncontested": act(self._check_finalize_uncontested(s)),
                    "vote": act(self._check_vote(s, user_info["is_voter"], user_info["has_voted"])),
                    "veto": act(self._check_veto(s, who, None)),
                    "finalize_market": act(self._check_finalize_market(s)),
                },
            }
        return status

    def _user_flags(self, resolver: str, market: str, round: int, who: str) -> Dict[str, bool]:
        fns = self._r(resolver)
        return {
            "is_voter": fns.authorizedVoter(who).call(),
            "is_trusted_proposer": fns.trustedProposer(who).call(),
            "has_voted": fns.hasVoted(market, round, who).call(),
        }

    def _snapshot(self, market: str) -> Dict[str, Any]:
        """Everything the pre-checks need, read fresh. ``now`` is the latest block time: a rule that opens at a time
        (``block.timestamp >= X``) is checked against ``now`` itself (the next block may carry the same time, so
        ``now + 1`` would pass one second early and let a bond approval mine before a main tx that reverts); a rule that
        closes at a time (``block.timestamp < Y`` / ``<= Y``) is checked from ``now + 1`` (the earliest a tx can be
        mined)."""
        market = _cs(market)
        resolver = self.resolver_of(market)
        host = self.host_of(market)
        fns = self._r(resolver)
        md = None if is_zero_address(host) else self._contract(host, "TrancheMarket").functions.getMarketData(market).call()
        dd = fns.disputes(market).call()
        in_dispute = fns.inDispute(market).call()
        in_veto = fns.inVeto(market).call()
        round_ = fns.currentRound(market).call()
        c = self._read_constants(resolver)
        now = self.web3.eth.get_block("latest")["timestamp"]
        leader, leader_count, total_votes, tie = fns.tallies(market, round_).call()
        if md is not None:
            resolved = md[8]
        else:
            try:
                resolved = fns.resolved(market).call()
            except Exception:  # noqa: BLE001
                resolved = False
        return {
            "resolver": resolver, "host": host,
            "market_exists": _eq(md[0], market) if md is not None else False,
            "resolved": resolved,
            "creator": md[1] if md is not None else ZERO_ADDRESS,
            "end_time": md[6] if md is not None else 0,
            "main": md[2] if md is not None else ZERO_ADDRESS,
            "dd": {
                "proposer": dd[0], "disputer": dd[1], "vetoer": dd[2],
                "original_key": _hex32(dd[3]), "disputed_key": _hex32(dd[4]), "veto_key": _hex32(dd[5]),
                "proposer_bond": dd[6], "disputer_bond": dd[7], "veto_bond": dd[8], "dispute_start_time": dd[9],
            },
            "in_dispute": in_dispute, "in_veto": in_veto, "round": round_,
            "tally": {"leader": _hex32(leader), "leader_count": leader_count, "total_votes": total_votes, "tie": tie},
            "c": c,
            "now": now,
        }


    @staticmethod
    def _check_market(s: Dict[str, Any]) -> Optional[str]:
        if is_zero_address(s["host"]):
            return "Not a ranked market"
        if not s["market_exists"]:
            return "unknown market"
        if s["resolved"]:
            return "Already resolved"
        return None

    def _check_propose(self, s: Dict[str, Any], me: str, key: Optional[str], trusted: bool) -> Optional[str]:
        """``key``: the key to propose, None for any ranking. A dated market takes proposals from end_time (its creator)
        or end_time + 15 min (everyone else); a trusted proposer may propose INVALID before that."""
        m = self._check_market(s)
        if m:
            return m
        early_invalid = trusted and key is not None and _eq(key, KEY_INVALID)
        if (s["end_time"] != 0 and not early_invalid
                and s["now"] < s["end_time"] + (0 if _eq(s["creator"], me) else PROPOSE_DELAY)):
            return "Too early to propose"
        if not is_zero_address(s["dd"]["proposer"]):
            return "Already proposed"
        return None

    def _proposal_rule_refusal(self, s: Dict[str, Any], market: str, who: str, trusted: bool) -> Optional[str]:
        """The resolver's optional proposal rule, while it is on: None when it lets ``who`` propose, else why not.
        Trusted proposers (``trusted``) skip it. A rule check that reverts counts as a refusal; an RPC failure raises
        ``BasisRpcError``."""
        if trusted:
            return None
        fns = self._r(s["resolver"])
        rules = fns.proposalRules().call()
        if not fns.proposalRulesOn().call() or is_zero_address(rules):
            return None
        try:
            ok = self.web3.eth.contract(address=_cs(rules), abi=PROPOSAL_RULES_ABI).functions.qualifiesForProposal(who, market).call()
        except Exception as e:  # noqa: BLE001
            err = classify_error(e, None, "qualifiesForProposal")
            if isinstance(err, BasisRpcError):
                raise err
            return f"the proposal rule refused {who}: {str(err).removeprefix('[basis-sdk] ')}"
        return None if ok else f"{who} does not meet the proposal rule on this market. It can still dispute or veto"

    def _check_dispute(self, s: Dict[str, Any], me: str, key: Optional[str]) -> Optional[str]:
        m = self._check_market(s)
        if m:
            return m
        dd = s["dd"]
        if is_zero_address(dd["proposer"]):
            return "No proposal to dispute"
        if s["in_dispute"]:
            return "Already in dispute"
        if s["now"] + 1 >= dd["dispute_start_time"] + s["c"]["PROPOSAL_PERIOD"]:
            return "Dispute period over"
        if key is not None and _eq(key, dd["original_key"]):
            return "Must be different"
        if _eq(me, dd["proposer"]):
            return "Proposer cannot dispute self"
        return None

    def _check_finalize_uncontested(self, s: Dict[str, Any]) -> Optional[str]:
        m = self._check_market(s)
        if m:
            return m
        dd = s["dd"]
        if is_zero_address(dd["proposer"]):
            return "noProposal"
        if not is_zero_address(dd["disputer"]):
            return "disputed"
        if s["now"] < dd["dispute_start_time"] + s["c"]["PROPOSAL_PERIOD"]:
            return "Wait more"
        return None

    @staticmethod
    def _check_vote(s: Dict[str, Any], is_voter: bool, has_voted: bool) -> Optional[str]:
        if is_zero_address(s["host"]):
            return "Not a ranked market"
        if not s["in_dispute"]:
            return "Not voting"
        if s["in_veto"]:
            return "cannot vote during veto"
        if s["now"] + 1 > s["dd"]["dispute_start_time"] + s["c"]["DISPUTE_PERIOD"]:
            return "Disputed period ended"
        if not is_voter:
            return "Not an authorized voter"
        if has_voted:
            return "Voted"
        return None

    @staticmethod
    def _check_veto(s: Dict[str, Any], me: str, key: Optional[str]) -> Optional[str]:
        dd = s["dd"]
        if is_zero_address(s["host"]):
            return "Not a ranked market"
        if not s["market_exists"]:
            return "Invalid market"
        if s["resolved"]:
            return "Already resolved"
        if not s["in_dispute"]:
            return "Veto only allowed on disputed/voting markets"
        if s["in_veto"]:
            return "Veto only allowed once"
        if not is_zero_address(dd["vetoer"]):
            return "Already vetoed"
        if key is not None and _eq(dd["disputed_key"], key):
            return "Cant veto propose dispute outcome"
        if s["now"] < dd["dispute_start_time"] + s["c"]["DISPUTE_PERIOD"]:
            return "Veto window not started yet"
        if _eq(me, dd["proposer"]) or _eq(me, dd["disputer"]):
            return "Party to the dispute cannot veto"
        if key is not None and _eq(key, dd["original_key"]):
            return "Cannot veto back to the proposal"
        return None

    @staticmethod
    def _check_finalize_market(s: Dict[str, Any]) -> Optional[str]:
        if is_zero_address(s["host"]):
            return "Not a ranked market"
        if not s["in_dispute"]:
            return "notInDispute"
        if s["resolved"]:
            return "Already resolved"
        if s["in_veto"]:
            return "inVeto"
        t = s["now"]
        start = s["dd"]["dispute_start_time"]
        c, tally = s["c"], s["tally"]
        fast_early = (_eq(tally["leader"], KEY_EARLY) and not tally["tie"] and tally["total_votes"] >= c["MIN_QUORUM"]
                      and t >= start + c["EARLY_FAST_PERIOD"])
        if not fast_early and t < start + c["DISPUTE_PERIOD"] + c["VETO_PERIOD"]:
            return "Wait more"
        return None

    @staticmethod
    def _fail(rule: Optional[str], action: str) -> None:
        if not rule:
            return
        raise ValidationError(rule, f"{action}: {_FAIL_MESSAGES.get(rule, rule)}.")

    def check_ranking(self, market: str, ranking: Sequence[int], tie_mask: int = 0) -> None:
        """Validate a ranking against the market (client-side rules, then the host's own check).
        ``[]`` = INVALID, always legal. Picks must be whole numbers 0-255 (``rankingEntry``): nothing is truncated."""
        market = to_address(market, "market")
        picks = _picks(ranking)
        if len(picks) == 0:
            return
        ranking_key(picks, tie_mask)
        host = self.host_of(market)
        if is_zero_address(host):
            raise ValidationError("Not a ranked market", "This market is not registered on a ranked host with its resolver.")
        h = self._contract(host, "TrancheMarket").functions
        n = h.getNumOutcomes(_cs(market)).call()
        try:
            bp = h.getTrancheBP(_cs(market)).call()
        except Exception:  # noqa: BLE001
            bp = None
        ok = h.isValidRanking(_cs(market), picks, tie_mask).call()
        if bp is not None:
            validate_ranking(picks, tie_mask, int(n), len(bp))
        if not ok:
            raise ValidationError("Bad ranking", "The host rejects this ranking (isValidRanking is false).")

    def _post_bond(self, s: Dict[str, Any], amount: int) -> None:
        """The bond's checks and its exact approval: 'pair asset paused' when the issuer of ``BOND_TOKEN`` has paused it
        (the bond could not move and the tx would revert after the approval), then the balance ('bondBalance'), both
        before the approval. TS: ``postBond``."""
        if amount == 0:
            return
        token = s["c"]["BOND_TOKEN"]
        refuse_paused_asset(self.client, token, None, "the bond cannot be posted")
        bal = self._balance_of(token)
        if bal < amount:
            raise ValidationError("bondBalance", f"The bond is {amount} raw BOND_TOKEN ({token}); the wallet holds {bal}.")
        self._ensure_allowance(token, s["resolver"], amount)


    def propose(self, market: str, ranking: Sequence[int], tie_mask: int = 0) -> Dict[str, Any]:
        """Propose a result. ``ranking`` lists the finish order of the top-K slots (ties included per ``tie_mask``);
        ``[]`` proposes INVALID. Bond: ``get_required_bond(market)`` in ``BOND_TOKEN``, approved exactly. The creator
        may propose from ``endTime``, anyone else 15 minutes later; open-ended markets any time. A
        trusted proposer may propose INVALID (``[]``) on a dated market before that, for a cancelled event or a broken
        question; it runs the normal dispute and vote steps. Every market takes no bets while a proposal is live.
        When the resolver has its optional proposal rule on, a wallet the rule refuses gets
        ``ValidationError('Rules: not qualified')`` before any bond is approved (trusted proposers skip the rule).
        ``bond_posted`` comes from the ``Proposal`` event."""
        market = to_address(market, "market")
        me = self._account
        key = ranking_key(ranking, tie_mask)
        picks = _picks(ranking)
        s = self._snapshot(market)
        self._pre_ranked(s, market, picks, tie_mask)
        fns = self._r(s["resolver"])
        trusted = fns.trustedProposer(me).call()
        self._fail(self._check_propose(s, me, key, trusted), "propose")
        refused = self._proposal_rule_refusal(s, market, me, trusted)
        if refused:
            raise ValidationError(RULES_RULE, f"propose: {refused}.")
        bond = fns.requiredBond(market, me).call()
        self._post_bond(s, bond)
        bond_of = self._bond_of(s)
        tx = self._send(fns.proposeRanked(market, picks, tie_mask), "MarketResolver")
        ev = self._market_event(tx, s["resolver"], "Proposal", market, ["bond_posted"])
        return {**tx, "key": _hex32(ev["outcomeKey"]) if ev else key, "bond": bond_of(bond),
                "bond_posted": bond_of(ev["bond"]) if ev else None}

    def propose_outcome(self, market: str, outcome: int) -> Dict[str, Any]:
        """Winner-take-all proposal: ``propose(market, [outcome], 0)``."""
        return self.propose(market, [outcome], 0)

    def dispute(self, market: str, ranking: Sequence[int], tie_mask: int = 0) -> Dict[str, Any]:
        """Dispute the live proposal with another result (``[]`` = INVALID). Within PROPOSAL_PERIOD of the proposal,
        not by the proposer, key must differ. Bond: ``get_dispute_bond``, approved exactly. ``bond_posted`` comes from
        the ``DisputeStarted`` event. Risk: a losing dispute loses the bond to the winner and insurance. ``ranking`` must
        be a list/tuple (anything else, None included, is ``ValidationError('ranking')`` before any read): EARLY is
        ``dispute_early``."""
        return self._do_dispute(market, False, ranking, tie_mask)

    def dispute_early(self, market: str) -> Dict[str, Any]:
        """Dispute the proposal as EARLY (the event has not happened yet). Same bond as ``dispute``. If EARLY wins you
        get your bond back plus half the proposer's bond; the other half goes to insurance. The market is not resolved: the
        proposal is wiped and the next proposal on it costs double."""
        return self._do_dispute(market, True, [], 0)

    def _do_dispute(self, market: str, early: bool, ranking: Sequence[int], tie_mask: int) -> Dict[str, Any]:
        """``early`` True (only from ``dispute_early``) = an EARLY dispute, ``ranking`` unused; else ``ranking`` is the
        caller's, checked strictly (``ranking_key``: a None or non-list ranking is 'ranking', never read as EARLY)."""
        market = to_address(market, "market")
        me = self._account
        key = KEY_EARLY if early else ranking_key(ranking, tie_mask)
        picks = None if early else _picks(ranking)
        s = self._snapshot(market)
        self._pre_ranked(s, market, picks, tie_mask)
        self._fail(self._check_dispute(s, me, key), "dispute")
        fns = self._r(s["resolver"])
        bond = _dispute_bond_for(s["dd"]["proposer_bond"], s["c"], fns.trustedProposer(me).call())
        self._post_bond(s, bond)
        bond_of = self._bond_of(s)
        if picks is None:
            tx = self._send(fns.disputeRankedEarly(market), "MarketResolver")
        else:
            tx = self._send(fns.disputeRanked(market, picks, tie_mask), "MarketResolver")
        ev = self._market_event(tx, s["resolver"], "DisputeStarted", market, ["bond_posted"])
        return {**tx, "key": _hex32(ev["newOutcomeKey"]) if ev else key, "bond": bond_of(bond),
                "bond_posted": bond_of(ev["bond"]) if ev else None}

    def vote(self, market: str, ranking: Sequence[int], tie_mask: int = 0) -> Dict[str, Any]:
        """Panel vote (``[]`` = INVALID). Authorized voters only, one vote per round, within DISPUTE_PERIOD of the
        dispute. The key is computed (and the picks checked) before anything is read or sent.
        ``round`` comes from the ``Vote`` event."""
        market = to_address(market, "market")
        key = ranking_key(ranking, tie_mask)
        picks = _picks(ranking)
        s = self._pre_vote(market, picks, tie_mask)
        bond_of = self._bond_of(s)
        tx = self._send(self._r(s["resolver"]).voteRanked(market, picks, tie_mask), "MarketResolver")
        return self._vote_result(tx, s, market, key, bond_of)

    def vote_early(self, market: str) -> Dict[str, Any]:
        """Panel vote for EARLY."""
        market = to_address(market, "market")
        s = self._pre_vote(market, None, 0)
        bond_of = self._bond_of(s)
        tx = self._send(self._r(s["resolver"]).voteRankedEarly(market), "MarketResolver")
        return self._vote_result(tx, s, market, KEY_EARLY, bond_of)

    def _pre_vote(self, market: str, picks: Optional[list], tie_mask: int) -> Dict[str, Any]:
        """``picks`` None = EARLY."""
        me = self._account
        s = self._snapshot(market)
        self._pre_ranked(s, market, picks, tie_mask)
        flags = self._user_flags(s["resolver"], market, s["round"], me)
        self._fail(self._check_vote(s, flags["is_voter"], flags["has_voted"]), "vote")
        return s

    def _vote_result(self, tx: Dict[str, Any], s: Dict[str, Any], market: str, key: str,
                     bond_of: AmountOf) -> Dict[str, Any]:
        ev = self._market_event(tx, s["resolver"], "Vote", market, ["round"])
        return {**tx, "key": _hex32(ev["outcomeKey"]) if ev else key, "bond": bond_of(0),
                "round": int(ev["round"]) if ev else None}

    def _bond_of(self, s: Dict[str, Any]) -> AmountOf:
        """Formatter for ``BOND_TOKEN`` amounts of the snapshot's resolver, resolved before sending."""
        return amount_of(self.client, s["c"]["BOND_TOKEN"])

    def _pair_of(self, s: Dict[str, Any]) -> AmountOf:
        """Formatter for amounts in the market's pair asset, resolved before sending."""
        return pair_amount_of(self.client.mains.get(s["main"]))

    def veto(self, market: str, ranking: Sequence[int], tie_mask: int = 0) -> Dict[str, Any]:
        """Veto toward another result (``[]`` = INVALID; EARLY is not allowed). From disputeStartTime +
        DISPUTE_PERIOD, once, not by the proposer or disputer, key neither the proposal's nor the dispute's. Bond:
        ``get_veto_bond``, approved exactly. After a veto only BASIS resolves the market. ``bond_posted`` comes from the
        ``Vetoed`` event. Risk: a losing veto loses its whole bond to insurance. A winning veto gets its bond back plus
        a quarter of the other two bonds, capped at 2x its own."""
        market = to_address(market, "market")
        me = self._account
        key = ranking_key(ranking, tie_mask)
        picks = _picks(ranking)
        s = self._snapshot(market)
        self._pre_ranked(s, market, picks, tie_mask)
        self._fail(self._check_veto(s, me, key), "veto")
        bond = _veto_bond_for(s["dd"]["proposer_bond"], s["c"])
        self._post_bond(s, bond)
        bond_of = self._bond_of(s)
        tx = self._send(self._r(s["resolver"]).vetoRanked(market, picks, tie_mask), "MarketResolver")
        ev = self._market_event(tx, s["resolver"], "Vetoed", market, ["bond_posted"])
        return {**tx, "key": _hex32(ev["proposedKey"]) if ev else key, "bond": bond_of(bond),
                "bond_posted": bond_of(ev["bond"]) if ev else None}

    def _pre_ranked(self, s: Dict[str, Any], market: str, picks: Optional[list], tie_mask: int) -> None:
        """The first two rules of every ranked action: a ranked market, then a legal ranking (None / ``[]`` skip the
        second)."""
        if is_zero_address(s["host"]):
            self._fail("Not a ranked market", "resolver")
        if picks:
            self.check_ranking(market, picks, tie_mask)

    def finalize_uncontested(self, market: str) -> Dict[str, Any]:
        """Resolve an undisputed proposal once PROPOSAL_PERIOD has passed (anyone; refunds the proposer's bond).
        The result has ``outcome`` 'resolved', the winning ``key`` and the bond refund in ``payouts``."""
        market = to_address(market, "market")
        s = self._snapshot(market)
        self._fail(self._check_finalize_uncontested(s), "finalizeUncontested")
        bond_of, pair_of = self._bond_of(s), self._pair_of(s)
        tx = self._send(self._r(s["resolver"]).finalizeUncontested(market), "MarketResolver")
        return self._finalize_result(tx, s, market, bond_of, pair_of)

    def finalize_market(self, market: str) -> Dict[str, Any]:
        """Close a disputed market after DISPUTE_PERIOD + VETO_PERIOD (or EARLY_FAST_PERIOD when EARLY leads alone
        with a quorum). Anyone. Below MIN_QUORUM votes or an exact tie reopens a fresh round instead of resolving;
        EARLY winning resets the proposal (and doubles the next bond) without resolving. On EARLY the proposer's bond
        is lost (an EARLY disputer gets half of it, insurance the other half); a dispute that named a result loses its bond too. The result says
        which one happened (``outcome``), with the bond payouts and seizures."""
        market = to_address(market, "market")
        s = self._snapshot(market)
        self._fail(self._check_finalize_market(s), "finalizeMarket")
        bond_of, pair_of = self._bond_of(s), self._pair_of(s)
        tx = self._send(self._r(s["resolver"]).finalizeMarket(market), "MarketResolver")
        return self._finalize_result(tx, s, market, bond_of, pair_of)

    def _finalize_result(self, tx: Dict[str, Any], s: Dict[str, Any], market: str, bond_of: AmountOf,
                         pair_of: AmountOf) -> Dict[str, Any]:
        """Decode a finalize receipt: Resolved / VoteReopened / EarlyReset (+ DisputeReset), bond payouts and seizures
        of ``market``, the pot the protocol kept when no outcome won."""
        resolver = s["resolver"]
        c = self._contract(resolver, "MarketResolver")

        def mine(name: str) -> list:
            return [e for e in self._events(tx, c, name) if _eq(e["marketToken"], market)]

        resolved, reopened, early, reset = (next(iter(mine(n)), None) for n in ("Resolved", "VoteReopened", "EarlyReset", "DisputeReset"))
        payouts = []
        for log in tx["receipt"].get("logs") or []:
            if not _eq(str(log.get("address", "")), resolver):
                continue
            one = {**dict(tx), "receipt": {**dict(tx["receipt"]), "logs": [log]}}
            for e in self._events(one, c, "BondsDistributed"):
                if _eq(e["marketToken"], market):
                    payouts.append({"to": e["winner"], "amount": bond_of(e["totalAmount"]), "redirected": False})
            for e in self._events(one, c, "BondRedirected"):
                if _eq(e["marketToken"], market):
                    payouts.append({"to": e["intended"], "amount": bond_of(e["amount"]), "redirected": True})
        seized = sum(e["totalAmount"] for e in mine("BondsSeized"))
        host = self._contract(s["host"], "TrancheMarket")
        swept = sum(e["usdcToTaxes"] for e in self._events(tx, host, "NoWinnerSwept") if _eq(e["marketToken"], market))
        outcome = "resolved" if resolved else "earlyReset" if early else "reopened" if reopened else None
        if outcome is None:
            add_missing_event_warning(tx, "MarketResolver", "Resolved/VoteReopened/EarlyReset", ("outcome", "key", "round"))
        rnd = int(reopened["round"]) if reopened else (int(reset["round"]) if early and reset else None)
        return {
            **tx,
            "outcome": outcome,
            "key": _hex32(resolved["outcomeKey"]) if resolved else None,
            "round": rnd,
            "early_resets": int(early["resets"]) if early else None,
            "payouts": payouts,
            "seized": bond_of(seized),
            "swept": pair_of(swept),
        }

    def _market_event(self, tx: Dict[str, Any], resolver: str, name: str, market: str,
                      fields: Sequence[str]) -> Optional[Dict[str, Any]]:
        """The first ``name`` log of ``market`` from the resolver, or None with a warning naming ``fields``."""
        c = self._contract(resolver, "MarketResolver")
        ev = next((e for e in self._events(tx, c, name) if _eq(e["marketToken"], market)), None)
        if ev is None:
            add_missing_event_warning(tx, "MarketResolver", name, fields)
        return ev
