"""``client.loans``: loans against a main or a factory token.

Borrow the pair asset against a main or a factory token, then repay (in full or in part, in the stable or another
token), extend, top up, sell part, or claim what is left after a liquidation. Loans are made by the BASIS protocol. A
loan can be liquidated once its ``liquidation_time`` has passed. Hub ids are per wallet (1..``get_user_loan_count``;
``core_loan_id`` is the loan's id on its main, the ``loan_id`` ``client.liquidation`` reads take with the loan hub as
``user``). A wallet has ONE open loan per (main, collateral token): a second borrow there is refused, ``increase_loan``
adds to the open one. A vault position's loan belongs to the vault, not the wallet. Every method works on the address
book's loan hub; ``at_hub(hub)`` reaches the loans on an older hub.

Inputs are raw ints (collateral in the token's units). Result dicts (snake_case keys):
  hub loan details (``get_user_loan_details``): hub_id, ecosystem (the main), core_loan_id, collateral_token, token,
    collateral_amount, liquidated_amount, full_amount (debt, raw pair asset), borrowed_amount (raw pair asset),
    liquidation_time (unix seconds), liquidation_claim, is_liquidated, active, creation_time.
  ecosystem: stable (the main's pair asset), swap_contract (the swap its partial sells and repay-withs use), active.
  loan terms: main, min_days, max_days, minimum_loan (Amount, pair asset), static_fee_bps (on the collateral value,
    bps), dynamic_fee_percentage (daily fee, in units of 0.001% of the amount per day).
  loan quote: main, collateral_value (= the debt), fee (the loan fee), borrowed (Amounts, pair asset), meets_minimum,
    fees_exceed_value (fee >= value: take_loan refuses "No stable received"), possible (take_loan would go through
    now), refusal (the rule it would be refused with, in its order, or None: "cannot be zero", "pair not active" (the
    main's pair is paused), "Ecosystem inactive", "Unauthorized caller", "Open loan: increase it", "incorrect loan
    duration", "Minimum loan amount not met", "pair asset paused" (its issuer paused the pair asset the loan pays
    out), "No stable received"; the balance is not checked).
  increase quote (``quote_increase_loan``, raw pair asset): hub_id, new_collateral_value, added_value (0 without
    borrowing power), fee, borrowed, days_left, possible, refusal (in ``increase_loan``'s order, or None: "pair not
    active", "Duration too short", "Zero amount", "No borrowing power", "Fees exceed value", "pair asset paused" (read
    only when nothing else refuses); the balance is not checked).
  extension quote: possible, fee, extra_out (raw pair asset).
  user loans (``get_user_loans``): hub loan details plus hub (the hub the loan is on), time_of_closure (unix s, 0 while
    open) and claim_deadline (liquidated: time_of_closure + 365 days, from then on an unclaimed leftover can be taken;
    else 0).
  Write results carry the figures read from the transaction (None when the event is missing; ``warnings`` says why),
  money figures as ``Amount`` objects (the stable in the loan main's pair asset, collateral in the collateral token):
    take_loan: hub_id, core_loan_id, borrowed. repay_loan: closed, repaid, collateral_released. extend_loan:
    new_liquidation_time, new_full_amount, extra_out (never None: 0 without a refinance). increase_loan: borrowed.
    claim_liquidation: claimed. partial_sell_loan: payout (net pair asset, as ``trading.partial_sell_leverage``).
  repay preview (``get_repay_preview``): hub_id, active (a closed loan keeps its old debt / collateral figures and
    is never ``allowed``), debt, minimum_loan, max_partial (debt - minimum_loan, 0 = only in full), amount (the stable
    taken: min(amount, debt)), full, allowed (an open loan, and a full repay or one leaving at least minimum_loan),
    collateral_released, collateral_left, debt_left.
  repay-with quote (``quote_repay_loan_with``): hub_id, token_in, stable, debt, amount_in, stable_out (net of the
    route fee), covers (stable_out >= debt), refund, collateral_released.
  repay_loan_with result: the repay_loan figures plus stable_out and refund. All of stable_out goes
    to the debt: a route paying more than quoted repays more on a partial (repaid = stable_out, more collateral back,
    refund 0); only what is above the whole debt is refunded.
  hub partial-sell quote: gross (before the exit fee), taxable (= gross on a hub loan), tax_bps (the borrower's own sell
    rate), fee (tax_bps on the whole sale), net (what the wallet gets, what ``min_out`` binds), collateral_sold,
    debt_repaid.
"""

from typing import Any, Dict, List, Optional, Union

from web3 import Web3

from ..config.addresses import ZERO_ADDRESS, is_zero_address
from ..core.amounts import amount_of, opt_amount, pair_amount_of
from ..core.base import BaseModule
from ..core.errors import ValidationError
from ..core.events import add_missing_event_warning
from ..core.loans import (check_deadline, check_explicit_min, core_extend_refusal, is_asset_paused, is_pair_active,
                          pair_asset_paused, pair_not_active,
                          quote_hop_within_caps, quote_partial_sell, read_core_loan, read_repay_sizing, size_repay_with)
from ..core.mains import pair_amount
from ..core.multicall import multicall
from ..core.revert import decode_revert
from ..core.units import max_with_slippage, opt_uint, to_address, to_bool, to_uint, validate_slippage_bps
from .liquidation import CLAIM_PERIOD_SECONDS

Number = Union[int, float]

DAY = 86_400
MAX_UINT256 = 2 ** 256 - 1
REPAY_WITH_BUFFER_BPS = 50
REPAY_WITH_DEADLINE = 600
LOANS_PAGE = 100

_DETAIL_FIELDS = (
    "hub_id", "ecosystem", "core_loan_id", "collateral_token", "token", "collateral_amount", "liquidated_amount",
    "full_amount", "borrowed_amount", "liquidation_time", "liquidation_claim", "is_liquidated", "active",
    "creation_time",
)



def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


def _eq(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def _is_int(v: Any) -> bool:
    """JS ``Number.isInteger``: an int (not bool) or a whole float."""
    if isinstance(v, bool):
        return False
    return isinstance(v, int) or (isinstance(v, float) and v.is_integer())


def _check_sell_pct(percentage: Any) -> None:
    """Hub partial sells take 10-100 % in steps of 10, checked in order: "% by 10", then "% range"."""
    if not _is_int(percentage) or percentage % 10 != 0:
        raise ValidationError("% by 10", f"percentage must be a multiple of 10 (10-100), got {_js(percentage)}.")
    if percentage < 10 or percentage > 100:
        raise ValidationError("% range", f"percentage must be 10-100, got {_js(percentage)}.")


def _check_hub(hub: Any) -> str:
    """A hub given to ``at_hub``: an address (``to_address``), not the zero address (rule "hub"), checksummed."""
    a = to_address(hub, "hub")
    if is_zero_address(a):
        raise ValidationError("hub", f"hub must be a non-zero address, got {hub}.")
    return a


def _opt_user(user: Optional[str]) -> Optional[str]:
    """An optional wallet argument through ``to_address`` (rule ``user``), None when omitted."""
    return None if user is None else to_address(user, "user")


def _js(v: Any) -> str:
    """``v`` as TS ``String(v)`` shows it in messages."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


class LoansModule(BaseModule):
    """``client.loans``: borrow the pair asset against a main or a factory token. Loans are made by the BASIS
    protocol."""

    def __init__(self, client, hub: Optional[str] = None):
        super().__init__(client)
        self._hub_override: Optional[str] = None if hub is None else _check_hub(hub)

    @property
    def deployed(self) -> bool:
        """True when the module's hub has an address (the address book's, or the one given to ``at_hub``)."""
        return not is_zero_address(self._hub_override or self.client.addresses["loanHub"])

    @property
    def _hub_address(self) -> str:
        return self._require(self._hub_override or self.client.addresses["loanHub"], "LoanHub")

    def at_hub(self, hub: str) -> "LoansModule":
        """This module on another loan hub, e.g. an older one (the address book follows the current one). An older
        hub keeps serving its own loans: every read here (``get_user_loans``,
        ``get_user_loan_details``, the quotes, ...) and every write on an existing loan (``repay_loan``,
        ``repay_loan_with``, ``extend_loan``, ``increase_loan``, ``partial_sell_loan``, ``claim_liquidation``) then runs
        on ``hub``. Hub ids count per hub. New loans open only on a main's current hub: ``take_loan`` elsewhere is
        refused ("Unauthorized caller"). Rule ``hub`` (not a non-zero address). No RPC."""
        return LoansModule(self.client, _check_hub(hub))

    @property
    def _hub(self):
        return self._contract(self._hub_address, "LoanHub")


    def take_loan(self, collateral: str, amount: int, number_of_days: Number) -> Dict[str, Any]:
        """Borrow against ``amount`` (raw) of ``collateral`` for ``number_of_days``. The main is derived from the
        collateral (the collateral itself, or the main it trades against). Checks the pair ("pair not active" while it
        is paused), that the main takes loans on this hub ("Ecosystem inactive", "Unauthorized caller"), one open loan
        per (main, collateral) ("Open loan: increase it": use ``increase_loan`` on it), the days range, the minimum
        value ``minimum_loan``, "pair asset paused" (its issuer paused the pair asset the loan pays out), fees below the
        value ("No stable received") and the balance; approves the collateral to the loan hub. Pays out the pair asset.
        Returns the write result plus ``hub_id``, ``core_loan_id`` and ``borrowed``."""
        hub = self._hub_address
        amt = to_uint(amount, "amount")
        if amt == 0:
            raise ValidationError("cannot be zero", "Collateral amount must be greater than 0.")
        days = to_uint(number_of_days, "numberOfDays")
        collateral = to_address(collateral, "collateral")
        me = self._account
        plan = self._loan_plan(collateral, amt, days, me)
        if plan["error"] is not None:
            raise plan["error"]
        info = plan["info"]
        bal = self._balance_of(collateral)
        if bal < amt:
            raise ValidationError("balance", f"Balance of {collateral} is {bal}, below the {amt} needed.")

        self._ensure_allowance(collateral, hub, amt)
        tx = self._send(self._hub.functions.takeLoan(info.main, _cs(collateral), amt, days), "LoanHub")
        ev = self._expect_event(tx, self._hub, "LoanCreated", "LoanHub", ("hub_id", "core_loan_id", "borrowed"))
        return {**tx, "hub_id": ev["hubId"] if ev else None, "core_loan_id": ev["coreId"] if ev else None,
                "borrowed": opt_amount(pair_amount_of(info), ev["borrowed"] if ev else None)}

    def repay_loan(self, hub_id: Number, amount: Optional[int] = None) -> Dict[str, Any]:
        """Repay a hub loan with its stable (the main's pair asset); nothing is sold. ``amount`` (raw) None = in full
        (exactly the debt is taken and all collateral comes back). Under the debt: the debt drops by it, the repaid
        share of the collateral comes back and the loan stays open; what is left must stay at least the main's
        ``minimum_loan`` ("Repay in full"; ``get_repay_preview`` gives the largest partial). Then "pair asset paused"
        (its issuer paused the stable: the debt cannot be paid in) and the balance. Approves the stable taken to the
        loan hub. Returns the write result plus ``closed``, ``repaid`` and ``collateral_released``."""
        hid = to_uint(hub_id, "hubId")
        hub = self._hub_address
        amt = MAX_UINT256 if amount is None else to_uint(amount, "amount")
        if amt == 0:
            raise ValidationError("Zero amount", "amount must be greater than 0 (omit it to repay in full).")
        d = self.get_user_loan_details(self._account, hid)
        if not d["active"]:
            raise ValidationError("Core loan inactive", f"Hub loan {hid} is not active (repaid, sold or liquidated).")
        p = self._repay_preview(d, amt)
        if not p["allowed"]:
            raise ValidationError(
                "Repay in full",
                f"Repaying {amt} of {p['debt']} would leave {p['debt_left']}, under the minimum loan "
                f"{p['minimum_loan']}: repay at most {p['max_partial']}, or in full.",
            )
        stable = self.get_ecosystem(d["ecosystem"])["stable"]
        self._refuse_paused(d, stable, "the loan cannot be repaid in it now")
        bal = self._balance_of(stable)
        if bal < p["amount"]:
            raise ValidationError("balance", f"Balance of {stable} is {bal}, below the {p['amount']} to repay.")
        fmt = self._loan_formats(d)
        self._ensure_allowance(stable, hub, p["amount"])
        tx = self._send(self._hub.functions.repayLoan(hid, amt), "LoanHub")
        return {**tx, **self._repaid_figures(tx, d, stable, fmt)}

    def repay_loan_with(self, hub_id: Number, token_in: str, amount_in: Optional[int] = None,
                        min_stable_out: Optional[int] = None, close: Optional[bool] = None,
                        slippage_bps: Optional[int] = None, buffer_bps: Optional[int] = None,
                        deadline: Optional[int] = None) -> Dict[str, Any]:
        """Repay a hub loan with another token (e.g. OUT, or USDG on an NVDA+ loan): ``token_in`` is converted to the
        stable through an outside route (net of the route fee), all of it goes to the debt (a partial repay releases its
        share of the collateral) and the stable left over is refunded. ``amount_in`` None: sized to repay everything (the amount quoting the debt, plus ``buffer_bps``, default
        50 = 0.5%) and ``close`` defaults to True; an explicit ``amount_in`` whose quote covers the debt also defaults
        ``close`` to True (all or nothing, so a move within slippage cannot leave the loan part-repaid), else False.
        ``min_stable_out`` None: the quote minus ``slippage_bps`` (default ``client.slippage_bps["trade"]``), and at least
        the debt with ``close`` or a covering quote; an explicit one above the quote is refused ("Min out"). A partial
        repay must stay a valid partial even if the route pays up to the slippage more than quoted ("Repay in full").
        Checks the deadline first (default latest block + 600 s; an explicit one before the latest block time:
        "Deadline"), then the route ("No swap for ecosystem", "no executor", "no route"), "pair asset paused" (its
        issuer paused the stable the debt is paid in), an explicit ``amount_in`` against the balance and against what
        the route's caps allow a swap sent now ("above cap": what other swaps left of them in the current window on
        Robinhood Chain, where waiting about 12 seconds can clear it), the explicit minimum and the balance; approves
        ``token_in`` to the loan hub. Returns the ``repay_loan`` figures plus ``stable_out`` and ``refund``."""
        hid = to_uint(hub_id, "hubId")
        explicit = opt_uint(min_stable_out, "minStableOut")
        opt_deadline = opt_uint(deadline, "deadline")
        close_opt = None if close is None else to_bool(close, "close")
        if buffer_bps is not None:
            validate_slippage_bps(buffer_bps, "bufferBps")
        if slippage_bps is not None:
            validate_slippage_bps(slippage_bps)
        token_in = to_address(token_in, "tokenIn")
        hub = self._hub_address
        me = self._account
        dl = self._send_deadline(opt_deadline)
        d = self.get_user_loan_details(me, hid)
        if not d["active"]:
            raise ValidationError("Core loan inactive", f"Hub loan {hid} is not active (repaid, sold or liquidated).")
        sizing = amount_in is None
        route = self._repay_route(d, token_in)
        self._refuse_paused(d, route["stable"], "the loan cannot be repaid in it now")
        if not sizing:
            amt = to_uint(amount_in, "amountIn")
            if amt == 0:
                raise ValidationError("Zero amount", "amountIn must be greater than 0.")
            have = self._balance_of(token_in)
            if have < amt:
                raise ValidationError("balance", f"Balance of {token_in} is {have}, below the {amt} needed.")
        q = self._repay_with_quote(d, token_in, amount_in, buffer_bps, route)
        do_close = (sizing or q["covers"]) if close_opt is None else close_opt
        if do_close and not q["covers"]:
            raise ValidationError("Not enough to close",
                                  f"{q['amount_in']} of {token_in} converts to {q['stable_out']}, under the "
                                  f"{q['debt']} debt.")
        if not q["covers"]:
            p = self._repay_preview(d, q["stable_out"])
            high = q["stable_out"] if route["direct"] else max_with_slippage(
                q["stable_out"], self.client.slippage_bps["trade"] if slippage_bps is None else slippage_bps)
            if not p["allowed"] or high > p["max_partial"]:
                raise ValidationError(
                    "Repay in full",
                    f"{q['amount_in']} of {token_in} converts to {q['stable_out']} (up to {high} after slippage), which "
                    f"could leave under the minimum loan {p['minimum_loan']} of the {p['debt']} debt: repay at most "
                    f"{p['max_partial']} worth (less the slippage), or everything.",
                )
        m = self._resolve_min(explicit, slippage_bps, lambda: q["stable_out"], "trade", "minStableOut")
        check_explicit_min(explicit, q["stable_out"], "minStableOut", f"what {q['amount_in']} of {token_in} converts to")
        if explicit is None and (do_close or q["covers"]) and m < q["debt"]:
            m = q["debt"]
        bal = self._balance_of(token_in)
        if bal < q["amount_in"]:
            raise ValidationError("balance", f"Balance of {token_in} is {bal}, below the {q['amount_in']} needed.")
        fmt = self._loan_formats(d)
        self._ensure_allowance(token_in, hub, q["amount_in"])
        tx = self._send(self._hub.functions.repayLoanWith(hid, token_in, q["amount_in"], m, do_close, dl), "LoanHub")
        ev = self._expect_event(tx, self._hub, "RepaidWith", "LoanHub", ("stable_out", "refund"))
        return {**tx, **self._repaid_figures(tx, d, route["stable"], fmt),
                "stable_out": fmt["pair"](ev["stableOut"]) if ev else None,
                "refund": fmt["pair"](ev["refund"]) if ev else None}

    def extend_loan(self, hub_id: Number, number_of_days: Number, pay_in_pair_asset: bool,
                    refinance: bool) -> Dict[str, Any]:
        """Extend a hub loan by ``number_of_days`` (``get_loan_terms`` ``min_days..max_days``, or 0 with
        ``refinance``). The days count from the due date, or from the time of the transaction when the loan is past due
        (not yet liquidated): the new due date is the later of the two plus ``number_of_days``.
        ``pay_in_pair_asset``: the fee is paid from the wallet (the stable is approved to the loan hub); otherwise it is
        added to the debt, or with ``refinance`` the surplus collateral value is borrowed out.
        ``pay_in_pair_asset and refinance`` is refused. A paused pair is refused next ("pair not active"), and so is a
        stable its issuer paused ("pair asset paused"), before any approval. Then, in order: "Must be refinance if not
        extending time", "incorrect loan duration", "Loan not active", then "Not enough collateral to cover fees" (add
        to debt) or "No equity", "Minimum surplus not met", "Time remaining too short", "Fees exceed surplus value"
        (refinance). A pay-in passes once the days and the loan are valid. Returns the write result plus
        ``new_liquidation_time``, ``new_full_amount`` and the refinance payout ``extra_out``."""
        pay_in_pair_asset = to_bool(pay_in_pair_asset, "payInPairAsset")
        refinance = to_bool(refinance, "refinance")
        if pay_in_pair_asset and refinance:
            raise ValidationError("Pay in stable or refinance, not both",
                                  "Choose either payInPairAsset or refinance, not both.")
        hid = to_uint(hub_id, "hubId")
        hub = self._hub_address
        me = self._account
        days = to_uint(number_of_days, "numberOfDays")
        d = self.get_user_loan_details(me, hid)
        q = self._extension_quote(d, days, pay_in_pair_asset, refinance)
        if not is_pair_active(self.client, d["ecosystem"]):
            raise pair_not_active(d["ecosystem"])
        stable = self.get_ecosystem(d["ecosystem"])["stable"]
        self._refuse_paused(d, stable, "no extension can take its fee")
        refusal = core_extend_refusal(self.client, d["ecosystem"], d, days, pay_in_pair_asset, refinance, q)
        if refusal is not None:
            raise refusal
        pair = pair_amount_of(self.client.mains.get(d["ecosystem"]))
        if pay_in_pair_asset and q["fee"] > 0:
            bal = self._balance_of(stable)
            if bal < q["fee"]:
                raise ValidationError("balance", f"Balance of {stable} is {bal}, below the {q['fee']} fee.")
            self._ensure_allowance(stable, hub, q["fee"])
        tx = self._send(self._hub.functions.extendLoan(hid, days, pay_in_pair_asset, refinance), "LoanHub")
        main = self._contract(d["ecosystem"], "MainToken")
        ev = next((e for e in self._events(tx, main, "LoanExtended")
                   if _eq(e["user"], hub) and e["loanId"] == d["core_loan_id"]), None)
        if ev is None:
            add_missing_event_warning(tx, "MainToken", "LoanExtended", ("new_liquidation_time", "new_full_amount"))
        extra_out = self._transferred(tx, stable, hub, me) if refinance else 0
        return {**tx, "new_liquidation_time": ev["newLiquidationTime"] if ev else None,
                "new_full_amount": pair(ev["newFullAmount"]) if ev else None, "extra_out": pair(extra_out)}

    def increase_loan(self, hub_id: Number, amount_to_add: int) -> Dict[str, Any]:
        """Add ``amount_to_add`` (raw) of the loan's collateral token and borrow the added value (minus fees) in the
        pair asset. Rules: "Loan not active", "pair not active" (the main's pair is paused), then in order: "Duration
        too short" (needs more than the main's ``min_days`` days left), "Zero amount", "No borrowing power", "Fees
        exceed value" (``quote_increase_loan`` previews the payout), then "pair asset paused" (its issuer paused the
        pair asset the top-up pays out) and "balance". Approves the collateral to the loan hub; after an approval it
        sent, "Duration too short" is checked again before the increase is sent (fewer days are left by then). Returns
        the write result plus ``borrowed``."""
        hid = to_uint(hub_id, "hubId")
        hub = self._hub_address
        amt = to_uint(amount_to_add, "amountToAdd")
        d = self.get_user_loan_details(self._account, hid)
        if not d["active"]:
            raise ValidationError("Loan not active", f"Hub loan {hid} is not active.")
        q = self._increase_quote(d, amt)
        if not q["live"]:
            raise pair_not_active(d["ecosystem"])

        def too_short() -> ValidationError:
            return ValidationError(
                "Duration too short",
                f"Hub loan {hid} needs more than {q['min_days']} days left to be increased; extend it first.",
            )
        if not q["time_ok"]:
            raise too_short()
        if amt == 0:
            raise ValidationError("Zero amount", "amountToAdd must be greater than 0.")
        if q["new_collateral_value"] <= d["full_amount"]:
            raise ValidationError(
                "No borrowing power",
                f"Adding {amt} collateral adds no borrowing power (value {q['new_collateral_value']} vs debt "
                f"{d['full_amount']}).",
            )
        if q["fee_most"] >= q["added_value"]:
            raise ValidationError(
                "Fees exceed value",
                f"The fees {q['fee_most']} on the added value {q['added_value']} (raw pair asset, {q['most_days']} "
                "days left) would take all of it.",
            )
        self._refuse_paused(d, None, "no top-up can pay it out")
        bal = self._balance_of(d["collateral_token"])
        if bal < amt:
            raise ValidationError("balance", f"Balance of {d['collateral_token']} is {bal}, below the {amt} needed.")
        pair = pair_amount_of(self.client.mains.get(d["ecosystem"]))
        if self._ensure_allowance(d["collateral_token"], hub, amt) is not None:
            now = self._pre_send_read(self._chain_time) + 1
            if not now + q["min_days"] * DAY < d["liquidation_time"]:
                raise too_short()
        tx = self._send(self._hub.functions.increaseLoan(hid, amt), "LoanHub")
        ev = self._expect_event(tx, self._hub, "LoanIncreased", "LoanHub", ("borrowed",))
        return {**tx, "borrowed": pair(ev["borrowed"]) if ev else None}

    def claim_liquidation(self, hub_id: Number) -> Dict[str, Any]:
        """Claim the collateral left over after a hub loan was liquidated (paid in the collateral token). A leftover
        still unclaimed 365 days after the liquidation can be taken, and once it is, this answers "Nothing to claim".
        Returns the write result plus ``claimed``."""
        hid = to_uint(hub_id, "hubId")
        d = self.get_user_loan_details(self._account, hid)
        if d["active"]:
            raise ValidationError("Loan is still active", f"Hub loan {hid} is still active.")
        if d["liquidation_claim"] == 0:
            why = (" (already claimed, or taken: unclaimed for more than 365 days after the liquidation)"
                   if d["is_liquidated"] else " (it was not liquidated)")
            raise ValidationError("Nothing to claim", f"Nothing to claim on hub loan {hid}{why}.")
        col = self._loan_formats(d)["col"]
        tx = self._send(self._hub.functions.claimLiquidation(hid), "LoanHub")
        ev = self._expect_event(tx, self._hub, "LiquidationClaimed", "LoanHub", ("claimed",))
        return {**tx, "claimed": col(ev["claimed"]) if ev else None}

    def partial_sell_loan(self, hub_id: Number, percentage: Number, min_out: Optional[int] = None,
                          slippage_bps: Optional[int] = None) -> Dict[str, Any]:
        """Sell ``percentage`` (10-100, step 10) of a hub loan's collateral: the same share of the debt is cleared, and
        the rest of the sale is paid out in the pair asset minus the exit fee (the wallet's own sell fee rate on the
        whole sale, read live; a fee-whitelisted wallet pays none). ``min_out`` binds what the wallet receives, after
        that fee (raw, never 0); None -> ``quote_partial_sell_loan(...)["net"]`` minus ``slippage_bps`` (default
        ``client.slippage_bps["trade"]``, 1%). 100% closes the loan. A paused pair cannot sell: "pair not active"; nor
        can a sale pay out a stable its issuer paused ("pair asset paused", after "No swap for ecosystem" and the
        minimum). Returns the write result plus ``payout`` (net of the exit fee)."""
        hid = to_uint(hub_id, "hubId")
        _check_sell_pct(percentage)
        pct = int(percentage)
        explicit = opt_uint(min_out, "minOut")
        me = self._account
        d = self.get_user_loan_details(me, hid)
        if not d["active"]:
            raise ValidationError("Loan not active", f"Hub loan {hid} is not active.")
        eco = self.get_ecosystem(d["ecosystem"])
        if not is_pair_active(self.client, d["ecosystem"]):
            raise pair_not_active(d["ecosystem"])
        if is_zero_address(eco["swap_contract"]):
            raise ValidationError("No swap for ecosystem", f"Main {d['ecosystem']} has no swap set on the loan hub.")
        m = self._resolve_min(explicit, slippage_bps, lambda: self._exit_quote(d, pct, me)["net"])
        self._refuse_paused(d, eco["stable"], "no sale can pay it out")
        pair = pair_amount_of(self.client.mains.get(d["ecosystem"]))
        tx = self._send(self._hub.functions.hubPartialLoanSell(hid, pct, m), "LoanHub")
        ev = self._expect_event(tx, self._hub, "PartialLoanSold", "LoanHub", ("payout",))
        return {**tx, "payout": pair(ev["received"]) if ev else None}


    def get_user_loan_details(self, user: str, hub_id: Number) -> Dict[str, Any]:
        """Hub loan ``hub_id`` of ``user``. Reverts "Loan does not exist" for an unknown id."""
        hid = to_uint(hub_id, "hubId")
        who = to_address(user, "user")
        c = self._hub
        try:
            r = c.functions.getUserLoanDetails(who, hid).call()
        except Exception as e:  # noqa: BLE001
            raise decode_revert(e, contract="LoanHub", function_name="getUserLoanDetails", abi=c.abi) from e
        return dict(zip(_DETAIL_FIELDS, r))

    def get_user_loan_count(self, user: Optional[str] = None) -> int:
        """Number of hub loans ``user`` (default the signer) opened. Ids run 1..count."""
        return self._hub.functions.userLoanCount(_opt_user(user) or self._account).call()

    def get_user_loans(self, user: Optional[str] = None, active_only: bool = False) -> List[Dict[str, Any]]:
        """Every hub loan of ``user`` (default the signer), ids 1..``get_user_loan_count``, oldest first: the hub
        details plus ``time_of_closure`` and ``claim_deadline`` (a liquidation leftover can be taken from then on).
        One multicall for the details and one for the closure times of the closed loans (per 100 loans).
        ``active_only``: open loans only. A wallet's own loans only: vault and vesting loans belong to those contracts
        (``client.vault``, ``client.vesting``). Each row names its ``hub``; the loans on an older hub are listed by
        ``at_hub(old_hub).get_user_loans()``."""
        active_only = to_bool(active_only, "activeOnly", False)
        who = _opt_user(user) or self._account
        hub = self._hub_address
        fns = self._hub.functions
        count = self.get_user_loan_count(who)
        rows: List[Dict[str, Any]] = []
        for start in range(1, count + 1, LOANS_PAGE):
            ids = range(start, min(start + LOANS_PAGE, count + 1))
            rows += [dict(zip(_DETAIL_FIELDS, r))
                     for r in multicall(self.client, [(fns.getUserLoanDetails(who, i), "LoanHub") for i in ids])]
        kept = [r for r in rows if r["active"]] if active_only else rows
        closed = [r for r in kept if not r["active"]]
        closed_at: Dict[int, int] = {}
        for i in range(0, len(closed), LOANS_PAGE):
            page = closed[i:i + LOANS_PAGE]
            closure = multicall(self.client, [
                (self._contract(r["ecosystem"], "MainToken").functions.loans(hub, r["core_loan_id"]), "MainToken")
                for r in page])
            closed_at.update({r["hub_id"]: c[11] for r, c in zip(page, closure)})
        out = []
        for r in kept:
            t = closed_at.get(r["hub_id"], 0)
            out.append({**r, "hub": hub, "time_of_closure": t,
                        "claim_deadline": t + CLAIM_PERIOD_SECONDS if r["is_liquidated"] else 0})
        return out

    def get_loan_ref(self, user: str, hub_id: Number) -> Dict[str, Any]:
        """Where hub loan ``hub_id`` of ``user`` lives: {ecosystem (the main), core_loan_id (the loan's id on it),
        collateral_token} (zero ecosystem = no such loan)."""
        hid = to_uint(hub_id, "hubId")
        ecosystem, core_loan_id, collateral_token = self._hub.functions.userLoans(to_address(user, "user"), hid).call()
        return {"ecosystem": ecosystem, "core_loan_id": core_loan_id, "collateral_token": collateral_token}

    def get_core_loan(self, user: str, hub_id: Number) -> Dict[str, Any]:
        """A hub loan as its main records it (keys as ``read_core_loan`` returns them), incl. ``time_of_closure`` which
        the hub details omit."""
        hid = to_uint(hub_id, "hubId")
        user = to_address(user, "user")
        ref = self.get_loan_ref(user, hid)
        if is_zero_address(ref["ecosystem"]):
            raise ValidationError("Loan does not exist", f"No hub loan {hid} for {user}.")
        return read_core_loan(self.client, ref["ecosystem"], self._hub_address, ref["core_loan_id"], False)

    def get_ecosystem(self, main: str) -> Dict[str, Any]:
        """The loan setup of ``main``: {stable, swap_contract, active} (all zero / false for a main never added to the
        loan hub)."""
        addr = self.client.mains.address_of(main)
        stable, swap_contract, active = self._hub.functions.ecosystems(addr).call()
        return {"stable": stable, "swap_contract": swap_contract, "active": active}

    def is_ecosystem_registered(self, main: str) -> bool:
        """True when ``main`` was added to the loan hub (its stable is set; ``active`` may be off)."""
        return self._hub.functions.isEcosystemRegistered(self.client.mains.address_of(main)).call()

    def open_loan_of(self, collateral: str, user: Optional[str] = None) -> int:
        """The hub id of ``user``'s (default the signer) OPEN loan on ``collateral`` (its main derived from it), 0 when
        none (also when the last loan there has closed)."""
        collateral = to_address(collateral, "collateral")
        who = _opt_user(user) or self._account
        info = self.client.mains.info_of_token(collateral)
        hub_id = self._hub.functions.openLoanOf(who, _cs(info.main), collateral).call()
        if hub_id == 0:
            return 0
        return hub_id if self.get_user_loan_details(who, hub_id)["active"] else 0

    def _is_multi_loan(self, borrower: str) -> bool:
        """True for the vesting contract, which may hold more than one loan per collateral."""
        return self._hub.functions.multiLoan(_cs(borrower)).call()

    def core_loan_owner(self, main: str, core_loan_id: Number) -> Dict[str, Any]:
        """{user, hub_id}: the wallet (or vault / vesting contract) and hub id behind loan ``core_loan_id`` of the loan
        hub on ``main``."""
        addr = self.client.mains.address_of(main)
        user, hub_id = self._hub.functions.coreLoanOwner(addr, to_uint(core_loan_id, "coreLoanId")).call()
        return {"user": user, "hub_id": hub_id}

    def _is_liquidation_hook(self, borrower: str) -> bool:
        """True for the vault, which may also hold more than one loan per collateral."""
        return self._hub.functions.liquidationHook(_cs(borrower)).call()

    def get_loan_terms(self, main: Optional[str] = None) -> Dict[str, Any]:
        """Loan settings of ``main`` (default the default main), all read from chain."""
        info = self.client.mains.default() if main is None else self.client.mains.get(main)
        fns = self._contract(info.main, "MainToken").functions
        return {
            "main": info.main,
            "min_days": fns.minDaysLoan().call(),
            "max_days": fns.maxDaysLoan().call(),
            "minimum_loan": pair_amount(fns.minimumLoan().call(), info),
            "static_fee_bps": fns.staticFeePercentage().call(),
            "dynamic_fee_percentage": fns.dynamicFeePercentage().call(),
        }

    def quote_loan(self, collateral: str, amount: int, number_of_days: Number,
                   user: Optional[str] = None) -> Dict[str, Any]:
        """Preview ``take_loan(collateral, amount, days)`` for ``user`` (default the signer): collateral value (= debt),
        fees and the payout, in the main's pair asset, plus ``possible`` / ``refusal`` from the same checks
        ``take_loan`` runs, in its order. The open-loan check needs a wallet: with no ``user`` and no signer it is
        skipped. With ``possible``, ``borrowed`` is what ``take_loan`` pays in the same state."""
        amt = to_uint(amount, "amount")
        days = to_uint(number_of_days, "numberOfDays")
        collateral = to_address(collateral, "collateral")
        who = _opt_user(user) or (self.client.account.address if self.client.account else None)
        plan = self._loan_plan(collateral, amt, days, who)
        info, value, fee, err = plan["info"], plan["value"], plan["fee"], plan["error"]
        return {
            "main": info.main,
            "collateral_value": pair_amount(value, info),
            "fee": pair_amount(fee, info),
            "borrowed": pair_amount(value - fee if value > fee else 0, info),
            "meets_minimum": value >= plan["minimum_loan"],
            "fees_exceed_value": fee >= value,
            "possible": err is None,
            "refusal": err.rule if err is not None else None,
        }

    def quote_increase_loan(self, hub_id: Number, amount_to_add: int, user: Optional[str] = None) -> Dict[str, Any]:
        """Preview ``increase_loan(hub_id, amount_to_add)`` for ``user`` (default the signer), as it would run in the
        next block: {hub_id, new_collateral_value, added_value (0 without borrowing power), fee (for the add, over the
        whole days left), borrowed, days_left (whole days left at the next block), possible, refusal (the rule
        ``increase_loan`` would refuse it with, in its order, or None; "pair asset paused" last, read only when nothing
        else refuses)}, raw pair asset. A closed loan raises "Loan not active"."""
        hid = to_uint(hub_id, "hubId")
        amt = to_uint(amount_to_add, "amountToAdd")
        d = self.get_user_loan_details(_opt_user(user) or self._account, hid)
        if not d["active"]:
            raise ValidationError("Loan not active", f"Hub loan {hid} is not active.")
        q = self._increase_quote(d, amt)
        if not q["live"]:
            refusal = "pair not active"
        elif not q["time_ok"]:
            refusal = "Duration too short"
        elif amt == 0:
            refusal = "Zero amount"
        elif q["added_value"] == 0:
            refusal = "No borrowing power"
        elif q["fee_most"] >= q["added_value"]:
            refusal = "Fees exceed value"
        else:
            refusal = self._paused_rule(d)
        return {
            "hub_id": hid, "new_collateral_value": q["new_collateral_value"], "added_value": q["added_value"],
            "fee": q["fee"], "borrowed": q["added_value"] - q["fee"] if q["added_value"] > q["fee"] else 0,
            "days_left": q["days_left"], "possible": refusal is None, "refusal": refusal,
        }

    def get_extension_quote(self, hub_id: Number, number_of_days: Number, pay_in_pair_asset: bool, refinance: bool,
                            user: Optional[str] = None) -> Dict[str, Any]:
        """Preview ``extend_loan(hub_id, number_of_days, pay_in_pair_asset, refinance)`` for ``user`` (default the
        signer): whether it goes through, its fee and a refinance's payout. Both flags at once are never possible (``extend_loan`` refuses them): ``possible`` False, ``fee`` and ``extra_out`` 0. On a
        paused pair (``extend_loan`` refuses "pair not active"), or while the issuer of the stable has paused it ("pair
        asset paused"), ``possible`` is False; the figures stay."""
        days = to_uint(number_of_days, "numberOfDays")
        pay, refi = to_bool(pay_in_pair_asset, "payInPairAsset"), to_bool(refinance, "refinance")
        d = self.get_user_loan_details(_opt_user(user) or self._account, hub_id)
        if pay and refi:
            return {"possible": False, "fee": 0, "extra_out": 0}
        q = self._extension_quote(d, days, pay, refi)
        live = is_pair_active(self.client, d["ecosystem"])
        possible = bool(q["possible"]) and live and self._paused_rule(d) is None
        return {**q, "possible": possible}

    def get_repay_preview(self, hub_id: Number, amount: Optional[int] = None,
                          user: Optional[str] = None) -> Dict[str, Any]:
        """Preview ``repay_loan(hub_id, amount)`` for ``user`` (default the signer): the debt, the minimum loan, the
        largest partial repay, the stable taken and the collateral released. ``amount`` None = in full. A closed loan
        gives ``active: False, allowed: False`` (``repay_loan`` would be refused "Core loan inactive")."""
        d = self.get_user_loan_details(_opt_user(user) or self._account, hub_id)
        return self._repay_preview(d, MAX_UINT256 if amount is None else to_uint(amount, "amount"))

    def quote_repay_loan_with(self, hub_id: Number, token_in: str, amount_in: Optional[int] = None,
                              buffer_bps: Optional[int] = None, user: Optional[str] = None) -> Dict[str, Any]:
        """Preview ``repay_loan_with(hub_id, token_in, amount_in)`` for ``user`` (default the signer). ``amount_in``
        None: the ``token_in`` that repays the whole debt (the least amount quoting it, plus ``buffer_bps``, default 0.5%,
        trimmed to the route's ``max_in`` / ``max_out`` caps, or to what other swaps left of them in the current window
        on Robinhood Chain). Checks the route ("no executor" / "no route"; a debt, or an explicit ``amount_in``, over the
        caps or over what is left of them now: "above cap"). A closed loan raises "Core loan inactive"."""
        if buffer_bps is not None:
            validate_slippage_bps(buffer_bps, "bufferBps")
        token_in = to_address(token_in, "tokenIn")
        d = self.get_user_loan_details(_opt_user(user) or self._account, hub_id)
        if not d["active"]:
            raise ValidationError("Core loan inactive",
                                  f"Hub loan {d['hub_id']} is not active (repaid, sold or liquidated).")
        return self._repay_with_quote(d, token_in, amount_in, buffer_bps)

    def quote_partial_sell_loan(self, hub_id: Number, percentage: Number, user: Optional[str] = None) -> Dict[str, int]:
        """Preview ``partial_sell_loan(hub_id, percentage)`` for ``user`` (default the signer), in the same state: the
        gross pair asset, ``user``'s sell rate and the fee it takes on the whole sale, the net payout (``min_out`` binds
        it), and the collateral / debt slices: {gross, taxable, tax_bps, fee, net, collateral_sold, debt_repaid}. Exact
        for main collateral; for a factory token the payout may be a little higher than quoted (dust), never lower. A
        closed loan raises "Loan not active"; a paused pair raises "pair not active", as ``partial_sell_loan`` does."""
        hid = to_uint(hub_id, "hubId")
        _check_sell_pct(percentage)
        pct = int(percentage)
        who = _opt_user(user) or self._account
        d = self.get_user_loan_details(who, hid)
        if not d["active"]:
            raise ValidationError("Loan not active", f"Hub loan {hid} is not active.")
        if not is_pair_active(self.client, d["ecosystem"]):
            raise pair_not_active(d["ecosystem"])
        return {**self._exit_quote(d, pct, who), "collateral_sold": d["collateral_amount"] * pct // 100,
                "debt_repaid": d["full_amount"] * pct // 100}


    def _loan_plan(self, collateral: str, amt: int, days: int, user: Optional[str]) -> Dict[str, Any]:
        """What ``take_loan(collateral, amt, days)`` by ``user`` would do now: {info (the main), value (the collateral
        value = the debt), fee, minimum_loan (raw pair asset), error (the first rule it would be refused with, in
        ``take_loan``'s order, or None)}: "cannot be zero", "pair not active" (the main's pair is paused), "Ecosystem
        inactive", "Unauthorized caller" (the main's loans run on another hub), "Open loan: increase it" (skipped without a ``user``, and for the vault and vesting contracts, which may hold
        several loans), "incorrect loan duration", "Minimum loan amount not met", "pair asset paused" (its issuer paused
        the pair asset the loan pays out; read only when nothing before it refuses), "No stable received" (the fees take
        the whole value)."""
        hub = self._hub_address
        info = self.client.mains.info_of_token(collateral)
        live = is_pair_active(self.client, info.main)
        eco = self.get_ecosystem(info.main)
        loan_hub = self._contract(info.main, "MainToken").functions.LOAN().call()
        terms = self.get_loan_terms(info.main)
        open_id = 0 if user is None else self.open_loan_of(collateral, user)
        value = 0 if amt == 0 else self._collateral_value(info.main, collateral, amt)
        in_range = terms["min_days"] <= days <= terms["max_days"]
        try:
            fee = self._loan_fee(info.main, value, days, terms["static_fee_bps"])
        except Exception:  # noqa: BLE001
            if in_range:
                raise
            fee = 0
        minimum_loan = terms["minimum_loan"].raw
        error = None
        if amt == 0:
            error = ValidationError("cannot be zero", "Collateral amount must be greater than 0.")
        elif not live:
            error = pair_not_active(info.main, info.symbol)
        elif not eco["active"]:
            error = ValidationError("Ecosystem inactive", f"Main {info.main} is not active on the loan hub.")
        elif not _eq(loan_hub, hub):
            error = ValidationError("Unauthorized caller", f"Main {info.main} takes new loans from hub {loan_hub}, not {hub}.")
        elif open_id != 0 and user is not None and not self._is_liquidation_hook(user) and not self._is_multi_loan(user):
            error = ValidationError(
                "Open loan: increase it",
                f"{user} already has open hub loan {open_id} on {collateral}: add to it with increase_loan({open_id}, "
                f"amount) instead of a second loan.",
            )
        elif not in_range:
            error = ValidationError("incorrect loan duration",
                                    f"Days must be {terms['min_days']}-{terms['max_days']} on this main, got {days}.")
        elif value < minimum_loan:
            error = ValidationError(
                "Minimum loan amount not met",
                f"Collateral value {pair_amount(value, info).formatted} {info.pair_symbol} is below the minimum loan "
                f"{terms['minimum_loan'].formatted}.",
            )
        elif is_asset_paused(self.client, info.pair_asset):
            error = pair_asset_paused(info.pair_asset, info.pair_symbol, "no loan can pay it out")
        elif fee >= value:
            error = ValidationError("No stable received",
                                    f"The loan fees {fee} would take the whole collateral value {value} (raw "
                                    f"{info.pair_symbol}); borrow for fewer days.")
        return {"info": info, "value": value, "fee": fee, "minimum_loan": minimum_loan, "error": error}

    def _refuse_paused(self, d: Dict[str, Any], asset: Optional[str], effect: str) -> None:
        """"pair asset paused" for a write on hub loan ``d``: the issuer of its main's pair asset (``asset``, the loan's
        stable for the main; None = the main's pair asset) has paused it, so ``effect``. Before any approval. The main is
        looked up for its symbol only after a paused answer (and for the asset when none is given). TS:
        ``refusePaused``."""
        a = self.client.mains.get(d["ecosystem"]).pair_asset if asset is None else asset
        if is_asset_paused(self.client, a):
            raise pair_asset_paused(a, self.client.mains.get(d["ecosystem"]).pair_symbol, effect)

    def _paused_rule(self, d: Dict[str, Any]) -> Optional[str]:
        """"pair asset paused" when the issuer of hub loan ``d``'s pair asset has paused it, else None (a quote's
        refusal). TS: ``pausedRule``."""
        info = self.client.mains.get(d["ecosystem"])
        return "pair asset paused" if is_asset_paused(self.client, info.pair_asset) else None

    def _exit_quote(self, d: Dict[str, Any], pct: int, borrower: str) -> Dict[str, int]:
        """What a partial sell of hub loan ``d`` pays ``borrower``, at the borrower's own sell fee rate."""
        return quote_partial_sell(self.client, d["ecosystem"],
                                  {"token": d["collateral_token"], "full_amount": d["full_amount"]},
                                  self._hub_address, d["core_loan_id"], pct, False, borrower)

    def _repay_preview(self, d: Dict[str, Any], amount: int) -> Dict[str, Any]:
        minimum_loan = self._contract(d["ecosystem"], "MainToken").functions.minimumLoan().call()
        debt = d["full_amount"]
        full = amount >= debt
        pay = debt if full else amount
        released = d["collateral_amount"] if full else (0 if debt == 0 else d["collateral_amount"] * pay // debt)
        return {
            "hub_id": d["hub_id"], "active": d["active"], "debt": debt, "minimum_loan": minimum_loan,
            "max_partial": debt - minimum_loan if debt > minimum_loan else 0,
            "amount": pay, "full": full,
            "allowed": bool(d["active"]) and pay > 0 and (full or debt - pay >= minimum_loan),
            "collateral_released": released, "collateral_left": d["collateral_amount"] - released,
            "debt_left": debt - pay,
        }

    def _repay_route(self, d: Dict[str, Any], token_in: str) -> Dict[str, Any]:
        """The loan's stable and how ``token_in`` converts to it: itself, or ``quoteHop`` on the route executor
        ``exec`` (route checked)."""
        eco = self.get_ecosystem(d["ecosystem"])
        stable, swap = eco["stable"], eco["swap_contract"]
        if is_zero_address(swap):
            raise ValidationError("No swap for ecosystem", f"Main {d['ecosystem']} has no swap set on the loan hub.")
        if _eq(token_in, stable):
            return {"stable": stable, "direct": True, "exec": ZERO_ADDRESS, "quote": lambda x: x}
        exec_ = self._contract(swap, "Swap").functions.hopExecutor().call()
        if is_zero_address(exec_):
            raise ValidationError("no executor", f"The swap {swap} has no outside-route executor: repay in {stable}.")
        hops = self._contract(exec_, "HopExecutor").functions.getRoute(_cs(token_in), _cs(stable)).call()
        if len(hops) == 0:
            raise ValidationError("no route",
                                  f"No outside route from {token_in} to {stable}: repay in {stable} or another token.")
        return {"stable": stable, "direct": False, "exec": exec_,
                "quote": lambda x: self._quote_hop(exec_, token_in, stable, x)}

    def _repay_with_quote(self, d: Dict[str, Any], token_in: str, amount_in: Optional[int],
                          buffer_bps: Optional[int] = None, route: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        route = route or self._repay_route(d, token_in)
        stable, quote = route["stable"], route["quote"]
        debt = d["full_amount"]
        if amount_in is not None:
            x = to_uint(amount_in, "amountIn")
            if x == 0:
                raise ValidationError("Zero amount", "amountIn must be greater than 0.")
            stable_out = x if route["direct"] else quote_hop_within_caps(self.client, route["exec"], token_in, stable, x,
                                                                         quote)
        else:
            if route["direct"]:
                x = debt
            else:
                x = self._size_for(route["exec"], token_in, stable, debt, quote,
                                   REPAY_WITH_BUFFER_BPS if buffer_bps is None else buffer_bps)
            stable_out = quote(x)
        covers = stable_out >= debt
        released = d["collateral_amount"] if covers else (0 if debt == 0 else d["collateral_amount"] * stable_out // debt)
        return {
            "hub_id": d["hub_id"], "token_in": _cs(token_in), "stable": stable, "debt": debt, "amount_in": x,
            "stable_out": stable_out, "covers": covers, "refund": stable_out - debt if covers else 0,
            "collateral_released": released,
        }

    def _size_for(self, exec_: str, token_in: str, stable: str, target: int, quote, buffer_bps: int) -> int:
        """``token_in`` to send for ``target`` of ``stable``: the least amount quoting it, plus ``buffer_bps``, within what
        the route takes now (on Robinhood Chain, what other swaps left of its caps in the current window)."""
        return size_repay_with(quote, target, {**read_repay_sizing(self.client, exec_, token_in, stable),
                                               "buffer_bps": buffer_bps})

    def _quote_hop(self, exec_: str, token_in: str, token_out: str, amount_in: int) -> int:
        """``quoteHop`` on the route executor (an eth_call; net of the route fee)."""
        c = self._contract(exec_, "HopExecutor")
        tx = {"from": self.client.account.address} if self.client.account else {}
        try:
            return c.functions.quoteHop(_cs(token_in), _cs(token_out), int(amount_in)).call(tx)
        except Exception as e:  # noqa: BLE001
            raise decode_revert(e, contract="HopExecutor", function_name="quoteHop", abi=c.abi) from e


    def _extension_quote(self, d: Dict[str, Any], days: int, pay_in_pair_asset: bool,
                         refinance: bool) -> Dict[str, Any]:
        possible, fee, extra_out = self._contract(d["ecosystem"], "MainToken").functions.extensionEligibility(
            self._hub_address, d["core_loan_id"], days, False, pay_in_pair_asset, refinance).call()
        return {"possible": possible, "fee": fee, "extra_out": extra_out}

    def _collateral_value(self, main: str, token: str, amount: int) -> int:
        """The collateral value of ``amount`` of ``token`` on ``main``, raw pair asset (reverts "wrong pair" / "Only
        ecosystem tokens" decoded)."""
        c = self._contract(main, "MainToken")
        try:
            return c.functions.getColleteralValue(int(amount), _cs(token)).call()
        except Exception as e:  # noqa: BLE001
            raise decode_revert(e, contract="MainToken", function_name="getColleteralValue", abi=c.abi) from e

    def _chain_time(self) -> int:
        return self.web3.eth.get_block("latest")["timestamp"]

    def _send_deadline(self, explicit: Optional[int]) -> int:
        """The ``repay_loan_with`` deadline to send (unix seconds, chain time): ``explicit``, refused ("Deadline") when it
        is before the latest block time (``check_deadline``), else the latest block time + 600 s."""
        now = self._pre_send_read(lambda: self.web3.eth.get_block("latest"))["timestamp"]
        return now + REPAY_WITH_DEADLINE if explicit is None else check_deadline(explicit, now)

    def _loan_fee(self, main: str, value: int, days: int, static_fee_bps: int) -> int:
        """The fee of a new loan on ``value`` (raw pair asset) for ``days``."""
        dynamic = self._contract(main, "MainToken").functions.getDynamicFee(int(value), int(days)).call()
        return value * static_fee_bps // 10_000 + dynamic

    def _increase_quote(self, d: Dict[str, Any], amt: int) -> Dict[str, Any]:
        """The ``increase_loan`` figures for the next block (latest + 1 s: the tx mines later): live = the pair is not
        paused, time_ok = more than the minimum days left, the collateral value after the add, the value added to the
        debt (0 without borrowing power) and its fee for the whole days left (days_left, from the next block).
        fee_most / most_days: the fee on the days left at the latest block, which "Fees exceed value" is checked on
        (the tx can mine in a block with the same time, which can leave one more whole day)."""
        live = is_pair_active(self.client, d["ecosystem"])
        mf = self._contract(d["ecosystem"], "MainToken").functions
        min_days = mf.minDaysLoan().call()
        static_fee = mf.staticFeePercentage().call()
        latest = self._chain_time()
        now = latest + 1
        new_value = self._collateral_value(d["ecosystem"], d["collateral_token"], d["collateral_amount"] + amt)
        liq = d["liquidation_time"]
        days_left = (liq - now) // DAY if liq > now else 0
        most_days = (liq - latest) // DAY if liq > latest else 0
        added = new_value - d["full_amount"] if new_value > d["full_amount"] else 0

        def dynamic_for(n: int) -> int:
            return 0 if added == 0 else mf.getDynamicFee(int(added), int(n)).call()

        static_part = added * static_fee // 10_000
        fee = static_part + dynamic_for(days_left)
        fee_most = fee if most_days == days_left else static_part + dynamic_for(most_days)
        return {"live": live, "min_days": min_days, "time_ok": now + min_days * DAY < liq,
                "days_left": days_left, "most_days": most_days,
                "new_collateral_value": new_value, "added_value": added, "fee": fee, "fee_most": fee_most}

    def _loan_formats(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Formatters of a hub loan's figures ({pair: its main's pair asset, col: its collateral token}), resolved
        before a write sends."""
        info = self.client.mains.get(d["ecosystem"])
        return {"pair": pair_amount_of(info), "col": amount_of(self.client, d["collateral_token"], info)}

    def _repaid_figures(self, tx: Dict[str, Any], d: Dict[str, Any], stable: str, fmt: Dict[str, Any]) -> Dict[str, Any]:
        """What a repay of loan ``d`` did, from the transaction's events (``Amount``s via ``fmt``): a partial repay's
        amount and collateral released, else a full repay's stable repaid and the collateral sent back to the wallet
        (from the ERC-20 transfers). Neither: all None and a warning."""
        hub = self._hub_address
        pair, col = fmt["pair"], fmt["col"]
        part = next((e for e in self._events(tx, self._hub, "LoanPartlyRepaid") if e["hubId"] == d["hub_id"]), None)
        if part is not None:
            return {"closed": False, "repaid": pair(part["amount"]), "collateral_released": col(part["collateralReleased"])}
        full = next((e for e in self._events(tx, self._hub, "LoanRepaid") if e["hubId"] == d["hub_id"]), None)
        if full is None:
            add_missing_event_warning(tx, "LoanHub", "LoanRepaid", ("closed", "repaid", "collateral_released"))
            return {"closed": None, "repaid": None, "collateral_released": None}
        return {"closed": True, "repaid": pair(self._transferred(tx, stable, hub, d["ecosystem"])),
                "collateral_released": col(self._transferred(tx, d["collateral_token"], hub, self._account))}

    def _transferred(self, tx: Dict[str, Any], token: str, frm: str, to: str) -> int:
        """Sum of the ``token`` transfers ``frm`` -> ``to`` in ``tx`` (ERC-20 ``Transfer`` logs emitted by ``token``)."""
        return sum(e["value"] for e in self._events(tx, self._contract(token, "ERC20"), "Transfer")
                   if _eq(e["from"], frm) and _eq(e["to"], to))
