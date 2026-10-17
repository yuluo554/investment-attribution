"""纯本地零上传的两层审计。

**第一层（静态）**：``scan_network_imports`` 做 AST import 审计——源码树里
任何模块都不得 import 网络栈。它回答"代码里有没有出网路径"。

**第二层（运行期）**：``runtime_probe`` 用 ``sys.addaudithook`` 在进程内拦
截真实网络调用（建 socket、域名解析、urllib/ftplib/smtplib 请求），记账并
抛 ``NetworkAccessBlocked``。它回答"这一次运行有没有真的出网"。静态门禁
替代不了动态拦截：静态审计看不出运行期才装配的调用路径。

探针**默认不装**（``armed=False``），由 CLI 入口或归因引擎显式武装；
这样库态导入本模块的第三方代码不会被误伤。审计钩子一旦装上就不能卸载，
所以"关掉探针"= 置 ``armed=False``，钩子仍在校对但不动作。

注意：本文件自身的字符串里出现网络库名属于"判定词表"，不是 import；
AST 只读 ``Import`` / ``ImportFrom`` 节点，不读字符串字面量。
"""

import ast
import os
import sys
from contextlib import contextmanager

from .errors import DegradedError

FORBIDDEN_TOP_LEVEL = frozenset(
    ("socket", "ssl", "urllib", "urllib2", "http", "httplib", "requests", "urllib3", "aiohttp", "ftplib", "smtplib", "telnetlib", "xmlrpc")
)

#: 运行期探针拦截的审计事件前缀（CPython 审计事件的命名空间）。
NETWORK_AUDIT_PREFIXES = (
    "socket.",
    "ssl.",
    "urllib.",
    "ftplib.",
    "smtplib.",
    "telnetlib.",
    "http.client.",
)


class NetworkAccessBlocked(DegradedError):
    """运行期探针拦截到网络访问尝试（对应退出码 1）。"""

    code = "NETWORK_ACCESS_BLOCKED"

    def __init__(self, message, *, event=None):
        super().__init__(message)
        self.event = event


class RuntimeNetworkProbe(object):
    """运行期零出网探针：记账 + 拦截。

    ``attempts`` 逐条记录被拦截的审计事件名；``armed`` 为 False 时钩子
    只做透传（进程内其它组件用 socket 不受影响）。
    """

    def __init__(self):
        self.attempts = []
        self.armed = False
        self._installed = False
        self._depth = 0

    @property
    def installed(self):
        return self._installed

    def install(self):
        """幂等安装审计钩子；同一进程内只装一次。"""
        if not self._installed:
            sys.addaudithook(self._audit_hook)
            self._installed = True
        return self

    def reset(self):
        self.attempts = []

    def _audit_hook(self, event, args):
        if not self.armed:
            return
        for prefix in NETWORK_AUDIT_PREFIXES:
            if event.startswith(prefix):
                self.attempts.append(event)
                raise NetworkAccessBlocked(
                    "运行期零上传探针拦截网络调用：{}".format(event), event=event
                )


PROBE = RuntimeNetworkProbe()


def runtime_probe():
    """返回进程内唯一的探针对象（并确保钩子已安装）。"""
    return PROBE.install()


@contextmanager
def armed_runtime_probe():
    """武装探针执行一段代码；退出时解除武装并返回本次拦截次数。

    支持嵌套：内层退出时只减深度，最外层退出才真正解除武装。
    """
    probe = runtime_probe()
    probe._depth += 1
    probe.armed = True
    try:
        yield probe
    finally:
        probe._depth -= 1
        if probe._depth <= 0:
            probe._depth = 0
            probe.armed = False


def _iter_python_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _imported_top_levels(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".", 1)[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module is None:
                continue
            yield node.module.split(".", 1)[0]


def scan_network_imports(root):
    """返回违规清单 ``[(相对路径, 行号, 导入名)]``；空列表表示通过。"""
    root = os.path.abspath(root)
    violations = []
    for path in _iter_python_files(root):
        with open(path, "r", encoding="utf-8") as stream:
            source = stream.read()
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            violations.append((os.path.relpath(path, root), exc.lineno or 0, "<syntax-error>"))
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module.split(".", 1)[0]]
            for name in names:
                if name in FORBIDDEN_TOP_LEVEL:
                    violations.append((os.path.relpath(path, root), node.lineno, name))
    return violations
