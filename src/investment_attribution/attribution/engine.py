"""收益归因引擎（M4）：现金流还原 → XIRR / TWR / 拆分 / 费用拖累 / 多维分解。

**组合口径**（唯一定义处 = `data/README.md §4.2.1` / §4.3.1）：

- 组合 = 证券持仓；**期末价值 = 估值日证券市值**（估值表给价、台账给量）；
- **外部现金流** = 账户边界上的资金划转（银证转入/转出、内部划转），投资者
  口径：**投入为负、收回为正**；
- 成交与分红是组合内部的价值转换与实现，**不进外部现金流**——否则同一笔钱
  会被计两次（转入一次、买入一次），XIRR 立刻失去意义；
- **待确认现金行**（规则表认不出的 other 行）计入 ``other_income`` 并落
  warning：它是真实发生过的现金，不当"没这回事"，也不当"行情赚来的"。

**守恒断言**（两套独立算法互证）：

    期末总资产 − 净投入资金 == 已实现（含分红） + 未实现

左边走现金流口径、右边走成本口径，差 1 分以上即落 ``SPLIT_NOT_CONSERVED``。

口径参数（成本法、TWR 估值点、XIRR 参数）全部来自参数包与 CLI 显式参数；
本模块不读时钟、不联网、不改写流水。
"""

from bisect import bisect_right
from decimal import Decimal

from ..actions import build_actions
from ..errors import DegradedError, InputError, UsageError
from ..ingest.rules import read_json
from ..ledger import build_holdings
from ..ledger.engine import load_cost_params, read_ledger_file, require_method
from ..serialize import write_json_atomic
from .benchmark import load_benchmark, period_return
from .industries import load_industries
from .twr import TWR_ZERO_BASE, basis_label, basis_notice, compute_twr
from .valuation import load_valuation, market_value_fen
from .xirr import XIRR_NO_SOLUTION, load_xirr_params, rate_text, solve_xirr

ATTRIBUTION_FILE = "attribution.json"

SPLIT_NOT_CONSERVED = "SPLIT_NOT_CONSERVED"
FEE_TOTAL_MISMATCH = "FEE_TOTAL_MISMATCH"
VALUATION_MISSING = "VALUATION_MISSING"
VALUATION_QUANTITY_MISMATCH = "VALUATION_QUANTITY_MISMATCH"
UNCLASSIFIED_CASH_ROW = "UNCLASSIFIED_CASH_ROW"
BENCHMARK_DATE_MISSING = "BENCHMARK_DATE_MISSING"
DECOMPOSITION_NOT_CONSERVED = "DECOMPOSITION_NOT_CONSERVED"

FEE_FIELDS = ("commission", "stamp_tax", "transfer_fee", "other_fee", "unallocated")
INCOME_KINDS = ("dividend", "reinvested_dividend", "unclassified")
MERGED_LABEL = "合并视图"


class _MergedIdentity(object):
    """合并视图的行标（无账户身份，只有名字）。"""

    key = "merged"
    label = MERGED_LABEL


_MERGED_VIEW = _MergedIdentity()


def _fen(value):
    return int(value or 0)


def _decimal(text):
    if text is None:
        return Decimal("0")
    return Decimal(str(text))


def _text(value):
    return format(value.normalize(), "f")


class AttributionResult(object):
    def __init__(self, artifact, method, degraded):
        self.artifact = artifact
        self.method = method
        self.degraded = degraded

    @property
    def exit_code(self):
        from ..exit_codes import EXIT_DEGRADED, EXIT_OK

        return EXIT_DEGRADED if self.degraded else EXIT_OK


# ---------------------------------------------------------------------------
# M3 → 行级归属
# ---------------------------------------------------------------------------


def _classification_index(actions):
    """``(source_file, line_no) → 归属``：规则表是业务语义的唯一裁决者。"""
    index = {}
    for item in actions.get("non_actions") or []:
        index[(item.get("source_file"), item.get("source_line_no"))] = {
            "bucket": "capital",
            "matched_pattern": item.get("matched_pattern"),
        }
    for item in actions.get("events") or []:
        index[(item.get("source_file"), item.get("source_line_no"))] = {
            "bucket": "event",
            "event_type": item.get("event_type"),
            "rule_id": item.get("rule_id"),
            "matched_pattern": item.get("matched_pattern"),
        }
    for item in actions.get("pending") or []:
        index.setdefault(
            (item.get("source_file"), item.get("source_line_no")),
            {"bucket": "pending", "reason": item.get("reason")},
        )
    return index


def _classify_events(events, classification, warnings):
    """把一行流水归到：外部现金流 / 收益 / 内部动作。"""
    capital = []
    income = []
    for event in events:
        verdict = classification.get(
            (event.get("source_file"), event.get("source_line_no"))
        )
        kind = event.get("kind")
        settlement = _fen(event.get("settlement_fen"))
        if kind == "cash_event":
            if verdict is not None and verdict.get("bucket") == "capital":
                capital.append(
                    {
                        "date": event["trade_date"],
                        "settlement_fen": settlement,
                        "business": event.get("business"),
                        "matched_pattern": verdict.get("matched_pattern"),
                        "source_file": event.get("source_file"),
                        "source_line_no": event.get("source_line_no"),
                    }
                )
            elif verdict is not None and verdict.get("bucket") == "event":
                income.append(
                    {
                        "date": event["trade_date"],
                        "amount_fen": settlement,
                        "kind": "dividend",
                        "event_type": verdict.get("event_type"),
                        "symbol": event.get("symbol"),
                        "business": event.get("business"),
                        "source_file": event.get("source_file"),
                        "source_line_no": event.get("source_line_no"),
                    }
                )
            else:
                income.append(
                    {
                        "date": event["trade_date"],
                        "amount_fen": settlement,
                        "kind": "unclassified",
                        "event_type": None,
                        "symbol": event.get("symbol"),
                        "business": event.get("business"),
                        "source_file": event.get("source_file"),
                        "source_line_no": event.get("source_line_no"),
                    }
                )
                warnings.append(
                    {
                        "code": UNCLASSIFIED_CASH_ROW,
                        "message": (
                            "规则表未识别的现金行按『其他现金收入』计入总收益，"
                            "同时进 corporate-actions 待确认队列"
                        ),
                        "reason": (verdict or {}).get("reason") or "UNKNOWN",
                        "business": event.get("business"),
                        "amount_fen": settlement,
                        "source_file": event.get("source_file"),
                        "source_line_no": event.get("source_line_no"),
                    }
                )
        elif kind == "receipt_valued":
            income.append(
                {
                    "date": event["trade_date"],
                    "amount_fen": _fen(event.get("amount_fen")),
                    "kind": "reinvested_dividend",
                    "event_type": "reinvested_dividend",
                    "symbol": event.get("symbol"),
                    "business": event.get("business"),
                    "source_file": event.get("source_file"),
                    "source_line_no": event.get("source_line_no"),
                }
            )
    return capital, income


def _capital_flows(capital, opening_cash, first_date):
    """投资者口径：投入为负、收回为正；期初资金视同一开始就已投入。"""
    flows = []
    for row in capital:
        flows.append(
            {
                "date": row["date"],
                "amount_fen": -row["settlement_fen"],
                "kind": "capital_in" if row["settlement_fen"] > 0 else "capital_out",
                "business": row["business"],
                "matched_pattern": row["matched_pattern"],
                "source_file": row["source_file"],
                "source_line_no": row["source_line_no"],
            }
        )
    if opening_cash and first_date:
        flows.append(
            {
                "date": first_date,
                "amount_fen": -opening_cash,
                "kind": "opening_cash",
                "business": "期初资金余额",
                "matched_pattern": None,
                "source_file": None,
                "source_line_no": None,
            }
        )
    return flows


# ---------------------------------------------------------------------------
# 时点取值：现金余额链 + 每日持仓快照
# ---------------------------------------------------------------------------


class _Timeline(object):
    def __init__(self, snapshot_dates, positions_by_date, cash_dates, cash_amounts, opening_cash):
        self.snapshot_dates = snapshot_dates
        self.positions_by_date = positions_by_date
        self.cash_dates = cash_dates
        self.cash_amounts = cash_amounts
        self.opening_cash = opening_cash

    def positions_at(self, date):
        index = bisect_right(self.snapshot_dates, date)
        if index == 0:
            return {}
        return self.positions_by_date.get(self.snapshot_dates[index - 1], {})

    def cash_at(self, date):
        if not self.cash_dates:
            return self.opening_cash
        index = bisect_right(self.cash_dates, date)
        if index == 0:
            return self.opening_cash
        return self.cash_amounts[index - 1]


class _MergedTimeline(object):
    def __init__(self, timelines, opening_cash):
        self.timelines = timelines
        self.opening_cash = opening_cash

    def positions_at(self, date):
        merged = {}
        for timeline in self.timelines:
            for symbol, quantity in timeline.positions_at(date).items():
                merged[symbol] = merged.get(symbol, Decimal("0")) + quantity
        return merged

    def cash_at(self, date):
        return sum(timeline.cash_at(date) for timeline in self.timelines)


def _timeline_data(holdings):
    snapshot_dates = [item["date"] for item in holdings["daily_snapshots"]]
    positions = {}
    for snapshot in holdings["daily_snapshots"]:
        date = snapshot["date"]
        for entry in snapshot["positions"]:
            positions.setdefault(entry["account_key"], {}).setdefault(date, {})[
                entry["symbol"]
            ] = _decimal(entry["quantity"])
    cash = {}
    for event in holdings["events"]:
        dates, amounts = cash.setdefault(event["account_key"], ([], []))
        date = event["trade_date"]
        if dates and dates[-1] == date:
            amounts[-1] = _fen(event["cash_after_fen"])
        else:
            dates.append(date)
            amounts.append(_fen(event["cash_after_fen"]))
    return snapshot_dates, positions, cash


def _securities_value(timeline, date, valuation, missing, label):
    """某视图在 ``date`` 的证券市值（fen）；缺价记 ``missing`` 并返回 None。"""
    total = 0
    complete = True
    for symbol, quantity in sorted(timeline.positions_at(date).items()):
        if quantity <= 0:
            continue
        price = valuation.price(date, symbol)
        if price is None:
            record = (date, symbol, label)
            if record not in missing:
                missing.append(record)
            complete = False
            continue
        total += market_value_fen(quantity, price)
    return total if complete else None


def _positions_detail(positions, valuation, missing, label):
    rows = []
    complete = True
    for position in positions:
        quantity = _decimal(position.get("quantity"))
        if quantity <= 0:
            continue
        symbol = position["symbol"]
        price = valuation.price(valuation.valuation_date, symbol)
        if price is None:
            record = (valuation.valuation_date, symbol, label)
            if record not in missing:
                missing.append(record)
            complete = False
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": position.get("name"),
                "quantity": position.get("quantity"),
                "price": _text(price),
                "market_value_fen": market_value_fen(quantity, price),
                "cost_fen": _fen(position.get("cost_fen")),
                "source_line_no": valuation.line_of(valuation.valuation_date, symbol),
            }
        )
    return rows, complete


def _twr_points_for(
    timeline, basis, capital, valuation_date, snapshot_dates, valuation, missing, label, first_date
):
    if basis == "cashflow":
        dates = sorted({row["date"] for row in capital} | {valuation_date})
    else:
        dates = sorted(set(snapshot_dates) | {valuation_date})
    if first_date:
        dates = [date for date in dates if date >= first_date]
    if not dates:
        return []
    flows_by_date = {}
    for row in capital:
        flows_by_date[row["date"]] = flows_by_date.get(row["date"], 0) + row[
            "settlement_fen"
        ]
    points = []
    for date in dates:
        securities = _securities_value(timeline, date, valuation, missing, label)
        if securities is None:
            return None
        points.append(
            (
                date,
                int(timeline.cash_at(date) + securities),
                int(flows_by_date.get(date, 0)),
            )
        )
    return points


# ---------------------------------------------------------------------------
# 单账户 / 合并视图求值
# ---------------------------------------------------------------------------


def _evaluate_account(
    view,
    account,
    capital,
    income,
    positions,
    timeline,
    valuation,
    missing,
    sections,
    twr_basis,
    snapshot_dates,
    xirr_params,
    problems,
):
    detail, _ = _positions_detail(
        [item for item in positions if item["account_key"] == view.key],
        valuation,
        missing,
        view.label,
    )
    securities_mv = sum(row["market_value_fen"] for row in detail)
    open_cost = _fen(account.get("position_cost_fen"))
    realized_sells = _fen(account.get("realized_net_fen"))
    income_by_kind = {name: 0 for name in INCOME_KINDS}
    for row in income:
        bucket = row["kind"] if row["kind"] in income_by_kind else "unclassified"
        income_by_kind[bucket] += row["amount_fen"]
    income_total = sum(income_by_kind.values())
    unrealized = securities_mv - open_cost
    realized_total = realized_sells + income_total
    total_return = realized_total + unrealized

    opening_cash = _fen(account.get("opening_cash_fen"))
    contributions = sum(
        row["settlement_fen"] for row in capital if row["settlement_fen"] > 0
    )
    withdrawals = sum(
        -row["settlement_fen"] for row in capital if row["settlement_fen"] < 0
    )
    if opening_cash > 0:
        contributions += opening_cash
    net_contribution = contributions - withdrawals
    cash_end = opening_cash + _fen(account.get("net_cash_flow_fen"))
    terminal_total = cash_end + securities_mv
    difference = terminal_total - net_contribution - total_return
    conserved = abs(difference) <= 1

    first_date = view.events[0]["trade_date"] if view.events else None
    flows = _capital_flows(capital, opening_cash, first_date)
    flows.append(
        {
            "date": valuation.valuation_date,
            "amount_fen": terminal_total,
            "kind": "terminal_value",
            "business": "期末市值",
            "matched_pattern": None,
            "source_file": valuation.path,
            "source_line_no": valuation.line_of(
                valuation.valuation_date, detail[0]["symbol"] if detail else ""
            ),
        }
    )

    entry = {
        "account_id": view.account_id,
        "account_alias": view.alias,
        "account_status": view.status,
        "account_key": view.key,
        "label": view.label,
        "capital": capital,
        "income": income,
        "positions": detail,
        "all_positions": [item for item in positions if item["account_key"] == view.key],
        "securities_market_value_fen": securities_mv,
        "cash_end_fen": cash_end,
        "terminal_total_fen": terminal_total,
        "opening_cash_fen": opening_cash,
        "contributions_fen": contributions,
        "withdrawals_fen": withdrawals,
        "net_contribution_fen": net_contribution,
        "realized_sells_fen": realized_sells,
        "income_by_kind": income_by_kind,
        "open_cost_fen": open_cost,
        "unrealized_fen": unrealized,
        "realized_total_fen": realized_total,
        "total_return_fen": total_return,
        "difference_fen": difference,
        "conserved": conserved,
        "fees": {
            "total_fen": sum(
                _fen(value) for value in (account.get("fees_fen") or {}).values()
            ),
            "by_type_fen": {
                name: _fen((account.get("fees_fen") or {}).get(name))
                for name in FEE_FIELDS
            },
        },
        "cash_flows": flows,
    }
    entry["pnl"] = _pnl_block(entry, income)
    if not conserved:
        problems.append(
            {
                "code": SPLIT_NOT_CONSERVED,
                "message": (
                    "拆分守恒被打破（{}）：期末总资产 − 净投入 = {} 分，"
                    "已实现+未实现 = {} 分，差 {} 分".format(
                        view.label,
                        terminal_total - net_contribution,
                        total_return,
                        difference,
                    )
                ),
                "account_key": view.key,
            }
        )
    if "xirr" in sections:
        entry["xirr"] = _solve_xirr_for(flows, xirr_params, view, problems)
    if "twr" in sections:
        points = _twr_points_for(
            timeline,
            twr_basis,
            capital,
            valuation.valuation_date,
            snapshot_dates,
            valuation,
            missing,
            view.label,
            first_date,
        )
        entry["twr"] = _solve_twr_for(points, twr_basis, view, problems)
    return entry


def _pnl_block(entry, income):
    label = entry["label"]
    return {
        "account_id": entry["account_id"],
        "account_alias": entry["account_alias"],
        "account_status": entry["account_status"],
        "account_key": entry["account_key"],
        "label": label,
        "realized_from_sells_fen": entry["realized_sells_fen"],
        "dividend_income_fen": entry["income_by_kind"]["dividend"],
        "reinvested_income_fen": entry["income_by_kind"]["reinvested_dividend"],
        "other_income_fen": entry["income_by_kind"]["unclassified"],
        "realized_total_fen": entry["realized_total_fen"],
        "unrealized_fen": entry["unrealized_fen"],
        "total_return_fen": entry["total_return_fen"],
        "net_contribution_fen": entry["net_contribution_fen"],
        "terminal_cash_fen": entry["cash_end_fen"],
        "terminal_market_value_fen": entry["securities_market_value_fen"],
        "terminal_total_fen": entry["terminal_total_fen"],
        "difference_fen": entry["difference_fen"],
        "conserved": entry["conserved"],
        "fees_fen": entry["fees"]["total_fen"],
        "income_rows": income,
        "positions": entry["positions"],
    }


def _aggregate_merged(
    merged_timeline, entries, merged_opening, valuation, missing, sections, twr_basis, snapshot_dates, xirr_params, problems
):
    capital = []
    income = []
    positions = []
    for entry in entries:
        capital.extend(entry["capital"])
        income.extend(entry["income"])
        positions.extend(entry["positions"])
    securities_mv = sum(entry["securities_market_value_fen"] for entry in entries)
    open_cost = sum(entry["open_cost_fen"] for entry in entries)
    realized_sells = sum(entry["realized_sells_fen"] for entry in entries)
    income_by_kind = {
        name: sum(entry["income_by_kind"][name] for entry in entries)
        for name in INCOME_KINDS
    }
    income_total = sum(income_by_kind.values())
    unrealized = securities_mv - open_cost
    realized_total = realized_sells + income_total
    total_return = realized_total + unrealized
    contributions = sum(entry["contributions_fen"] for entry in entries)
    withdrawals = sum(entry["withdrawals_fen"] for entry in entries)
    net_contribution = contributions - withdrawals
    cash_end = sum(entry["cash_end_fen"] for entry in entries)
    terminal_total = cash_end + securities_mv
    difference = terminal_total - net_contribution - total_return
    conserved = abs(difference) <= 1

    first_dates = [
        entry["capital"][0]["date"] for entry in entries if entry["capital"]
    ]
    first_date = min(first_dates) if first_dates else None
    flows = _capital_flows(capital, merged_opening, first_date)
    flows.append(
        {
            "date": valuation.valuation_date,
            "amount_fen": terminal_total,
            "kind": "terminal_value",
            "business": "期末市值",
            "matched_pattern": None,
            "source_file": valuation.path,
            "source_line_no": None,
        }
    )
    entry = {
        "account_id": None,
        "account_alias": None,
        "account_status": None,
        "account_key": "merged",
        "label": MERGED_LABEL,
        "capital": capital,
        "income": income,
        "positions": positions,
        "all_positions": [
            item for account in entries for item in account["all_positions"]
        ],
        "securities_market_value_fen": securities_mv,
        "cash_end_fen": cash_end,
        "terminal_total_fen": terminal_total,
        "opening_cash_fen": merged_opening,
        "contributions_fen": contributions,
        "withdrawals_fen": withdrawals,
        "net_contribution_fen": net_contribution,
        "realized_sells_fen": realized_sells,
        "income_by_kind": income_by_kind,
        "open_cost_fen": open_cost,
        "unrealized_fen": unrealized,
        "realized_total_fen": realized_total,
        "total_return_fen": total_return,
        "difference_fen": difference,
        "conserved": conserved,
        "fees": {
            "total_fen": sum(entry["fees"]["total_fen"] for entry in entries),
            "by_type_fen": {
                name: sum(entry["fees"]["by_type_fen"][name] for entry in entries)
                for name in FEE_FIELDS
            },
        },
        "cash_flows": flows,
    }
    entry["pnl"] = _pnl_block(entry, income)
    if not conserved:
        problems.append(
            {
                "code": SPLIT_NOT_CONSERVED,
                "message": "合并视图拆分守恒被打破：差 {} 分".format(difference),
                "account_key": "merged",
            }
        )
    if "xirr" in sections:
        entry["xirr"] = _solve_xirr_for(flows, xirr_params, _MERGED_VIEW, problems)
    if "twr" in sections:
        points = _twr_points_for(
            merged_timeline,
            twr_basis,
            capital,
            valuation.valuation_date,
            snapshot_dates,
            valuation,
            missing,
            MERGED_LABEL,
            first_date,
        )
        entry["twr"] = _solve_twr_for(points, twr_basis, _MERGED_VIEW, problems)
    return entry


def _solve_xirr_for(flows, xirr_params, view, problems):
    try:
        solution = solve_xirr(
            [(row["date"], row["amount_fen"]) for row in flows], xirr_params
        )
    except DegradedError as exc:
        problems.append(
            {
                "code": getattr(exc, "code", XIRR_NO_SOLUTION),
                "message": str(exc),
                "account_key": view.key,
                "label": view.label,
            }
        )
        return {
            "status": "no_solution",
            "rate": None,
            "solver": None,
            "iterations": None,
            "npv_at_rate": None,
            "flow_count": len(flows),
        }
    return {
        "status": "ok",
        "rate": rate_text(solution.rate),
        "solver": solution.solver,
        "iterations": solution.iterations,
        "npv_at_rate": rate_text(solution.npv_value, places=6),
        "flow_count": len(flows),
    }


def _solve_twr_for(points, twr_basis, view, problems):
    if points is None:
        return {
            "basis": twr_basis,
            "status": "valuation_missing",
            "cumulative_rate": None,
            "segment_count": 0,
            "segments": [],
        }
    try:
        cumulative, segments = compute_twr(points)
    except DegradedError as exc:
        problems.append(
            {
                "code": getattr(exc, "code", TWR_ZERO_BASE),
                "message": str(exc),
                "account_key": view.key,
                "label": view.label,
            }
        )
        return {
            "basis": twr_basis,
            "status": "zero_base",
            "cumulative_rate": None,
            "segment_count": 0,
            "segments": [],
        }
    return {
        "basis": twr_basis,
        "status": "ok",
        "cumulative_rate": rate_text(cumulative),
        "segment_count": len(segments),
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# 交叉核对与分解
# ---------------------------------------------------------------------------


def _check_valuation_quantities(timelines, valuation, problems):
    """估值表声明数量 vs 台账持仓（容差 1 股）：不一致即报，绝不静默。"""
    tolerance = Decimal("1")
    for (date, symbol), declared in sorted(valuation.quantities.items()):
        ledger_quantity = Decimal("0")
        for timeline in timelines:
            ledger_quantity += timeline.positions_at(date).get(symbol, Decimal("0"))
        if abs(declared - ledger_quantity) > tolerance:
            problems.append(
                {
                    "code": VALUATION_QUANTITY_MISMATCH,
                    "message": "估值表声明 {} {} 持有 {}，台账为 {}".format(
                        date, symbol, _text(declared), _text(ledger_quantity)
                    ),
                    "date": date,
                    "symbol": symbol,
                    "declared_quantity": _text(declared),
                    "ledger_quantity": _text(ledger_quantity),
                    "source_line_no": valuation.line_of(date, symbol),
                }
            )


def _decomposition(evaluations, merged, industries, valuation_date, holdings):
    per_symbol = {}
    for position in holdings["positions"]:
        symbol = position["symbol"]
        bucket = per_symbol.setdefault(
            symbol,
            {
                "symbol": symbol,
                "name": position.get("name"),
                "realized_fen": 0,
                "income_fen": 0,
                "unrealized_fen": 0,
            },
        )
        if position.get("name"):
            bucket["name"] = position["name"]
        bucket["realized_fen"] += _fen(position.get("realized_net_fen"))
    for row in merged["positions"]:
        bucket = per_symbol.setdefault(
            row["symbol"],
            {
                "symbol": row["symbol"],
                "name": row.get("name"),
                "realized_fen": 0,
                "income_fen": 0,
                "unrealized_fen": 0,
            },
        )
        bucket["unrealized_fen"] += row["market_value_fen"] - _fen(row.get("cost_fen"))
    for row in merged["income"]:
        symbol = row["symbol"] or "<未标注>"
        bucket = per_symbol.setdefault(
            symbol,
            {
                "symbol": symbol,
                "name": None,
                "realized_fen": 0,
                "income_fen": 0,
                "unrealized_fen": 0,
            },
        )
        bucket["income_fen"] += row["amount_fen"]

    symbols = []
    for symbol in sorted(per_symbol):
        item = per_symbol[symbol]
        symbols.append(
            {
                "symbol": symbol,
                "name": item.get("name"),
                "industry": industries.of(symbol),
                "realized_fen": item["realized_fen"],
                "income_fen": item["income_fen"],
                "unrealized_fen": item["unrealized_fen"],
                "total_fen": item["realized_fen"]
                + item["income_fen"]
                + item["unrealized_fen"],
            }
        )

    by_industry = {}
    for item in symbols:
        bucket = by_industry.setdefault(
            item["industry"],
            {
                "industry": item["industry"],
                "symbols": [],
                "realized_fen": 0,
                "income_fen": 0,
                "unrealized_fen": 0,
                "total_fen": 0,
            },
        )
        bucket["symbols"].append(item["symbol"])
        for field in ("realized_fen", "income_fen", "unrealized_fen", "total_fen"):
            bucket[field] += item[field]
    industry_rows = [by_industry[name] for name in sorted(by_industry)]
    for row in industry_rows:
        row["symbols"] = sorted(row["symbols"])

    account_rows = [
        {
            "account_id": entry["account_id"],
            "account_alias": entry["account_alias"],
            "account_key": entry["account_key"],
            "realized_fen": entry["realized_total_fen"],
            "income_fen": sum(entry["income_by_kind"].values()),
            "unrealized_fen": entry["unrealized_fen"],
            "total_fen": entry["total_return_fen"],
        }
        for entry in evaluations
    ]

    per_year = {}
    for event in holdings["events"]:
        year = str(event["trade_date"])[:4]
        per_year.setdefault(
            year, {"year": year, "realized_fen": 0, "income_fen": 0, "unrealized_fen": 0}
        )
    for event in holdings["events"]:
        if event.get("kind") != "trade_sell":
            continue
        realized = (event.get("realized") or {}).get("realized_fen")
        if realized is None:
            continue
        year = str(event["trade_date"])[:4]
        per_year.setdefault(
            year, {"year": year, "realized_fen": 0, "income_fen": 0, "unrealized_fen": 0}
        )["realized_fen"] += _fen(realized)
    for row in merged["income"]:
        year = str(row["date"])[:4]
        per_year.setdefault(
            year, {"year": year, "realized_fen": 0, "income_fen": 0, "unrealized_fen": 0}
        )["income_fen"] += row["amount_fen"]
    valuation_year = str(valuation_date)[:4]
    per_year.setdefault(
        valuation_year,
        {"year": valuation_year, "realized_fen": 0, "income_fen": 0, "unrealized_fen": 0},
    )["unrealized_fen"] += merged["unrealized_fen"]
    year_rows = [
        {
            "year": year,
            "realized_fen": per_year[year]["realized_fen"],
            "income_fen": per_year[year]["income_fen"],
            "unrealized_fen": per_year[year]["unrealized_fen"],
            "total_fen": per_year[year]["realized_fen"]
            + per_year[year]["income_fen"]
            + per_year[year]["unrealized_fen"],
        }
        for year in sorted(per_year)
    ]

    merged_total = merged["total_return_fen"]
    sums = {
        "by_symbol_fen": sum(item["total_fen"] for item in symbols),
        "by_industry_fen": sum(item["total_fen"] for item in industry_rows),
        "by_account_fen": sum(item["total_fen"] for item in account_rows),
        "by_year_fen": sum(item["total_fen"] for item in year_rows),
        "merged_total_fen": merged_total,
    }
    sums["all_equal"] = all(
        sums[key] == merged_total
        for key in ("by_symbol_fen", "by_industry_fen", "by_account_fen", "by_year_fen")
    )
    return {
        "by_symbol": symbols,
        "by_industry": industry_rows,
        "by_account": account_rows,
        "by_year": year_rows,
        "checks": sums,
        "unclassified_symbols": industries.unclassified(
            [item["symbol"] for item in symbols]
        ),
    }


def _benchmark_section(benchmark, merged, sections, problems):
    if benchmark is None:
        return {
            "provided": False,
            "note": "未提供本地基准 CSV：超额列不显示，命令仍成功退出。",
        }
    capital = merged["capital"]
    if not capital:
        return {
            "provided": True,
            **benchmark.describe(),
            "excess": None,
            "note": "组合没有外部资金划转，无法与基准对齐期间。",
        }
    start_date = min(row["date"] for row in capital)
    end_date = merged["cash_flows"][-1]["date"]
    window = period_return(benchmark, start_date, end_date)
    if window is None:
        problems.append(
            {
                "code": BENCHMARK_DATE_MISSING,
                "message": "基准缺少 {} 或 {} 的数值：不插值，超额列不显示".format(
                    start_date, end_date
                ),
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        return {
            "provided": True,
            **benchmark.describe(),
            "start_date": start_date,
            "end_date": end_date,
            "excess": None,
            "note": "基准缺起止日期数值，超额列不显示。",
        }
    excess = {
        "window": {
            "start_date": start_date,
            "end_date": end_date,
            "days": window["days"],
        },
        "benchmark_cumulative_return": rate_text(window["cumulative"]),
        "benchmark_annualized_return": rate_text(window["annualized"]),
        "xirr_excess": None,
        "twr_excess": None,
    }
    if "xirr" in sections and merged.get("xirr", {}).get("rate") is not None:
        excess["xirr_excess"] = rate_text(
            float(merged["xirr"]["rate"]) - float(window["annualized"])
        )
    if "twr" in sections and merged.get("twr", {}).get("cumulative_rate") is not None:
        excess["twr_excess"] = rate_text(
            float(merged["twr"]["cumulative_rate"]) - float(window["cumulative"])
        )
    return {
        "provided": True,
        **benchmark.describe(),
        "start_date": start_date,
        "end_date": end_date,
        "excess": excess,
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_attribution(
    ledger_path,
    method,
    data_dir,
    valuation_path=None,
    benchmark_path=None,
    twr_basis=None,
    sections=("xirr", "twr"),
    out_path=None,
):
    """归因入口：校验口径 → **武装运行期零上传探针** → 计算 → 落盘。"""
    if not valuation_path:
        raise UsageError(
            "必须显式指定 --valuation 估值表（未实现收益与 XIRR 期末市值都取自它）",
            code="VALUATION_REQUIRED",
        )
    requested = tuple(sections or ())
    if not requested:
        raise UsageError(
            "至少请求一个口径段（--xirr / --twr）", code="ATTRIBUTION_SECTIONS_EMPTY"
        )

    cost_params = load_cost_params(data_dir)
    require_method(method, cost_params)
    xirr_params = load_xirr_params(data_dir)
    params = read_json(data_dir, "rules/params.json", "PARAMS_MISSING")
    twr_params = params.get("twr") or {}
    bases = list(twr_params.get("bases") or [])
    default_basis = twr_params.get("default_basis")
    if not bases or default_basis not in bases:
        raise InputError(
            "params.twr 缺 bases/default_basis，TWR 估值点口径无法确定",
            code="TWR_PARAMS_INCOMPLETE",
        )
    resolved_basis = twr_basis if twr_basis is not None else default_basis
    if resolved_basis not in bases:
        raise UsageError(
            "TWR 估值点口径 {!r} 不在 params.twr.bases 白名单内".format(resolved_basis),
            code="TWR_BASIS_UNKNOWN",
        )

    from ..netguard import armed_runtime_probe

    with armed_runtime_probe() as probe:
        artifact = _compute_artifact(
            ledger_path=ledger_path,
            method=method,
            data_dir=data_dir,
            valuation_path=valuation_path,
            benchmark_path=benchmark_path,
            resolved_basis=resolved_basis,
            requested=requested,
            cost_params=cost_params,
            xirr_params=xirr_params,
            twr_params=twr_params,
        )
        artifact["zero_upload"] = {
            "runtime_probe": {
                "installed": probe.installed,
                "armed": True,
                "blocked_attempts": len(probe.attempts),
            }
        }
    if out_path:
        write_json_atomic(out_path, artifact)
    return AttributionResult(artifact, method, bool(artifact["invalid"]))


def _compute_artifact(
    ledger_path,
    method,
    data_dir,
    valuation_path,
    benchmark_path,
    resolved_basis,
    requested,
    cost_params,
    xirr_params,
    twr_params,
):
    payload, ledger_sha = read_ledger_file(ledger_path)
    holdings = build_holdings(ledger_path, method, data_dir).holdings
    actions = build_actions(ledger_path, method, data_dir).actions
    valuation = load_valuation(valuation_path)
    benchmark = load_benchmark(benchmark_path) if benchmark_path else None
    industries = load_industries(data_dir)

    if holdings["events"]:
        last_row_date = holdings["events"][-1]["trade_date"]
        if valuation.valuation_date < last_row_date:
            raise InputError(
                "估值日 {} 早于流水最后一行 {}：期末市值必须覆盖整段流水".format(
                    valuation.valuation_date, last_row_date
                ),
                code="VALUATION_BEFORE_LEDGER_END",
            )

    problems = []
    warnings = []
    missing = []
    classification = _classification_index(actions)
    snapshot_dates, positions_index, cash_index = _timeline_data(holdings)

    account_items = []
    for account in holdings["accounts"]:
        key = account["account_key"]
        events = [event for event in holdings["events"] if event["account_key"] == key]
        cash_dates, cash_amounts = cash_index.get(key, ([], []))
        timeline = _Timeline(
            snapshot_dates=snapshot_dates,
            positions_by_date=positions_index.get(key, {}),
            cash_dates=cash_dates,
            cash_amounts=cash_amounts,
            opening_cash=_fen(account.get("opening_cash_fen")),
        )
        account_items.append((account, key, timeline, events))

    merged_opening = sum(
        _fen(account.get("opening_cash_fen")) for account, _, _, _ in account_items
    )
    merged_timeline = _MergedTimeline(
        [timeline for _, _, timeline, _ in account_items], merged_opening
    )

    evaluations = []
    for account, key, timeline, events in account_items:
        capital, income = _classify_events(events, classification, warnings)
        view = _ViewStub("account", key, account, events)
        evaluations.append(
            _evaluate_account(
                view,
                account,
                capital,
                income,
                holdings["positions"],
                timeline,
                valuation,
                missing,
                requested,
                resolved_basis,
                snapshot_dates,
                xirr_params,
                problems,
            )
        )

    merged = _aggregate_merged(
        merged_timeline,
        evaluations,
        merged_opening,
        valuation,
        missing,
        requested,
        resolved_basis,
        snapshot_dates,
        xirr_params,
        problems,
    )

    _check_valuation_quantities(
        [timeline for _, _, timeline, _ in account_items], valuation, problems
    )

    for date, symbol, label in missing:
        problems.append(
            {
                "code": VALUATION_MISSING,
                "message": "估值表缺 {} 的 {} 价格（视图 {}）：当日市值无法确定".format(
                    date, symbol, label
                ),
                "date": date,
                "symbol": symbol,
                "label": label,
            }
        )

    fees_total = sum(entry["fees"]["total_fen"] for entry in evaluations)
    m2_fees_total = _fen(holdings["totals"].get("fees_total_fen"))
    if fees_total != m2_fees_total:
        problems.append(
            {
                "code": FEE_TOTAL_MISMATCH,
                "message": "分账户费用合计 {} 分 != M2 totals.fees_total_fen {} 分".format(
                    fees_total, m2_fees_total
                ),
            }
        )

    contributions = sum(entry["contributions_fen"] for entry in evaluations)
    decomposition = _decomposition(
        evaluations, merged, industries, valuation.valuation_date, holdings
    )
    if not decomposition["checks"]["all_equal"]:
        problems.append(
            {
                "code": DECOMPOSITION_NOT_CONSERVED,
                "message": "四维分解之和与总收益不等：{}".format(
                    decomposition["checks"]
                ),
            }
        )

    artifact = {
        "format": "ia.attribution",
        "format_version": 1,
        "contract_version": cost_params["params"].get("contract_version"),
        "method": method,
        "method_label": holdings["method_label"],
        "method_notice": holdings["method_notice"],
        "basis": {
            "cost_method": method,
            "cost_method_label": holdings["method_label"],
            "twr_basis": resolved_basis,
            "twr_basis_label": basis_label(resolved_basis),
            "twr_basis_notice": basis_notice(resolved_basis),
            "benchmark_provided": benchmark is not None,
            "benchmark_notice": (
                "已提供本地基准 CSV，超额列按起止估值日折年比较。"
                if benchmark is not None
                else "未提供本地基准 CSV：超额列不显示，命令仍成功退出。"
            ),
            "valuation_date": valuation.valuation_date,
            "valuation_notice": (
                "期末市值 = 估值日证券市值（估值表给价、台账给量）；"
                "估值表缺价即报 VALUATION_MISSING，不做插值。"
            ),
            "xirr_convention": "external_capital_flow_investor_signed",
            "xirr_notice": (
                "XIRR 现金流 = 账户边界上的资金划转（银证转入/转出、内部划转），"
                "投入为负、收回与期末市值为正；成交与分红是组合内部动作，不进现金流。"
            ),
            "split_convention": "已实现（含分红）+ 未实现 = 期末总资产 − 净投入",
        },
        "inputs": {
            "ledger": str(ledger_path),
            "ledger_sha256": ledger_sha,
            "rows": len(holdings["events"]),
            "valuation": valuation.describe(),
            "benchmark": benchmark.describe() if benchmark else None,
            "industries": industries.describe(),
            "xirr_params": xirr_params["raw"],
            "twr_params": twr_params,
            "sections": list(requested),
        },
        "pnl": {
            "accounts": [entry["pnl"] for entry in evaluations],
            "merged": merged["pnl"],
            "checks": {
                "conserved": all(entry["conserved"] for entry in evaluations)
                and merged["conserved"],
                "tolerance_fen": 1,
                "max_difference_fen": max(
                    [abs(entry["difference_fen"]) for entry in evaluations]
                    + [abs(merged["difference_fen"])]
                ),
            },
        },
        "fees": {
            "total_fen": fees_total,
            "by_type_fen": {
                name: _fen((holdings["totals"].get("fees_fen") or {}).get(name))
                for name in FEE_FIELDS
            },
            "by_account": [
                {
                    "account_id": entry["account_id"],
                    "account_alias": entry["account_alias"],
                    "fees_fen": entry["fees"]["total_fen"],
                    "by_type_fen": entry["fees"]["by_type_fen"],
                }
                for entry in evaluations
            ],
            "contributions_fen": contributions,
            "drag_bps": (
                int(fees_total * 10000 // contributions) if contributions else None
            ),
            "matches_m2_total": fees_total == m2_fees_total,
        },
        "decomposition": decomposition,
        "benchmark": _benchmark_section(benchmark, merged, requested, problems),
        "invalid": problems,
        "warnings": warnings,
    }

    if "xirr" in requested:
        artifact["xirr"] = {
            "convention": "external_capital_flow_investor_signed",
            "notice": artifact["basis"]["xirr_notice"],
            "accounts": [_xirr_report(entry) for entry in evaluations]
            + [_xirr_report(merged)],
        }
    if "twr" in requested:
        artifact["twr"] = {
            "basis": resolved_basis,
            "basis_label": basis_label(resolved_basis),
            "basis_notice": basis_notice(resolved_basis),
            "accounts": [_twr_report(entry) for entry in evaluations]
            + [_twr_report(merged)],
        }
    return artifact


class _ViewStub(object):
    """账户视图的行标（只承载身份与行序，取值走 timeline）。"""

    def __init__(self, scope, key, account, events=()):
        self.scope = scope
        self.key = key
        self.label = account.get("account_id") or key
        self.account_id = account.get("account_id")
        self.alias = account.get("account_alias")
        self.status = account.get("account_status")
        self.events = list(events)


def _xirr_report(entry):
    report = {
        "account_id": entry["account_id"],
        "account_alias": entry["account_alias"],
        "account_key": entry["account_key"],
        "label": entry["label"],
        "net_contribution_fen": entry["net_contribution_fen"],
        "terminal_total_fen": entry["terminal_total_fen"],
        "cash_flows": entry["cash_flows"],
    }
    report.update(entry["xirr"])
    return report


def _twr_report(entry):
    report = {
        "account_id": entry["account_id"],
        "account_alias": entry["account_alias"],
        "account_key": entry["account_key"],
        "label": entry["label"],
    }
    report.update(entry["twr"])
    return report
