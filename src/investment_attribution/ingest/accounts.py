"""账户—股东账号映射（不自动猜测）。

纪律（plan/03 §M1）：未登记账户落 **待确认**，**不并入默认账户**。
把未登记账号挂到"默认账户"是最常见的静默失真：用户以为数据进了账本，
实际是在另一个账户名下算的。

映射只认"登记表 + 文件里显式出现的资金账号"两处证据；股东账号仅在
登记表声明过时才用于**校验**，不会用股东账号反推资金账号。
"""


class AccountResolution(object):
    def __init__(self, account_id, alias, shareholder_account, status, pending_reason):
        self.account_id = account_id
        self.alias = alias
        self.shareholder_account = shareholder_account
        self.status = status
        self.pending_reason = pending_reason

    @property
    def registered(self):
        return self.status == "registered"

    def as_dict(self):
        return {
            "account_id": self.account_id,
            "alias": self.alias,
            "shareholder_account": self.shareholder_account,
            "status": self.status,
            "pending_reason": self.pending_reason,
        }


class AccountRegistry(object):
    def __init__(self, accounts, pending_reasons=None):
        self.accounts = dict(accounts)
        self.extra_reasons = dict(pending_reasons or {})
        self._cache = {}

    def resolve(self, account_id, shareholder_account):
        key = (account_id or "", shareholder_account or "")
        if key in self._cache:
            return self._cache[key]
        resolution = self._resolve(account_id, shareholder_account)
        self._cache[key] = resolution
        return resolution

    def _resolve(self, account_id, shareholder_account):
        account_id = (account_id or "").strip()
        shareholder_account = (shareholder_account or "").strip()

        if not account_id:
            return AccountResolution(
                None,
                None,
                shareholder_account or None,
                "pending",
                "ACCOUNT_NOT_IN_FILE",
            )

        entry = self.accounts.get(account_id)
        if entry is None:
            reason = self.extra_reasons.get(account_id, "ACCOUNT_NOT_REGISTERED")
            return AccountResolution(
                account_id, None, shareholder_account or None, "pending", reason
            )

        declared = entry.get("shareholder_accounts") or []
        if declared and shareholder_account and shareholder_account not in declared:
            return AccountResolution(
                account_id,
                entry.get("alias") or account_id,
                shareholder_account,
                "pending",
                "SHAREHOLDER_ACCOUNT_MISMATCH",
            )

        return AccountResolution(
            account_id,
            entry.get("alias") or account_id,
            shareholder_account or (declared[0] if declared else None),
            "registered",
            None,
        )
