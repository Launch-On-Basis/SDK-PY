import logging
from web3 import Web3
from .factory import load_abi

logger = logging.getLogger(__name__)


class TradingModule:
    def __init__(self, client, swap_address: str):
        self.client = client
        self.swap_address = Web3.to_checksum_address(swap_address)
        self.swap_abi = load_abi('ASwap.json')
        self.erc20_abi = load_abi('IERC20.json')
        self.token_abi = load_abi('FACTORYTOKEN.json')
        self.maintoken_abi = load_abi('MAINTOKEN.json')
        self.contract = self.client.web3.eth.contract(address=self.swap_address, abi=self.swap_abi)

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
            self.client.account.address, self.swap_address
        ).call()

        if allowance < amount:
            func = token_contract.functions.approve(self.swap_address, amount)
            self.client.send_transaction(func)

    def _build_buy_path(self, token_address: str) -> list[str]:
        usdb = self.client.usdb_address
        main_token = self.client.main_token_address
        target = Web3.to_checksum_address(token_address)

        if target == main_token:
            return [usdb, main_token]
        return [usdb, main_token, target]

    def _build_sell_path(self, token_address: str, to_usdb: bool) -> list[str]:
        usdb = self.client.usdb_address
        main_token = self.client.main_token_address
        target = Web3.to_checksum_address(token_address)

        if target == main_token:
            return [main_token, usdb]
        if to_usdb:
            return [target, main_token, usdb]
        return [target, main_token]

    def buy(self, token_address: str, usdb_amount: int, min_out: int = 0, wrap_tokens: bool = False):
        """Simplified buy: purchases the target token using USDB. Builds path automatically.

        Args:
            usdb_amount: USDB amount in wei (18 decimals)
            min_out: minimum output in wei (18 decimals), 0 for no slippage protection
        """
        path = self._build_buy_path(token_address)
        return self.buy_tokens(usdb_amount, min_out, path, wrap_tokens)

    def sell(self, token_address: str, amount: int, to_usdb: bool = False, min_out: int = 0, swap_to_eth: bool = False):
        """Simplified sell: sells a token. Set to_usdb=True to swap factory tokens all the way to USDB.

        Args:
            amount: token amount in wei (18 decimals)
            min_out: minimum output in wei (18 decimals), 0 for no slippage protection
        """
        path = self._build_sell_path(token_address, to_usdb)
        return self.sell_tokens(amount, min_out, path, swap_to_eth)

    def buy_bonding_tokens(self, amount: int, min_out: int, path: list[str], wrap_tokens: bool):
        """Buy bonding tokens.

        Args:
            amount: USDB amount in wei (18 decimals)
            min_out: minimum output in wei (18 decimals)
        """
        return self.buy_tokens(amount, min_out, path, wrap_tokens)

    def sell_bonding_tokens(self, amount: int, min_out: int, path: list[str], swap_to_eth: bool):
        """Sell bonding tokens.

        Args:
            amount: token amount in wei (18 decimals)
            min_out: minimum output in wei (18 decimals)
        """
        return self.sell_tokens(amount, min_out, path, swap_to_eth)

    def buy_tokens(self, amount: int, min_out: int, path: list[str], wrap_tokens: bool):
        """Buy tokens along a swap path.

        Args:
            amount: USDB amount in wei (18 decimals)
            min_out: minimum output in wei (18 decimals)
        """
        checksum_path = [Web3.to_checksum_address(p) for p in path]
        if checksum_path:
            self._approve_if_needed(checksum_path[0], amount)
            
        func = self.contract.functions.buyTokens(amount, min_out, checksum_path, wrap_tokens)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def sell_tokens(self, amount: int, min_out: int, path: list[str], swap_to_eth: bool):
        """Sell tokens along a swap path.

        Args:
            amount: token amount in wei (18 decimals)
            min_out: minimum output in wei (18 decimals)
        """
        checksum_path = [Web3.to_checksum_address(p) for p in path]
        if checksum_path:
            self._approve_if_needed(checksum_path[0], amount)
            
        func = self.contract.functions.sellTokens(amount, min_out, checksum_path, swap_to_eth)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def get_token_price(self, token_address: str):
        checksum_addr = Web3.to_checksum_address(token_address)
        token_contract = self.client.web3.eth.contract(address=checksum_addr, abi=self.token_abi)
        price = token_contract.functions.getTokenPrice().call()
        return str(price)

    def get_usd_price(self, token_address: str):
        checksum_addr = Web3.to_checksum_address(token_address)
        token_contract = self.client.web3.eth.contract(address=checksum_addr, abi=self.token_abi)
        price = token_contract.functions.getUSDPrice().call()
        return str(price)

    def leverage_buy(self, amount: int, min_out: int, path: list[str], number_of_days: int):
        """Leveraged buy: purchases tokens with leverage (creates a loan position).

        Args:
            amount: USDB amount in wei (18 decimals)
            min_out: minimum output in wei (18 decimals)
            number_of_days: integer, minimum 10
        """
        checksum_path = [Web3.to_checksum_address(p) for p in path]
        if checksum_path:
            self._approve_if_needed(checksum_path[0], amount)
        func = self.contract.functions.leverageBuy(amount, min_out, checksum_path, number_of_days)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def partial_loan_sell(self, loan_id: int, percentage: int, is_leverage: bool, min_out: int = 0):
        """Partially sells collateral from a loan/leverage position.

        Args:
            percentage: integer 10-100, divisible by 10
            min_out: minimum output in wei (18 decimals)
        """
        func = self.contract.functions.partialLoanSell(loan_id, percentage, is_leverage, min_out)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def sell_percentage(self, token_address: str, percentage: int, to_usdb: bool = False, min_out: int = 0, swap_to_eth: bool = False):
        """Sells a percentage of the user's token balance.

        Args:
            percentage: integer 1-100
            min_out: minimum output in wei (18 decimals)
        """
        if not self.client.account:
            raise ValueError("Stateful initialization (private_key) is required for write methods.")
        if percentage < 1 or percentage > 100:
            raise ValueError("Percentage must be between 1 and 100.")

        checksum_addr = Web3.to_checksum_address(token_address)
        token_contract = self.client.web3.eth.contract(address=checksum_addr, abi=self.erc20_abi)
        balance = token_contract.functions.balanceOf(self.client.account.address).call()

        if balance == 0:
            raise ValueError("Token balance is zero.")

        sell_amount = (balance * percentage) // 100
        return self.sell(token_address, sell_amount, to_usdb, min_out, swap_to_eth)

    _LEVERAGE_ABI = [
        {"inputs":[{"name":"","type":"address"}],"name":"leverageCount","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
        {"inputs":[{"name":"","type":"address"},{"name":"","type":"uint256"}],"name":"leverages","outputs":[{"name":"user","type":"address"},{"name":"token","type":"address"},{"name":"collateralAmount","type":"uint256"},{"name":"liquidatedAmount","type":"uint256"},{"name":"fullAmount","type":"uint256"},{"name":"borrowedAmount","type":"uint256"},{"name":"liquidationTime","type":"uint256"},{"name":"liquidationClaim","type":"uint256"},{"name":"isLiquidated","type":"bool"},{"name":"active","type":"bool"},{"name":"creationTime","type":"uint256"},{"name":"timeOfClosure","type":"uint256"},{"name":"leverage","type":"tuple","components":[{"name":"leverageBuyAmount","type":"uint256"},{"name":"cashedOut","type":"uint256"}]}],"stateMutability":"view","type":"function"},
    ]

    def get_leverage_count(self, user: str) -> int:
        """Gets the leverage position count for a user from MAINTOKEN."""
        checksum_user = Web3.to_checksum_address(user)
        contract = self.client.web3.eth.contract(
            address=self.client.main_token_address, abi=self._LEVERAGE_ABI
        )
        return contract.functions.leverageCount(checksum_user).call()

    def get_leverage_position(self, user: str, loan_id: int):
        """Gets a specific leverage position from MAINTOKEN."""
        checksum_user = Web3.to_checksum_address(user)
        contract = self.client.web3.eth.contract(
            address=self.client.main_token_address, abi=self._LEVERAGE_ABI
        )
        return contract.functions.leverages(checksum_user, loan_id).call()

    def get_amounts_out(self, amount: int, path: list[str]) -> int:
        """Returns the estimated output amounts for a given input amount and swap path.

        Args:
            amount: input amount in wei (18 decimals)
        """
        checksum_path = [Web3.to_checksum_address(p) for p in path]
        return self.contract.functions.getAmountsOut(amount, checksum_path).call()
