"""退出码契约。

语义一旦写进 README 就不得漂移：

- ``0`` 成功
- ``1`` 降级完成 / 已检出问题（详见各子命令说明）
- ``2`` 用法错误或输入不可用（参数错、文件缺失、格式不可用）

骨架期的临时语义 ``3 = 阶段未就绪`` **已在 M5 全部退役**：十条子命令
（``import`` / ``cost`` / ``pnl`` / ``corporate-actions`` / ``attribution`` /
``merge`` / ``report`` / ``run`` / ``bench`` / ``selftest``）全部落地，CLI
不再有"未就绪"这条出口。随之删除的还有阶段登记表与未就绪异常类，不留
"看起来还会拦截"的幽灵代码——幽灵拦截和过时断言一样会骗人。
"""

EXIT_OK = 0
EXIT_DEGRADED = 1
EXIT_INPUT = 2

ALL_EXIT_CODES = (
    EXIT_OK,
    EXIT_DEGRADED,
    EXIT_INPUT,
)
