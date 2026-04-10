from web3 import Web3
from .factory import load_abi


class MarketReaderModule:
    def __init__(self, client, reader_address: str):
        self.client = client
        self.reader_address = Web3.to_checksum_address(reader_address)
        self.reader_abi = load_abi('AMarketReader.json')
        self._contract = self.client.web3.eth.contract(address=self.reader_address, abi=self.reader_abi)

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    def get_all_outcomes(self, router_address: str, market_token: str) -> list:
        """Returns all outcomes for a market."""
        checksum_router = Web3.to_checksum_address(router_address)
        checksum_market = Web3.to_checksum_address(market_token)
        return self._contract.functions.getAllOutcomes(checksum_router, checksum_market).call()

    def estimate_shares_out(self, router_address: str, market_token: str, outcome_id: int, usdb_amount: int, order_ids: list[int], user: str) -> int:
        """Estimates the number of shares received for a given USDB input.

        Args:
            usdb_amount: USDB amount in wei (18 decimals)
        """
        checksum_router = Web3.to_checksum_address(router_address)
        checksum_market = Web3.to_checksum_address(market_token)
        checksum_user = Web3.to_checksum_address(user)
        return self._contract.functions.estimateSharesOut(checksum_router, checksum_market, outcome_id, usdb_amount, order_ids, checksum_user).call()

    def get_potential_payout(self, router_address: str, market_token: str, outcome_id: int, shares_amount: int, estimated_usdb_to_pool: int) -> tuple:
        """Returns the potential payout for selling shares.

        Args:
            shares_amount: shares in wei (18 decimals)
            estimated_usdb_to_pool: USDB amount in wei (18 decimals)
        """
        checksum_router = Web3.to_checksum_address(router_address)
        checksum_market = Web3.to_checksum_address(market_token)
        return self._contract.functions.getPotentialPayout(checksum_router, checksum_market, outcome_id, shares_amount, estimated_usdb_to_pool).call()
