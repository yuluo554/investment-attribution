"""``run`` 子命令编排：一条命令跑完整链路。

**纪律**：``run`` 只是编排——它按固定顺序调用与逐命令完全相同的同一批纯函数，
写出的产物与逐命令调用**逐字节一致**。``run`` 里没有第二套计算路径，也没有
自己的默认值：口径、流水、估值表全部来自 CLI 参数。

顺序固定为：

    import → cost → pnl --fees → corporate-actions → attribution → merge → report

退出码：取各步退出码的**最大值**（0/1）——任何一步降级都如实反映在总码上；
任一步用法或输入不可用（2）直接抛出，链路随即中断。样例如含待确认的公司
行为行，``corporate-actions`` 按既定契约返回 1，故 ``run`` 也返回 1——这条
1 是**数据结论**，与网络无关（全链路不依赖任何网络能力）。
"""

import os
from pathlib import Path

from ..actions import ACTIONS_FILE
from ..attribution import ATTRIBUTION_FILE
from ..errors import UsageError
from ..ingest.service import LEDGER_FILE
from ..ledger import HOLDINGS_FILE, SNAPSHOTS_FILE
from ..merge import MERGED_FILE
from ..paths import resolve_data_dir
from ..report import REPORT_DATA_FILE, REPORT_HTML_FILE
from .attribution_cmd import run_attribution
from .cost_cmd import PNL_FILE, run_cost, run_pnl
from .import_cmd import run_import
from .merge_cmd import run_merge
from .report_cmd import run_report
from .actions_cmd import run_corporate_actions

#: 链路固定的产物文件名（与逐命令的默认名一一对应）。
ARTIFACT_NAMES = (
    LEDGER_FILE,
    "import_errors.json",
    "fingerprints.json",
    "account_map.json",
    HOLDINGS_FILE,
    SNAPSHOTS_FILE,
    PNL_FILE,
    ACTIONS_FILE,
    "pending.json",
    ATTRIBUTION_FILE,
    MERGED_FILE,
    REPORT_HTML_FILE,
    REPORT_DATA_FILE,
)


class RunResult(object):
    def __init__(self, steps, exit_code):
        self.steps = steps
        self.exit_code = exit_code


def run_all(
    inputs,
    method=None,
    data_dir=None,
    valuation_path=None,
    out_dir=None,
    benchmark_path=None,
    twr_basis=None,
):
    """执行完整链路；返回 ``RunResult``（各步结果 + 总退出码）。"""
    resolved = resolve_data_dir(data_dir)
    if method is None:
        raise UsageError(
            "必须显式指定 --method fifo|wavg（成本口径无默认值）",
            code="COST_METHOD_REQUIRED",
        )
    if not out_dir:
        raise UsageError(
            "必须显式指定 --out-dir（链路各步产物都落在它下面）",
            code="OUT_DIR_REQUIRED",
        )
    if not valuation_path:
        raise UsageError(
            "必须显式指定 --valuation 估值表（未实现收益与期末市值都取自它）",
            code="VALUATION_REQUIRED",
        )
    output = Path(out_dir)
    if not output.is_dir():
        os.makedirs(str(output), exist_ok=True)

    ledger_path = str(output / LEDGER_FILE)
    steps = []
    codes = []

    imported = run_import(inputs, data_dir=resolved, out_dir=str(output))
    steps.append(("import", imported))
    codes.append(imported.exit_code)

    cost = run_cost(method, ledger_path=ledger_path, data_dir=resolved, out_dir=str(output))
    steps.append(("cost", cost))
    codes.append(cost.exit_code)

    pnl, _payload = run_pnl(
        method,
        ledger_path=ledger_path,
        data_dir=resolved,
        out_dir=str(output),
        include_fees=True,
    )
    steps.append(("pnl", pnl))
    codes.append(pnl.exit_code)

    actions = run_corporate_actions(
        method, ledger_path=ledger_path, data_dir=resolved, out_dir=str(output)
    )
    steps.append(("corporate-actions", actions))
    codes.append(actions.exit_code)

    attribution = run_attribution(
        method,
        ledger_path=ledger_path,
        data_dir=resolved,
        valuation_path=valuation_path,
        benchmark_path=benchmark_path,
        twr_basis=twr_basis,
        sections=("xirr", "twr"),
        out_dir=str(output),
    )
    steps.append(("attribution", attribution))
    codes.append(attribution.exit_code)

    merged = run_merge(
        method,
        ledger_path=ledger_path,
        attribution_path=str(output / ATTRIBUTION_FILE),
        data_dir=resolved,
        out_dir=str(output),
    )
    steps.append(("merge", merged))
    codes.append(merged.exit_code)

    report = run_report(merged_path=str(output / MERGED_FILE), out_dir=str(output))
    steps.append(("report", report))
    codes.append(report.exit_code)

    return RunResult(steps, max(codes) if codes else 0)


def describe_run(result):
    """给 CLI 用的中文摘要行（只陈述各步退出码，不做业务判断）。"""
    lines = ["完整链路完成：{} 步 / 总退出码 {}".format(len(result.steps), result.exit_code)]
    for name, step in result.steps:
        lines.append(
            "  {}：退出码 {} {}".format(
                name,
                step.exit_code,
                "（无问题）" if step.exit_code == 0 else "（有需要人看的东西）",
            )
        )
    return lines
