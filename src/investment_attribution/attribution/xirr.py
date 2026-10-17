"""XIRR 求解：Newton–Raphson 优先，不收敛退二分法。

口径唯一定义处 = `data/README.md §4.2` 与 §4.2.1（M4 落地细则）。参数全部
来自 `data/rules/params.json` 的 ``xirr`` 段：初值、收敛阈值、最大迭代次数、
二分区间、NPV 容差、年基天数。引擎里**不写第二份数值**。

三条纪律：

- **确定性**：现金流按日期升序固定顺序累加，不使用随机初值、不并发求和，
  同一输入重复运行结果逐位一致；
- **无解不造数**：区间两端 NPV 同号即抛 ``XIRR_NO_SOLUTION``（退出码 1），
  **不输出近似值、不打印 NaN**；
- **单位**：现金流以**分**传入，NPV 换算成**元**计算，故 ``npv_tolerance``
  的 1e-6 对应 1e-6 元（分的量级下浮点无法表达更细的有效位）。
"""

from decimal import Decimal, ROUND_HALF_UP

from ..errors import DataError, DegradedError
from ..ingest.rules import read_json

XIRR_PARAM_KEYS = (
    "initial_guess",
    "rate_tolerance",
    "max_iterations",
    "year_basis_days",
    "bisection_lower",
    "bisection_upper",
    "npv_tolerance",
    "max_bisection_iterations",
)

XIRR_NO_SOLUTION = "XIRR_NO_SOLUTION"


def rate_text(value, places=12):
    """把浮点率值规范成固定小数位的十进制文本（保证产物字节稳定）。"""
    if value is None:
        return None
    quantized = Decimal(repr(float(value))).quantize(
        Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP
    )
    return format(quantized, "f")


def load_xirr_params(data_dir):
    """读取并校验参数包的 ``xirr`` 段；任何缺口都抛 ``DataError``。"""
    params = read_json(data_dir, "rules/params.json", "PARAMS_MISSING")
    section = params.get("xirr")
    if not isinstance(section, dict):
        raise DataError("params.xirr 缺失或不是对象", code="XIRR_PARAMS_MISSING")
    missing = [key for key in XIRR_PARAM_KEYS if key not in section]
    if missing:
        raise DataError(
            "params.xirr 缺字段：{}".format(", ".join(missing)),
            code="XIRR_PARAMS_INCOMPLETE",
        )
    loaded = {
        "initial_guess": float(section["initial_guess"]),
        "rate_tolerance": float(section["rate_tolerance"]),
        "max_iterations": int(section["max_iterations"]),
        "year_basis_days": int(section["year_basis_days"]),
        "bisection_lower": float(section["bisection_lower"]),
        "bisection_upper": float(section["bisection_upper"]),
        "npv_tolerance": float(section["npv_tolerance"]),
        "max_bisection_iterations": int(section["max_bisection_iterations"]),
    }
    if loaded["year_basis_days"] != 365:
        raise DataError(
            "params.xirr.year_basis_days 必须为 365，实际 {}".format(
                loaded["year_basis_days"]
            ),
            code="XIRR_PARAMS_DRIFT",
        )
    if loaded["bisection_lower"] <= -1.0:
        raise DataError(
            "params.xirr.bisection_lower 必须大于 -1", code="XIRR_PARAMS_DRIFT"
        )
    if loaded["bisection_upper"] <= loaded["bisection_lower"]:
        raise DataError(
            "params.xirr 二分区间上端必须大于下端", code="XIRR_PARAMS_DRIFT"
        )
    if loaded["max_iterations"] <= 0 or loaded["max_bisection_iterations"] <= 0:
        raise DataError(
            "params.xirr 迭代次数必须为正", code="XIRR_PARAMS_DRIFT"
        )
    loaded["raw"] = dict(section)
    return loaded


def to_series(cash_flows):
    """``[(date_text, amount_fen)]`` → ``[(days_from_first, amount_yuan)]``。

    按 ``(日期, 原始序)`` 升序排列，保证浮点累加顺序固定——顺序一变，
    末位就会漂，产物字节随之漂。
    """
    if not cash_flows:
        return []
    from datetime import date as _date

    def _parse(text):
        try:
            return _date.fromisoformat(text)
        except (TypeError, ValueError):
            raise DegradedError(
                "现金流日期不是 ISO 日期：{!r}".format(text), code="XIRR_DATE_INVALID"
            )

    ordered = sorted(
        enumerate(cash_flows), key=lambda pair: (pair[1][0], pair[0])
    )
    base = _parse(ordered[0][1][0])
    series = []
    for _, (date_text, amount_fen) in ordered:
        days = (_parse(date_text) - base).days
        series.append((days, amount_fen / 100.0))
    return series


def _npv(rate, series, year_basis):
    total = 0.0
    factor = 1.0 + rate
    for days, amount in series:
        total += amount / (factor ** (days / float(year_basis)))
    return total


def _dnpv(rate, series, year_basis):
    total = 0.0
    factor = 1.0 + rate
    for days, amount in series:
        exponent = days / float(year_basis)
        total -= exponent * amount / (factor ** (exponent + 1.0))
    return total


def _bisect(series, params):
    lower = params["bisection_lower"]
    upper = params["bisection_upper"]
    f_lower = _npv(lower, series, params["year_basis_days"])
    f_upper = _npv(upper, series, params["year_basis_days"])
    if f_lower == 0.0:
        return lower, 0, 0.0
    if f_upper == 0.0:
        return upper, 0, 0.0
    if f_lower * f_upper > 0.0:
        return None, 0, None
    midpoint = 0.0
    npv_value = None
    for iteration in range(1, params["max_bisection_iterations"] + 1):
        midpoint = (lower + upper) / 2.0
        npv_value = _npv(midpoint, series, params["year_basis_days"])
        if abs(npv_value) < params["npv_tolerance"]:
            return midpoint, iteration, npv_value
        if f_lower * npv_value < 0.0:
            upper = midpoint
            f_upper = npv_value
        else:
            lower = midpoint
            f_lower = npv_value
    if abs(f_upper) > abs(f_lower):
        return lower, params["max_bisection_iterations"], f_lower
    return midpoint, params["max_bisection_iterations"], npv_value


class XirrSolution(object):
    """一次 XIRR 求解的完整结论（含解法与残差，便于审计）。"""

    def __init__(self, rate, solver, iterations, npv_value, series_length):
        self.rate = rate
        self.solver = solver
        self.iterations = iterations
        self.npv_value = npv_value
        self.series_length = series_length

    def as_dict(self):
        return {
            "rate": rate_text(self.rate),
            "solver": self.solver,
            "iterations": self.iterations,
            "npv_at_rate": rate_text(self.npv_value, places=6),
        }


def solve_xirr(cash_flows, params):
    """求解 XIRR；无解抛 ``XIRR_NO_SOLUTION``（退出码 1）。

    ``cash_flows`` = ``[(date_text, amount_fen)]``，投资者口径：
    投入为负、收回与期末市值为正。
    """
    series = to_series(cash_flows)
    nonzero = [item for item in series if item[1] != 0.0]
    if len(nonzero) < 2:
        raise DegradedError(
            "现金流不足两笔（无法构成收益率方程）", code=XIRR_NO_SOLUTION
        )
    if all(item[1] < 0 for item in nonzero) or all(item[1] > 0 for item in nonzero):
        raise DegradedError(
            "现金流符号单一，方程在定义域内无解", code=XIRR_NO_SOLUTION
        )

    rate = params["initial_guess"]
    for iteration in range(1, params["max_iterations"] + 1):
        factor = 1.0 + rate
        if factor <= 0.0:
            break
        f_value = _npv(rate, series, params["year_basis_days"])
        d_value = _dnpv(rate, series, params["year_basis_days"])
        if d_value == 0.0 or d_value != d_value or abs(d_value) == float("inf"):
            break
        step = f_value / d_value
        next_rate = rate - step
        if next_rate <= -1.0 or next_rate != next_rate or abs(next_rate) == float("inf"):
            break
        if abs(next_rate - rate) < params["rate_tolerance"]:
            return XirrSolution(
                next_rate,
                "newton",
                iteration,
                _npv(next_rate, series, params["year_basis_days"]),
                len(series),
            )
        rate = next_rate

    rate, iterations, npv_value = _bisect(series, params)
    if rate is None:
        raise DegradedError(
            "二分区间两端 NPV 同号，判定无解", code=XIRR_NO_SOLUTION
        )
    return XirrSolution(rate, "bisection", iterations, npv_value, len(series))
