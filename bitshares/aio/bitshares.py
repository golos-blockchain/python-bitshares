# -*- coding: utf-8 -*-
import logging

from datetime import datetime, timedelta

from graphenecommon.aio.chain import AbstractGrapheneChain

from bitsharesapi.aio.bitsharesnoderpc import BitSharesNodeRPC
from bitsharesbase import operations
from bitsharesbase.account import PublicKey
from bitsharesbase.asset_permissions import asset_permissions, toint

from .account import Account
from .amount import Amount
from .asset import Asset
from .committee import Committee
from ..exceptions import AccountExistsException, KeyAlreadyInStoreException
from .instance import set_shared_blockchain_instance, shared_blockchain_instance
from .price import Price
from ..storage import get_default_config_store
from .transactionbuilder import ProposalBuilder, TransactionBuilder
from .vesting import Vesting
from .wallet import Wallet
from .witness import Witness
from .worker import Worker
from .htlc import Htlc
from .market import Market
from ..bitshares import BitShares as SyncBitShares


log = logging.getLogger(__name__)

class GolosTransactionBuilder(TransactionBuilder):
    def add_required_fees(self, ops, asset_id="1.3.0"):
        return ops

class BitShares(AbstractGrapheneChain, SyncBitShares):
    """
    BitShares async client.

    This is an asyncio version of :class:`bitshares.BitShares`

    :param object loop: asyncio event loop


    Example usage:

    .. code-block:: python

        bitshares = BitShares(loop=loop)
        await bitshares.connect()
    """

    def define_classes(self):
        from .blockchainobject import BlockchainObject

        self.wallet_class = Wallet
        self.account_class = Account
        self.rpc_class = BitSharesNodeRPC
        self.default_key_store_app_name = "bitshares"
        self.proposalbuilder_class = ProposalBuilder
        self.transactionbuilder_class = GolosTransactionBuilder
        self.blockchainobject_class = BlockchainObject

    # -------------------------------------------------------------------------
    # Simple Transfer
    # -------------------------------------------------------------------------
    async def transfer(self, to, amount, asset, memo="", account=None, **kwargs):
        """
        Transfer an asset to another account.

        :param str to: Recipient
        :param float amount: Amount to transfer
        :param str asset: Asset to transfer
        :param str memo: (optional) Memo, may begin with `#` for encrypted
            messaging
        :param str account: (optional) the source account for the transfer
            if not ``default_account``
        """
        from .memo import Memo

        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")

        account = await Account(account, blockchain_instance=self)
        amount = await Amount(amount, asset, blockchain_instance=self)
        to = await Account(to, blockchain_instance=self)

        memoObj = await Memo(
            from_account=account, to_account=to, blockchain_instance=self
        )

        op = operations.Transfer(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "from": account["id"],
                "to": to["id"],
                "amount": {
                    "amount": int(amount),
                    "asset_id": (await amount.asset)["id"],
                },
                "memo": memoObj.encrypt(memo),
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account, "active", **kwargs)

    # -------------------------------------------------------------------------
    # Account related calls
    # -------------------------------------------------------------------------
    async def create_account(
        self,
        account_name,
        registrar=None,
        referrer="1.2.35641",
        referrer_percent=50,
        owner_key=None,
        active_key=None,
        memo_key=None,
        owner_account=None,
        active_account=None,
        password=None,
        additional_owner_keys=None,
        additional_active_keys=None,
        additional_owner_accounts=None,
        additional_active_accounts=None,
        proxy_account="proxy-to-self",
        storekeys=True,
        **kwargs
    ):
        """
        Create new account on BitShares.

        The brainkey/password can be used to recover all generated keys
        (see `bitsharesbase.account` for more details.

        By default, this call will use ``default_account`` to
        register a new name ``account_name`` with all keys being
        derived from a new brain key that will be returned. The
        corresponding keys will automatically be installed in the
        wallet.

        .. warning:: Don't call this method unless you know what
                      you are doing! Be sure to understand what this
                      method does and where to find the private keys
                      for your account.

        .. note:: Please note that this imports private keys
                  (if password is present) into the wallet by
                  default. However, it **does not import the owner
                  key** for security reasons. Do NOT expect to be
                  able to recover it from the wallet if you lose
                  your password!

        :param str account_name: (**required**) new account name
        :param str registrar: which account should pay the registration fee
                            (defaults to ``default_account``)
        :param str owner_key: Main owner key
        :param str active_key: Main active key
        :param str memo_key: Main memo_key
        :param str password: Alternatively to providing keys, one
                             can provide a password from which the
                             keys will be derived
        :param array additional_owner_keys:  Additional owner public keys
        :param array additional_active_keys: Additional active public keys
        :param array additional_owner_accounts: Additional owner account
            names
        :param array additional_active_accounts: Additional acctive account
            names
        :param bool storekeys: Store new keys in the wallet (default:
            ``True``)
        :raises AccountExistsException: if the account already exists on
            the blockchain
        """
        if not registrar and self.config["default_account"]:
            registrar = self.config["default_account"]
        if not registrar:
            raise ValueError(
                "Not registrar account given. Define it with "
                + "registrar=x, or set the default_account using uptick"
            )
        if password and (owner_key or active_key or memo_key):
            raise ValueError("You cannot use 'password' AND provide keys!")

        if additional_owner_keys is None:
            additional_owner_keys = []
        if additional_active_keys is None:
            additional_active_keys = []
        if additional_owner_accounts is None:
            additional_owner_accounts = []
        if additional_active_accounts is None:
            additional_active_accounts = []

        try:
            await Account(account_name, blockchain_instance=self)
            raise AccountExistsException
        except Exception:
            pass

        referrer = await Account(referrer, blockchain_instance=self)
        registrar = await Account(registrar, blockchain_instance=self)

        " Generate new keys from password"
        from bitsharesbase.account import PasswordKey, PublicKey

        owner_key_authority = []
        active_key_authority = []
        owner_accounts_authority = []
        active_accounts_authority = []

        if password:
            active_key = PasswordKey(account_name, password, role="active")
            owner_key = PasswordKey(account_name, password, role="owner")
            memo_key = PasswordKey(account_name, password, role="memo")
            active_pubkey = active_key.get_public_key()
            owner_pubkey = owner_key.get_public_key()
            memo_pubkey = memo_key.get_public_key()
            active_privkey = active_key.get_private_key()
            # owner_privkey   = owner_key.get_private_key()
            memo_privkey = memo_key.get_private_key()
            # store private keys
            if storekeys:
                self._store_keys(active_privkey, memo_privkey)
            owner_key_authority = [[format(owner_pubkey, self.prefix), 1]]
            active_key_authority = [[format(active_pubkey, self.prefix), 1]]
            memo = format(memo_pubkey, self.prefix)
        elif owner_key and active_key and memo_key:
            active_pubkey = PublicKey(active_key, prefix=self.prefix)
            owner_pubkey = PublicKey(owner_key, prefix=self.prefix)
            memo_pubkey = PublicKey(memo_key, prefix=self.prefix)
            owner_key_authority = [[format(owner_pubkey, self.prefix), 1]]
            active_key_authority = [[format(active_pubkey, self.prefix), 1]]
            memo = format(memo_pubkey, self.prefix)
        elif owner_account and active_account and memo_key:
            memo_pubkey = PublicKey(memo_key, prefix=self.prefix)
            memo = format(memo_pubkey, self.prefix)
            owner_account = await Account(owner_account, blockchain_instance=self)
            active_account = await Account(active_account, blockchain_instance=self)
            owner_accounts_authority = [[owner_account["id"], 1]]
            active_accounts_authority = [[active_account["id"], 1]]
        else:
            raise ValueError(
                "Call incomplete! Provide either a password, owner/active public keys "
                "or owner/active accounts + memo key!"
            )

        # additional authorities
        for k in additional_owner_keys:
            PublicKey(k, prefix=self.prefix)
            owner_key_authority.append([k, 1])
        for k in additional_active_keys:
            PublicKey(k, prefix=self.prefix)
            active_key_authority.append([k, 1])
        for k in additional_owner_accounts:
            addaccount = Account(k, blockchain_instance=self)
            owner_accounts_authority.append([addaccount["id"], 1])
        for k in additional_active_accounts:
            addaccount = Account(k, blockchain_instance=self)
            active_accounts_authority.append([addaccount["id"], 1])

        # voting account
        voting_account = await Account(
            proxy_account or "proxy-to-self", blockchain_instance=self
        )

        op = {
            "fee": {"amount": 0, "asset_id": "1.3.0"},
            "registrar": registrar["id"],
            "referrer": referrer["id"],
            "referrer_percent": int(referrer_percent * 100),
            "name": account_name,
            "owner": {
                "account_auths": owner_accounts_authority,
                "key_auths": owner_key_authority,
                "address_auths": [],
                "weight_threshold": 1,
            },
            "active": {
                "account_auths": active_accounts_authority,
                "key_auths": active_key_authority,
                "address_auths": [],
                "weight_threshold": 1,
            },
            "options": {
                "memo_key": memo,
                "voting_account": voting_account["id"],
                "num_witness": 0,
                "num_committee": 0,
                "votes": [],
                "extensions": [],
            },
            "extensions": {},
            "prefix": self.prefix,
        }
        op = operations.Account_create(**op)
        return await self.finalizeOp(op, registrar, "active", **kwargs)

    async def upgrade_account(self, account=None, **kwargs):
        """
        Upgrade an account to Lifetime membership.

        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        op = operations.Account_upgrade(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account_to_upgrade": account["id"],
                "upgrade_to_lifetime_member": True,
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def allow(
        self,
        foreign,
        weight=None,
        permission="active",
        account=None,
        threshold=None,
        **kwargs
    ):
        """
        Give additional access to an account by some other public key or account.

        :param str foreign: The foreign account that will obtain access
        :param int weight: (optional) The weight to use. If not
            define, the threshold will be used. If the weight is
            smaller than the threshold, additional signatures will
            be required. (defaults to threshold)
        :param str permission: (optional) The actual permission to
            modify (defaults to ``active``)
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        :param int threshold: The threshold that needs to be reached
            by signatures to be able to interact
        """
        from copy import deepcopy

        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")

        if permission not in ["owner", "active"]:
            raise ValueError("Permission needs to be either 'owner', or 'active")
        account = await Account(account, blockchain_instance=self)

        if not weight:
            weight = account[permission]["weight_threshold"]

        authority = deepcopy(account[permission])
        try:
            pubkey = PublicKey(foreign, prefix=self.prefix)
            authority["key_auths"].append([str(pubkey), weight])
        except Exception:
            try:
                foreign_account = await Account(foreign, blockchain_instance=self)
                authority["account_auths"].append([foreign_account["id"], weight])
            except Exception:
                raise ValueError("Unknown foreign account or invalid public key")
        if threshold:
            authority["weight_threshold"] = threshold
            self._test_weights_treshold(authority)

        op = operations.Account_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account": account["id"],
                permission: authority,
                "extensions": {},
                "prefix": self.prefix,
            }
        )
        if permission == "owner":
            return await self.finalizeOp(op, account["name"], "owner", **kwargs)
        else:
            return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def disallow(
        self, foreign, permission="active", account=None, threshold=None, **kwargs
    ):
        """
        Remove additional access to an account by some other public key or account.

        :param str foreign: The foreign account that will obtain access
        :param str permission: (optional) The actual permission to
            modify (defaults to ``active``)
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        :param int threshold: The threshold that needs to be reached
            by signatures to be able to interact
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")

        if permission not in ["owner", "active"]:
            raise ValueError("Permission needs to be either 'owner', or 'active")
        account = await Account(account, blockchain_instance=self)
        authority = account[permission]

        try:
            pubkey = PublicKey(foreign, prefix=self.prefix)
            affected_items = list(
                filter(lambda x: x[0] == str(pubkey), authority["key_auths"])
            )
            authority["key_auths"] = list(
                filter(lambda x: x[0] != str(pubkey), authority["key_auths"])
            )
        except Exception:
            try:
                foreign_account = await Account(foreign, blockchain_instance=self)
                affected_items = list(
                    filter(
                        lambda x: x[0] == foreign_account["id"],
                        authority["account_auths"],
                    )
                )
                authority["account_auths"] = list(
                    filter(
                        lambda x: x[0] != foreign_account["id"],
                        authority["account_auths"],
                    )
                )
            except Exception:
                raise ValueError("Unknown foreign account or unvalid public key")

        if not affected_items:
            raise ValueError("Changes nothing!")
        removed_weight = affected_items[0][1]

        # Define threshold
        if threshold:
            authority["weight_threshold"] = threshold

        # Correct threshold (at most by the amount removed from the
        # authority)
        try:
            self._test_weights_treshold(authority)
        except Exception:
            log.critical(
                "The account's threshold will be reduced by %d" % (removed_weight)
            )
            authority["weight_threshold"] -= removed_weight
            self._test_weights_treshold(authority)

        op = operations.Account_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account": account["id"],
                permission: authority,
                "extensions": {},
            }
        )
        if permission == "owner":
            return await self.finalizeOp(op, account["name"], "owner", **kwargs)
        else:
            return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def update_memo_key(self, key, account=None, **kwargs):
        """
        Update an account's memo public key.

        This method does **not** add any private keys to your
        wallet but merely changes the memo public key.

        :param str key: New memo public key
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")

        PublicKey(key, prefix=self.prefix)

        account = await Account(account, blockchain_instance=self)
        account["options"]["memo_key"] = key
        op = operations.Account_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account": account["id"],
                "new_options": account["options"],
                "extensions": {},
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    # -------------------------------------------------------------------------
    #  Approval and Disapproval of witnesses, workers, committee, and proposals
    # -------------------------------------------------------------------------
    async def approvewitness(self, witnesses, account=None, **kwargs):
        """
        Approve a witness.

        :param list witnesses: list of Witness name or id
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        options = account["options"]

        if not isinstance(witnesses, (list, set, tuple)):
            witnesses = {witnesses}

        for witness in witnesses:
            witness = await Witness(witness, blockchain_instance=self)
            options["votes"].append(witness["vote_id"])

        options["votes"] = list(set(options["votes"]))
        options["num_witness"] = len(
            list(filter(lambda x: float(x.split(":")[0]) == 1, options["votes"]))
        )
        options["voting_account"] = "1.2.5"  # Account("proxy-to-self")["id"]

        op = operations.Account_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account": account["id"],
                "new_options": options,
                "extensions": {},
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def disapprovewitness(self, witnesses, account=None, **kwargs):
        """
        Disapprove a witness.

        :param list witnesses: list of Witness name or id
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        options = account["options"]

        if not isinstance(witnesses, (list, set, tuple)):
            witnesses = {witnesses}

        for witness in witnesses:
            witness = await Witness(witness, blockchain_instance=self)
            if witness["vote_id"] in options["votes"]:
                options["votes"].remove(witness["vote_id"])

        options["votes"] = list(set(options["votes"]))
        options["num_witness"] = len(
            list(filter(lambda x: float(x.split(":")[0]) == 1, options["votes"]))
        )
        options["voting_account"] = "1.2.5"  # Account("proxy-to-self")["id"]

        op = operations.Account_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account": account["id"],
                "new_options": options,
                "extensions": {},
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def approvecommittee(self, committees, account=None, **kwargs):
        """
        Approve a committee.

        :param list committees: list of committee member name or id
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        options = account["options"]

        if not isinstance(committees, (list, set, tuple)):
            committees = {committees}

        for committee in committees:
            committee = await Committee(committee, blockchain_instance=self)
            options["votes"].append(committee["vote_id"])

        options["votes"] = list(set(options["votes"]))
        options["num_committee"] = len(
            list(filter(lambda x: float(x.split(":")[0]) == 0, options["votes"]))
        )
        options["voting_account"] = "1.2.5"  # Account("proxy-to-self")["id"]

        op = operations.Account_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account": account["id"],
                "new_options": options,
                "extensions": {},
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def disapprovecommittee(self, committees, account=None, **kwargs):
        """
        Disapprove a committee.

        :param list committees: list of committee name or id
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        options = account["options"]

        if not isinstance(committees, (list, set, tuple)):
            committees = {committees}

        for committee in committees:
            committee = await Committee(committee, blockchain_instance=self)
            if committee["vote_id"] in options["votes"]:
                options["votes"].remove(committee["vote_id"])

        options["votes"] = list(set(options["votes"]))
        options["num_committee"] = len(
            list(filter(lambda x: float(x.split(":")[0]) == 0, options["votes"]))
        )
        options["voting_account"] = "1.2.5"  # Account("proxy-to-self")["id"]

        op = operations.Account_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account": account["id"],
                "new_options": options,
                "extensions": {},
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def approveproposal(
        self, proposal_ids, account=None, approver=None, **kwargs
    ):
        """
        Approve Proposal.

        :param list proposal_id: Ids of the proposals
        :param str appprover: The account or key to use for approval
            (defaults to ``account``)
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        from .proposal import Proposal

        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        is_key = approver and approver[:3] == self.prefix
        if not approver and not is_key:
            approver = account
        elif approver and not is_key:
            approver = await Account(approver, blockchain_instance=self)
        else:
            approver = PublicKey(approver)

        if not isinstance(proposal_ids, (list, set, tuple)):
            proposal_ids = {proposal_ids}

        op = []
        for proposal_id in proposal_ids:
            proposal = await Proposal(proposal_id, blockchain_instance=self)
            update_dict = {
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "fee_paying_account": account["id"],
                "proposal": proposal["id"],
                "prefix": self.prefix,
            }
            if is_key:
                update_dict.update({"key_approvals_to_add": [str(approver)]})
            else:
                update_dict.update({"active_approvals_to_add": [approver["id"]]})
            op.append(operations.Proposal_update(**update_dict))
        if is_key:
            await self.txbuffer.appendSigner(approver, "active")
            return await self.finalizeOp(op, account["name"], "active", **kwargs)
        return await self.finalizeOp(op, approver, "active", **kwargs)

    async def disapproveproposal(
        self, proposal_ids, account=None, approver=None, **kwargs
    ):
        """
        Disapprove Proposal.

        :param list proposal_ids: Ids of the proposals
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        from .proposal import Proposal

        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        if not approver:
            approver = account
        else:
            approver = await Account(approver, blockchain_instance=self)

        if not isinstance(proposal_ids, (list, set, tuple)):
            proposal_ids = {proposal_ids}

        op = []
        for proposal_id in proposal_ids:
            proposal = await Proposal(proposal_id, blockchain_instance=self)
            op.append(
                operations.Proposal_update(
                    **{
                        "fee": {"amount": 0, "asset_id": "1.3.0"},
                        "fee_paying_account": account["id"],
                        "proposal": proposal["id"],
                        "active_approvals_to_remove": [approver["id"]],
                        "prefix": self.prefix,
                    }
                )
            )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def approveworker(self, workers, account=None, **kwargs):
        """
        Approve a worker.

        :param list workers: list of worker member name or id
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        options = account["options"]

        if not isinstance(workers, (list, set, tuple)):
            workers = {workers}

        for worker in workers:
            worker = await Worker(worker, blockchain_instance=self)
            options["votes"].append(worker["vote_for"])
        options["votes"] = list(set(options["votes"]))
        options["voting_account"] = "1.2.5"  # Account("proxy-to-self")["id"]

        op = operations.Account_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account": account["id"],
                "new_options": options,
                "extensions": {},
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def disapproveworker(self, workers, account=None, **kwargs):
        """
        Disapprove a worker.

        :param list workers: list of worker name or id
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        options = account["options"]

        if not isinstance(workers, (list, set, tuple)):
            workers = {workers}

        for worker in workers:
            worker = await Worker(worker, blockchain_instance=self)
            if worker["vote_for"] in options["votes"]:
                options["votes"].remove(worker["vote_for"])
        options["votes"] = list(set(options["votes"]))
        options["voting_account"] = "1.2.5"  # Account("proxy-to-self")["id"]

        op = operations.Account_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account": account["id"],
                "new_options": options,
                "extensions": {},
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def unset_proxy(self, account=None, **kwargs):
        """Unset the proxy account to start voting yourself."""
        return await self.set_proxy("proxy-to-self", account=account, **kwargs)

    async def set_proxy(self, proxy_account, account=None, **kwargs):
        """
        Set a specific proxy for account.

        :param bitshares.account.Account proxy_account: Account to be
                proxied
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        proxy = await Account(proxy_account, blockchain_instance=self)
        options = account["options"]
        options["voting_account"] = proxy["id"]

        op = operations.Account_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account": account["id"],
                "new_options": options,
                "extensions": {},
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def cancel(self, orderNumbers, account=None, **kwargs):
        """
        Cancels an order you have placed in a given market. Requires only the
        "orderNumbers". An order number takes the form ``1.7.xxx``.

        :param str orderNumbers: The Order Object ide of the form
            ``1.7.xxxx``
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, full=False, blockchain_instance=self)

        if not isinstance(orderNumbers, (list, set, tuple)):
            orderNumbers = {orderNumbers}

        op = []
        for order in orderNumbers:
            op.append(
                operations.Limit_order_cancel(
                    **{
                        "fee": {"amount": 0, "asset_id": "1.3.0"},
                        "fee_paying_account": account["id"],
                        "order": order,
                        "extensions": [],
                        "prefix": self.prefix,
                    }
                )
            )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def vesting_balance_withdraw(
        self, vesting_id, amount=None, account=None, **kwargs
    ):
        """
        Withdraw vesting balance.

        :param str vesting_id: Id of the vesting object
        :param bitshares.amount.Amount Amount: to withdraw ("all" if not
            provided")
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)

        if not amount:
            obj = await Vesting(vesting_id, blockchain_instance=self)
            amount = await obj.claimable

        op = operations.Vesting_balance_withdraw(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "vesting_balance": vesting_id,
                "owner": account["id"],
                "amount": {"amount": int(amount), "asset_id": amount["asset"]["id"]},
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active")

    async def publish_price_feed(
        self, symbol, settlement_price, cer=None, mssr=110, mcr=200, account=None
    ):
        """
        Publish a price feed for a market-pegged asset.

        :param str symbol: Symbol of the asset to publish feed for
        :param bitshares.price.Price settlement_price: Price for settlement
        :param bitshares.price.Price cer: Core exchange Rate (default
            ``settlement_price + 5%``)
        :param float mssr: Percentage for max short squeeze ratio (default:
            110%)
        :param float mcr: Percentage for maintenance collateral ratio
            (default: 200%)
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)

        .. note:: The ``account`` needs to be allowed to produce a
                  price feed for ``symbol``. For witness produced
                  feeds this means ``account`` is a witness account!
        """
        assert mcr > 100
        assert mssr > 100
        assert isinstance(
            settlement_price, Price
        ), "settlement_price needs to be instance of `bitshares.price.Price`!"
        assert cer is None or isinstance(
            cer, Price
        ), "cer needs to be instance of `bitshares.price.Price`!"
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        asset = await Asset(symbol, blockchain_instance=self, full=True)
        backing_asset = asset["bitasset_data"]["options"]["short_backing_asset"]
        assert (
            asset["id"] == settlement_price["base"]["asset"]["id"]
            or asset["id"] == settlement_price["quote"]["asset"]["id"]
        ), "Price needs to contain the asset of the symbol you'd like to produce a feed for!"
        assert asset.is_bitasset, "Symbol needs to be a bitasset!"
        assert (
            settlement_price["base"]["asset"]["id"] == backing_asset
            or settlement_price["quote"]["asset"]["id"] == backing_asset
        ), "The Price needs to be relative to the backing collateral!"

        settlement_price = await settlement_price.as_base(symbol)

        if cer:
            cer = await cer.as_base(symbol)
            if cer["quote"]["asset"]["id"] != "1.3.0":
                raise ValueError("CER must be defined against core asset '1.3.0'")
        else:
            if settlement_price["quote"]["asset"]["id"] != "1.3.0":
                raise ValueError(
                    "CER must be manually provided because it relates to core asset '1.3.0'"
                )
            cer = float(await settlement_price.as_quote(symbol)) * 0.95
            cer = await Price(
                cer,
                quote=await Asset(symbol),
                base=await Asset("1.3.0"),
                blockchain_instance=self,
            )

        op = operations.Asset_publish_feed(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "publisher": account["id"],
                "asset_id": asset["id"],
                "feed": {
                    "settlement_price": (await settlement_price.as_base(symbol)).json(),
                    "core_exchange_rate": (await cer.as_base(symbol)).json(),
                    "maximum_short_squeeze_ratio": int(mssr * 10),
                    "maintenance_collateral_ratio": int(mcr * 10),
                },
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active")

    async def update_cer(self, symbol, cer, account=None):
        """
        Update the Core Exchange Rate (CER) of an asset.

        :param str symbol: Symbol of the asset to publish feed for
        :param bitshares.price.Price cer: Core exchange Rate
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        assert isinstance(
            cer, Price
        ), "cer needs to be instance of `bitshares.price.Price`!"
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        asset = await Asset(symbol, blockchain_instance=self, full=True)
        assert (
            asset["id"] == cer["base"]["asset"]["id"]
            or asset["id"] == cer["quote"]["asset"]["id"]
        ), "Price needs to contain the asset of the symbol you'd like to produce a feed for!"

        cer = await cer.as_base(symbol)
        if cer["quote"]["asset"]["id"] != "1.3.0":
            raise ValueError("CER must be defined against core asset '1.3.0'")

        options = asset["options"]
        options.update({"core_exchange_rate": (await cer.as_base(symbol)).json()})
        op = operations.Asset_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "issuer": account["id"],
                "asset_to_update": asset["id"],
                "new_options": options,
                "extensions": [],
                "prefix": self.prefix,
            }
        )
        return await self.finalizeOp(op, account["name"], "active")

    async def update_witness(self, witness_identifier, url=None, key=None, **kwargs):
        """
        Upgrade a witness account.

        :param str witness_identifier: Identifier for the witness
        :param str url: New URL for the witness
        :param str key: Public Key for the signing
        """
        witness = await Witness(witness_identifier)
        account = await witness.account
        op = operations.Witness_update(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "prefix": self.prefix,
                "witness": witness["id"],
                "witness_account": account["id"],
                "new_url": url,
                "new_signing_key": key,
            }
        )
        return await self.finalizeOp(op, account["name"], "active", **kwargs)

    async def reserve(self, amount, account=None, **kwargs):
        """
        Reserve/Burn an amount of this shares.

        This removes the shares from the supply

        :param bitshares.amount.Amount amount: The amount to be burned.
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        assert isinstance(amount, Amount)
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        op = operations.Asset_reserve(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "payer": account["id"],
                "amount_to_reserve": {
                    "amount": int(amount),
                    "asset_id": amount["asset"]["id"],
                },
                "extensions": [],
            }
        )
        return await self.finalizeOp(op, account, "active", **kwargs)

    async def create_asset(
        self,
        symbol,
        precision,
        max_supply,
        description="",
        is_bitasset=False,
        is_prediction_market=False,
        market_fee_percent=0,
        max_market_fee=None,
        permissions=None,
        flags=None,
        whitelist_authorities=None,
        blacklist_authorities=None,
        whitelist_markets=None,
        blacklist_markets=None,
        bitasset_options=None,
        account=None,
        **kwargs
    ):
        """
        Create a new asset.

        :param str symbol: Asset symbol
        :param int precision: Asset precision
        :param int max_supply: Asset max supply
        :param str description: (optional) Asset description
        :param bool is_bitasset: (optional) True = bitasset, False = UIA (default:
            False)
        :param bool is_prediction_market: (optional) True: PD, False = plain
            smartcoin (default: False)
        :param float market_fee_percent: (optional) Charge market fee (0-100)
            (default: 0)
        :param float max_market_fee: (optional) Absolute amount of max
            market fee, value of this option should be a whole number (default:
            same as max_supply)
        :param dict permissions: (optional) Asset permissions
        :param dict flags: (optional) Enabled asset flags
        :param list whitelist_authorities: (optional) List of accounts that
            serve as whitelist authorities
        :param list blacklist_authorities: (optional) List of accounts that
            serve as blacklist authorities
        :param list whitelist_markets: (optional) List of assets to allow
            trading with
        :param list blacklist_markets: (optional) List of assets to prevent
            trading with
        :param dict bitasset_options: (optional) Bitasset settings
        :param str account: (optional) the issuer account
            to (defaults to ``default_account``)
        """

        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)

        if permissions is None:
            permissions = {
                "charge_market_fee": True,
                "white_list": True,
                "override_authority": True,
                "transfer_restricted": True,
                "disable_force_settle": True,
                "global_settle": True,
                "disable_confidential": True,
                "witness_fed_asset": True,
                "committee_fed_asset": True,
            }
        if flags is None:
            flags = {
                "charge_market_fee": False,
                "white_list": False,
                "override_authority": False,
                "transfer_restricted": False,
                "disable_force_settle": False,
                "global_settle": False,
                "disable_confidential": False,
                "witness_fed_asset": False,
                "committee_fed_asset": False,
            }
        if whitelist_authorities is None:
            whitelist_authorities = []
        if blacklist_authorities is None:
            blacklist_authorities = []
        if whitelist_markets is None:
            whitelist_markets = []
        if blacklist_markets is None:
            blacklist_markets = []
        if bitasset_options is None:
            bitasset_options = {
                "feed_lifetime_sec": 86400,
                "minimum_feeds": 7,
                "force_settlement_delay_sec": 86400,
                "force_settlement_offset_percent": 100,
                "maximum_force_settlement_volume": 50,
                "short_backing_asset": "1.3.0",
                "extensions": [],
            }

        if not is_bitasset:
            # Turn off bitasset-specific options
            permissions["disable_force_settle"] = False
            permissions["global_settle"] = False
            permissions["witness_fed_asset"] = False
            permissions["committee_fed_asset"] = False
            bitasset_options = None

        assert set(permissions.keys()).issubset(
            asset_permissions.keys()
        ), "unknown permission"
        assert set(flags.keys()).issubset(asset_permissions.keys()), "unknown flag"
        # Transform permissions and flags into bitmask
        permissions_int = toint(permissions)
        flags_int = toint(flags)

        if not max_market_fee:
            max_market_fee = max_supply

        op = operations.Asset_create(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "issuer": account["id"],
                "symbol": symbol,
                "precision": precision,
                "common_options": {
                    "max_supply": int(max_supply * 10 ** precision),
                    "market_fee_percent": int(market_fee_percent * 100),
                    "max_market_fee": int(max_market_fee * 10 ** precision),
                    "issuer_permissions": permissions_int,
                    "flags": flags_int,
                    "core_exchange_rate": {
                        "base": {"amount": 1, "asset_id": "1.3.0"},
                        "quote": {"amount": 1, "asset_id": "1.3.1"},
                    },
                    "whitelist_authorities": [
                        await Account(a, blockchain_instance=self)["id"]
                        for a in whitelist_authorities
                    ],
                    "blacklist_authorities": [
                        await Account(a, blockchain_instance=self)["id"]
                        for a in blacklist_authorities
                    ],
                    "whitelist_markets": [
                        await Asset(a, blockchain_instance=self)["id"]
                        for a in whitelist_markets
                    ],
                    "blacklist_markets": [
                        await Asset(a, blockchain_instance=self)["id"]
                        for a in blacklist_markets
                    ],
                    "description": description,
                    "extensions": [],
                },
                "bitasset_opts": bitasset_options,
                "is_prediction_market": is_prediction_market,
                "extensions": [],
            }
        )

        return await self.finalizeOp(op, account, "active", **kwargs)

    async def create_worker(
        self,
        name,
        daily_pay,
        end,
        url="",
        begin=None,
        payment_type="vesting",
        pay_vesting_period_days=0,
        account=None,
        **kwargs
    ):
        """
        Create a worker.

        This removes the shares from the supply

        **Required**

        :param str name: Name of the worker
        :param bitshares.amount.Amount daily_pay: The amount to be paid
            daily
        :param datetime end: Date/time of end of the worker

        **Optional**

        :param str url: URL to read more about the worker
        :param datetime begin: Date/time of begin of the worker
        :param string payment_type: ["burn", "refund", "vesting"] (default:
            "vesting")
        :param int pay_vesting_period_days: Days of vesting (default: 0)
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        from bitsharesbase.transactions import timeformat

        assert isinstance(daily_pay, Amount)
        assert daily_pay["asset"]["id"] == "1.3.0"
        if not begin:
            begin = datetime.utcnow() + timedelta(seconds=30)
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)

        if payment_type == "refund":
            initializer = [0, {}]
        elif payment_type == "vesting":
            initializer = [1, {"pay_vesting_period_days": pay_vesting_period_days}]
        elif payment_type == "burn":
            initializer = [2, {}]
        else:
            raise ValueError('payment_type not in ["burn", "refund", "vesting"]')

        op = operations.Worker_create(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "owner": account["id"],
                "work_begin_date": begin.strftime(timeformat),
                "work_end_date": end.strftime(timeformat),
                "daily_pay": int(daily_pay),
                "name": name,
                "url": url,
                "initializer": initializer,
            }
        )
        return await self.finalizeOp(op, account, "active", **kwargs)

    async def fund_fee_pool(self, symbol, amount, account=None, **kwargs):
        """
        Fund the fee pool of an asset.

        :param str symbol: The symbol to fund the fee pool of
        :param float amount: The amount to be burned.
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        assert isinstance(amount, float)
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        amount = await Amount(amount, "1.3.0", blockchain_instance=self)
        account = await Account(account, blockchain_instance=self)
        asset = await Asset(symbol, blockchain_instance=self)
        op = operations.Asset_fund_fee_pool(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "from_account": account["id"],
                "asset_id": asset["id"],
                "amount": int(amount),
                "extensions": [],
            }
        )
        return await self.finalizeOp(op, account, "active", **kwargs)

    async def create_committee_member(self, url="", account=None, **kwargs):
        """
        Create a committee member.

        :param str url: URL to read more about the worker
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)

        op = operations.Committee_member_create(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "committee_member_account": account["id"],
                "url": url,
            }
        )
        return await self.finalizeOp(op, account, "active", **kwargs)

    async def account_whitelist(
        self, account_to_whitelist, lists=None, account=None, **kwargs
    ):
        """
        Account whitelisting.

        :param str account_to_whitelist: The account we want to add
            to either the white- or the blacklist
        :param set lists: (defaults to ``('white')``). Lists the
            user should be added to. Either empty set, 'black',
            'white' or both.
        :param str account: (optional) the account to allow access
            to (defaults to ``default_account``)
        """
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        account_to_list = await Account(account_to_whitelist, blockchain_instance=self)

        if lists is None:
            lists = ["white"]

        if not isinstance(lists, (set, list)):
            raise ValueError('"lists" must be of instance list()')

        new_listing = operations.Account_whitelist.no_listing
        if "white" in lists:
            new_listing += operations.Account_whitelist.white_listed
        if "black" in lists:
            new_listing += operations.Account_whitelist.black_listed

        op = operations.Account_whitelist(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "authorizing_account": account["id"],
                "account_to_list": account_to_list["id"],
                "new_listing": new_listing,
            }
        )
        return await self.finalizeOp(op, account, "active", **kwargs)

    async def bid_collateral(
        self, additional_collateral, debt_covered, account=None, **kwargs
    ):
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)

        if not isinstance(additional_collateral, (Amount)):
            raise ValueError("additional_collateral must be of type Amount")

        if not isinstance(debt_covered, (Amount)):
            raise ValueError("debt_covered must be of type Amount")

        op = operations.Bid_collateral(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "bidder": account["id"],
                "additional_collateral": additional_collateral.json(),
                "debt_covered": debt_covered.json(),
                "extensions": [],
            }
        )
        return await self.finalizeOp(op, account, "active", **kwargs)

    async def asset_settle(self, amount, account=None, **kwargs):
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)

        if not isinstance(amount, (Amount)):
            raise ValueError("'amount' must be of type Amount")

        op = operations.Asset_settle(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "account": account["id"],
                "amount": amount.json(),
                "extensions": [],
            }
        )
        return await self.finalizeOp(op, account, "active", **kwargs)

    async def htlc_create(
        self,
        amount,
        to,
        preimage,
        hash_type="ripemd160",
        account=None,
        expiration=60 * 60,
        **kwargs
    ):
        import hashlib
        from binascii import hexlify
        from graphenebase.base58 import ripemd160

        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            raise ValueError("You need to provide an account")
        account = await Account(account, blockchain_instance=self)
        to = await Account(to, blockchain_instance=self)

        if not isinstance(amount, (Amount)):
            raise ValueError("'amount' must be of type Amount")

        if hash_type == "ripemd160":
            preimage_type = 0
            preimage_hash = hexlify(
                ripemd160(hexlify(bytes(preimage, "utf-8")))
            ).decode("ascii")
        elif hash_type == "sha1":
            preimage_type = 1
            preimage_hash = hashlib.sha1(bytes(preimage, "utf-8")).hexdigest()
        elif hash_type == "sha256":
            preimage_type = 2
            preimage_hash = hashlib.sha256(bytes(preimage, "utf-8")).hexdigest()
        else:
            raise ValueError(
                "Unknown 'hash_type'. Must be 'sha1', 'sha256', or 'ripemd160'"
            )

        op = operations.Htlc_create(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "from": account["id"],
                "to": to["id"],
                "amount": amount.json(),
                "preimage_hash": [preimage_type, preimage_hash],
                "preimage_size": len(preimage),
                "claim_period_seconds": expiration,
                "extensions": [],
            }
        )
        return await self.finalizeOp(op, account, "active", **kwargs)

    async def htlc_redeem(self, htlc_id, preimage, account=None, **kwargs):
        from binascii import hexlify

        htlc = await Htlc(htlc_id, blockchain_instance=self)
        if not account:
            if "default_account" in self.config:
                account = self.config["default_account"]
        if not account:
            account = htlc["to"]
        account = await Account(account, blockchain_instance=self)

        op = operations.Htlc_redeem(
            **{
                "fee": {"amount": 0, "asset_id": "1.3.0"},
                "redeemer": account["id"],
                "preimage": hexlify(bytes(preimage, "utf-8")).decode("ascii"),
                "htlc_id": htlc["id"],
                "extensions": [],
            }
        )
        return await self.finalizeOp(op, account, "active", **kwargs)

    async def subscribe_to_blocks(self, event_id=2):
        """
        Activate subscription to block.

        Each time block is applied an event will occur in self.notifications.

        :param int event_id: id of this subscription in upcoming notifications
        """
        await self.rpc.set_block_applied_callback(event_id)

    async def subscribe_to_pending_transactions(self, event_id=0):
        """
        Activate subscription to pending transactions.

        Each time transaction is pushed to database an event will occur in
        self.notifications.

        :param int event_id: id of this subscription in upcoming notifications
        """
        await self.rpc.set_pending_transaction_callback(event_id)

    async def subscribe_to_accounts(self, accounts, event_id=1):
        """
        Activate subscription to account-related events.

        :param list accounts: account names or ids to subscribe
        :param int event_id: id of this subscription in upcoming notifications
        """
        if isinstance(accounts, str):
            accounts = [accounts]

        # Set subscription, False means we're don't need ALL create/delete events
        await self.rpc.set_subscribe_callback(event_id, False)
        # True means we're activating subscription on account
        await self.rpc.get_full_accounts(accounts, True)

    async def subscribe_to_market(self, market, event_id=4):
        """
        Activate subscription on market events.

        :param str,bitshares.aio.Market market: market to set subscription
            on
        :param int event_id: id of this subscription in upcoming notifications
        """
        if isinstance(market, str):
            market = await Market(market, blockchain_instance=self)

        await self.rpc.subscribe_to_market(
            event_id, market["base"]["id"], market["quote"]["id"]
        )

    async def cancel_subscriptions(self):
        """Cancel all active subscriptions."""
        await self.rpc.cancel_all_subscriptions()
