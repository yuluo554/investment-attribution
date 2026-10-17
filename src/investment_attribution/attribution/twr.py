"""TWR（时间加权收益率）：按估值点连乘分段收益。

口径唯一定义处 = `data/README.md §4.3` 与 §4.3.1（M4 落地细则）：

- **分段**：估值点由调用方给出（``cashflow`` 口径 = 外部现金流发生日 +
  估值日；``daily`` 口径 = 每个交易日快照 + 估值日），本模块只做连乘；
- **每段收益**：``r = (V_end − CF_end) / V_start − 1``，其中 ``CF_end`` 是
  该估值点的**组合口径**外部净流入（转入为正、转出为负）；
- **首点是基准**：组合从零开始时，第一笔投入不产生收益率可测量的区间，
  因此首个估值点只做基准，不参与连乘；
- **不插值**：估值点缺市值是调用方的事（报 ``VALUATION_MISSING``），
  本模块拿到的一定是逐点实测值。
"""

from .xirr import rate_text

TWR_ZERO_BASE = "TWR_ZERO_BASE"


def compute_twr(points):
    """``[(date, value_fen, flow_fen)]`` 升序 → ``(cumulative, segments)``。

    首点为基准（``nav_index = 1``）；基值为非正数时抛 ``TWR_ZERO_BASE``
    （除零不是收益率，坚决不输出）。
    """
    from ..errors import DegradedError

    if not points:
        return 0.0, []
    index = 1.0
    segments = []
    for position in range(1, len(points)):
        previous = points[position - 1]
        current = points[position]
        if previous[1] <= 0:
            raise DegradedError(
                "TWR 段起点市值为 {}（{}），无法计算段收益".format(
                    previous[1], previous[0]
                ),
                code=TWR_ZERO_BASE,
            )
        period = (current[1] - current[2]) / float(previous[1]) - 1.0
        index *= 1.0 + period
        segments.append(
            {
                "date": current[0],
                "previous_date": previous[0],
                "value_before_fen": int(previous[1]),
                "value_fen": int(current[1]),
                "flow_fen": int(current[2]),
                "period_return": rate_text(period),
                "nav_index": rate_text(index),
            }
        )
    return index - 1.0, segments


def basis_label(basis):
    return {
        "cashflow": "外部现金流发生日",
        "daily": "每日持仓快照",
    }.get(basis, basis)


def basis_notice(basis):
    if basis == "cashflow":
        return (
            "TWR 估值点口径 = 外部现金流发生日（含期末估值日）；"
            "两种口径都不插值，缺市值日报 VALUATION_MISSING。"
        )
    return (
        "TWR 估值点口径 = 每个有流水的交易日快照 × 当日市值（含期末估值日）；"
        "两种口径都不插值，缺市值日报 VALUATION_MISSING。"
    )
