"""渲染层零判定静态门禁（M5）。

**为什么需要它**：报告要"看起来对"很容易——在模板里塞一句
``if method == "fifo": ...`` 就能让输出变个样子。但那样报告就**自己**成了
第二套判定路径：口径、事件类型、收益口径一旦在渲染层被重新解释，报告与
产物就可能不一致，而且不一致还会被"渲染正确"掩盖。

**三条判定，一条都不许出现在渲染层**：

- ``R1 成本口径``：不得按成本口径分支（``fifo`` / ``wavg`` / ``cost_method`` …）；
- ``R2 事件类型``：不得按事件或行形态分支（``event_type`` / ``dividend`` /
  ``split`` / ``trade_buy`` …）；
- ``R3 收益口径``：不得重新解释收益构成（``realized`` / ``unrealized`` /
  ``total_return`` / ``xirr`` / ``twr`` …）。

判定词表覆盖**字符串字面量、标识符、属性名**三类载体——三者都是"渲染层
真的在提这个词"的证据。唯一的例外是标准库方法名与业务词同形的少数情况
（``split`` / ``join`` / ``get`` …），这些列进 ``SAFE_ATTRIBUTES`` 白名单，
只作为**方法名**放行；写成字符串或变量仍然算命中。

门禁与它的正/负对照都在 ``tests/test_report.py``：没有阳性对照的门禁
只是装饰。
"""

import ast
import os

#: 三条判定各自的判定词表。
RULES = (
    ("R1_cost_method", ("cost_method_label", "cost_method", "fifo", "wavg")),
    (
        "R2_event_type",
        (
            "event_type",
            "rule_id",
            "stock_dividend",
            "reinvested_dividend",
            "rights_issue",
            "corporate_action",
            "trade_buy",
            "trade_sell",
            "receipt_free",
            "receipt_paid",
            "receipt_valued",
            "cash_event",
            "dividend",
            "split",
        ),
    ),
    (
        "R3_return_basis",
        (
            "net_contribution",
            "total_return",
            "annualized",
            "unrealized",
            "realized",
            "drag_bps",
            "excess",
            "xirr",
            "twr",
        ),
    ),
)

#: 标准库方法名与业务词同形时的白名单（只作方法名放行）。
SAFE_ATTRIBUTES = frozenset(
    (
        "split",
        "join",
        "strip",
        "lstrip",
        "rstrip",
        "format",
        "replace",
        "startswith",
        "endswith",
        "lower",
        "upper",
        "title",
        "items",
        "keys",
        "values",
        "get",
        "append",
        "extend",
        "insert",
        "sort",
        "setdefault",
        "copy",
        "update",
        "add",
        "pop",
        "index",
        "count",
        "encode",
        "decode",
    )
)

RENDERER_RELATIVE_PATH = os.path.join("report", "render.py")


def _tokens_in(text):
    """返回 ``text`` 命中的 ``(rule, token)``；同一处只取最长命中。"""
    found = []
    for rule, tokens in RULES:
        best = None
        for token in tokens:
            if token in text and (best is None or len(token) > len(best)):
                best = token
        if best is not None:
            found.append((rule, best))
    return found


def scan_source(text, filename="<source>"):
    """扫描一段源码，返回违例清单 ``[{rule, token, line, filename}]``。"""
    violations = []
    tree = ast.parse(text, filename=filename)
    for node in ast.walk(tree):
        candidates = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            candidates.append(node.value)
        elif isinstance(node, ast.Name):
            candidates.append(node.id)
        elif isinstance(node, ast.Attribute):
            if node.attr not in SAFE_ATTRIBUTES:
                candidates.append(node.attr)
        for candidate in candidates:
            for rule, token in _tokens_in(candidate):
                violations.append(
                    {
                        "rule": rule,
                        "token": token,
                        "line": getattr(node, "lineno", 0),
                        "filename": filename,
                    }
                )
    return violations


def scan_file(path):
    with open(path, "r", encoding="utf-8") as stream:
        return scan_source(stream.read(), os.path.basename(path))


def scan_renderer(package_root):
    """扫描渲染层源码；返回违例清单（空 = 渲染层零判定）。"""
    return scan_file(os.path.join(str(package_root), RENDERER_RELATIVE_PATH))


# ---------------------------------------------------------------------------
# 零外链出口断言
# ---------------------------------------------------------------------------

#: 外部引用形态：绝对地址与协议相对地址。
EXTERNAL_MARKERS = ("https://", "http://", "//")


def scan_external_references(text):
    """扫描文本里的外部地址引用；返回违例清单（空 = 零外链）。

    ``//`` 一并判红：协议相对地址一样会在打开报告时发起外部请求，
    "看起来不像链接"不是放行的理由。
    """
    violations = []
    for marker in EXTERNAL_MARKERS:
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            violations.append(
                {
                    "marker": marker,
                    "offset": index,
                    "excerpt": text[max(0, index - 40) : index + 40],
                }
            )
            start = index + len(marker)
    return violations
