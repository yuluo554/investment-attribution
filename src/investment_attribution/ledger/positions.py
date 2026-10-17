"""持仓批次账（FIFO）与移动加权账（WAVG），共享同一接口。

**金额全程整数分**，数量用 ``Decimal``（规范十进制文本最多 4 位小数）。
两种口径只在"卖出时取多少成本"上不同，买入 / 零成本份额 / 快照的骨架相同。

口径定义见 `data/README.md §4.1`（唯一定义处）；本模块只实现，不另立口径。
"""

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from ..ingest.values import canonical_number
from .model import allocate_cost

QUANTITY_EXP = Decimal("0.0001")
UNIT_COST_EXP = Decimal("0.000001")


def quantize_quantity(value):
    return value.quantize(QUANTITY_EXP, rounding=ROUND_DOWN)


def quantity_text(value):
    return canonical_number(quantize_quantity(value))


def unit_cost(cost_fen, quantity):
    """单位成本是展示值（6 位小数）；权威值是整数分 ``cost_fen``。"""
    if quantity <= 0:
        return None
    raw = Decimal(cost_fen) / quantity
    quantized = raw.quantize(UNIT_COST_EXP, rounding=ROUND_HALF_UP)
    return canonical_number(quantized)


def _source_payload(source):
    if source is None:
        return None
    return dict(source)


class TakeResult(object):
    def __init__(self, cost_fen, matched, unmatched, average_unit_cost=None):
        self.cost_fen = cost_fen
        self.matched = matched
        self.unmatched = unmatched
        self.average_unit_cost = average_unit_cost


class Book(object):
    """账本接口；``method`` 由子类固定。

    接口方法在基类不提供实现——子类必须覆盖；基类不抛"未实现"异常，
    避免把"抽象占位"混进可执行路径（入口只经 ``make_book`` 造具体账本）。
    """

    method = None
    quantity = Decimal("0")
    cost_fen = 0

    def add(self, quantity, cost_fen, source):
        raise ValueError("账本子类必须实现 add()")

    def add_free(self, quantity, source):
        raise ValueError("账本子类必须实现 add_free()")

    def take(self, quantity):
        raise ValueError("账本子类必须实现 take()")

    def lots(self):
        return []

    def summary(self):
        return {
            "quantity": quantity_text(self.quantity),
            "cost_fen": int(self.cost_fen),
            "unit_cost": unit_cost(self.cost_fen, self.quantity),
        }


class FifoBook(Book):
    """先进先出：卖出按买入时间升序消耗批次；每批成本含该批买入费用。"""

    method = "fifo"

    def __init__(self):
        self._lots = []  # [quantity Decimal, cost_fen int, source dict]

    @property
    def quantity(self):
        return sum((lot[0] for lot in self._lots), Decimal("0"))

    @property
    def cost_fen(self):
        return sum(lot[1] for lot in self._lots)

    def add(self, quantity, cost_fen, source):
        if quantity <= 0:
            return
        self._lots.append([quantize_quantity(quantity), int(cost_fen), _source_payload(source)])

    def add_free(self, quantity, source):
        """零成本份额（送股 / 拆分）：按比例放大各批数量，总成本不变。

        中间批次按比例向下取整到 4 位小数，余数归最后一批，保证
        各批数量之和**精确**等于原数量 + 本次增量。
        """
        quantity = quantize_quantity(quantity)
        if quantity <= 0:
            return
        total = self.quantity
        if total <= 0:
            self._lots.append([quantity, 0, _source_payload(source)])
            return
        remaining = quantity
        for index, lot in enumerate(self._lots):
            if index == len(self._lots) - 1:
                part = remaining
            else:
                part = quantize_quantity(quantity * lot[0] / total)
                if part > remaining:
                    part = remaining
            lot[0] += part
            remaining -= part

    def take(self, quantity):
        quantity = quantize_quantity(quantity)
        remaining = quantity
        cost = 0
        matched = []
        while remaining > 0 and self._lots:
            lot = self._lots[0]
            lot_quantity, lot_cost, lot_source = lot
            if lot_quantity <= remaining:
                cost += lot_cost
                matched.append(
                    {
                        "quantity": quantity_text(lot_quantity),
                        "cost_fen": int(lot_cost),
                        "unit_cost": unit_cost(lot_cost, lot_quantity),
                        "acquired": _source_payload(lot_source),
                    }
                )
                remaining -= lot_quantity
                self._lots.pop(0)
            else:
                part = allocate_cost(lot_cost, remaining, lot_quantity)
                lot[0] = quantize_quantity(lot_quantity - remaining)
                lot[1] = int(lot_cost - part)
                cost += part
                matched.append(
                    {
                        "quantity": quantity_text(remaining),
                        "cost_fen": int(part),
                        "unit_cost": unit_cost(part, remaining),
                        "acquired": _source_payload(lot_source),
                    }
                )
                remaining = Decimal("0")
        return TakeResult(int(cost), matched, quantity_text(remaining))

    def lots(self):
        return [
            {
                "quantity": quantity_text(lot[0]),
                "cost_fen": int(lot[1]),
                "unit_cost": unit_cost(lot[1], lot[0]),
                "acquired": _source_payload(lot[2]),
            }
            for lot in self._lots
        ]


class WavgBook(Book):
    """移动加权平均：每次买入后重算单位成本；卖出只按单位成本结转。"""

    method = "wavg"

    def __init__(self):
        self._quantity = Decimal("0")
        self._cost_fen = 0

    @property
    def quantity(self):
        return self._quantity

    @property
    def cost_fen(self):
        return self._cost_fen

    def add(self, quantity, cost_fen, source):
        if quantity <= 0:
            return
        self._quantity = quantize_quantity(self._quantity + quantity)
        self._cost_fen = int(self._cost_fen + int(cost_fen))

    def add_free(self, quantity, source):
        if quantity <= 0:
            return
        self._quantity = quantize_quantity(self._quantity + quantity)

    def take(self, quantity):
        quantity = quantize_quantity(quantity)
        if quantity <= 0:
            return TakeResult(0, [], "0", None)
        held = self._quantity
        if quantity >= held:
            cost = self._cost_fen
            average = unit_cost(cost, held)
            self._quantity = Decimal("0")
            self._cost_fen = 0
            matched = []
            if held > 0:
                matched.append(
                    {
                        "quantity": quantity_text(held),
                        "cost_fen": int(cost),
                        "unit_cost": average,
                        "acquired": None,
                    }
                )
            return TakeResult(int(cost), matched, quantity_text(quantity - held), average)
        average = unit_cost(self._cost_fen, held)
        part = allocate_cost(self._cost_fen, quantity, held)
        self._quantity = quantize_quantity(held - quantity)
        self._cost_fen = int(self._cost_fen - part)
        return TakeResult(
            int(part),
            [
                {
                    "quantity": quantity_text(quantity),
                    "cost_fen": int(part),
                    "unit_cost": average,
                    "acquired": None,
                }
            ],
            "0",
            average,
        )


BOOKS = {"fifo": FifoBook, "wavg": WavgBook}


def make_book(method):
    if method not in BOOKS:
        raise ValueError("未知成本口径 {!r}".format(method))
    return BOOKS[method]()
