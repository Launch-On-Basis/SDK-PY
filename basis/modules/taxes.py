import logging

from web3 import Web3
from .factory import load_abi

logger = logging.getLogger(__name__)


class TaxesModule:
    def __init__(self, client, taxes_address: str):
        self.client = client
        self.taxes_address = Web3.to_checksum_address(taxes_address)
        self.taxes_abi = load_abi('ATaxes.json')
        self.contract = self.client.web3.eth.contract(address=self.taxes_address, abi=self.taxes_abi)

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    def get_tax_rate(self, token: str, user: str) -> int:
        """Returns the tax rate in basis points for a token/user pair."""
        checksum_token = Web3.to_checksum_address(token)
        checksum_user = Web3.to_checksum_address(user)
        return self.contract.functions.getTaxRate(checksum_token, checksum_user).call()

    def get_current_surge_tax(self, token: str) -> int:
        """Returns the current surge tax for a token."""
        checksum_token = Web3.to_checksum_address(token)
        return self.contract.functions.getCurrentSurgeTax(checksum_token).call()

    def get_available_surge_quota(self, token: str) -> int:
        """Returns the available surge quota for a token."""
        checksum_token = Web3.to_checksum_address(token)
        return self.contract.functions.availableSurgeQuota(checksum_token).call()

    def get_base_tax_rates(self) -> dict:
        """Returns the base tax rates for all token types."""
        stasis = self.contract.functions._taxRateStasis().call()
        stable = self.contract.functions._taxRateStable().call()
        default = self.contract.functions._taxRateDefault().call()
        prediction = self.contract.functions._taxRatePrediction().call()
        return {
            'stasis': stasis,
            'stable': stable,
            'default': default,
            'prediction': prediction,
        }

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    def _sync_tx(self, tx_hash: str):
        """Sync tx to backend. Raises on failure."""
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        self.client.api.sync_transaction(tx_hash)

    def start_surge_tax(self, start_rate: int, end_rate: int, duration: int, token: str):
        """Start a decaying surge tax on a factory token. Only callable by the token's DEV.

        Args:
            start_rate: basis points (0-10000)
            end_rate: basis points (0-10000)
            duration: duration in seconds
        """
        func = self.contract.functions.startSurgeTax(start_rate, end_rate, duration, Web3.to_checksum_address(token))
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def end_surge_tax(self, token: str):
        """End an active surge tax early. Only callable by the token's DEV."""
        func = self.contract.functions.endSurgeTax(Web3.to_checksum_address(token))
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def add_dev_share(self, token: str, wallet: str, basis_points: int):
        """Add a developer revenue share wallet. Only callable by the token's DEV.

        Args:
            basis_points: basis points (0-10000)
        """
        func = self.contract.functions.addDevShare(
            Web3.to_checksum_address(token), Web3.to_checksum_address(wallet), basis_points
        )
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def remove_dev_share(self, token: str, wallet: str):
        """Remove a developer revenue share wallet. Only callable by the token's DEV."""
        func = self.contract.functions.removeDevShare(
            Web3.to_checksum_address(token), Web3.to_checksum_address(wallet)
        )
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result
