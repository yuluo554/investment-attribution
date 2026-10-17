"""B3 公司行为基准：引擎事件台账 vs 真值台账的逐类比对。

与 B2 同纪律：比对函数**只读两边产物**，不重算成本、不调用规则表。
把引擎算错的事件类型 / 调整前后值 / 待确认集合传进来，这里必须给出违例，
否则"逐类事件对齐"只是口头承诺。

门槛来自 ``manifest.bench_thresholds``（数量 ≤ 1 股、金额 ≤ 0.01 元）。
"""

from decimal import Decimal

from ..errors import DataError


class B3Report(object):
    def __init__(self, violations, checked, tolerances):
        self.violations = violations
        self.checked = checked
        self.tolerances = tolerances

    @property
    def ok(self):
        return not self.violations

    def as_dict(self):
        return {
            "suite": "B3",
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
    }


def _key_of(item):
    return (item.get("source_file"), item.get("source_line_no"))


def compare(actions_payload, truth_payload, data_dir, method=None):
    """比对事件台账、调整前后值、待确认队列；返回 ``B3Report``。"""
    if truth_payload.get("format") != "ia.truth.actions":
        raise DataError("公司行为真值 format 不匹配", code="ACTIONS_TRUTH_INCOMPLETE")
    methods = truth_payload.get("methods") or {}
    if not methods:
        raise DataError("公司行为真值缺 methods 段", code="ACTIONS_TRUTH_INCOMPLETE")

    tolerances = _tolerances(data_dir)
    quantity_tolerance = tolerances["quantity"]
    amount_tolerance = tolerances["amount_fen"]
    violations = []
    checked = 0

    target = method or actions_payload.get("method")
    truth = methods.get(target)
    if truth is None:
        raise DataError(
            "公司行为真值缺口径 {!r}".format(target), code="ACTIONS_TRUTH_INCOMPLETE"
        )
    if actions_payload.get("method") != target:
        violations.append(
            {
                "check": "method_label",
                "expected": target,
                "actual": actions_payload.get("method"),
            }
        )

    engine_events = {_key_of(event): event for event in actions_payload.get("events", [])}
    truth_events = truth.get("events", [])
    if len(engine_events) != len(truth_events):
        violations.append(
            {
                "check": "event_count",
                "expected": len(truth_events),
                "actual": len(engine_events),
            }
        )

    for expected in truth_events:
        key = _key_of(expected)
        actual = engine_events.get(key)
        if actual is None:
            violations.append({"check": "event_missing", "key": list(key)})
            continue
        checked += 1
        for field in ("event_type", "rule_id", "kind"):
            if actual.get(field) != expected.get(field):
                violations.append(
                    {
                        "check": "event_" + field,
                        "key": list(key),
                        "expected": expected.get(field),
                        "actual": actual.get(field),
                    }
                )
        adjustment = actual.get("adjustment") or {}
        before = adjustment.get("before") or {}
        after = adjustment.get("after") or {}
        for side, source in (("before", before), ("after", after)):
            expected_side = expected[side]
            difference = abs(
                Decimal(str(source.get("quantity")))
                - Decimal(str(expected_side["quantity"]))
            )
            if difference > quantity_tolerance:
                violations.append(
                    {
                        "check": "adjustment_{}_quantity".format(side),
                        "key": list(key),
                        "expected": expected_side["quantity"],
                        "actual": source.get("quantity"),
                    }
                )
            if (
                abs(
                    int(source.get("cost_fen") or 0)
                    - int(expected_side["cost_fen"])
                )
                > amount_tolerance
            ):
                violations.append(
                    {
                        "check": "adjustment_{}_cost".format(side),
                        "key": list(key),
                        "expected": expected_side["cost_fen"],
                        "actual": source.get("cost_fen"),
                    }
                )
            expected_unit = expected_side.get("unit_cost")
            actual_unit = source.get("unit_cost")
            if (expected_unit is None) != (actual_unit is None):
                violations.append(
                    {
                        "check": "adjustment_{}_unit_cost_presence".format(side),
                        "key": list(key),
                        "expected": expected_unit,
                        "actual": actual_unit,
                    }
                )
            elif expected_unit is not None and abs(
                Decimal(str(actual_unit)) - Decimal(str(expected_unit))
            ) > Decimal("0.000001"):
                violations.append(
                    {
                        "check": "adjustment_{}_unit_cost".format(side),
                        "key": list(key),
                        "expected": expected_unit,
                        "actual": actual_unit,
                    }
                )
        if (actual.get("cash_effect")) != expected.get("cash_effect"):
            violations.append(
                {
                    "check": "event_cash_effect",
                    "key": list(key),
                    "expected": expected.get("cash_effect"),
                    "actual": actual.get("cash_effect"),
                }
            )
        evidence = actual.get("evidence") or {}
        for field in ("source_file", "source_line_no"):
            if evidence.get(field) != expected.get(field):
                violations.append(
                    {
                        "check": "evidence_" + field,
                        "key": list(key),
                        "expected": expected.get(field),
                        "actual": evidence.get(field),
                    }
                )
        if expected.get("rule_id") and not evidence.get("rule_id"):
            violations.append(
                {"check": "evidence_rule_missing", "key": list(key)}
            )

    engine_pending = {
        _key_of(item): item for item in actions_payload.get("pending", [])
    }
    truth_pending = truth.get("pending", [])
    if set(engine_pending) != {_key_of(item) for item in truth_pending}:
        violations.append(
            {
                "check": "pending_set",
                "expected": sorted(_key_of(item) for item in truth_pending),
                "actual": sorted(engine_pending),
            }
        )
    for expected in truth_pending:
        key = _key_of(expected)
        actual = engine_pending.get(key)
        if actual is None:
            continue
        checked += 1
        if actual.get("reason") != expected.get("reason"):
            violations.append(
                {
                    "check": "pending_reason",
                    "key": list(key),
                    "expected": expected.get("reason"),
                    "actual": actual.get("reason"),
                }
            )

    engine_non_actions = {
        _key_of(item) for item in actions_payload.get("non_actions", [])
    }
    truth_non_actions = {_key_of(item) for item in truth.get("non_actions", [])}
    if engine_non_actions != truth_non_actions:
        violations.append(
            {
                "check": "non_action_set",
                "expected": sorted(truth_non_actions),
                "actual": sorted(engine_non_actions),
            }
        )

    for expected in truth.get("adjusted_positions", []):
        key = (expected["account_key"], expected["symbol"])
        actual = next(
            (
                item
                for item in actions_payload.get("adjusted_positions", [])
                if (item.get("account_key"), item.get("symbol")) == key
            ),
            None,
        )
        if actual is None:
            violations.append(
                {"check": "position_missing", "key": list(key)}
            )
            continue
        if (
            abs(
                Decimal(str(actual.get("quantity")))
                - Decimal(str(expected["quantity"]))
            )
            > quantity_tolerance
        ):
            violations.append(
                {
                    "check": "position_quantity",
                    "key": list(key),
                    "expected": expected["quantity"],
                    "actual": actual.get("quantity"),
                }
            )
        if (
            abs(int(actual.get("cost_fen") or 0) - int(expected["cost_fen"]))
            > amount_tolerance
        ):
            violations.append(
                {
                    "check": "position_cost",
                    "key": list(key),
                    "expected": expected["cost_fen"],
                    "actual": actual.get("cost_fen"),
                }
            )

    for expected in truth.get("by_event_type", []):
        actual = (actions_payload.get("summary") or {}).get("by_event_type", {})
        if int(actual.get(expected["event_type"], 0)) != int(expected["count"]):
            violations.append(
                {
                    "check": "event_type_count",
                    "key": [expected["event_type"]],
                    "expected": expected["count"],
                    "actual": actual.get(expected["event_type"], 0),
                }
            )

    return B3Report(
        violations,
        checked,
        {
            "quantity_shares": str(quantity_tolerance),
            "amount_fen": str(amount_tolerance),
        },
    )
