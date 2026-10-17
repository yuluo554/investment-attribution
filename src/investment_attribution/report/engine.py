"""``report`` 编排（M5）：合并产物 → 单文件 HTML + 机读 ``report_data.json``。

退出码语义（K1）：

- ``0``：报告生成完成，上游产物无问题；
- ``1``：报告生成完成，但上游产物带 ``invalid``（问题如实呈现在报告里，
  退出码同时告知脚本调用方——渲染成功不等于账本没问题）；
- ``2``：用法或输入不可用（未指定 ``--merged``、文件缺失、形态不符）。

三条纪律：

1. **同源**：数据模型只构建一次，HTML 与 JSON 都从这同一个对象产出；
2. **零外链**：出口前对 HTML 做静态扫描，命中外部引用即报错（不产出半份报告）；
3. **零网络**：构建与渲染全程处于运行期武装探针下，拦截次数写进两份产物。
"""

import hashlib
import json
import os
from pathlib import Path

from ..errors import DegradedError, InputError, UsageError
from ..serialize import write_atomic, write_json_atomic
from .guard import scan_external_references
from .model import REPORT_DATA_FILE, REPORT_HTML_FILE, build_report_data
from .render import render_html


class ReportResult(object):
    def __init__(self, data, html, paths, degraded):
        self.data = data
        self.html = html
        self.paths = paths
        self.degraded = degraded

    @property
    def exit_code(self):
        from ..exit_codes import EXIT_DEGRADED, EXIT_OK

        return EXIT_DEGRADED if self.degraded else EXIT_OK


def read_merged_file(path):
    """读取合并产物并校验顶层形态；返回 ``(payload, sha256)``。"""
    target = Path(path)
    if not target.is_file():
        raise InputError(
            "合并产物不存在：{}（先运行 merge 生成）".format(target),
            code="MERGED_MISSING",
        )
    raw = target.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InputError(
            "合并产物不是合法 JSON：{}：{}".format(target, exc),
            code="MERGED_INVALID_JSON",
        )
    return payload, hashlib.sha256(raw).hexdigest()


def _ensure_dir(target):
    if Path(target).is_dir():
        raise UsageError(
            "输出路径是目录而不是文件：{}（目录请用 --out-dir）".format(target),
            code="OUT_TARGET_IS_DIRECTORY",
        )
    directory = Path(target).parent
    if str(directory) and not directory.is_dir():
        os.makedirs(str(directory), exist_ok=True)


def _attach_render_probe(data, probe):
    """把渲染期探针记账挂进模型（HTML 与 JSON 都来自这份最终模型）。"""
    counts = {
        "installed": probe.installed,
        "armed": True,
        "blocked_attempts": len(probe.attempts),
    }
    data.setdefault("zero_upload", {})["render_probe"] = counts
    for section in data.get("sections") or []:
        if section.get("id") != "zero_upload":
            continue
        section.setdefault("items", []).append(
            {
                "title": "报告渲染层",
                "body": (
                    "报告数据模型构建与 HTML 渲染全程处于武装探针下；"
                    "拦截到的网络调用次数：{}。".format(counts["blocked_attempts"])
                ),
                "tone": "ok" if not counts["blocked_attempts"] else "bad",
            }
        )
    return data


def _targets(out, json_out, out_dir):
    html_target = out
    json_target = json_out
    if out_dir:
        if html_target is None:
            html_target = str(Path(out_dir) / REPORT_HTML_FILE)
        if json_target is None:
            json_target = str(Path(out_dir) / REPORT_DATA_FILE)
    return html_target, json_target


def build_report(merged_path, out=None, json_out=None, out_dir=None):
    """生成报告：读合并产物 → 构建模型 → 渲染 → 出口扫描 → 落盘。"""
    if not merged_path:
        raise UsageError(
            "必须显式指定 --merged 合并产物路径（先运行 merge 生成）",
            code="MERGED_REQUIRED",
        )
    merged, merged_sha = read_merged_file(merged_path)
    if merged.get("format") != "ia.merge":
        raise InputError(
            "合并产物 format 不匹配：{!r}（报告只吃 merge 的产物）".format(
                merged.get("format")
            ),
            code="MERGED_FORMAT_MISMATCH",
        )

    html_target, json_target = _targets(out, json_out, out_dir)
    if out_dir and not Path(out_dir).is_dir():
        os.makedirs(str(out_dir), exist_ok=True)
    for target in (html_target, json_target):
        if target:
            _ensure_dir(target)

    from ..netguard import armed_runtime_probe

    with armed_runtime_probe() as probe:
        data = build_report_data(merged, merged_path, merged_sha)
        _attach_render_probe(data, probe)
        html = render_html(data)

    violations = scan_external_references(html)
    if violations:
        raise DegradedError(
            "报告出口扫描发现外部引用（{} 处，首个：{}）：单文件报告不得外链".format(
                len(violations), violations[0]["excerpt"]
            ),
            code="REPORT_EXTERNAL_REFERENCE",
        )

    paths = {}
    if json_target:
        write_json_atomic(json_target, data)
        paths["report_data"] = str(json_target)
    if html_target:
        write_atomic(html_target, html)
        paths["report_html"] = str(html_target)
    return ReportResult(data, html, paths, bool(merged.get("invalid")))


def describe_report(result):
    """给 CLI 用的中文摘要行（只陈述结果，不做业务判断）。"""
    data = result.data
    counts = data.get("counts") or {}
    probe = (data.get("zero_upload") or {}).get("render_probe") or {}
    lines = [
        "报告完成：{} 个段落 / {} 个汇总数 / {} 行表格；外链 0 处，运行期拦截 {} 次".format(
            counts.get("section_count"),
            counts.get("figure_count"),
            counts.get("table_row_count"),
            probe.get("blocked_attempts"),
        ),
        "  打码：{} 处账号在生成阶段已打码（{}）".format(
            (data.get("masking") or {}).get("masked_token_count"),
            (data.get("masking") or {}).get("rule"),
        ),
    ]
    for name, path in sorted(result.paths.items()):
        lines.append("  {}: {}".format(name, path))
    return lines
