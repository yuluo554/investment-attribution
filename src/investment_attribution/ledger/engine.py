"""成本引擎：成交流水 → 持仓台账 / 每日快照 / 费用明细 / 勾稽台账。

**口径是显式参数，无默认值**（K7）。本模块不读时钟、不联网、不修改输入
文件；同一输入双跑逐字节一致。

输入 = M1 的 ``ledger.json``（统一流水）；排序键 = ``trade_date +
trade_time + source_line_no``（plan/02 §五）。

两种口径共享同一账本骨架，只在卖出取成本上不同：

- ``fifo``：按买入时间升序消耗批次（每批成本含该批买入费用）；
- ``wavg``：每次买入后重算单位成本；卖出不改变单位成本。

公司行为的**规则匹配与人工确认**属于 M3；M2 只按行形态处理份额与对价：
有数量无对价 = 零成本份额（送股 / 拆分），有对价且现金流出 = 买入成本，
有对价且无现金流出 = 视同投入（红利再投）。
"""

import hashlib
from decimal import Decimal

from ..errors import InputError, UsageError
from ..ingest.rules import read_json
from ..ingest.values import canonical_number
from ..manifest import load_manifest
from ..serialize import write_json_atomic
from .model import (
    ACQUISITION_KINDS,
    COST_METHODS,
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
from .positions import make_book, quantity_text, unit_cost

HOLDINGS_FILE = "holdings.json"
SNAPSHOTS_FILE = "snapshots.json"


class LedgerResult(object):
    def __init__(self, holdings, snapshots, method, degraded):
        self.holdings = holdings
        self.snapshots = snapshots
        self.method = method
        self.degraded = degraded

    @property
    def exit_code(self):
        from ..exit_codes import EXIT_DEGRADED, EXIT_OK

        return EXIT_DEGRADED if self.degraded else EXIT_OK


def load_cost_params(data_dir):
    """加载参数包；成本口径白名单以 ``params.cost_methods`` 为准。"""
    params = read_json(data_dir, "rules/params.json", "PARAMS_MISSING")
    methods = params.get("cost_methods")
    if not isinstance(methods, list) or not methods:
        raise InputError(
            "params.cost_methods 缺失或为空", code="PARAMS_INCOMPLETE"
        )
    if tuple(methods) != COST_METHODS:
        raise InputError(
            "params.cost_methods 与引擎实现不一致：{!r}".format(methods),
            code="COST_METHODS_DRIFT",
        )
    fee_allocation = params.get("fee_allocation") or {}
    manifest = load_manifest(data_dir)
    return {
        "params": params,
        "fee_allocation": fee_allocation,
        "quantity_tolerance": manifest["bench_thresholds"][
            "position_quantity_tolerance_shares"
        ],
        "amount_tolerance_fen": int(
            round(manifest["bench_thresholds"]["position_amount_tolerance_cny"] * 100)
        ),
    }


def require_method(method, cost_params):
    """K7：口径必须显式给；缺省是用法错误（exit 2），不得有默认值。"""
    if method is None:
        raise UsageError(
            "必须显式指定 --method fifo|wavg（成本口径无默认值）",
            code="COST_METHOD_REQUIRED",
        )
    if method not in cost_params["params"]["cost_methods"]:
        raise UsageError(
            "成本口径 {!r} 不在 params.cost_methods 白名单内".format(method),
            code="COST_METHOD_UNKNOWN",
        )
    return method


def read_ledger_file(path):
    """读取统一流水并校验顶层形态；返回 ``(payload, sha256)``。"""
    import json
    from pathlib import Path

    target = Path(path)
    if not target.is_file():
        raise InputError(
            "统一流水文件不存在：{}（先运行 import 生成）".format(target),
            code="LEDGER_MISSING",
        )
    raw = target.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InputError(
            "统一流水不是合法 UTF-8 JSON：{}：{}".format(target, exc),
            code="LEDGER_INVALID_JSON",
        )
    return payload, sha


class _AccountState(object):
    def __init__(self, key, row, method):
        self.key = key
        self.method = method
        self.account_id = row.get("account_id")
        self.alias = row.get("account_alias")
        self.status = row.get("account_status")
        self.pending_reason = row.get("account_pending_reason")
        self.shareholders = set()
        self.books = {}
        self.names = {}
        self.cumulative_settle = 0
        self.opening_cash = None
        self.cash_checked = 0
        self.cash_mismatch = 0
        self.realized_net = 0
        self.fees_by_type = {
            "commission": 0,
            "stamp_tax": 0,
            "transfer_fee": 0,
            "other_fee": 0,
            "unallocated": 0,
        }
        self.cash_in = 0
        self.cash_out = 0
        self.rows = 0

    def book(self, symbol):
        book = self.books.get(symbol)
        if book is None:
            book = make_book(self.method)
            self.books[symbol] = book
        return book


class _Engine(object):
    def __init__(self, method, rows, cost_params, ledger_sha, ledger_path):
        self.method = method
        self.rows = rows
        self.cost_params = cost_params
        self.ledger_sha = ledger_sha
        self.ledger_path = ledger_path
        self.accounts = {}
        self.events = []
        self.invalid = []
        self.warnings = []
        self.snapshots = []
        self._snapshot_date = None

    # -- invalid / warning helpers -----------------------------------------

    def _invalid(self, code, message, row, analysis=None, extras=None):
        payload = {
            "code": code,
            "message": message,
            "source_file": row.get("source_file"),
            "source_sha256": row.get("source_sha256"),
            "source_line_no": row.get("source_line_no"),
            "account_id": row.get("account_id"),
            "symbol": row.get("symbol"),
            "business": row.get("business"),
        }
        if analysis is not None:
            payload.update(analysis_extras(analysis))
        if extras:
            payload.update(extras)
        self.invalid.append(payload)

    def _warning(self, code, message, row=None, extras=None):
        payload = {"code": code, "message": message}
        if row is not None:
            payload.update(
                {
                    "source_file": row.get("source_file"),
                    "source_line_no": row.get("source_line_no"),
                    "account_id": row.get("account_id"),
                }
            )
        if extras:
            payload.update(extras)
        self.warnings.append(payload)

    # -- snapshot ------------------------------------------------------------

    def _snapshot(self, date):
        """把当日收盘后的持仓冻结成快照（只含数量 > 0 的持仓）。

        快照按**有流水的交易日**生成，不是自然日连续序列：没有成交就没有
        状态变化，补一串空日期只会让产物变长而不增加信息。
        """
        if not self.accounts:
            return
        entries = []
        for account_key_value in sorted(self.accounts):
            state = self.accounts[account_key_value]
            for symbol in sorted(state.books):
                book = state.books[symbol]
                if book.quantity <= 0:
                    continue
                entries.append(
                    {
                        "account_id": state.account_id,
                        "account_alias": state.alias,
                        "account_status": state.status,
                        "account_key": state.key,
                        "symbol": symbol,
                        "name": state.names.get(symbol),
                        "quantity": quantity_text(book.quantity),
                        "cost_fen": int(book.cost_fen),
                        "unit_cost": unit_cost(book.cost_fen, book.quantity),
                    }
                )
        total_cost = sum(item["cost_fen"] for item in entries)
        self.snapshots.append(
            {
                "date": date,
                "positions": entries,
                "position_count": len(entries),
                "total_cost_fen": total_cost,
            }
        )

    def _flush_before(self, date):
        if self._snapshot_date is None:
            self._snapshot_date = date
            return
        if date != self._snapshot_date:
            self._snapshot(self._snapshot_date)
            self._snapshot_date = date

    # -- row processing ------------------------------------------------------

    def run(self):
        for index, row in enumerate(self.rows):
            analysis = analyze_row(row, index)
            self._flush_before(row["trade_date"])
            self._apply_row(row, index, analysis)
        if self._snapshot_date is not None:
            self._snapshot(self._snapshot_date)
        return self._build_result()

    def _apply_row(self, row, index, analysis):
        key = account_key(row)
        state = self.accounts.get(key)
        if state is None:
            state = _AccountState(key, row, self.method)
            self.accounts[key] = state
            if state.status == "pending":
                self._warning(
                    "ACCOUNT_PENDING",
                    "账户未登记（{}），已单独成组，未并入任何默认账户".format(
                        state.pending_reason or "ACCOUNT_NOT_REGISTERED"
                    ),
                    row,
                    {"account_id": state.account_id},
                )
        state.rows += 1
        if row.get("account_alias"):
            state.alias = row["account_alias"]
        if row.get("account_status") == "pending":
            state.status = "pending"
        if row.get("shareholder_account"):
            state.shareholders.add(row["shareholder_account"])
        if row.get("name"):
            state.names[row["symbol"]] = row["name"]

        for code, message, extras in analysis.problems:
            self._invalid(code, message, row, analysis, extras)

        book = state.books.get(row["symbol"])
        if book is None and analysis.applicable and analysis.kind in (
            ACQUISITION_KINDS | frozenset((KIND_TRADE_SELL,))
        ):
            # 只有真正影响份额的行才建仓；现金事件（转入/转出/红利）不产生持仓。
            book = state.book(row["symbol"])
        quantity_before = book.quantity if book is not None else Decimal("0")
        cost_before = book.cost_fen if book is not None else 0
        cash_before = state.cumulative_settle
        realized = None
        matched_lots = []

        if not analysis.applicable or book is None:
            pass
        elif analysis.kind in (KIND_TRADE_BUY, KIND_RECEIPT_PAID):
            acquisition_cost = (analysis.amount_fen or 0) + analysis.fees_fen
            book.add(
                analysis.quantity,
                acquisition_cost,
                self._source(row, analysis),
            )
        elif analysis.kind == KIND_RECEIPT_VALUED:
            acquisition_cost = analysis.amount_fen or 0
            book.add(analysis.quantity, acquisition_cost, self._source(row, analysis))
        elif analysis.kind == KIND_RECEIPT_FREE:
            book.add_free(analysis.quantity, self._source(row, analysis))
        elif analysis.kind == KIND_TRADE_SELL:
            proceeds = (analysis.amount_fen or 0) - analysis.fees_fen
            take = book.take(analysis.quantity)
            matched_lots = take.matched
            if take.unmatched != "0":
                self._invalid(
                    "INSUFFICIENT_POSITION",
                    "卖出 {} 股超过持仓，未匹配部分 {}".format(
                        quantity_text(analysis.quantity), take.unmatched
                    ),
                    row,
                    analysis,
                    {"unmatched_quantity": take.unmatched},
                )
            realized_fen = proceeds - take.cost_fen
            state.realized_net += realized_fen
            realized = {
                "cost_fen": int(take.cost_fen),
                "proceeds_fen": int(proceeds),
                "realized_fen": int(realized_fen),
                "unit_cost": take.average_unit_cost
                or unit_cost(take.cost_fen, analysis.quantity),
            }
        elif analysis.kind == KIND_CASH_EVENT:
            pass
        elif analysis.kind == KIND_NOOP:
            pass

        state.cumulative_settle += analysis.settle_fen
        if analysis.settle_fen > 0:
            state.cash_in += analysis.settle_fen
        elif analysis.settle_fen < 0:
            state.cash_out += -analysis.settle_fen

        fee_detail = dict(analysis.fee_detail)
        fee_detail["unallocated_fen"] = (
            analysis.fees_fen if analysis.fee_source == "derived_from_settlement" else 0
        )
        for field in ("commission", "stamp_tax", "transfer_fee", "other_fee"):
            value = fee_detail.get(field)
            if value is not None:
                state.fees_by_type[field] += value
        if fee_detail["unallocated_fen"]:
            state.fees_by_type["unallocated"] += fee_detail["unallocated_fen"]

        checks, reported = self._reconcile(state, row, analysis, book)

        self.events.append(
            {
                "seq": len(self.events) + 1,
                "row_index": index,
                "source_file": row.get("source_file"),
                "source_sha256": row.get("source_sha256"),
                "source_line_no": row.get("source_line_no"),
                "account_id": state.account_id,
                "account_alias": state.alias,
                "account_status": state.status,
                "account_key": state.key,
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "business": row.get("business"),
                "trade_date": row.get("trade_date"),
                "trade_time": row.get("trade_time"),
                "direction": row.get("direction"),
                "kind": analysis.kind,
                "quantity": canonical_number(analysis.quantity),
                "price": row.get("price"),
                "amount_fen": analysis.amount_fen,
                "settlement_fen": int(analysis.settle_fen),
                "fees_fen": int(analysis.fees_fen),
                "fee_source": analysis.fee_source,
                "fee_detail": fee_detail,
                "cash_before_fen": int(cash_before),
                "cash_after_fen": int(state.cumulative_settle),
                "position": {
                    "quantity_before": quantity_text(quantity_before),
                    "quantity_after": quantity_text(
                        book.quantity if book is not None else Decimal("0")
                    ),
                    "cost_before_fen": int(cost_before),
                    "cost_after_fen": int(book.cost_fen if book is not None else 0),
                    "unit_cost_after": (
                        unit_cost(book.cost_fen, book.quantity)
                        if book is not None
                        else None
                    ),
                },
                "realized": realized,
                "matched_lots": matched_lots,
                "reported": reported,
                "checks": checks,
            }
        )

    def _source(self, row, analysis):
        return {
            "source_file": row.get("source_file"),
            "source_sha256": row.get("source_sha256"),
            "source_line_no": row.get("source_line_no"),
            "trade_date": row.get("trade_date"),
            "trade_time": row.get("trade_time"),
            "business": row.get("business"),
            "amount_fen": int((analysis.amount_fen or 0) + analysis.fees_fen),
        }

    def _reconcile(self, state, row, analysis, book):
        """T+1 资金余额勾稽 + 股份余额勾稽；不平即落 invalid（逐条可回放）。"""
        extra = row.get("extra") or {}
        reported_cash = extra.get("cash_balance")
        reported_share = extra.get("share_balance")
        checks = {"cash_balance": "not_reported", "share_balance": "not_reported"}
        reported = {
            "cash_balance_fen": reported_cash,
            "share_balance": reported_share,
        }

        if isinstance(reported_cash, int) and not isinstance(reported_cash, bool):
            if state.opening_cash is None:
                state.opening_cash = reported_cash - state.cumulative_settle
            expected = state.opening_cash + state.cumulative_settle
            state.cash_checked += 1
            if expected != reported_cash:
                state.cash_mismatch += 1
                checks["cash_balance"] = "mismatch"
                self._invalid(
                    "CASH_BALANCE_MISMATCH",
                    "资金余额勾稽不平：累计发生金额推导 {} 分，交割单 {} 分（差 {} 分）".format(
                        expected, reported_cash, reported_cash - expected
                    ),
                    row,
                    analysis,
                    {
                        "expected_cash_fen": int(expected),
                        "reported_cash_fen": int(reported_cash),
                        "difference_fen": int(reported_cash - expected),
                    },
                )
            else:
                checks["cash_balance"] = "ok"

        if reported_share not in (None, ""):
            try:
                reported_quantity = Decimal(str(reported_share))
            except Exception:
                reported_quantity = None
            if reported_quantity is not None:
                engine_quantity = book.quantity if book is not None else Decimal("0")
                tolerance = Decimal(str(self.cost_params["quantity_tolerance"]))
                difference = abs(engine_quantity - reported_quantity)
                checks["share_balance"] = (
                    "ok" if difference <= tolerance else "mismatch"
                )
                if difference > tolerance:
                    self._invalid(
                        "SHARE_BALANCE_MISMATCH",
                        "股份余额勾稽不平：引擎持仓 {}，交割单 {}（差 {}）".format(
                            quantity_text(engine_quantity),
                            canonical_number(reported_quantity),
                            canonical_number(difference),
                        ),
                        row,
                        analysis,
                        {
                            "expected_quantity": quantity_text(engine_quantity),
                            "reported_quantity": canonical_number(reported_quantity),
                            "difference_quantity": canonical_number(difference),
                            "tolerance_quantity": str(self.cost_params["quantity_tolerance"]),
                        },
                    )
        return checks, reported

    # -- result --------------------------------------------------------------

    def _build_result(self):
        positions = []
        for account_key_value in sorted(self.accounts):
            state = self.accounts[account_key_value]
            for symbol in sorted(state.books):
                book = state.books[symbol]
                sells = [
                    event
                    for event in self.events
                    if event["account_key"] == state.key
                    and event["symbol"] == symbol
                    and event["kind"] == KIND_TRADE_SELL
                ]
                positions.append(
                    {
                        "account_id": state.account_id,
                        "account_alias": state.alias,
                        "account_status": state.status,
                        "account_key": state.key,
                        "symbol": symbol,
                        "name": state.names.get(symbol),
                        "quantity": quantity_text(book.quantity),
                        "cost_fen": int(book.cost_fen),
                        "unit_cost": unit_cost(book.cost_fen, book.quantity),
                        "is_open": book.quantity > 0,
                        "lots": book.lots(),
                        "sell_count": len(sells),
                        "realized_net_fen": int(
                            sum(
                                event["realized"]["realized_fen"]
                                for event in sells
                                if event["realized"] is not None
                            )
                        ),
                    }
                )

        accounts = []
        for account_key_value in sorted(self.accounts):
            state = self.accounts[account_key_value]
            open_positions = [
                position
                for position in positions
                if position["account_key"] == state.key and position["is_open"]
            ]
            accounts.append(
                {
                    "account_id": state.account_id,
                    "account_alias": state.alias,
                    "account_status": state.status,
                    "pending_reason": state.pending_reason,
                    "account_key": state.key,
                    "shareholder_accounts": sorted(state.shareholders),
                    "rows": state.rows,
                    "symbols": sorted(state.books),
                    "open_position_count": len(open_positions),
                    "position_quantity": quantity_text(
                        sum((book.quantity for book in state.books.values()), Decimal("0"))
                    ),
                    "position_cost_fen": int(
                        sum(book.cost_fen for book in state.books.values())
                    ),
                    "realized_net_fen": int(state.realized_net),
                    "fees_fen": dict(state.fees_by_type),
                    "cash_in_fen": int(state.cash_in),
                    "cash_out_fen": int(state.cash_out),
                    "net_cash_flow_fen": int(state.cumulative_settle),
                    "opening_cash_fen": state.opening_cash,
                    "cash_check": {
                        "checked": state.cash_checked,
                        "mismatch": state.cash_mismatch,
                        "status": (
                            "mismatch"
                            if state.cash_mismatch
                            else ("ok" if state.cash_checked else "not_reported")
                        ),
                    },
                }
            )

        totals = {
            "position_cost_fen": sum(item["position_cost_fen"] for item in accounts),
            "realized_net_fen": sum(item["realized_net_fen"] for item in accounts),
            "fees_fen": {
                key: sum(item["fees_fen"][key] for item in accounts)
                for key in (
                    "commission",
                    "stamp_tax",
                    "transfer_fee",
                    "other_fee",
                    "unallocated",
                )
            },
            "open_positions": sum(1 for item in positions if item["is_open"]),
            "closed_positions": sum(1 for item in positions if not item["is_open"]),
            "accounts": len(accounts),
            "snapshots": len(self.snapshots),
            "events": len(self.events),
            "invalid": len(self.invalid),
        }
        totals["fees_total_fen"] = sum(totals["fees_fen"].values())

        degraded = bool(self.invalid) or any(
            account["account_status"] == "pending" for account in accounts
        )

        holdings = {
            "format": "ia.holdings",
            "format_version": 1,
            "contract_version": self.cost_params["params"].get("contract_version"),
            "method": self.method,
            "method_label": METHOD_LABELS[self.method],
            "method_notice": (
                "本产物按 {} 口径计算；成本口径是显式参数，切换口径不修改原始流水。"
                .format(METHOD_LABELS[self.method])
            ),
            "inputs": {
                "ledger": self.ledger_path,
                "ledger_sha256": self.ledger_sha,
                "rows": len(self.rows),
            },
            "fee_allocation": {
                "mode": self.cost_params["fee_allocation"].get("mode", "as_reported"),
                "unallocated": self.cost_params["fee_allocation"].get(
                    "unallocated", "keep_separate"
                ),
                "note": self.cost_params["fee_allocation"].get("note"),
            },
            "totals": totals,
            "accounts": accounts,
            "positions": positions,
            "events": self.events,
            "daily_snapshots": self.snapshots,
            "invalid": self.invalid,
            "warnings": self.warnings,
        }
        snapshots = {
            "format": "ia.snapshots",
            "format_version": 1,
            "contract_version": self.cost_params["params"].get("contract_version"),
            "method": self.method,
            "method_label": METHOD_LABELS[self.method],
            "inputs": {
                "ledger": self.ledger_path,
                "ledger_sha256": self.ledger_sha,
            },
            "snapshots": self.snapshots,
        }
        return holdings, snapshots, degraded


def build_holdings(
    ledger_path,
    method,
    data_dir,
    out_path=None,
    snapshots_path=None,
    include_fees=False,
):
    """读流水 → 算账本 → （可选）落盘；返回 ``LedgerResult``。"""
    cost_params = load_cost_params(data_dir)
    require_method(method, cost_params)
    payload, ledger_sha = read_ledger_file(ledger_path)
    rows = validate_rows(payload)
    engine = _Engine(method, sorted_rows(rows), cost_params, ledger_sha, str(ledger_path))
    holdings, snapshots, degraded = engine.run()

    if out_path:
        write_json_atomic(out_path, holdings)
    if snapshots_path:
        write_json_atomic(snapshots_path, snapshots)
    return LedgerResult(holdings, snapshots, method, degraded)


def build_pnl(
    ledger_path,
    method,
    data_dir,
    out_path=None,
    include_fees=False,
):
    """由同一账本派生已实现 / 未实现与费用口径。"""
    result = build_holdings(
        ledger_path,
        method,
        data_dir,
        out_path=None,
        include_fees=include_fees,
    )
    holdings = result.holdings

    sells = [event for event in holdings["events"] if event["kind"] == KIND_TRADE_SELL]
    gross = sum((event["realized"] or {}).get("realized_fen", 0) for event in sells)
    selling_fees = sum(event["fees_fen"] for event in sells)
    realized_net = sum((event["realized"] or {}).get("realized_fen", 0) for event in sells)
    open_positions = [item for item in holdings["positions"] if item["is_open"]]

    per_symbol = {}
    for symbol in sorted({item["symbol"] for item in holdings["positions"]}):
        items = [item for item in holdings["positions"] if item["symbol"] == symbol]
        per_symbol[symbol] = {
            "symbol": symbol,
            "name": next((item["name"] for item in items if item["name"]), None),
            "accounts": len({item["account_id"] for item in items}),
            "quantity": quantity_text(
                sum((Decimal(item["quantity"]) for item in items), Decimal("0"))
            ),
            "open_cost_fen": int(sum(item["cost_fen"] for item in items)),
            "realized_net_fen": int(sum(item["realized_net_fen"] for item in items)),
        }

    pnl = {
        "format": "ia.pnl",
        "format_version": 1,
        "contract_version": holdings["contract_version"],
        "method": holdings["method"],
        "method_label": holdings["method_label"],
        "method_notice": holdings["method_notice"],
        "inputs": holdings["inputs"],
        "fees": {
            "by_type_fen": holdings["totals"]["fees_fen"],
            "total_fen": holdings["totals"]["fees_total_fen"],
            "allocation_mode": holdings["fee_allocation"]["mode"],
            "unallocated_policy": holdings["fee_allocation"]["unallocated"],
        },
        "realized": {
            "gross_fen": int(gross),
            "selling_fees_fen": int(selling_fees),
            "net_fen": int(realized_net),
            "sell_count": len(sells),
        },
        "open_positions": {
            "count": len(open_positions),
            "cost_fen": int(holdings["totals"]["position_cost_fen"]),
            "by_position": [
                {
                    "account_id": item["account_id"],
                    "symbol": item["symbol"],
                    "quantity": item["quantity"],
                    "cost_fen": item["cost_fen"],
                    "unit_cost": item["unit_cost"],
                }
                for item in open_positions
            ],
        },
        "per_symbol": [per_symbol[symbol] for symbol in sorted(per_symbol)],
        "per_account": [
            {
                "account_id": account["account_id"],
                "account_alias": account["account_alias"],
                "account_status": account["account_status"],
                "realized_net_fen": account["realized_net_fen"],
                "open_cost_fen": account["position_cost_fen"],
                "fees_fen": account["fees_fen"],
            }
            for account in holdings["accounts"]
        ],
        "invalid_count": len(holdings["invalid"]),
        "warnings": holdings["warnings"],
    }
    if include_fees:
        pnl["fee_detail"] = [
            {
                "source_file": event["source_file"],
                "source_line_no": event["source_line_no"],
                "account_id": event["account_id"],
                "symbol": event["symbol"],
                "business": event["business"],
                "kind": event["kind"],
                "fees_fen": event["fees_fen"],
                "fee_source": event["fee_source"],
                "fee_detail": event["fee_detail"],
            }
            for event in holdings["events"]
            if event["fees_fen"] or event["kind"] in (KIND_TRADE_BUY, KIND_TRADE_SELL)
        ]
    if out_path:
        write_json_atomic(out_path, pnl)
    return result, pnl
