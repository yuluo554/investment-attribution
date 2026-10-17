"""``merge`` 子命令编排。

退出码语义（K1）：

- ``0``：合并完成，划转两端全部抵消、合并前后总收益不变；
- ``1``：合并完成但检出问题（单腿划转、划转净额不为零、合并前后总收益不等、
  上游归因产物自带问题）；
- ``2``：用法或输入不可用（未指定口径 / 流水 / 归因产物，口径与归因产物不一致）。

成本口径**无默认值**（K7）；合并视图**不重算成本**，输入就是 M4 的
``attribution.json``。
"""

import os
from pathlib import Path

from ..errors import UsageError
from ..merge import MERGED_FILE, build_merge
from ..paths import resolve_data_dir


def _require_method(method):
    if method is None:
        raise UsageError(
            "必须显式指定 --method fifo|wavg（成本口径无默认值）",
            code="COST_METHOD_REQUIRED",
        )
    return method


def _ensure_dir(target):
    if Path(target).is_dir():
        raise UsageError(
            "输出路径是目录而不是文件：{}（目录请用 --out-dir）".format(target),
            code="OUT_TARGET_IS_DIRECTORY",
        )
    directory = Path(target).parent
    if str(directory) and not directory.is_dir():
        os.makedirs(str(directory), exist_ok=True)


def _targets(out, out_dir, default_name):
    if out:
        return str(out)
    if out_dir:
        return str(Path(out_dir) / default_name)
    return None


def run_merge(
    method,
    ledger_path=None,
    attribution_path=None,
    data_dir=None,
    out=None,
    out_dir=None,
):
    """执行多账户合并；返回 ``MergeResult``（含退出码）。"""
    resolved = resolve_data_dir(data_dir)
    _require_method(method)
    if not ledger_path:
        raise UsageError(
            "必须显式指定 --ledger 统一流水路径（划转两腿要从原始行取证）",
            code="LEDGER_REQUIRED",
        )
    if not attribution_path:
        raise UsageError(
            "必须显式指定 --attribution 归因产物路径（合并视图不重算成本）",
            code="ATTRIBUTION_REQUIRED",
        )

    target = _targets(out, out_dir, MERGED_FILE)
    if out_dir and not Path(out_dir).is_dir():
        os.makedirs(str(out_dir), exist_ok=True)
    if target:
        _ensure_dir(target)

    return build_merge(
        ledger_path,
        method,
        resolved,
        attribution_path=attribution_path,
        out_path=target,
    )


def describe_merge(result):
    """给 CLI 用的中文摘要行（只陈述结果，不做业务判断）。"""
    artifact = result.artifact
    merged = artifact["merged"]
    transfers = artifact["transfers"]
    checks = artifact["checks"]
    lines = [
        "多账户合并完成（{}）：{} 个账户 / 期末总资产 {:.2f} 元".format(
            artifact["method_label"],
            checks["account_count"],
            merged["terminal_total_fen"] / 100.0,
        ),
        "  合并前后总收益 {:.2f} 元（差 {} 分，门槛 ≤ {} 分）：{}".format(
            checks["total_return_after_fen"] / 100.0,
            checks["difference_fen"],
            checks["tolerance_fen"],
            "守恒" if checks["conserved"] else "**不守恒**",
        ),
        "  账户间划转 {} 笔（已抵消 {} 笔，净额 {} 分）{}".format(
            len(transfers["entries"]),
            transfers["matched_count"],
            transfers["net_fen"],
            "，未识别到划转模式" if not transfers["entries"] else "",
        ),
    ]
    for entry in transfers["entries"]:
        lines.append(
            "    划转 {} {}：{} 分 / {} 腿 / 净额 {} 分 / {}".format(
                entry["contract_no"],
                entry["trade_date"],
                entry["amount_fen"],
                entry["leg_count"],
                entry["net_fen"],
                "已抵消" if entry["offset"]["balanced"] else "**未抵消**",
            )
        )
    lines.append(
        "  合并持仓 {} 个标的（同名标的已汇总）".format(len(artifact["holdings"]))
    )
    for item in artifact["invalid"][:10]:
        lines.append("  问题 {} {}".format(item["code"], item["message"]))
    if len(artifact["invalid"]) > 10:
        lines.append("  … 其余 {} 条见产物 invalid 段".format(len(artifact["invalid"]) - 10))
    return lines
