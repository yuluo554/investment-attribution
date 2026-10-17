"""可证伪的自检。

``selftest`` 的价值在于**离开开发机仍能证明自己完整**，所以每条检查都
必须对"必然失败的输入"返回失败：

- 数据目录指向空目录 → 契约清单缺失检查必须红；
- 契约清单被改坏 → 结构检查必须红；
- 源码里出现网络库 import → 零上传静态检查必须红。

``run_selftest`` 只返回结果，不抛异常——便于 CLI 与测试共用同一实现。
"""

import os

from . import __version__
from .manifest import load_manifest, manifest_path
from .netguard import scan_network_imports
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
