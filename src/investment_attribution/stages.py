"""阶段未就绪登记表。

骨架期用退出码 3 显式拦截尚未落地的模块，**绝不伪造空结果**。每个模块
落地后：① 从 ``STAGE_NOT_READY`` 删条目；② 改 CLI 分派；③ 改 README 退出码表；
④ 记决策记录；⑤ 改测试（含防复活负断言）。

``selftest`` 不在此表内——它是 M0 就必须真实可证伪的命令。
"""

from .errors import StageNotReady

STAGE_NOT_READY = {
    "import": "M1",
    "cost": "M2",
    "pnl": "M2",
    "corporate-actions": "M3",
    "attribution": "M4",
    "merge": "M5",
    "report": "M5",
    "run": "M5",
    "bench": "M5",
}


def require_ready(command):
    """命令未就绪则抛出 ``StageNotReady``（CLI 转退出码 3）。"""
    milestone = STAGE_NOT_READY.get(command)
    if milestone is not None:
        raise StageNotReady(
            "子命令 {} 尚未落地（计划里程碑 {}）；本骨架不输出伪造结果。".format(
                command, milestone
            ),
            code="STAGE_NOT_READY:{}".format(command),
        )
