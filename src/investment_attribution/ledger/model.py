"""统一流水 → 成本引擎的行级语义（M2）。

金额全程**整数分**；数量用 ``Decimal`` 解析规范十进制文本，不转 float。
行级语义只读交割单自身字段（方向 / 数量 / 金额 / 发生金额 / 四项费用），
**不按业务名称建规则表**——公司行为的规则匹配与人工确认属于 M3。

行形态分类（与 ``data/README.md §4.1`` 同源）::

    trade_buy       买入
    trade_sell      卖出
    receipt_paid    方向 other，有数量、有对价、有现金流出（如配股）
    receipt_valued  方向 other，有数量、有对价、无现金流出（如红利再投）
    receipt_free    方向 other，有数量、无对价（如送股 / 拆分）
    cash_event      方向 other，无数量、有现金变动（转入 / 转出 / 红利 / 其他）
    noop            方向 other，无数量、无现金变动

**未登记账户**按 ``source_file + shareholder_account`` 单独分组，
不并入默认账户（K5 / M1 约定）。
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..errors import InputError
from ..ingest.values import canonical_number

FEE_FIELDS = ("commission", "stamp_tax", "transfer_fee", "other_fee")
COST_METHODS = ("fifo", "wavg")
METHOD_LABELS = {
    "fifo": "先进先出 (FIFO)",
    "wavg": "移动加权平均 (WAVG)",
}

KIND_TRADE_BUY = "trade_buy"
KIND_TRADE_SELL = "trade_sell"
KIND_RECEIPT_PAID = "receipt_paid"
KIND_RECEIPT_VALUED = "receipt_valued"
KIND_RECEIPT_FREE = "receipt_free"
KIND_CASH_EVENT = "cash_event"
KIND_NOOP = "noop"

ACQUISITION_KINDS = frozenset(
    (KIND_TRADE_BUY, KIND_RECEIPT_PAID, KIND_RECEIPT_VALUED, KIND_RECEIPT_FREE)
)

REQUIRED_ROW_KEYS = (
    "source_file",
    "source_sha256",
    "source_line_no",
    "symbol",
    "business",
    "trade_date",
    "quantity",
    "direction",
)


class RowAnalysis(object):
    """一行流水的引擎语义；``problems`` 为 ``(code, message, extras)``。"""

    def __init__(
        self,
        kind,
        quantity,
        amount_fen,
        reported_settle_fen,
        settle_fen,
        fees_fen,
        fee_source,
        fee_detail,
        problems,
        applicable,
    ):
        self.kind = kind
        self.quantity = quantity
        self.amount_fen = amount_fen
        self.reported_settle_fen = reported_settle_fen
        self.settle_fen = settle_fen
        self.fees_fen = fees_fen
        self.fee_source = fee_source
        self.fee_detail = fee_detail
        self.problems = problems
        self.applicable = applicable

    @property
    def codes(self):
        return [code for code, _, _ in self.problems]


def decimal_value(value, label="数值", where="row"):
    """把规范十进制文本解析成 ``Decimal``；空值按 0。"""
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, bool):
        raise InputError(
            "{}: {} 不是合法数字 {!r}".format(where, label, value),
            code="LEDGER_VALUE_INVALID",
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InputError(
            "{}: {} 不是合法数字 {!r}".format(where, label, value),
            code="LEDGER_VALUE_INVALID",
        )
    if not number.is_finite():
        raise InputError(
            "{}: {} 不是有限数字 {!r}".format(where, label, value),
            code="LEDGER_VALUE_INVALID",
        )
    return number


def optional_decimal(value, label="价格", where="row"):
    if value is None or value == "":
        return None
    return decimal_value(value, label, where)


def round_half_up(value):
    """``Decimal`` → 整数（四舍五入，不用银行家舍入）。"""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def allocate_cost(cost_fen, take, total):
    """按数量比例拆分整数分成本；整批取尽时不做除法，保证守恒。"""
    if take == total:
        return cost_fen
    if total == 0:
        return 0
    part = round_half_up(Decimal(cost_fen) * take / total)
    if part < 0:
        return 0
    if part > cost_fen:
        return cost_fen
    return part


def _money(value, label, where):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError(
            "{}: {} 必须是整数分（被告知的口径），实际 {!r}".format(where, label, value),
            code="LEDGER_MONEY_NOT_INTEGER",
        )
    return int(value)


def row_where(row, index):
    return "{}:{}（第 {} 条）".format(
        row.get("source_file") or "<未知文件>",
        row.get("source_line_no"),
        index + 1,
    )


def validate_rows(payload):
    """校验统一流水顶层形态，返回 ``rows`` 列表（不修改原对象）。"""
    if not isinstance(payload, dict) or payload.get("format") != "ia.ledger":
        raise InputError(
            "统一流水 format 必须为 ia.ledger",
            code="LEDGER_FORMAT_MISMATCH",
        )
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise InputError(
            "统一流水缺 rows 数组",
            code="LEDGER_ROWS_MISSING",
        )
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InputError(
                "统一流水第 {} 条不是对象".format(index + 1),
                code="LEDGER_ROW_MALFORMED",
            )
        missing = [key for key in REQUIRED_ROW_KEYS if key not in row]
        if missing:
            raise InputError(
                "统一流水第 {} 条缺字段：{}".format(index + 1, ", ".join(missing)),
                code="LEDGER_ROW_MALFORMED",
            )
        if not isinstance(row["source_line_no"], int) or isinstance(
            row["source_line_no"], bool
        ):
            raise InputError(
                "统一流水第 {} 条 source_line_no 必须是整数".format(index + 1),
                code="LEDGER_ROW_MALFORMED",
            )
    return rows


def order_key(row):
    """全链路排序键：``trade_date + trade_time + source_line_no``。

    ``source_file`` 只用于打破"不同文件同一行号"的平局，不改变主键语义。
    时间缺失按空串排在最前；日期与行号都是字符串/整数，不依赖 locale。
    """
    return (
        str(row.get("trade_date") or ""),
        str(row.get("trade_time") or ""),
        row["source_line_no"],
        str(row.get("source_file") or ""),
    )


def sorted_rows(rows):
    return sorted(rows, key=order_key)


def account_key(row):
    """分账户台账键。

    未登记账户（``account_id is None``）按文件名 + 股东账号单独成组，
    **不并入任何默认账户**；否则两个待确认账户会互相抵消。
    """
    if row.get("account_id"):
        return row["account_id"]
    return "unregistered:{}:{}".format(
        row.get("source_file") or "",
        row.get("shareholder_account") or "",
    )


def _kind_of(row, quantity, amount_fen, reported_settle_fen):
    direction = row.get("direction")
    if direction == "buy":
        return KIND_TRADE_BUY
    if direction == "sell":
        return KIND_TRADE_SELL
    if direction not in (None, "other"):
        return None

    if quantity < 0:
        return None
    if quantity > 0:
        if amount_fen is not None and amount_fen > 0:
            if reported_settle_fen is None or reported_settle_fen == 0:
                return KIND_RECEIPT_VALUED
            if reported_settle_fen < 0:
                return KIND_RECEIPT_PAID
            return None
        return KIND_RECEIPT_FREE
    if reported_settle_fen not in (None, 0):
        return KIND_CASH_EVENT
    return KIND_NOOP


def _expected_settlement(kind, amount_fen, fees_fen):
    amount = amount_fen or 0
    if kind in (KIND_TRADE_BUY, KIND_RECEIPT_PAID):
        return -(amount + fees_fen)
    if kind == KIND_TRADE_SELL:
        return amount - fees_fen
    if kind in (KIND_RECEIPT_VALUED, KIND_RECEIPT_FREE, KIND_NOOP):
        return 0
    return None


def analyze_row(row, index):
    """把一行统一流水转成引擎语义；不做任何状态变更。"""
    where = row_where(row, index)
    problems = []

    quantity = decimal_value(row.get("quantity"), "数量", where)
    if quantity < 0:
        problems.append(
            (
                "NEGATIVE_QUANTITY",
                "数量为负（{}），无法判定是买入还是卖出".format(canonical_number(quantity)),
                {"quantity": canonical_number(quantity)},
            )
        )

    amount_fen = _money(row.get("amount"), "amount", where)
    price = optional_decimal(row.get("price"), "价格", where)
    amount_derived = False
    if amount_fen is None and quantity != 0 and price is not None:
        amount_fen = round_half_up(quantity * price * 100)
        amount_derived = True

    reported_settle = _money(row.get("settlement_amount"), "settlement_amount", where)

    kind = _kind_of(row, quantity, amount_fen, reported_settle)
    if kind is None:
        problems.append(
            (
                "UNCLASSIFIED_ROW",
                "无法按行形态归类（方向 {!r}，数量 {}，发生金额 {}）".format(
                    row.get("direction"),
                    canonical_number(quantity),
                    reported_settle,
                ),
                {"direction": row.get("direction")},
            )
        )

    reported_fees = [row.get(field) for field in FEE_FIELDS]
    fee_detail = {field: row.get(field) for field in FEE_FIELDS}
    fees_fen = 0
    fee_source = "as_reported"
    if all(value is None for value in reported_fees):
        if reported_settle is None or kind not in (
            KIND_TRADE_BUY,
            KIND_TRADE_SELL,
            KIND_RECEIPT_PAID,
        ):
            fees_fen = 0
            fee_source = "not_reported"
        else:
            # 买入/配股：settle = -(amount + fees) → fees = -amount - settle
            # 卖出：      settle = amount - fees  → fees = amount - settle
            fees_fen = _expected_settlement(kind, amount_fen, 0) - reported_settle
            if fees_fen < 0:
                problems.append(
                    (
                        "FEE_IMPLIED_NEGATIVE",
                        "费用未分列，且由发生金额推导出的费用为负（{} 分）".format(fees_fen),
                        {"implied_fee_fen": fees_fen},
                    )
                )
                fees_fen = 0
            fee_source = "derived_from_settlement"
    elif any(value is None for value in reported_fees):
        fees_fen = sum(value for value in reported_fees if value is not None)
        fee_source = "partially_reported"
    else:
        fees_fen = sum(reported_fees)
        fee_source = "as_reported"

    if kind in (KIND_TRADE_BUY, KIND_TRADE_SELL) and amount_fen is None:
        problems.append(
            (
                "MISSING_TRADE_AMOUNT",
                "成交行缺成交金额且无法由数量 × 价格推导",
                {},
            )
        )
    if kind in (KIND_TRADE_BUY, KIND_TRADE_SELL) and quantity == 0:
        problems.append(
            (
                "ZERO_QUANTITY_TRADE",
                "成交行数量为 0",
                {},
            )
        )

    expected_settle = _expected_settlement(kind, amount_fen, fees_fen)
    if reported_settle is not None and expected_settle is not None:
        if reported_settle != expected_settle:
            problems.append(
                (
                    "SETTLEMENT_MISMATCH",
                    "发生金额 {} 与 成交金额±费用 推导的 {} 不一致".format(
                        reported_settle, expected_settle
                    ),
                    {
                        "expected_settlement_fen": expected_settle,
                        "reported_settlement_fen": reported_settle,
                        "difference_fen": reported_settle - expected_settle,
                    },
                )
            )

    if reported_settle is not None:
        settle_fen = reported_settle
    elif expected_settle is not None:
        settle_fen = expected_settle
    else:
        settle_fen = 0

    applicable = not any(
        code
        in (
            "UNCLASSIFIED_ROW",
            "NEGATIVE_QUANTITY",
            "MISSING_TRADE_AMOUNT",
            "ZERO_QUANTITY_TRADE",
        )
        for code in (item[0] for item in problems)
    )

    return RowAnalysis(
        kind=kind,
        quantity=quantity,
        amount_fen=amount_fen,
        reported_settle_fen=reported_settle,
        settle_fen=settle_fen,
        fees_fen=fees_fen,
        fee_source=fee_source,
        fee_detail=fee_detail,
        problems=problems,
        applicable=applicable,
    )


def analysis_extras(analysis):
    return {
        "kind": analysis.kind,
        "quantity": canonical_number(analysis.quantity),
        "amount_fen": analysis.amount_fen,
        "settlement_fen": analysis.settle_fen,
        "fees_fen": analysis.fees_fen,
        "fee_source": analysis.fee_source,
    }
