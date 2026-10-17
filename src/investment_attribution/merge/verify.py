"""B5 合并基准：合并产物 vs 合并真值的逐项比对。

与 B2 / B3 / B4 同纪律：比对函数**只读两边产物**——它不重算归因、不重算
成本、不重新识别划转。把合并算错的总收益、漏掉的划转腿、汇总错的合并持仓
传进来，这里必须给出违例，否则"合并前后总收益不变"只是一句口号。

门槛来自 ``manifest.bench_thresholds``（数量 ≤ 1 股、金额 ≤ 0.01 元、
拆分守恒 ≤ 0.01 元、划转抵消 ≤ 0.01 元），本模块不硬编码第二份数值。
"""

from decimal import Decimal

from ..errors import DataError

AMOUNT_FIELDS = (
    "net_contribution_fen",
    "terminal_cash_fen",
    "terminal_market_value_fen",
    "terminal_total_fen",
    "realized_from_sells_fen",
    "realized_total_fen",
    "unrealized_fen",
    "total_return_fen",
)


class B5Report(object):
    def __init__(self, violations, checked, tolerances):
        self.violations = violations
        self.checked = checked
        self.tolerances = tolerances

    @property
    def ok(self):
        return not self.violations

    def as_dict(self):
        return {
            "suite": "B5",
            "ok": self.ok,
            "checked": self.checked,
            "violation_count": len(self.violations),
            "tolerances": self.tolerances,
            "violations": self.violations,
        }


def _tolerances(data_dir):
    from ..manifest import load_manifest

    thresholds = load_manifest(data_dir)["bench_thresholds"]
    return {
        "quantity": Decimal(str(thresholds["position_quantity_tolerance_shares"])),
        "amount_fen": Decimal(
            str(int(round(thresholds["position_amount_tolerance_cny"] * 100)))
        ),
        "split_fen": Decimal(
            str(int(round(thresholds["split_conservation_tolerance_cny"] * 100)))
        ),
        "transfer_fen": Decimal(
            str(int(round(thresholds["transfer_offset_tolerance_cny"] * 100)))
        ),
    }


def _amount(violations, key, expected, actual, tolerance):
    if actual is None:
        violations.append(
            {"check": "amount_missing", "key": key, "expected": int(expected), "actual": None}
        )
        return
    if abs(Decimal(str(int(actual))) - Decimal(str(int(expected)))) > tolerance:
        violations.append(
            {
                "check": "amount",
                "key": key,
                "expected": int(expected),
                "actual": int(actual),
            }
        )


def _index(payload, field):
    return {item.get(field): item for item in payload or []}


def compare(merged_payload, truth_payload, data_dir, method=None):
    """逐账户 / 合并视图 / 划转 / 合并持仓比对；返回 ``B5Report``。"""
    if truth_payload.get("format") != "ia.truth.merge":
        raise DataError("合并真值 format 不匹配", code="MERGE_TRUTH_INCOMPLETE")
    target = method or merged_payload.get("method")
    methods = truth_payload.get("methods") or {}
    if target not in methods:
        raise DataError(
            "合并真值缺口径 {!r}".format(target), code="MERGE_TRUTH_INCOMPLETE"
        )
    truth = methods[target]
    tolerances = _tolerances(data_dir)
    violations = []
    checked = 0

    if merged_payload.get("method") != target:
        violations.append(
            {
                "check": "method_label",
                "expected": target,
                "actual": merged_payload.get("method"),
            }
        )

    actual_accounts = _index(merged_payload.get("accounts"), "account_id")
    for expected in truth.get("accounts") or []:
        key = expected["account_id"]
        actual = actual_accounts.get(key)
        if actual is None:
            violations.append({"check": "account_missing", "key": [key]})
            continue
        checked += 1
        for field in AMOUNT_FIELDS:
            if field in expected:
                _amount(
                    violations,
                    [key, field],
                    expected[field],
                    actual.get(field),
                    tolerances["split_fen"],
                )
    extra = sorted(set(actual_accounts) - {item["account_id"] for item in truth.get("accounts") or []})
    if extra:
        violations.append({"check": "unexpected_account", "key": extra})

    expected_merged = truth.get("merged") or {}
    actual_merged = merged_payload.get("merged") or {}
    if not actual_merged:
        violations.append({"check": "merged_missing", "key": ["merged"]})
    else:
        checked += 1
        for field in AMOUNT_FIELDS:
            if field in expected_merged:
                _amount(
                    violations,
                    ["merged", field],
                    expected_merged[field],
                    actual_merged.get(field),
                    tolerances["split_fen"],
                )

    checks = merged_payload.get("checks") or {}
    expected_checks = truth.get("checks") or {}
    for field in (
        "total_return_before_fen",
        "total_return_after_fen",
        "difference_fen",
    ):
        if field in expected_checks:
            _amount(
                violations,
                ["checks", field],
                expected_checks[field],
                checks.get(field),
                tolerances["split_fen"],
            )
    if expected_checks.get("conserved") is not None:
        if bool(checks.get("conserved")) is not bool(expected_checks["conserved"]):
            violations.append(
                {
                    "check": "checks_conserved",
                    "key": ["checks", "conserved"],
                    "expected": expected_checks["conserved"],
                    "actual": checks.get("conserved"),
                }
            )
    if checks.get("difference_fen") is not None and abs(
        Decimal(str(int(checks["difference_fen"])))
    ) > tolerances["transfer_fen"]:
        violations.append(
            {
                "check": "checks_difference_exceeds_tolerance",
                "key": ["checks", "difference_fen"],
                "expected": "<= {}".format(tolerances["transfer_fen"]),
                "actual": checks["difference_fen"],
            }
        )

    actual_transfers = _index(
        (merged_payload.get("transfers") or {}).get("entries"), "contract_no"
    )
    truth_transfers = truth_payload.get("transfer_ledger") or []
    if set(actual_transfers) != {item["contract_no"] for item in truth_transfers}:
        violations.append(
            {
                "check": "transfer_set",
                "expected": sorted({item["contract_no"] for item in truth_transfers}),
                "actual": sorted(actual_transfers),
            }
        )
    for expected in truth_transfers:
        key = expected["contract_no"]
        actual = actual_transfers.get(key)
        if actual is None:
            continue
        checked += 1
        for field in ("trade_date", "leg_count"):
            if actual.get(field) != expected.get(field):
                violations.append(
                    {
                        "check": "transfer_" + field,
                        "key": [key],
                        "expected": expected.get(field),
                        "actual": actual.get(field),
                    }
                )
        for field in ("amount_fen", "net_fen"):
            _amount(
                violations, [key, field], expected[field], actual.get(field), tolerances["transfer_fen"]
            )
        if (actual.get("offset") or {}).get("balanced") is not True:
            violations.append(
                {
                    "check": "transfer_not_balanced",
                    "key": [key],
                    "expected": True,
                    "actual": (actual.get("offset") or {}).get("balanced"),
                }
            )
        actual_legs = {
            (leg.get("account_id"), leg.get("direction"), leg.get("source_file"), leg.get("source_line_no")): leg
            for leg in actual.get("legs") or []
        }
        for expected_leg in expected.get("legs") or []:
            leg_key = (
                expected_leg["account_id"],
                expected_leg["direction"],
                expected_leg["source_file"],
                expected_leg["source_line_no"],
            )
            leg = actual_legs.get(leg_key)
            if leg is None:
                violations.append(
                    {"check": "transfer_leg_missing", "key": [key, list(leg_key)]}
                )
                continue
            _amount(
                violations,
                [key, "leg", expected_leg["account_id"]],
                expected_leg["settlement_fen"],
                leg.get("settlement_fen"),
                tolerances["amount_fen"],
            )

    actual_holdings = _index(merged_payload.get("holdings"), "symbol")
    truth_holdings = truth.get("merged_holdings") or []
    if set(actual_holdings) != {item["symbol"] for item in truth_holdings}:
        violations.append(
            {
                "check": "holdings_set",
                "expected": sorted({item["symbol"] for item in truth_holdings}),
                "actual": sorted(actual_holdings),
            }
        )
    for expected in truth_holdings:
        symbol = expected["symbol"]
        actual = actual_holdings.get(symbol)
        if actual is None:
            continue
        checked += 1
        difference = abs(
            Decimal(str(actual.get("quantity"))) - Decimal(str(expected["quantity"]))
        )
        if difference > tolerances["quantity"]:
            violations.append(
                {
                    "check": "holding_quantity",
                    "key": [symbol],
                    "expected": expected["quantity"],
                    "actual": actual.get("quantity"),
                }
            )
        for field in ("cost_fen", "market_value_fen", "account_count_ever"):
            if field not in expected:
                continue
            _amount(
                violations,
                [symbol, field],
                expected[field],
                actual.get(field),
                tolerances["amount_fen"],
            )

    return B5Report(
        violations,
        checked,
        {
            "quantity_shares": str(tolerances["quantity"]),
            "amount_fen": str(tolerances["amount_fen"]),
            "split_fen": str(tolerances["split_fen"]),
            "transfer_fen": str(tolerances["transfer_fen"]),
        },
    )
