"""退出码契约。

语义一旦写进 README 就不得漂移：
- 0 = 成功
- 1 = 降级完成 / 已检出问题（详见各子命令说明）
- 2 = 用法错误或输入不可用（参数错、文件缺失、格式不可用）
- 3 = 阶段未就绪（模块尚未落地，显式拦截；**绝不伪造空结果**）

退出码 3 是临时语义，随模块落地逐个退役。退役时须同步五处：
常量表 → CLI 分派 → README 退出码表 → 决策记录 → 测试（含防复活断言）。
"""

EXIT_OK = 0
EXIT_DEGRADED = 1
EXIT_INPUT = 2
EXIT_STAGE_NOT_READY = 3

ALL_EXIT_CODES = (
    EXIT_OK,
    EXIT_DEGRADED,
    EXIT_INPUT,
    EXIT_STAGE_NOT_READY,
)
