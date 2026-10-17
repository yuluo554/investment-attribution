"""编排层：每个子命令一个纯函数，输入输出皆可序列化。

CLI 只做参数解析与退出码转译，业务语义在这里。直调与 CLI 必须行为等价
（plan/02 §二 编排层唯一业务入口）。
"""

from .actions_cmd import run_corporate_actions  # noqa: F401
from .import_cmd import run_import  # noqa: F401
