"""``report`` 子命令编排。

退出码语义（K1）：见 ``report/engine.py`` 的 ``build_report``。本模块只做
路径解析与目录准备——渲染本身在 ``report/`` 内，编排层不碰判定。
"""

import os
from pathlib import Path

from ..errors import UsageError
from ..report import build_report, describe_report  # noqa: F401
from ..report import REPORT_DATA_FILE, REPORT_HTML_FILE

DEFAULT_NAMES = {"html": REPORT_HTML_FILE, "json": REPORT_DATA_FILE}


def _ensure_dir(target):
    if Path(target).is_dir():
        raise UsageError(
            "输出路径是目录而不是文件：{}（目录请用 --out-dir）".format(target),
            code="OUT_TARGET_IS_DIRECTORY",
        )
    directory = Path(target).parent
    if str(directory) and not directory.is_dir():
        os.makedirs(str(directory), exist_ok=True)


def run_report(merged_path=None, out=None, json_out=None, out_dir=None):
    """生成单文件报告与机读数据；返回 ``ReportResult``（含退出码）。"""
    if not merged_path:
        raise UsageError(
            "必须显式指定 --merged 合并产物路径（先运行 merge 生成）",
            code="MERGED_REQUIRED",
        )
    html_target = out
    json_target = json_out
    if out_dir:
        if html_target is None:
            html_target = str(Path(out_dir) / DEFAULT_NAMES["html"])
        if json_target is None:
            json_target = str(Path(out_dir) / DEFAULT_NAMES["json"])
    if out_dir and not Path(out_dir).is_dir():
        os.makedirs(str(out_dir), exist_ok=True)
    for target in (html_target, json_target):
        if target:
            _ensure_dir(target)
    return build_report(merged_path, out=html_target, json_out=json_target)
