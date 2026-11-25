"""The staking vault.

Stake a main, earn rewards in it, and borrow the pair asset against the position. One position per (main, user).
Stakes are 1:1 in the main: no shares, no receipt token. Layer 1: stake / unstake / claim / compound. Layer 2: borrow
the pair asset against the position (the pledge happens in ``borrow``), add to it, repay, extend. Loans are made by the
BASIS protocol. If a loan is liquidated, the loss comes off the stake, then off unclaimed rewards; there is nothing to
settle by hand. Buy-and-stake is ``client.trading.buy_and_stake``.

Inputs are raw ints: main amounts in main units (18 decimals). Reads return ``Amount``s. Every method takes
``main=None`` (address or address-book key; default: the default main) and ``vault=None``: the vault holding the
position when it is not the main's current one. A main can move to a new vault; positions and open loans stay
on the old one, which keeps every exit (unstake, claim, compound, repay, extend_loan) and every read open. Entries
(stake, borrow, add_to_loan) only run on the current vault: an override that differs is refused ("Main not on this
vault").

While the issuer of a main's pair asset has paused it (USDG and tokenized stocks can be paused), the asset cannot move:
every write that pays it in or out (borrow, add_to_loan, repay, extend_loan, unstake with ``to_pair_asset``) is refused
before any approval or tx ("pair asset paused"); stake, a plain unstake, claim and compound move only the main.

Result dicts (snake_case keys):
  position: main, vault, user, staked (includes the pledged part, which keeps earning), pledged, hub_id,
    has_active_loan, checkpoint (raw, bookkeeping only: use earned), pending (unclaimed rewards as of the position's
    last update), hub (the loan hub an open loan is on), earned (all unclaimed rewards), collateral_of (stake plus
    unclaimed rewards), free_to_unstake, available_stasis (what can still be borrowed against) (main amounts as
    ``Amount``).
  loan: main, vault, hub, hub_id, ecosystem, core_loan_id, collateral_token, token, collateral_amount,
    liquidated_amount, full_amount, borrowed_amount, liquidation_time, liquidation_claim, is_liquidated, active,
    creation_time (collateral / claim in main units, debt in the pair asset).
  totals: main, vault, total_staked, total_pledged, rewards_held, total_stasis_available (new borrows cannot pledge
    more), acc_reward_per_stake (raw, bookkeeping only).
  extension quote: possible (False on a paused pair: "pair not active", and while the pair asset's issuer has paused
    it: "pair asset paused"), fee (pay-in: what the vault pulls from the wallet; else added to the debt or taken from a
    refinance), extra_out (pair-asset ``Amount``s).
  borrow quote (``quote_borrow``): main, vault, hub (``get_loan_hub``), collateral_value (= the debt), fee,
    borrowed, meets_minimum, possible, refusal (pair-asset ``Amount``s; the rule ``borrow`` would refuse it with, or
    None; "pair not active" while the main's pair is paused, "pair asset paused" while its issuer paused the asset).
  add-to-loan quote (``quote_add_to_loan``): main, vault, hub (the loan's hub), hub_id, new_collateral_value,
    added_value, fee, borrowed (pair-asset ``Amount``s), days_left, possible, refusal.
  loan owner: user, main.
  Write results carry the figures of the transaction's events as ``Amount``s (None when the event is missing;
    ``warnings`` says why): stake: staked. unstake: unstaked, pair_received. claim: claimed. compound: compounded.
    borrow: hub_id, hub, pledged, received. add_to_loan: pledged, received. repay: hub_id, returned (main released
    back to the position). extend_loan: new_liquidation_time (unix s), new_full_amount, extra_out (refinance payout,
    else 0).
"""

from typing import Any, Dict, List, Optional, Tuple, Union

from web3 import Web3

from ..config.addresses import is_zero_address
from ..core.base import BaseModule
from ..core.errors import ValidationError
from ..core.events import add_missing_event_warning
from ..core.loans import (core_extend_refusal, is_asset_paused, is_pair_active, pair_asset_paused, pair_not_active,
                          refuse_paused_asset)
from ..core.mains import MainInfo, main_amount, pair_amount
from ..core.multicall import multicall
from ..core.units import Amount, opt_uint, to_address, to_bool, to_uint

_POSITION_READS = 5
_DAY = 86_400

Number = Union[int, float]



def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


class VaultModule(BaseModule):
    """Stake a main, earn its trading yield, and borrow the pair asset against the position."""

    @property
    def deployed(self) -> bool:
        """True when the vault can be used on this chain."""
        return not is_zero_address(self.client.addresses["swap"])

    def vault_of(self, main: Optional[str] = None) -> str:
        """The address of the vault serving ``main`` (default: the default main)."""
        return self._ctx(main, None)[1]


    def stake(self, amount: int, main: Optional[str] = None, vault: Optional[str] = None) -> Dict[str, Any]:
        """Stake ``amount`` (raw main, at least ``get_min_stake()``, else "Below min stake"). Approves the main to the
        vault. Returns the write result plus ``staked``."""
        amt = to_uint(amount, "amount")
        info, vault = self._ctx(main, vault, entry=True)
        vc = self._contract(vault, "Vault")
        min_stake = vc.functions.minStake().call()
        if amt < min_stake:
            raise ValidationError("Below min stake", f"Stake must be at least {min_stake} raw {info.symbol}, got {amt}.")
        self._check_balance(info.main, amt, info.symbol)
        self._ensure_allowance(info.main, vault, amt)
        tx = self._send(vc.functions.stake(_cs(info.main), amt), "Vault")
        ev = self._expect_event(tx, vc, "Staked", "Vault", ("staked",))
        return {**tx, "staked": main_amount(ev["amount"], info) if ev else None}

    def unstake(self, amount: int, to_pair_asset: bool = False, min_out: Optional[int] = None,
                slippage_bps: Optional[int] = None, main: Optional[str] = None, vault: Optional[str] = None) -> Dict[str, Any]:
        """Unstake ``amount`` (raw main) of the stake. With ``to_pair_asset`` the main is sold at the signer's own fee
        rate and the pair asset paid out; the minimum is derived from
        ``trading.quote([main, pair_asset], amount, signer)`` unless ``min_out`` (raw pair asset, never 0) is given.
        Rewards are taken out with ``claim``. Refused with "Insufficient stake" above the stake and "Collateral in use"
        above ``free_to_unstake`` (the part not backing a loan). On a vault the main moved away from (``vault``
        override), only a plain unstake works ("Only STAKING"). ``to_pair_asset`` is also refused while the main's pair
        is paused ("unknown pair", read live) and while the pair asset's issuer has paused it ("pair asset paused").
        Returns the write result plus ``unstaked`` and ``pair_received``."""
        amt = to_uint(amount, "amount")
        if amt == 0:
            raise ValidationError("Zero amount", "Amount must be greater than 0.")
        to_pair_asset = to_bool(to_pair_asset, "toPairAsset", False)
        if not to_pair_asset and min_out is not None:
            raise ValidationError("minOut", "minOut only applies with toPairAsset (a plain unstake returns the main 1:1).")
        explicit_min = opt_uint(min_out, "minOut")
        info, vault = self._ctx(main, vault)
        vc = self._contract(vault, "Vault")
        me = self._account
        pos = self._raw_position(vault, info.main, me)
        free = vc.functions.freeToUnstake(_cs(info.main), me).call()
        if amt > pos["staked"]:
            raise ValidationError("Insufficient stake", f"Staked {pos['staked']}, cannot unstake {amt}.")
        if amt > free:
            raise ValidationError("Collateral in use", f"Only {free} of the stake is free to unstake (the rest backs the loan).")
        if to_pair_asset and not self._listed(info, vault):
            raise ValidationError(
                "Only STAKING",
                f"SWAP lists {info.vault} as the vault of {info.symbol}, so vault {vault} can no longer sell it; "
                "unstake without to_pair_asset.")
        min_out_sent = 0
        if to_pair_asset:
            swap = self._contract(self._require(self.client.addresses["swap"], "Swap"), "Swap")
            _, _, active = swap.functions.pairs(_cs(info.main)).call()
            if not active:
                raise ValidationError(
                    "unknown pair",
                    f"{info.symbol} ({info.main}) is paused on SWAP, so the vault cannot "
                    f"sell it for {info.pair_symbol}; unstake without to_pair_asset to get the main back 1:1.")
            refuse_paused_asset(
                self.client, info.pair_asset, info.pair_symbol,
                f"the vault cannot pay it out for the unstaked {info.symbol}; unstake without to_pair_asset to get the "
                "main back 1:1")
            min_out_sent = self._resolve_min(
                explicit_min, slippage_bps, lambda: self.client.trading.quote([info.main, info.pair_asset], amt, me))
        tx = self._send(vc.functions.unstake(_cs(info.main), amt, to_pair_asset, min_out_sent), "Vault")
        ev = self._expect_event(tx, vc, "Unstaked", "Vault", ("unstaked", "pair_received"))
        return {**tx, "unstaked": main_amount(ev["amount"], info) if ev else None,
                "pair_received": pair_amount(ev["mainPairReceived"], info) if ev else None}

    def claim(self, main: Optional[str] = None, vault: Optional[str] = None) -> Dict[str, Any]:
        """Pay out unclaimed rewards in the main. With a loan open, only the part not needed as collateral. Returns the
        write result plus ``claimed``."""
        info, vault = self._ctx(main, vault)
        vc = self._contract(vault, "Vault")
        me = self._account
        pos = self._raw_position(vault, info.main, me)
        earned = vc.functions.earned(_cs(info.main), me).call()
        total = pos["staked"] + earned
        free = total - pos["pledged"] if total > pos["pledged"] else 0
        if min(earned, free) == 0:
            raise ValidationError("Nothing to claim", "No unclaimed rewards." if earned == 0 else "All unclaimed rewards back the loan.")
        tx = self._send(vc.functions.claim(_cs(info.main)), "Vault")
        ev = self._expect_event(tx, vc, "RewardsClaimed", "Vault", ("claimed",))
        return {**tx, "claimed": main_amount(ev["amount"], info) if ev else None}

    def compound(self, main: Optional[str] = None, vault: Optional[str] = None) -> Dict[str, Any]:
        """Add the unclaimed rewards to the stake (nothing leaves the vault). Returns the write result plus
        ``compounded``."""
        info, vault = self._ctx(main, vault)
        vc = self._contract(vault, "Vault")
        earned = vc.functions.earned(_cs(info.main), self._account).call()
        if earned == 0:
            raise ValidationError("Nothing to compound", "No unclaimed rewards.")
        tx = self._send(vc.functions.compound(_cs(info.main)), "Vault")
        ev = self._expect_event(tx, vc, "RewardsCompounded", "Vault", ("compounded",))
        return {**tx, "compounded": main_amount(ev["amount"], info) if ev else None}


    def borrow(self, amount: int, days: Number, main: Optional[str] = None, vault: Optional[str] = None) -> Dict[str, Any]:
        """Borrow the pair asset against ``amount`` (raw main) of the position for ``days`` (within the main's loan-day
        range). Pledges in the same call (no lock step). One loan per position: use ``add_to_loan`` when one is open.
        Refused, in this order, with: "Main not on this vault", "Position active. Use addToLoan", "Hook not registered"
        (borrowing is not set up on this vault), "Zero amount", "Insufficient collateral value" (above the unpledged
        stake), "Contract low on liquidity" (the vault has too little free main to pledge), "pair not active" (the
        main's pair is paused), "Ecosystem inactive", "Unauthorized caller" (vault loans are not open on this main right
        now), "incorrect loan duration", "Minimum loan amount not met", "pair asset paused" (its issuer paused the pair
        asset), "No stable received" (the fee takes the whole value). ``quote_borrow`` previews it.
        Returns the write result plus ``hub_id``, ``hub``, ``pledged`` and ``received``."""
        amt = to_uint(amount, "amount")
        d = to_uint(days, "days")
        info, vault = self._ctx(main, vault, entry=True)
        m = _cs(info.main)
        vc = self._contract(vault, "Vault")
        plan = self._borrow_plan(info, vault, None, self._account, amt, d)
        if plan["error"] is not None:
            raise plan["error"]
        tx = self._send(vc.functions.borrow(m, amt, d), "Vault")
        ev = self._expect_event(tx, vc, "LoanTaken", "Vault", ("hub_id", "hub", "pledged", "received"))
        return {**tx, "hub_id": ev["hubId"] if ev else None, "hub": ev["hub"] if ev else None,
                "pledged": main_amount(ev["stasisPledged"], info) if ev else None,
                "received": pair_amount(ev["mainPairReceived"], info) if ev else None}

    def add_to_loan(self, amount: int, main: Optional[str] = None, vault: Optional[str] = None) -> Dict[str, Any]:
        """Pledge ``amount`` (raw main) more to the open loan and receive more pair asset (the added collateral value
        minus the fee for the days left; the end date stays). Refused, in this order, with: "Main not on this vault",
        "No active loan", "Zero amount", "Insufficient collateral value", "Contract low on liquidity", "pair not active"
        (the main's pair is paused), "Loan not active", "Duration too short" (too few days left: extend first), "No
        borrowing power", "Fees exceed value", "pair asset paused" (its issuer paused the pair asset).
        ``quote_add_to_loan`` previews it. Returns the write result plus ``pledged`` and ``received``."""
        amt = to_uint(amount, "amount")
        info, vault = self._ctx(main, vault, entry=True)
        m = _cs(info.main)
        vc = self._contract(vault, "Vault")
        plan = self._add_plan(info, vault, None, self._account, amt)
        if plan["error"] is not None:
            raise plan["error"]
        tx = self._send(vc.functions.addToLoan(m, amt), "Vault")
        ev = self._expect_event(tx, vc, "LoanAdded", "Vault", ("pledged", "received"))
        return {**tx, "pledged": main_amount(ev["additionalStasis"], info) if ev else None,
                "received": pair_amount(ev["mainPairReceived"], info) if ev else None}

    def repay(self, main: Optional[str] = None, vault: Optional[str] = None) -> Dict[str, Any]:
        """Repay the loan in full (there is no partial repay: it always closes the loan). Approves the debt
        (``full_amount``, raw pair asset) to the vault. The pledge is released. Refused before the approval: "No active
        loan", "Core loan inactive" (the loan is no longer active, e.g. liquidated), "pair asset paused" (its issuer
        paused the pair asset: the debt cannot be paid in until it is unpaused), "balance". Returns the write result plus
        ``hub_id`` and ``returned`` (main released back to the position)."""
        info, vault = self._ctx(main, vault)
        vc = self._contract(vault, "Vault")
        pos = self._raw_position(vault, info.main, self._account)
        if not pos["has_active_loan"]:
            raise ValidationError("No active loan", "This position has no loan.")
        loan = self._hub_loan(vault, pos)
        if not loan["active"]:
            raise ValidationError("Core loan inactive", f"Hub loan {pos['hub_id']} is not active.")
        refuse_paused_asset(self.client, info.pair_asset, info.pair_symbol, "the loan cannot be repaid in it now")
        self._check_balance(info.pair_asset, loan["full_amount"], info.pair_symbol)
        self._ensure_allowance(info.pair_asset, vault, loan["full_amount"])
        tx = self._send(vc.functions.repay(_cs(info.main)), "Vault")
        ev = self._expect_event(tx, vc, "LoanRepaid", "Vault", ("hub_id", "returned"))
        return {**tx, "hub_id": ev["hubId"] if ev else None,
                "returned": main_amount(ev["stasisReturned"], info) if ev else None}

    def extend_loan(self, days: Number, pay_in_pair_asset: bool, refinance: bool,
                    main: Optional[str] = None, vault: Optional[str] = None) -> Dict[str, Any]:
        """Extend the loan by ``days`` (the main's range, or 0 for a refinance). The days count from the due date, or
        from the time of the transaction when the loan is past due (not yet liquidated): the new due date is the later
        of the two plus ``days``. ``pay_in_pair_asset`` pays the fee from the wallet (approved to the vault); otherwise
        it is added to the debt, or a refinance pays the surplus out. Refused, in this order, with: "No active loan";
        then "pair not active" (the main's pair is paused) and "pair asset paused" (its issuer paused the pair asset),
        before any approval; for a pay-in, "not possible" (any failure, 0 days and days out of range included); "Pay in
        stable or refinance, not both"; then, for add-to-debt and refinance, the loan rules ("Must be refinance if not
        extending time", "incorrect loan duration", "Loan not active", "Not enough collateral to cover fees", "No
        equity", "Minimum surplus not met", "Time remaining too short", "Fees exceed surplus value"). Returns the write
        result plus ``new_liquidation_time``, ``new_full_amount`` and the refinance payout ``extra_out``."""
        d = to_uint(days, "days")
        pay_in_pair_asset = to_bool(pay_in_pair_asset, "payInPairAsset")
        refinance = to_bool(refinance, "refinance")
        info, vault = self._ctx(main, vault)
        vc = self._contract(vault, "Vault")
        pos = self._raw_position(vault, info.main, self._account)
        if not pos["has_active_loan"]:
            raise ValidationError("No active loan", "This position has no loan.")
        loan = self._hub_loan(vault, pos)
        q = self._extension_eligibility(pos, loan["ecosystem"], loan["core_loan_id"], d, pay_in_pair_asset, refinance)
        if not is_pair_active(self.client, loan["ecosystem"]):
            raise pair_not_active(loan["ecosystem"], info.symbol)
        refuse_paused_asset(self.client, info.pair_asset, info.pair_symbol, "no extension can take its fee")
        if pay_in_pair_asset and not q["possible"]:
            min_d, max_d = self._day_range(loan["ecosystem"])
            why = (f"hub loan {pos['hub_id']} is not active" if not loan["active"]
                   else "0 days is only allowed for a refinance without a pay-in" if d == 0
                   else f"days must be {min_d}-{max_d} on this main, got {d}")
            raise ValidationError("not possible", f"The loan cannot be extended with a pay-in: {why}.")
        if pay_in_pair_asset and refinance:
            raise ValidationError("Pay in stable or refinance, not both", "Choose either payInPairAsset or refinance, not both.")
        if not pay_in_pair_asset:
            refusal = core_extend_refusal(self.client, loan["ecosystem"], loan, d, False, refinance, q)
            if refusal is not None:
                raise refusal
        else:
            self._check_balance(info.pair_asset, q["fee"], info.pair_symbol)
            self._ensure_allowance(info.pair_asset, vault, q["fee"])
        tx = self._send(vc.functions.extendLoan(_cs(info.main), d, pay_in_pair_asset, refinance), "Vault")
        core = next((e for e in self._events(tx, self._contract(loan["ecosystem"], "MainToken"), "LoanExtended")
                     if e["user"].lower() == pos["hub"].lower() and e["loanId"] == loan["core_loan_id"]), None)
        if core is None:
            add_missing_event_warning(tx, "MainToken", "LoanExtended", ("new_liquidation_time", "new_full_amount"))
        extra_out = 0
        if refinance:
            ev = self._events(tx, vc, "RefinanceSurplus")
            extra_out = ev[0]["mainPairReceived"] if ev else 0
        return {**tx, "new_liquidation_time": core["newLiquidationTime"] if core else None,
                "new_full_amount": pair_amount(core["newFullAmount"], info) if core else None,
                "extra_out": pair_amount(extra_out, info)}


    def get_position(self, user: Optional[str] = None, main: Optional[str] = None,
                     vault: Optional[str] = None) -> Dict[str, Any]:
        """The live position of ``user`` (default the signer) on the main, with earned, collateral_of,
        free_to_unstake and available_stasis (all read on chain: the API serves only the stored stake)."""
        who = self._account if user is None else to_address(user, "user")
        info, vault = self._ctx(main, vault)
        return self._to_position(info, vault, who, multicall(self.client, self._position_reads(info, vault, who)))

    def get_positions(self, user: Optional[str] = None, vaults: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """The live positions of ``user`` (default the signer) on every listed main, on its current vault and on
        each of ``vaults`` (vaults a main moved away from; a list or a tuple, None = none: anything else is refused,
        rule ``vaults``, before any read), skipping empty ones (no stake, no unclaimed rewards, no open loan). One
        multicall for all of them."""
        who = self._account if user is None else to_address(user, "user")
        if vaults is not None and not isinstance(vaults, (list, tuple)):
            raise ValidationError("vaults", "vaults must be an array of vault addresses.")
        extra = [self._check_vault(v) for v in (vaults or [])]
        seen = set()
        pairs = []
        for info in self.client.mains.list():
            for v in [info.vault, *extra]:
                key = f"{info.main}:{v}".lower()
                if is_zero_address(v) or key in seen:
                    continue
                seen.add(key)
                pairs.append((info, _cs(v)))
        res = multicall(self.client, [r for info, v in pairs for r in self._position_reads(info, v, who)])
        n = _POSITION_READS
        out = [self._to_position(info, v, who, res[i * n:(i + 1) * n]) for i, (info, v) in enumerate(pairs)]
        return [p for p in out if p["staked"].raw > 0 or p["earned"].raw > 0 or p["has_active_loan"]]

    def get_vault_loan(self, user: Optional[str] = None, main: Optional[str] = None,
                       vault: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The loan behind ``user``'s position (default the signer), or None without an open loan."""
        who = self._account if user is None else to_address(user, "user")
        info, vault = self._ctx(main, vault)
        pos = self._raw_position(vault, info.main, who)
        if not pos["has_active_loan"]:
            return None
        d = self._hub_loan(vault, pos)

        def m(raw: int) -> Amount:
            return main_amount(raw, info)

        def p(raw: int) -> Amount:
            return pair_amount(raw, info)

        return {
            "main": info.main, "vault": vault, "hub": pos["hub"], "hub_id": d["hub_id"], "ecosystem": d["ecosystem"],
            "core_loan_id": d["core_loan_id"], "collateral_token": d["collateral_token"], "token": d["token"],
            "collateral_amount": m(d["collateral_amount"]), "liquidated_amount": m(d["liquidated_amount"]),
            "full_amount": p(d["full_amount"]), "borrowed_amount": p(d["borrowed_amount"]),
            "liquidation_time": d["liquidation_time"], "liquidation_claim": m(d["liquidation_claim"]),
            "is_liquidated": d["is_liquidated"], "active": d["active"], "creation_time": d["creation_time"],
        }

    def get_extension_quote(self, days: Number, pay_in_pair_asset: bool, refinance: bool,
                            main: Optional[str] = None, user: Optional[str] = None,
                            vault: Optional[str] = None) -> Dict[str, Any]:
        """Preview ``extend_loan`` for ``user``'s (default the signer) loan: {possible, fee, extra_out} with the
        amounts as pair-asset ``Amount``s. Both flags at once are never possible (``possible`` False, ``fee`` and
        ``extra_out`` 0). On a paused pair (``extend_loan`` refuses "pair not active") and while the pair asset's issuer
        has paused it ("pair asset paused") ``possible`` is False; the figures stay."""
        d = to_uint(days, "days")
        pay_in_pair_asset = to_bool(pay_in_pair_asset, "payInPairAsset")
        refinance = to_bool(refinance, "refinance")
        who = self._account if user is None else to_address(user, "user")
        info, vault = self._ctx(main, vault)
        pos = self._raw_position(vault, info.main, who)
        if not pos["has_active_loan"]:
            raise ValidationError("No active loan", "This position has no loan.")
        loan = self._hub_loan(vault, pos)
        zero = pair_amount(0, info)
        if pay_in_pair_asset and refinance:
            return {"possible": False, "fee": zero, "extra_out": zero}
        q = self._extension_eligibility(pos, loan["ecosystem"], loan["core_loan_id"], d, pay_in_pair_asset, refinance)
        live = is_pair_active(self.client, loan["ecosystem"])
        possible = bool(q["possible"]) and live and not is_asset_paused(self.client, info.pair_asset)
        return {"possible": possible, "fee": pair_amount(q["fee"], info), "extra_out": pair_amount(q["extra_out"], info)}

    def quote_borrow(self, amount: int, days: Number, main: Optional[str] = None, user: Optional[str] = None,
                     vault: Optional[str] = None) -> Dict[str, Any]:
        """Preview ``borrow(amount, days)`` for ``user`` (default the signer): the value of the pledge (= the debt), the
        fees and what the wallet receives, in the pair asset, plus ``possible`` / ``refusal`` from the same checks
        ``borrow`` runs, in the same order: {main, vault, hub, collateral_value, fee, borrowed, meets_minimum,
        possible, refusal}. With ``possible``, ``borrowed`` is what ``borrow`` pays in the same state. A ``vault`` that
        is not the main's current vault gives ``refusal`` "Main not on this vault"."""
        amt = to_uint(amount, "amount")
        d = to_uint(days, "days")
        who = self._account if user is None else to_address(user, "user")
        info, vault = self._ctx(main, vault)
        entry = None if self._listed(info, vault) else self._not_on_vault(info, vault)
        p = self._borrow_plan(info, vault, entry, who, amt, d)
        value, fee, err = p["value"], p["fee"], p["error"]
        return {"main": info.main, "vault": vault, "hub": p["hub"], "collateral_value": pair_amount(value, info),
                "fee": pair_amount(fee, info), "borrowed": pair_amount(value - fee if value > fee else 0, info),
                "meets_minimum": value >= p["minimum_loan"], "possible": err is None,
                "refusal": err.rule if err is not None else None}

    def quote_add_to_loan(self, amount: int, main: Optional[str] = None, user: Optional[str] = None,
                          vault: Optional[str] = None) -> Dict[str, Any]:
        """Preview ``add_to_loan(amount)`` for ``user`` (default the signer), as it would run in the next block
        (latest + 1 s): {main, vault, hub, hub_id, new_collateral_value (the whole pledge after the add), added_value
        (added to the debt), fee (on the added value, for the whole days left), borrowed (what the wallet receives),
        days_left, possible, refusal}, amounts as pair-asset
        ``Amount``s, from the same checks ``add_to_loan`` runs ("Fees exceed value" also allows for a block with the
        latest block's time, which can leave one more whole day). Without a loan: zero figures and ``refusal`` "No
        active loan"."""
        amt = to_uint(amount, "amount")
        who = self._account if user is None else to_address(user, "user")
        info, vault = self._ctx(main, vault)
        entry = None if self._listed(info, vault) else self._not_on_vault(info, vault)
        p = self._add_plan(info, vault, entry, who, amt)
        added, fee, err = p["added_value"], p["fee"], p["error"]
        return {"main": info.main, "vault": vault, "hub": p["hub"], "hub_id": p["hub_id"],
                "new_collateral_value": pair_amount(p["new_collateral_value"], info),
                "added_value": pair_amount(added, info), "fee": pair_amount(fee, info),
                "borrowed": pair_amount(added - fee if added > fee else 0, info), "days_left": p["days_left"],
                "possible": err is None, "refusal": err.rule if err is not None else None}

    def get_available_stasis(self, user: Optional[str] = None, main: Optional[str] = None,
                             vault: Optional[str] = None) -> Amount:
        """Collateral not yet pledged: what ``user`` (default the signer) can still borrow against, main units."""
        who = self._account if user is None else to_address(user, "user")
        info, vault = self._ctx(main, vault)
        raw = self._contract(vault, "Vault").functions.getAvailableStasis(_cs(info.main), who).call()
        return main_amount(raw, info)

    def get_vault_totals(self, main: Optional[str] = None, vault: Optional[str] = None) -> Dict[str, Any]:
        """The main's totals on the vault: staked, pledged, rewards held, main free to pledge, and a raw reward
        index."""
        info, vault = self._ctx(main, vault)
        vf = self._contract(vault, "Vault").functions
        total_staked, total_pledged, rewards_held, acc = vf.pools(_cs(info.main)).call()
        idle = vf.totalStasisAvailable(_cs(info.main)).call()

        def m(raw: int) -> Amount:
            return main_amount(raw, info)

        return {
            "main": info.main, "vault": vault,
            "total_staked": m(total_staked), "total_pledged": m(total_pledged), "rewards_held": m(rewards_held),
            "total_stasis_available": m(idle), "acc_reward_per_stake": acc,
        }

    def get_loan_owner(self, hub_id: Number, main: Optional[str] = None,
                       hub: Optional[str] = None, vault: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The staker and main behind vault loan ``hub_id`` on ``hub`` (default: ``get_loan_hub()``): {user, main};
        None when the vault has no open loan with that id there (also after a repay or a liquidation)."""
        hid = to_uint(hub_id, "hubId")
        given = None if hub is None else to_address(hub, "hub")
        _, vault = self._ctx(main, vault)
        vf = self._contract(vault, "Vault").functions
        h = given if given is not None else vf.loanHub().call()
        user, owner_main = vf.loanOwner(h, hid).call()
        return None if is_zero_address(user) else {"user": user, "main": owner_main}

    def get_min_stake(self, main: Optional[str] = None, vault: Optional[str] = None) -> Amount:
        """Minimum stake, main units."""
        info, vault = self._ctx(main, vault)
        return main_amount(self._contract(vault, "Vault").functions.minStake().call(), info)

    def get_loan_hub(self, main: Optional[str] = None, vault: Optional[str] = None) -> str:
        """The loan hub new vault loans open on; an open loan stays on its position's ``hub``."""
        _, vault = self._ctx(main, vault)
        return self._contract(vault, "Vault").functions.loanHub().call()


    def _ctx(self, main: Optional[str], vault: Optional[str] = None, entry: bool = False) -> Tuple[MainInfo, str]:
        """The main and the vault to act on: ``vault`` when given, else the main's current vault. ``entry`` (stake /
        borrow / add_to_loan): a main without a vault, or an override that is not its current vault, is refused. The
        override is checked before any read."""
        override = None if vault is None else self._check_vault(vault)
        info = self.client.mains.default() if main is None else self.client.mains.get(main)
        if entry and (is_zero_address(info.vault) or (override is not None and not self._listed(info, override))):
            raise self._not_on_vault(info, override)
        return info, override if override is not None else self._require(info.vault, "Vault")

    @staticmethod
    def _not_on_vault(info: MainInfo, vault: Optional[str]) -> ValidationError:
        """"Main not on this vault": an entry on a main with no vault, or on another vault than its current one."""
        return ValidationError(
            "Main not on this vault",
            f"SWAP lists no staking vault for {info.symbol} ({info.main})." if is_zero_address(info.vault) else
            f"SWAP lists {info.vault} as the vault of {info.symbol}, not {vault}: stake, borrow and add_to_loan "
            "only run there.")

    def _borrow_plan(self, info: MainInfo, vault: str, entry: Optional[ValidationError], who: str, amt: int,
                     days: int) -> Dict[str, Any]:
        """What ``borrow(amt, days)`` by ``who`` would do now: {hub, value, fee, minimum_loan (raw pair asset), error
        (the first rule it would fail, in the order ``borrow`` lists them, or None)}; ``entry`` is the "Main not on this
        vault" refusal, or None. The issuer pause is read only when the rules before it pass."""
        m = _cs(info.main)
        vf = self._contract(vault, "Vault").functions
        mf = self._contract(m, "MainToken").functions
        live = is_pair_active(self.client, m)
        pos = self._raw_position(vault, m, who)
        available = vf.getAvailableStasis(m, _cs(who)).call()
        idle = vf.totalStasisAvailable(m).call()
        hub = vf.loanHub().call()
        minimum_loan = mf.minimumLoan().call()
        static_fee = mf.staticFeePercentage().call()
        main_hub = mf.LOAN().call()
        min_d, max_d = self._day_range(m)
        value = 0 if amt == 0 else mf.getColleteralValue(amt, m).call()
        hf = self._contract(hub, "LoanHub").functions
        hooked = hf.liquidationHook(_cs(vault)).call()
        eco = hf.ecosystems(m).call()
        in_range = min_d <= days <= max_d
        try:
            dynamic = mf.getDynamicFee(int(value), int(days)).call()
        except Exception:  # noqa: BLE001
            if in_range:
                raise
            dynamic = 0
        fee = value * static_fee // 10_000 + dynamic
        error = entry
        if error is not None:
            pass
        elif pos["has_active_loan"]:
            error = ValidationError("Position active. Use addToLoan", "This position already has a loan; use addToLoan.")
        elif not hooked:
            error = ValidationError("Hook not registered",
                                    f"The loan hub {hub} is not set up for vault {vault}: borrowing is not available "
                                    "there.")
        elif amt == 0:
            error = ValidationError("Zero amount", "Amount must be greater than 0.")
        elif amt > available:
            error = ValidationError("Insufficient collateral value",
                                    f"Only {available} raw {info.symbol} is available to borrow against, got {amt}.")
        elif amt > idle:
            error = ValidationError("Contract low on liquidity",
                                    f"The vault has only {idle} raw {info.symbol} free to pledge.")
        elif not live:
            error = pair_not_active(info.main, info.symbol)
        elif not eco[2]:
            error = ValidationError("Ecosystem inactive", f"Main {info.main} is not active on the loan hub {hub}.")
        elif main_hub.lower() != hub.lower():
            error = ValidationError("Unauthorized caller",
                                    f"Main {info.main} takes new loans from hub {main_hub}, not the vault's hub {hub}.")
        elif not in_range:
            error = ValidationError("incorrect loan duration", f"Days must be {min_d}-{max_d} on this main, got {days}.")
        elif value < minimum_loan:
            error = ValidationError("Minimum loan amount not met",
                                    f"Collateral value {value} is below the minimum loan {minimum_loan} (raw "
                                    f"{info.pair_symbol}).")
        elif is_asset_paused(self.client, info.pair_asset):
            error = pair_asset_paused(info.pair_asset, info.pair_symbol, "no loan can pay it out")
        elif fee >= value:
            error = ValidationError("No stable received",
                                    f"The loan fees would take the whole collateral value {value} (raw "
                                    f"{info.pair_symbol}); borrow for fewer days.")
        return {"hub": hub, "value": value, "fee": fee, "minimum_loan": minimum_loan, "error": error}

    def _add_plan(self, info: MainInfo, vault: str, entry: Optional[ValidationError], who: str,
                  amt: int) -> Dict[str, Any]:
        """What ``add_to_loan(amt)`` by ``who`` would do in the next block (latest + 1 s): {hub, hub_id,
        new_collateral_value, added_value, fee (raw pair asset; all 0 without a loan), days_left, error (the first rule
        it would fail, in the order ``add_to_loan`` lists them, or None)}; ``entry`` is the "Main not on this vault"
        refusal, or None. The issuer pause is read only when the rules before it pass. The days left (and the fee quoted
        on them) count from the next block, as "Duration too short" does (a later block only leaves fewer days). "Fees
        exceed value" is checked on the days left at the latest block: on a chain with about 10 blocks a second the add
        can mine in a block with the same time, one more whole day (a higher fee) when the loan ends a whole number of
        days after it."""
        m = _cs(info.main)
        vf = self._contract(vault, "Vault").functions
        live = is_pair_active(self.client, m)
        pos = self._raw_position(vault, m, who)
        available = vf.getAvailableStasis(m, _cs(who)).call()
        idle = vf.totalStasisAvailable(m).call()
        f = {"active": False, "time_ok": False, "min_days": 0, "days_left": 0, "new_collateral_value": 0,
             "full_amount": 0, "added_value": 0, "fee": 0, "fee_most": 0}
        if pos["has_active_loan"]:
            loan = self._hub_loan(vault, pos)
            ef = self._contract(loan["ecosystem"], "MainToken").functions
            min_days = ef.minDaysLoan().call()
            latest = self.web3.eth.get_block("latest")["timestamp"]
            nxt = latest + 1
            new_value = ef.getColleteralValue(int(loan["collateral_amount"] + amt), _cs(loan["token"])).call()
            static_fee = ef.staticFeePercentage().call()
            liq = loan["liquidation_time"]
            days_left = (liq - nxt) // _DAY if liq > nxt else 0
            most_days = (liq - latest) // _DAY if liq > latest else 0
            added = new_value - loan["full_amount"] if new_value > loan["full_amount"] else 0

            def dynamic_for(n: int) -> int:
                return 0 if added == 0 else ef.getDynamicFee(int(added), int(n)).call()

            static_part = added * static_fee // 10_000
            fee = static_part + dynamic_for(days_left)
            fee_most = fee if most_days == days_left else static_part + dynamic_for(most_days)
            f = {"active": loan["active"], "time_ok": nxt + min_days * _DAY < liq, "min_days": min_days,
                 "days_left": days_left, "new_collateral_value": new_value, "full_amount": loan["full_amount"],
                 "added_value": added, "fee": fee, "fee_most": fee_most}
        error = entry
        if error is not None:
            pass
        elif not pos["has_active_loan"]:
            error = ValidationError("No active loan", "This position has no loan; use borrow.")
        elif amt == 0:
            error = ValidationError("Zero amount", "Amount must be greater than 0.")
        elif amt > available:
            error = ValidationError("Insufficient collateral value",
                                    f"Only {available} raw {info.symbol} is available, got {amt}.")
        elif amt > idle:
            error = ValidationError("Contract low on liquidity",
                                    f"The vault has only {idle} raw {info.symbol} free to pledge.")
        elif not live:
            error = pair_not_active(info.main, info.symbol)
        elif not f["active"]:
            error = ValidationError("Loan not active", f"Hub loan {pos['hub_id']} is not active.")
        elif not f["time_ok"]:
            error = ValidationError("Duration too short",
                                    f"Less than {f['min_days']} days left on the loan; extend it first.")
        elif f["new_collateral_value"] <= f["full_amount"]:
            error = ValidationError("No borrowing power",
                                    f"The collateral after the add is worth {f['new_collateral_value']}, not above "
                                    f"the debt {f['full_amount']} (raw {info.pair_symbol}).")
        elif f["fee_most"] >= f["added_value"]:
            error = ValidationError("Fees exceed value",
                                    f"The fees on the added value {f['added_value']} (raw {info.pair_symbol}) would "
                                    "take all of it.")
        elif is_asset_paused(self.client, info.pair_asset):
            error = pair_asset_paused(info.pair_asset, info.pair_symbol, "no top-up can pay it out")
        return {"hub": pos["hub"], "hub_id": pos["hub_id"], "new_collateral_value": f["new_collateral_value"],
                "added_value": f["added_value"], "fee": f["fee"], "days_left": f["days_left"], "error": error}

    @staticmethod
    def _listed(info: MainInfo, vault: str) -> bool:
        """``vault`` is the main's current vault."""
        return not is_zero_address(info.vault) and info.vault.lower() == vault.lower()

    @staticmethod
    def _check_vault(vault: Any) -> str:
        """A vault the caller typed (``to_address``, rule ``vault``), checksummed; the zero address is refused."""
        v = to_address(vault, "vault")
        if is_zero_address(v):
            raise ValidationError("vault", f"vault must be a non-zero address, got {v}.")
        return v


    def _day_range(self, main: str) -> Tuple[int, int]:
        fns = self._contract(main, "MainToken").functions
        return fns.minDaysLoan().call(), fns.maxDaysLoan().call()

    def _position_reads(self, info: MainInfo, vault: str, who: str) -> List[Tuple[Any, str]]:
        """The ``_POSITION_READS`` reads behind one position, in ``_to_position``'s order (for one multicall)."""
        vf = self._contract(vault, "Vault").functions
        m_addr = _cs(info.main)
        return [(getattr(vf, name)(m_addr, who), "Vault")
                for name in ("positions", "earned", "collateralOf", "freeToUnstake", "getAvailableStasis")]

    def _to_position(self, info: MainInfo, vault: str, who: str, res: List[Any]) -> Dict[str, Any]:
        r = res[0]
        p = {"staked": r[0], "pledged": r[1], "hub_id": r[2], "checkpoint": r[3], "pending": r[4], "hub": r[5],
             "has_active_loan": not is_zero_address(r[5])}
        earned, collateral, free, available = res[1:]

        def m(raw: int) -> Amount:
            return main_amount(raw, info)

        return {
            "main": info.main, "vault": vault, "user": who,
            "staked": m(p["staked"]), "pledged": m(p["pledged"]), "hub_id": p["hub_id"],
            "has_active_loan": p["has_active_loan"], "checkpoint": p["checkpoint"], "pending": m(p["pending"]),
            "hub": p["hub"],
            "earned": m(earned), "collateral_of": m(collateral), "free_to_unstake": m(free), "available_stasis": m(available),
        }

    def _raw_position(self, vault: str, main: str, user: str) -> Dict[str, Any]:
        """The raw position; a loan is open when hub is not the zero address."""
        r = self._contract(vault, "Vault").functions.positions(_cs(main), _cs(user)).call()
        return {"staked": r[0], "pledged": r[1], "hub_id": r[2], "checkpoint": r[3], "pending": r[4], "hub": r[5],
                "has_active_loan": not is_zero_address(r[5])}

    def _hub_loan(self, vault: str, pos: Dict[str, Any]) -> Dict[str, Any]:
        """The position's loan details."""
        r = self._contract(pos["hub"], "LoanHub").functions.getUserLoanDetails(vault, pos["hub_id"]).call()
        return {
            "hub_id": r[0], "ecosystem": r[1], "core_loan_id": r[2], "collateral_token": r[3], "token": r[4],
            "collateral_amount": r[5], "liquidated_amount": r[6], "full_amount": r[7], "borrowed_amount": r[8],
            "liquidation_time": r[9], "liquidation_claim": r[10], "is_liquidated": r[11], "active": r[12],
            "creation_time": r[13],
        }

    def _extension_eligibility(self, pos: Dict[str, Any], main: str, core_loan_id: int, days: int, pay: bool,
                               refinance: bool) -> Dict[str, Any]:
        """The extension preview for the position's loan."""
        possible, fee, extra_out = self._contract(main, "MainToken").functions.extensionEligibility(
            pos["hub"], int(core_loan_id), int(days), False, pay, refinance).call()
        return {"possible": possible, "fee": fee, "extra_out": extra_out}

    def _check_balance(self, token: str, amount: int, symbol: str) -> None:
        bal = self._balance_of(token)
        if bal < amount:
            raise ValidationError("balance", f"Balance of {symbol} is {bal}, below the {amount} needed.")
