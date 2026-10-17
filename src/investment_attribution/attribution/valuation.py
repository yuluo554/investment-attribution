"""估值序列输入（期末市值 / TWR 估值点共用一份输入）。

格式（CSV，逗号或 Tab 分隔，编码按 K6 顺序探测）：::

    date,symbol,name,quantity,price
    2025-01-06,699001,示例科技甲,1500,10.20
    ...
    2025-12-31,699001,示例科技甲,500,10.20

语义（M4 细则见 `data/README.md §4.2.1`）：

- **价格是权威**：某日某标的的市价由本表给出，引擎绝不插值、绝不沿用前一日价格；
- **数量是交叉核对**：``quantity`` 可选；给了就与台账持仓逐日比对，超出
  1 股容差落 ``VALUATION_QUANTITY_MISMATCH``（不让"估值表说持有 2000 股、
  台账说 7000 股"这种矛盾静默通过）；
- **最后一日 = 期末估值日**：XIRR 的期末市值记在这一天。

日期列名支持中英两套（``date`` / ``日期`` 等），因为这是**用户自备输入**，
不是券商导出格式，不能要求人家记英文列名。
"""

import hashlib
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from ..errors import InputError
from ..ingest.csvio import parse_rows
from ..ingest.encoding import decode_bytes
from ..ingest.values import FieldValueError, parse_date, parse_price, parse_quantity

HEADER_ALIASES = {
    "date": "date",
    "日期": "date",
    "估值日": "date",
    "symbol": "symbol",
    "证券代码": "symbol",
    "代码": "symbol",
    "name": "name",
    "证券名称": "name",
    "名称": "name",
    "quantity": "quantity",
    "数量": "quantity",
    "持仓数量": "quantity",
    "price": "price",
    "价格": "price",
    "市价": "price",
    "收盘价": "price",
}

REQUIRED_FIELDS = ("date", "symbol", "price")
OPTIONAL_FIELDS = ("name", "quantity")


class Valuation(object):
    """估值序列：``(date, symbol) → price``（附可选数量交叉核对）。"""

    def __init__(
        self,
        path,
        sha256,
        prices,
        quantities,
        names,
        line_numbers,
        row_count,
        unknown_columns=(),
    ):
        self.path = path
        self.sha256 = sha256
        self.prices = prices
        self.quantities = quantities
        self.names = names
        self.line_numbers = line_numbers
        self.row_count = row_count
        self.unknown_columns = list(unknown_columns)
        self.dates = sorted({date for date, _ in prices})
        self.valuation_date = self.dates[-1] if self.dates else None
        self.symbols = sorted({symbol for _, symbol in prices})

    def price(self, date, symbol):
        return self.prices.get((date, symbol))

    def quantity(self, date, symbol):
        return self.quantities.get((date, symbol))

    def line_of(self, date, symbol):
        return self.line_numbers.get((date, symbol))

    def describe(self):
        return {
            "path": self.path,
            "sha256": self.sha256,
            "row_count": self.row_count,
            "date_count": len(self.dates),
            "valuation_date": self.valuation_date,
            "symbols": self.symbols,
        }


def _delimiter_of(text):
    for line in text.splitlines():
        if not line.strip():
            continue
        return "\t" if ("\t" in line and "," not in line) else ","
    return ","


def _read_text(path):
    target = Path(path)
    if not target.is_file():
        raise InputError(
            "估值表不存在：{}（M4 需要期末市值才能算未实现与 XIRR）".format(target),
            code="VALUATION_MISSING",
        )
    text, _ = decode_bytes(target.read_bytes(), str(target))
    return text


def load_valuation(path):
    """读取估值序列；结构缺陷一律 ``InputError``（退出码 2）。"""
    target = Path(path)
    text = _read_text(target)
    raw = target.read_bytes()
    rows = parse_rows(text, _delimiter_of(text))
    if not rows:
        raise InputError("估值表是空文件：{}".format(target), code="VALUATION_EMPTY")

    header_cells = [cell.strip() for cell in rows[0][1]]
    mapping = {}
    unknown = []
    for index, label in enumerate(header_cells):
        field = HEADER_ALIASES.get(label.strip().lower()) or HEADER_ALIASES.get(label.strip())
        if field is None:
            if label:
                unknown.append(label)
            continue
        if field in mapping:
            raise InputError(
                "估值表列名重复：{}".format(label), code="VALUATION_HEADER_INVALID"
            )
        mapping[field] = index

    missing = [field for field in REQUIRED_FIELDS if field not in mapping]
    if missing:
        raise InputError(
            "估值表缺列：{}（需要 date/symbol/price）".format(", ".join(missing)),
            code="VALUATION_HEADER_INVALID",
        )

    prices = {}
    quantities = {}
    names = {}
    line_numbers = {}
    for line_no, cells in rows[1:]:
        if not any(cell.strip() for cell in cells):
            continue

        def cell(field, default=None):
            index = mapping.get(field)
            if index is None or index >= len(cells):
                return default
            return cells[index]

        try:
            date_text = parse_date(cell("date"))
        except FieldValueError as exc:
            raise InputError(
                "估值表第 {} 行日期不可解析：{}".format(line_no, exc),
                code="VALUATION_ROW_INVALID",
            )
        symbol = (cell("symbol") or "").strip()
        if not symbol:
            raise InputError(
                "估值表第 {} 行缺证券代码".format(line_no), code="VALUATION_ROW_INVALID"
            )
        try:
            price_text = parse_price(cell("price"))
        except FieldValueError as exc:
            raise InputError(
                "估值表第 {} 行价格不可解析：{}".format(line_no, exc),
                code="VALUATION_ROW_INVALID",
            )
        if price_text is None:
            raise InputError(
                "估值表第 {} 行价格为空".format(line_no), code="VALUATION_ROW_INVALID"
            )
        price = Decimal(price_text)
        if price <= 0:
            raise InputError(
                "估值表第 {} 行价格必须为正，实际 {}".format(line_no, price_text),
                code="VALUATION_ROW_INVALID",
            )
        key = (date_text, symbol)
        if key in prices:
            raise InputError(
                "估值表重复行：{} {}".format(date_text, symbol),
                code="VALUATION_DUPLICATE_ROW",
            )
        prices[key] = price
        line_numbers[key] = line_no
        raw_name = cell("name")
        if raw_name is not None and raw_name.strip():
            names[key] = raw_name.strip()
        raw_quantity = cell("quantity")
        if raw_quantity is not None and raw_quantity.strip():
            try:
                quantity_text = parse_quantity(raw_quantity)
            except FieldValueError as exc:
                raise InputError(
                    "估值表第 {} 行数量不可解析：{}".format(line_no, exc),
                    code="VALUATION_ROW_INVALID",
                )
            quantities[key] = Decimal(quantity_text)

    if not prices:
        raise InputError(
            "估值表只有表头、没有数据行：{}".format(target), code="VALUATION_EMPTY"
        )

    valuation = Valuation(
        path=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        prices=prices,
        quantities=quantities,
        names=names,
        line_numbers=line_numbers,
        row_count=len(prices),
        unknown_columns=sorted(unknown),
    )
    return valuation


def market_value_fen(quantity, price):
    """数量 × 价格 → 整数分（四舍五入到分，与成本口径同一舍入规则）。"""
    value = Decimal(quantity) * Decimal(price) * Decimal(100)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
