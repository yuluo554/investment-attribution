"""公司行为还原引擎（M3）：规则表叠加在 M2 行形态之上。

职责边界（`data/README.md §4.1.2` 为唯一定义处）：

- **只做归类、挂依据、记调整前后**；份额与成本怎么变仍由 M2 的行形态决定
  （``receipt_free`` → 零成本份额，``receipt_paid`` → 视同买入，
  ``receipt_valued`` → 按对价入成本，``cash_event`` → 只动资金）。
  本模块**不给 M2 的 ``kind`` 加业务名分支**。
- 规则表能唯一确定 → 自动入账并挂 ``rule_id`` + 原始行；
- 不能确定（无规则 / 多规则 / 行形态不符 / 现金方向不符 / 规则要求人工确认）
  → 进待确认队列，**不丢弃、不静默跳过**；
- 调整只作用于派生台账：原始 ``ledger.json`` 只读，本模块不写回。

成本口径与 M2 同纪律：**显式参数，无默认值**（K7）。同一输入双跑逐字节一致。
"""

from decimal import Decimal

from ..errors import DataError, InputError
from ..ingest.rules import read_json_file
from ..ingest.values import canonical_number
from ..ledger.model import (
    ACQUISITION_KINDS,
    KIND_CASH_EVENT,
    KIND_NOOP,
    KIND_RECEIPT_FREE,
    KIND_RECEIPT_PAID,
    KIND_RECEIPT_VALUED,
    KIND_TRADE_BUY,
    KIND_TRADE_SELL,
    METHOD_LABELS,
    account_key,
    analysis_extras,
    analyze_row,
    sorted_rows,
    validate_rows,
)
from ..ledger.positions import make_book, quantity_text, unit_cost
from ..serialize import write_json_atomic
from .rules import load_corporate_action_rules

ACTIONS_FILE = "actions.json"
PENDING_FILE = "pending.json"
CONFIRMATION_FORMAT = "ia.confirmations"

CASH_INFLOW = "inflow"
CASH_OUTFLOW = "outflow"
CASH_NONE = "none"

# 行形态 → 调整动作。按**形态**归类，不按业务名（与 M2 同一纪律）。
ACTION_BY_KIND = {
    KIND_RECEIPT_FREE: "free_shares",
    KIND_RECEIPT_PAID: "paid_acquisition",
    KIND_RECEIPT_VALUED: "valued_acquisition",
    KIND_CASH_EVENT: "cash_only",
    KIND_NOOP: "no_effect",
}

PENDING_REASONS = (
    "NO_RULE_MATCH",
    "AMBIGUOUS_RULES",
    "KIND_NOT_EXPECTED",
    "CASH_EFFECT_MISMATCH",
    "RULE_REQUIRES_CONFIRM",
    "UNCLASSIFIED_ROW",
)


class ActionsResult(object):
    def __init__(self, actions, pending, method, degraded):
        self.actions = actions
        self.pending = pending
        self.method = method
        self.degraded = degraded

    @property
    def exit_code(self):
        from ..exit_codes import EXIT_DEGRADED, EXIT_OK

        return EXIT_DEGRADED if self.degraded else EXIT_OK


# ---------------------------------------------------------------------------
# 确认记录
# ---------------------------------------------------------------------------


class Confirmations(object):
    """人工确认记录：``(source_file, source_line_no) → 记录``。"""

    def __init__(self, records, path, sha256):
        self.records = records
        self.path = path
        self.sha256 = sha256

    def get(self, row):
        return self.records.get((row.get("source_file"), row.get("source_line_no")))

    def describe(self):
        if self.path is None:
            return None
        return {
            "path": self.path,
            "sha256": self.sha256,
            "count": len(self.records),
        }


def load_confirmations(path, rules):
    """读取人工确认记录并做结构校验；不合格即 ``InputError``（exit 2）。"""
    import hashlib
    from pathlib import Path

    if not path:
        return Confirmations({}, None, None)
    target = Path(path)
    if not target.is_file():
        raise InputError(
            "确认记录文件不存在：{}".format(target), code="CA_CONFIRMATION_MISSING"
        )
    payload = read_json_file(target, "CA_CONFIRMATION_INVALID")
    if not isinstance(payload, dict) or payload.get("format") != CONFIRMATION_FORMAT:
        raise InputError(
            "确认记录 format 必须为 {}".format(CONFIRMATION_FORMAT),
            code="CA_CONFIRMATION_FORMAT",
        )
    records = payload.get("confirmations")
    if not isinstance(records, list):
        raise InputError(
            "确认记录缺 confirmations 数组", code="CA_CONFIRMATION_INVALID"
        )

    by_key = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise InputError(
                "确认记录第 {} 条不是对象".format(index + 1),
                code="CA_CONFIRMATION_INVALID",
            )
        key = (record.get("source_file"), record.get("source_line_no"))
        if not key[0] or not isinstance(key[1], int) or isinstance(key[1], bool):
            raise InputError(
                "确认记录第 {} 条缺 source_file / source_line_no".format(index + 1),
                code="CA_CONFIRMATION_INVALID",
            )
        if key in by_key:
            raise InputError(
                "确认记录重复指向同一行：{}:{}".format(key[0], key[1]),
                code="CA_CONFIRMATION_DUPLICATE",
            )
        for field in ("decided_by", "decided_at", "basis"):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                raise InputError(
                    "确认记录 {}:{} 缺 {}（确认必须可追溯）".format(
                        key[0], key[1], field
                    ),
                    code="CA_CONFIRMATION_INCOMPLETE",
                )
        rule_id = record.get("rule_id")
        if rules.by_rule_id(rule_id) is None:
            raise InputError(
                "确认记录 {}:{} 引用未知规则 {!r}".format(key[0], key[1], rule_id),
                code="CA_CONFIRMATION_UNKNOWN_RULE",
            )
        by_key[key] = {
            "rule_id": rule_id,
            "decided_by": record["decided_by"],
            "decided_at": record["decided_at"],
            "basis": record["basis"],
        }
    return Confirmations(
        by_key, str(path), hashlib.sha256(target.read_bytes()).hexdigest()
    )


# ---------------------------------------------------------------------------
# 持仓跟踪（复用 M2 的账本原语，不复制成本逻辑）
# ---------------------------------------------------------------------------


class _Tracker(object):
    def __init__(self, method):
        self.method = method
        self.books = {}
        self.names = {}

    def book(self, key, symbol, create=False):
        book = self.books.get((key, symbol))
        if book is None and create:
            book = make_book(self.method)
            self.books[(key, symbol)] = book
        return book

    def state(self, book):
        quantity = book.quantity if book is not None else Decimal("0")
        cost = int(book.cost_fen) if book is not None else 0
        return {
            "quantity": quantity_text(quantity),
            "cost_fen": cost,
            "unit_cost": unit_cost(cost, quantity),
        }


def _apply(book, analysis):
    """按 M2 行形态推进账本；与 ``ledger.engine._apply_row`` 同语义。"""
    if analysis.kind in (KIND_TRADE_BUY, KIND_RECEIPT_PAID):
        book.add(
            analysis.quantity,
            (analysis.amount_fen or 0) + analysis.fees_fen,
            None,
        )
    elif analysis.kind == KIND_RECEIPT_VALUED:
        book.add(analysis.quantity, analysis.amount_fen or 0, None)
    elif analysis.kind == KIND_RECEIPT_FREE:
        book.add_free(analysis.quantity, None)
    elif analysis.kind == KIND_TRADE_SELL:
        book.take(analysis.quantity)


def _cash_effect(settle_fen):
    if settle_fen > 0:
        return CASH_INFLOW
    if settle_fen < 0:
        return CASH_OUTFLOW
    return CASH_NONE


def _expected_delta(action, analysis):
    """调整的**声明**增量；用于验证 before + delta == after（可逆复算）。"""
    if action == "free_shares":
        return analysis.quantity, 0
    if action == "paid_acquisition":
        return analysis.quantity, int((analysis.amount_fen or 0) + analysis.fees_fen)
    if action == "valued_acquisition":
        return analysis.quantity, int(analysis.amount_fen or 0)
    return Decimal("0"), 0


def _reversible(before, after, delta_quantity, delta_cost_fen):
    quantity_ok = (
        Decimal(before["quantity"]) + Decimal(delta_quantity)
        == Decimal(after["quantity"])
    )
    cost_ok = before["cost_fen"] + int(delta_cost_fen) == after["cost_fen"]
    return bool(quantity_ok and cost_ok)


def _evidence(row, rule=None, pattern=None):
    payload = {
        "source_file": row.get("source_file"),
        "source_sha256": row.get("source_sha256"),
        "source_line_no": row.get("source_line_no"),
    }
    if rule is not None:
        payload["rule_id"] = rule["rule_id"]
        payload["matched_pattern"] = pattern
        payload["evidence_hint"] = rule["evidence_hint"]
    return payload


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------


class _Engine(object):
    def __init__(self, method, rows, rules, confirmations, ledger_path, ledger_sha):
        self.method = method
        self.rows = rows
        self.rules = rules
        self.confirmations = confirmations
        self.ledger_path = ledger_path
        self.ledger_sha = ledger_sha
        self.tracker = _Tracker(method)
        self.events = []
        self.pending = []
        self.non_actions = []
        self.problems = []
        self.warnings = []
        self.by_event_type = {}
        self.other_rows = 0
        self.consumed_confirmations = set()
        self.reversibility_checked = 0
        self.reversibility_failed = 0

    # -- helpers ------------------------------------------------------------

    def _problem(self, code, message, row, extras=None):
        payload = {
            "code": code,
            "message": message,
            "source_file": row.get("source_file"),
            "source_line_no": row.get("source_line_no"),
        }
        if extras:
            payload.update(extras)
        self.problems.append(payload)

    def _pending(self, row, analysis, reason, message, candidates):
        entry = {
            "reason": reason,
            "message": message,
            "source_file": row.get("source_file"),
            "source_sha256": row.get("source_sha256"),
            "source_line_no": row.get("source_line_no"),
            "account_id": row.get("account_id"),
            "account_key": account_key(row),
            "symbol": row.get("symbol"),
            "name": row.get("name"),
            "business": row.get("business"),
            "trade_date": row.get("trade_date"),
            "trade_time": row.get("trade_time"),
            "kind": analysis.kind,
            "quantity": canonical_number(analysis.quantity),
            "amount_fen": analysis.amount_fen,
            "settlement_fen": int(analysis.settle_fen),
            "cash_effect": _cash_effect(analysis.settle_fen),
            "candidates": candidates,
            "evidence": _evidence(row),
        }
        entry.update(analysis_extras(analysis))
        self.pending.append(entry)

    def _event(self, row, analysis, rule, pattern, confirmation, before, after):
        action = ACTION_BY_KIND.get(analysis.kind, "no_effect")
        delta_quantity, delta_cost = _expected_delta(action, analysis)
        reversible = _reversible(before, after, delta_quantity, delta_cost)
        self.reversibility_checked += 1
        if not reversible:
            self.reversibility_failed += 1
            self._problem(
                "ADJUSTMENT_NOT_REVERSIBLE",
                "调整前后与声明增量不闭合：before {} + {} != after {}".format(
                    before, delta_quantity, after
                ),
                row,
                {"rule_id": rule["rule_id"], "action": action},
            )
        self.by_event_type[rule["event_type"]] = (
            self.by_event_type.get(rule["event_type"], 0) + 1
        )
        self.events.append(
            {
                "seq": len(self.events) + 1,
                "source_file": row.get("source_file"),
                "source_sha256": row.get("source_sha256"),
                "source_line_no": row.get("source_line_no"),
                "account_id": row.get("account_id"),
                "account_alias": row.get("account_alias"),
                "account_status": row.get("account_status"),
                "account_key": account_key(row),
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "business": row.get("business"),
                "trade_date": row.get("trade_date"),
                "trade_time": row.get("trade_time"),
                "rule_id": rule["rule_id"],
                "event_type": rule["event_type"],
                "matched_pattern": pattern,
                "evidence_hint": rule["evidence_hint"],
                "tax_rule": rule["tax_rule"],
                "requires_manual_confirm": rule["requires_manual_confirm"],
                "kind": analysis.kind,
                "quantity": canonical_number(analysis.quantity),
                "price": row.get("price"),
                "amount_fen": analysis.amount_fen,
                "settlement_fen": int(analysis.settle_fen),
                "fees_fen": int(analysis.fees_fen),
                "fee_source": analysis.fee_source,
                "cash_effect": _cash_effect(analysis.settle_fen),
                "adjustment": {
                    "action": action,
                    "quantity_delta": canonical_number(delta_quantity),
                    "cost_delta_fen": int(delta_cost),
                    "before": before,
                    "after": after,
                    "conserves_cost": int(delta_cost) == 0,
                    "reversible": reversible,
                },
                "confirmation": confirmation,
                "evidence": _evidence(row, rule, pattern),
            }
        )

    def _check_expected(self, rule, analysis):
        """规则声明的行形态与现金方向是否与这一行相符；返回原因或 ``None``。"""
        if analysis.kind not in rule["expected_kinds"]:
            return "KIND_NOT_EXPECTED"
        if _cash_effect(analysis.settle_fen) != rule["cash_effect"]:
            return "CASH_EFFECT_MISMATCH"
        return None

    # -- run ----------------------------------------------------------------

    def run(self):
        for index, row in enumerate(self.rows):
            analysis = analyze_row(row, index)
            if row.get("direction") != "other":
                self._advance(row, analysis)
                continue

            self.other_rows += 1
            self._classify_other(row, index, analysis)
        self._verify_confirmations_consumed()
        return self._build_result()

    def _advance(self, row, analysis):
        key = account_key(row)
        symbol = row.get("symbol")
        if row.get("name"):
            self.tracker.names[(key, symbol)] = row["name"]
        if not analysis.applicable:
            return
        if analysis.kind in (ACQUISITION_KINDS | frozenset((KIND_TRADE_SELL,))):
            book = self.tracker.book(key, symbol, create=True)
            _apply(book, analysis)

    def _classify_other(self, row, index, analysis):
        key = account_key(row)
        symbol = row.get("symbol")
        if row.get("name"):
            self.tracker.names[(key, symbol)] = row["name"]

        book = self.tracker.book(key, symbol)
        before = self.tracker.state(book)
        applies_to_position = analysis.applicable and analysis.kind in ACQUISITION_KINDS
        if applies_to_position and book is None:
            book = self.tracker.book(key, symbol, create=True)
        if applies_to_position:
            _apply(book, analysis)
        after = self.tracker.state(book)

        matches = self.rules.match(row.get("business"))
        candidates = [match.as_candidate() for match in matches]
        outcome = self._decide(row, analysis, matches)
        confirmation = self.confirmations.get(row)

        if confirmation is not None:
            self._handle_confirmation(
                row, analysis, confirmation, outcome, before, after, candidates
            )
            return

        if outcome[0] == "event":
            self._event(row, analysis, outcome[1], outcome[2], None, before, after)
        elif outcome[0] == "non_action":
            self.non_actions.append(
                {
                    "source_file": row.get("source_file"),
                    "source_line_no": row.get("source_line_no"),
                    "account_id": row.get("account_id"),
                    "account_key": key,
                    "symbol": symbol,
                    "business": row.get("business"),
                    "trade_date": row.get("trade_date"),
                    "kind": analysis.kind,
                    "settlement_fen": int(analysis.settle_fen),
                    "matched_pattern": outcome[1],
                    "reason": "NON_ACTION_PATTERN",
                    "note": "资金划转/存取，不是公司行为；不改持仓",
                }
            )
        else:
            self._pending(row, analysis, outcome[1], outcome[2], candidates)

    def _decide(self, row, analysis, matches):
        """规则表对一行的判定：``("event", rule, pattern)`` / ``("non_action", pattern)``
        / ``("pending", reason, message)``。判定是纯函数，便于与人工确认分开推理。"""
        if not analysis.applicable or analysis.kind is None:
            return (
                "pending",
                "UNCLASSIFIED_ROW",
                "行形态无法归类，规则表不参与判断；已原样进待确认队列",
            )

        if not matches:
            pattern = self.rules.non_action_pattern(row.get("business"))
            if pattern is not None:
                return ("non_action", pattern)
            return (
                "pending",
                "NO_RULE_MATCH",
                "规则表没有命中该业务名，无法确定公司行为类型",
            )

        if len(matches) > 1:
            return (
                "pending",
                "AMBIGUOUS_RULES",
                "命中 {} 条规则，无法确定唯一解释".format(len(matches)),
            )

        rule = matches[0].rule
        pattern = matches[0].matched_pattern
        if rule["requires_manual_confirm"]:
            return (
                "pending",
                "RULE_REQUIRES_CONFIRM",
                "规则 {} 要求人工确认".format(rule["rule_id"]),
            )

        mismatch = self._check_expected(rule, analysis)
        if mismatch is not None:
            return (
                "pending",
                mismatch,
                "命中规则 {} 但行形态/现金方向不符（kind={}，cash_effect={}）".format(
                    rule["rule_id"], analysis.kind, _cash_effect(analysis.settle_fen)
                ),
            )
        return ("event", rule, pattern)

    def _handle_confirmation(
        self, row, analysis, confirmation, outcome, before, after, candidates
    ):
        key = (row.get("source_file"), row.get("source_line_no"))
        self.consumed_confirmations.add(key)
        rule = self.rules.by_rule_id(confirmation["rule_id"])

        # 确认只能解决"待确认"，不能覆盖规则表的自动判定，也不能覆盖非行为判定。
        if outcome[0] != "pending":
            raise InputError(
                "确认记录 {}:{} 指向的行已被规则表判定为 {}，确认记录多余".format(
                    key[0], key[1], outcome[0]
                ),
                code="CA_CONFIRMATION_NOT_PENDING",
            )
        if outcome[1] == "UNCLASSIFIED_ROW":
            raise InputError(
                "确认记录 {}:{} 指向无法归类的行，不能套用规则".format(key[0], key[1]),
                code="CA_CONFIRMATION_INVALID",
            )
        mismatch = self._check_expected(rule, analysis)
        if mismatch is not None:
            raise InputError(
                "确认记录 {}:{} 选择规则 {}，但该行 kind={} / cash_effect={} 与规则声明不符".format(
                    key[0],
                    key[1],
                    rule["rule_id"],
                    analysis.kind,
                    _cash_effect(analysis.settle_fen),
                ),
                code="CA_CONFIRMATION_INVALID",
            )
        pattern = None
        for match in self.rules.match(row.get("business")):
            if match.rule_id == rule["rule_id"]:
                pattern = match.matched_pattern
                break
        self._event(row, analysis, rule, pattern, confirmation, before, after)

    def _verify_confirmations_consumed(self):
        unknown = [
            key for key in sorted(self.confirmations.records) if key not in self.consumed_confirmations
        ]
        if unknown:
            raise InputError(
                "确认记录指向统一流水里不存在的行：{}".format(
                    ", ".join("{}:{}".format(name, line) for name, line in unknown)
                ),
                code="CA_CONFIRMATION_UNKNOWN_ROW",
            )

    # -- result -------------------------------------------------------------

    def _build_result(self):
        positions = []
        for (key, symbol) in sorted(self.tracker.books):
            book = self.tracker.books[(key, symbol)]
            positions.append(
                {
                    "account_key": key,
                    "symbol": symbol,
                    "name": self.tracker.names.get((key, symbol)),
                    "quantity": quantity_text(book.quantity),
                    "cost_fen": int(book.cost_fen),
                    "unit_cost": unit_cost(book.cost_fen, book.quantity),
                    "is_open": book.quantity > 0,
                }
            )

        summary = {
            "rows": len(self.rows),
            "other_rows": self.other_rows,
            "action_events": len(self.events),
            "pending": len(self.pending),
            "non_action_rows": len(self.non_actions),
            "confirmed_by_record": sum(
                1 for event in self.events if event["confirmation"] is not None
            ),
            "unclassified_rows": sum(
                1 for item in self.pending if item["reason"] == "UNCLASSIFIED_ROW"
            ),
            "by_event_type": dict(
                (name, self.by_event_type[name]) for name in sorted(self.by_event_type)
            ),
            "reversibility": {
                "checked": self.reversibility_checked,
                "failed": self.reversibility_failed,
            },
            "problems": len(self.problems),
        }
        balanced = (
            summary["action_events"]
            + summary["pending"]
            + summary["non_action_rows"]
            == summary["other_rows"]
        )
        if not balanced:
            self._problem(
                "ACTION_BUCKETS_UNBALANCED",
                "方向 other 的行没有被完整分桶：{} != {} + {} + {}".format(
                    summary["other_rows"],
                    summary["action_events"],
                    summary["pending"],
                    summary["non_action_rows"],
                ),
                {},
            )
            summary["problems"] = len(self.problems)
        summary["buckets_balanced"] = balanced

        header = {
            "format": "ia.actions",
            "format_version": 1,
            "contract_version": self.rules.payload.get("contract_version"),
            "method": self.method,
            "method_label": METHOD_LABELS[self.method],
            "method_notice": (
                "本产物按 {} 口径复算调整前后持仓；口径是显式参数，"
                "调整只作用于派生台账，不回写原始流水。".format(METHOD_LABELS[self.method])
            ),
            "rule_pack": self.rules.describe(),
            "inputs": {
                "ledger": self.ledger_path,
                "ledger_sha256": self.ledger_sha,
                "rows": len(self.rows),
                "confirmations": self.confirmations.describe(),
            },
        }
        actions = dict(header)
        actions["summary"] = summary
        actions["events"] = self.events
        actions["pending"] = self.pending
        actions["non_actions"] = self.non_actions
        actions["adjusted_positions"] = positions
        actions["problems"] = self.problems
        actions["warnings"] = self.warnings

        pending = dict(header)
        pending["format"] = "ia.pending"
        pending["summary"] = {
            "other_rows": summary["other_rows"],
            "pending": summary["pending"],
            "action_events": summary["action_events"],
            "non_action_rows": summary["non_action_rows"],
            "reasons": _reason_counts(self.pending),
        }
        pending["items"] = self.pending
        return actions, pending

    @property
    def degraded(self):
        return bool(self.pending) or bool(self.problems)


def _reason_counts(items):
    counts = {}
    for item in items:
        counts[item["reason"]] = counts.get(item["reason"], 0) + 1
    return dict((key, counts[key]) for key in sorted(counts))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_actions(
    ledger_path,
    method,
    data_dir,
    out_path=None,
    pending_path=None,
    confirm_path=None,
):
    """读流水 → 规则匹配 → 调整前后台账；返回 ``ActionsResult``。

    ``ledger.json`` 只读：本函数不写回、不改写任何原始行。
    """
    from ..ledger.engine import load_cost_params, read_ledger_file, require_method

    cost_params = load_cost_params(data_dir)
    require_method(method, cost_params)
    rules = load_corporate_action_rules(data_dir)
    confirmations = load_confirmations(confirm_path, rules)
    payload, ledger_sha = read_ledger_file(ledger_path)
    rows = validate_rows(payload)

    engine = _Engine(
        method,
        sorted_rows(rows),
        rules,
        confirmations,
        str(ledger_path),
        ledger_sha,
    )
    actions, pending = engine.run()

    if out_path:
        write_json_atomic(out_path, actions)
    if pending_path:
        write_json_atomic(pending_path, pending)
    return ActionsResult(actions, pending, method, engine.degraded)
