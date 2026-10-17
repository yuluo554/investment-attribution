"""多账户合并与账户间划转抵消（M5）。

口径唯一定义处 = `data/README.md §4.6`。三条纪律：

1. **不重算成本**：输入是 M4 的 ``attribution.json``（已含分账户与合并两套数）。
   本模块只加一层增量——**逐笔划转台账**与**合并前后守恒断言**；它不碰成本
   引擎，也不重新走一遍持仓。
2. **不写第二份业务名表**：划转模式取自冻结规则包
   ``rules/corporate_actions.json`` 的 ``transfer_patterns``（与 M3 同纪律）。
3. **划转两腿按合同号归组**：同一合同号下两腿金额相反、分属不同账户即
   "已抵消"，相消证据（``net_fen == 0`` 与合并视图当日净现金流为 0）逐笔
   落进台账。单腿、净额不为零、两腿同账户一律**报问题**（退出码 1），
   绝不假装抵消成功。

守恒断言：``Σ 各账户总收益 == 合并视图总收益``，差超过
``bench_thresholds.transfer_offset_tolerance_cny`` 落
``MERGE_TOTAL_NOT_CONSERVED``。
"""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from ..errors import InputError, UsageError
from ..ingest.rules import read_json
from ..ingest.values import canonical_number
from ..ledger.engine import load_cost_params, read_ledger_file, require_method
from ..manifest import load_manifest
from ..serialize import write_json_atomic

MERGED_FILE = "merged.json"

MERGE_TOTAL_NOT_CONSERVED = "MERGE_TOTAL_NOT_CONSERVED"
MERGE_HOLDINGS_NOT_CONSERVED = "MERGE_HOLDINGS_NOT_CONSERVED"
TRANSFER_LEG_UNPAIRED = "TRANSFER_LEG_UNPAIRED"
TRANSFER_NET_NOT_ZERO = "TRANSFER_NET_NOT_ZERO"
TRANSFER_SAME_ACCOUNT = "TRANSFER_SAME_ACCOUNT"
TRANSFER_CONTRACT_MISSING = "TRANSFER_CONTRACT_MISSING"

MERGED_LABEL = "合并视图"


class MergeResult(object):
    def __init__(self, artifact, method, degraded):
        self.artifact = artifact
        self.method = method
        self.degraded = degraded

    @property
    def exit_code(self):
        from ..exit_codes import EXIT_DEGRADED, EXIT_OK

        return EXIT_DEGRADED if self.degraded else EXIT_OK


def _fen(value):
    return int(value or 0)


def _quantity_text(value):
    return canonical_number(Decimal(str(value if value is not None else "0")))


def _key(source_file, line_no):
    return "{}|{}".format(source_file, line_no)


def load_transfer_patterns(data_dir):
    """划转模式来自冻结规则包；缺了就报错，不在代码里补一份默认值。"""
    payload = read_json(data_dir, "rules/corporate_actions.json", "CA_RULES_MISSING")
    patterns = payload.get("transfer_patterns")
    if not isinstance(patterns, list) or not patterns:
        raise InputError(
            "规则包 corporate_actions.json 缺 transfer_patterns：无法识别账户间划转",
            code="TRANSFER_PATTERNS_MISSING",
        )
    return tuple(patterns)


def _read_attribution_file(path):
    target = Path(path)
    if not target.is_file():
        raise InputError(
            "归因产物不存在：{}（先运行 attribution 生成）".format(target),
            code="ATTRIBUTION_MISSING",
        )
    raw = target.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InputError(
            "归因产物不是合法 JSON：{}：{}".format(target, exc),
            code="ATTRIBUTION_INVALID_JSON",
        )
    return payload, hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# 证据索引：每个锚点都要 source_file / source_sha256 / source_line_no（K5）
# ---------------------------------------------------------------------------


def _provenance_index(ledger_rows, valuation, positions):
    index = {}

    def put(source_file, line_no, sha):
        if source_file is None or line_no is None:
            return None
        key = _key(source_file, line_no)
        index[key] = {
            "source_file": source_file,
            "source_sha256": sha,
            "source_line_no": int(line_no),
        }
        return key

    for row in ledger_rows:
        put(row.get("source_file"), row.get("source_line_no"), row.get("source_sha256"))
    valuation_name = Path(str((valuation or {}).get("path") or "")).name
    valuation_sha = (valuation or {}).get("sha256")
    if valuation_name and valuation_sha:
        for position in positions:
            put(valuation_name, position.get("source_line_no"), valuation_sha)
    return index


def _resolve(index, keys):
    return [index[key] for key in keys if key in index]


# ---------------------------------------------------------------------------
# 划转识别与两端剔除
# ---------------------------------------------------------------------------


def _is_transfer(row, patterns):
    if row.get("direction") != "other":
        return None
    business = row.get("business") or ""
    for pattern in patterns:
        if pattern in business:
            return pattern
    return None


def _transfer_ledger(ledger_rows, patterns, index, merged_cash_flows):
    groups = {}
    problems = []
    for position, row in enumerate(ledger_rows):
        pattern = _is_transfer(row, patterns)
        if pattern is None:
            continue
        contract_no = row.get("contract_no")
        if not contract_no:
            problems.append(
                {
                    "code": TRANSFER_CONTRACT_MISSING,
                    "message": "划转行缺合同号：无法与另一端配对，不假装已抵消",
                    "source_file": row.get("source_file"),
                    "source_line_no": row.get("source_line_no"),
                }
            )
            continue
        groups.setdefault(contract_no, []).append((position, row, pattern))

    flow_by_date = {}
    for flow in merged_cash_flows or []:
        flow_by_date[flow.get("date")] = flow_by_date.get(flow.get("date"), 0) + _fen(
            flow.get("amount_fen")
        )

    entries = []
    for contract_no in sorted(groups):
        legs = []
        for _position, row, pattern in groups[contract_no]:
            legs.append(
                {
                    "account_id": row.get("account_id"),
                    "account_alias": row.get("account_alias"),
                    "settlement_fen": _fen(row.get("settlement_amount")),
                    "business": row.get("business"),
                    "matched_pattern": pattern,
                    "trade_date": row.get("trade_date"),
                    "source_file": row.get("source_file"),
                    "source_sha256": row.get("source_sha256"),
                    "source_line_no": row.get("source_line_no"),
                    "evidence_key": _key(row.get("source_file"), row.get("source_line_no")),
                }
            )
        legs.sort(
            key=lambda leg: (
                leg["trade_date"] or "",
                leg["source_file"] or "",
                leg["source_line_no"] or 0,
            )
        )
        for leg in legs:
            leg["direction"] = "out" if leg["settlement_fen"] < 0 else "in"
        net_fen = sum(leg["settlement_fen"] for leg in legs)
        amount_fen = sum(
            leg["settlement_fen"] for leg in legs if leg["settlement_fen"] > 0
        )
        accounts = sorted({leg["account_id"] for leg in legs})
        date = legs[0]["trade_date"]
        balanced = (
            len(legs) >= 2 and net_fen == 0 and len(accounts) == len(legs)
        )
        if len(legs) < 2:
            problems.append(
                {
                    "code": TRANSFER_LEG_UNPAIRED,
                    "message": "划转 {} 只有一腿：无法证明两端已抵消，请导入另一端账户".format(
                        contract_no
                    ),
                    "contract_no": contract_no,
                    "source_file": legs[0]["source_file"],
                    "source_line_no": legs[0]["source_line_no"],
                }
            )
        elif net_fen != 0:
            problems.append(
                {
                    "code": TRANSFER_NET_NOT_ZERO,
                    "message": "划转 {} 两腿净额 {} 分不为零：不构成内部划转".format(
                        contract_no, net_fen
                    ),
                    "contract_no": contract_no,
                }
            )
        elif len(accounts) < len(legs):
            problems.append(
                {
                    "code": TRANSFER_SAME_ACCOUNT,
                    "message": "划转 {} 的两腿落在同一账户：不是账户间划转".format(
                        contract_no
                    ),
                    "contract_no": contract_no,
                }
            )
        entries.append(
            {
                "contract_no": contract_no,
                "trade_date": date,
                "leg_count": len(legs),
                "amount_fen": amount_fen,
                "net_fen": net_fen,
                "accounts": accounts,
                "legs": legs,
                "offset": {
                    "balanced": balanced,
                    "net_fen": net_fen,
                    "distinct_accounts": len(accounts),
                    "merged_flow_on_date_fen": flow_by_date.get(date, 0),
                    "evidence": _resolve(index, [leg["evidence_key"] for leg in legs]),
                },
            }
        )
    return entries, problems


# ---------------------------------------------------------------------------
# 合并持仓：同名标的按账户汇总（两账户都持有时合并成一行）
# ---------------------------------------------------------------------------


def _merged_holdings(account_entries, ledger_rows, index):
    valuation_name = None
    holdings = {}
    for entry in account_entries:
        for position in entry["positions"]:
            symbol = position["symbol"]
            bucket = holdings.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "name": position.get("name"),
                    "quantity": Decimal("0"),
                    "cost_fen": 0,
                    "market_value_fen": 0,
                    "accounts_at_terminal": [],
                    "terminal_evidence": [],
                },
            )
            if position.get("name"):
                bucket["name"] = position["name"]
            bucket["quantity"] += Decimal(str(position["quantity"]))
            bucket["cost_fen"] += _fen(position.get("cost_fen"))
            bucket["market_value_fen"] += _fen(position.get("market_value_fen"))
            bucket["accounts_at_terminal"].append(entry["account_id"])
            if position.get("source_line_no") is not None:
                valuation_name = valuation_name or position.get("source_file")
                bucket["terminal_evidence"].append(
                    _key(valuation_name, position["source_line_no"])
                )

    history = {}
    for row in ledger_rows:
        symbol = row.get("symbol")
        if not symbol:
            continue
        history.setdefault(symbol, {}).setdefault(row.get("account_id"), []).append(
            _key(row.get("source_file"), row.get("source_line_no"))
        )

    rows = []
    for symbol in sorted(holdings):
        bucket = holdings[symbol]
        ever = sorted(account for account in history.get(symbol, {}) if account)
        rows.append(
            {
                "symbol": symbol,
                "name": bucket["name"],
                "quantity": _quantity_text(bucket["quantity"]),
                "cost_fen": bucket["cost_fen"],
                "market_value_fen": bucket["market_value_fen"],
                "unrealized_fen": bucket["market_value_fen"] - bucket["cost_fen"],
                "accounts_at_terminal": sorted(bucket["accounts_at_terminal"]),
                "account_count_at_terminal": len(bucket["accounts_at_terminal"]),
                "accounts_ever": ever,
                "account_count_ever": len(ever),
                "history_evidence": [
                    item for account in ever for item in history.get(symbol, {}).get(account, [])
                ],
                "evidence": {
                    "terminal_market_value": _resolve(
                        index, bucket["terminal_evidence"][:1]
                    ),
                },
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_merge(
    ledger_path,
    method,
    data_dir,
    attribution_path=None,
    attribution_payload=None,
    out_path=None,
):
    """合并入口：读归因产物 → 武装运行期零上传探针 → 计算 → 落盘。"""
    if not ledger_path:
        raise UsageError(
            "必须显式指定 --ledger 统一流水路径（划转两腿要从原始行取证）",
            code="LEDGER_REQUIRED",
        )
    cost_params = load_cost_params(data_dir)
    require_method(method, cost_params)

    if attribution_payload is None:
        if not attribution_path:
            raise UsageError(
                "必须显式指定 --attribution 归因产物路径（合并视图不重算成本）",
                code="ATTRIBUTION_REQUIRED",
            )
        attribution, attribution_sha = _read_attribution_file(attribution_path)
        attribution_ref = str(attribution_path)
    else:
        attribution, attribution_sha = attribution_payload, None
        attribution_ref = None
    if attribution.get("format") != "ia.attribution":
        raise InputError(
            "归因产物 format 不匹配：{!r}".format(attribution.get("format")),
            code="ATTRIBUTION_FORMAT_MISMATCH",
        )
    if attribution.get("method") != method:
        raise UsageError(
            "口径不一致：--method {!r} 与归因产物口径 {!r} 不符；"
            "合并视图不替你猜口径".format(method, attribution.get("method")),
            code="METHOD_MISMATCH_WITH_ATTRIBUTION",
        )

    from ..netguard import armed_runtime_probe

    with armed_runtime_probe() as probe:
        artifact = _compute_artifact(
            ledger_path, method, data_dir, attribution, attribution_ref,
            attribution_sha, cost_params,
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
    return MergeResult(artifact, method, bool(artifact["invalid"]))


def _compute_artifact(
    ledger_path, method, data_dir, attribution, attribution_ref, attribution_sha,
    cost_params,
):
    ledger_payload, ledger_sha = read_ledger_file(ledger_path)
    ledger_rows = ledger_payload["rows"]
    patterns = load_transfer_patterns(data_dir)
    thresholds = load_manifest(data_dir)["bench_thresholds"]
    tolerance_fen = int(round(thresholds["transfer_offset_tolerance_cny"] * 100))

    pnl = attribution.get("pnl") or {}
    xirr_rows = {
        item.get("account_key"): item
        for item in (attribution.get("xirr") or {}).get("accounts") or []
    }
    twr_rows = {
        item.get("account_key"): item
        for item in (attribution.get("twr") or {}).get("accounts") or []
    }
    all_positions = [
        position
        for account in pnl.get("accounts") or []
        for position in account.get("positions") or []
    ]
    index = _provenance_index(ledger_rows, attribution.get("inputs", {}).get("valuation"), all_positions)

    sells_by_account = {}
    shareholders_by_account = {}
    for row in ledger_rows:
        account = row.get("account_id")
        if row.get("direction") == "sell":
            sells_by_account.setdefault(account, []).append(
                _key(row.get("source_file"), row.get("source_line_no"))
            )
        if row.get("shareholder_account"):
            bucket = shareholders_by_account.setdefault(account, set())
            bucket.add(row["shareholder_account"])

    account_entries = []
    valuation_name = Path(
        str(((attribution.get("inputs") or {}).get("valuation") or {}).get("path") or "")
    ).name
    # 期末无证券持仓的账户没有自己的估值行；这类账户的「期末市值锚点」回落到
    # 组合里第一条真实估值行（陈述事实，不造锚点）。
    default_terminal_key = None
    for account in pnl.get("accounts") or []:
        for position in account.get("positions") or []:
            if position.get("source_line_no") is not None:
                default_terminal_key = _key(valuation_name, position["source_line_no"])
                break
        if default_terminal_key:
            break
    for account in pnl.get("accounts") or []:
        key = account.get("account_key")
        xirr_row = xirr_rows.get(key) or {}
        twr_row = twr_rows.get(key) or {}
        income_keys = [
            _key(item.get("source_file"), item.get("source_line_no"))
            for item in account.get("income_rows") or []
            if item.get("source_file") and item.get("source_line_no")
        ]
        capital_keys = [
            _key(item.get("source_file"), item.get("source_line_no"))
            for item in xirr_row.get("cash_flows") or []
            if item.get("source_file") and item.get("source_line_no")
        ]
        positions = []
        for position in account.get("positions") or []:
            row = dict(position)
            row["unrealized_fen"] = _fen(position.get("market_value_fen")) - _fen(
                position.get("cost_fen")
            )
            positions.append(row)
        account_entries.append(
            {
                "account_id": account.get("account_id"),
                "account_alias": account.get("account_alias"),
                "account_status": account.get("account_status"),
                "account_key": key,
                "label": account.get("label"),
                "shareholder_accounts": sorted(shareholders_by_account.get(key, set())),
                "net_contribution_fen": _fen(account.get("net_contribution_fen")),
                "terminal_cash_fen": _fen(account.get("terminal_cash_fen")),
                "terminal_market_value_fen": _fen(
                    account.get("terminal_market_value_fen")
                ),
                "terminal_total_fen": _fen(account.get("terminal_total_fen")),
                "realized_from_sells_fen": _fen(account.get("realized_from_sells_fen")),
                "realized_total_fen": _fen(account.get("realized_total_fen")),
                "unrealized_fen": _fen(account.get("unrealized_fen")),
                "total_return_fen": _fen(account.get("total_return_fen")),
                "dividend_income_fen": _fen(account.get("dividend_income_fen")),
                "reinvested_income_fen": _fen(account.get("reinvested_income_fen")),
                "other_income_fen": _fen(account.get("other_income_fen")),
                "fees_fen": _fen(account.get("fees_fen")),
                "difference_fen": _fen(account.get("difference_fen")),
                "conserved": bool(account.get("conserved")),
                "xirr": {
                    "status": xirr_row.get("status"),
                    "rate": xirr_row.get("rate"),
                },
                "twr": {
                    "basis": twr_row.get("basis"),
                    "status": twr_row.get("status"),
                    "cumulative_rate": twr_row.get("cumulative_rate"),
                },
                "positions": positions,
                "income_rows": account.get("income_rows") or [],
                "evidence": {
                    "sells": _resolve(index, sells_by_account.get(key, [])),
                    "income": _resolve(index, income_keys),
                    "capital": _resolve(index, capital_keys),
                    "terminal_market_value": _resolve(
                        index,
                        [
                            _key(valuation_name, position["source_line_no"])
                            for position in positions
                            if position.get("source_line_no") is not None
                        ][:1]
                        or ([default_terminal_key] if default_terminal_key else []),
                    ),
                },
            }
        )

    holdings = _merged_holdings(account_entries, ledger_rows, index)
    merged = pnl.get("merged") or {}
    before_fen = sum(entry["total_return_fen"] for entry in account_entries)
    after_fen = _fen(merged.get("total_return_fen"))
    difference_fen = after_fen - before_fen
    conserved = abs(difference_fen) <= tolerance_fen

    positions_cost = sum(
        entry["terminal_market_value_fen"] for entry in account_entries
    )
    holdings_cost = sum(row["market_value_fen"] for row in holdings)
    holdings_conserved = positions_cost == holdings_cost

    transfers, transfer_problems = _transfer_ledger(
        ledger_rows,
        patterns,
        index,
        (xirr_rows.get("merged") or {}).get("cash_flows"),
    )
    transfer_net_fen = sum(entry["net_fen"] for entry in transfers)
    unbalanced = [entry for entry in transfers if not entry["offset"]["balanced"]]

    invalid = list(attribution.get("invalid") or [])
    if not conserved:
        invalid.append(
            {
                "code": MERGE_TOTAL_NOT_CONSERVED,
                "message": (
                    "合并前后总收益不等：各账户合计 {} 分，合并视图 {} 分，差 {} 分"
                    "（门槛 {} 分）".format(
                        before_fen, after_fen, difference_fen, tolerance_fen
                    )
                ),
            }
        )
    if not holdings_conserved:
        invalid.append(
            {
                "code": MERGE_HOLDINGS_NOT_CONSERVED,
                "message": "合并持仓市值 {} 分 != 各账户持仓市值合计 {} 分".format(
                    holdings_cost, positions_cost
                ),
            }
        )
    invalid.extend(transfer_problems)

    artifact = {
        "format": "ia.merge",
        "format_version": 1,
        "contract_version": cost_params["params"].get("contract_version"),
        "method": method,
        "method_label": attribution.get("method_label"),
        "method_notice": attribution.get("method_notice"),
        "basis": dict(
            attribution.get("basis") or {},
            merge_convention="Σ 各账户总收益 == 合并视图总收益；内部划转两腿同日相反，按日期相加自然相消",
            transfer_notice=(
                "划转模式取自规则包 transfer_patterns；两腿按合同号归组，"
                "净额为零且分属不同账户才算『已抵消』"
            ),
        ),
        "inputs": {
            "ledger": str(ledger_path),
            "ledger_sha256": ledger_sha,
            "rows": len(ledger_rows),
            "attribution": attribution_ref,
            "attribution_sha256": attribution_sha,
            "valuation": attribution.get("inputs", {}).get("valuation"),
            "transfer_patterns": list(patterns),
            "accounts_input": len(account_entries),
        },
        "provenance": index,
        "accounts": account_entries,
        "merged": {
            "account_id": None,
            "account_alias": None,
            "account_status": None,
            "account_key": "merged",
            "label": MERGED_LABEL,
            "shareholder_accounts": sorted(
                {
                    account
                    for item in account_entries
                    for account in item["shareholder_accounts"]
                }
            ),
            "net_contribution_fen": _fen(merged.get("net_contribution_fen")),
            "terminal_cash_fen": _fen(merged.get("terminal_cash_fen")),
            "terminal_market_value_fen": _fen(merged.get("terminal_market_value_fen")),
            "terminal_total_fen": _fen(merged.get("terminal_total_fen")),
            "realized_from_sells_fen": _fen(merged.get("realized_from_sells_fen")),
            "realized_total_fen": _fen(merged.get("realized_total_fen")),
            "unrealized_fen": _fen(merged.get("unrealized_fen")),
            "total_return_fen": after_fen,
            "fees_fen": _fen(merged.get("fees_fen")),
            "difference_fen": _fen(merged.get("difference_fen")),
            "conserved": bool(merged.get("conserved")),
            "xirr": {
                "status": (xirr_rows.get("merged") or {}).get("status"),
                "rate": (xirr_rows.get("merged") or {}).get("rate"),
            },
            "twr": {
                "basis": (twr_rows.get("merged") or {}).get("basis"),
                "status": (twr_rows.get("merged") or {}).get("status"),
                "cumulative_rate": (twr_rows.get("merged") or {}).get(
                    "cumulative_rate"
                ),
            },
            "account_count": len(account_entries),
        },
        "holdings": holdings,
        "transfers": {
            "patterns": list(patterns),
            "entries": transfers,
            "matched_count": len(transfers) - len(unbalanced),
            "unbalanced_count": len(unbalanced),
            "net_fen": transfer_net_fen,
        },
        "checks": {
            "total_return_before_fen": before_fen,
            "total_return_after_fen": after_fen,
            "difference_fen": difference_fen,
            "tolerance_fen": tolerance_fen,
            "conserved": conserved,
            "transfer_net_fen": transfer_net_fen,
            "transfer_balanced": not unbalanced and not transfer_problems,
            "holdings_conserved": holdings_conserved,
            "account_count": len(account_entries),
        },
        "fees": attribution.get("fees"),
        "decomposition": attribution.get("decomposition"),
        "benchmark": attribution.get("benchmark"),
        "invalid": invalid,
        "warnings": list(attribution.get("warnings") or []),
    }
    return artifact
