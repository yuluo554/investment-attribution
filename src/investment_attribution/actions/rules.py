"""公司行为规则包：加载、结构校验与业务名匹配（M3）。

规则包是 M1 由 ``tools/gen_data.py`` 产出的**冻结产物**
（``data/rules/corporate_actions.json``），本模块只读不写、缺字段即
``DataError``。代码里不得写第二份业务名表——匹配语义、事件类型、
允许的行形态（``expected_kinds``）与现金方向（``cash_effect``）全部由
规则包声明。

匹配纪律（`data/README.md §4.1.2` 为唯一定义处）：

- 只匹配 ``direction == "other"`` 的行；
- 业务名 ``business`` 与模式 ``pattern`` 的命中条件是
  ``business == pattern`` 或 ``pattern in business``（子串，与 M1 的方向
  识别同源）；
- 一条规则都不中 → 看 ``non_action_patterns``：命中即"非公司行为的资金
  划转"，否则进待确认队列（``unmatched_policy``）；
- 命中多条 → 歧义，进待确认队列，附全部候选；
- 命中一条但行形态或现金方向不符 → 进待确认队列，附候选解释。
"""

import hashlib
from pathlib import Path

from ..errors import DataError
from ..ingest.rules import read_json

RULE_FILE = "rules/corporate_actions.json"
FORMAT = "ia.corporate_actions"
EVENT_TYPES = (
    "dividend",
    "stock_dividend",
    "rights_issue",
    "split",
    "reinvested_dividend",
)
CASH_EFFECTS = ("inflow", "outflow", "none")
ROW_KINDS = (
    "trade_buy",
    "trade_sell",
    "receipt_paid",
    "receipt_valued",
    "receipt_free",
    "cash_event",
    "noop",
)
TAX_RULES = ("as_reported", "none")
UNMATCHED_POLICY = "pending_confirmation"


class RuleMatch(object):
    def __init__(self, rule, matched_pattern):
        self.rule = rule
        self.matched_pattern = matched_pattern

    @property
    def rule_id(self):
        return self.rule["rule_id"]

    @property
    def event_type(self):
        return self.rule["event_type"]

    def as_candidate(self):
        return {
            "rule_id": self.rule["rule_id"],
            "event_type": self.rule["event_type"],
            "expected_kinds": list(self.rule["expected_kinds"]),
            "cash_effect": self.rule["cash_effect"],
        }


class CorporateActionRules(object):
    """校验后的规则包；字段缺失或语义漂移一律 ``DataError``。"""

    def __init__(self, payload, path, sha256):
        self.payload = payload
        self.path = path
        self.sha256 = sha256
        self.rules = payload["rules"]
        self.non_action_patterns = payload["non_action_patterns"]
        self.unmatched_policy = payload["unmatched_policy"]

    # -- matching -----------------------------------------------------------

    def match(self, business):
        """返回 ``[RuleMatch]``（规则文件顺序，全部候选）。"""
        text = business or ""
        matches = []
        for rule in self.rules:
            for pattern in rule["business_name_patterns"]:
                if text == pattern or pattern in text:
                    matches.append(RuleMatch(rule, pattern))
                    break
        return matches

    def non_action_pattern(self, business):
        text = business or ""
        for pattern in self.non_action_patterns:
            if text == pattern or pattern in text:
                return pattern
        return None

    def by_rule_id(self, rule_id):
        for rule in self.rules:
            if rule["rule_id"] == rule_id:
                return rule
        return None

    def by_event_type(self, event_type):
        found = [rule for rule in self.rules if rule["event_type"] == event_type]
        return found[0] if len(found) == 1 else None

    # -- header -------------------------------------------------------------

    def describe(self):
        return {
            "path": RULE_FILE,
            "format": self.payload.get("format"),
            "format_version": self.payload.get("format_version"),
            "contract_version": self.payload.get("contract_version"),
            "sha256": self.sha256,
            "rule_count": len(self.rules),
            "rule_ids": [rule["rule_id"] for rule in self.rules],
            "unmatched_policy": self.unmatched_policy,
            "non_action_pattern_count": len(self.non_action_patterns),
        }


def _require_list(payload, key, code):
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise DataError("公司行为规则缺 {} 或为空".format(key), code=code)
    return value


def _validate_rule(rule, index, seen_ids, seen_patterns):
    if not isinstance(rule, dict):
        raise DataError(
            "公司行为规则第 {} 条不是对象".format(index + 1), code="CA_RULE_MALFORMED"
        )
    rule_id = rule.get("rule_id")
    if not isinstance(rule_id, str) or not rule_id:
        raise DataError(
            "公司行为规则第 {} 条缺 rule_id".format(index + 1), code="CA_RULE_MALFORMED"
        )
    if rule_id in seen_ids:
        raise DataError("rule_id 重复：{}".format(rule_id), code="CA_RULE_DUPLICATE")
    seen_ids.add(rule_id)

    event_type = rule.get("event_type")
    if event_type not in EVENT_TYPES:
        raise DataError(
            "规则 {} 的 event_type 不在白名单：{!r}".format(rule_id, event_type),
            code="CA_RULE_MALFORMED",
        )

    patterns = rule.get("business_name_patterns")
    if not isinstance(patterns, list) or not patterns:
        raise DataError(
            "规则 {} 缺 business_name_patterns".format(rule_id),
            code="CA_RULE_MALFORMED",
        )
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern.strip():
            raise DataError(
                "规则 {} 的 business_name_patterns 含空模式".format(rule_id),
                code="CA_RULE_MALFORMED",
            )
        if pattern in seen_patterns:
            raise DataError(
                "业务名模式 {} 同时属于 {} 与 {}".format(
                    pattern, seen_patterns[pattern], rule_id
                ),
                code="CA_PATTERN_DUPLICATE",
            )
        seen_patterns[pattern] = rule_id

    expected = rule.get("expected_kinds")
    if not isinstance(expected, list) or not expected:
        raise DataError(
            "规则 {} 缺 expected_kinds".format(rule_id), code="CA_RULE_MALFORMED"
        )
    for kind in expected:
        if kind not in ROW_KINDS:
            raise DataError(
                "规则 {} 的 expected_kinds 含未知行形态 {!r}".format(rule_id, kind),
                code="CA_RULE_MALFORMED",
            )

    if rule.get("cash_effect") not in CASH_EFFECTS:
        raise DataError(
            "规则 {} 的 cash_effect 非法：{!r}".format(rule_id, rule.get("cash_effect")),
            code="CA_RULE_MALFORMED",
        )
    if rule.get("tax_rule") not in TAX_RULES:
        raise DataError(
            "规则 {} 的 tax_rule 非法：{!r}".format(rule_id, rule.get("tax_rule")),
            code="CA_RULE_MALFORMED",
        )
    if not isinstance(rule.get("requires_manual_confirm"), bool):
        raise DataError(
            "规则 {} 的 requires_manual_confirm 必须是布尔".format(rule_id),
            code="CA_RULE_MALFORMED",
        )
    if not isinstance(rule.get("evidence_hint"), str):
        raise DataError(
            "规则 {} 缺 evidence_hint".format(rule_id), code="CA_RULE_MALFORMED"
        )


def load_corporate_action_rules(data_dir):
    """读取并校验冻结规则包；返回 ``CorporateActionRules``。"""
    path = Path(data_dir) / RULE_FILE
    if not path.is_file():
        raise DataError("公司行为规则包缺失：{}".format(path), code="CA_RULES_MISSING")
    payload = read_json(data_dir, RULE_FILE, "CA_RULES_MISSING")
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise DataError(
            "公司行为规则 format 必须为 {}".format(FORMAT), code="CA_RULES_FORMAT"
        )
    if payload.get("unmatched_policy") != UNMATCHED_POLICY:
        raise DataError(
            "unmatched_policy 必须为 {}（未识别不得静默跳过）".format(UNMATCHED_POLICY),
            code="CA_UNMATCHED_POLICY",
        )

    rules = _require_list(payload, "rules", "CA_RULES_INCOMPLETE")
    non_action = payload.get("non_action_patterns")
    if not isinstance(non_action, list):
        raise DataError(
            "公司行为规则缺 non_action_patterns 数组", code="CA_RULES_INCOMPLETE"
        )

    seen_ids = set()
    seen_patterns = {}
    for index, rule in enumerate(rules):
        _validate_rule(rule, index, seen_ids, seen_patterns)

    for pattern in non_action:
        if not isinstance(pattern, str) or not pattern.strip():
            raise DataError(
                "non_action_patterns 含空模式", code="CA_RULES_INCOMPLETE"
            )
        if pattern in seen_patterns:
            raise DataError(
                "业务名模式 {} 同时出现在公司行为规则 {} 与非行为清单".format(
                    pattern, seen_patterns[pattern]
                ),
                code="CA_PATTERN_CONFLICT",
            )

    missing = [name for name in EVENT_TYPES if not any(
        rule["event_type"] == name for rule in rules
    )]
    if missing:
        raise DataError(
            "公司行为规则缺 event_type：{}".format(", ".join(missing)),
            code="CA_EVENT_TYPE_MISSING",
        )

    return CorporateActionRules(
        payload, RULE_FILE, hashlib.sha256(path.read_bytes()).hexdigest()
    )
