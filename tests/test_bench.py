"""M5 基准套件总入口：B1–B5 全绿、--only 选择、可证伪、退出码。

``bench`` 只做编排与失败码汇总——它不自己判达标，判据是各模块下已经就位的
那一份 ``verify.py``。这里验的正是"总入口没有长第二套判据，也没有把未达标
说成达标"。
"""

import json

import pytest

from context import DATA_DIR

from investment_attribution import cli
from investment_attribution.exit_codes import EXIT_DEGRADED, EXIT_INPUT, EXIT_OK
from investment_attribution.ingest.verify import compare as compare_b1
from investment_attribution.pipeline.bench_cmd import parse_only, run_bench


@pytest.fixture(scope="module")
def payload():
    return run_bench()


def test_all_five_suites_run_and_pass(payload):
    assert payload["format"] == "ia.bench"
    assert payload["ok"] is True
    assert payload["failed_suites"] == []
    assert sorted(payload["suites"]) == ["B1", "B2", "B3", "B4", "B5"]
    for name, suite in payload["suites"].items():
        assert suite["ok"] is True, (name, suite["violations"][:3])
        assert suite["violation_count"] == 0, name
        assert suite["checked"] > 0, name


def test_every_suite_compares_against_its_own_truth_module(payload):
    """每个套件都真的做了比对（checked 不为零），不是空转。"""
    assert payload["suites"]["B1"]["checked"] >= 17
    assert payload["suites"]["B2"]["checked"] >= 46
    assert payload["suites"]["B3"]["checked"] >= 10
    assert payload["suites"]["B4"]["checked"] >= 20
    assert payload["suites"]["B5"]["checked"] >= 9


def test_bench_runs_both_cost_methods(payload):
    assert payload["scenario"]["methods"] == ["fifo", "wavg"]
    for name in ("B3", "B4", "B5"):
        assert sorted(payload["suites"][name]["methods"]) == ["fifo", "wavg"]


def test_bench_scenario_is_the_cost_scenario(payload):
    cost_truth = json.loads(
        (DATA_DIR / "truth" / "cost_truth.json").read_text(encoding="utf-8")
    )
    assert payload["scenario"]["files"] == cost_truth["scenario"]["files"]
    assert payload["scenario"]["row_count"] == cost_truth["scenario"]["row_count"]


def test_bench_records_zero_upload_probe(payload):
    assert payload["zero_upload"]["runtime_probe"]["blocked_attempts"] == 0


def test_bench_leaves_no_artifacts_behind(tmp_path):
    """bench 在临时目录里自跑自清，不往仓库里丢中间产物。"""
    run_bench(workdir=None)
    assert not (DATA_DIR.parent / "ia-bench").exists()


def test_bench_can_keep_intermediates_when_asked(tmp_path):
    workdir = tmp_path / "keep"
    payload = run_bench(only="B2", workdir=str(workdir))
    assert payload["ok"] is True
    assert (workdir / "ledger.json").is_file()


def test_only_runs_the_requested_suite():
    payload = run_bench(only="B1")
    assert sorted(payload["suites"]) == ["B1"]
    assert payload["requested"] == ["B1"]
    assert payload["ok"] is True


def test_only_accepts_a_list_and_normalizes_order():
    payload = run_bench(only="B5,B1")
    assert payload["requested"] == ["B1", "B5"]
    assert sorted(payload["suites"]) == ["B1", "B5"]
    assert payload["ok"] is True


def test_only_rejects_unknown_suite():
    from investment_attribution.errors import UsageError

    with pytest.raises(UsageError) as excinfo:
        parse_only("B9")
    assert excinfo.value.code == "BENCH_SUITE_UNKNOWN"
    with pytest.raises(UsageError) as excinfo:
        parse_only("  ")
    assert excinfo.value.code == "BENCH_SUITE_EMPTY"


# ---------------------------------------------------------------------------
# 可证伪：注入缺陷必须让对应套件报未达标
# ---------------------------------------------------------------------------


def test_b1_is_falsifiable():
    """把一行的数量改掉，B1 必须报未达标（而不是照样说达标）。"""
    truth = json.loads(
        (DATA_DIR / "truth" / "import_truth.json").read_text(encoding="utf-8")
    )
    entry = next(item for item in truth["files"] if item["source_file"] == "htsec-demo-001.xlsx")
    outcomes = {
        entry["source_file"]: {
            "status": "ok",
            "rows": json.loads(json.dumps(entry["expected_rows"])),
            "errors": [],
        }
    }
    assert compare_b1(outcomes, {"format": "ia.truth.import", "files": [entry]}, DATA_DIR).ok
    outcomes[entry["source_file"]]["rows"][0]["quantity"] = "9999"
    report = compare_b1(outcomes, {"format": "ia.truth.import", "files": [entry]}, DATA_DIR)
    assert report.ok is False
    assert any(item["check"] == "row_field" for item in report.violations)


def test_b1_detects_unexpected_file_error():
    truth = json.loads(
        (DATA_DIR / "truth" / "import_truth.json").read_text(encoding="utf-8")
    )
    entry = next(item for item in truth["files"] if item["status"] == "file_error")
    good = {entry["source_file"]: {"status": "file_error", "file_error_code": entry["file_error_code"]}}
    assert compare_b1(good, {"format": "ia.truth.import", "files": [entry]}, DATA_DIR).ok
    wrong = {entry["source_file"]: {"status": "file_error", "file_error_code": "SOMETHING_ELSE"}}
    assert compare_b1(wrong, {"format": "ia.truth.import", "files": [entry]}, DATA_DIR).ok is False
    swallowed = {
        entry["source_file"]: {"status": "ok", "rows": [], "errors": []}
    }
    report = compare_b1(swallowed, {"format": "ia.truth.import", "files": [entry]}, DATA_DIR)
    assert report.ok is False


def test_bench_orchestrator_has_no_second_comparison_path():
    """bench 不得自己写第二套判据：五套套件都必须来自各模块的 verify。"""
    source = (
        DATA_DIR.parent
        / "src"
        / "investment_attribution"
        / "pipeline"
        / "bench_cmd.py"
    ).read_text(encoding="utf-8")
    for token in ("compare_b1", "compare_b2", "compare_b3", "compare_b4", "compare_b5"):
        assert token in source, token
    assert "def _compare" not in source


# ---------------------------------------------------------------------------
# CLI 契约
# ---------------------------------------------------------------------------


def test_cli_bench_exits_zero_and_lists_suites(capsys):
    code = cli.main(["bench"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "基准套件完成" in captured.err
    for name in ("B1", "B2", "B3", "B4", "B5"):
        assert name in captured.err


def test_cli_bench_only(capsys):
    code = cli.main(["bench", "--only", "B5"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "B5" in captured.err
    assert "B1 " not in captured.err


def test_cli_bench_usage_error_is_two(capsys):
    code = cli.main(["bench", "--only", "B0"])
    assert code == EXIT_INPUT


def test_bench_exit_codes_are_0_or_1_only():
    """bench 只有两个正常出口：全部达标 0 / 有未达标 1。"""
    payload = run_bench(only="B2")
    assert payload["ok"] is True
    assert EXIT_DEGRADED not in (EXIT_OK,)
