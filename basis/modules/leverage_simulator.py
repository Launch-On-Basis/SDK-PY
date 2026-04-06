from web3 import Web3
from .factory import load_abi


class LeverageSimulatorModule:
    def __init__(self, client, leverage_address: str):
        self.client = client
        self.leverage_address = Web3.to_checksum_address(leverage_address)
        self.leverage_abi = load_abi('ALEVERAGE.json')
        self.contract = self.client.web3.eth.contract(address=self.leverage_address, abi=self.leverage_abi)

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    def simulate_leverage(self, amount: int, path: list[str], number_of_days: int):
        """Simulates a leverage position and returns the result."""
        checksum_path = [Web3.to_checksum_address(p) for p in path]
        return self.contract.functions.simulateLeverage(amount, checksum_path, number_of_days).call()

    def simulate_leverage_factory(self, amount: int, path: list[str], number_of_days: int):
        """Simulates a leverage position for factory tokens and returns the result."""
        checksum_path = [Web3.to_checksum_address(p) for p in path]
        return self.contract.functions.simulateLeverageFactory(amount, checksum_path, number_of_days).call()

    def calculate_floor(self, hybrid_multiplier: int, reserve0: int, reserve1: int, base_reserve0: int, xe_reserve0: int, xe_reserve1: int) -> int:
        """Calculates the floor price for a hybrid token."""
        return self.contract.functions.calculateFloor(hybrid_multiplier, reserve0, reserve1, base_reserve0, xe_reserve0, xe_reserve1).call()

    def get_token_price(self, reserve0: int, reserve1: int) -> int:
        """Returns the token price given reserves."""
        return self.contract.functions.getTokenPrice(reserve0, reserve1).call()

    def get_usd_price(self, reserve0: int, reserve1: int, xe_reserve0: int, xe_reserve1: int) -> int:
        """Returns the USD price given reserves."""
        return self.contract.functions.getUSDPrice(reserve0, reserve1, xe_reserve0, xe_reserve1).call()

    def get_collateral_value(self, token_amount: int, reserve0: int, reserve1: int) -> int:
        """Returns the collateral value for a given token amount."""
        return self.contract.functions.getColleteralValue(token_amount, reserve0, reserve1).call()

    def get_collateral_value_hybrid(self, token_amount: int, reserve0: int, reserve1: int, xe_reserve0: int, xe_reserve1: int, multiplier: int, base_reserve0: int) -> int:
        """Returns the collateral value for a hybrid token."""
        return self.contract.functions.getColleteralValueHybrid(token_amount, reserve0, reserve1, xe_reserve0, xe_reserve1, multiplier, base_reserve0).call()

    def calculate_tokens_for_buy(self, usdb_amount: int, reserve0: int, reserve1: int) -> int:
        """Calculates the number of tokens received for a given USDB input."""
        return self.contract.functions.calculateTokensForBuy(usdb_amount, reserve0, reserve1).call()

    def calculate_tokens_to_burn(self, amount_in: int, multiplier: int, input_reserve0: int, input_reserve1: int, splitter: int) -> int:
        """Calculates the number of tokens to burn."""
        return self.contract.functions.calculateTokensToBurn(amount_in, multiplier, input_reserve0, input_reserve1, splitter).call()
