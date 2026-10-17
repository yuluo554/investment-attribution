"""``corporate-actions`` 子命令编排。

退出码语义（K1 / K12）：

- ``0``：全部公司行为行都被规则表唯一确定，待确认队列为空；
- ``1``：数据可用但检出问题（待确认队列非空 / 调整不可逆 / 分桶不平）；
- ``2``：用法或输入不可用（未指定口径、流水缺失、规则包损坏、确认记录非法）。

成本口径**无默认值**：未传 ``--method`` 一律 ``exit 2``，不猜。
"""

import os
from pathlib import Path

from ..actions import ACTIONS_FILE, PENDING_FILE, build_actions
from ..errors import UsageError
from ..paths import resolve_data_dir


def _require_ledger(ledger_path):
    if not ledger_path:
        raise UsageError(
            "必须显式指定 --ledger 统一流水路径（先运行 import 生成）",
            code="LEDGER_REQUIRED",
        )
    return ledger_path


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


def run_corporate_actions(
    method,
    ledger_path=None,
    data_dir=None,
    out=None,
    out_dir=None,
    pending_out=None,
    confirm=None,
):
    """执行公司行为还原；返回 ``ActionsResult``（含退出码）。"""
    resolved = resolve_data_dir(data_dir)
    ledger = _require_ledger(ledger_path)

    actions_out = _targets(out, out_dir, ACTIONS_FILE)
    pending_target = pending_out
    if pending_target is None and out_dir:
        pending_target = str(Path(out_dir) / PENDING_FILE)

    if out_dir and not Path(out_dir).is_dir():
        os.makedirs(str(out_dir), exist_ok=True)
    for target in (actions_out, pending_target):
        if target:
            _ensure_dir(target)

    return build_actions(
        ledger,
        method,
        resolved,
        out_path=actions_out,
        pending_path=pending_target,
        confirm_path=confirm,
    )


def describe_actions(result):
    """给 CLI 用的中文摘要行（只陈述结果，不做业务判断）。"""
    actions = result.actions
    summary = actions["summary"]
    lines = [
        "公司行为台账完成（{}）：{} 条 other 方向行 / {} 条事件 / {} 条待确认 / {} 条非行为".format(
            actions["method_label"],
            summary["other_rows"],
            summary["action_events"],
            summary["pending"],
            summary["non_action_rows"],
        )
    ]
    by_type = summary["by_event_type"]
    if by_type:
        lines.append(
            "  事件分类：{}".format(
                " / ".join("{} {}".format(name, by_type[name]) for name in sorted(by_type))
            )
        )
    lines.append(
        "  规则包 {}（{} 条规则，sha256 {}…）/ 调整可逆校验 {} 条，失败 {}".format(
            actions["rule_pack"]["path"],
            actions["rule_pack"]["rule_count"],
            actions["rule_pack"]["sha256"][:12],
            summary["reversibility"]["checked"],
            summary["reversibility"]["failed"],
        )
    )
    for item in actions["pending"][:10]:
        lines.append(
            "  待确认 {} {}:{} {}（{}）".format(
                item["reason"],
                item["source_file"],
                item["source_line_no"],
                item["business"],
                item["message"],
            )
        )
    if len(actions["pending"]) > 10:
        lines.append("  … 其余 {} 条见产物 pending 段".format(len(actions["pending"]) - 10))
    for item in actions["problems"][:10]:
        lines.append(
            "  问题 {} {}:{} {}".format(
                item["code"], item["source_file"], item["source_line_no"], item["message"]
            )
        )
    return lines
