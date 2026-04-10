import logging
from web3 import Web3
from .factory import load_abi

logger = logging.getLogger(__name__)


class StakingModule:
    def __init__(self, client, staking_address: str):
        self.client = client
        self.staking_address = Web3.to_checksum_address(staking_address)
        self.staking_abi = load_abi('AStasisVault.json')
        self.erc20_abi = load_abi('IERC20.json')
        self._contract = self.client.web3.eth.contract(address=self.staking_address, abi=self.staking_abi)

    def _approve_if_needed(self, token_address: str, spender: str, amount: int):
        if not self.client.account:
            raise ValueError("Wallet account is required for approval.")

        checksum_token = Web3.to_checksum_address(token_address)
        checksum_spender = Web3.to_checksum_address(spender)
        token_contract = self.client.web3.eth.contract(address=checksum_token, abi=self.erc20_abi)

        allowance = token_contract.functions.allowance(
            self.client.account.address, checksum_spender
        ).call()

        if allowance < amount:
            func = token_contract.functions.approve(checksum_spender, amount)
            self.client.send_transaction(func)

    def _sync_tx(self, tx_hash: str):
        """Sync tx to backend. Raises on failure."""
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        self.client.api.sync_transaction(tx_hash)

    def buy(self, amount: int):
        """Wraps STASIS (MAINTOKEN) into wSTASIS. Auto-approves.

        Args:
            amount: STASIS amount in wei (18 decimals)
        """
        self._approve_if_needed(self.client.main_token_address, self.staking_address, amount)
        func = self._contract.functions.buy(amount)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def sell(self, shares: int, claim_usdb: bool = False, min_usdb: int = 0):
        """Unwraps wSTASIS back to STASIS, optionally converting to USDB.

        Args:
            shares: wSTASIS shares in wei (18 decimals)
            min_usdb: minimum USDB output in wei (18 decimals)
        """
        func = self._contract.functions.sell(shares, claim_usdb, min_usdb)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def lock(self, shares: int):
        """Locks wSTASIS as collateral for borrowing. Auto-approves.

        Args:
            shares: wSTASIS shares in wei (18 decimals)
        """
        self._approve_if_needed(self.staking_address, self.staking_address, shares)
        func = self._contract.functions.lock(shares)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def unlock(self, shares: int):
        """Unlocks wSTASIS collateral.

        Args:
            shares: wSTASIS shares in wei (18 decimals)
        """
        func = self._contract.functions.unlock(shares)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def borrow(self, stasis_amount_to_borrow: int, days: int):
        """Pledges STASIS as collateral and borrows USDB against it.
        USDB received is collateral value minus fees.

        Args:
            stasis_amount_to_borrow: STASIS collateral in wei (18 decimals)
            days: integer, minimum 10
        """
        func = self._contract.functions.borrow(stasis_amount_to_borrow, days)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def repay(self):
        """Repays the active staking loan. Auto-approves USDB."""
        if not self.client.account:
            raise ValueError("Stateful initialization (private_key) is required.")
        usdb_contract = self.client.web3.eth.contract(
            address=Web3.to_checksum_address(self.client.usdb_address), abi=self.erc20_abi
        )
        balance = usdb_contract.functions.balanceOf(self.client.account.address).call()
        if balance > 0:
            self._approve_if_needed(self.client.usdb_address, self.staking_address, balance)
        func = self._contract.functions.repay()
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def extend_loan(self, days_to_add: int, pay_in_usdb: bool, refinance: bool):
        """Extends the active staking loan. Auto-approves USDB when pay_in_usdb is True.

        Args:
            days_to_add: integer, minimum 10
        """
        if pay_in_usdb and self.client.account:
            usdb_contract = self.client.web3.eth.contract(
                address=Web3.to_checksum_address(self.client.usdb_address), abi=self.erc20_abi
            )
            balance = usdb_contract.functions.balanceOf(self.client.account.address).call()
            if balance > 0:
                self._approve_if_needed(self.client.usdb_address, self.staking_address, balance)
        func = self._contract.functions.extendLoan(days_to_add, pay_in_usdb, refinance)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def get_user_stake_details(self, user: str):
        """Returns (liquidShares, lockedShares, totalShares, totalAssetValue)."""
        checksum_user = Web3.to_checksum_address(user)
        return self._contract.functions.getUserStakeDetails(checksum_user).call()

    def get_available_stasis(self, user: str) -> int:
        """Gets available STASIS (collateral value minus pledged)."""
        checksum_user = Web3.to_checksum_address(user)
        return self._contract.functions.getAvailableStasis(checksum_user).call()

    def convert_to_shares(self, assets: int) -> int:
        """Converts STASIS amount to wSTASIS shares.

        Args:
            assets: STASIS amount in wei (18 decimals)
        """
        return self._contract.functions.convertToShares(assets).call()

    def convert_to_assets(self, shares: int) -> int:
        """Converts wSTASIS shares to STASIS amount.

        Args:
            shares: wSTASIS shares in wei (18 decimals)
        """
        return self._contract.functions.convertToAssets(shares).call()

    def total_assets(self) -> int:
        """Returns total STASIS held by the vault (available + pledged)."""
        return self._contract.functions.totalAssets().call()

    def add_to_loan(self, additional_stasis_to_borrow: int):
        """Adds to the existing staking loan by borrowing more.

        Args:
            additional_stasis_to_borrow: STASIS collateral in wei (18 decimals)
        """
        func = self._contract.functions.addToLoan(additional_stasis_to_borrow)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def settle_liquidation(self):
        """Settles a liquidation on the staking position."""
        func = self._contract.functions.settleLiquidation()
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result
