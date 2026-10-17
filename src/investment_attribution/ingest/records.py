"""单行 → 统一流水；失败抛 ``RowError``（带原因码与原始行）。

审计纪律（K5）：每条入库行必带 ``source_file`` / ``source_sha256`` /
``source_line_no``，``source_line_no`` 是**源文件物理行号**，不是数组下标。
"""

from ..errors import IAError
from .cells import strip_shell
from .values import (
    FieldValueError,
    parse_date,
    parse_direction,
    parse_money_fen,
    parse_price,
    parse_quantity,
    parse_time,
    tidy,
)

EXTRA_TYPES = {"cash_balance": "money", "share_balance": "quantity", "note": "text"}


class RowError(IAError):
    """行级失败：不中断整份文件，进行级错误台账。"""

    code = "ROW_INVALID"

    def __init__(self, message, *, code, raw_line, line_no):
        super().__init__(message, code=code)
        self.raw_line = raw_line
        self.line_no = line_no


def _cell(cells, index):
    if index >= len(cells):
        return ""
    return cells[index] if cells[index] is not None else ""


def build_record(cells, header, context):
    """把一行单元格转成统一流水 dict。

    ``context`` 提供：source_file / source_sha256 / line_no / raw_line /
    account_registry / rules。
    """
    values = {}
    for index, field_name in header.field_names:
        raw = _cell(cells, index)
        clean, ok = strip_shell(raw)
        if not ok:
            raise RowError(
                "单元格壳非法（剥离后仍含引号）：第 {} 列 {!r}".format(index + 1, raw),
                code="INVALID_CELL_SHELL",
                raw_line=context["raw_line"],
                line_no=context["line_no"],
            )
        values[field_name] = clean

    rules = context["rules"]

    try:
        trade_date = parse_date(values.get("trade_date"))
    except FieldValueError as exc:
        raise RowError(str(exc), code=exc.code, raw_line=context["raw_line"], line_no=context["line_no"])

    try:
        trade_time = parse_time(values.get("trade_time"))
    except FieldValueError as exc:
        raise RowError(str(exc), code=exc.code, raw_line=context["raw_line"], line_no=context["line_no"])

    business = tidy(values.get("business"))
    if not business:
        raise RowError(
            "业务名称为空，无法判定流水类型",
            code="MISSING_BUSINESS",
            raw_line=context["raw_line"],
            line_no=context["line_no"],
        )

    symbol = tidy(values.get("symbol"))
    if not symbol:
        raise RowError(
            "证券代码为空",
            code="MISSING_SYMBOL",
            raw_line=context["raw_line"],
            line_no=context["line_no"],
        )

    try:
        quantity = parse_quantity(values.get("quantity"), rules["quantity_max_scale"])
        price = parse_price(values.get("price"), rules["price_max_scale"])
    except FieldValueError as exc:
        raise RowError(str(exc), code=exc.code, raw_line=context["raw_line"], line_no=context["line_no"])

    money = {}
    for field_name in (
        "amount",
        "settlement_amount",
        "commission",
        "stamp_tax",
        "transfer_fee",
        "other_fee",
    ):
        try:
            money[field_name] = parse_money_fen(
                values.get(field_name),
                code="INVALID_AMOUNT",
                label=field_name,
                max_scale=rules["money_max_scale"],
            )
        except FieldValueError as exc:
            raise RowError(
                str(exc), code=exc.code, raw_line=context["raw_line"], line_no=context["line_no"]
            )

    extra = {}
    for index, raw_header in header.unknown_columns:
        clean, ok = strip_shell(_cell(cells, index))
        if not ok:
            raise RowError(
                "未登记列 {} 的单元格壳非法".format(raw_header),
                code="INVALID_CELL_SHELL",
                raw_line=context["raw_line"],
                line_no=context["line_no"],
            )
        extra[raw_header] = clean

    for field_name, kind in EXTRA_TYPES.items():
        if field_name not in values:
            continue
        raw = values[field_name]
        try:
            if kind == "money":
                extra[field_name] = parse_money_fen(
                    raw,
                    code="INVALID_AMOUNT",
                    label=field_name,
                    max_scale=rules["money_max_scale"],
                )
            elif kind == "quantity":
                extra[field_name] = parse_quantity(raw, rules["quantity_max_scale"])
            else:
                extra[field_name] = raw
        except FieldValueError as exc:
            raise RowError(
                str(exc), code=exc.code, raw_line=context["raw_line"], line_no=context["line_no"]
            )

    direction = parse_direction(
        values.get("direction", ""),
        business,
        rules["direction_buy"],
        rules["direction_sell"],
    )

    account = context["account_registry"].resolve(
        values.get("account_id"), values.get("shareholder_account")
    )

    record = {
        "source_file": context["source_file"],
        "source_sha256": context["source_sha256"],
        "source_line_no": context["line_no"],
        "account_id": account.account_id,
        "account_alias": account.alias,
        "shareholder_account": account.shareholder_account,
        "account_status": account.status,
        "account_pending_reason": account.pending_reason,
        "symbol": symbol,
        "name": tidy(values.get("name")) or None,
        "business": business,
        "trade_date": trade_date,
        "trade_time": trade_time,
        "quantity": quantity,
        "price": price,
        "amount": money["amount"],
        "settlement_amount": money["settlement_amount"],
        "commission": money["commission"],
        "stamp_tax": money["stamp_tax"],
        "transfer_fee": money["transfer_fee"],
        "other_fee": money["other_fee"],
        "direction": direction,
        "contract_no": tidy(values.get("contract_no")) or None,
        "trade_no": tidy(values.get("trade_no")) or None,
        "extra": extra,
    }
    return record
