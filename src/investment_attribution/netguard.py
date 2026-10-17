"""纯本地零上传的静态门禁（M0 版）。

本模块只做**静态 import 审计**：源码树里任何模块都不得 import 网络栈。
运行期 socket 探针（四层审计的第三层）在 M4 落地——静态门禁不能替代
动态拦截，用词口径必须与审计范围对齐。

注意：本文件自身的字符串里出现网络库名属于"判定词表"，不是 import，
AST 只读 ``Import`` / ``ImportFrom`` 节点，不读字符串字面量。
"""

import ast
import os

FORBIDDEN_TOP_LEVEL = frozenset(
    ("socket", "ssl", "urllib", "urllib2", "http", "httplib", "requests", "urllib3", "aiohttp", "ftplib", "smtplib", "telnetlib", "xmlrpc")
)


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
