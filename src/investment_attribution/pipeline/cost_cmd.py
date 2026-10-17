"""``cost`` / ``pnl`` 子命令编排。

退出码语义（K1 / K12）：

- ``0``：账本建成，无余额勾稽不平、无未登记账户；
- ``1``：数据可用但检出问题（余额勾稽不平 / 未登记账户 / 无法归类行）；
- ``2``：用法或输入不可用（未指定口径、流水缺失 / 形态不符）。

成本口径**无默认值**：未传 ``--method`` 一律 ``exit 2``，不猜。
"""

import os
from pathlib import Path

from ..errors import UsageError
from ..ledger import (
    HOLDINGS_FILE,
    SNAPSHOTS_FILE,
    build_holdings,
    build_pnl,
)
from ..paths import resolve_data_dir

PNL_FILE = "pnl.json"


def _require_ledger(ledger_path):
    if not ledger_path:
        raise UsageError(
            "必须显式指定 --ledger 统一流水路径（先运行 import 生成）",
            code="LEDGER_REQUIRED",
        )
    return ledger_path


def _ensure_dir(target):
    """产物路径的父目录不存在就建；目标本身是目录则判用法错误。"""
    if Path(target).is_dir():
        raise UsageError(
            "输出路径是目录而不是文件：{}（目录请用 --out-dir）".format(target),
            code="OUT_TARGET_IS_DIRECTORY",
        )
    directory = Path(target).parent
    if str(directory) and not directory.is_dir():
        os.makedirs(str(directory), exist_ok=True)


def _targets(out, out_dir, default_name):
    """``--out`` 优先；否则 ``--out-dir`` 下的默认名；都没有则不落盘。"""
    if out:
        return str(out)
    if out_dir:
        return str(Path(out_dir) / default_name)
    return None


def run_cost(
    method,
    ledger_path=None,
    data_dir=None,
    out=None,
    out_dir=None,
    include_fees=False,
    snapshots_out=None,
):
    """执行成本台账；返回 ``LedgerResult``（含退出码）。"""
    resolved = resolve_data_dir(data_dir)
    ledger = _require_ledger(ledger_path)

    holdings_out = _targets(out, out_dir, HOLDINGS_FILE)
    snapshots_target = snapshots_out
    if snapshots_target is None and out_dir:
        snapshots_target = str(Path(out_dir) / SNAPSHOTS_FILE)

    if out_dir and not Path(out_dir).is_dir():
        os.makedirs(str(out_dir), exist_ok=True)
    for target in (holdings_out, snapshots_target):
        if target:
            _ensure_dir(target)

    return build_holdings(
        ledger,
        method,
        resolved,
        out_path=holdings_out,
        snapshots_path=snapshots_target,
        include_fees=include_fees,
    )


def run_pnl(
    method,
    ledger_path=None,
    data_dir=None,
    out=None,
    out_dir=None,
    include_fees=False,
):
    """执行收益台账；返回 ``(LedgerResult, pnl_payload)``。"""
    resolved = resolve_data_dir(data_dir)
    ledger = _require_ledger(ledger_path)

    pnl_out = _targets(out, out_dir, PNL_FILE)
    if out_dir and not Path(out_dir).is_dir():
        os.makedirs(str(out_dir), exist_ok=True)
    if pnl_out:
        _ensure_dir(pnl_out)

    return build_pnl(
        ledger,
        method,
        resolved,
        out_path=pnl_out,
        include_fees=include_fees,
    )


def describe_holdings(result):
    """给 CLI 用的中文摘要行（只陈述结果，不做业务判断）。"""
    holdings = result.holdings
    totals = holdings["totals"]
    lines = [
        "成本台账完成（{}）：{} 个账户 / {} 条流水 / {} 个未平仓持仓 / {} 条问题".format(
            holdings["method_label"],
            totals["accounts"],
            totals["events"],
            totals["open_positions"],
            totals["invalid"],
        )
    ]
    lines.append(
        "  持仓成本 {:.2f} 元 / 已实现净收益 {:.2f} 元 / 费用合计 {:.2f} 元".format(
            totals["position_cost_fen"] / 100.0,
            totals["realized_net_fen"] / 100.0,
            totals["fees_total_fen"] / 100.0,
        )
    )
    for account in holdings["accounts"]:
        lines.append(
            "  账户 {}（{}，{}）：已实现 {:.2f} / 持仓成本 {:.2f} / 资金勾稽 {}".format(
                account["account_id"] or "<未提供>",
                account["account_alias"] or "未登记",
                account["account_status"],
                account["realized_net_fen"] / 100.0,
                account["position_cost_fen"] / 100.0,
                account["cash_check"]["status"],
            )
        )
    for item in holdings["invalid"][:10]:
        lines.append(
            "  问题 {} {}:{} {}".format(
                item["code"],
                item["source_file"],
                item["source_line_no"],
                item["message"],
            )
        )
    if len(holdings["invalid"]) > 10:
        lines.append("  … 其余 {} 条见产物 invalid 段".format(len(holdings["invalid"]) - 10))
    return lines
