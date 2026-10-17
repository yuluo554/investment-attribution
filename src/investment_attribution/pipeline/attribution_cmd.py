"""``attribution`` 子命令编排。

退出码语义（K1 / K12）：

- ``0``：XIRR / TWR / 拆分 / 费用 / 分解全部算成，无问题；
- ``1``：数据可用但检出问题（XIRR 无解、估值缺价、数量对不上台账、拆分不守恒、
  基准缺日期）；
- ``2``：用法或输入不可用（未指定成本口径 / 流水 / 估值表，文件不可读，
  估值早于流水末尾）。

成本口径**无默认值**（K7）；TWR 估值点口径有默认值 ``cashflow``，来自参数包
（既定口径，非遗漏）。
"""

import os
from pathlib import Path

from ..attribution import ATTRIBUTION_FILE, build_attribution
from ..errors import UsageError
from ..paths import resolve_data_dir


def _require_method(method):
    """K7：成本口径无默认值；先报"缺口径"再报"缺输入"，与 cost/pnl 同序。"""
    if method is None:
        raise UsageError(
            "必须显式指定 --method fifo|wavg（成本口径无默认值）",
            code="COST_METHOD_REQUIRED",
        )
    return method


def _require_ledger(ledger_path):
    if not ledger_path:
        raise UsageError(
            "必须显式指定 --ledger 统一流水路径（先运行 import 生成）",
            code="LEDGER_REQUIRED",
        )
    return ledger_path


def _require_valuation(valuation_path):
    if not valuation_path:
        raise UsageError(
            "必须显式指定 --valuation 估值表（未实现收益与 XIRR 期末市值都取自它）",
            code="VALUATION_REQUIRED",
        )
    return valuation_path


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


def run_attribution(
    method,
    ledger_path=None,
    data_dir=None,
    valuation_path=None,
    benchmark_path=None,
    twr_basis=None,
    sections=("xirr", "twr"),
    out=None,
    out_dir=None,
):
    """执行收益归因；返回 ``AttributionResult``（含退出码）。"""
    resolved = resolve_data_dir(data_dir)
    _require_method(method)
    ledger = _require_ledger(ledger_path)
    valuation = _require_valuation(valuation_path)

    target = _targets(out, out_dir, ATTRIBUTION_FILE)
    if out_dir and not Path(out_dir).is_dir():
        os.makedirs(str(out_dir), exist_ok=True)
    if target:
        _ensure_dir(target)

    return build_attribution(
        ledger,
        method,
        resolved,
        valuation_path=valuation,
        benchmark_path=benchmark_path,
        twr_basis=twr_basis,
        sections=sections,
        out_path=target,
    )


def describe_attribution(result):
    """给 CLI 用的中文摘要行（只陈述结果，不做业务判断）。"""
    artifact = result.artifact
    basis = artifact["basis"]
    lines = [
        "收益归因完成（{} / TWR 估值点 = {}）：{} 个账户 / 估值日 {} / 基准{}".format(
            artifact["method_label"],
            basis["twr_basis_label"],
            len(artifact["pnl"]["accounts"]),
            basis["valuation_date"],
            "已提供" if basis["benchmark_provided"] else "未提供（超额列不显示）",
        )
    ]
    merged = artifact["pnl"]["merged"]
    lines.append(
        "  已实现 {:.2f} 元（含分红 {:.2f}）/ 未实现 {:.2f} / 总收益 {:.2f} 元".format(
            merged["realized_total_fen"] / 100.0,
            (merged["dividend_income_fen"] + merged["reinvested_income_fen"]) / 100.0,
            merged["unrealized_fen"] / 100.0,
            merged["total_return_fen"] / 100.0,
        )
    )
    for view in artifact.get("xirr", {}).get("accounts", []) or []:
        if view["status"] == "ok":
            lines.append(
                "  XIRR {}：{}（净投入 {:.2f} 元 / 期末 {:.2f} 元）".format(
                    view["label"],
                    view["rate"],
                    view["net_contribution_fen"] / 100.0,
                    view["terminal_total_fen"] / 100.0,
                )
            )
        else:
            lines.append("  XIRR {}：无解（{}）".format(view["label"], view["status"]))
    for view in artifact.get("twr", {}).get("accounts", []) or []:
        lines.append(
            "  TWR {}：{}（{} 段）".format(
                view["label"],
                view["cumulative_rate"]
                if view["cumulative_rate"] is not None
                else view["status"],
                view.get("segment_count", 0),
            )
        )
    fees = artifact["fees"]
    lines.append(
        "  费用 {:.2f} 元（占累计投入 {} bps）/ 与 M2 总额一致 {}".format(
            fees["total_fen"] / 100.0,
            fees["drag_bps"],
            fees["matches_m2_total"],
        )
    )
    checks = artifact["decomposition"]["checks"]
    lines.append(
        "  分解守恒：{}{}".format(
            "通过" if checks["all_equal"] else "未通过",
            "（未分类标的 {}）".format(
                ",".join(artifact["decomposition"]["unclassified_symbols"])
            )
            if artifact["decomposition"]["unclassified_symbols"]
            else "",
        )
    )
    for item in artifact["invalid"][:10]:
        lines.append("  问题 {} {}".format(item["code"], item["message"]))
    if len(artifact["invalid"]) > 10:
        lines.append("  … 其余 {} 条见产物 invalid 段".format(len(artifact["invalid"]) - 10))
    for item in artifact["warnings"][:5]:
        lines.append("  提示 {} {}".format(item["code"], item["message"]))
    return lines
