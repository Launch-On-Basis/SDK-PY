import logging
from web3 import Web3
from .factory import load_abi

logger = logging.getLogger(__name__)


class OrderBookModule:
    def __init__(self, client, market_trading_address: str):
        self.client = client
        self.market_trading_address = Web3.to_checksum_address(market_trading_address)
        self.market_trading_abi = load_abi('AMarketTrading.json')
        self.erc20_abi = load_abi('IERC20.json')
        self.contract = self.client.web3.eth.contract(address=self.market_trading_address, abi=self.market_trading_abi)

    def _approve_usdb_if_needed(self, amount: int):
        if not self.client.account:
            return
        usdb = Web3.to_checksum_address(self.client.usdb_address)
        token_contract = self.client.web3.eth.contract(address=usdb, abi=self.erc20_abi)
        allowance = token_contract.functions.allowance(
            self.client.account.address, self.market_trading_address
        ).call()
        if allowance < amount:
            func = token_contract.functions.approve(self.market_trading_address, amount)
            self.client.send_transaction(func)

    def _sync_order(self, tx_hash: str, market_type: str = "public"):
        """Sync order tx to backend. Raises on failure."""
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        self.client.api.sync_order(tx_hash, market_type)

    def list_order(self, market_token: str, outcome_id: int, amount: int, price_per_share: int):
        """Lists a sell order on the order book.

        Args:
            amount: shares in wei (18 decimals)
            price_per_share: USDB per share in wei (18 decimals)
        """
        checksum_market = Web3.to_checksum_address(market_token)
        func = self.contract.functions.listOrder(checksum_market, outcome_id, amount, price_per_share)
        result = self.client.send_transaction(func)
        self._sync_order(result['hash'])
        return result

    def cancel_order(self, market_token: str, order_id: int):
        """Cancels an existing order on the order book."""
        checksum_market = Web3.to_checksum_address(market_token)
        func = self.contract.functions.cancelOrder(checksum_market, order_id)
        result = self.client.send_transaction(func)
        self._sync_order(result['hash'])
        return result

    def buy_order(self, market_token: str, order_id: int, fill: int):
        """Buys from an existing order. Auto-approves USDB.

        Args:
            fill: shares to fill in wei (18 decimals)
        """
        checksum_market = Web3.to_checksum_address(market_token)
        # Auto-approve USDB for the order cost
        cost = self.get_buy_order_cost(checksum_market, order_id, fill)
        total_cost = cost[2]  # totalCostToBuyer at index 2
        self._approve_usdb_if_needed(int(total_cost))
        func = self.contract.functions.buyOrder(checksum_market, order_id, fill)
        result = self.client.send_transaction(func)
        self._sync_order(result['hash'])
        return result

    def buy_multiple_orders(self, market_token: str, order_ids: list[int], usdb_amount: int):
        """Buys from multiple orders. Auto-approves USDB.

        Args:
            usdb_amount: USDB amount in wei (18 decimals)
        """
        checksum_market = Web3.to_checksum_address(market_token)
        # Auto-approve USDB for the total input amount
        self._approve_usdb_if_needed(usdb_amount)
        func = self.contract.functions.buyMultipleOrders(checksum_market, order_ids, usdb_amount)
        result = self.client.send_transaction(func)
        self._sync_order(result['hash'])
        return result

    def get_buy_order_cost(self, market_token: str, order_id: int, fill: int):
        """Returns the cost to buy a specific order fill amount.

        Args:
            fill: shares to fill in wei (18 decimals)
        """
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.getBuyOrderCost(checksum_market, order_id, fill).call()

    def get_buy_order_amounts_out(self, market_token: str, order_id: int, usdb_amount: int):
        """Preview how many shares can be bought for a given USDB amount on a P2P order.

        Args:
            usdb_amount: USDB amount in wei (18 decimals)
        """
        checksum_market = Web3.to_checksum_address(market_token)
        return self.contract.functions.getBuyOrderAmountsOut(checksum_market, order_id, usdb_amount).call()
