"""可证伪的自检。

``selftest`` 的价值在于**离开开发机仍能证明自己完整**，所以每条检查都
必须对"必然失败的输入"返回失败：

- 数据目录指向空目录 → 契约清单缺失检查必须红；
- 契约清单被改坏 → 结构检查必须红；
- 源码里出现网络库 import → 零上传静态检查必须红。

``run_selftest`` 只返回结果，不抛异常——便于 CLI 与测试共用同一实现。
"""

import os
import sys

from . import __version__
from .manifest import load_manifest, manifest_path
from .netguard import (
    NetworkAccessBlocked,
    runtime_probe,
    scan_network_imports,
)
from .errors import DataError
from .paths import find_repo_root

PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))


def _check(name, func):
    try:
        detail = func()
    except DataError as exc:
        return {"name": name, "ok": False, "detail": "{}: {}".format(exc.code, exc)}
    except Exception as exc:  # 自检不得因单条检查崩掉整份报告
        return {"name": name, "ok": False, "detail": "{}: {}".format(type(exc).__name__, exc)}
    return {"name": name, "ok": True, "detail": detail}


def run_selftest(data_dir):
    """执行全部自检项，返回可机读结果（``ok`` 为全部通过）。"""
    checks = []

    manifest = {}

    def _manifest_loaded():
        payload = load_manifest(data_dir)
        manifest.update(payload)
        return "format={} version={}".format(payload["format"], payload["format_version"])

    checks.append(_check("manifest_load", _manifest_loaded))

    def _manifest_file_present():
        path = manifest_path(data_dir)
        if not path.is_file():
            raise DataError("契约清单文件不存在：{}".format(path), code="MANIFEST_MISSING")
        return "{} bytes".format(path.stat().st_size)

    checks.append(_check("manifest_file_present", _manifest_file_present))

    def _coverage_non_empty():
        surfaces = manifest.get("coverage", {}).get("surfaces", [])
        if not surfaces:
            raise DataError("coverage.surfaces 为空", code="COVERAGE_EMPTY")
        return "{} surfaces declared".format(len(surfaces))

    checks.append(_check("coverage_declared", _coverage_non_empty))

    def _thresholds_positive():
        thresholds = manifest.get("bench_thresholds", {})
        zero = sorted(k for k, v in thresholds.items() if v == 0)
        if zero:
            raise DataError(
                "以下门槛为 0，无法区分达标与未达标：{}".format(", ".join(zero)),
                code="THRESHOLD_ZERO",
            )
        return "{} thresholds".format(len(thresholds))

    checks.append(_check("thresholds_positive", _thresholds_positive))

    def _no_network_imports():
        violations = scan_network_imports(PACKAGE_ROOT)
        if violations:
            raise DataError(
                "源码出现网络栈 import：{}".format(violations),
                code="NETWORK_IMPORT_FOUND",
            )
        return "0 network imports in package sources"

    checks.append(_check("no_network_imports", _no_network_imports))

    def _no_runtime_network():
        """运行期探针（四层审计的第三层）：正/负对照都在同一条检查里。

        - 阳性对照：武装探针后触发 socket 审计事件，必须被拦截并记账；
        - 阴性对照：卸下武装后同一事件必须放行，且不得留下记账。

        缺任一侧这条检查就只是话术：只测拦截会漏掉"探针根本没装上"，
        只测放行会漏掉"探针装上了但不拦"。
        """
        probe = runtime_probe()
        probe.armed = True
        probe.reset()
        try:
            sys.audit("socket.getaddrinfo", "selftest.invalid", 80)
        except NetworkAccessBlocked:
            blocked = True
        else:
            blocked = False
        recorded = len(probe.attempts)
        probe.armed = False
        if not blocked:
            raise DataError(
                "运行期探针未拦截 socket 审计事件", code="PROBE_NOT_BLOCKING"
            )
        if recorded < 1:
            raise DataError(
                "运行期探针拦截了事件但未记账", code="PROBE_NOT_RECORDING"
            )
        probe.reset()
        try:
            sys.audit("socket.getaddrinfo", "selftest.invalid", 80)
        except NetworkAccessBlocked:
            raise DataError(
                "探针已解除武装却仍在拦截", code="PROBE_STILL_BLOCKING"
            )
        if probe.attempts:
            raise DataError(
                "探针已解除武装却仍在记账", code="PROBE_STILL_RECORDING"
            )
        return "blocked=1 recorded={} disarm_ok=True".format(recorded)

    checks.append(_check("runtime_socket_probe", _no_runtime_network))

    def _renderer_no_decisions():
        """渲染层零判定：报告模板里不得出现成本口径 / 事件类型 / 收益口径。

        这条检查与 ``tests/test_report.py`` 里的正/负对照同源：门禁本身
        在 ``report/guard.py``，这里只是把它拉进"离开开发机也能证"的清单。
        """
        from .report.guard import scan_renderer

        violations = scan_renderer(PACKAGE_ROOT)
        if violations:
            raise DataError(
                "渲染层出现业务判定：{}".format(violations[:3]),
                code="RENDERER_DECISION_FOUND",
            )
        return "0 business decisions in report renderer"

    checks.append(_check("renderer_no_decisions", _renderer_no_decisions))

    def _report_zero_external_links():
        """零外链出口扫描的正/负对照：干净模型零命中，注入外链必须被抓。"""
        from .report.guard import scan_external_references
        from .report.render import render_html

        model = {
            "meta": {"title": "自检报告", "subtitle": "零外链对照"},
            "header": {"badges": [], "notices": [], "disclaimer": "d"},
            "sections": [
                {
                    "id": "notes",
                    "kind": "notes",
                    "title": "t",
                    "note": "n",
                    "items": [{"title": "a", "body": "b", "tone": "info"}],
                },
                {
                    "id": "kpi",
                    "kind": "kpi",
                    "title": "t",
                    "note": "n",
                    "figures": [
                        {
                            "id": "f",
                            "label": "l",
                            "value_text": "1.00",
                            "unit": "元",
                            "tone": "flat",
                            "evidence": [],
                        }
                    ],
                },
                {
                    "id": "table",
                    "kind": "table",
                    "title": "t",
                    "note": "n",
                    "columns": [{"label": "c", "align": "left"}],
                    "rows": [{"cells": ["v"], "tone": "flat", "evidence": []}],
                },
            ],
            "counts": {
                "section_count": 3,
                "figure_count": 1,
                "table_row_count": 1,
                "note_item_count": 1,
            },
        }
        clean = render_html(model)
        found = scan_external_references(clean)
        if found:
            raise DataError(
                "渲染层产出含外部引用：{}".format(found[0]),
                code="REPORT_EXTERNAL_REFERENCE",
            )
        model["header"]["notices"] = ["见 http://example.invalid/x （注入用阳性对照，不是真链接）"]
        dirty = render_html(model)
        if not scan_external_references(dirty):
            raise DataError(
                "外链扫描没抓到注入的外部引用（门禁不可证伪）",
                code="REPORT_SCAN_NOT_FALSIFIABLE",
            )
        return "clean=0 dirty={}".format(len(scan_external_references(dirty)))

    checks.append(_check("report_zero_external_links", _report_zero_external_links))

    def _repo_root_resolvable():
        root = find_repo_root()
        if root is None:
            raise DataError("源码树探测失败", code="REPO_ROOT_NOT_FOUND")
        return str(root.name)

    checks.append(_check("repo_root", _repo_root_resolvable))

    failed = [check for check in checks if not check["ok"]]
    return {
        "format": "ia.selftest",
        "format_version": 1,
        "version": __version__,
        "data_dir_name": os.path.basename(os.path.abspath(data_dir)),
        "checks": checks,
        "ok": not failed,
        "failed_count": len(failed),
    }
