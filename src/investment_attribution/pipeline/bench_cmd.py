"""``bench`` 子命令编排：内置基准套件 B1–B5 的总入口与失败码汇总。

**纪律**：``bench`` 只做编排——它自己不长第二套计算路径，也不自己判达标。
每个套件的判据都是该模块下已经就位的那一份 ``verify.py``：

- ``B1`` 导入层：``ingest/verify.py``（从 ``tests/test_import.py`` 抽出的同一份实现）
- ``B2`` 成本层：``ledger/verify.py``
- ``B3`` 公司行为：``actions/verify.py``
- ``B4`` 收益归因：``attribution/verify.py``
- ``B5`` 合并层：``merge/verify.py``

比对函数**只读两边产物**（不重算导入、不重算成本、不重算归因），所以
"注入缺陷必须报未达标"是可证伪的。``bench`` 在临时目录里按 CI 的同一套
流程自跑样例，跑完即清理，不留中间产物。

退出码：``0`` 全部请求套件达标；``1`` 有套件未达标；``2`` 用法错误
（未识别的套件名）。
"""

import json
import shutil
import tempfile
from pathlib import Path

from ..actions import build_actions, compare_b3
from ..attribution import build_attribution, compare_b4
from ..errors import IAError, UsageError
from ..ingest import import_files
from ..ingest.verify import compare as compare_b1
from ..ledger import build_holdings, compare_b2
from ..manifest import load_manifest
from ..merge import build_merge, compare_b5
from ..netguard import runtime_probe
from ..paths import resolve_data_dir

SUITE_ORDER = ("B1", "B2", "B3", "B4", "B5")
SUITE_TITLES = {
    "B1": "导入层：逐行列值 / 行级错误 / 文件指纹对齐导入真值",
    "B2": "成本层：双口径逐笔持仓与已实现对齐成本真值",
    "B3": "公司行为：逐事件类型 / 调整前后 / 待确认队列对齐真值",
    "B4": "收益归因：XIRR / TWR / 拆分 / 四维分解对齐真值",
    "B5": "合并层：合并前后总收益 / 划转台账 / 合并持仓对齐真值",
}
METHODS = ("fifo", "wavg")


def parse_only(value):
    """把 ``--only`` 解析成套件名单；未识别即用法错误（不静默忽略）。"""
    if value is None:
        return list(SUITE_ORDER)
    names = [
        part.strip().upper()
        for part in value.replace(" ", ",").split(",")
        if part.strip()
    ]
    if not names:
        raise UsageError("--only 不能是空串", code="BENCH_SUITE_EMPTY")
    unknown = sorted(set(names) - set(SUITE_ORDER))
    if unknown:
        raise UsageError(
            "--only 只认 {}（收到 {!r}）".format(" / ".join(SUITE_ORDER), unknown),
            code="BENCH_SUITE_UNKNOWN",
        )
    return [name for name in SUITE_ORDER if name in names]


def _load_truth(data_dir, name):
    path = Path(data_dir) / "truth" / name
    if not path.is_file():
        raise IAError("基准真值缺失：{}".format(path), code="BENCH_TRUTH_MISSING")
    return json.loads(path.read_text(encoding="utf-8"))


def _import_samples(data_dir, workdir):
    """逐份导入样例（B1 要逐文件结论），再合并导入场景四件套（B2–B5 用）。"""
    import_truth = _load_truth(data_dir, "import_truth.json")
    cost_truth = _load_truth(data_dir, "cost_truth.json")
    scenario = cost_truth["scenario"]["files"]
    samples = Path(data_dir) / "samples"

    outcomes = {}
    for entry in import_truth["files"]:
        name = entry["source_file"]
        try:
            result = import_files([str(samples / name)], data_dir)
        except IAError as exc:
            outcomes[name] = {"status": "file_error", "file_error_code": exc.code}
            continue
        fingerprint = result.fingerprints["files"][0]
        outcomes[name] = {
            "status": "ok",
            "rows": result.ledger["rows"],
            "errors": result.errors["errors"],
            "fingerprint": {
                "sha256": fingerprint["sha256"],
                "encoding": fingerprint["encoding"],
                "delimiter": fingerprint["delimiter"],
                "unknown_columns": fingerprint["unknown_columns"],
            },
        }

    combined = import_files(
        [str(samples / name) for name in scenario],
        data_dir,
        out_dir=str(workdir),
    )
    return outcomes, combined.paths["ledger"], cost_truth


def _merge_reports(suite, per_method):
    """把逐口径报告并成套件级结论（违例逐条保留，带口径标签）。"""
    violations = []
    checked = 0
    tolerances = {}
    for method in sorted(per_method):
        report = per_method[method].as_dict()
        checked += report.get("checked") or 0
        tolerances = tolerances or (report.get("tolerances") or {})
        for item in report.get("violations") or []:
            entry = dict(item)
            entry["method"] = method
            violations.append(entry)
    return {
        "suite": suite,
        "title": SUITE_TITLES[suite],
        "ok": not violations,
        "checked": checked,
        "violation_count": len(violations),
        "tolerances": tolerances,
        "violations": violations,
        "methods": {method: per_method[method].as_dict() for method in sorted(per_method)},
    }


def run_bench(data_dir=None, only=None, workdir=None):
    """运行内置基准套件；返回可机读汇总（含 ``exit_code``）。"""
    resolved = resolve_data_dir(data_dir)
    requested = parse_only(only)
    needs_engine = bool(set(requested) & {"B2", "B3", "B4", "B5"})
    temporary = workdir is None
    target = Path(workdir) if workdir is not None else Path(
        tempfile.mkdtemp(prefix="ia-bench-")
    )
    if not target.is_dir():
        target.mkdir(parents=True, exist_ok=True)

    try:
        outcomes, ledger_path, cost_truth = _import_samples(resolved, target)
        attribution_truth = _load_truth(resolved, "attribution_truth.json")
        actions_truth = _load_truth(resolved, "actions_truth.json")
        merge_truth = _load_truth(resolved, "merge_truth.json")
        valuation = str(
            Path(resolved) / "samples" / attribution_truth["scenario"]["valuation"]
        )

        suites = {}
        if "B1" in requested:
            suites["B1"] = compare_b1(
                outcomes, _load_truth(resolved, "import_truth.json"), resolved
            ).as_dict()

        holdings = {}
        actions = {}
        artifacts = {}
        if needs_engine:
            for method in METHODS:
                if "B2" in requested:
                    holdings[method] = build_holdings(
                        ledger_path, method, resolved
                    ).holdings
                if "B3" in requested:
                    actions[method] = build_actions(
                        ledger_path, method, resolved
                    ).actions
                if set(requested) & {"B4", "B5"}:
                    artifacts[method] = build_attribution(
                        ledger_path,
                        method,
                        resolved,
                        valuation_path=valuation,
                        sections=("xirr", "twr"),
                    ).artifact

        if "B2" in requested:
            suites["B2"] = compare_b2(holdings, cost_truth, resolved).as_dict()
        if "B3" in requested:
            per_method = {
                method: compare_b3(actions[method], actions_truth, resolved, method)
                for method in METHODS
            }
            suites["B3"] = _merge_reports("B3", per_method)
        if "B4" in requested:
            per_method = {
                method: compare_b4(artifacts[method], attribution_truth, resolved, method)
                for method in METHODS
            }
            suites["B4"] = _merge_reports("B4", per_method)
        if "B5" in requested:
            per_method = {}
            for method in METHODS:
                merged = build_merge(
                    ledger_path,
                    method,
                    resolved,
                    attribution_payload=artifacts[method],
                ).artifact
                per_method[method] = compare_b5(merged, merge_truth, resolved, method)
            suites["B5"] = _merge_reports("B5", per_method)

        failed = [name for name in SUITE_ORDER if name in suites and not suites[name]["ok"]]
        probe = runtime_probe()
        payload = {
            "format": "ia.bench",
            "format_version": 1,
            "contract_version": load_manifest(resolved)["contract_version"],
            "scenario": {
                "files": cost_truth["scenario"]["files"],
                "row_count": cost_truth["scenario"]["row_count"],
                "methods": list(METHODS),
                "valuation": attribution_truth["scenario"]["valuation"],
            },
            "requested": list(requested),
            "suites": suites,
            "ok": not failed,
            "failed_suites": failed,
            "zero_upload": {
                "runtime_probe": {
                    "installed": probe.installed,
                    "armed": probe.armed,
                    "blocked_attempts": len(probe.attempts),
                }
            },
        }
        return payload
    finally:
        if temporary:
            shutil.rmtree(str(target), ignore_errors=True)


def describe_bench(payload):
    """给 CLI 用的中文摘要行（只陈述结果，不做业务判断）。"""
    lines = [
        "基准套件完成：{} 个套件{}".format(
            len(payload["suites"]),
            "，未达标 {}".format("、".join(payload["failed_suites"]))
            if payload["failed_suites"]
            else "，全部达标",
        )
    ]
    for name in SUITE_ORDER:
        suite = payload["suites"].get(name)
        if suite is None:
            continue
        lines.append(
            "  {} {}：{} 项比对 / {} 条违例 / {}".format(
                name,
                suite.get("title") or SUITE_TITLES[name],
                suite["checked"],
                suite["violation_count"],
                "达标" if suite["ok"] else "**未达标**",
            )
        )
        for item in suite["violations"][:5]:
            lines.append(
                "      违例 {} {}（期望 {} / 实际 {}）".format(
                    item.get("check"),
                    item.get("key"),
                    item.get("expected"),
                    item.get("actual"),
                )
            )
    return lines
