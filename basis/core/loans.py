"""Loan reads shared by trading (leverage) and the loan modules (TypeScript SDK: src/core/loans.ts).

Each user has two books on a main: leverage positions and loans (``read_core_loan``; for a ``client.loans`` loan use
``loans.get_core_loan``). Positions are dicts with snake_case keys; amounts raw (collateral in the token's units, debt
in the pair asset):

    user, token (the bought / collateral token), collateral_amount, liquidated_amount,
    full_amount (debt to repay, raw pair asset), borrowed_amount (raw pair asset),
    liquidation_time (unix seconds), liquidation_claim (tokens claimable after a liquidation),
    is_liquidated, active, creation_time, time_of_closure,
    leverage_buy_amount / cashed_out (raw pair asset put in at open / taken out so far by partial sells).
"""

from typing import Any, Callable, Dict, Optional

from web3 import Web3

from ..config.addresses import is_zero_address
from .errors import BasisRpcError, NotDeployedError, ValidationError
from .revert import classify_error, decode_revert, is_revert_named
from .units import max_with_slippage, to_address, to_uint


def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


def is_pair_active(client, main: str) -> bool:
    """True when ``main``'s pair is live (not paused), read now: the mains cache may be stale. While the pair is paused,
    new loans, top-ups, extensions and partial sells on it fail; repaying still works."""
    swap = client.addresses["swap"]
    if is_zero_address(swap):
        raise NotDeployedError("Swap", client.chain_id)
    return bool(client.mains._contract(swap, "Swap").functions.isMain(_cs(main)).call())


def pair_not_active(main: str, symbol: Optional[str] = None) -> ValidationError:
    """The 'pair not active' refusal of a credit write on ``main`` (``symbol`` names it in the message)."""
    who = f"{symbol} ({main})" if symbol else f"Main {main}"
    return ValidationError(
        "pair not active",
        f"{who} is on a paused pair: new loans, top-ups, extensions and partial sells are refused while it is paused "
        "(repaying still works).")


_PAUSED_CALL = "0x5c975abb"


def is_asset_paused(client: Any, asset: str) -> bool:
    """True when the issuer of ``asset`` (a main's pair asset: USDG and tokenized stocks can be paused) has paused it.
    A token without ``paused()``, or one whose read reverts, counts as live; an RPC failure raises ``BasisRpcError``
    (nothing was sent). While the asset is paused it cannot move, so writes that pay it in or out refuse before any
    approval (``refuse_paused_asset``). TS: ``isAssetPaused`` (core/loans.ts)."""
    try:
        r = client.web3.eth.call({"to": _cs(asset), "data": _PAUSED_CALL})
    except Exception as e:  # noqa: BLE001
        err = classify_error(e, "ERC20")
        if isinstance(err, BasisRpcError):
            raise err from e
        return False
    return len(r) >= 32 and int.from_bytes(bytes(r[:32]), "big") == 1


def pair_asset_paused(asset: str, symbol: Optional[str], effect: str) -> ValidationError:
    """The 'pair asset paused' refusal: ``asset`` (named ``symbol`` when known) is paused by its issuer, so ``effect``
    (what the write cannot do). TS: ``pairAssetPaused``."""
    return ValidationError("pair asset paused", pair_asset_paused_message(asset, symbol, effect))


def pair_asset_paused_message(asset: str, symbol: Optional[str], effect: str) -> str:
    """The message of ``pair_asset_paused``, for a refusal list that carries its own messages (``check_buy``). TS:
    ``pairAssetPausedMessage``."""
    who = f"{symbol} ({asset})" if symbol else f"The pair asset {asset}"
    return f"{who} is paused by its issuer: it cannot move until it is unpaused, so {effect}. Nothing was sent."


def refuse_paused_asset(client: Any, asset: str, symbol: Optional[str], effect: str) -> None:
    """Raises ``pair_asset_paused(asset, symbol, effect)`` when the issuer has paused ``asset`` (``is_asset_paused``).
    TS: ``refusePausedAsset``."""
    if is_asset_paused(client, asset):
        raise pair_asset_paused(asset, symbol, effect)


def read_core_loan(client, main: str, user: str, loan_id: int, is_leverage: bool) -> Dict[str, Any]:
    """Loan (or, with ``is_leverage``, leverage position) ``loan_id`` of ``user`` on ``main``. ``main`` and ``user`` go
    through ``to_address``."""
    main = to_address(main, "main")
    user = to_address(user, "user")
    fns = client.mains._contract(main, "MainToken").functions
    fn = fns.leverages if is_leverage else fns.loans
    r = fn(user, int(loan_id)).call()
    return {
        "user": r[0], "token": r[1], "collateral_amount": r[2], "liquidated_amount": r[3], "full_amount": r[4],
        "borrowed_amount": r[5], "liquidation_time": r[6], "liquidation_claim": r[7], "is_liquidated": r[8],
        "active": r[9], "creation_time": r[10], "time_of_closure": r[11],
        "leverage_buy_amount": r[12][0], "cashed_out": r[12][1],
    }


def get_partial_loan_sell_amounts(client, main: str, loan_id: int, user: str, pct: int,
                                  is_leverage: bool) -> Dict[str, int]:
    """What a partial sell of ``pct`` % (1-100) of loan or leverage position ``loan_id`` of ``user`` on ``main`` would
    use up and leave to sell, raw: {main_used (main tokens used to clear this share of the debt), tokens_used
    (collateral used for it; equals main_used for main collateral), residual_tokens (collateral left to sell)}. A loan
    that is not open (repaid, sold or never opened) raises ValidationError("Loan not active"). A bad percentage, a
    liquidated loan and a sale with no profit come back as BasisContractError ("Invalid percentage", "Already
    liquidated", "cannot sell off with no profit"). Raises NotDeployedError before any RPC when the main lens is not
    deployed on this chain, and ValidationError("percentage") before any read for a ``pct`` that is not a whole number
    (a fraction, a bool, a string or a negative); ``main`` and ``user`` go through ``to_address``."""
    lens = client.addresses["mainLens"]
    if is_zero_address(lens):
        raise NotDeployedError("MainLens", client.chain_id)
    p = to_uint(pct, "percentage")
    main = to_address(main, "main")
    user = to_address(user, "user")
    if 1 <= p <= 100:
        loan = read_core_loan(client, main, user, loan_id, is_leverage)
        if not loan["active"] and not loan["is_liquidated"]:
            what = "Leverage position" if is_leverage else "Loan"
            raise ValidationError("Loan not active",
                                  f"{what} {loan_id} of {user} is not active (repaid, sold or never opened).")
    c = client.mains._contract(lens, "MainLens")
    try:
        main_used, tokens_used, residual = c.functions.getPartialLoanSellAmounts(
            _cs(main), int(loan_id), _cs(user), p, bool(is_leverage)).call()
    except Exception as e:  # noqa: BLE001
        raise decode_revert(e, contract="MainLens", function_name="getPartialLoanSellAmounts", abi=c.abi) from e
    return {"main_used": main_used, "tokens_used": tokens_used, "residual_tokens": residual}


def estimate_partial_sell(client, main: str, loan: Dict[str, Any], user: str, loan_id: int, pct: int,
                          is_leverage: bool) -> int:
    """Gross pair asset a partial sell of ``pct`` % of loan or leverage position ``loan_id`` of ``user`` on ``main``
    produces (before the exit fee). It may come out a little low (dust), never high, so a minimum derived from it is
    never above what the sale pays. ``loan`` needs the position's ``token`` and ``full_amount`` (for a ``client.loans``
    loan use ``loans.quote_partial_sell_loan``). ``pct`` as in ``get_partial_loan_sell_amounts``."""
    p = to_uint(pct, "percentage")
    main = to_address(main, "main")
    user = to_address(user, "user")
    amounts = get_partial_loan_sell_amounts(client, main, loan_id, user, p, is_leverage)
    r0, r1 = client.mains._contract(main, "MainToken").functions.getCurveReserves().call()[:2]
    payment = loan["full_amount"] * p // 100
    r0_after = r0 - amounts["main_used"]
    r1_after = r1 - payment
    main_in = amounts["residual_tokens"]
    if loan["token"].lower() != main.lower():
        tf = client.mains._contract(loan["token"], "FactoryToken").functions
        t0, t1 = tf.getCurveReserves().call()[:2]
        mag = tf.hybridMultiplier().call()
        rev = tf.reversed().call()
        shift = amounts["tokens_used"] * mag // 100
        t0_after = t0 + shift if rev else t0 - shift
        t1_after = t1 - amounts["main_used"]
        res = amounts["residual_tokens"]
        main_in = 0 if res == 0 else res * t1_after // (t0_after + res)
    if main_in == 0:
        return 0
    return main_in * r1_after // (r0_after + main_in)


def quote_partial_sell(client, main: str, loan: Dict[str, Any], user: str, loan_id: int, pct: int, is_leverage: bool,
                       seller: str) -> Dict[str, int]:
    """What a partial sell of ``pct`` % of loan or leverage position ``loan_id`` of ``user`` on ``main`` pays ``seller``
    (the wallet behind it): the gross of ``estimate_partial_sell``, less the exit fee. A leverage position pays the fee
    on its profit only, so a sale at a loss pays none.

    ``loan`` as in ``estimate_partial_sell``; a leverage position also needs its ``leverage_buy_amount`` and
    ``cashed_out``. Returns {gross (before the fee), taxable (the part charged: all of it on a ``client.loans`` loan,
    the profit on a leverage position), tax_bps (the seller's own sell fee rate for the collateral token, in bps, read
    live; 0 for a fee-whitelisted wallet), fee (the exit fee: ``tax_bps`` of ``taxable``), net (gross - fee: what
    ``min_out`` binds)}, raw pair asset."""
    p = to_uint(pct, "percentage")
    main = to_address(main, "main")
    user = to_address(user, "user")
    seller = to_address(seller, "seller")
    gross = estimate_partial_sell(client, main, loan, user, loan_id, p, is_leverage)
    tax_bps = client.taxes.get_tax_rate(loan["token"], seller)
    taxable = gross
    if is_leverage:
        bought = loan.get("leverage_buy_amount") or 0
        cashed = loan.get("cashed_out") or 0
        allowance = bought - cashed if bought > cashed else 0
        taxable = gross - allowance if gross > allowance else 0
    fee = taxable * tax_bps // 10_000
    return {"gross": gross, "taxable": taxable, "tax_bps": tax_bps, "fee": fee, "net": gross - fee}


def core_extend_refusal(client, main: str, loan: Dict[str, Any], days: int, pay_in: bool, refinance: bool,
                        q: Dict[str, Any]) -> Optional[ValidationError]:
    """The refusal an extension of ``loan`` on ``main`` by ``days`` would meet, or None when it goes through: "Must be
    refinance if not extending time" (0 days is only a refinance with no pay-in), "incorrect loan duration", "Loan not
    active"; then, for an add-to-debt or refinance the loan cannot cover, "Not enough collateral to cover fees" (add to
    debt), or "No equity", "Minimum surplus not met", "Time remaining too short", "Fees exceed surplus value"
    (refinance). A pay-in passes once the days and the loan are valid. The days count from the due date, or from the
    time of the transaction when the loan is past due.

    ``loan`` (raw units): active, token (the collateral token), collateral_amount, full_amount (debt, raw pair asset),
    liquidation_time. ``q``: possible and fee (whether the extension can be charged to the loan, and its fee)."""
    if days == 0 and (not refinance or pay_in):
        return ValidationError("Must be refinance if not extending time",
                               "0 days is only allowed for a refinance without a pay-in.")
    ef = client.mains._contract(main, "MainToken").functions
    min_days, max_days = ef.minDaysLoan().call(), ef.maxDaysLoan().call()
    if days > 0 and (days < min_days or days > max_days):
        return ValidationError("incorrect loan duration", f"Days must be {min_days}-{max_days} on this main, got {days}.")
    if not loan["active"]:
        return ValidationError("Loan not active", "The loan is not active (repaid, sold or liquidated).")
    if pay_in or q["possible"]:
        return None
    value = ef.getColleteralValue(int(loan["collateral_amount"]), _cs(loan["token"])).call()
    u = client.mains.get(main).pair_symbol
    full, fee = loan["full_amount"], q["fee"]
    if not refinance:
        return ValidationError(
            "Not enough collateral to cover fees",
            f"Debt {full} + fee {fee} is not below the collateral value {value} (raw {u}); pay the fee from the "
            "wallet (pay_in_pair_asset).")
    if value <= full:
        return ValidationError("No equity", f"The collateral is worth {value}, not above the debt {full} (raw {u}): "
                                            "nothing to refinance.")
    surplus = value - full
    min_surplus = ef.baseReserve1().call() // 10000
    if surplus < min_surplus:
        return ValidationError("Minimum surplus not met", f"The surplus {surplus} is below the minimum {min_surplus} (raw {u}).")
    now = client.web3.eth.get_block("latest")["timestamp"]
    remaining = (loan["liquidation_time"] - now) // _DAY if loan["liquidation_time"] > now else 0
    if days == 0 and remaining < min_days:
        return ValidationError("Time remaining too short",
                               f"Only {remaining} days left (minimum {min_days}); refinance with days added.")
    return ValidationError("Fees exceed surplus value", f"The refinance fees {fee} take the whole surplus {surplus} (raw {u}).")


_DAY = 86400


def read_repay_sizing(client, exec_: str, token_in: str, token_out: str) -> Dict[str, Any]:
    """What ``size_repay_with`` needs about ``exec_``'s route ``token_in`` -> ``token_out``, read now (every key but
    ``buffer_bps``): one whole ``token_in``, the caps, the fee and, on a chain with
    ``chain_config.contract_block_is_l1``, what is left of the caps (``in_left`` / ``out_left``)."""
    f = client.mains._contract(exec_, "HopExecutor").functions
    max_in, max_out = f.getCaps(_cs(token_in), _cs(token_out)).call()
    s: Dict[str, Any] = {
        "unit": 10 ** int(client.mains.token_meta(token_in)["decimals"]), "max_in": max_in, "max_out": max_out,
        "fee_ppm": f.feePpm().call(), "token_in": token_in, "token_out": token_out}
    if getattr(client.chain_config, "contract_block_is_l1", False):
        s["in_left"], s["out_left"] = f.capsLeft(_cs(token_in), _cs(token_out)).call()
    return s


def size_repay_with(quote: Callable[[int], int], target: int, sizing: Dict[str, Any]) -> int:
    """The ``token_in`` to send through an outside route to repay ``target`` of ``token_out`` (raw units): the least
    amount whose quote reaches ``target`` (exact to one raw unit of ``token_out``), plus ``buffer_bps``, trimmed to the
    route's caps (``max_in``, and ``max_out`` on the payout; what is left of them with ``in_left`` / ``out_left``) so
    the buffered amount still goes through. ``quote`` is ``quoteHop`` (net of the route fee). A target over the route's
    cap raises "above cap"; one within the full caps but over what other swaps left in the current window says to wait
    about 12 seconds.

    ``sizing``: ``unit`` (one whole ``token_in``, the first guess), ``max_in`` / ``max_out`` (the route's caps: the
    most ``token_in`` it takes in, and ``token_out`` it pays out before its fee, per window; about 12 s on Robinhood
    Chain), optional ``in_left`` / ``out_left`` (what is left of the caps in the current window: what a repay sent now
    meets; omitted or None, the full caps apply), ``fee_ppm`` (the route's fee, parts per million: quotes are net of it,
    ``max_out`` is not), ``buffer_bps`` (headroom on top of the least amount: the price may move before the tx lands;
    the leftover is refunded), ``token_in``, ``token_out``."""
    fee_ppm = sizing["fee_ppm"]
    token_in, token_out = sizing["token_in"], sizing["token_out"]

    def net(out: int) -> int:
        return out - out * fee_ppm // _PPM

    if target > net(sizing["max_out"]):
        raise ValidationError("above cap", f"Repaying {target} of {token_out} is above the route's {net(sizing['max_out'])} cap.")
    in_left, out_left = sizing.get("in_left"), sizing.get("out_left")
    max_in = in_left if in_left is not None and in_left < sizing["max_in"] else sizing["max_in"]
    max_out = out_left if out_left is not None and out_left < sizing["max_out"] else sizing["max_out"]
    wait = CAP_WAIT_HINT if max_in < sizing["max_in"] or max_out < sizing["max_out"] else ""
    max_net = net(max_out)
    if target > max_net or max_in == 0:
        raise ValidationError("above cap", f"Repaying {target} of {token_out} is above what the route has left now "
                                           f"({max_in} of {token_in} in, {max_net} of {token_out} out).{wait}")
    lo = q_lo = 0
    prev = q_prev = 0
    hi: Optional[int] = None
    q_hi = 0
    top = max_in + 1
    last_hi = False
    x = min(sizing["unit"], max_in)
    for _ in range(40):
        try:
            q = quote(x)
        except Exception as e:  # noqa: BLE001
            if not is_revert_named(e, "above cap") or x - lo <= 1:
                raise
            top = x
            x = lo + (x - lo) // 2
            continue
        if q >= target:
            hi, q_hi, last_hi = x, q, True
        else:
            if lo > 0:
                prev, q_prev = lo, q_lo
            lo, q_lo, last_hi = x, q, False
        if hi is not None and (hi - lo <= 1 or q_hi == target):
            break
        if q_lo == 0:
            low = lo * 2
        elif prev > 0 and q_lo > q_prev:
            low = lo + (lo - prev) * (target - q_lo) // (q_lo - q_prev)
        else:
            low = lo * target // q_lo
        if hi is None:
            nxt = low if low > 2 * lo else 2 * low - lo
            if nxt <= lo:
                nxt = lo + 1
            if nxt >= top:
                if lo >= top - 1:
                    break
                nxt = top - 1
        else:
            up = lo + _ceil_div((hi - lo) * (target - q_lo), q_hi - q_lo)
            nxt = up if not last_hi else (low if q_lo > 0 else 2 * up - hi)
            if nxt <= lo or nxt >= hi:
                nxt = lo + (hi - lo) // 2
        x = nxt
    if hi is None:
        raise ValidationError("above cap" if lo + 1 >= top else "quote",
                              f"Could not size {token_in} to repay {target} of {token_out} from the route's quotes "
                              f"(the route takes at most {max_in} of it now).{wait}")
    amount = min(max_with_slippage(hi, sizing["buffer_bps"]), max_in)
    gross_hi = _ceil_div(q_hi * _PPM, _PPM - fee_ppm)
    out_cap = amount if gross_hi == 0 else hi * max_out // gross_hi
    if amount > out_cap:
        amount = max(out_cap, hi)
    return amount


_PPM = 1_000_000


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


CAP_WAIT_HINT = " Other swaps used part of the route's cap in this window: wait about 12 seconds and try again."


def gross_of_hop(out: int, fee_ppm: int) -> int:
    """What the exchange pays before the hop fee for a quote ``out`` net of it (``quoteHop``): the figure the route's
    ``max_out`` cap is checked against. Rounded down, so a hop exactly at the cap is not refused."""
    return out * _PPM // (_PPM - fee_ppm) if fee_ppm > 0 else out


def _above_cap(c: Dict[str, Any], amount_in: int, token_in: str, token_out: str) -> ValidationError:
    """The 'above cap' refusal of ``amount_in`` of ``token_in`` through the outside route to ``token_out``, from its cap
    check (``hop_executor.check_caps``)."""
    if c["busy"]:
        return ValidationError(
            "above cap",
            f"{amount_in} of {token_in} is above what the outside route has left now ({c['caps_left']['in_left']} of "
            f"{token_in} in, {c['caps_left']['out_left']} of {token_out} out before the hop fee).{CAP_WAIT_HINT}")
    return ValidationError(
        "above cap",
        f"{amount_in} of {token_in} is above the outside route's caps ({c['caps']['max_in']} of {token_in} in, "
        f"{c['caps']['max_out']} of {token_out} out before the hop fee): send less.")


def quote_hop_within_caps(client, exec_: str, token_in: str, token_out: str, amount_in: int,
                          quote: Callable[[int], int]) -> int:
    """``quote(amount_in)`` for an amount the caller chose (not a sizing) through ``exec_``'s outside route ``token_in``
    -> ``token_out``, checked first the way a swap sent now is (``hop_executor.check_caps``: what other swaps left of the
    caps in the current window on Robinhood Chain, else the full caps): ``amount_in`` against the input cap, then the
    payout before the hop fee against the output cap. Raises 'above cap', saying to wait about 12 seconds when the
    amount fits the full caps but not what is left now. ``quote`` is ``quoteHop`` on ``exec_`` (net of the hop fee)."""
    hop = client.hop_executor
    pre = hop.check_caps(token_in, token_out, amount_in, executor=exec_)
    if pre["blocked"]:
        raise _above_cap(pre, amount_in, token_in, token_out)
    try:
        out = quote(amount_in)
        fee_ppm = client.mains._contract(exec_, "HopExecutor").functions.feePpm().call()
    except Exception as e:  # noqa: BLE001
        if is_revert_named(e, "above cap"):
            raise _above_cap({**pre, "busy": False}, amount_in, token_in, token_out) from e
        raise
    post = hop.check_caps(token_in, token_out, amount_in, gross_of_hop(out, fee_ppm), executor=exec_)
    if post["blocked"]:
        raise _above_cap(post, amount_in, token_in, token_out)
    return out


def check_deadline(deadline: int, now: int) -> int:
    """An explicit deadline (unix seconds) as sent, or ``ValidationError("Deadline")`` when it has passed: before the
    latest block time ``now``. A deadline equal to ``now`` can still be met, but only just: give some room."""
    if deadline < now:
        raise ValidationError("Deadline", f"deadline {deadline} has passed (chain time {now}).")
    return deadline


def check_explicit_min(explicit: Optional[int], quoted: int, field: str, what: str) -> None:
    """Refuses an explicit minimum (``explicit``, raw units; None = not given) above ``quoted``, the SDK's exact quote of
    what the write produces now (``what`` names it): the write would be refused ("Min out"), so it is refused before
    any approval. A minimum at or under the quote passes."""
    if explicit is not None and explicit > quoted:
        raise ValidationError("Min out", f"{field} {explicit} is above {what} now ({quoted}), so it would be refused: "
                                         "lower it, or omit it to derive it from the quote.")
