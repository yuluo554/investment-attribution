"""CLI 入口。

七条子命令 + ``run`` / ``bench`` / ``selftest``。骨架期未落地的子命令以
退出码 3 显式拦截，**不输出任何伪造结果**。

参数语义纪律：
- None 表示"用户没传"，0 / 空串是合法值，不得用 ``or DEFAULT`` 吞掉；
- ``--data-dir`` 缺省走源码树探测，不回落 CWD。
"""

import argparse
import sys

from . import __version__
from .errors import IAError, UsageError
from .exit_codes import (
    EXIT_INPUT,
    EXIT_OK,
    EXIT_STAGE_NOT_READY,
)
from .paths import resolve_data_dir
from .pipeline.actions_cmd import describe_actions, run_corporate_actions
from .pipeline.cost_cmd import describe_holdings, run_cost, run_pnl
from .pipeline.import_cmd import exit_code_for, run_import
from .selftest import run_selftest
from .serialize import dumps_json, write_atomic, write_json_atomic
from .stages import require_ready

SUBCOMMANDS = (
    "import",
    "cost",
    "pnl",
    "corporate-actions",
    "attribution",
    "merge",
    "report",
    "run",
    "bench",
    "selftest",
)


def _ensure_utf8_stdio():
    """只在本机 stdio 不是 UTF-8 时重配，保住中文控制台原有编码。"""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding and encoding not in ("utf8", "utf8mb4", "cp65001"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, ValueError):
                pass


def build_parser():
    parser = argparse.ArgumentParser(
        prog="investment-attribution",
        description="券商交割单收益归因工作台：纯本地、零上传、可审计。不做投资建议，只做账。",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--data-dir",
        default=None,
        metavar="DIR",
        help="数据目录（缺省：源码树 data/；也可用环境变量 IA_DATA_DIR）",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    for name in ("import",):
        p = sub.add_parser(name, help="导入交割单，产出统一流水与行级错误台账")
        p.add_argument("input", nargs="+", help="券商导出的交割单文件")
        p.add_argument("--out-dir", default=None, help="产物输出目录（统一流水 / 错误台账 / 指纹 / 账户映射）")
        p.add_argument("--out", default=None, help="统一流水输出路径（覆盖 --out-dir 默认名）")
        p.add_argument("--errors", default=None, help="行级错误台账输出路径（覆盖 --out-dir 默认名）")
        p.add_argument("--accounts", default=None, help="账户登记表 JSON（覆盖随包登记表）")

    for name in ("cost", "pnl"):
        p = sub.add_parser(name, help="成本台账 / 已实现未实现收益")
        p.add_argument("--method", choices=("fifo", "wavg"), default=None, help="成本口径（必填，无默认值）")
        p.add_argument("--ledger", default=None, help="统一流水输入路径（必填）")
        p.add_argument("--out", default=None, help="产物输出路径")
        p.add_argument("--out-dir", default=None, help="产物输出目录（默认名见 README）")
        p.add_argument("--fees", action="store_true", help="拆分费用三件套")

    p = sub.add_parser("corporate-actions", help="公司行为还原与人工确认队列")
    p.add_argument("--method", choices=("fifo", "wavg"), default=None, help="成本口径（必填，无默认值）")
    p.add_argument("--ledger", default=None, help="统一流水输入路径")
    p.add_argument("--out", default=None, help="事件台账输出路径")
    p.add_argument("--out-dir", default=None, help="产物输出目录（默认名见 README）")
    p.add_argument("--pending-out", default=None, help="待确认队列输出路径")
    p.add_argument("--confirm", default=None, help="人工确认记录输入路径")

    p = sub.add_parser("attribution", help="XIRR / TWR / 收益分解")
    p.add_argument("--xirr", action="store_true", help="输出 XIRR")
    p.add_argument("--twr", action="store_true", help="输出 TWR 净值曲线")
    p.add_argument("--twr-basis", choices=("cashflow", "daily"), default=None, help="TWR 估值点口径")
    p.add_argument("--benchmark", default=None, help="本地基准 CSV（可选）")
    p.add_argument("--out", default=None, help="产物输出路径")

    p = sub.add_parser("merge", help="多账户合并与划转抵消")
    p.add_argument("--accounts", default=None, help="账户登记表")
    p.add_argument("--out", default=None, help="合并产物输出路径")

    p = sub.add_parser("report", help="生成单文件本地 HTML 报告")
    p.add_argument("--merged", default=None, help="合并产物输入路径")
    p.add_argument("--out", default=None, help="HTML 输出路径")
    p.add_argument("--json-out", default=None, help="机读 report_data.json 输出路径")

    p = sub.add_parser("run", help="一条命令跑完整链路")
    p.add_argument("input", nargs="+", help="券商导出的交割单文件")
    p.add_argument("--cost-method", choices=("fifo", "wavg"), default=None, help="成本口径（必填）")
    p.add_argument("--out-dir", default=None, help="全部中间产物与报告的输出目录")

    p = sub.add_parser("bench", help="运行内置基准套件 B1–B5")
    p.add_argument("--only", default=None, help="只跑指定套件（如 B1）")

    p = sub.add_parser("selftest", help="可证伪自检（离开开发机也能证明完整性）")
    p.add_argument("--json-out", default=None, help="把自检结果写入 JSON 文件")
    p.add_argument("--strict", action="store_true", help="任一检查失败即以非零退出码结束")

    return parser


def _cmd_selftest(args):
    data_dir = resolve_data_dir(args.data_dir)
    result = run_selftest(data_dir)
    text = dumps_json(result)
    sys.stdout.write(text)
    if args.json_out:
        write_atomic(args.json_out, text)
    if args.strict and not result["ok"]:
        return EXIT_INPUT
    return EXIT_OK


def _cmd_import(args):
    """导入交割单：成功 0 / 有行级问题 1 / 整份文件不可用 2。"""
    result = run_import(
        args.input,
        data_dir=args.data_dir,
        out_dir=args.out_dir,
        accounts_path=args.accounts,
        ledger_path=args.out,
        errors_path=args.errors,
    )
    summary = result.ledger["summary"]
    sys.stderr.write(
        "导入完成：{} 个文件 / {} 行入库 / {} 行拒绝 / {} 个重复文件 / {} 行待确认账户\n".format(
            summary["files_input"],
            summary["rows_accepted"],
            summary["rows_rejected"],
            summary["files_duplicate"],
            summary["rows_pending_account"],
        )
    )
    for target in ("ledger", "errors", "fingerprints", "account_map"):
        if target in result.paths:
            sys.stderr.write("  {}: {}\n".format(target, result.paths[target]))
    if result.degraded:
        pending = result.account_map["pending"]
        if pending:
            sys.stderr.write(
                "待确认账户（未并入默认账户）：{}\n".format(
                    ", ".join(str(item["account_id"]) for item in pending)
                )
            )
        if result.errors["summary"]["error_count"]:
            sys.stderr.write(
                "行级失败 {} 行，已写入错误台账（原始行文本 + 源文件行号 + 原因）。\n".format(
                    result.errors["summary"]["error_count"]
                )
            )
    return exit_code_for(result)


def _cmd_cost(args):
    """成本台账：成功 0 / 检出勾稽问题 1 / 用法或输入不可用 2。"""
    result = run_cost(
        args.method,
        ledger_path=args.ledger,
        data_dir=args.data_dir,
        out=args.out,
        out_dir=args.out_dir,
        include_fees=args.fees,
    )
    for line in describe_holdings(result):
        sys.stderr.write(line + "\n")
    return result.exit_code


def _cmd_pnl(args):
    """收益台账：失败原因口径同 ``cost``。"""
    result, payload = run_pnl(
        args.method,
        ledger_path=args.ledger,
        data_dir=args.data_dir,
        out=args.out,
        out_dir=args.out_dir,
        include_fees=args.fees,
    )
    holdings = result.holdings
    totals = holdings["totals"]
    sys.stderr.write(
        "收益台账完成（{}）：已实现净收益 {:.2f} 元 / 未平仓成本 {:.2f} 元 / {} 条问题\n".format(
            holdings["method_label"],
            payload["realized"]["net_fen"] / 100.0,
            payload["open_positions"]["cost_fen"] / 100.0,
            totals["invalid"],
        )
    )
    if payload.get("fee_detail") is not None:
        fees = payload["fees"]["by_type_fen"]
        sys.stderr.write(
            "  费用拆列：佣金 {:.2f} / 印花税 {:.2f} / 过户费 {:.2f} / 其他 {:.2f} / 未分列 {:.2f}\n".format(
                fees["commission"] / 100.0,
                fees["stamp_tax"] / 100.0,
                fees["transfer_fee"] / 100.0,
                fees["other_fee"] / 100.0,
                fees["unallocated"] / 100.0,
            )
        )
    for item in holdings["invalid"][:10]:
        sys.stderr.write(
            "  问题 {} {}:{} {}\n".format(
                item["code"], item["source_file"], item["source_line_no"], item["message"]
            )
        )
    return result.exit_code


def _cmd_corporate_actions(args):
    """公司行为：成功 0 / 有待确认或调整问题 1 / 用法或输入不可用 2。"""
    result = run_corporate_actions(
        args.method,
        ledger_path=args.ledger,
        data_dir=args.data_dir,
        out=args.out,
        out_dir=args.out_dir,
        pending_out=args.pending_out,
        confirm=args.confirm,
    )
    for line in describe_actions(result):
        sys.stderr.write(line + "\n")
    return result.exit_code


def _cmd_stage_not_ready(command):
    require_ready(command)
    # 走到这里说明登记表与分派不一致，属实现缺陷，不能静默返回成功。
    raise UsageError(
        "子命令 {} 未登记未就绪状态，但也没有实现。".format(command),
        code="STAGE_TABLE_MISMATCH",
    )


HANDLERS = {
    "import": _cmd_import,
    "cost": _cmd_cost,
    "pnl": _cmd_pnl,
    "corporate-actions": _cmd_corporate_actions,
    "selftest": _cmd_selftest,
}


def main(argv=None):
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return EXIT_INPUT

    handler = HANDLERS.get(args.command)
    try:
        if handler is not None:
            return handler(args)
        return _cmd_stage_not_ready(args.command)
    except IAError as exc:
        sys.stderr.write("{} [{}]: {}\n".format(type(exc).__name__, exc.code, exc))
        return exc.exit_code
