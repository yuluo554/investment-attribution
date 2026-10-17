"""可选本地基准指数序列（缺失即不显示超额列，命令仍成功）。

格式（CSV，逗号或 Tab 分隔）：::

    date,value
    2025-01-02,1000.00
    ...
    2025-12-31,1060.00

口径（`data/README.md §4.2.1`）：基准收益率在**组合的起止估值日**之间按
365 天年基折年，与 XIRR 同口径比较；两端日期缺失即落 ``BENCHMARK_DATE_MISSING``，
**不插值、不外推**。基准是可选输入：文件没给就不显示超额列，命令仍 ``exit 0``。
"""

import hashlib
from decimal import Decimal
from pathlib import Path

from ..errors import InputError
from ..ingest.csvio import parse_rows
from ..ingest.encoding import decode_bytes
from ..ingest.values import FieldValueError, parse_date, parse_price

VALUE_LABELS = ("value", "close", "index", "price", "收盘", "点位", "指数")


class Benchmark(object):
    """基准指数序列：``date → level``。"""

    def __init__(self, path, sha256, points, row_count):
        self.path = path
        self.sha256 = sha256
        self.points = points
        self.row_count = row_count
        self.dates = sorted(points)

    def value_at(self, date):
        return self.points.get(date)

    def describe(self):
        return {
            "path": self.path,
            "sha256": self.sha256,
            "row_count": self.row_count,
            "start_date": self.dates[0] if self.dates else None,
            "end_date": self.dates[-1] if self.dates else None,
        }


def _delimiter_of(text):
    for line in text.splitlines():
        if not line.strip():
            continue
        return "\t" if ("\t" in line and "," not in line) else ","
    return ","


def load_benchmark(path):
    """读取基准序列；结构缺陷抛 ``InputError``（退出码 2）。"""
    target = Path(path)
    if not target.is_file():
        raise InputError(
            "基准文件不存在：{}".format(target), code="BENCHMARK_MISSING"
        )
    raw = target.read_bytes()
    text, _ = decode_bytes(raw, str(target))
    rows = parse_rows(text, _delimiter_of(text))
    if not rows:
        raise InputError("基准文件是空文件：{}".format(target), code="BENCHMARK_EMPTY")

    header = [cell.strip() for cell in rows[0][1]]
    lower = [label.lower() for label in header]
    if "date" not in lower and "日期" not in header:
        raise InputError(
            "基准文件缺 date 列：{}".format(target), code="BENCHMARK_HEADER_INVALID"
        )
    date_index = lower.index("date") if "date" in lower else header.index("日期")
    value_index = None
    for label in VALUE_LABELS:
        if label in lower:
            value_index = lower.index(label)
            break
        if label in header:
            value_index = header.index(label)
            break
    if value_index is None:
        raise InputError(
            "基准文件缺数值列（value/close/index 之一）：{}".format(target),
            code="BENCHMARK_HEADER_INVALID",
        )

    points = {}
    for line_no, cells in rows[1:]:
        if not any(cell.strip() for cell in cells):
            continue
        if date_index >= len(cells) or value_index >= len(cells):
            raise InputError(
                "基准文件第 {} 行列数不足".format(line_no), code="BENCHMARK_ROW_INVALID"
            )
        try:
            date_text = parse_date(cells[date_index])
            value_text = parse_price(cells[value_index])
        except FieldValueError as exc:
            raise InputError(
                "基准文件第 {} 行不可解析：{}".format(line_no, exc),
                code="BENCHMARK_ROW_INVALID",
            )
        if value_text is None:
            raise InputError(
                "基准文件第 {} 行数值为空".format(line_no), code="BENCHMARK_ROW_INVALID"
            )
        value = Decimal(value_text)
        if value <= 0:
            raise InputError(
                "基准文件第 {} 行数值必须为正".format(line_no),
                code="BENCHMARK_ROW_INVALID",
            )
        if date_text in points:
            raise InputError(
                "基准文件重复日期：{}".format(date_text), code="BENCHMARK_DUPLICATE_DATE"
            )
        points[date_text] = value

    if len(points) < 2:
        raise InputError(
            "基准文件至少需要两个日期点：{}".format(target), code="BENCHMARK_TOO_SHORT"
        )
    return Benchmark(
        path=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        points=points,
        row_count=len(points),
    )


def period_return(benchmark, start_date, end_date, year_basis_days=365):
    """基准在 ``[start_date, end_date]`` 上的累计与折年收益率。

    两端日期必须**精确存在**；缺失返回 ``None``（调用方落
    ``BENCHMARK_DATE_MISSING``，不做插值）。
    """
    start = benchmark.value_at(start_date)
    end = benchmark.value_at(end_date)
    if start is None or end is None or start <= 0:
        return None
    cumulative = end / start - Decimal(1)
    days = _days_between(start_date, end_date)
    if days <= 0:
        return {"cumulative": cumulative, "annualized": cumulative, "days": days}
    annualized = (Decimal(1) + cumulative) ** (
        Decimal(year_basis_days) / Decimal(days)
    ) - Decimal(1)
    return {"cumulative": cumulative, "annualized": annualized, "days": days}


def _days_between(start_date, end_date):
    from datetime import date as _date

    return (_date.fromisoformat(end_date) - _date.fromisoformat(start_date)).days
