"""Revert message table. Data only. Generated file. Do not edit.
Mirrors src/core/errorMap.ts."""

SDK_ABI_NAMES = ["AggregatorV3","ERC20","EcosystemToken","Factory","FactoryToken","HopExecutor","Leverage","Liquidator","LoanHub","MainLens","MainToken","MarketResolver","MultiUpDown","PaperAsset","RedemptionReader","Referrals","Swap","Taxes","TrancheMarket","TrancheMarketPrivate","TrancheReader","Vault","Vesting"]

ERROR_MAP = {
  "Factory": {
    "createTokenFor": {
      "CEO required": "This token can't be created right now: the factory is not set up for it.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "invalid multiplier": "The multiplier must be between 1 and 100.",
      "auto-vest duration 1-990 days": "Auto-vesting must last between 1 and 990 days.",
      "frozen needs presale": "An invite-only market or token needs a presale size above zero.",
      "invalid starting LP": "Starting liquidity must be between 1,000 and 100,000.",
      "invalid supply multiplier": "The supply multiplier is outside what this main token allows.",
      "Wrong fee": "The creation fee sent does not match. Refresh the fee (feeAmount, or 0 for a fee-whitelisted wallet) and try again.",
      "Fee transfer failed": "The creation fee could not be forwarded. Try again later."
    },
    "createReversedTokenFor": {
      "CEO required": "This token can't be created right now: the factory is not set up for it.",
      "presale above half the launch supply": "On a reversed token (Meme+, Yolo+, Yolo) the presale can be at most half of the launch supply. Lower the presale.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "invalid multiplier": "The multiplier must be between 1 and 100.",
      "auto-vest duration 1-990 days": "Auto-vesting must last between 1 and 990 days.",
      "frozen needs presale": "An invite-only market or token needs a presale size above zero.",
      "invalid starting LP": "Starting liquidity must be between 1,000 and 100,000.",
      "invalid supply multiplier": "The supply multiplier is outside what this main token allows.",
      "Wrong fee": "The creation fee sent does not match. Refresh the fee (feeAmount, or 0 for a fee-whitelisted wallet) and try again.",
      "Fee transfer failed": "The creation fee could not be forwarded. Try again later."
    },
    "*": {
      "InvalidPair": "This pair is invalid or already registered.",
      "MainNotRegistered": "Tokens can't be created on this main token right now.",
      "WrongPairOrder": "The main token reports its pair tokens in the wrong order."
    }
  },
  "FactoryToken": {
    "disableFreeze": {
      "Only DEV": "Only the token's creator can do this."
    },
    "setWhitelistedWallet": {
      "Only DEV": "Only the token's creator can do this.",
      "no wallets provided": "Add at least one wallet to the whitelist."
    },
    "removeWhitelist": {
      "Only DEV": "Only the token's creator can do this.",
      "Wallet is not whitelisted": "That wallet is not on the whitelist."
    },
    "addToRewards": {
      "cannot be zero": "Enter an amount above zero.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again."
    },
    "claimRewards": {
      "mainPair transfer failed": "Rewards could not be paid out. Try again later."
    },
    "burn": {
      "min burn": "That burn is too small. The minimum is one launch unit of tokens, about one dollar's worth."
    }
  },
  "HopExecutor": {
    "quoteHop": {
      "no route": "There is no outside-DEX route between these tokens yet.",
      "above cap": "That swap is larger than this route allows right now (a cap per 12-second window). Try a smaller amount or wait about 12 seconds.",
      "no quote": "The outside pool could not quote this swap."
    },
    "hop": {
      "Only swap": "Only the BASIS swap can run a hop."
    },
    "*": {
      "QuoteResult": "Internal quote signal from the hop executor, not a failure."
    }
  },
  "LoanHub": {
    "hubPartialLoanSell": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "main transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "invalid multiplier": "This token's multiplier is out of range.",
      "Insufficient main token reserve": "That amount is too large to fill in one go. Try a smaller amount.",
      "Invalid token amount": "That amount rounds to zero. Try a larger amount.",
      "Stable transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Invalid loan": "No loan with this ID in your wallet.",
      "No swap for ecosystem": "Selling part of a loan is not available for this main token right now.",
      "No mainPair received from swap": "The sale returned nothing. Try a larger percentage.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "Loan not active": "This loan is already closed (repaid, sold or liquidated).",
      "Invalid percentage": "Pick a percentage between 1 and 100.",
      "cannot sell off with no profit": "Selling this part would not cover its share of the debt. Wait for the price to rise, or repay instead.",
      "payout failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Approve": "Swap approval failed.",
      "Bad tax token": "Fee routing failed.",
      "% by 10": "Sell in steps of 10% (10, 20 ... 100).",
      "% range": "Pick between 10% and 100%.",
      "Min out not met": "The price moved past your slippage limit. Nothing was sold. Try again or allow more slippage.",
      "ERC20: burn amount exceeds balance": "Not enough tokens to burn for this sale."
    },
    "repayLoanWith": {
      "hook not allowed": "That V4 pool's hook is not on the allow list.",
      "partial fill": "The outside pool could not fill the whole amount (not enough liquidity in range). Try a smaller amount.",
      "bad delta": "The outside pool returned an invalid result.",
      "Stable transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Invalid loan": "No loan with this ID in your wallet.",
      "Core loan inactive": "This loan is already closed (repaid, sold or liquidated).",
      "Stable approve failed": "Loan hub approval failed.",
      "Not all collateral returned": "The repayment did not return the collateral it should (its share on a partial repay). Nothing was changed.",
      "Collateral return failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Loan not active": "This loan is already closed (repaid, sold or liquidated).",
      "Zero amount": "Enter an amount.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Transfer": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ReentrancyGuard: reentrant call": "The contract is busy with another step of this transaction. Try again.",
      "Deadline": "The time limit ran out before this went out. Nothing was sent. Try again.",
      "Repay in full": "Repay the whole loan: what's left would be under the minimum loan.",
      "Not enough to close": "Not enough to repay all. Add a bit more and try again.",
      "no route": "No route possible: this token can't be used here.",
      "above cap": "That payment is larger than the outside route allows right now (a cap per 12-second window). Pay with a smaller amount or wait about 12 seconds.",
      "zero hop": "The outside pool returned nothing for this amount. Pay with a larger amount.",
      "no executor": "No route possible: this token can't be used here.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Transfer in": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Min out": "The token would convert to less than your minimum (the outside price moved). Nothing was repaid. Try again.",
      "Approve failed": "Loan hub approval failed."
    },
    "takeLoan": {
      "Ecosystem inactive": "Loans are not available for this main token right now.",
      "Collateral transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Collateral approve failed": "Loan hub approval failed.",
      "No stable received": "The loan paid out nothing. Use more collateral.",
      "Stable transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "incorrect loan duration": "Loan length must be between the minimum and maximum days (10 to 1,000 by default; read minDaysLoan/maxDaysLoan).",
      "Minimum loan amount not met": "That collateral is worth less than the minimum loan. Add more collateral.",
      "Tax approve failed": "Fee routing failed.",
      "cannot be zero": "Enter an amount above zero.",
      "Only ecosystem tokens": "Only BASIS tokens can be used as collateral.",
      "wrong pair": "This token trades against a different main token. Borrow on that main.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Open loan: increase it": "You already have an open loan on this main with this collateral. Add to it with increaseLoan instead of taking a second loan."
    },
    "increaseLoan": {
      "Collateral transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Collateral approve failed": "Loan hub approval failed.",
      "Stable transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Invalid loan": "No loan with this ID in your wallet.",
      "No borrowed funds received": "Adding this collateral would not let you borrow anything more.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Loan not active": "This loan is already closed (repaid, sold or liquidated).",
      "Duration too short": "Extend your loan first.",
      "Zero amount": "Enter an amount.",
      "No borrowing power": "The added collateral does not raise what you can borrow.",
      "Fees exceed value": "The fees on this top-up would be larger than what it lets you borrow. Add more collateral.",
      "Tax approve failed": "Fee routing failed.",
      "cannot be zero": "Enter an amount above zero.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens."
    },
    "repayLoan": {
      "Stable transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Invalid loan": "No loan with this ID in your wallet.",
      "Core loan inactive": "This loan is already closed (repaid, sold or liquidated).",
      "Stable approve failed": "Loan hub approval failed.",
      "Not all collateral returned": "The repayment did not return the collateral it should (its share on a partial repay). Nothing was changed.",
      "Collateral return failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Zero amount": "Enter an amount.",
      "Repay in full": "Repay the whole loan: what's left would be under the minimum loan."
    },
    "extendLoan": {
      "Invalid loan": "No loan with this ID in your wallet.",
      "Pay in stable or refinance, not both": "Choose one: pay the extension fee from your wallet, or refinance. Not both.",
      "Fee transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Fee approve failed": "Loan hub approval failed.",
      "incorrect loan duration": "Loan length must be between the minimum and maximum days (10 to 1,000 by default; read minDaysLoan/maxDaysLoan).",
      "Loan not active": "This loan is already closed (repaid, sold or liquidated).",
      "Tax approve failed": "Fee routing failed.",
      "Must be refinance if not extending time": "Adding 0 days only works as a refinance. Pick days, or turn on refinance.",
      "mainPair fee transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "No equity": "Refinancing needs the collateral to be worth more than the loan. It is not right now.",
      "Minimum surplus not met": "The collateral has not gained enough value to refinance.",
      "Time remaining too short": "A refinance without adding days needs at least the minimum loan term left on the loan. Add days too.",
      "Fees exceed surplus value": "The refinance fees are larger than the value it would free up.",
      "Not enough collateral to cover fees": "Your collateral cannot cover the extension fee. Pay the fee from your wallet instead.",
      "cannot be zero": "Enter an amount above zero.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again."
    },
    "claimLiquidation": {
      "Nothing to claim": "There is nothing to claim on this position: it was already claimed, or the 365-day claim window after the liquidation has passed.",
      "Claim transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Loan is still active": "This loan is still open. There is nothing to claim until it is liquidated."
    },
    "getUserLoanDetails": {
      "Loan does not exist": "No hub loan with this ID for that wallet. Hub ids start at 1."
    }
  },
  "MainLens": {
    "getPartialLoanSellAmounts": {
      "Invalid percentage": "Pick a percentage between 1 and 100.",
      "cannot sell off with no profit": "Selling this part would not cover its share of the debt. Wait for the price to rise, or repay instead.",
      "Already liquidated": "This position was liquidated. There is nothing left to sell; claim the residual instead."
    },
    "calculateMainPairForTokens": {
      "Insufficient token reserve": "That amount is too large for this token right now. Try a smaller amount.",
      "Invalid mainPair amount": "That amount rounds to zero."
    },
    "calculateTokensForMainPair": {
      "Insufficient mainPair reserve": "That amount is too large to fill in one go. Try a smaller amount.",
      "Invalid token amount": "That amount rounds to zero."
    }
  },
  "MainToken": {
    "repayLoan": {
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Unauthorized caller": "Open and manage loans through the loan hub, and leverage positions from your own wallet.",
      "Loan not active": "This loan is already closed (repaid, sold or liquidated).",
      "Zero amount": "Enter an amount.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Repay in full": "Repay the whole loan: what's left would be under the minimum loan."
    },
    "extendLoan": {
      "incorrect loan duration": "Loan length must be between the minimum and maximum days (10 to 1,000 by default; read minDaysLoan/maxDaysLoan).",
      "Unauthorized caller": "Open and manage loans through the loan hub, and leverage positions from your own wallet.",
      "Loan not active": "This loan is already closed (repaid, sold or liquidated).",
      "Tax approve failed": "Fee routing failed.",
      "Must be refinance if not extending time": "Adding 0 days only works as a refinance. Pick days, or turn on refinance.",
      "mainPair fee transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "No equity": "Refinancing needs the collateral to be worth more than the loan. It is not right now.",
      "Minimum surplus not met": "The collateral has not gained enough value to refinance.",
      "Time remaining too short": "A refinance without adding days needs at least the minimum loan term left on the loan. Add days too.",
      "Fees exceed surplus value": "The refinance fees are larger than the value it would free up.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Not enough collateral to cover fees": "Your collateral cannot cover the extension fee. Pay the fee from your wallet instead.",
      "cannot be zero": "Enter an amount above zero.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again."
    },
    "claimLiquidation": {
      "Unauthorized caller": "Open and manage loans through the loan hub, and leverage positions from your own wallet.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Loan is still active": "This loan is still open. There is nothing to claim until it is liquidated.",
      "Nothing to claim": "There is nothing to claim on this position: it was already claimed, or the 365-day claim window after the liquidation has passed."
    },
    "getColleteralValue": {
      "cannot be zero": "Enter an amount above zero.",
      "Only ecosystem tokens": "Only BASIS tokens can be used as collateral.",
      "wrong pair": "This token trades against a different main token. Borrow on that main."
    }
  },
  "MarketResolver": {
    "proposeRanked": {
      "Bad ranking": "That ranking is not valid for this market (check the places, repeats and ties).",
      "Not a ranked market": "This market takes a single answer, not a ranking.",
      "Too early to propose": "It's too early to propose a result. The creator can propose at close; everyone else 15 minutes after.",
      "Already proposed": "A result has already been proposed. You can dispute it instead.",
      "Rules: not qualified": "This wallet does not meet the proposal rules for this market, so it can't propose. It can still dispute or veto."
    },
    "disputeRanked": {
      "Bad ranking": "That ranking is not valid for this market (check the places, repeats and ties).",
      "Not a ranked market": "This market takes a single answer, not a ranking.",
      "No proposal to dispute": "No result has been proposed yet. Propose one instead.",
      "Already in dispute": "This result is already disputed and in voting.",
      "Dispute period over": "The window to dispute this result has closed.",
      "Must be different": "A dispute must propose a different result from the one on the table.",
      "Proposer cannot dispute self": "You proposed this result, so you can't dispute it."
    },
    "voteRanked": {
      "Bad ranking": "That ranking is not valid for this market (check the places, repeats and ties).",
      "Not a ranked market": "This market takes a single answer, not a ranking.",
      "Not voting": "This market is not in a vote.",
      "cannot vote during veto": "This ruling was vetoed, so voting is closed. After a veto, the BASIS team makes the final ruling.",
      "Disputed period ended": "The voting period has ended.",
      "Not an authorized voter": "Your wallet is not on the resolution voter list.",
      "Voted": "You already voted in this round."
    },
    "vetoRanked": {
      "Bad ranking": "That ranking is not valid for this market (check the places, repeats and ties).",
      "Not a ranked market": "This market takes a single answer, not a ranking.",
      "Invalid market": "Unknown market.",
      "Already resolved": "This market is already resolved.",
      "Veto only allowed on disputed/voting markets": "A veto is only possible after a dispute.",
      "Veto only allowed once": "This market has already been vetoed.",
      "Already vetoed": "This market has already been vetoed.",
      "Cant veto propose dispute outcome": "A veto must name a result other than the disputed one.",
      "Veto window not started yet": "The veto window opens when voting ends.",
      "Party to the dispute cannot veto": "The proposer and the disputer can't veto.",
      "Cannot veto back to the proposal": "A veto can't restore the original proposal."
    },
    "dispute": {
      "Ranked market: use the *Ranked entrypoints": "This market is resolved with a ranking. Pick the finishing order, not a single answer.",
      "No uint8 host": "Single-answer resolution is not available on this resolver.",
      "Bad outcome": "That answer is not an option in this market.",
      "No proposal to dispute": "No result has been proposed yet. Propose one instead.",
      "Already in dispute": "This result is already disputed and in voting.",
      "Dispute period over": "The window to dispute this result has closed.",
      "Must be different": "A dispute must propose a different result from the one on the table.",
      "Proposer cannot dispute self": "You proposed this result, so you can't dispute it."
    },
    "veto": {
      "Ranked market: use the *Ranked entrypoints": "This market is resolved with a ranking. Pick the finishing order, not a single answer.",
      "No uint8 host": "Single-answer resolution is not available on this resolver.",
      "Bad outcome": "That answer is not an option in this market.",
      "Invalid market": "Unknown market.",
      "Already resolved": "This market is already resolved.",
      "Veto only allowed on disputed/voting markets": "A veto is only possible after a dispute.",
      "Veto only allowed once": "This market has already been vetoed.",
      "Already vetoed": "This market has already been vetoed.",
      "Cant veto propose dispute outcome": "A veto must name a result other than the disputed one.",
      "Veto window not started yet": "The veto window opens when voting ends.",
      "Party to the dispute cannot veto": "The proposer and the disputer can't veto.",
      "Cannot veto back to the proposal": "A veto can't restore the original proposal."
    },
    "disputeRankedEarly": {
      "Not a ranked market": "This market takes a single answer, not a ranking.",
      "No proposal to dispute": "No result has been proposed yet. Propose one instead.",
      "Already in dispute": "This result is already disputed and in voting.",
      "Dispute period over": "The window to dispute this result has closed.",
      "Proposer cannot dispute self": "You proposed this result, so you can't dispute it."
    },
    "voteRankedEarly": {
      "Not a ranked market": "This market takes a single answer, not a ranking.",
      "Not voting": "This market is not in a vote.",
      "cannot vote during veto": "This ruling was vetoed, so voting is closed. After a veto, the BASIS team makes the final ruling.",
      "Disputed period ended": "The voting period has ended.",
      "Not an authorized voter": "Your wallet is not on the resolution voter list.",
      "Voted": "You already voted in this round."
    },
    "finalizeUncontested": {
      "Wait more": "It's too early to finalize. Wait for the current window to end."
    },
    "finalizeMarket": {
      "Wait more": "It's too early to finalize. Wait for the current window to end."
    }
  },
  "MultiUpDown": {
    "betBull": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "Overcapped on WL buying": "That buy is over your whitelist limit for this token. Buy a smaller amount.",
      "token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Approve": "Swap approval failed.",
      "Sell: need market token": "A redeem must send the market's own token.",
      "Buy: need asset/MAIN": "This token can't pay for this bet here: it needs an outside route to the market's pair asset, and no route executor is set up on this chain. Pay with the pair asset, the market's main or a BASIS token that shares the pair asset.",
      "Bad tax token": "Fee routing failed.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "SafeERC20: ERC20 operation did not succeed": "A token transfer failed. Check your balance and approval.",
      "Address: call to non-contract": "That token address is not a contract on this chain.",
      "SafeERC20: low-level call failed": "The token transfer failed without giving a reason. Check your balance and approval, then try again.",
      "no route": "This token can't pay for this bet: no route from it to the market's pair asset is set up. Pay with the pair asset, the market's main or another token.",
      "above cap": "That bet is larger than the outside route allows right now (a cap per 12-second window). Bet a smaller amount or wait about 12 seconds.",
      "zero hop": "The outside pool returned nothing for this bet amount. Bet a larger amount.",
      "Zero bought": "That bet is too small to buy any of the market's main token. Bet a larger amount.",
      "Zero main": "That token amount is too small to sell for anything, so it can't pay for a bet. Bet a larger amount.",
      "Zero sold": "That token amount is too small to sell for anything, so it can't pay for a bet. Bet a larger amount.",
      "hosts only": "This market can't take bets in this token. Pay with the market's pair asset, its main or the market token.",
      "Deadline": "The time limit ran out before this went out. Nothing was sent. Try again."
    },
    "betBear": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "Overcapped on WL buying": "That buy is over your whitelist limit for this token. Buy a smaller amount.",
      "token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Approve": "Swap approval failed.",
      "Sell: need market token": "A redeem must send the market's own token.",
      "Buy: need asset/MAIN": "This token can't pay for this bet here: it needs an outside route to the market's pair asset, and no route executor is set up on this chain. Pay with the pair asset, the market's main or a BASIS token that shares the pair asset.",
      "Bad tax token": "Fee routing failed.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "SafeERC20: ERC20 operation did not succeed": "A token transfer failed. Check your balance and approval.",
      "Address: call to non-contract": "That token address is not a contract on this chain.",
      "SafeERC20: low-level call failed": "The token transfer failed without giving a reason. Check your balance and approval, then try again.",
      "no route": "This token can't pay for this bet: no route from it to the market's pair asset is set up. Pay with the pair asset, the market's main or another token.",
      "above cap": "That bet is larger than the outside route allows right now (a cap per 12-second window). Bet a smaller amount or wait about 12 seconds.",
      "zero hop": "The outside pool returned nothing for this bet amount. Bet a larger amount.",
      "Zero bought": "That bet is too small to buy any of the market's main token. Bet a larger amount.",
      "Zero main": "That token amount is too small to sell for anything, so it can't pay for a bet. Bet a larger amount.",
      "Zero sold": "That token amount is too small to sell for anything, so it can't pay for a bet. Bet a larger amount.",
      "hosts only": "This market can't take bets in this token. Pay with the market's pair asset, its main or the market token.",
      "Deadline": "The time limit ran out before this went out. Nothing was sent. Try again."
    },
    "claim": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "main transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Approve": "Swap approval failed.",
      "Sell: need market token": "A redeem must send the market's own token.",
      "Bad tax token": "Fee routing failed.",
      "fraction 1-10000": "Pick a share of your payout between 0.01% and 100%.",
      "slices 1-25": "Pick between 1 and 25 slices.",
      "Below minOut": "The payout came in under your minimum. Try again, use more slices, or lower the minimum.",
      "SafeERC20: ERC20 operation did not succeed": "A token transfer failed. Check your balance and approval.",
      "SafeERC20: low-level call failed": "The token transfer failed without giving a reason. Check your balance and approval, then try again.",
      "hosts only": "This market can't take bets in this token. Pay with the market's pair asset, its main or the market token."
    },
    "*": {
      "AlreadyBetOnOtherSide": "You already bet on the other side this round. One side per round.",
      "AlreadyStarted": "Every timeframe of this asset is already running.",
      "BettingClosed": "Betting is closed for this round (it locks 15 seconds before the end). Bet on the next round.",
      "InvalidAmount": "That bet is too small, or it isn't in a currency this asset accepts (its pool token, settle asset or main).",
      "InvalidAsset": "Unknown asset.",
      "InvalidPrice": "The price feed returned an invalid price, so the next round can't open. Try again shortly.",
      "InvalidTimeframe": "Unknown timeframe.",
      "MinSharesNotMet": "The odds moved and you'd get fewer shares than your minimum. Try again.",
      "NoActiveRound": "This timeframe has no round yet (or that round doesn't exist).",
      "NoUpdateAfterEndTime": "The price feed hasn't updated since the round ended. Try settling again in a minute.",
      "NoValidPriceInWindow": "No usable closing price was found for this round. If it stays this way, cancel the round after the settle window.",
      "NothingToClaim": "Nothing to claim for this round (no bet, a losing bet, or already claimed).",
      "Paused": "Betting is paused on this market right now.",
      "PriceDriftTooLarge": "The closest price update is too far from the round's end time. Try again shortly, or cancel the round after the settle window.",
      "RoundAlreadySettled": "This round is already settled. Refresh.",
      "RoundNotSettled": "This round isn't settled yet. Claim opens after settlement.",
      "StalePrice": "The price feed is stale (no update in the last hour), so the next round can't open.",
      "TooEarlyToSettle": "Too early. Settle opens when the round ends; cancel opens 20 minutes after it ends.",
      "TooLateForValidPrice": "The 20-minute settle window has passed. Cancel the round instead (everyone is refunded).",
      "ZeroAddress": "An address is missing.",
      "ZeroShares": "That bet is too small to buy any shares. Bet more."
    }
  },
  "PaperAsset": {
    "claim": {
      "already claimed": "This wallet already claimed this paper token: one claim per wallet."
    }
  },
  "RedemptionReader": {
    "simulateSlicedSell": {
      "slices 1-25": "Pick between 1 and 25 slices."
    },
    "*": {
      "PRBMath_MulDiv18_Overflow": "The token's floor price can't be read right now, so this loan or leverage action can't go through. Try a different amount, or try again later.",
      "PRBMath_MulDiv_Overflow": "The token's floor price can't be read right now, so this loan or leverage action can't go through. Try a different amount, or try again later.",
      "PRBMath_UD60x18_Exp2_InputTooBig": "The token's floor price can't be read right now, so this loan or leverage action can't go through. Try a different amount, or try again later.",
      "PRBMath_UD60x18_Log_InputTooSmall": "The token's floor price can't be read right now, so this loan or leverage action can't go through. Try a different amount, or try again later."
    }
  },
  "Referrals": {
    "setReferrer": {
      "closed": "New referrals are not accepted right now.",
      "referrer set": "Your referrer is already set. Once set, you can't change it yourself.",
      "invalid referrer": "You can't be your own referrer.",
      "referral loop": "That wallet was referred by you, so it can't be your referrer."
    }
  },
  "Swap": {
    "buyTokens": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "Overcapped on WL buying": "That buy is over your whitelist limit for this token. Buy a smaller amount.",
      "token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Below min stake": "That is below the vault's minimum stake (read minStake; 1 token by default).",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Transfer": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Transfer in": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Bad path": "That trade route is not supported. Refresh and try again.",
      "Alien output": "You can only buy BASIS tokens here.",
      "Not main": "This token's quote token is not a registered main.",
      "Bad pair": "You can't pay with that currency for this token. Pick its main token or pair asset.",
      "Approve": "Swap approval failed.",
      "Zero bought": "That amount is too small to buy any tokens. Enter a larger amount.",
      "Min out": "The price moved past your slippage limit. Nothing was traded. Try again or allow more slippage.",
      "Alien token": "You can't pay with that token here.",
      "Not ecosystem": "That token is not a BASIS factory token.",
      "Wrong main": "This token trades against a different main token. Refresh the route.",
      "Not pair asset": "That currency is not this main token's pair asset.",
      "Bad tax token": "Fee routing failed.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens."
    },
    "mixedBuy": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "Overcapped on WL buying": "That buy is over your whitelist limit for this token. Buy a smaller amount.",
      "token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "incorrect loan duration": "Loan length must be between the minimum and maximum days (10 to 1,000 by default; read minDaysLoan/maxDaysLoan).",
      "Tax approve failed": "Fee routing failed.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "wrong path": "Leverage must be paid in the main token's pair asset. Pick that currency.",
      "Transfer": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Range": "The leverage percentage is out of range: mixedBuy takes 1 to 99 (100 = a plain leverageBuy); the simulator takes 1 to 100.",
      "Alien output": "You can only buy BASIS tokens here.",
      "Not main": "This token's quote token is not a registered main.",
      "Bad pair": "You can't pay with that currency for this token. Pick its main token or pair asset.",
      "Approve": "Swap approval failed.",
      "Zero bought": "That amount is too small to buy any tokens. Enter a larger amount.",
      "Min out": "The price moved past your slippage limit. Nothing was traded. Try again or allow more slippage.",
      "Bad tax token": "Fee routing failed.",
      "In presale": "This token auto-vests during its presale, so leverage opens only after the presale.",
      "WL cap": "This leveraged buy is over your whitelist limit. Use a smaller amount.",
      "Under 2x": "This leverage is too low. Raise it.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "Extend first": "Your open leverage position on this token has less than the minimum loan days left (minDaysLoan). Extend it before adding to it."
    },
    "swapCross": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "Overcapped on WL buying": "That buy is over your whitelist limit for this token. Buy a smaller amount.",
      "token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "main transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "hook not allowed": "That V4 pool's hook is not on the allow list.",
      "no route": "There is no outside-DEX route between these tokens yet.",
      "above cap": "That swap is larger than this route allows right now (a cap per 12-second window). Try a smaller amount or wait about 12 seconds.",
      "zero hop": "The outside pool returned nothing for this amount. Try a larger amount.",
      "partial fill": "The outside pool could not fill the whole amount (not enough liquidity in range). Try a smaller amount.",
      "bad delta": "The outside pool returned an invalid result.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Deadline": "The time limit ran out before this went out. Nothing was sent. Try again.",
      "no executor": "Cross-pair swaps are not set up on this chain yet.",
      "native in": "To pay in the chain's native coin, the route must start with its wrapped token and the coin sent must equal the amount.",
      "Transfer in": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Approve": "Swap approval failed.",
      "Bad tax token": "Fee routing failed.",
      "Transfer": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Min out": "The price moved past your slippage limit. Nothing was traded. Try again or allow more slippage.",
      "Bad path": "A route needs at least two tokens.",
      "same token": "You can't swap a token for itself.",
      "Bad route order": "That route is not supported. Refresh the route.",
      "no BASIS leg": "A cross-pair swap must include a BASIS token.",
      "round trip": "That route sells and buys the same main token. Use a normal trade.",
      "Zero main": "That amount is too small to route. Enter a larger amount.",
      "Zero sold": "That amount is too small to route. Enter a larger amount.",
      "Zero bought": "That amount is too small to buy any tokens. Enter a larger amount.",
      "autovest presale": "This token auto-vests during its presale; buy it directly, not through a cross-pair swap.",
      "no recipient": "A recipient address is required.",
      "not native": "To receive the chain's native coin, the route must end in its wrapped token.",
      "ETH fail": "The native-coin payout could not be sent to the recipient.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens."
    },
    "sellTokens": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "main transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Transfer": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Bad path": "That trade route is not supported. Refresh and try again.",
      "Bad pair": "You can't pay with that currency for this token. Pick its main token or pair asset.",
      "Approve": "Swap approval failed.",
      "Min out": "The price moved past your slippage limit. Nothing was traded. Try again or allow more slippage.",
      "use swapCross": "Selling straight to the chain's native coin goes through the cross-pair swap.",
      "Wrong token": "You can only sell BASIS tokens here.",
      "Not ecosystem": "That token is not a BASIS factory token.",
      "Not MAIN": "The middle of the route must be a registered main token.",
      "Wrong main": "This token trades against a different main token. Refresh the route.",
      "Not pair asset": "That currency is not this main token's pair asset.",
      "Zero main": "That sale is too small to return anything. Sell more.",
      "Zero sold": "That sale is too small to return anything. Sell more.",
      "Bad tax token": "Fee routing failed.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ERC20: burn amount exceeds balance": "Not enough tokens to burn for this sale."
    },
    "partialLoanSellFor": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "main transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "invalid multiplier": "This token's multiplier is out of range.",
      "Insufficient main token reserve": "That amount is too large to fill in one go. Try a smaller amount.",
      "Invalid token amount": "That amount rounds to zero. Try a larger amount.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "Loan not active": "This loan is already closed (repaid, sold or liquidated).",
      "Invalid percentage": "Pick a percentage between 1 and 100.",
      "cannot sell off with no profit": "Selling this part would not cover its share of the debt. Wait for the price to rise, or repay instead.",
      "payout failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Approve": "Swap approval failed.",
      "Bad tax token": "Fee routing failed.",
      "% by 10": "Sell in steps of 10% (10, 20 ... 100).",
      "% range": "Pick between 10% and 100%.",
      "Not user": "This position is not in your wallet. Hub loans are sold through the loan hub.",
      "Min out not met": "The price moved past your slippage limit. Nothing was sold. Try again or allow more slippage.",
      "Transfer": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ERC20: burn amount exceeds balance": "Not enough tokens to burn for this sale."
    },
    "quoteCross": {
      "hook not allowed": "That V4 pool's hook is not on the allow list.",
      "no route": "There is no outside-DEX route between these tokens yet.",
      "above cap": "That swap is larger than this route allows right now (a cap per 12-second window). Try a smaller amount or wait about 12 seconds.",
      "no quote": "The outside pool could not quote this swap.",
      "partial fill": "The outside pool could not fill the whole amount (not enough liquidity in range). Try a smaller amount.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "Bad path": "A route needs at least two tokens.",
      "same token": "You can't swap a token for itself.",
      "Bad route order": "That route is not supported. Refresh the route.",
      "no BASIS leg": "A cross-pair swap must include a BASIS token.",
      "round trip": "That route sells and buys the same main token. Use a normal trade.",
      "Zero main": "That amount is too small to route. Enter a larger amount.",
      "Zero sold": "That amount is too small to route. Enter a larger amount.",
      "no executor": "Cross-pair swaps are not set up on this chain yet.",
      "zero hop": "The outside pool returned nothing for this amount. Try a larger amount.",
      "Zero bought": "That amount is too small to buy any tokens. Enter a larger amount.",
      "autovest presale": "This token auto-vests during its presale; buy it directly, not through a cross-pair swap.",
      "Overcapped on WL buying": "That buy is over your whitelist limit for this token.",
      "Trading is not enabled": "Trading on this pair has not opened yet."
    },
    "repayWith": {
      "hook not allowed": "That V4 pool's hook is not on the allow list.",
      "partial fill": "The outside pool could not fill the whole amount (not enough liquidity in range). Try a smaller amount.",
      "bad delta": "The outside pool returned an invalid result.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Loan not active": "This loan is already closed (repaid, sold or liquidated).",
      "Zero amount": "Enter an amount.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "Deadline": "The time limit ran out before this went out. Nothing was sent. Try again.",
      "Approve": "Swap approval failed.",
      "Transfer": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Repay in full": "Repay the whole loan: what's left would be under the minimum loan.",
      "Not enough to close": "Not enough to repay all. Add a bit more and try again.",
      "no route": "No route possible: this token can't be used here.",
      "above cap": "That payment is larger than the outside route allows right now (a cap per 12-second window). Pay with a smaller amount or wait about 12 seconds.",
      "zero hop": "The outside pool returned nothing for this amount. Pay with a larger amount.",
      "no executor": "No route possible: this token can't be used here.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Transfer in": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Min out": "The token would convert to less than your minimum (the outside price moved). Nothing was repaid. Try again."
    },
    "leverageBuy": {
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "incorrect loan duration": "Loan length must be between the minimum and maximum days (10 to 1,000 by default; read minDaysLoan/maxDaysLoan).",
      "Tax approve failed": "Fee routing failed.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "wrong path": "Leverage must be paid in the main token's pair asset. Pick that currency.",
      "Transfer": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Approve": "Swap approval failed.",
      "In presale": "This token auto-vests during its presale, so leverage opens only after the presale.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "WL cap": "This leveraged buy is over your whitelist limit. Use a smaller amount.",
      "Under 2x": "This leverage is too low. Raise the leverage or enter a larger amount.",
      "Min out": "The price moved past your slippage limit. Nothing was traded. Try again or allow more slippage.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Extend first": "Your open leverage position on this token has less than the minimum loan days left (minDaysLoan). Extend it before adding to it."
    },
    "getAmountsOut": {
      "Bad path": "That trade route is not supported. Refresh and try again."
    }
  },
  "Taxes": {
    "setCreatorFee": {
      "not on MAIN": "Main tokens have no creator or rewards fee.",
      "only dev": "Only the token's creator can change its fees.",
      "over max": "That fee is above the maximum for this token (creator fee max 1%; rewards fee max from maxRewardsFee).",
      "fee cooldown": "You changed this fee recently. Wait for the cooldown to end."
    },
    "setRewardsFee": {
      "not on MAIN": "Main tokens have no creator or rewards fee.",
      "only dev": "Only the token's creator can change its fees.",
      "over max": "That fee is above the maximum for this token (creator fee max 1%; rewards fee max from maxRewardsFee).",
      "fee cooldown": "You changed this fee recently. Wait for the cooldown to end.",
      "rewards off": "Holder rewards are switched off for this token, so it has no rewards fee."
    },
    "addDevShare": {
      "Cannot set for MAINTOKEN": "Main tokens have no creator fee shares.",
      "Only DEV can add shares": "Only the token's creator can add fee shares.",
      "Invalid wallet": "Enter a wallet address.",
      "Basis points must be >0": "A share must be above 0%.",
      "Exceeds 100%": "Fee shares can't add up to more than 100%.",
      "Max 10 shares": "A token can have at most 10 fee recipients."
    },
    "removeDevShare": {
      "Cannot set for MAINTOKEN": "Main tokens have no creator fee shares.",
      "Only DEV can remove shares": "Only the token's creator can remove fee shares."
    }
  },
  "TrancheMarket": {
    "buy": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "Overcapped on WL buying": "That buy is over your whitelist limit for this token. Buy a smaller amount.",
      "token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Approve": "Swap approval failed.",
      "Sell: need market token": "A redeem must send the market's own token.",
      "Buy: need asset/MAIN": "This token can't pay for this bet here: it needs an outside route to the market's pair asset, and no route executor is set up on this chain. Pay with the pair asset, the market's main or a BASIS token that shares the pair asset.",
      "Bad tax token": "Fee routing failed.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "Betting closed": "Betting on this market has closed.",
      "Outcome proposed": "A result has been proposed for this market: no bets while the proposal is live.",
      "no route": "This token can't pay for this bet: no route from it to the market's pair asset is set up. Pay with the pair asset, the market's main or another token.",
      "above cap": "That bet is larger than the outside route allows right now (a cap per 12-second window). Bet a smaller amount or wait about 12 seconds.",
      "zero hop": "The outside pool returned nothing for this bet amount. Bet a larger amount.",
      "Zero bought": "That bet is too small to buy any of the market's main token. Bet a larger amount.",
      "Zero main": "That token amount is too small to sell for anything, so it can't pay for a bet. Bet a larger amount.",
      "Zero sold": "That token amount is too small to sell for anything, so it can't pay for a bet. Bet a larger amount.",
      "hosts only": "This market can't take bets in this token. Pay with the market's pair asset, its main or the market token.",
      "Deadline": "The time limit ran out before this went out. Nothing was sent. Try again."
    },
    "buyOrdersAndContract": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "Overcapped on WL buying": "That buy is over your whitelist limit for this token. Buy a smaller amount.",
      "token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Approve": "Swap approval failed.",
      "Sell: need market token": "A redeem must send the market's own token.",
      "Bad tax token": "Fee routing failed.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "Betting closed": "Betting on this market has closed.",
      "Outcome proposed": "A result has been proposed for this market: no bets while the proposal is live.",
      "pair unit": "The order book can't settle in this market's currency.",
      "dust fill": "That fill is too small. Buy more.",
      "max cost": "The order's price moved above your limit. Try again.",
      "hosts only": "This market can't take bets in this token. Pay with the market's pair asset, its main or the market token."
    },
    "createMarket": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "Overcapped on WL buying": "That buy is over your whitelist limit for this token. Buy a smaller amount.",
      "token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "frozen needs presale": "An invite-only market or token needs a presale size above zero.",
      "Wrong fee": "The creation fee sent does not match. Refresh the fee (feeAmount, or 0 for a fee-whitelisted wallet) and try again.",
      "Fee transfer failed": "The creation fee could not be forwarded. Try again later.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "Approve": "Swap approval failed.",
      "Bad tax token": "Fee routing failed.",
      "Not an approved host": "Market creation is not available on this contract.",
      "Already registered": "This market is already registered with the resolver.",
      "Approval expired": "The market review approval expired before sending. Ask for a new review.",
      "Signer unset": "Not available right now. Not something you can fix here.",
      "Approval already used": "That approval was already used. Request a new review.",
      "Invalid approval": "The contract rejected the review approval (the name, symbol or answers changed, or it was signed for another contract). Request a fresh review.",
      "asset mismatch": "This main token is paired with a different asset than this market contract has registered for it, so a market can't be opened on it. Pick another main.",
      "hosts only": "This market can't take bets in this token. Pay with the market's pair asset, its main or the market token."
    },
    "redeem": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "main transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Approve": "Swap approval failed.",
      "Sell: need market token": "A redeem must send the market's own token.",
      "Bad tax token": "Fee routing failed.",
      "slices 1-25": "Pick between 1 and 25 slices.",
      "Below minOut": "The payout came in under your minimum. Try again, use more slices, or lower the minimum.",
      "fraction 1-10000": "Pick a share of your payout between 0.01% and 100%.",
      "Nothing to redeem": "You have nothing left to redeem on this market.",
      "hosts only": "This market can't take bets in this token. Pay with the market's pair asset, its main or the market token."
    },
    "setWhitelistedWallet": {
      "no wallets provided": "Add at least one wallet to the whitelist."
    },
    "removeWhitelist": {
      "Wallet is not whitelisted": "That wallet is not on the whitelist."
    },
    "listOrder": {
      "empty order": "List at least some shares."
    },
    "buyOrder": {
      "pair unit": "The order book can't settle in this market's currency.",
      "dust fill": "That fill is too small. Buy more.",
      "max cost": "The order's price moved above your limit. Try again."
    },
    "*": {
      "Invalid proposed": "Listed shares can't be bought while the market might be voided (INVALID is proposed or backed by a vote)."
    }
  },
  "TrancheMarketPrivate": {
    "buyOrdersAndContract": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "Overcapped on WL buying": "That buy is over your whitelist limit for this token. Buy a smaller amount.",
      "token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Approve": "Swap approval failed.",
      "Sell: need market token": "A redeem must send the market's own token.",
      "Bad tax token": "Fee routing failed.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "pair unit": "The order book can't settle in this market's currency.",
      "dust fill": "That fill is too small. Buy more.",
      "max cost": "The order's price moved above your limit. Try again.",
      "hosts only": "This market can't take bets in this token. Pay with the market's pair asset, its main or the market token."
    },
    "createMarket": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "Overcapped on WL buying": "That buy is over your whitelist limit for this token. Buy a smaller amount.",
      "token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "frozen needs presale": "An invite-only market or token needs a presale size above zero.",
      "Wrong fee": "The creation fee sent does not match. Refresh the fee (feeAmount, or 0 for a fee-whitelisted wallet) and try again.",
      "Fee transfer failed": "The creation fee could not be forwarded. Try again later.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "ReentrancyGuard: reentrant call": "The swap is busy with another step of this transaction. Try again.",
      "Approve": "Swap approval failed.",
      "Bad tax token": "Fee routing failed.",
      "Approval expired": "The market review approval expired before sending. Ask for a new review.",
      "Signer unset": "Not available right now. Not something you can fix here.",
      "Approval already used": "That approval was already used. Request a new review.",
      "Invalid approval": "The contract rejected the review approval (the name, symbol or answers changed, or it was signed for another contract). Request a fresh review.",
      "asset mismatch": "This main token is paired with a different asset than this market contract has registered for it, so a market can't be opened on it. Pick another main.",
      "hosts only": "This market can't take bets in this token. Pay with the market's pair asset, its main or the market token."
    },
    "manageWhitelist": {
      "no wallets provided": "Add at least one wallet to the whitelist.",
      "Wallet is not whitelisted": "That wallet is not on the whitelist."
    },
    "togglePrivateEventBuyers": {
      "no wallets provided": "Add at least one wallet to the whitelist.",
      "Wallet is not whitelisted": "That wallet is not on the whitelist."
    },
    "listOrder": {
      "empty order": "List at least some shares."
    },
    "buyOrder": {
      "pair unit": "The order book can't settle in this market's currency.",
      "dust fill": "That fill is too small. Buy more.",
      "max cost": "The order's price moved above your limit. Try again."
    }
  },
  "Vault": {
    "unstake": {
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "main transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "Zero amount": "Enter an amount.",
      "Insufficient stake": "You are trying to unstake more than you have staked.",
      "Collateral in use": "Part of your stake backs your loan. Repay the loan or unstake less.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "Bad path": "That trade route is not supported. Refresh and try again.",
      "Approve": "Swap approval failed.",
      "Min out": "The price moved past your slippage limit. Nothing was traded. Try again or allow more slippage.",
      "Zero sold": "That sale is too small to return anything. Sell more.",
      "Bad tax token": "Fee routing failed.",
      "ERC20: burn amount exceeds balance": "Not enough tokens to burn for this sale."
    },
    "borrow": {
      "Ecosystem inactive": "Loans are not available for this main token right now.",
      "incorrect loan duration": "Loan length must be between the minimum and maximum days (10 to 1,000 by default; read minDaysLoan/maxDaysLoan).",
      "Minimum loan amount not met": "That collateral is worth less than the minimum loan. Add more collateral.",
      "Tax approve failed": "Fee routing failed.",
      "cannot be zero": "Enter an amount above zero.",
      "Zero amount": "Enter an amount.",
      "Insufficient collateral value": "You are trying to borrow against more than your free stake.",
      "Contract low on liquidity": "That amount is too large right now; try less.",
      "Position active. Use addToLoan": "This vault position already has a loan. Borrow more with addToLoan instead.",
      "Hook not registered": "Vault loans are not available right now.",
      "No stable received": "The loan paid out nothing. Borrow against more stake.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Main not on this vault": "This main token is not served by this vault (the swap lists another staking vault for it), so you can't stake or borrow on it here."
    },
    "extendLoan": {
      "Pay in stable or refinance, not both": "Choose one: pay the extension fee from your wallet, or refinance. Not both.",
      "incorrect loan duration": "Loan length must be between the minimum and maximum days (10 to 1,000 by default; read minDaysLoan/maxDaysLoan).",
      "Tax approve failed": "Fee routing failed.",
      "No equity": "Refinancing needs the collateral to be worth more than the loan. It is not right now.",
      "Minimum surplus not met": "The collateral has not gained enough value to refinance.",
      "Time remaining too short": "A refinance without adding days needs at least the minimum loan term left on the loan. Add days too.",
      "Fees exceed surplus value": "The refinance fees are larger than the value it would free up.",
      "cannot be zero": "Enter an amount above zero.",
      "No active loan": "You have no open loan on this vault.",
      "not possible": "Adding 0 days only works as a refinance. Otherwise check the number of days.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again."
    },
    "addToLoan": {
      "No borrowed funds received": "Adding this collateral would not let you borrow anything more.",
      "Duration too short": "Extend your loan first.",
      "Zero amount": "Enter an amount.",
      "No borrowing power": "The added collateral does not raise what you can borrow.",
      "Fees exceed value": "The fees on this top-up would be larger than what it lets you borrow. Add more collateral.",
      "Tax approve failed": "Fee routing failed.",
      "cannot be zero": "Enter an amount above zero.",
      "Insufficient collateral value": "You are trying to borrow against more than your free stake.",
      "Contract low on liquidity": "That amount is too large right now; try less.",
      "No stable received": "The loan paid out nothing. Borrow against more stake.",
      "No active loan": "You have no open loan on this vault.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Main not on this vault": "This main token is not served by this vault (the swap lists another staking vault for it), so you can't stake or borrow on it here."
    },
    "stake": {
      "Below min stake": "That is below the vault's minimum stake (read minStake; 1 token by default).",
      "Main not on this vault": "This main token is not served by this vault (the swap lists another staking vault for it), so you can't stake or borrow on it here."
    },
    "claim": {
      "Nothing to claim": "No rewards to claim (rewards backing your loan stay in the vault)."
    },
    "compound": {
      "Nothing to compound": "No rewards to compound yet."
    },
    "repay": {
      "No active loan": "You have no open loan on this vault.",
      "Collateral short": "Repayment did not return all the pledged stake. Nothing was changed."
    },
    "*": {
      "Unknown main": "That main token is not listed on the swap, so the vault has no pair asset for it. Check the main address."
    }
  },
  "Vesting": {
    "takeLoanOnVesting": {
      "Ecosystem inactive": "Loans are not available for this main token right now.",
      "incorrect loan duration": "Loan length must be between the minimum and maximum days (10 to 1,000 by default; read minDaysLoan/maxDaysLoan).",
      "Minimum loan amount not met": "That collateral is worth less than the minimum loan. Add more collateral.",
      "Tax approve failed": "Fee routing failed.",
      "cannot be zero": "Enter an amount above zero.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Only beneficiary can take loan": "Only the vesting's recipient can borrow against it.",
      "Active loan exists": "This vesting already has a loan. Repay it first.",
      "No unclaimed tokens": "Nothing is left in this vesting to borrow against.",
      "Token approval failed": "Vesting approval failed.",
      "Borrowed token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again."
    },
    "repayExtendAndReborrow": {
      "Tax approve failed": "Fee routing failed.",
      "cannot be zero": "Enter an amount above zero.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Invalid token": "Fees can only be routed for BASIS tokens.",
      "mainPair transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "No unclaimed tokens": "Nothing is left in this vesting to borrow against.",
      "Token approval failed": "Vesting approval failed.",
      "Borrowed token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "No active loan": "This vesting has no loan.",
      "Borrowed token approve failed": "Vesting approval failed.",
      "Only beneficiary": "Only the vesting's recipient can do this.",
      "Loan not active": "The loan is no longer open (it was liquidated). Settle it with Repay first.",
      "Additional days must be greater than zero": "Add at least one day.",
      "Vesting already ended": "This vesting has already finished.",
      "Extension exceeds maximum duration": "That would leave more than 990 days on the vesting. Add fewer days."
    },
    "createGradualVesting": {
      "Invalid ecosystem": "That main token is not registered with vesting. Pick the token's own main.",
      "Not an Ecosystem token": "Only BASIS tokens can be vested.",
      "Amount must be greater than zero": "Enter an amount above zero for every recipient.",
      "Vesting duration must be at least 1 hour": "A vesting must last at least 1 hour.",
      "Duration too long": "A vesting can last at most 990 days.",
      "Start too far": "The start date can be at most 7 days from now.",
      "End too far": "The vesting must end within 990 days from now. Shorten it or start sooner.",
      "Insufficient fee": "Send the vesting fee in the chain's native coin with this transaction (feeAmount when feeEnabled and you are not fee-whitelisted; a surplus is refunded).",
      "ETH transfer failed": "The fee could not be forwarded or refunded.",
      "Invalid time unit": "Pick a valid time unit."
    },
    "createCliffVesting": {
      "Invalid ecosystem": "That main token is not registered with vesting. Pick the token's own main.",
      "Not an Ecosystem token": "Only BASIS tokens can be vested.",
      "Amount must be greater than zero": "Enter an amount above zero for every recipient.",
      "Vesting duration must be at least 1 hour": "A vesting must last at least 1 hour.",
      "Duration too long": "A vesting can last at most 990 days.",
      "Unlock time must be in the future": "Pick an unlock date in the future.",
      "Insufficient fee": "Send the vesting fee in the chain's native coin with this transaction (feeAmount when feeEnabled and you are not fee-whitelisted; a surplus is refunded).",
      "ETH transfer failed": "The fee could not be forwarded or refunded."
    },
    "batchCreateGradualVesting": {
      "Invalid ecosystem": "That main token is not registered with vesting. Pick the token's own main.",
      "Not an Ecosystem token": "Only BASIS tokens can be vested.",
      "Amount must be greater than zero": "Enter an amount above zero for every recipient.",
      "Vesting duration must be at least 1 hour": "A vesting must last at least 1 hour.",
      "Duration too long": "A vesting can last at most 990 days.",
      "Start too far": "The start date can be at most 7 days from now.",
      "End too far": "The vesting must end within 990 days from now. Shorten it or start sooner.",
      "Insufficient fee": "Send the vesting fee in the chain's native coin with this transaction (feeAmount when feeEnabled and you are not fee-whitelisted; a surplus is refunded).",
      "ETH transfer failed": "The fee could not be forwarded or refunded.",
      "Arrays length mismatch": "Every recipient needs an amount and a memo.",
      "Empty beneficiaries array": "Add at least one recipient.",
      "Invalid beneficiary address": "Every recipient needs a valid wallet address.",
      "Invalid time unit": "Pick a valid time unit."
    },
    "batchCreateCliffVesting": {
      "Invalid ecosystem": "That main token is not registered with vesting. Pick the token's own main.",
      "Not an Ecosystem token": "Only BASIS tokens can be vested.",
      "Amount must be greater than zero": "Enter an amount above zero for every recipient.",
      "Vesting duration must be at least 1 hour": "A vesting must last at least 1 hour.",
      "Duration too long": "A vesting can last at most 990 days.",
      "Unlock time must be in the future": "Pick an unlock date in the future.",
      "Insufficient fee": "Send the vesting fee in the chain's native coin with this transaction (feeAmount when feeEnabled and you are not fee-whitelisted; a surplus is refunded).",
      "ETH transfer failed": "The fee could not be forwarded or refunded.",
      "Arrays length mismatch": "Every recipient needs an amount and a memo.",
      "Empty beneficiaries array": "Add at least one recipient.",
      "Invalid beneficiary address": "Every recipient needs a valid wallet address."
    },
    "changeBeneficiary": {
      "Invalid beneficiary address": "Every recipient needs a valid wallet address.",
      "Only creator can change beneficiary": "Only the vesting's creator can change who receives it.",
      "Vesting renounced": "The creator gave up control of this vesting, so it can't be changed."
    },
    "extendVestingPeriod": {
      "Vesting renounced": "The creator gave up control of this vesting, so it can't be changed.",
      "Vesting does not exist": "No vesting with this ID.",
      "Only creator or beneficiary can extend": "Only the creator or the recipient can extend this vesting.",
      "Loan open: repay first": "This vesting has an open loan. Repay it first.",
      "Additional days must be greater than zero": "Add at least one day.",
      "Vesting already ended": "This vesting has already finished.",
      "Extension exceeds maximum duration": "That would leave more than 990 days on the vesting. Add fewer days."
    },
    "renounceVesting": {
      "Only creator can renounce": "Only the vesting's creator can renounce it.",
      "Already renounced": "This vesting is already renounced."
    },
    "repayLoanOnVesting": {
      "Borrowed token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Only beneficiary or creator": "Only the vesting's recipient, or its creator while the vesting is not renounced, can repay its loan.",
      "No active loan": "This vesting has no loan.",
      "Borrowed token approve failed": "Vesting approval failed."
    },
    "claimTokens": {
      "Only beneficiary can claim": "Only the vesting's recipient can claim.",
      "No tokens to claim": "Nothing has unlocked yet."
    },
    "addTokensToVesting": {
      "Vesting does not exist": "No vesting with this ID.",
      "Loan open: repay first": "This vesting has an open loan. Repay it first.",
      "Vesting already ended": "This vesting has already finished.",
      "Only creator can add tokens": "Only the vesting's creator can add tokens.",
      "Additional amount must be greater than zero": "Enter an amount above zero."
    },
    "getTokenVestingIds": {
      "Invalid index range": "Invalid page range.",
      "End index out of bounds": "Page goes past the last vesting."
    },
    "getVestingDetailsBatch": {
      "Invalid vesting ID": "No vesting with this ID."
    },
    "transferCreatorRole": {
      "Invalid vesting ID": "No vesting with this ID.",
      "Only creator can transfer role": "Only the vesting's creator can hand over the creator role.",
      "Invalid creator address": "Enter a valid wallet address."
    },
    "*": {
      "Memo too long": "A memo can be at most 256 bytes (emoji and accents count as more than one). Shorten it."
    }
  },
  "*": {
    "*": {
      "ERC20InsufficientAllowance": "Approve the amount first. Your approval is lower than this transaction needs.",
      "ERC20InsufficientBalance": "Not enough balance for this transaction.",
      "ERC721NonexistentToken": "No token with that id exists (ERC-721). For an agent: the agentId is not registered; check it (agent.ownerOf / the Registered event of your registration tx).",
      "ERC721InsufficientApproval": "This wallet is neither the owner of that token nor approved for it (ERC-721). For an agent: only its owner or an approved operator can change it.",
      "ERC721IncorrectOwner": "That token belongs to another wallet than the one given as its owner (ERC-721).",
      "ERC721InvalidOwner": "The zero address cannot own a token (ERC-721).",
      "ERC721InvalidSender": "Invalid sender for this token transfer (ERC-721): the zero address, or a token that does not exist.",
      "ERC721InvalidReceiver": "The receiver cannot take ERC-721 tokens: the zero address, or a contract without onERC721Received.",
      "ERC721InvalidApprover": "Invalid approver (ERC-721): only the owner or an approved operator can approve.",
      "ERC721InvalidOperator": "Invalid operator (ERC-721): the zero address cannot be an operator.",
      "Only CEO": "Only BASIS can do this.",
      "Only DEV": "Only the token's creator can do this.",
      "Only MT": "Only the main token contract can call this.",
      "CEO required": "This token can't be created right now: the factory is not set up for it.",
      "presale above half the launch supply": "On a reversed token (Meme+, Yolo+, Yolo) the presale can be at most half of the launch supply. Lower the presale.",
      "Presale over": "The presale has finished. Refresh: the token now trades on its normal market.",
      "No WL for freeze": "This token is whitelist-only right now, and your wallet is not on the list.",
      "token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "has not bonded yet": "The token is still in its presale. Refresh and try again.",
      "no wallets provided": "Add at least one wallet to the whitelist.",
      "Wallet is not whitelisted": "That wallet is not on the whitelist.",
      "cannot be zero": "Enter an amount above zero.",
      "main transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "unknown pair": "This token's main token is not registered with the swap, or its pair is paused.",
      "auto-vest duration 1-990 days": "Auto-vesting must last between 1 and 990 days.",
      "frozen needs presale": "An invite-only market or token needs a presale size above zero.",
      "invalid starting LP": "Starting liquidity must be between 1,000 and 100,000.",
      "invalid supply multiplier": "The supply multiplier is outside what this main token allows.",
      "Wrong fee": "The creation fee sent does not match. Refresh the fee (feeAmount, or 0 for a fee-whitelisted wallet) and try again.",
      "not recyclable": "This wallet's rewards cannot be recycled yet: it must be flagged and 7 days must pass.",
      "hook not allowed": "That V4 pool's hook is not on the allow list.",
      "Only swap": "Only the BASIS swap can run a hop.",
      "no quote": "The outside pool could not quote this swap.",
      "not the pool": "Unexpected pool callback.",
      "partial fill": "The outside pool could not fill the whole amount (not enough liquidity in range). Try a smaller amount.",
      "not the pool manager": "Unexpected V4 callback.",
      "bad delta": "The outside pool returned an invalid result.",
      "Zero address": "An address is missing.",
      "Not an ecosystem": "Only a registered main can report a liquidation.",
      "Ecosystem inactive": "Loans are not available for this main token right now.",
      "Collateral transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Collateral approve failed": "Loan hub approval failed.",
      "Stable transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Invalid loan": "No loan with this ID in your wallet.",
      "Core loan inactive": "This loan is already closed (repaid, sold or liquidated).",
      "Stable approve failed": "Loan hub approval failed.",
      "Not all collateral returned": "The repayment did not return the collateral it should (its share on a partial repay). Nothing was changed.",
      "Collateral return failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Pay in stable or refinance, not both": "Choose one: pay the extension fee from your wallet, or refinance. Not both.",
      "Fee approve failed": "Loan hub approval failed.",
      "No borrowed funds received": "Adding this collateral would not let you borrow anything more.",
      "Claim transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "No swap for ecosystem": "Selling part of a loan is not available for this main token right now.",
      "No mainPair received from swap": "The sale returned nothing. Try a larger percentage.",
      "Loan does not exist": "No hub loan with this ID for that wallet. Hub ids start at 1.",
      "Trading is not enabled": "Trading on this pair has not opened yet.",
      "incorrect loan duration": "Loan length must be between the minimum and maximum days (10 to 1,000 by default; read minDaysLoan/maxDaysLoan).",
      "Unauthorized caller": "Open and manage loans through the loan hub, and leverage positions from your own wallet.",
      "Minimum loan amount not met": "That collateral is worth less than the minimum loan. Add more collateral.",
      "Duration too short": "Extend your loan first.",
      "Zero amount": "Enter an amount.",
      "No borrowing power": "The added collateral does not raise what you can borrow.",
      "Fees exceed value": "The fees on this top-up would be larger than what it lets you borrow. Add more collateral.",
      "Tax approve failed": "Fee routing failed.",
      "Must be refinance if not extending time": "Adding 0 days only works as a refinance. Pick days, or turn on refinance.",
      "mainPair fee transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "No equity": "Refinancing needs the collateral to be worth more than the loan. It is not right now.",
      "Minimum surplus not met": "The collateral has not gained enough value to refinance.",
      "Time remaining too short": "A refinance without adding days needs at least the minimum loan term left on the loan. Add days too.",
      "Fees exceed surplus value": "The refinance fees are larger than the value it would free up.",
      "Not enough collateral to cover fees": "Your collateral cannot cover the extension fee. Pay the fee from your wallet instead.",
      "Only liquidator": "A loan can only be liquidated by the protocol, after it expires.",
      "Invalid percentage": "Pick a percentage between 1 and 100.",
      "cannot sell off with no profit": "Selling this part would not cover its share of the debt. Wait for the price to rise, or repay instead.",
      "payout failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Loan is still active": "This loan is still open. There is nothing to claim until it is liquidated.",
      "Only ecosystem tokens": "Only BASIS tokens can be used as collateral.",
      "wrong pair": "This token trades against a different main token. Borrow on that main.",
      "Only registry": "Only the factory registry can call this.",
      "Invalid mainPair amount": "That amount rounds to zero.",
      "Insufficient mainPair reserve": "That amount is too large to fill in one go. Try a smaller amount.",
      "Already liquidated": "This position was liquidated. There is nothing left to sell; claim the residual instead.",
      "Below min stake": "That is below the vault's minimum stake (read minStake; 1 token by default).",
      "Insufficient stake": "You are trying to unstake more than you have staked.",
      "Collateral in use": "Part of your stake backs your loan. Repay the loan or unstake less.",
      "Nothing to compound": "No rewards to compound yet.",
      "Insufficient collateral value": "You are trying to borrow against more than your free stake.",
      "Contract low on liquidity": "That amount is too large right now; try less.",
      "Position active. Use addToLoan": "This vault position already has a loan. Borrow more with addToLoan instead.",
      "Hook not registered": "Vault loans are not available right now.",
      "Collateral short": "Repayment did not return all the pledged stake. Nothing was changed.",
      "not possible": "Adding 0 days only works as a refinance. Otherwise check the number of days.",
      "Unknown loan": "Unknown vault loan.",
      "Only STAKING": "Only a registered vault can call this.",
      "wrong path": "Leverage must be paid in the main token's pair asset. Pick that currency.",
      "Transfer": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Range": "The leverage percentage is out of range: mixedBuy takes 1 to 99 (100 = a plain leverageBuy); the simulator takes 1 to 100.",
      "Deadline": "The time limit ran out before this went out. Nothing was sent. Try again.",
      "native in": "To pay in the chain's native coin, the route must start with its wrapped token and the coin sent must equal the amount.",
      "Transfer in": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Alien output": "You can only buy BASIS tokens here.",
      "Not main": "This token's quote token is not a registered main.",
      "Bad pair": "You can't pay with that currency for this token. Pick its main token or pair asset.",
      "Approve": "Swap approval failed.",
      "Alien token": "You can't pay with that token here.",
      "use swapCross": "Selling straight to the chain's native coin goes through the cross-pair swap.",
      "Wrong token": "You can only sell BASIS tokens here.",
      "Not ecosystem": "That token is not a BASIS factory token.",
      "Not MAIN": "The middle of the route must be a registered main token.",
      "Wrong main": "This token trades against a different main token. Refresh the route.",
      "Not pair asset": "That currency is not this main token's pair asset.",
      "Sell: need market token": "A redeem must send the market's own token.",
      "Buy: need asset/MAIN": "This token can't pay for this bet here: it needs an outside route to the market's pair asset, and no route executor is set up on this chain. Pay with the pair asset, the market's main or a BASIS token that shares the pair asset.",
      "Bad tax token": "Fee routing failed.",
      "% by 10": "Sell in steps of 10% (10, 20 ... 100).",
      "% range": "Pick between 10% and 100%.",
      "Not user": "This position is not in your wallet. Hub loans are sold through the loan hub.",
      "Min out not met": "The price moved past your slippage limit. Nothing was sold. Try again or allow more slippage.",
      "In presale": "This token auto-vests during its presale, so leverage opens only after the presale.",
      "WL cap": "This leveraged buy is over your whitelist limit. Use a smaller amount.",
      "same token": "You can't swap a token for itself.",
      "Bad route order": "That route is not supported. Refresh the route.",
      "no BASIS leg": "A cross-pair swap must include a BASIS token.",
      "round trip": "That route sells and buys the same main token. Use a normal trade.",
      "autovest presale": "This token auto-vests during its presale; buy it directly, not through a cross-pair swap.",
      "no recipient": "A recipient address is required.",
      "not native": "To receive the chain's native coin, the route must end in its wrapped token.",
      "ETH fail": "The native-coin payout could not be sent to the recipient.",
      "not on MAIN": "Main tokens have no creator or rewards fee.",
      "only dev": "Only the token's creator can change its fees.",
      "over max": "That fee is above the maximum for this token (creator fee max 1%; rewards fee max from maxRewardsFee).",
      "fee cooldown": "You changed this fee recently. Wait for the cooldown to end.",
      "closed": "New referrals are not accepted right now.",
      "referrer set": "Your referrer is already set. Once set, you can't change it yourself.",
      "invalid referrer": "You can't be your own referrer.",
      "referral loop": "That wallet was referred by you, so it can't be your referrer.",
      "Cannot set for MAINTOKEN": "Main tokens have no creator fee shares.",
      "Only DEV can add shares": "Only the token's creator can add fee shares.",
      "Invalid wallet": "Enter a wallet address.",
      "Basis points must be >0": "A share must be above 0%.",
      "Exceeds 100%": "Fee shares can't add up to more than 100%.",
      "Max 10 shares": "A token can have at most 10 fee recipients.",
      "Only DEV can remove shares": "Only the token's creator can remove fee shares.",
      "only swap": "Only the BASIS swap can call this.",
      "base above rate": "Fee routing error.",
      "not a BASIS contract": "Fees can only come from BASIS contracts.",
      "Rewards approve failed": "Fee routing failed.",
      "Vault approve failed": "Fee routing failed.",
      "Invalid ecosystem": "That main token is not registered with vesting. Pick the token's own main.",
      "Not an Ecosystem token": "Only BASIS tokens can be vested.",
      "Amount must be greater than zero": "Enter an amount above zero for every recipient.",
      "Vesting duration must be at least 1 hour": "A vesting must last at least 1 hour.",
      "Duration too long": "A vesting can last at most 990 days.",
      "Start too far": "The start date can be at most 7 days from now.",
      "End too far": "The vesting must end within 990 days from now. Shorten it or start sooner.",
      "Unlock time must be in the future": "Pick an unlock date in the future.",
      "Arrays length mismatch": "Every recipient needs an amount and a memo.",
      "Empty beneficiaries array": "Add at least one recipient.",
      "Invalid beneficiary address": "Every recipient needs a valid wallet address.",
      "Only creator can change beneficiary": "Only the vesting's creator can change who receives it.",
      "Vesting renounced": "The creator gave up control of this vesting, so it can't be changed.",
      "Only creator can renounce": "Only the vesting's creator can renounce it.",
      "Already renounced": "This vesting is already renounced.",
      "Only beneficiary can take loan": "Only the vesting's recipient can borrow against it.",
      "Active loan exists": "This vesting already has a loan. Repay it first.",
      "No unclaimed tokens": "Nothing is left in this vesting to borrow against.",
      "Token approval failed": "Vesting approval failed.",
      "Borrowed token transfer failed": "A token transfer inside the contract failed. Check your balance and approval, then try again.",
      "Only beneficiary or creator": "Only the vesting's recipient, or its creator while the vesting is not renounced, can repay its loan.",
      "Borrowed token approve failed": "Vesting approval failed.",
      "Only beneficiary": "Only the vesting's recipient can do this.",
      "Only beneficiary can claim": "Only the vesting's recipient can claim.",
      "No tokens to claim": "Nothing has unlocked yet.",
      "Vesting does not exist": "No vesting with this ID.",
      "Only creator or beneficiary can extend": "Only the creator or the recipient can extend this vesting.",
      "Loan open: repay first": "This vesting has an open loan. Repay it first.",
      "Additional days must be greater than zero": "Add at least one day.",
      "Vesting already ended": "This vesting has already finished.",
      "Extension exceeds maximum duration": "That would leave more than 990 days on the vesting. Add fewer days.",
      "Only creator can add tokens": "Only the vesting's creator can add tokens.",
      "Additional amount must be greater than zero": "Enter an amount above zero.",
      "Invalid time unit": "Pick a valid time unit.",
      "Invalid index range": "Invalid page range.",
      "End index out of bounds": "Page goes past the last vesting.",
      "Invalid vesting ID": "No vesting with this ID.",
      "Only creator can transfer role": "Only the vesting's creator can hand over the creator role.",
      "Invalid creator address": "Enter a valid wallet address.",
      "Not an approved host": "Market creation is not available on this contract.",
      "Bad ranking": "That ranking is not valid for this market (check the places, repeats and ties).",
      "Ranked market: use the *Ranked entrypoints": "This market is resolved with a ranking. Pick the finishing order, not a single answer.",
      "No uint8 host": "Single-answer resolution is not available on this resolver.",
      "Bad outcome": "That answer is not an option in this market.",
      "Not a ranked market": "This market takes a single answer, not a ranking.",
      "Too early to propose": "It's too early to propose a result. The creator can propose at close; everyone else 15 minutes after.",
      "Already proposed": "A result has already been proposed. You can dispute it instead.",
      "No proposal to dispute": "No result has been proposed yet. Propose one instead.",
      "Already in dispute": "This result is already disputed and in voting.",
      "Dispute period over": "The window to dispute this result has closed.",
      "Must be different": "A dispute must propose a different result from the one on the table.",
      "Proposer cannot dispute self": "You proposed this result, so you can't dispute it.",
      "Wait more": "It's too early to finalize. Wait for the current window to end.",
      "Not voting": "This market is not in a vote.",
      "cannot vote during veto": "This ruling was vetoed, so voting is closed. After a veto, the BASIS team makes the final ruling.",
      "Disputed period ended": "The voting period has ended.",
      "Not an authorized voter": "Your wallet is not on the resolution voter list.",
      "Voted": "You already voted in this round.",
      "Invalid market": "Unknown market.",
      "Already resolved": "This market is already resolved.",
      "Veto only allowed on disputed/voting markets": "A veto is only possible after a dispute.",
      "Veto only allowed once": "This market has already been vetoed.",
      "Already vetoed": "This market has already been vetoed.",
      "Cant veto propose dispute outcome": "A veto must name a result other than the disputed one.",
      "Veto window not started yet": "The veto window opens when voting ends.",
      "Party to the dispute cannot veto": "The proposer and the disputer can't veto.",
      "Cannot veto back to the proposal": "A veto can't restore the original proposal.",
      "Betting closed": "Betting on this market has closed.",
      "Outcome proposed": "A result has been proposed for this market: no bets while the proposal is live.",
      "slices 1-25": "Pick between 1 and 25 slices.",
      "empty order": "List at least some shares.",
      "pair unit": "The order book can't settle in this market's currency.",
      "dust fill": "That fill is too small. Buy more.",
      "max cost": "The order's price moved above your limit. Try again.",
      "Below minOut": "The payout came in under your minimum. Try again, use more slices, or lower the minimum.",
      "fraction 1-10000": "Pick a share of your payout between 0.01% and 100%.",
      "Nothing to redeem": "You have nothing left to redeem on this market.",
      "Approval expired": "The market review approval expired before sending. Ask for a new review.",
      "Signer unset": "Not available right now. Not something you can fix here.",
      "Approval already used": "That approval was already used. Request a new review.",
      "Invalid approval": "The contract rejected the review approval (the name, symbol or answers changed, or it was signed for another contract). Request a fresh review.",
      "Rules: not qualified": "This wallet does not meet the proposal rules for this market, so it can't propose. It can still dispute or veto.",
      "ERC20: insufficient allowance": "Approve the amount first. Your approval is lower than this transaction needs.",
      "ERC20: transfer amount exceeds balance": "Not enough balance for this transaction.",
      "ERC20: transfer to the zero address": "The recipient address is missing.",
      "ERC20: transfer from the zero address": "Transfer from the zero address.",
      "ERC20: burn amount exceeds balance": "Not enough tokens to burn for this sale.",
      "ERC20: burn from the zero address": "Burn from the zero address.",
      "ERC20: mint to the zero address": "Mint to the zero address.",
      "ERC20: approve to the zero address": "The spender address is missing (a contract address is still 0x0 on this chain).",
      "ERC20: approve from the zero address": "Approve from the zero address.",
      "ERC20: decreased allowance below zero": "You can't lower the approval below zero.",
      "SafeERC20: ERC20 operation did not succeed": "A token transfer failed. Check your balance and approval.",
      "Address: call to non-contract": "That token address is not a contract on this chain.",
      "ERC20: transfer amount exceeds allowance": "Approve the amount first. Your approval is lower than this transaction needs.",
      "BEP20: transfer amount exceeds allowance": "Approve the amount first. Your approval is lower than this transaction needs.",
      "BEP20: transfer amount exceeds balance": "Not enough balance for this transaction.",
      "SafeERC20: low-level call failed": "The token transfer failed without giving a reason. Check your balance and approval, then try again.",
      "hosts only": "This market can't take bets in this token. Pay with the market's pair asset, its main or the market token.",
      "Extend first": "Your open leverage position on this token has less than the minimum loan days left (minDaysLoan). Extend it before adding to it.",
      "Open loan: increase it": "You already have an open loan on this main with this collateral. Add to it with increaseLoan instead of taking a second loan.",
      "Main not on this vault": "This main token is not served by this vault (the swap lists another staking vault for it), so you can't stake or borrow on it here.",
      "Repay in full": "Repay the whole loan: what's left would be under the minimum loan.",
      "Not enough to close": "Not enough to repay all. Add a bit more and try again.",
      "hubs only": "Only a loan hub can convert a token for a repay.",
      "Unknown main": "That main token is not registered with the swap, so the vault can't use it.",
      "Loan active": "This vault loan is still open, so it can't be settled as liquidated yet.",
      "no ETH": "This contract does not take native coin sent to it directly."
    }
  }
}

NO_REASON_HINTS = {
  "HopExecutor": {
    "quoteHop": [
      "The outside pool rejected this swap. Try a smaller amount or try again."
    ]
  },
  "LoanHub": {
    "hubPartialLoanSell": [
      "Clearing stuck liquidity failed."
    ],
    "takeLoan": [
      "Token transfer or approval failed."
    ],
    "repayLoanWith": [
      "The outside pool rejected this swap. Try a smaller amount or try again."
    ]
  },
  "MarketResolver": {
    "proposeRanked": [
      "This market is unknown or already resolved.",
      "Approve the bond first, and make sure you hold enough of the bond token."
    ],
    "dispute": [
      "This market is unknown or already resolved.",
      "Approve the bond first, and make sure you hold enough of the bond token."
    ],
    "disputeRanked": [
      "This market is unknown or already resolved.",
      "Approve the bond first, and make sure you hold enough of the bond token."
    ],
    "disputeRankedEarly": [
      "This market is unknown or already resolved.",
      "Approve the bond first, and make sure you hold enough of the bond token."
    ],
    "finalizeUncontested": [
      "This market is already resolved.",
      "There is no uncontested proposal to finalize (none yet, or it was disputed: use finalize after voting).",
      "Bond payout failed.",
      "Token transfer or approval failed."
    ],
    "finalizeMarket": [
      "This market is not ready to finalize (not in a dispute, already resolved, or in its veto window).",
      "Bond payout failed.",
      "Token transfer or approval failed."
    ],
    "veto": [
      "Approve the veto bond first, and make sure you hold enough of the bond token."
    ],
    "vetoRanked": [
      "Approve the veto bond first, and make sure you hold enough of the bond token."
    ]
  },
  "MultiUpDown": {
    "claim": [
      "That amount is too small to convert into the pair asset. On a bet this means the bet fee is worth less than the smallest unit of the pair asset: bet a larger amount. On a redeem or claim nothing was sold: use a larger amount.",
      "Clearing stuck liquidity failed.",
      "That token is not a BASIS market token.",
      "Token transfer or approval failed."
    ],
    "betBull": [
      "That amount is too small to convert into the pair asset. On a bet this means the bet fee is worth less than the smallest unit of the pair asset: bet a larger amount. On a redeem or claim nothing was sold: use a larger amount.",
      "That bet is too small to convert into market tokens. Bet more.",
      "That token is not a BASIS market token.",
      "Token transfer or approval failed.",
      "The outside pool rejected this swap. Try a smaller amount or try again."
    ],
    "betBear": [
      "That amount is too small to convert into the pair asset. On a bet this means the bet fee is worth less than the smallest unit of the pair asset: bet a larger amount. On a redeem or claim nothing was sold: use a larger amount.",
      "That bet is too small to convert into market tokens. Bet more.",
      "That token is not a BASIS market token.",
      "Token transfer or approval failed.",
      "The outside pool rejected this swap. Try a smaller amount or try again."
    ],
    "settleCurrentRound": [
      "The price feed did not answer, so the round can't be opened or settled right now. Try again shortly."
    ],
    "cancelCurrentRoundAndStartNext": [
      "The price feed did not answer, so the round can't be opened or settled right now. Try again shortly."
    ]
  },
  "Swap": {
    "sellTokens": [
      "Clearing stuck liquidity failed."
    ],
    "partialLoanSellFor": [
      "Clearing stuck liquidity failed."
    ],
    "swapCross": [
      "Clearing stuck liquidity failed.",
      "The outside pool rejected this swap. Try a smaller amount or try again."
    ],
    "buyTokens": [
      "Token transfer or approval failed."
    ],
    "quoteCross": [
      "The outside pool rejected this swap. Try a smaller amount or try again."
    ],
    "repayWith": [
      "The outside pool rejected this swap. Try a smaller amount or try again."
    ]
  },
  "TrancheMarket": {
    "redeem": [
      "This market isn't resolved yet. Redeem opens once it is.",
      "You have no winning (or refundable) shares in this market.",
      "Token transfer or approval failed.",
      "That amount is too small to convert into the pair asset. On a bet this means the bet fee is worth less than the smallest unit of the pair asset: bet a larger amount. On a redeem or claim nothing was sold: use a larger amount.",
      "Clearing stuck liquidity failed.",
      "That token is not a BASIS market token."
    ],
    "createMarket": [
      "Market creation is paused on this contract right now.",
      "Markets can't be created on this main token right now.",
      "This market contract has no resolver set. Not something you can fix here.",
      "The close time must be at least 1 minute from now (or none, for an open-ended market), and must match the approval.",
      "A market needs between 2 and the maximum number of answers (MAX_OUTCOMES).",
      "Approve the seed amount first, and make sure you hold it.",
      "The seed converted to nothing. Raise the seed.",
      "The creation fee sent does not match. Refresh the fee and try again.",
      "The seed must be at least this market contract's minimum for the chosen main and a whole multiple of its seed step (10 units of that main).",
      "Prize places must be between 1 and the maximum, and no more than the number of answers.",
      "Each prize place needs a minimum share. Change the split.",
      "Prize shares can't grow from one place to the next.",
      "Prize shares must add up to 100%.",
      "The seed is too small to open this many answers. Raise the seed or use fewer answers.",
      "Token transfer or approval failed.",
      "Too many outcomes for one market.",
      "That bet is too small to convert into market tokens. Bet more.",
      "That token is not a BASIS market token."
    ],
    "buy": [
      "That bet is below this market's minimum bet, or below your minimum amount after conversion.",
      "Betting is paused on this contract right now.",
      "This market can't take that bet (unknown market, already resolved, no such answer, or zero amount).",
      "This market is whitelist-only, and your wallet is not on the list.",
      "You would get fewer shares than your minimum. The price moved. Try again.",
      "Approve the bet amount first, and make sure you hold it.",
      "That bet is too small to convert. Bet more.",
      "Token transfer or approval failed.",
      "That amount is too small to convert into the pair asset. On a bet this means the bet fee is worth less than the smallest unit of the pair asset: bet a larger amount. On a redeem or claim nothing was sold: use a larger amount.",
      "That bet is too small to convert into market tokens. Bet more.",
      "That token is not a BASIS market token.",
      "The outside pool rejected this swap. Try a smaller amount or try again."
    ],
    "buyOrdersAndContract": [
      "Trading is paused on this contract right now.",
      "This market is resolved. Trading is closed.",
      "Pay with this market's own currency.",
      "You would get fewer shares than your minimum. The price moved. Try again.",
      "Betting is paused on this contract right now.",
      "This market can't take that bet (unknown market, already resolved, no such answer, or zero amount).",
      "This market is whitelist-only, and your wallet is not on the list.",
      "You can't buy your own order.",
      "This order is priced above the market right now.",
      "The seller no longer holds these shares.",
      "Approve the market's currency first, and make sure you hold enough.",
      "Approve the bet amount first, and make sure you hold it.",
      "That bet is too small to convert. Bet more.",
      "Pay with the market's own currency.",
      "Token transfer or approval failed.",
      "That amount is too small to convert into the pair asset. On a bet this means the bet fee is worth less than the smallest unit of the pair asset: bet a larger amount. On a redeem or claim nothing was sold: use a larger amount.",
      "That bet is too small to convert into market tokens. Bet more.",
      "That token is not a BASIS market token."
    ],
    "disableFreeze": [
      "Only the market's creator can lift the whitelist."
    ],
    "setWhitelistedWallet": [
      "Only the market's creator can edit its whitelist."
    ],
    "removeWhitelist": [
      "Only the market's creator can edit its whitelist."
    ],
    "listOrder": [
      "This market is resolved. Listings are closed.",
      "Limit price must be between 0.001 and 1,000,000 per share (or 0 for the market price).",
      "You don't have that many free shares to list (some may already be listed)."
    ],
    "buyOrder": [
      "Trading is paused on this contract right now.",
      "This market is resolved. Trading is closed.",
      "This order is no longer available.",
      "Fill amount is zero or larger than the order.",
      "You can't buy your own order.",
      "This order is priced above the market right now.",
      "The seller no longer holds these shares.",
      "Approve the market's currency first, and make sure you hold enough.",
      "Token transfer or approval failed."
    ],
    "cancelOrder": [
      "This isn't your order.",
      "This order is already filled or cancelled."
    ]
  },
  "TrancheMarketPrivate": {
    "createMarket": [
      "Market creation is paused on this contract right now.",
      "Markets can't be created on this main token right now.",
      "The close time must be at least 1 minute from now (or none, for an open-ended market), and must match the approval.",
      "A market needs between 2 and the maximum number of answers (MAX_OUTCOMES).",
      "Approve the seed amount first, and make sure you hold it.",
      "The seed converted to nothing. Raise the seed.",
      "The creation fee sent does not match. Refresh the fee and try again.",
      "The seed must be at least this market contract's minimum for the chosen main and a whole multiple of its seed step (10 units of that main).",
      "Prize places must be between 1 and the maximum, and no more than the number of answers.",
      "Each prize place needs a minimum share. Change the split.",
      "Prize shares can't grow from one place to the next.",
      "Prize shares must add up to 100%.",
      "The seed is too small to open this many answers. Raise the seed or use fewer answers.",
      "Token transfer or approval failed.",
      "Too many outcomes for one market.",
      "That bet is too small to convert into market tokens. Bet more.",
      "That token is not a BASIS market token."
    ],
    "buyOrdersAndContract": [
      "Trading is paused on this contract right now.",
      "This market is resolved. Trading is closed.",
      "Pay with this market's own currency.",
      "You would get fewer shares than your minimum. The price moved. Try again.",
      "Betting is paused on this contract right now.",
      "This market can't take that bet (already resolved, no such answer, or zero amount).",
      "This is a private market and your wallet is not on its buyer list.",
      "Betting on this market has closed.",
      "Voting has started on this open-ended market, so betting is closed.",
      "This market is whitelist-only, and your wallet is not on the list.",
      "You can't buy your own order.",
      "This order is priced above the market right now.",
      "The seller no longer holds these shares.",
      "Approve the market's currency first, and make sure you hold enough.",
      "Approve the bet amount first, and make sure you hold it.",
      "That bet is too small to convert. Bet more.",
      "Pay with the market's own currency.",
      "Token transfer or approval failed.",
      "That amount is too small to convert into the pair asset. On a bet this means the bet fee is worth less than the smallest unit of the pair asset: bet a larger amount. On a redeem or claim nothing was sold: use a larger amount.",
      "That bet is too small to convert into market tokens. Bet more.",
      "That token is not a BASIS market token."
    ],
    "manageVoter": [
      "Only the market's creator can manage its voters.",
      "Voting has started, so the voter list is locked.",
      "Enter a voter address.",
      "That wallet is already a voter.",
      "A market can have at most 11 voters.",
      "That wallet is not a voter (and the creator can't be removed)."
    ],
    "togglePrivateEventBuyers": [
      "Only the creator of a private market can manage its buyers."
    ],
    "manageWhitelist": [
      "Only the market's creator can edit its whitelist."
    ],
    "listOrder": [
      "This market is resolved. Listings are closed.",
      "Limit price must be between 0.001 and 1,000,000 per share (or 0 for the market price).",
      "You don't have that many free shares to list (some may already be listed)."
    ],
    "buyOrder": [
      "Trading is paused on this contract right now.",
      "This market is resolved. Trading is closed.",
      "This order is no longer available.",
      "Fill amount is zero or larger than the order.",
      "You can't buy your own order.",
      "This order is priced above the market right now.",
      "The seller no longer holds these shares.",
      "Approve the market's currency first, and make sure you hold enough.",
      "Token transfer or approval failed."
    ],
    "vote": [
      "You are not a voter on this market, or you already voted.",
      "This market is already resolved.",
      "Voting opens when the market closes.",
      "That ranking is not valid for this market.",
      "Token transfer or approval failed."
    ],
    "finalize": [
      "This market is already resolved.",
      "Voting is still open (or nobody has voted yet). Finalize after the voting window.",
      "Nobody voted, so the market can't be finalized.",
      "The vote is tied and the creator didn't vote to break it.",
      "Token transfer or approval failed."
    ],
    "cancelOrder": [
      "This isn't your order.",
      "This order is already filled or cancelled."
    ]
  },
  "Vault": {
    "unstake": [
      "Token transfer or approval failed.",
      "Clearing stuck liquidity failed."
    ],
    "stake": [
      "Token transfer or approval failed."
    ],
    "claim": [
      "Token transfer or approval failed."
    ],
    "borrow": [
      "Token transfer or approval failed."
    ],
    "addToLoan": [
      "Token transfer or approval failed."
    ],
    "repay": [
      "Token transfer or approval failed."
    ],
    "extendLoan": [
      "Token transfer or approval failed."
    ]
  },
  "Vesting": {
    "changeBeneficiary": [
      "This vesting has a loan. The beneficiary must repay or settle it first."
    ],
    "claimTokens": [
      "This vesting has an open loan. Repay (or settle) it before claiming."
    ]
  }
}

PANIC_MESSAGES = {
  "0x00": "The contract hit an internal error. Nothing was changed.",
  "0x01": "Unexpected contract state.",
  "0x11": "The numbers didn't add up for this transaction (often an amount larger than what is left). Try a smaller amount.",
  "0x12": "This market or pool has no liquidity yet.",
  "0x21": "The contract hit a value it does not recognise. Refresh and try again.",
  "0x22": "The contract hit an internal storage error. Nothing was changed.",
  "0x31": "The contract hit an internal error (empty list). Refresh and try again.",
  "0x32": "That item doesn't exist.",
  "0x41": "That list is too long for one transaction. Split it into smaller batches.",
  "0x51": "The contract hit an internal error. Nothing was changed."
}

INTERNAL_ERRORS = ["QuoteResult"]

EXTRA_ERRORS = [
  {
    "type": "error",
    "name": "ERC20InsufficientAllowance",
    "inputs": [
      {
        "name": "spender",
        "type": "address"
      },
      {
        "name": "allowance",
        "type": "uint256"
      },
      {
        "name": "needed",
        "type": "uint256"
      }
    ]
  },
  {
    "type": "error",
    "name": "ERC20InsufficientBalance",
    "inputs": [
      {
        "name": "sender",
        "type": "address"
      },
      {
        "name": "balance",
        "type": "uint256"
      },
      {
        "name": "needed",
        "type": "uint256"
      }
    ]
  },
  {
    "type": "error",
    "name": "ERC721NonexistentToken",
    "inputs": [
      {
        "name": "tokenId",
        "type": "uint256"
      }
    ]
  },
  {
    "type": "error",
    "name": "ERC721InsufficientApproval",
    "inputs": [
      {
        "name": "operator",
        "type": "address"
      },
      {
        "name": "tokenId",
        "type": "uint256"
      }
    ]
  },
  {
    "type": "error",
    "name": "ERC721IncorrectOwner",
    "inputs": [
      {
        "name": "sender",
        "type": "address"
      },
      {
        "name": "tokenId",
        "type": "uint256"
      },
      {
        "name": "owner",
        "type": "address"
      }
    ]
  },
  {
    "type": "error",
    "name": "ERC721InvalidOwner",
    "inputs": [
      {
        "name": "owner",
        "type": "address"
      }
    ]
  },
  {
    "type": "error",
    "name": "ERC721InvalidSender",
    "inputs": [
      {
        "name": "sender",
        "type": "address"
      }
    ]
  },
  {
    "type": "error",
    "name": "ERC721InvalidReceiver",
    "inputs": [
      {
        "name": "receiver",
        "type": "address"
      }
    ]
  },
  {
    "type": "error",
    "name": "ERC721InvalidApprover",
    "inputs": [
      {
        "name": "approver",
        "type": "address"
      }
    ]
  },
  {
    "type": "error",
    "name": "ERC721InvalidOperator",
    "inputs": [
      {
        "name": "operator",
        "type": "address"
      }
    ]
  }
]

GENERIC_REVERT = "The transaction would fail. The contract gave no reason."
