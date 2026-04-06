import logging
from web3 import Web3
from .factory import load_abi

logger = logging.getLogger(__name__)


class PrivateMarketsModule:
    def __init__(self, client, private_market_address: str):
        self.client = client
        self.private_market_address = Web3.to_checksum_address(private_market_address)
        self.private_market_abi = load_abi('APrivateTradingMarket.json')
        self.erc20_abi = load_abi('IERC20.json')
        self.contract = self.client.web3.eth.contract(address=self.private_market_address, abi=self.private_market_abi)

    def _approve_if_needed(self, token_address: str, amount: int):
        if not self.client.account:
            raise ValueError("Wallet account is required for approval.")

        checksum_addr = Web3.to_checksum_address(token_address)
        token_contract = self.client.web3.eth.contract(address=checksum_addr, abi=self.erc20_abi)

        allowance = token_contract.functions.allowance(
            self.client.account.address, self.private_market_address
        ).call()

        if allowance < amount:
            func = token_contract.functions.approve(self.private_market_address, amount)
            self.client.send_transaction(func)

    def _sync_tx(self, tx_hash: str):
        """Sync tx to backend. Non-fatal on failure."""
        try:
            if not tx_hash.startswith("0x"):
                tx_hash = "0x" + tx_hash
            self.client.api.sync_transaction(tx_hash)
        except Exception as e:
            logger.warning("Sync warning: %s", e)

    def _sync_order(self, tx_hash: str):
        """Sync order tx to backend. Non-fatal on failure."""
        try:
            if not tx_hash.startswith("0x"):
                tx_hash = "0x" + tx_hash
            self.client.api.sync_order(tx_hash, 'private')
        except Exception as e:
            logger.warning("Order sync warning: %s", e)

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    def _create_market(self, market_name: str, symbol: str, end_time: int, option_names: list[str], maintoken: str, private_event: bool, frozen: bool, bonding: int, seed_amount: int = 0):
        """Internal: creates a private market on-chain. Use create_market_with_metadata() instead."""
        checksum_maintoken = Web3.to_checksum_address(maintoken)
        eco_data = self.contract.functions.ecosystems(checksum_maintoken).call()
        factory_address = eco_data[0]
        factory_abi = load_abi('ATokenFactory.json')
        factory_contract = self.client.web3.eth.contract(address=factory_address, abi=factory_abi)
        fee_amount = factory_contract.functions.feeAmount().call()

        # Auto-approve USDB for seed amount if needed
        if seed_amount > 0:
            self._approve_if_needed(self.client.usdb_address, seed_amount)

        func = self.contract.functions.createMarket(market_name, symbol, end_time, option_names, checksum_maintoken, private_event, frozen, bonding, seed_amount)
        result = self.client.send_transaction(func, value=fee_amount)
        self._sync_tx(result['hash'])
        return result

    def create_market_with_metadata(self, market_name: str, symbol: str, end_time: int,
                                     option_names: list, maintoken: str, private_event: bool = True,
                                     frozen: bool = False, bonding: int = 0, seed_amount: int = 0,
                                     description: str = None, image_url: str = None,
                                     website: str = None, telegram: str = None, twitterx: str = None):
        """Creates a private market and registers its metadata on IPFS in one call.

        Requires SIWE authentication.
        Returns dict with hash, receipt, market_token_address, image_url, metadata.
        """
        create_result = self._create_market(
            market_name=market_name, symbol=symbol, end_time=end_time,
            option_names=option_names, maintoken=maintoken, private_event=private_event,
            frozen=frozen, bonding=bonding, seed_amount=seed_amount,
        )

        receipt = create_result['receipt']
        if receipt.get('status') == 0:
            raise RuntimeError(f"Private market creation reverted (tx: {create_result['hash']})")

        # Parse market token from MarketCreated event
        market_created_topic = Web3.keccak(text="MarketCreated(address,address,address)").hex()
        private_lower = self.private_market_address.lower()
        market_token_address = None
        for log_entry in receipt.get('logs', []):
            addr = log_entry.get('address', '')
            if addr.lower() != private_lower:
                continue
            topics = log_entry.get('topics', [])
            if not topics:
                continue
            t0 = topics[0].hex() if isinstance(topics[0], bytes) else str(topics[0])
            if t0 == market_created_topic and len(topics) > 1:
                raw = topics[1].hex() if isinstance(topics[1], bytes) else str(topics[1])
                market_token_address = Web3.to_checksum_address("0x" + raw[-40:])
                break

        if not market_token_address:
            raise RuntimeError("Could not extract market address from creation logs.")

        # Upload image if provided
        uploaded_image_url = None
        if image_url:
            uploaded_image_url = self.client.api.upload_image_from_url(image_url, contract_address=market_token_address)

        # Create metadata on IPFS
        metadata = self.client.api.update_metadata(
            address=market_token_address,
            description=description,
            image=uploaded_image_url,
            website=website,
            telegram=telegram,
            twitterx=twitterx,
        )

        return {
            'hash': create_result['hash'],
            'receipt': create_result['receipt'],
            'market_token_address': market_token_address,
            'image_url': uploaded_image_url,
            'metadata': metadata,
        }

    def buy(self, market_token: str, outcome_id: int, input_token: str, input_amount: int, min_usdb: int, min_shares: int):
        """Buys shares in a private market outcome. Auto-approves input token."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_input = Web3.to_checksum_address(input_token)
        self._approve_if_needed(checksum_input, input_amount)
        func = self.contract.functions.buy(checksum_market, outcome_id, checksum_input, input_amount, min_usdb, min_shares)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def redeem(self, market_token: str):
        """Redeems shares from a resolved private market."""
        checksum_market = Web3.to_checksum_address(market_token)
        func = self.contract.functions.redeem(checksum_market)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def list_order(self, market_token: str, outcome_id: int, amount: int, price_per_share: int):
        """Lists a sell order on the private market order book."""
        checksum_market = Web3.to_checksum_address(market_token)
        func = self.contract.functions.listOrder(checksum_market, outcome_id, amount, price_per_share)
        result = self.client.send_transaction(func)
        self._sync_order(result['hash'])
        return result

    def cancel_order(self, market_token: str, order_id: int):
        """Cancels an existing order on the private market."""
        checksum_market = Web3.to_checksum_address(market_token)
        func = self.contract.functions.cancelOrder(checksum_market, order_id)
        result = self.client.send_transaction(func)
        self._sync_order(result['hash'])
        return result

    def buy_order(self, market_token: str, order_id: int, fill: int):
        """Buys from an existing order on the private market. Auto-approves USDB."""
        checksum_market = Web3.to_checksum_address(market_token)
        # Auto-approve USDB for the order cost
        cost = self.get_buy_order_cost(market_token, order_id, fill)
        total_cost = cost[2]  # totalCostToBuyer at index 2
        self._approve_if_needed(self.client.usdb_address, int(total_cost))
        func = self.contract.functions.buyOrder(checksum_market, order_id, fill)
        result = self.client.send_transaction(func)
        self._sync_order(result['hash'])
        return result

    def buy_multiple_orders(self, market_token: str, order_ids: list[int], usdb_amount: int):
        """Buys from multiple orders on the private market. Auto-approves USDB."""
        checksum_market = Web3.to_checksum_address(market_token)
        # Auto-approve USDB for the total input amount
        self._approve_if_needed(self.client.usdb_address, usdb_amount)
        func = self.contract.functions.buyMultipleOrders(checksum_market, order_ids, usdb_amount)
        result = self.client.send_transaction(func)
        self._sync_order(result['hash'])
        return result

    def buy_orders_and_contract(self, market_token: str, outcome_id: int, order_ids: list[int], input_token: str, total_input: int, min_shares: int):
        """Buys from order book and AMM in a single transaction. Auto-approves input token."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_input = Web3.to_checksum_address(input_token)
        self._approve_if_needed(checksum_input, total_input)
        func = self.contract.functions.buyOrdersAndContract(checksum_market, outcome_id, order_ids, checksum_input, total_input, min_shares)
        result = self.client.send_transaction(func)
        self._sync_order(result['hash'])
        return result

    def vote(self, market_token: str, outcome_id: int):
        """Casts a vote on a private market outcome."""
        checksum_market = Web3.to_checksum_address(market_token)
        func = self.contract.functions.vote(checksum_market, outcome_id)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def finalize(self, market_token: str):
        """Finalizes a private market after voting is complete."""
        checksum_market = Web3.to_checksum_address(market_token)
        func = self.contract.functions.finalize(checksum_market)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def claim_bounty(self, market_token: str):
        """Claims the bounty reward for voting correctly."""
        checksum_market = Web3.to_checksum_address(market_token)
        func = self.contract.functions.claimBounty(checksum_market)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def manage_voter(self, market_token: str, voter: str, status: bool):
        """Manages voter status for a private market."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_voter = Web3.to_checksum_address(voter)
        func = self.contract.functions.manageVoter(checksum_market, checksum_voter, status)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def toggle_private_event_buyers(self, market_token: str, buyers: list[str], status: bool):
        """Toggles whether specific addresses can buy in a private event market."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_buyers = [Web3.to_checksum_address(b) for b in buyers]
        func = self.contract.functions.togglePrivateEventBuyers(checksum_market, checksum_buyers, status)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def disable_freeze(self, market_token: str):
        """Disables the freeze on a private market."""
        checksum_market = Web3.to_checksum_address(market_token)
        func = self.contract.functions.DisableFreeze(checksum_market)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def manage_whitelist(self, market_token: str, wallets: list[str], amount: int, tag: str, status: bool):
        """Manages the whitelist for a private market."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_wallets = [Web3.to_checksum_address(w) for w in wallets]
        func = self.contract.functions.manageWhitelist(checksum_market, checksum_wallets, amount, tag, status)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    def get_market_data(self, market_token: str):
        """Returns market data for a private market."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.getMarketData(checksum_market).call()

    def get_outcome(self, market_token: str, outcome_id: int):
        """Returns outcome data for a specific outcome."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.outcomes(checksum_market, outcome_id).call()

    def get_user_shares(self, market_token: str, user: str, outcome_id: int):
        """Returns a user's shares for a specific outcome."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_user = Web3.to_checksum_address(user)
        return self.contract.functions.userShares(checksum_market, checksum_user, outcome_id).call()

    def get_buy_order_cost(self, market_token: str, order_id: int, fill: int):
        """Returns the cost to buy a specific order fill amount."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.getBuyOrderCost(checksum_market, order_id, fill).call()

    def get_initial_reserves(self, num_outcomes: int) -> tuple:
        """Returns (perOutcome, totalReserve) for a given number of outcomes."""
        return self.contract.functions.getInitialReserves(num_outcomes).call()

    def get_num_outcomes(self, market_token: str) -> int:
        """Returns the number of outcomes for a market."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.getNumOutcomes(checksum_market).call()

    def has_betted(self, market_token: str, user: str) -> bool:
        """Returns whether a user has bet on a market."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_user = Web3.to_checksum_address(user)
        return self.contract.functions.hasBetted(checksum_market, checksum_user).call()

    def get_bounty_pool(self, market_token: str) -> int:
        """Returns the bounty pool amount for a market."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.bountyPool(checksum_market).call()

    def get_buy_order_amounts_out(self, market_token: str, order_id: int, usdb_amount: int):
        """Returns the amounts out when buying an order with a specific USDB amount."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.getBuyOrderAmountsOut(checksum_market, order_id, usdb_amount).call()

    def get_market_orders(self, market_token: str, order_id: int):
        """Returns an order by market and order ID."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.marketOrders(checksum_market, order_id).call()

    def get_next_order_id(self, market_token: str) -> int:
        """Returns the next order ID for a market."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.nextOrderId(checksum_market).call()

    def is_market_voter(self, market_token: str, voter: str) -> bool:
        """Returns whether an address is a voter for a market."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_voter = Web3.to_checksum_address(voter)
        return self.contract.functions.isMarketVoter(checksum_market, checksum_voter).call()

    def get_voter_choice(self, market_token: str, voter: str) -> int:
        """Returns the outcome a voter chose for a market."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_voter = Web3.to_checksum_address(voter)
        return self.contract.functions.voterChoice(checksum_market, checksum_voter).call()

    def get_first_vote_time(self, market_token: str) -> int:
        """Returns the first vote time for a market."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.firstVoteTime(checksum_market).call()

    def can_user_buy(self, market_token: str, user: str) -> bool:
        """Returns whether a user can buy in a private event market."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_user = Web3.to_checksum_address(user)
        return self.contract.functions.userCanBuyEvent(checksum_market, checksum_user).call()

    def get_bounty_per_vote(self, market_token: str) -> int:
        """Returns the bounty per correct vote for a market."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.bountyPerCorrectVote(checksum_market).call()

    def has_claimed(self, market_token: str, voter: str) -> bool:
        """Returns whether a voter has claimed the bounty for a market."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_voter = Web3.to_checksum_address(voter)
        return self.contract.functions.bountyClaimed(checksum_market, checksum_voter).call()
