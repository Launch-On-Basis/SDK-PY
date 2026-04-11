import logging

from web3 import Web3
from .factory import load_abi

logger = logging.getLogger(__name__)

class PredictionMarketsModule:
    def __init__(self, client, market_trading_address: str):
        self.client = client
        self.market_trading_address = Web3.to_checksum_address(market_trading_address)
        self.market_trading_abi = load_abi('AMarketTrading.json')
        self.erc20_abi = load_abi('IERC20.json')
        self._contract = self.client.web3.eth.contract(address=self.market_trading_address, abi=self.market_trading_abi)

    def _sync_tx(self, tx_hash: str):
        """Sync tx to backend. Raises on failure."""
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        self.client.api.sync_transaction(tx_hash)

    def _approve_if_needed(self, token_address: str, amount: int):
        if not self.client.account:
            raise ValueError("Wallet account is required for approval.")

        checksum_addr = Web3.to_checksum_address(token_address)
        token_contract = self.client.web3.eth.contract(address=checksum_addr, abi=self.erc20_abi)

        allowance = token_contract.functions.allowance(
            self.client.account.address, self.market_trading_address
        ).call()

        if allowance < amount:
            func = token_contract.functions.approve(self.market_trading_address, amount)
            self.client.send_transaction(func)

    def create_market_with_metadata(
        self,
        market_name: str,
        symbol: str,
        end_time: int,
        option_names: list[str],
        maintoken: str = None,
        image_url: str = None,
        image_file: str = None,
        description: str = None,
        website: str = None,
        telegram: str = None,
        twitterx: str = None,
        frozen: bool = False,
        bonding: int = 0,
        seed_amount: int = 0,
    ):
        """Creates a prediction market and registers metadata on IPFS in one call.

        This is the ONLY way to create a market — image and metadata are mandatory.

        Requires SIWE authentication.
        Returns dict with hash, receipt, market_token_address, image_url, metadata.

        Args:
            end_time: Unix timestamp in seconds
            bonding: USDB amount in wei (18 decimals)
            seed_amount: USDB amount in wei (18 decimals)
            image_url: URL of the market image (provide image_url or image_file)
            image_file: local file path for the image (alternative to image_url)
        """
        # 0. Validate image up front — fail before spending gas
        if not image_url and not image_file:
            raise ValueError('Either image_url or image_file is required.')

        # 1. Create market on-chain
        if not maintoken:
            maintoken = self.client.main_token_address
        checksum_maintoken = Web3.to_checksum_address(maintoken)
        eco_data = self._contract.functions.ecosystems(checksum_maintoken).call()
        factory_address = eco_data[0]
        if factory_address == '0x' + '0' * 40:
            raise ValueError(
                f"Token {maintoken} is not a registered ecosystem token — "
                f"cannot create a market under it. Use an existing ecosystem token address as maintoken."
            )
        factory_abi = load_abi('ATokenFactory.json')
        factory_contract = self.client.web3.eth.contract(address=factory_address, abi=factory_abi)
        fee_amount = factory_contract.functions.feeAmount().call()

        if seed_amount > 0:
            self._approve_if_needed(self.client.usdb_address, seed_amount)

        func = self._contract.functions.createMarket(market_name, symbol, end_time, option_names, checksum_maintoken, frozen, bonding, seed_amount)
        create_result = self.client.send_transaction(func, value=fee_amount)

        receipt = create_result['receipt']
        if receipt.get('status') == 0:
            raise RuntimeError(f"Market creation reverted (tx: {create_result['hash']})")

        # Parse market token from MarketCreated event
        market_created_topic = Web3.keccak(text="MarketCreated(address,address,address)").hex()
        mt_lower = self.market_trading_address.lower()
        market_token_address = None
        for log_entry in receipt.get('logs', []):
            addr = log_entry.get('address', '')
            if addr.lower() != mt_lower:
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

        # Upload image
        if image_file:
            uploaded_image_url = self.client.api.upload_image(image_file, purpose='token', address=market_token_address)
        else:
            uploaded_image_url = self.client.api.upload_image_from_url(image_url, contract_address=market_token_address)

        # Create metadata
        metadata = self.client.api.update_metadata(
            address=market_token_address,
            description=description,
            image=uploaded_image_url,
            website=website,
            telegram=telegram,
            twitterx=twitterx,
        )

        result = {
            'hash': create_result['hash'],
            'receipt': receipt,
            'market_token_address': market_token_address,
            'image_url': uploaded_image_url,
            'metadata': metadata,
        }
        self._sync_tx(result['hash'])
        return result

    def buy(self, market_token: str, outcome_id: int, input_token: str, input_amount: int, min_usdb: int, min_shares: int):
        """Buys shares in a prediction market outcome. Auto-approves input token.

        Args:
            input_amount: input token amount in wei (18 decimals)
            min_usdb: minimum USDB in wei (18 decimals)
            min_shares: minimum shares in wei (18 decimals)
        """
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_input = Web3.to_checksum_address(input_token)
        
        self._approve_if_needed(checksum_input, input_amount)
        
        func = self._contract.functions.buy(checksum_market, outcome_id, checksum_input, input_amount, min_usdb, min_shares)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def redeem(self, market_token: str):
        """Redeems shares from a resolved market."""
        checksum_market = Web3.to_checksum_address(market_token)
        func = self._contract.functions.redeem(checksum_market)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def get_market_data(self, market_token: str):
        """Returns market data for a prediction market."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self._contract.functions.getMarketData(checksum_market).call()

    def get_outcome(self, market_token: str, outcome_id: int):
        """Returns outcome data for a specific outcome."""
        checksum_market = Web3.to_checksum_address(market_token)
        return self._contract.functions.getOutcome(checksum_market, outcome_id).call()

    def get_user_shares(self, market_token: str, user: str, outcome_id: int):
        """Returns a user's shares for a specific outcome."""
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_user = Web3.to_checksum_address(user)
        return self._contract.functions.getUserShares(checksum_market, checksum_user, outcome_id).call()

    def buy_orders_and_contract(self, market_token: str, outcome_id: int, order_ids: list[int], input_token: str, total_input: int, min_shares: int):
        """Buys from order book and AMM in a single transaction. Auto-approves input token.

        Args:
            total_input: input token amount in wei (18 decimals)
            min_shares: minimum shares in wei (18 decimals)
        """
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_input = Web3.to_checksum_address(input_token)
        self._approve_if_needed(checksum_input, total_input)
        func = self._contract.functions.buyOrdersAndContract(checksum_market, outcome_id, order_ids, checksum_input, total_input, min_shares)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        # Also sync order fills since this method fills P2P orders
        self.client.api.sync_order(result['hash'], 'public')
        return result

    def get_initial_reserves(self, num_outcomes: int) -> tuple:
        """Returns (perOutcome, totalReserve) for a given number of outcomes."""
        return self._contract.functions.getInitialReserves(num_outcomes).call()

    def get_num_outcomes(self, market_token: str) -> int:
        """Returns the number of outcomes for a market."""
        return self._contract.functions.getNumOutcomes(Web3.to_checksum_address(market_token)).call()

    def get_option_names(self, market_token: str) -> list:
        """Returns the option names for a market."""
        return self._contract.functions.getOptionNames(Web3.to_checksum_address(market_token)).call()

    def has_betted_on_market(self, market_token: str, user: str) -> bool:
        """Returns whether a user has bet on a market."""
        return self._contract.functions.hasBettedOnMarket(
            Web3.to_checksum_address(market_token), Web3.to_checksum_address(user)
        ).call()

    def get_bounty_pool(self, market_token: str) -> int:
        """Returns the bounty pool amount for a market."""
        return self._contract.functions.getBountyPool(Web3.to_checksum_address(market_token)).call()

    def get_general_pot(self, market_token: str) -> int:
        """Returns the general pot amount for a market."""
        return self._contract.functions.getGeneralPot(Web3.to_checksum_address(market_token)).call()

    def get_buy_order_amounts_out(self, market_token: str, order_id: int, usdb_amount: int):
        """Returns the amounts out when buying an order with a specific USDB amount.

        Args:
            usdb_amount: USDB amount in wei (18 decimals)
        """
        return self._contract.functions.getBuyOrderAmountsOut(
            Web3.to_checksum_address(market_token), order_id, usdb_amount
        ).call()
