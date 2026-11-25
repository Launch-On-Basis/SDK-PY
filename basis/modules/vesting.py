"""Vesting: gradual and cliff vestings of BASIS tokens, loans against them, and the reads. Loans are made by the BASIS
protocol. Reads return token amounts raw in the vested token's decimals unless a parameter says otherwise; write
results return ``Amount`` objects. Struct results are dicts with snake_case keys.

Token amounts (``TokenAmountInput``): an ``int`` is raw (vested-token units); a ``str`` / ``float`` / ``Decimal``
is a human amount scaled by the token's decimals.

Write results carry the figures of the transaction's events (None when the event is missing; ``warnings``
says why): creates: vesting_id / vesting_ids. renounce_vesting: renounced. change_beneficiary: previous_beneficiary,
beneficiary, plus liquidated, recovered and loan_id (None when no loan was settled). transfer_creator_role:
previous_creator, creator. add_tokens_to_vesting: new_total_amount. extend_vesting_period: days_added. claim_tokens:
claimed. take_loan_on_vesting: loan_id, hub, token_amount, loan_duration_days, borrowed. repay_loan_on_vesting:
liquidated, recovered, loan_id. repay_extend_and_reborrow: the take_loan figures plus repaid_loan_id and days_added.
Money figures are ``Amount`` objects: claimed, new_total_amount, token_amount and recovered in the vested token,
borrowed in the vesting main's pair asset.

Unclaimed vested factory tokens earn holder rewards for the current beneficiary, like tokens it holds (when the token
has holder rewards on). A vesting loan pauses that until it is repaid or settled. A vested main earns none.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from web3 import Web3

from ..config.addresses import ADDRESS_KEYS, OPTIONAL_ADDRESS_KEYS, is_zero_address
from ..core.amounts import amount_of, opt_amount, pair_amount_of
from ..core.base import BaseModule
from ..core.errors import BasisError, ValidationError
from ..core.events import add_missing_event_warning
from ..core.loans import is_asset_paused, is_pair_active, pair_asset_paused, pair_not_active, refuse_paused_asset
from ..core.multicall import multicall
from ..core.pinned import pinned_call
from ..core.units import check_text, parse_amount, to_address, to_bool, to_uint

VESTING_TIME_UNIT = {"second": 0, "minute": 1, "hour": 2, "day": 3}
_OUTSIDE_KEYS = frozenset({"wrappedNative", "erc8004Registry"})
_PROTOCOL_KEYS = tuple(k for k in (*ADDRESS_KEYS, *OPTIONAL_ADDRESS_KEYS) if k not in _OUTSIDE_KEYS)


class VestingTimeUnit:
    """``TimeUnit`` enum values (TS ``VestingTimeUnit``)."""
    SECOND = 0
    MINUTE = 1
    HOUR = 2
    DAY = 3


TokenAmountInput = Union[int, str, float, Decimal]
TimeUnitInput = Union[int, str]

DAY = 86_400
MAX_MEMO_BYTES = 256
DETAILS_PAGE = 100
READS_PAGE = 50
IDS_PAGE = 500
BATCH_LIMIT = 70
TX_GAS_LIMIT = 32_000_000
BATCH_GAS = 143_000
ROW_GAS = 420_000
MEMO_GAS_PER_BYTE = 1_000


def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


def _list_of(v: Any, field: str) -> List[Any]:
    """A batch create's list (``beneficiaries``, ``total_amounts``, ``user_memos``): a list or a tuple, copied as a
    list. Anything else is refused with the list's name as the rule, before any read: a string is never read character
    by character, and a set (no order) never pairs amounts or memos with the wrong wallets."""
    if not isinstance(v, (list, tuple)):
        got = "None" if v is None else type(v).__name__
        raise ValidationError(field, f"{field} must be a list, one entry per vesting; got {got}.")
    return list(v)


def _to_details(vesting_id: int, s) -> Dict[str, Any]:
    return {
        "id": vesting_id,
        "creator": s[0],
        "beneficiary": s[1],
        "token": s[2],
        "ecosystem": s[3],
        "total_amount": s[4],
        "claimed_amount": s[5],
        "start_time": s[6],
        "duration_in_days": s[7],
        "unlock_time": s[8],
        "is_gradual": s[9],
        "active_loan_id": s[10],
        "memo": s[11],
        "time_unit": s[12],
        "renounced": s[13],
    }


def _to_hub_details(r) -> Dict[str, Any]:
    """Loan details with the same snake_case keys as the loans module."""
    return {
        "hub_id": r[0], "ecosystem": r[1], "core_loan_id": r[2], "collateral_token": r[3], "token": r[4],
        "collateral_amount": r[5], "liquidated_amount": r[6], "full_amount": r[7], "borrowed_amount": r[8],
        "liquidation_time": r[9], "liquidation_claim": r[10], "is_liquidated": r[11], "active": r[12],
        "creation_time": r[13],
    }


class VestingModule(BaseModule):
    """Vesting: gradual and cliff vestings, loans against them, and the reads."""

    def __init__(self, client):
        super().__init__(client)
        self._limits: Optional[Dict[str, int]] = None

    @property
    def deployed(self) -> bool:
        """True when the vesting contract has an address on this chain."""
        return not is_zero_address(self.client.addresses["vesting"])

    @property
    def _address(self) -> str:
        return self._require(self.client.addresses["vesting"], "Vesting")

    @property
    def _vesting(self):
        return self._contract(self._address, "Vesting")


    def create_gradual_vesting(self, beneficiary: str, token: str, total_amount: TokenAmountInput,
                               start_time: int, duration_in_days: int, time_unit: TimeUnitInput, memo: str,
                               ecosystem: Optional[str] = None, renounce_on_creation: bool = False) -> Dict[str, Any]:
        """Create a gradual vesting. Approves the token, pays the exact creation fee, returns the new id (``vesting_id``).
        ``beneficiary``: a wallet: the zero address ("Invalid beneficiary address") and a BASIS contract or token
        ("BASIS contract": any protocol contract or token, including every main with its pair asset and vault, the
        vested token, its main and that main's pair asset and vault; none of them could ever claim) are refused before
        any approval;
        ``total_amount``: raw int or a human amount in the token's decimals; ``start_time``: unix seconds (a past time
        starts now; at most 7 days ahead); ``duration_in_days``: whole days (1..990), and the
        schedule must end within 990 days of now; ``time_unit``: release step (0-3 or
        'second' | 'minute' | 'hour' | 'day'); ``memo``: at most 256 bytes of UTF-8 ("Memo too long"); ``ecosystem``:
        the token's main (address or key), default its own main; the token must be that main itself or a factory token
        trading against it ("Not an Ecosystem token"); ``renounce_on_creation`` (default, also for None: False): give up
        the creator's rights at once: ``change_beneficiary``, creator extensions, and repaying or settling the
        schedule's loan as creator."""
        address = self._address
        _ = self._account
        beneficiary = self._to_beneficiary(beneficiary, "beneficiary")
        token = to_address(token, "token")
        self._check_memo(memo, "memo")
        renounce = to_bool(renounce_on_creation, "renounceOnCreation", False)
        main = self._check_token(token, ecosystem)
        amount = self._to_token_amount(token, total_amount, "totalAmount")
        start = to_uint(start_time, "startTime")
        days = to_uint(duration_in_days, "durationInDays")
        unit = self._to_time_unit(time_unit)
        self._check_gradual_schedule(start, days)
        self._refuse_vested_own([beneficiary], ["beneficiary"], self._vested_own(token, main))
        value = self._prepare_create(token, amount)

        tx = self._send(self._contract(address, "Vesting").functions.createGradualVesting(
            beneficiary, token, amount, start, days, unit, memo, _cs(main), renounce),
            "Vesting", value=value)
        ids = self._created_ids(tx, ("vesting_id",))
        return {**tx, "vesting_id": ids[0] if ids else None}

    def create_cliff_vesting(self, beneficiary: str, token: str, total_amount: TokenAmountInput, unlock_time: int,
                             memo: str, ecosystem: Optional[str] = None,
                             renounce_on_creation: bool = False) -> Dict[str, Any]:
        """Create a cliff vesting: everything unlocks at ``unlock_time`` (unix seconds, 1 hour to 990 days from now).
        Approves the token, pays the exact fee, returns the new id (``vesting_id``). ``beneficiary`` ("BASIS contract"
        for a protocol contract or token), ``memo`` and ``ecosystem`` as ``create_gradual_vesting``."""
        address = self._address
        _ = self._account
        beneficiary = self._to_beneficiary(beneficiary, "beneficiary")
        token = to_address(token, "token")
        self._check_memo(memo, "memo")
        renounce = to_bool(renounce_on_creation, "renounceOnCreation", False)
        main = self._check_token(token, ecosystem)
        amount = self._to_token_amount(token, total_amount, "totalAmount")
        unlock = to_uint(unlock_time, "unlockTime")
        self._check_cliff_unlock(unlock)
        self._refuse_vested_own([beneficiary], ["beneficiary"], self._vested_own(token, main))
        value = self._prepare_create(token, amount)
        self._check_cliff_unlock(unlock)

        tx = self._send(self._contract(address, "Vesting").functions.createCliffVesting(
            beneficiary, token, amount, unlock, memo, _cs(main), renounce),
            "Vesting", value=value)
        ids = self._created_ids(tx, ("vesting_id",))
        return {**tx, "vesting_id": ids[0] if ids else None}

    def batch_create_gradual_vesting(self, beneficiaries: Sequence[str], token: str,
                                     total_amounts: Sequence[TokenAmountInput], user_memos: Sequence[str],
                                     start_time: int, duration_in_days: int, time_unit: TimeUnitInput,
                                     ecosystem: Optional[str] = None,
                                     renounce_on_creation: bool = False) -> Dict[str, Any]:
        """One gradual vesting per beneficiary in one transaction (one fee). Approves the sum. Returns the ids in order
        (``vesting_ids``). ``total_amounts``: one per beneficiary (raw or human, token decimals); ``user_memos``: one
        per beneficiary, each at most 256 bytes of UTF-8. The three lists must each be a list or a tuple (rule: the
        list's name, before any read: a string or a set is refused). One batch must fit one transaction: at most 70
        beneficiaries, fewer with long memos (rule ``batchSize``, checked before the approval); split a longer list into
        several batches. Each beneficiary is a wallet, as in ``create_gradual_vesting`` ("BASIS contract" names the
        entry, ``beneficiaries[i]``)."""
        address = self._address
        _ = self._account
        listed, total_amounts, user_memos = self._check_batch(beneficiaries, total_amounts, user_memos)
        token = to_address(token, "token")
        renounce = to_bool(renounce_on_creation, "renounceOnCreation", False)
        main = self._check_token(token, ecosystem)
        amounts = [self._to_token_amount(token, a, f"totalAmounts[{i}]") for i, a in enumerate(total_amounts)]
        start = to_uint(start_time, "startTime")
        days = to_uint(duration_in_days, "durationInDays")
        unit = self._to_time_unit(time_unit)
        self._check_gradual_schedule(start, days)
        self._refuse_vested_own(listed, [f"beneficiaries[{i}]" for i in range(len(listed))], self._vested_own(token, main))
        value = self._prepare_create(token, sum(amounts))

        tx = self._send(self._contract(address, "Vesting").functions.batchCreateGradualVesting(
            listed, token, amounts, user_memos, start, days, unit, _cs(main),
            renounce), "Vesting", value=value)
        return {**tx, "vesting_ids": self._created_ids(tx, ("vesting_ids",))}

    def batch_create_cliff_vesting(self, beneficiaries: Sequence[str], token: str,
                                   total_amounts: Sequence[TokenAmountInput], unlock_time: int,
                                   user_memos: Sequence[str], ecosystem: Optional[str] = None,
                                   renounce_on_creation: bool = False) -> Dict[str, Any]:
        """One cliff vesting per beneficiary in one transaction (one fee). Approves the sum. Returns the ids in order
        (``vesting_ids``). Note the order: ``unlock_time`` comes before ``user_memos``. One batch must fit one
        transaction, as ``batch_create_gradual_vesting`` (rule ``batchSize``); the three lists must be lists or tuples,
        as there."""
        address = self._address
        _ = self._account
        listed, total_amounts, user_memos = self._check_batch(beneficiaries, total_amounts, user_memos)
        token = to_address(token, "token")
        renounce = to_bool(renounce_on_creation, "renounceOnCreation", False)
        main = self._check_token(token, ecosystem)
        amounts = [self._to_token_amount(token, a, f"totalAmounts[{i}]") for i, a in enumerate(total_amounts)]
        unlock = to_uint(unlock_time, "unlockTime")
        self._check_cliff_unlock(unlock)
        self._refuse_vested_own(listed, [f"beneficiaries[{i}]" for i in range(len(listed))], self._vested_own(token, main))
        value = self._prepare_create(token, sum(amounts))
        self._check_cliff_unlock(unlock)

        tx = self._send(self._contract(address, "Vesting").functions.batchCreateCliffVesting(
            listed, token, amounts, unlock, user_memos, _cs(main),
            renounce), "Vesting", value=value)
        return {**tx, "vesting_ids": self._created_ids(tx, ("vesting_ids",))}


    def renounce_vesting(self, vesting_id: int) -> Dict[str, Any]:
        """Give up the creator's rights for good: ``change_beneficiary`` (its ``repay_loan`` path included), creator
        extensions, and repaying or settling the schedule's loan as creator (``repay_loan_on_vesting``). A creator that
        is also the beneficiary keeps what the beneficiary may do. Creator only. Returns the write result plus
        ``renounced``."""
        vid = to_uint(vesting_id, "vestingId")
        v = self._load_existing(vid)
        self._require_caller(v["creator"], "Only creator can renounce", "Only the creator can renounce a vesting.")
        if v["renounced"]:
            raise ValidationError("Already renounced", f"Vesting {vid} is already renounced.")
        tx = self._send(self._vesting.functions.renounceVesting(vid), "Vesting")
        ev = self._expect_event(tx, self._vesting, "VestingRenounced", "Vesting", ("renounced",))
        return {**tx, "renounced": True if ev else None}

    def change_beneficiary(self, vesting_id: int, new_beneficiary: str, repay_loan: bool = False) -> Dict[str, Any]:
        """Move the vesting to a new beneficiary. Creator only; not when renounced. A linked loan blocks it unless
        ``repay_loan`` is True: then the same transaction repays a live loan in full from the creator's wallet
        (approving the pair asset for the debt, refused first while its issuer has paused it: 'pair asset paused'; the
        beneficiary keeps what it borrowed) or settles a liquidated one (no payment; what the liquidation left goes back
        on the schedule). No effect without a loan. The new beneficiary must be a wallet: the zero address and a BASIS
        contract or token (as for a create: "BASIS contract") are refused before any approval. The current beneficiary
        again is refused when no loan is linked ("same beneficiary", nothing sent: the tx would change nothing); with
        ``repay_loan`` and a linked loan it is allowed (the tx settles the loan). Returns the write result plus
        ``previous_beneficiary`` and ``beneficiary``, and ``liquidated``, ``recovered`` (``Amount`` in the vested token,
        0 for a repay) and ``loan_id`` as ``repay_loan_on_vesting`` reports them (all None when no loan was settled)."""
        vid = to_uint(vesting_id, "vestingId")
        new_beneficiary = to_address(new_beneficiary, "newBeneficiary")
        repay = to_bool(repay_loan, "repayLoan", False)
        v = self._load_existing(vid)
        self._require_caller(v["creator"], "Only creator can change beneficiary",
                             "Only the creator can change the beneficiary.")
        if v["renounced"]:
            raise ValidationError("Vesting renounced", f"Vesting {vid} is renounced: its beneficiary is fixed.")
        if is_zero_address(new_beneficiary):
            raise ValidationError("Invalid beneficiary address", "newBeneficiary must not be the zero address.")
        if v["active_loan_id"] != 0 and not repay:
            raise ValidationError(
                "Loan open: repay first",
                f"Vesting {vid} has a loan linked (hub id {v['active_loan_id']}): pass repayLoan to settle it in "
                "the same transaction, or settle it first with repayLoanOnVesting.")
        if v["active_loan_id"] == 0 and new_beneficiary.lower() == v["beneficiary"].lower():
            raise ValidationError(
                "same beneficiary",
                f"{new_beneficiary} is already the beneficiary of vesting {vid} and no loan is linked to settle: there is "
                "nothing to change. Nothing was sent.")
        self._refuse_protocol_address(new_beneficiary, "newBeneficiary", self._vested_own(v["token"], v["ecosystem"]),
                                      "beneficiary")
        fmt = None
        if v["active_loan_id"] != 0:
            self._prepare_reconcile(self._loan_of(v))
            fmt = self._token_fmt(v)
        tx = self._send(self._vesting.functions.changeBeneficiary(vid, new_beneficiary, repay), "Vesting")
        ev = self._expect_event(tx, self._vesting, "BeneficiaryChanged", "Vesting",
                                ("previous_beneficiary", "beneficiary"))
        settled = (self._reconciled(tx, fmt) if fmt is not None
                   else {"liquidated": None, "recovered": None, "loan_id": None})
        return {**tx, "previous_beneficiary": ev["oldBeneficiary"] if ev else None,
                "beneficiary": ev["newBeneficiary"] if ev else None, **settled}

    def transfer_creator_role(self, vesting_id: int, new_creator: str) -> Dict[str, Any]:
        """Hand the creator role to another wallet. Creator only (allowed on a renounced vesting). The new creator must
        be a wallet: the zero address ("Invalid creator address") and a BASIS contract or token ("BASIS contract", as
        for a beneficiary) are refused before the tx. Returns the write result plus ``previous_creator`` and
        ``creator``."""
        vid = to_uint(vesting_id, "vestingId")
        new_creator = to_address(new_creator, "newCreator")
        v = self._load_existing(vid)
        self._require_caller(v["creator"], "Only creator can transfer role",
                             "Only the creator can transfer the creator role.")
        if is_zero_address(new_creator):
            raise ValidationError("Invalid creator address", "newCreator must not be the zero address.")
        self._refuse_protocol_address(new_creator, "newCreator", self._vested_own(v["token"], v["ecosystem"]), "creator")
        tx = self._send(self._vesting.functions.transferCreatorRole(vid, new_creator), "Vesting")
        ev = self._expect_event(tx, self._vesting, "CreatorRoleTransferred", "Vesting", ("previous_creator", "creator"))
        return {**tx, "previous_creator": ev["oldCreator"] if ev else None, "creator": ev["newCreator"] if ev else None}

    def add_tokens_to_vesting(self, vesting_id: int, additional_amount: TokenAmountInput) -> Dict[str, Any]:
        """Add tokens to a running vesting. Creator only; refused while a loan is linked, or when the vesting has ended
        by the next block ("Vesting already ended", checked again after the approval). Approves the token.
        ``additional_amount``: raw int or a human amount in the token's decimals. Returns the write result plus
        ``new_total_amount``."""
        vid = to_uint(vesting_id, "vestingId")
        address = self._address
        v = self._load_existing(vid)
        self._require_caller(v["creator"], "Only creator can add tokens", "Only the creator can add tokens.")
        self._require_no_loan(v, "Loan open: repay first")
        amount = self._to_token_amount(v["token"], additional_amount, "additionalAmount",
                                       "Additional amount must be greater than zero")
        self._check_still_running(v)
        self._check_balance(v["token"], amount)
        self._ensure_allowance(v["token"], address, amount)
        self._check_still_running(v)
        fmt = self._token_fmt(v)
        tx = self._send(self._vesting.functions.addTokensToVesting(vid, amount), "Vesting")
        ev = self._expect_event(tx, self._vesting, "VestingUpdated", "Vesting", ("new_total_amount",))
        return {**tx, "new_total_amount": opt_amount(fmt, ev["totalAmount"] if ev else None)}


    def extend_vesting_period(self, vesting_id: int, additional_days: int) -> Dict[str, Any]:
        """Extend the vesting by whole days. Creator (not when renounced, unless it is also the beneficiary) or
        beneficiary; refused while a loan is linked (use ``repay_extend_and_reborrow``), once the vesting has ended by the
        next block ("Vesting already ended"), or past 990 days remaining (the whole days left counted from the latest
        block: the extension can mine in a block with the same time, which leaves the most days). Returns the write
        result plus ``days_added``."""
        vid = to_uint(vesting_id, "vestingId")
        days = to_uint(additional_days, "additionalDays")
        v = self._load_existing(vid)
        me = self._account.lower()
        is_creator = v["creator"].lower() == me
        is_beneficiary = v["beneficiary"].lower() == me
        if not is_creator and not is_beneficiary:
            raise ValidationError("Only creator or beneficiary can extend",
                                  "Only the creator or the beneficiary can extend a vesting.")
        if is_creator and not is_beneficiary and v["renounced"]:
            raise ValidationError("Vesting renounced",
                                  f"Vesting {vid} is renounced: the creator can no longer extend it.")
        self._require_no_loan(v, "Loan open: repay first")
        self._check_extension(v, days)
        tx = self._send(self._vesting.functions.extendVestingPeriod(vid, days), "Vesting")
        ev = self._expect_event(tx, self._vesting, "VestingExtended", "Vesting", ("days_added",))
        return {**tx, "days_added": ev["additionalDays"] if ev else None}


    def claim_tokens(self, vesting_id: int) -> Dict[str, Any]:
        """Claim the vested, unclaimed tokens. Beneficiary only; not while a loan is linked.
        Adds ``claimed`` (``Amount`` in the vested token)."""
        vid = to_uint(vesting_id, "vestingId")
        address = self._address
        v = self._load_existing(vid)
        self._require_caller(v["beneficiary"], "Only beneficiary can claim", "Only the beneficiary can claim.")
        self._require_no_loan(v)
        claimable = self.get_claimable_amount(vid)
        if claimable == 0:
            raise ValidationError("No tokens to claim", f"Vesting {vid} has nothing to claim yet.")
        fmt = self._token_fmt(v)
        tx = self._send(self._vesting.functions.claimTokens(vid), "Vesting")
        ev = self._expect_event(tx, self._contract(address, "Vesting"), "TokensClaimed", "Vesting", ("claimed",))
        return {**tx, "claimed": opt_amount(fmt, ev["amount"] if ev else None)}

    def take_loan_on_vesting(self, vesting_id: int) -> Dict[str, Any]:
        """Borrow against the unclaimed tokens. The loan runs to the vesting's end (whole days) plus the loan buffer
        (``get_loan_buffer``) days; the pair asset is paid to the beneficiary. Beneficiary only; one loan at a time
        ("Active loan exists"). While the loan is linked the schedule cannot be claimed, topped up or extended, and its
        unclaimed tokens earn no holder rewards. Runs the loan rules first (``quote_loan_on_vesting`` previews the
        loan): "pair not active" (the main's pair is paused), "Ecosystem inactive", "Unauthorized caller", "incorrect
        loan duration", "Minimum loan amount not met", "pair asset paused" (its issuer paused the pair asset), "No stable
        received".
        Adds loan_id, hub, token_amount (``Amount``, vested token), loan_duration_days, borrowed (``Amount``, pair asset)."""
        vid = to_uint(vesting_id, "vestingId")
        v = self._load_existing(vid)
        self._require_caller(v["beneficiary"], "Only beneficiary can take loan",
                             "Only the beneficiary can borrow against a vesting.")
        if v["active_loan_id"] != 0:
            raise ValidationError("Active loan exists", f"Vesting {vid} already has a loan. Repay it first.")
        plan = self._loan_plan(v, 0)
        if plan["error"] is not None:
            raise plan["error"]
        fmts = self._loan_fmts(v)
        tx = self._send(self._vesting.functions.takeLoanOnVesting(vid), "Vesting")
        return self._loan_taken(tx, fmts)

    def repay_loan_on_vesting(self, vesting_id: int) -> Dict[str, Any]:
        """Repay the vesting's loan in full, approving the pair asset for the debt; refused before the approval while
        the pair asset's issuer has paused it ('pair asset paused': it cannot move). When the loan was liquidated, this
        instead settles it: what the liquidation left goes back on the schedule (no payment). Settle within 365 days of
        the liquidation: a leftover still unclaimed after that can be taken. The
        beneficiary, or the creator while the vesting is not renounced. Adds ``liquidated``, ``recovered`` (``Amount`` in
        the vested token; 0 for a repay) and the settled ``loan_id``."""
        vid = to_uint(vesting_id, "vestingId")
        v = self._load_existing(vid)
        if self._account.lower() != v["creator"].lower() or v["renounced"]:
            self._require_caller(v["beneficiary"], "Only beneficiary or creator",
                                 "Only the vesting's beneficiary, or its creator while it is not renounced, can repay its loan.")
        loan = self._loan_of(v)
        if not loan:
            raise ValidationError("No active loan", f"Vesting {vid} has no loan.")
        self._prepare_reconcile(loan)
        fmt = self._token_fmt(v)
        tx = self._send(self._vesting.functions.repayLoanOnVesting(vid), "Vesting")
        return {**tx, **self._reconciled(tx, fmt)}

    def repay_extend_and_reborrow(self, vesting_id: int, additional_days: int) -> Dict[str, Any]:
        """Refinance in one go: repay the open loan in full (approving the pair asset for the debt), extend the vesting
        by ``additional_days``, and take a new loan sized to the new schedule (fees are paid again).
        Beneficiary only. A liquidated loan must be settled with ``repay_loan_on_vesting`` first. The new loan is
        checked against the loan rules on the extended schedule (as ``take_loan_on_vesting``). The extension rules
        are measured as ``extend_vesting_period`` measures them (the end from the next block, the days left from the
        latest block), and checked again after the approval. Returns the new loan's figures plus ``repaid_loan_id`` and
        ``days_added``."""
        vid = to_uint(vesting_id, "vestingId")
        days = to_uint(additional_days, "additionalDays")
        address = self._address
        v = self._load_existing(vid)
        self._require_caller(v["beneficiary"], "Only beneficiary", "Only the beneficiary can refinance a vesting loan.")
        loan = self._loan_of(v)
        if not loan:
            raise ValidationError("No active loan", f"Vesting {vid} has no loan.")
        if not loan["details"]["active"]:
            raise ValidationError("Loan not active",
                                  f"The loan on vesting {vid} was liquidated: settle it with repayLoanOnVesting.")
        self._check_extension(v, days)
        plan = self._loan_plan(v, days, True)
        if plan["error"] is not None:
            raise plan["error"]
        self._check_balance(loan["repay_token"], loan["details"]["full_amount"])
        self._ensure_allowance(loan["repay_token"], address, loan["details"]["full_amount"])
        self._check_extension(v, days)
        fmts = self._loan_fmts(v)
        tx = self._send(self._vesting.functions.repayExtendAndReborrow(vid, days), "Vesting")
        vc = self._contract(address, "Vesting")
        repaid = self._expect_event(tx, vc, "LoanRepaid", "Vesting", ("repaid_loan_id",))
        ext = self._expect_event(tx, vc, "VestingExtended", "Vesting", ("days_added",))
        return {**self._loan_taken(tx, fmts), "repaid_loan_id": repaid["loanId"] if repaid else None,
                "days_added": ext["additionalDays"] if ext else None}

    def quote_loan_on_vesting(self, vesting_id: int) -> Dict[str, Any]:
        """Preview ``take_loan_on_vesting(vesting_id)``: {vesting_id, hub (``get_loan_hub``), main, token, token_amount
        (total - claimed), loan_duration_days (whole days left to the end + the loan buffer), collateral_value (= the
        debt), fee, borrowed, meets_minimum (the main's minimum loan size is met), refusal (the rule the loan would be
        refused with, or None; "Active loan exists" first while a loan is linked)}; raw units
        (tokens in the vested token, values in the main's pair asset). The length counts from the next block (latest +
        1 s), so the loan can run one day longer (the tx mines in a block with the latest block's time, about 10 blocks
        a second share a second, and the end is a whole number of days from it) or one day shorter (it lands later,
        past a day boundary). The refusals allow for both: the length rule ("incorrect loan duration") checks its
        minimum from the next block and its maximum from the latest block, and "No stable received" uses the longer
        length's fee."""
        vid = to_uint(vesting_id, "vestingId")
        v = self.get_vesting_details(vid)
        if is_zero_address(v["creator"]):
            raise ValidationError("Vesting does not exist", f"Vesting {vid} does not exist.")
        plan = self._loan_plan(v, 0)
        err = plan.pop("error")
        return {**plan, "refusal": err.rule if err is not None else None}

    def quote_repay_extend_and_reborrow(self, vesting_id: int, additional_days: int) -> Dict[str, Any]:
        """Preview ``repay_extend_and_reborrow(vesting_id, additional_days)`` for the beneficiary: the debt of the
        linked loan it repays first (``repay_amount``), the new loan on the schedule extended by ``additional_days``
        (as ``quote_loan_on_vesting`` sizes it) and ``net`` = ``borrowed - repay_amount``. Returns
        the ``quote_loan_on_vesting`` keys plus additional_days, repaid_loan_id (the linked loan's id on
        ``get_loan_hub_of``, 0 when none), repay_amount (its ``full_amount``, raw pair asset; 0 unless the loan is
        live) and net (raw pair asset, negative when the beneficiary pays in more than the new loan pays out).
        ``refusal`` is the rule it would be refused with, in the order ``repay_extend_and_reborrow`` checks them, or
        None: "No active loan", "Loan not active" (liquidated: settle it with ``repay_loan_on_vesting``), the extension
        rules ("Additional days must be greater than zero", "Vesting already ended", "Extension exceeds maximum
        duration"), the loan rules on the extended schedule ("No unclaimed tokens", "pair not active", "Ecosystem
        inactive", "Unauthorized caller", "incorrect loan duration", "Minimum loan amount not met", "pair asset paused",
        "No stable received"), then "insufficient balance" (the beneficiary holds less than ``repay_amount``). The new loan is sized before the
        repay; its length counts as in ``quote_loan_on_vesting``, the extension rules as in ``extend_vesting_period``.
        Rule ``Vesting does not exist``."""
        vid = to_uint(vesting_id, "vestingId")
        days = to_uint(additional_days, "additionalDays")
        v = self.get_vesting_details(vid)
        if is_zero_address(v["creator"]):
            raise ValidationError("Vesting does not exist", f"Vesting {vid} does not exist.")
        loan = self._loan_of(v)
        ext = self._extension_refusal(v, days)
        plan = self._loan_plan(v, days, True)
        err = plan.pop("error")
        live = loan is not None and loan["details"]["active"]
        repay_amount = loan["details"]["full_amount"] if live else 0
        refusal = None
        if loan is None:
            refusal = "No active loan"
        elif not live:
            refusal = "Loan not active"
        elif ext is not None:
            refusal = ext.rule
        elif err is not None:
            refusal = err.rule
        elif self._balance_of(loan["repay_token"], v["beneficiary"]) < repay_amount:
            refusal = "insufficient balance"
        return {**plan, "additional_days": days, "repaid_loan_id": v["active_loan_id"], "repay_amount": repay_amount,
                "net": plan["borrowed"] - repay_amount, "refusal": refusal}


    def get_vesting_details(self, vesting_id: int) -> Dict[str, Any]:
        """The vesting's 14-field schedule (token amounts raw, vested-token decimals). Keys: id, creator, beneficiary,
        token, ecosystem (the main), total_amount, claimed_amount, start_time (unix s), duration_in_days (gradual only),
        unlock_time (cliff only, unix s), is_gradual, active_loan_id (loan id, 0 = none), memo, time_unit (0-3),
        renounced."""
        vid = to_uint(vesting_id, "vestingId")
        return _to_details(vid, self._vesting.functions.getVestingDetails(vid).call())

    def get_vesting_details_batch(self, vesting_ids: Sequence[int]) -> List[Dict[str, Any]]:
        """Several schedules in one call. ``vesting_ids`` must be a list or a tuple (anything else, None included, is
        refused with rule ``vestingIds`` before any read: a string, a set or a dict is never read item by item).
        Reverts "Invalid vesting ID" for an id at or above ``get_vesting_count()``."""
        if not isinstance(vesting_ids, (list, tuple)):
            raise ValidationError("vestingIds", "vestingIds must be an array of vesting ids.")
        ids = [to_uint(x, "vestingIds") for x in vesting_ids]
        rows = self._vesting.functions.getVestingDetailsBatch(ids).call()
        return [_to_details(ids[i], s) for i, s in enumerate(rows)]

    def get_claimable_amount(self, vesting_id: int) -> int:
        """Claimable now (raw, vested-token units). 0 while a loan is linked."""
        return self._vesting.functions.getClaimableAmount(to_uint(vesting_id, "vestingId")).call()

    def get_vested_amount(self, vesting_id: int) -> int:
        """Vested so far, claimed included (raw, vested-token units), on the recorded ``total_amount``."""
        return self._vesting.functions.getVestedAmount(to_uint(vesting_id, "vestingId")).call()

    def get_effective_total_amount(self, vesting_id: int) -> int:
        """What the schedule really holds (raw): after a liquidation, before it is settled, claimed + what the
        liquidation left."""
        return self._vesting.functions.effectiveTotalAmount(to_uint(vesting_id, "vestingId")).call()

    def get_effective_vested_amount(self, vesting_id: int) -> int:
        """``get_vested_amount`` scaled to the effective total (raw)."""
        return self._vesting.functions.effectiveVestedAmount(to_uint(vesting_id, "vestingId")).call()

    def get_active_loan(self, vesting_id: int) -> int:
        """Loan id linked to the vesting (0 = none), on ``get_loan_hub_of(id)``."""
        return self._vesting.functions.getActiveLoan(to_uint(vesting_id, "vestingId")).call()

    def get_loan_hub_of(self, vesting_id: int) -> str:
        """The loan hub the vesting's (latest) loan was taken on; zero if it never had one."""
        return self._vesting.functions.loanHubOf(to_uint(vesting_id, "vestingId")).call()

    def get_vesting_loan(self, vesting_id: int) -> Optional[Dict[str, Any]]:
        """The linked loan ``{hub, loan_id, details, repay_token}`` with its details (debt ``full_amount`` in raw
        pair-asset units), or None when none is linked."""
        return self._loan_of(self.get_vesting_details(vesting_id))

    def get_token_vesting_ids(self, token: str, start_index: int, end_index: int) -> List[int]:
        """Ids of the vestings of ``token`` in ``[start_index, end_index)`` (end <= ``get_token_vesting_count``)."""
        return list(self._vesting.functions.getTokenVestingIds(
            to_address(token, "token"), to_uint(start_index, "startIndex"), to_uint(end_index, "endIndex")).call())

    def get_token_vesting_count(self, token: str) -> int:
        return self._vesting.functions.tokenVestingCount(to_address(token, "token")).call()

    def get_vesting_count(self) -> int:
        """Total vestings ever created (ids are 0..count-1)."""
        return self._vesting.functions.vestingCount().call()

    def get_vestings_by_creator(self, creator: str) -> List[int]:
        """Every vesting id ``creator`` created, in one call (the whole list). A long list can exceed a node's gas cap
        for a read: ``get_vestings_of`` pages it instead."""
        return list(self._vesting.functions.getVestingsByCreator(to_address(creator, "creator")).call())

    def get_vestings_by_beneficiary(self, beneficiary: str) -> List[int]:
        """Every vesting id naming ``beneficiary``, in one call (the whole list). Anyone can create a schedule for any
        wallet, so this list can be made long enough to exceed a node's gas cap for a read: ``get_vestings_of`` pages
        it instead."""
        return list(self._vesting.functions.getVestingsByBeneficiary(to_address(beneficiary, "beneficiary")).call())

    def get_vestings_of(self, wallet: Optional[str] = None, role: Optional[str] = "any", active: Optional[bool] = None,
                        include_loan: bool = True) -> List[Dict[str, Any]]:
        """A wallet's vestings (default: the signer) with their live amounts and linked loans, newest start first.
        Lists whatever the chain holds for the address (the zero address too).
        ``role``: 'beneficiary', 'creator' or 'any' (both lists; None = 'any'); ``active``: True keeps schedules with
        tokens left (``remaining > 0``), False only finished ones, None all; ``include_loan``: read each linked loan
        (None = True).
        Each row is ``get_vesting_details`` plus is_beneficiary, is_creator, claimable (as ``get_claimable_amount``),
        vested (as ``get_effective_vested_amount``), effective_total (as ``get_effective_total_amount``: below
        total_amount after a liquidation not yet settled), remaining (effective_total - claimed_amount, floored at 0),
        end_time (unix s) and loan (``get_vesting_loan`` shape or None). Token amounts raw, vested-token units. Pages
        the wallet's id lists per ``role`` (500 ids per multicall, all at one block), so a list anyone lengthened with
        schedules naming the wallet still loads; the details, amounts and (``include_loan``) linked loans are read in
        pages too."""
        if role is None:
            role = "any"
        if not isinstance(role, str) or role not in ("any", "beneficiary", "creator"):
            raise ValidationError("Invalid role", f"role must be beneficiary, creator or any; got {role}.")
        active = None if active is None else to_bool(active, "active")
        include_loan = to_bool(include_loan, "includeLoan", True)
        who = self._account if wallet is None else to_address(wallet, "wallet")
        block = self.web3.eth.block_number
        as_ben = [] if role == "creator" else self._ids_of("beneficiary", who, block)
        as_cre = [] if role == "beneficiary" else self._ids_of("creator", who, block)
        ids = list(dict.fromkeys([*as_ben, *as_cre]))
        if not ids:
            return []
        me = who.lower()
        fns = self._vesting.functions
        details: List[Dict[str, Any]] = []
        for i in range(0, len(ids), DETAILS_PAGE):
            details += self.get_vesting_details_batch(ids[i:i + DETAILS_PAGE])
        rows = []
        for i in range(0, len(details), READS_PAGE):
            page = details[i:i + READS_PAGE]
            amounts = multicall(self.client, [(getattr(fns, name)(v["id"]), "Vesting") for v in page
                                              for name in ("getClaimableAmount", "effectiveVestedAmount",
                                                           "effectiveTotalAmount")])
            loans = self._loans_of(page) if include_loan else {}
            for j, v in enumerate(page):
                claimable, vested, effective_total = amounts[j * 3:j * 3 + 3]
                rows.append({
                    **v,
                    "is_beneficiary": v["beneficiary"].lower() == me,
                    "is_creator": v["creator"].lower() == me,
                    "claimable": claimable,
                    "vested": vested,
                    "effective_total": effective_total,
                    "remaining": max(effective_total - v["claimed_amount"], 0),
                    "end_time": self._end_time(v),
                    "loan": loans.get(v["id"]),
                })
        if active is not None:
            rows = [r for r in rows if (r["remaining"] > 0) == active]
        rows.sort(key=lambda r: (r["start_time"], r["id"]), reverse=True)
        return rows

    def get_ecosystem(self, main: Optional[str] = None) -> Dict[str, Any]:
        """How vesting records a main (default: the default main): maintoken, factory, mainpair (the main's pair
        asset: what vesting loans pay out and are repaid in), registered (creates on it are accepted)."""
        m = self._resolve_main(main) if main else self.client.mains.default_address()
        maintoken, factory, mainpair = self._vesting.functions.ecosystems(_cs(m)).call()
        return {"maintoken": maintoken, "factory": factory, "mainpair": mainpair,
                "registered": not is_zero_address(maintoken) and maintoken.lower() == m.lower()}

    def get_creation_fee(self, sender: Optional[str] = None) -> int:
        """The exact ``msg.value`` a create needs from ``sender`` (default: the signer), native wei: 0 when fees are
        off or whitelisted."""
        who = self._account if sender is None else to_address(sender, "sender")
        enabled = self.is_fee_enabled()
        amount = self.get_fee_amount()
        whitelisted = self.is_fee_whitelisted(who)
        return amount if enabled and not whitelisted else 0

    def get_fee_amount(self) -> int:
        """Creation fee in native wei (charged only when ``is_fee_enabled``)."""
        return self._vesting.functions.feeAmount().call()

    def is_fee_enabled(self) -> bool:
        return bool(self._vesting.functions.feeEnabled().call())

    def is_fee_whitelisted(self, addr: Optional[str] = None) -> bool:
        """True when ``addr`` (default: the signer) creates vestings without a fee."""
        who = self._account if addr is None else to_address(addr, "addr")
        return bool(self._vesting.functions.feeWhitelist(who).call())

    def get_loan_hub(self) -> str:
        """The loan hub NEW vesting loans open on. Existing loans stay on ``get_loan_hub_of(id)``."""
        return self._vesting.functions.LOAN().call()

    def get_loan_buffer(self) -> int:
        """Days added after the vesting's end when sizing a vesting loan."""
        return self._vesting.functions.loanBuffer().call()

    def get_duration_limits(self) -> Dict[str, int]:
        """The shortest and longest vesting duration, seconds: ``{min, max}`` (cached)."""
        if self._limits is None:
            fns = self._vesting.functions
            self._limits = {"min": fns.MIN_VESTING_DURATION().call(), "max": fns.MAX_VESTING_DURATION().call()}
        return dict(self._limits)


    def _now(self) -> int:
        return self.web3.eth.get_block("latest")["timestamp"]

    def _resolve_main(self, ref: str) -> str:
        try:
            return self.client.mains.get(ref).main
        except BasisError as e:
            msg = str(e)
            if msg.startswith("[basis-sdk] "):
                msg = msg[len("[basis-sdk] "):]
            raise ValidationError("Invalid ecosystem", f"{ref} is not a main: {msg}") from e

    def _check_token(self, token: str, ecosystem: Optional[str] = None) -> str:
        """The main to create under, in this order: ``ecosystem`` (default: the token's own main) registered for
        vesting ("Invalid ecosystem"), then the token: that main itself, or a factory token that trades against it
        ("Not an Ecosystem token", also for a token of another main). An RPC failure is raised as it is."""
        def own() -> str:
            try:
                return self.client.mains.main_of(token)
            except ValidationError as e:
                if e.rule == "Not an Ecosystem token":
                    raise ValidationError("Not an Ecosystem token", f"{token} is not a BASIS token.") from e
                raise

        main = own() if ecosystem is None else self._resolve_main(ecosystem)
        eco = self.get_ecosystem(main)
        if not eco["registered"]:
            raise ValidationError("Invalid ecosystem", f"Main {main} is not registered with the vesting contract.")
        if token.lower() == main.lower():
            return main
        if ecosystem is not None:
            its = own()
            if its.lower() != main.lower():
                raise ValidationError("Not an Ecosystem token",
                                      f"{token} trades against {its}, not {main}: vest it under its own main.")
        known = self._contract(eco["factory"], "Factory").functions.isEcosystemToken(token).call()
        if not known:
            raise ValidationError("Not an Ecosystem token", f"{token} is not a token of {main}'s factory.")
        return main

    def _to_beneficiary(self, beneficiary: Any, field: str) -> str:
        """A beneficiary the caller typed (``to_address`` under ``field``), checksummed; the zero address is refused. A
        BASIS contract or token is refused later, as the last check before the approval (``_refuse_vested_own``, which
        also knows the token's main)."""
        b = to_address(beneficiary, field)
        if is_zero_address(b):
            raise ValidationError("Invalid beneficiary address", f"{field} must not be the zero address.")
        return b

    def _protocol_addresses(self) -> set:
        """The protocol's own contracts and tokens in the address book, lower case: every BASIS contract key (all but
        the outside ``wrappedNative`` and ``erc8004Registry``), and each main with its pair asset and vault. These (and
        the vested token with its main; ``_vested_own`` adds that main's pair asset and vault when the book does not
        list it) are refused as a beneficiary or creator: none of them could ever claim or act on a schedule."""
        book = self.client.addresses
        out = set()

        def add(a: Any) -> None:
            if isinstance(a, str) and not is_zero_address(a):
                out.add(a.lower())

        for k in _PROTOCOL_KEYS:
            add(book.get(k))
        for m in book.get("mains") or []:
            add(m.get("main"))
            add(m.get("pairAsset"))
            add(m.get("vault"))
        return out

    def _vested_own(self, token: str, main: str) -> List[str]:
        """The ``extra`` of ``_refuse_protocol_address`` for a schedule of ``token`` under ``main``: the token and its
        main, plus that main's pair asset and vault when the address book does not list the main (a listed main's are in
        ``_protocol_addresses`` already). One read for such a main (``mains.get``, cached); none for a listed one."""
        m = main.lower()
        listed = self.client.addresses.get("mains") or []
        if any(isinstance(e.get("main"), str) and e["main"].lower() == m for e in listed):
            return [token, main]
        info = self.client.mains.get(main)
        return [token, main, info.pair_asset, info.vault]

    def _refuse_protocol_address(self, addr: str, field: str, extra: Sequence[str], role: str) -> None:
        """``ValidationError("BASIS contract")`` when ``addr`` (the ``field`` the caller gave) is one of
        ``_protocol_addresses`` or of ``extra`` (``_vested_own``: the vested token, its main and that main's pair asset
        and vault): a beneficiary there could never claim, a creator there could never act."""
        a = addr.lower()
        if a not in self._protocol_addresses() and all(x.lower() != a for x in extra):
            return
        why = "tokens vested there could never be claimed" if role == "beneficiary" else "it could never act as the creator"
        raise ValidationError("BASIS contract", f"{field} {addr} is a BASIS contract or token address, not a wallet: {why}.")

    def _refuse_vested_own(self, listed: Sequence[str], fields: Sequence[str], extra: Sequence[str]) -> None:
        """Each of ``listed`` (named ``fields[i]``) as ``_refuse_protocol_address``, with ``extra`` from ``_vested_own``:
        the last check of a create before its approval (the address book and the create's own figures;
        ``_vested_own`` reads only a main the book does not list)."""
        for b, f in zip(listed, fields):
            self._refuse_protocol_address(b, f, extra, "beneficiary")

    @staticmethod
    def _check_memo(memo: Any, field: str) -> None:
        """A memo is a ``str`` of at most ``MAX_MEMO_BYTES`` (256) UTF-8 bytes; ``field`` is the rule for a non-string
        and for a string UTF-8 cannot hold (``check_text``: a lone surrogate)."""
        if not isinstance(memo, str):
            raise ValidationError(field, f"{field} must be a string.")
        check_text(memo, field)
        size = len(memo.encode("utf-8"))
        if size > MAX_MEMO_BYTES:
            raise ValidationError("Memo too long",
                                  f"{field} is {size} bytes of UTF-8; a vesting memo holds at most {MAX_MEMO_BYTES}.")

    def _check_batch(self, beneficiaries: Any, total_amounts: Any,
                     user_memos: Any) -> Tuple[List[str], List[Any], List[str]]:
        """First the three lists are lists or tuples (rule: the list's name; a string, a set or any other value is
        refused, never read item by item: a memo string would become one memo per character, a set would pair amounts
        with the wrong wallets), then, in order: "Arrays length mismatch", then "Empty beneficiaries
        array"; then each beneficiary and memo; then ``batchSize`` (the batch must fit one transaction: at most
        ``BATCH_LIMIT`` rows and the gas estimate at most ``TX_GAS_LIMIT``). Returns copies of the lists as lists, the
        beneficiaries checksummed."""
        beneficiaries = _list_of(beneficiaries, "beneficiaries")
        total_amounts = _list_of(total_amounts, "totalAmounts")
        user_memos = _list_of(user_memos, "userMemos")
        if len(beneficiaries) != len(total_amounts) or len(beneficiaries) != len(user_memos):
            raise ValidationError("Arrays length mismatch",
                                  "beneficiaries, totalAmounts and userMemos must have the same length.")
        if len(beneficiaries) == 0:
            raise ValidationError("Empty beneficiaries array", "beneficiaries must not be empty.")
        listed = [self._to_beneficiary(b, f"beneficiaries[{i}]") for i, b in enumerate(beneficiaries)]
        for i, m in enumerate(user_memos):
            self._check_memo(m, f"userMemos[{i}]")
        n = len(listed)
        if n > BATCH_LIMIT:
            raise ValidationError(
                "batchSize",
                f"One transaction takes at most {BATCH_LIMIT} vestings; got {n}. Split the batch: send the other "
                f"{n - BATCH_LIMIT} in a second one.")
        gas = BATCH_GAS + sum(ROW_GAS + len(m.encode("utf-8")) * MEMO_GAS_PER_BYTE for m in user_memos)
        if gas > TX_GAS_LIMIT:
            raise ValidationError(
                "batchSize",
                f"{n} vestings with these memos need about {gas} gas, above the {TX_GAS_LIMIT} one transaction can use. "
                "Split the batch, or shorten the memos.")
        return listed, total_amounts, user_memos

    def _check_gradual_schedule(self, start: int, days: int) -> None:
        """A gradual schedule, in order: ``Start too far`` (a start at most 7 days after the block it is mined in), the
        duration, then ``End too far`` (the end at most 990 days out: a delayed start cannot use the longest duration).
        Both are upper bounds, measured from the latest block: on a chain with about 10 blocks a second the create can
        mine in a block with the same time, where they are tightest."""
        mx = self._vesting.functions.MAX_START_DELAY().call()
        latest = self._now()
        lim = self.get_duration_limits()
        if start > latest + mx:
            raise ValidationError(
                "Start too far",
                f"startTime {start} is more than {mx // DAY} days after the latest block ({latest}); pass unix seconds, "
                "not milliseconds.")
        secs = days * DAY
        if secs < lim["min"]:
            raise ValidationError("Vesting duration must be at least 1 hour",
                                  f"durationInDays must be at least {(lim['min'] + DAY - 1) // DAY}.")
        if secs > lim["max"]:
            raise ValidationError("Duration too long",
                                  f"durationInDays must be at most {lim['max'] // DAY}, got {days}.")
        begin = max(start, latest)
        if begin + secs > latest + lim["max"]:
            raise ValidationError("End too far", f"A start at {begin} plus {days} days ends more than {lim['max'] // DAY} "
                                                 f"days after the latest block ({latest}): start sooner or shorten it.")

    def _check_still_running(self, v: Dict[str, Any]) -> None:
        """A top-up mines in a later block (timestamp >= latest + 1) and is taken only while the vesting has not ended
        at that block's time ("Vesting already ended")."""
        now = self._now() + 1
        end = self._end_time(v)
        if now >= end:
            raise ValidationError("Vesting already ended", f"Vesting {v['id']} has already ended: its end {end} is not "
                                                           f"after the next block's time {now}.")

    def _check_cliff_unlock(self, unlock: int) -> None:
        """A cliff's unlock, as it is checked in the block the create mines in. The lower bounds ("Unlock time must be
        in the future", the minimum duration) are measured from the next block (latest + 1: a later block only tightens
        them); the upper bound ("Duration too long", at most the maximum duration) from the latest block (on a chain
        with about 10 blocks a second the create can mine in a block with the same time, where it is tightest)."""
        lim = self.get_duration_limits()
        latest = self._now()
        nxt = latest + 1
        if unlock <= nxt:
            raise ValidationError("Unlock time must be in the future",
                                  f"unlockTime {unlock} is not after the next block's time {nxt}.")
        if unlock - nxt < lim["min"]:
            raise ValidationError("Vesting duration must be at least 1 hour",
                                  f"unlockTime must be at least {lim['min']}s after the next block ({nxt}).")
        if unlock - latest > lim["max"]:
            raise ValidationError("Duration too long",
                                  f"unlockTime must be at most {lim['max'] // DAY} days after the latest block ({latest}).")

    def _check_extension(self, v: Dict[str, Any], days: int) -> None:
        """Extension rules: days > 0, not ended (by the next block), remaining whole days + days <= the max (from the
        latest block)."""
        error = self._extension_refusal(v, days)
        if error is not None:
            raise error

    def _extension_refusal(self, v: Dict[str, Any], days: int) -> Optional[ValidationError]:
        """The first extension rule ``days`` would break (``_check_extension``'s rules, in its order), or None. An
        extension is taken only while the vesting has not ended: a lower bound on the time, measured from the next
        block (latest + 1; a later block only tightens it). The maximum (990 days less the whole days left) is an
        upper bound: on a chain with about 10 blocks a second the extension can mine in a block with the latest block's
        time, which leaves the most days (so the smallest maximum): the days left are measured from the latest block."""
        if days == 0:
            return ValidationError("Additional days must be greater than zero", "additionalDays must be at least 1.")
        mx = self.get_duration_limits()["max"]
        latest = self._now()
        nxt = latest + 1
        end = self._end_time(v)
        if nxt >= end:
            return ValidationError("Vesting already ended", f"Vesting {v['id']} has already ended: its end {end} is not "
                                                            f"after the next block's time {nxt}.")
        max_extension = mx // DAY - (end - latest) // DAY
        if days > max_extension:
            return ValidationError("Extension exceeds maximum duration",
                                   f"Vesting {v['id']} can be extended by at most {max_extension} days.")
        return None

    def _ids_of(self, role: str, who: str, block: int) -> List[int]:
        """``who``'s vesting ids as beneficiary or creator, paged (``IDS_PAGE`` per multicall), every read at ``block``
        (a schedule moved to another beneficiary meanwhile cannot shift the list between pages)."""
        fns = self._vesting.functions
        if role == "beneficiary":
            count, at = pinned_call(fns.beneficiaryCount(who), block), fns.beneficiaryVestings
        else:
            count, at = pinned_call(fns.creatorCount(who), block), fns.creatorVestings
        out: List[int] = []
        for start in range(0, count, IDS_PAGE):
            out += multicall(self.client, [(at(who, i), "Vesting") for i in range(start, min(start + IDS_PAGE, count))],
                             block)
        return out

    @staticmethod
    def _end_time(v: Dict[str, Any]) -> int:
        return v["start_time"] + v["duration_in_days"] * DAY if v["is_gradual"] else v["unlock_time"]

    def _prepare_create(self, token: str, total: int) -> int:
        """Balance check, approval and the exact fee for a create."""
        address = self._address
        self._check_balance(token, total)
        fee = self.get_creation_fee()
        if fee > 0:
            native = self.web3.eth.get_balance(self._account)
            if native < fee:
                raise ValidationError("Insufficient fee", f"The vesting fee is {fee} wei; the wallet holds {native}.")
        self._ensure_allowance(token, address, total)
        return fee

    def _check_balance(self, token: str, amount: int) -> None:
        b = self._balance_of(token)
        if b < amount:
            raise ValidationError("insufficient balance", f"Needs {amount} of {token} (raw); the wallet holds {b}.")

    def _to_token_amount(self, token: str, value: TokenAmountInput, field: str,
                         zero_rule: str = "Amount must be greater than zero") -> int:
        """A raw int as is; a human str / float / Decimal scaled by the token's decimals. The decimals are read outside
        the parse (an RPC failure is raised as it is, not reported as a bad amount)."""
        decimals = 18 if isinstance(value, int) else int(self.client.mains.token_meta(token)["decimals"])
        try:
            raw = parse_amount(value, decimals)
        except ValidationError as e:
            raise ValidationError(field, f"{field} is not a valid amount: {value}.") from e
        if raw <= 0:
            raise ValidationError(zero_rule, f"{field} must be greater than zero.")
        return raw

    @staticmethod
    def _to_time_unit(u: TimeUnitInput) -> int:
        if isinstance(u, str):
            v = VESTING_TIME_UNIT.get(u.lower())
            if v is None:
                raise ValidationError("Invalid time unit", f"timeUnit must be second, minute, hour or day; got {u}.")
            return v
        if isinstance(u, bool) or not isinstance(u, (int, float)) or (isinstance(u, float) and not u.is_integer()) \
                or u < 0 or u > 3:
            raise ValidationError("Invalid time unit", f"timeUnit must be 0-3, got {u}.")
        return int(u)

    def _load_existing(self, vid: int) -> Dict[str, Any]:
        """The schedule a write acts on (signer checked first, so a wallet-less client gets WalletRequiredError)."""
        _ = self._account
        v = self.get_vesting_details(vid)
        if is_zero_address(v["creator"]):
            raise ValidationError("Vesting does not exist", f"Vesting {vid} does not exist.")
        return v

    def _require_caller(self, expected: str, rule: str, message: str) -> None:
        if expected.lower() != self._account.lower():
            raise ValidationError(rule, message)

    def _prepare_reconcile(self, loan: Dict[str, Any]) -> None:
        """Before settling ``loan`` from this wallet: a live one needs its pair asset movable ('pair asset paused'
        while its issuer has paused it) and its debt (``full_amount``, raw pair asset) held and approved."""
        if not loan["details"]["active"]:
            return
        refuse_paused_asset(self.client, loan["repay_token"], None, "the loan cannot be repaid in it now")
        self._check_balance(loan["repay_token"], loan["details"]["full_amount"])
        self._ensure_allowance(loan["repay_token"], self._address, loan["details"]["full_amount"])

    def _reconciled(self, tx: Dict[str, Any], fmt) -> Dict[str, Any]:
        """What settling a vesting loan did (a liquidated loan, or a repay), from the transaction's events."""
        vc = self._vesting
        liq = self._events(tx, vc, "VestingLoanLiquidated")
        if liq:
            return {"liquidated": True, "recovered": fmt(liq[0]["recovered"]), "loan_id": liq[0]["loanId"]}
        repaid = self._expect_event(tx, vc, "LoanRepaid", "Vesting", ("liquidated", "recovered", "loan_id"))
        return {"liquidated": False if repaid else None, "recovered": fmt(0) if repaid else None,
                "loan_id": repaid["loanId"] if repaid else None}

    @staticmethod
    def _require_no_loan(v: Dict[str, Any], rule: str = "Loan open: repay first") -> None:
        """Refused while any loan is linked, liquidated or not (on chain some of these calls fail with no reason)."""
        if v["active_loan_id"] != 0:
            raise ValidationError(
                rule,
                f"Vesting {v['id']} has a loan linked (hub id {v['active_loan_id']}): repay it (or settle a "
                "liquidation) with repayLoanOnVesting first.",
            )

    def _loan_of(self, v: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if v["active_loan_id"] == 0:
            return None
        address = self._address
        hub = self.get_loan_hub_of(v["id"])
        eco = self._vesting.functions.ecosystems(_cs(v["ecosystem"])).call()
        if is_zero_address(hub):
            raise BasisError(f"[basis-sdk] Vesting {v['id']} links loan {v['active_loan_id']} but has no loanHubOf.")
        details = self._contract(hub, "LoanHub").functions.getUserLoanDetails(address, v["active_loan_id"]).call()
        return {"hub": hub, "loan_id": v["active_loan_id"], "details": _to_hub_details(details), "repay_token": eco[2]}

    def _loans_of(self, rows: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        """The linked loans of ``rows`` by vesting id, in two multicalls (hubs and repay tokens, then the loan
        details)."""
        linked = [v for v in rows if v["active_loan_id"] != 0]
        if not linked:
            return {}
        address = self._address
        fns = self._vesting.functions
        refs = multicall(self.client, [c for v in linked for c in (
            (fns.loanHubOf(v["id"]), "Vesting"), (fns.ecosystems(_cs(v["ecosystem"])), "Vesting"))])
        hubs = []
        for i, v in enumerate(linked):
            hub = refs[i * 2]
            if is_zero_address(hub):
                raise BasisError(f"[basis-sdk] Vesting {v['id']} links loan {v['active_loan_id']} but has no loanHubOf.")
            hubs.append(hub)
        details = multicall(self.client, [
            (self._contract(hubs[i], "LoanHub").functions.getUserLoanDetails(address, v["active_loan_id"]), "LoanHub")
            for i, v in enumerate(linked)])
        return {v["id"]: {"hub": hubs[i], "loan_id": v["active_loan_id"], "details": _to_hub_details(details[i]),
                          "repay_token": refs[i * 2 + 1][2]} for i, v in enumerate(linked)}

    def _loan_plan(self, v: Dict[str, Any], extra_days: int, reborrow: bool = False) -> Dict[str, Any]:
        """The loan a vesting borrow would open for ``v`` with its end moved by ``extra_days`` (a reborrow after an
        extension), and the first rule it would fail (``error``), in the order ``quote_loan_on_vesting`` lists them
        ("Active loan exists" is skipped for a reborrow, which repays the open loan first; the issuer pause is read only
        when the rules before it pass). The length (whole days left to the end + the loan buffer) is quoted from the
        next block (latest + 1); the length rule checks its minimum from there (a later block only shortens the loan)
        and its maximum from the latest block (on a chain with about 10 blocks a second the loan can open in a block
        with the same time: one day longer when the end is a whole number of days away); "No stable received" is
        checked on that longest length too (a longer loan costs more)."""
        main = _cs(v["ecosystem"])
        token_amount = max(v["total_amount"] - v["claimed_amount"], 0)
        hub = self.get_loan_hub()
        buffer = self.get_loan_buffer()
        latest = self._now()
        end = self._end_time(v) + extra_days * DAY

        def length_at(t: int) -> int:
            return ((end - t) // DAY if end > t else 0) + buffer

        days = length_at(latest + 1)
        longest = length_at(latest)
        mf = self._contract(main, "MainToken").functions
        live = is_pair_active(self.client, main)
        eco = self._contract(hub, "LoanHub").functions.ecosystems(main).call()
        main_hub = mf.LOAN().call()
        min_days, max_days = mf.minDaysLoan().call(), mf.maxDaysLoan().call()
        minimum_loan = mf.minimumLoan().call()
        static_fee = mf.staticFeePercentage().call()
        value = 0 if token_amount == 0 else mf.getColleteralValue(token_amount, _cs(v["token"])).call()
        in_range = days >= min_days and longest <= max_days

        def dynamic_for(n: int) -> int:
            try:
                return mf.getDynamicFee(value, n).call()
            except Exception:  # noqa: BLE001
                if in_range:
                    raise
                return 0

        static_part = value * static_fee // 10_000
        fee = static_part + dynamic_for(days)
        fee_longest = fee if longest == days else static_part + dynamic_for(longest)
        error = None
        if not reborrow and v["active_loan_id"] != 0:
            error = ValidationError("Active loan exists",
                                    f"Vesting {v['id']} already has a loan (hub id {v['active_loan_id']}): repay it "
                                    "first, or refinance with repay_extend_and_reborrow.")
        elif token_amount == 0:
            error = ValidationError("No unclaimed tokens", f"Vesting {v['id']} has nothing left to borrow against.")
        elif not live:
            error = pair_not_active(main)
        elif not eco[2]:
            error = ValidationError("Ecosystem inactive", f"Main {main} is not active on the loan hub {hub}.")
        elif main_hub.lower() != hub.lower():
            error = ValidationError("Unauthorized caller",
                                    f"Main {main} takes new loans from hub {main_hub}, not the vesting's hub {hub}.")
        elif not in_range:
            shown = days if days < min_days else longest
            error = ValidationError("incorrect loan duration",
                                    f"The vesting loan would run {shown} days (days left to the end + loanBuffer "
                                    f"{buffer}), outside this main's {min_days}-{max_days}.")
        elif value < minimum_loan:
            error = ValidationError("Minimum loan amount not met",
                                    f"The unclaimed tokens are worth {value}, below the minimum loan {minimum_loan} "
                                    "(raw pair asset).")
        elif is_asset_paused(self.client, eco[0]):
            error = pair_asset_paused(eco[0], None, "no vesting loan can pay it out")
        elif fee_longest >= value:
            error = ValidationError("No stable received",
                                    f"The loan fees {fee_longest} would take the whole collateral value {value} (raw "
                                    "pair asset).")
        return {"vesting_id": v["id"], "hub": hub, "main": main, "token": v["token"], "token_amount": token_amount,
                "loan_duration_days": days, "collateral_value": value, "fee": fee,
                "borrowed": value - fee if value > fee else 0, "meets_minimum": value >= minimum_loan, "error": error}

    def _created_ids(self, tx: Dict[str, Any], fields: Sequence[str]) -> Optional[List[int]]:
        """The vesting ids from a create's events (None and a warning when there is none)."""
        ids = [e["vestingId"] for e in self._events(tx, self._vesting, "VestingCreated")]
        if ids:
            return ids
        add_missing_event_warning(tx, "Vesting", "VestingCreated", fields)
        return None

    def _token_fmt(self, v: Dict[str, Any]):
        """Formatter for the vested token of ``v`` (resolved before a write sends)."""
        return amount_of(self.client, v["token"], self.client.mains.get(v["ecosystem"]))

    def _loan_fmts(self, v: Dict[str, Any]) -> Dict[str, Any]:
        """Formatters for a vesting loan's figures: the vested token and the main's pair asset (resolved before sending)."""
        info = self.client.mains.get(v["ecosystem"])
        return {"token": amount_of(self.client, v["token"], info), "pair": pair_amount_of(info)}

    def _loan_taken(self, tx: Dict[str, Any], fmts: Dict[str, Any]) -> Dict[str, Any]:
        ev = self._expect_event(tx, self._vesting, "LoanTaken", "Vesting",
                                ("loan_id", "hub", "token_amount", "loan_duration_days", "borrowed"))
        return {**tx, "loan_id": ev["loanId"] if ev else None, "hub": ev["hub"] if ev else None,
                "token_amount": opt_amount(fmts["token"], ev["tokenAmount"] if ev else None),
                "loan_duration_days": ev["loanDurationDays"] if ev else None,
                "borrowed": opt_amount(fmts["pair"], ev["borrowed"] if ev else None)}
