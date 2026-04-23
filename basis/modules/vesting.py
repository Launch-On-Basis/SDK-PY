import logging

from web3 import Web3
from .factory import load_abi

logger = logging.getLogger(__name__)


class VestingModule:
    def __init__(self, client, vesting_address: str):
        self.client = client
        self.vesting_address = Web3.to_checksum_address(vesting_address)
        self.vesting_abi = load_abi('A_VestingContract.json')
        self.loan_hub_abi = load_abi('ALOAN_HUB.json')
        self.erc20_abi = load_abi('IERC20.json')
        self._contract = self.client.web3.eth.contract(address=self.vesting_address, abi=self.vesting_abi)

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

    def _get_fee_amount(self) -> int:
        try:
            if not self._contract.functions.feeEnabled().call():
                return 0
            if self.client.account:
                if self._contract.functions.feeWhitelist(self.client.account.address).call():
                    return 0
            return self._contract.functions.feeAmount().call()
        except Exception:
            return 0

    def _sync_tx(self, tx_hash: str):
        """Sync tx to backend. Raises on failure."""
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        self.client.api.sync_transaction(tx_hash)

    def create_gradual_vesting(self, beneficiary: str, token: str, total_amount: int, start_time: int, duration_in_days: int, time_unit: int, memo: str, ecosystem: str):
        """Creates a gradual vesting. Auto-approves token and attaches fee.

        Args:
            total_amount: token amount in wei (18 decimals)
            start_time: Unix timestamp in seconds
            duration_in_days: integer, number of days
            time_unit: enum: 0=Second, 1=Minute, 2=Hour, 3=Day
        """
        self._approve_if_needed(token, self.vesting_address, total_amount)
        fee = self._get_fee_amount()
        func = self._contract.functions.createGradualVesting(
            Web3.to_checksum_address(beneficiary),
            Web3.to_checksum_address(token),
            total_amount, start_time, duration_in_days, time_unit, memo,
            Web3.to_checksum_address(ecosystem)
        )
        result = self.client.send_transaction(func, value=fee)
        self._sync_tx(result['hash'])
        return result

    def create_cliff_vesting(self, beneficiary: str, token: str, total_amount: int, unlock_time: int, memo: str, ecosystem: str):
        """Creates a cliff vesting. Auto-approves token and attaches fee.

        Args:
            total_amount: token amount in wei (18 decimals)
            unlock_time: Unix timestamp in seconds
        """
        self._approve_if_needed(token, self.vesting_address, total_amount)
        fee = self._get_fee_amount()
        func = self._contract.functions.createCliffVesting(
            Web3.to_checksum_address(beneficiary),
            Web3.to_checksum_address(token),
            total_amount, unlock_time, memo,
            Web3.to_checksum_address(ecosystem)
        )
        result = self.client.send_transaction(func, value=fee)
        self._sync_tx(result['hash'])
        return result

    def claim_tokens(self, vesting_id: int):
        func = self._contract.functions.claimTokens(vesting_id)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def take_loan_on_vesting(self, vesting_id: int):
        func = self._contract.functions.takeLoanOnVesting(vesting_id)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def repay_loan_on_vesting(self, vesting_id: int):
        """Repays a loan taken on a vesting schedule.

        Auto-approves the exact debt of the borrowed token to the vesting
        contract. Reads ``vestingSchedules(id).ecosystem`` and
        ``ecosystems(maintoken).mainpair`` to discover the borrow token
        (typically USDB but ecosystem-defined), then reads the loan's
        ``fullAmount`` from the loan hub. If the underlying loan is no
        longer active, no approval is performed -- the contract handles
        cleanup paths without a transferFrom.
        """
        if not self.client.account:
            raise ValueError("Stateful initialization (private_key) is required.")

        # 1. Find the active loan id; bail early if none.
        active_loan_id = self._contract.functions.getActiveLoan(vesting_id).call()
        if active_loan_id == 0:
            raise ValueError("No active loan on this vesting schedule.")

        # 2. Discover the borrow token via the schedule's ecosystem.
        schedule = self._contract.functions.vestingSchedules(vesting_id).call()
        ecosystem = schedule[3]  # creator=0, beneficiary=1, token=2, ecosystem=3
        eco = self._contract.functions.ecosystems(ecosystem).call()
        borrowed_token = eco[2]  # (maintoken, factory, mainpair)

        # 3. Read the actual debt from the loan hub (borrower-of-record
        #    is the vesting contract itself, not msg.sender).
        loan_hub_address = Web3.to_checksum_address(self._contract.functions.LOAN().call())
        loan_hub = self.client.web3.eth.contract(address=loan_hub_address, abi=self.loan_hub_abi)
        details = loan_hub.functions.getUserLoanDetails(self.vesting_address, active_loan_id).call()
        # FullLoanDetails tuple: fullAmount=index 7, active=index 12
        full_amount = details[7]
        is_active = details[12]

        # 4. Approve only if the contract will actually pull funds.
        if is_active and full_amount > 0:
            self._approve_if_needed(borrowed_token, self.vesting_address, full_amount)

        func = self._contract.functions.repayLoanOnVesting(vesting_id)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def get_vesting_details(self, vesting_id: int):
        return self._contract.functions.getVestingDetails(vesting_id).call()

    def get_claimable_amount(self, vesting_id: int):
        return self._contract.functions.getClaimableAmount(vesting_id).call()

    def batch_create_gradual_vesting(self, beneficiaries: list[str], token: str, total_amounts: list[int], user_memos: list[str], start_time: int, duration_in_days: int, time_unit: int, ecosystem: str):
        """Creates gradual vestings for multiple beneficiaries. Auto-approves sum of amounts and attaches fee.

        Args:
            total_amounts: list of token amounts in wei (18 decimals)
            start_time: Unix timestamp in seconds
            duration_in_days: integer, number of days
            time_unit: enum: 0=Second, 1=Minute, 2=Hour, 3=Day
        """
        checksum_beneficiaries = [Web3.to_checksum_address(b) for b in beneficiaries]
        checksum_token = Web3.to_checksum_address(token)
        checksum_ecosystem = Web3.to_checksum_address(ecosystem)
        total = sum(total_amounts)
        self._approve_if_needed(token, self.vesting_address, total)
        fee = self._get_fee_amount()
        func = self._contract.functions.batchCreateGradualVesting(
            checksum_beneficiaries, checksum_token, total_amounts, user_memos,
            start_time, duration_in_days, time_unit, checksum_ecosystem
        )
        result = self.client.send_transaction(func, value=fee)
        self._sync_tx(result['hash'])
        return result

    def batch_create_cliff_vesting(self, beneficiaries: list[str], token: str, total_amounts: list[int], unlock_time: int, user_memos: list[str], ecosystem: str):
        """Creates cliff vestings for multiple beneficiaries. Auto-approves sum of amounts and attaches fee.

        Args:
            total_amounts: list of token amounts in wei (18 decimals)
            unlock_time: Unix timestamp in seconds
        """
        checksum_beneficiaries = [Web3.to_checksum_address(b) for b in beneficiaries]
        checksum_token = Web3.to_checksum_address(token)
        checksum_ecosystem = Web3.to_checksum_address(ecosystem)
        total = sum(total_amounts)
        self._approve_if_needed(token, self.vesting_address, total)
        fee = self._get_fee_amount()
        func = self._contract.functions.batchCreateCliffVesting(
            checksum_beneficiaries, checksum_token, total_amounts, unlock_time,
            user_memos, checksum_ecosystem
        )
        result = self.client.send_transaction(func, value=fee)
        self._sync_tx(result['hash'])
        return result

    def change_beneficiary(self, vesting_id: int, new_beneficiary: str):
        """Changes the beneficiary of a vesting."""
        func = self._contract.functions.changeBeneficiary(vesting_id, Web3.to_checksum_address(new_beneficiary))
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def extend_vesting_period(self, vesting_id: int, additional_days: int):
        """Extends the vesting period by additional days.

        Args:
            additional_days: integer, number of days
        """
        func = self._contract.functions.extendVestingPeriod(vesting_id, additional_days)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def add_tokens_to_vesting(self, vesting_id: int, additional_amount: int):
        """Adds tokens to an existing vesting. Reads vesting details to get token, then approves.

        Args:
            additional_amount: token amount in wei (18 decimals)
        """
        details = self.get_vesting_details(vesting_id)
        token = details[2]  # token address from vesting details (creator=0, beneficiary=1, token=2)
        self._approve_if_needed(token, self.vesting_address, additional_amount)
        func = self._contract.functions.addTokensToVesting(vesting_id, additional_amount)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def transfer_creator_role(self, vesting_id: int, new_creator: str):
        """Transfers the creator role of a vesting to a new address."""
        func = self._contract.functions.transferCreatorRole(vesting_id, Web3.to_checksum_address(new_creator))
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def get_vested_amount(self, vesting_id: int) -> int:
        """Returns the total vested amount for a vesting schedule."""
        return self._contract.functions.getVestedAmount(vesting_id).call()

    def get_active_loan(self, vesting_id: int) -> int:
        """Returns the active loan ID for a vesting schedule."""
        return self._contract.functions.getActiveLoan(vesting_id).call()

    def get_token_vesting_ids(self, token: str, start_index: int, end_index: int) -> list:
        """Returns vesting IDs for a given token within a specified index range."""
        return self._contract.functions.getTokenVestingIds(
            Web3.to_checksum_address(token), start_index, end_index
        ).call()

    def get_vesting_details_batch(self, vesting_ids: list):
        """Returns vesting details for multiple vesting IDs in a single call."""
        return self._contract.functions.getVestingDetailsBatch(vesting_ids).call()

    def get_vesting_count(self) -> int:
        """Returns the total number of vesting schedules created."""
        return self._contract.functions.vestingCount().call()

    def get_vestings_by_beneficiary(self, beneficiary: str) -> list:
        """Returns all vesting IDs for a beneficiary."""
        return self._contract.functions.getVestingsByBeneficiary(Web3.to_checksum_address(beneficiary)).call()

    def get_vestings_by_creator(self, creator: str) -> list:
        """Returns all vesting IDs for a creator."""
        return self._contract.functions.getVestingsByCreator(Web3.to_checksum_address(creator)).call()
