"""单元格值解析：日期、时间、数量、金额、方向。

数值纪律（与 `data/README.md §四` 对齐）：

- 金额一律转成**整数分**：解析时用 ``Decimal`` 一次量化，之后全程整数，
  避免浮点累加误差；超过参数包允许的小数位即判 ``invalid``，不静默截断；
- 数量 / 价格保留原值的规范十进制文本（数量最多 4 位小数，容纳基金份额）；
- 千分位逗号与全角字符是真实导出形态，属于**输入清洗**而非宽松修补，
  清洗后仍非数字照样落 ``invalid``。
"""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

FULLWIDTH = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "．": ".",
        "－": "-",
        "—": "-",
        "　": " ",
        "＋": "+",
    }
)

DATE_INPUTS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S")
TIME_INPUTS = ("%H:%M:%S", "%H:%M", "%H%M%S")


class FieldValueError(ValueError):
    """单元格值解析失败，带机读原因码。

    继承 ``ValueError`` 是为了让"值不合法"在 Python 语义上仍然是个值错误；
    带 ``code`` 是为了让行级台账能直接记账，不必解析异常文本。
    """

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def tidy(value):
    if value is None:
        return ""
    return value.translate(FULLWIDTH).strip()


def parse_date(value):
    text = tidy(value)
    if not text:
        raise FieldValueError("日期为空", "INVALID_DATE")
    for fmt in DATE_INPUTS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise FieldValueError("日期格式不认识：{!r}".format(value), "INVALID_DATE")


def parse_time(value):
    text = tidy(value)
    if not text:
        return None
    for fmt in TIME_INPUTS:
        try:
            return datetime.strptime(text, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    raise FieldValueError("时间格式不认识：{!r}".format(value), "INVALID_TIME")


NUMBER_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")


def _decimal(value, code, label):
    text = tidy(value).replace(",", "")
    if not text:
        raise FieldValueError("{}为空".format(label), code)
    if not NUMBER_RE.match(text):
        raise FieldValueError("{}不是合法数字：{!r}".format(label, value), code)
    try:
        return Decimal(text)
    except InvalidOperation:
        raise FieldValueError("{}不是合法数字：{!r}".format(label, value), code)


def _scaled(value, code, label, max_scale):
    number = _decimal(value, code, label)
    exponent = -number.as_tuple().exponent
    if exponent > max_scale:
        raise FieldValueError(
            "{}小数位超过 {} 位：{!r}".format(label, max_scale, value), code
        )
    return number


def parse_money_fen(value, code="INVALID_AMOUNT", label="金额", max_scale=2):
    """金额 → 整数分。空值返回 ``None``（列缺失与"值为 0"必须可区分）。"""
    text = tidy(value)
    if not text:
        return None
    number = _scaled(text, code, label, max_scale)
    quantized = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int((quantized * 100).to_integral_value())


def parse_quantity(value, max_scale=4):
    text = tidy(value)
    if not text:
        return "0"
    number = _scaled(text, "INVALID_QUANTITY", "数量", max_scale)
    return canonical_number(number)


def parse_price(value, max_scale=4):
    text = tidy(value)
    if not text:
        return None
    number = _scaled(text, "INVALID_PRICE", "价格", max_scale)
    return canonical_number(number)


def canonical_number(number):
    """规范十进制文本：去尾零、去 ``.0``、零写成 ``0``。"""
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


def parse_direction(value, business, buy_patterns, sell_patterns):
    """方向优先取显式方向列，回落到业务名称；都不命中即 ``other``。"""
    text = tidy(value)
    for pattern in buy_patterns:
        if text and text == pattern:
            return "buy"
    for pattern in sell_patterns:
        if text and text == pattern:
            return "sell"
    if text:
        lower = text.lower()
        for pattern in buy_patterns:
            if lower == pattern.lower():
                return "buy"
        for pattern in sell_patterns:
            if lower == pattern.lower():
                return "sell"
    for pattern in buy_patterns:
        if pattern in business:
            return "buy"
    for pattern in sell_patterns:
        if pattern in business:
            return "sell"
    return "other"
