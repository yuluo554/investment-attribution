"""B2 成本基准：引擎结果 vs 真值台账的逐笔比对。

比对函数独立于引擎计算路径——它只读两边产物，不重算成本。这样
"注入一个缺陷 → 该套件必须报未达标"才是可证伪的：把引擎算错的值传进来，
这里必须给出违例，而不是照样返回空列表。

门槛来自 ``manifest.bench_thresholds``（数量 ≤ 1 股、金额 ≤ 0.01 元），
本模块不硬编码第二份数值。
"""

from decimal import Decimal

from ..errors import DataError

METHOD_KEYS = ("fifo", "wavg")


class B2Report(object):
    def __init__(self, violations, checked, tolerances):
        self.violations = violations
        self.checked = checked
        self.tolerances = tolerances

    @property
    def ok(self):
        return not self.violations

    def as_dict(self):
        return {
            "suite": "B2",
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
        "amount_fen": Decimal(str(int(round(
            thresholds["position_amount_tolerance_cny"] * 100
        )))),
    }


def compare_one_method(holdings, truth, tolerances, method=None):
    """单个口径的逐笔比对；返回违例列表（空 = 达标）。"""
    violations = []
    quantity_tolerance = tolerances["quantity"]
    amount_tolerance = tolerances["amount_fen"]

    if method is not None and holdings.get("method") != method:
        violations.append(
            {"check": "method_label", "expected": method, "actual": holdings.get("method")}
        )

    engine_events = {
        (event["source_file"], event["source_line_no"]): event
        for event in holdings.get("events", [])
    }
    if len(engine_events) != len(truth.get("events", [])):
        violations.append(
            {
                "check": "event_count",
                "expected": len(truth.get("events", [])),
                "actual": len(engine_events),
            }
        )

    for expected in truth.get("events", []):
        key = tuple(expected["key"])
        actual = engine_events.get(key)
        if actual is None:
            violations.append({"check": "event_missing", "key": list(key)})
            continue
        if actual["kind"] != expected["kind"]:
            violations.append(
                {"check": "event_kind", "key": list(key),
                 "expected": expected["kind"], "actual": actual["kind"]}
            )
        before = Decimal(str(actual["position"]["quantity_before"]))
        expected_before = Decimal(str(expected["position_before_quantity"]))
        if abs(before - expected_before) > quantity_tolerance:
            violations.append(
                {"check": "quantity_before", "key": list(key),
                 "expected": str(expected_before), "actual": str(before)}
            )
        after = Decimal(str(actual["position"]["quantity_after"]))
        expected_after = Decimal(str(expected["position_after_quantity"]))
        if abs(after - expected_after) > quantity_tolerance:
            violations.append(
                {"check": "quantity_after", "key": list(key),
                 "expected": str(expected_after), "actual": str(after)}
            )
        cost_after = Decimal(str(actual["position"]["cost_after_fen"]))
        expected_cost = Decimal(str(expected["position_after_cost_fen"]))
        if abs(cost_after - expected_cost) > amount_tolerance:
            violations.append(
                {"check": "cost_after", "key": list(key),
                 "expected": str(expected_cost), "actual": str(cost_after)}
            )
        realized = (actual.get("realized") or {}).get("realized_fen")
        expected_realized = expected["realized_fen"]
        if (realized is None) != (expected_realized is None):
            violations.append(
                {"check": "realized_presence", "key": list(key),
                 "expected": expected_realized, "actual": realized}
            )
        elif expected_realized is not None:
            difference = abs(Decimal(str(realized)) - Decimal(str(expected_realized)))
            if difference > amount_tolerance:
                violations.append(
                    {"check": "realized", "key": list(key),
                     "expected": str(expected_realized), "actual": str(realized)}
                )

    engine_positions = {
        (item["account_id"], item["symbol"]): item
        for item in holdings.get("positions", [])
    }
    for expected in truth.get("positions", []):
        key = (expected["account_id"], expected["symbol"])
        actual = engine_positions.get(key)
        if actual is None:
            violations.append(
                {"check": "position_missing", "key": list(key)}
            )
            continue
        difference = abs(
            Decimal(str(actual["quantity"])) - Decimal(str(expected["quantity"]))
        )
        if difference > quantity_tolerance:
            violations.append(
                {"check": "position_quantity", "key": list(key),
                 "expected": expected["quantity"], "actual": actual["quantity"]}
            )
        if abs(int(actual["cost_fen"]) - int(expected["cost_fen"])) > amount_tolerance:
            violations.append(
                {"check": "position_cost", "key": list(key),
                 "expected": expected["cost_fen"], "actual": actual["cost_fen"]}
            )
        if bool(actual["is_open"]) is not bool(expected["is_open"]):
            violations.append(
                {"check": "position_open_flag", "key": list(key),
                 "expected": expected["is_open"], "actual": actual["is_open"]}
            )

    engine_accounts = {
        item["account_id"]: item for item in holdings.get("accounts", [])
    }
    for expected in truth.get("accounts", []):
        key = (expected["account_id"],)
        actual = engine_accounts.get(expected["account_id"])
        if actual is None:
            violations.append({"check": "account_missing", "key": list(key)})
            continue
        for field, actual_field in (
            ("realized_net_fen", "realized_net_fen"),
            ("position_cost_fen", "position_cost_fen"),
        ):
            if abs(int(actual[actual_field]) - int(expected[field])) > amount_tolerance:
                violations.append(
                    {"check": "account_" + field, "key": list(key),
                     "expected": expected[field], "actual": actual[actual_field]}
                )

    engine_snapshots = {
        snap["date"]: snap for snap in holdings.get("daily_snapshots", [])
    }
    for expected in truth.get("daily_snapshots", []):
        actual = engine_snapshots.get(expected["date"])
        if actual is None:
            violations.append({"check": "snapshot_missing", "key": [expected["date"]]})
            continue
        left = {
            (item["account_id"], item["symbol"]): (item["quantity"], item["cost_fen"])
            for item in actual["positions"]
        }
        right = {
            (item["account_id"], item["symbol"]): (item["quantity"], item["cost_fen"])
            for item in expected["positions"]
        }
        if set(left) != set(right):
            violations.append(
                {"check": "snapshot_positions", "key": [expected["date"]],
                 "expected": sorted(right), "actual": sorted(left)}
            )
            continue
        for key, (quantity, cost) in sorted(right.items()):
            actual_quantity, actual_cost = left[key]
            if abs(Decimal(actual_quantity) - Decimal(quantity)) > quantity_tolerance:
                violations.append(
                    {"check": "snapshot_quantity", "key": [expected["date"]] + list(key),
                     "expected": quantity, "actual": actual_quantity}
                )
            if abs(int(actual_cost) - int(cost)) > amount_tolerance:
                violations.append(
                    {"check": "snapshot_cost", "key": [expected["date"]] + list(key),
                     "expected": cost, "actual": actual_cost}
                )

    return violations


def truth_method(holdings, truth):
    return truth.get("method") or holdings.get("method")


def compare(holdings_by_method, truth_payload, data_dir):
    """对每个口径跑一遍比对，返回 ``B2Report``。"""
    methods = truth_payload.get("methods") or {}
    if not methods:
        raise DataError("成本真值缺 methods 段", code="COST_TRUTH_INCOMPLETE")
    tolerances = _tolerances(data_dir)
    violations = []
    checked = 0
    for method in METHOD_KEYS:
        holdings = holdings_by_method.get(method)
        if holdings is None:
            violations.append({"check": "method_missing", "key": [method]})
            continue
        truth = methods.get(method)
        if truth is None:
            violations.append({"check": "truth_method_missing", "key": [method]})
            continue
        method_violations = compare_one_method(holdings, truth, tolerances, method)
        for item in method_violations:
            item["method"] = method
        violations.extend(method_violations)
        checked += len(truth.get("events", []))
    return B2Report(
        violations,
        checked,
        {
            "quantity_shares": str(tolerances["quantity"]),
            "amount_fen": str(tolerances["amount_fen"]),
        },
    )
