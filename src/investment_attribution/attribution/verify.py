"""B4 收益归因基准：引擎产物 vs 真值台账的逐账户 / 逐视图比对。

与 B2 / B3 同纪律：比对函数**只读两边产物**，不重算成本、不调求解器。
把引擎算错的 XIRR、拆分数、费用或缺失的账户传进来，这里必须给出违例，
否则"与 Excel 手工对照达标"只是一句口号。

门槛来自 ``manifest.bench_thresholds``（XIRR 相对误差、金额 ≤ 0.01 元），
本模块不硬编码第二份数值。TWR 与 XIRR 同为收益率，共用相对误差门槛。
"""

from decimal import Decimal

from ..errors import DataError

RELATIVE_KINDS = ("xirr", "twr_cashflow", "twr_daily")

#: 真值字段名 → 产物字段名（真值只存期望，不向产物字段名妥协）。
AMOUNT_FIELD_MAP = {
    "realized_sells_fen": "realized_from_sells_fen",
    "dividend_income_fen": "dividend_income_fen",
    "reinvested_income_fen": "reinvested_income_fen",
    "other_income_fen": "other_income_fen",
    "realized_total_fen": "realized_total_fen",
    "unrealized_fen": "unrealized_fen",
    "total_return_fen": "total_return_fen",
    "fees_fen": "fees_fen",
}


class B4Report(object):
    def __init__(self, violations, checked, tolerances):
        self.violations = violations
        self.checked = checked
        self.tolerances = tolerances

    @property
    def ok(self):
        return not self.violations

    def as_dict(self):
        return {
            "suite": "B4",
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
        "xirr_relative": Decimal(str(thresholds["xirr_relative_tolerance"])),
        "amount_fen": Decimal(
            str(int(round(thresholds["position_amount_tolerance_cny"] * 100)))
        ),
        "split_fen": Decimal(
            str(int(round(thresholds["split_conservation_tolerance_cny"] * 100)))
        ),
    }


def _relative_violation(key, expected, actual, tolerance):
    if actual is None:
        return {
            "check": "relative_missing",
            "key": key,
            "expected": str(expected),
            "actual": None,
        }
    expected_decimal = Decimal(str(expected))
    actual_decimal = Decimal(str(actual))
    denominator = max(abs(expected_decimal), Decimal("0.000000001"))
    if abs(actual_decimal - expected_decimal) / denominator > tolerance:
        return {
            "check": "relative",
            "key": key,
            "expected": str(expected_decimal),
            "actual": str(actual_decimal),
        }
    return None


def _amount_violation(key, expected, actual, tolerance):
    if actual is None:
        return {
            "check": "amount_missing",
            "key": key,
            "expected": int(expected),
            "actual": None,
        }
    if abs(Decimal(str(int(actual))) - Decimal(str(int(expected)))) > tolerance:
        return {
            "check": "amount",
            "key": key,
            "expected": int(expected),
            "actual": int(actual),
        }
    return None


def _amount_key(key):
    return key if not isinstance(key, str) else [key]


def compare(artifact, truth_payload, data_dir, method=None):
    """逐账户 / 合并视图比对引擎产物与真值；返回 ``B4Report``。"""
    if truth_payload.get("format") != "ia.truth.attribution":
        raise DataError(
            "归因真值 format 不匹配", code="ATTRIBUTION_TRUTH_INCOMPLETE"
        )
    target = method or artifact.get("method")
    methods = truth_payload.get("methods") or {}
    if target not in methods:
        raise DataError(
            "归因真值缺口径 {!r}".format(target), code="ATTRIBUTION_TRUTH_INCOMPLETE"
        )
    tolerances = _tolerances(data_dir)
    violations = []
    checked = 0

    if artifact.get("method") != target:
        violations.append(
            {
                "check": "method_label",
                "expected": target,
                "actual": artifact.get("method"),
            }
        )

    engine = _index_views(artifact)
    artifact_basis = (artifact.get("twr") or {}).get("basis")
    truth_views = truth_payload.get("views") or {}
    expected_views = list(truth_views.get("accounts") or []) + [
        truth_views.get("merged") or {}
    ]
    for expected in expected_views:
        if not expected:
            continue
        key = expected.get("account_key")
        actual = engine.get(key)
        if actual is None:
            violations.append({"check": "view_missing", "key": [key]})
            continue
        checked += 1
        if expected.get("xirr") is not None:
            violation = _relative_violation(
                [key, "xirr"],
                expected["xirr"],
                (actual.get("xirr") or {}).get("rate"),
                tolerances["xirr_relative"],
            )
            if violation:
                violations.append(violation)
        for field in (
            "net_contribution_fen",
            "terminal_cash_fen",
            "terminal_securities_fen",
            "terminal_total_fen",
        ):
            if field in expected:
                violation = _amount_violation(
                    [key, field],
                    expected[field],
                    actual.get(field),
                    tolerances["amount_fen"],
                )
                if violation:
                    violations.append(violation)
        for basis in ("cashflow", "daily"):
            if basis != artifact_basis:
                continue
            expected_twr = (expected.get("twr") or {}).get(basis)
            if expected_twr is None:
                continue
            actual_twr = (actual.get("twr") or {}).get(basis)
            violation = _relative_violation(
                [key, "twr_" + basis],
                expected_twr,
                actual_twr,
                tolerances["xirr_relative"],
            )
            if violation:
                violations.append(violation)

    method_truth = methods[target]
    for expected in list(method_truth.get("accounts") or []) + [
        method_truth.get("merged") or {}
    ]:
        if not expected:
            continue
        key = expected.get("account_key")
        actual_pnl = _pnl_by_key(artifact).get(key)
        if actual_pnl is None:
            violations.append({"check": "pnl_missing", "key": [key]})
            continue
        checked += 1
        for field, actual_field in AMOUNT_FIELD_MAP.items():
            if field not in expected:
                continue
            violation = _amount_violation(
                [key, field],
                expected[field],
                actual_pnl.get(actual_field),
                tolerances["split_fen"],
            )
            if violation:
                violations.append(violation)

    decomposition_truth = method_truth.get("decomposition") or {}
    engine_decomposition = artifact.get("decomposition") or {}
    for dimension in ("by_symbol", "by_industry", "by_account", "by_year"):
        expected_rows = decomposition_truth.get(dimension) or []
        if not expected_rows:
            continue
        key_field = {
            "by_symbol": "symbol",
            "by_industry": "industry",
            "by_account": "account_key",
            "by_year": "year",
        }[dimension]
        actual_rows = {
            str(item.get(key_field)): item
            for item in (engine_decomposition.get(dimension) or [])
        }
        for expected in expected_rows:
            key = str(expected.get(key_field))
            actual = actual_rows.get(key)
            if actual is None:
                violations.append({"check": dimension + "_missing", "key": [key]})
                continue
            checked += 1
            for field in ("realized_fen", "income_fen", "unrealized_fen", "total_fen"):
                violation = _amount_violation(
                    [key, field], expected[field], actual.get(field), tolerances["split_fen"]
                )
                if violation:
                    violations.append(violation)

    expected_fees = method_truth.get("fees") or {}
    if "total_fen" in expected_fees:
        actual_fees = (artifact.get("fees") or {}).get("total_fen")
        violation = _amount_violation(
            ["fees", "total_fen"], expected_fees["total_fen"], actual_fees, tolerances["split_fen"]
        )
        if violation:
            violations.append(violation)

    return B4Report(
        violations,
        checked,
        {
            "xirr_relative": str(tolerances["xirr_relative"]),
            "amount_fen": str(tolerances["amount_fen"]),
            "split_fen": str(tolerances["split_fen"]),
        },
    )


def _index_views(artifact):
    """把产物里的账户视图（含 xirr / twr / 期末市值）索引起来。"""
    index = {}
    xirr_by_key = {
        item.get("account_key"): item
        for item in (artifact.get("xirr") or {}).get("accounts") or []
    }
    twr_by_key = {
        item.get("account_key"): item
        for item in (artifact.get("twr") or {}).get("accounts") or []
    }
    pnl_by_key = _pnl_by_key(artifact)
    for key, pnl in pnl_by_key.items():
        entry = {
            "account_key": key,
            "net_contribution_fen": pnl.get("net_contribution_fen"),
            "terminal_cash_fen": pnl.get("terminal_cash_fen"),
            "terminal_securities_fen": pnl.get("terminal_market_value_fen"),
            "terminal_total_fen": pnl.get("terminal_total_fen"),
            "xirr": (xirr_by_key.get(key) or {}),
            "twr": {},
        }
        for basis in ("cashflow", "daily"):
            view = twr_by_key.get(key)
            if view is not None and view.get("basis") == basis:
                entry["twr"][basis] = view.get("cumulative_rate")
        index[key] = entry
    return index


def _pnl_by_key(artifact):
    pnl = artifact.get("pnl") or {}
    rows = list(pnl.get("accounts") or []) + [pnl.get("merged") or {}]
    return {
        item.get("account_key"): item for item in rows if item
    }
