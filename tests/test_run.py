"""M5 ``run``：一条命令跑完整链路，且与逐命令产物**逐字节一致**。

这条纪律的意义：``run`` 只是编排。一旦它长出第二套计算路径，两条通路就会
在某个角落悄悄分叉——而"分叉"在账本软件里是最贵的一类缺陷。

比对办法：先用逐命令把同一批产物写到目录 D，快照一份；再用 ``run`` 覆盖
写同一个 D（路径字符串完全相同，故产物里记录的输入路径也完全相同），
逐文件比字节。
"""

import json
import shutil

import pytest

from context import DATA_DIR, REPO_ROOT

from investment_attribution import cli
from investment_attribution.exit_codes import EXIT_DEGRADED, EXIT_INPUT, EXIT_OK
from investment_attribution.pipeline.actions_cmd import run_corporate_actions
from investment_attribution.pipeline.attribution_cmd import run_attribution
from investment_attribution.pipeline.cost_cmd import run_cost, run_pnl
from investment_attribution.pipeline.import_cmd import run_import
from investment_attribution.pipeline.merge_cmd import run_merge
from investment_attribution.pipeline.report_cmd import run_report
from investment_attribution.pipeline.run_cmd import ARTIFACT_NAMES, run_all

SAMPLES = DATA_DIR / "samples"
COST_TRUTH = json.loads((DATA_DIR / "truth" / "cost_truth.json").read_text(encoding="utf-8"))
ATTRIBUTION_TRUTH = json.loads(
    (DATA_DIR / "truth" / "attribution_truth.json").read_text(encoding="utf-8")
)
SCENARIO_FILES = [str(SAMPLES / name) for name in COST_TRUTH["scenario"]["files"]]
VALUATION = str(SAMPLES / ATTRIBUTION_TRUTH["scenario"]["valuation"])


def _per_command(out_dir):
    """逐命令把同一套产物写进 ``out_dir``（与 run 的步骤一一对应）。"""
    ledger = str(__import__("pathlib").Path(out_dir) / "ledger.json")
    run_import(SCENARIO_FILES, data_dir=DATA_DIR, out_dir=out_dir)
    run_cost("fifo", ledger_path=ledger, data_dir=DATA_DIR, out_dir=out_dir)
    run_pnl(
        "fifo", ledger_path=ledger, data_dir=DATA_DIR, out_dir=out_dir, include_fees=True
    )
    run_corporate_actions("fifo", ledger_path=ledger, data_dir=DATA_DIR, out_dir=out_dir)
    run_attribution(
        "fifo",
        ledger_path=ledger,
        data_dir=DATA_DIR,
        valuation_path=VALUATION,
        sections=("xirr", "twr"),
        out_dir=out_dir,
    )
    run_merge(
        "fifo",
        ledger_path=ledger,
        attribution_path=str(__import__("pathlib").Path(out_dir) / "attribution.json"),
        data_dir=DATA_DIR,
        out_dir=out_dir,
    )
    run_report(
        merged_path=str(__import__("pathlib").Path(out_dir) / "merged.json"),
        out_dir=out_dir,
    )


def test_run_matches_per_command_byte_for_byte(tmp_path):
    out_dir = str(tmp_path / "chain")
    _per_command(out_dir)
    snapshot = tmp_path / "snapshot"
    shutil.copytree(out_dir, str(snapshot))

    result = run_all(
        SCENARIO_FILES,
        method="fifo",
        data_dir=DATA_DIR,
        valuation_path=VALUATION,
        out_dir=out_dir,
    )

    for name in ARTIFACT_NAMES:
        fresh = (snapshot / name).read_bytes()
        from_run = (tmp_path / "chain" / name).read_bytes()
        assert fresh == from_run, name
    assert result.exit_code == EXIT_DEGRADED


def test_run_writes_every_artifact(tmp_path):
    out_dir = str(tmp_path / "chain")
    run_all(
        SCENARIO_FILES,
        method="fifo",
        data_dir=DATA_DIR,
        valuation_path=VALUATION,
        out_dir=out_dir,
    )
    for name in ARTIFACT_NAMES:
        assert (tmp_path / "chain" / name).is_file(), name


def test_run_exit_code_is_the_max_of_steps(tmp_path):
    result = run_all(
        SCENARIO_FILES,
        method="fifo",
        data_dir=DATA_DIR,
        valuation_path=VALUATION,
        out_dir=str(tmp_path / "chain"),
    )
    codes = {name: step.exit_code for name, step in result.steps}
    # 样例含一条无法识别的现金行 → corporate-actions 按设计返回 1
    assert codes["corporate-actions"] == EXIT_DEGRADED
    assert codes["import"] == EXIT_OK
    assert codes["attribution"] == EXIT_OK
    assert codes["merge"] == EXIT_OK
    assert codes["report"] == EXIT_OK
    assert result.exit_code == max(codes.values()) == EXIT_DEGRADED


def test_run_zero_upload_probes_are_clean(tmp_path):
    """整条链路的运行期拦截次数全为 0：不依赖任何网络能力。"""
    out_dir = str(tmp_path / "chain")
    run_all(
        SCENARIO_FILES,
        method="fifo",
        data_dir=DATA_DIR,
        valuation_path=VALUATION,
        out_dir=out_dir,
    )
    attribution = json.loads((tmp_path / "chain" / "attribution.json").read_text(encoding="utf-8"))
    merged = json.loads((tmp_path / "chain" / "merged.json").read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "chain" / "report_data.json").read_text(encoding="utf-8"))
    assert attribution["zero_upload"]["runtime_probe"]["blocked_attempts"] == 0
    assert merged["zero_upload"]["runtime_probe"]["blocked_attempts"] == 0
    assert report["zero_upload"]["render_probe"]["blocked_attempts"] == 0
    assert report["zero_upload"]["render_probe"]["armed"] is True


#: 产物里不含任何路径字段的：可以跨目录直接比字节。
PATH_FREE_ARTIFACTS = (
    "ledger.json",
    "import_errors.json",
    "fingerprints.json",
    "account_map.json",
    "report.html",
)
#: 其余产物把输入路径记在 ``inputs`` / ``meta`` 里，跨目录比对时先摘掉。
PATH_FIELDS = ("inputs", "meta")


def _strip_paths(payload):
    stripped = json.loads(json.dumps(payload))
    for field in PATH_FIELDS:
        stripped.pop(field, None)
    return stripped


def test_run_double_execution_is_deterministic(tmp_path):
    """跨目录双跑：路径无关的部分必须逐字节一致，含路径的部分结构一致。"""
    first = tmp_path / "a"
    second = tmp_path / "b"
    run_all(
        SCENARIO_FILES,
        method="fifo",
        data_dir=DATA_DIR,
        valuation_path=VALUATION,
        out_dir=str(first),
    )
    run_all(
        SCENARIO_FILES,
        method="fifo",
        data_dir=DATA_DIR,
        valuation_path=VALUATION,
        out_dir=str(second),
    )
    for name in PATH_FREE_ARTIFACTS:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
    for name in ARTIFACT_NAMES:
        if name in PATH_FREE_ARTIFACTS:
            continue
        left = _strip_paths(json.loads((first / name).read_text(encoding="utf-8")))
        right = _strip_paths(json.loads((second / name).read_text(encoding="utf-8")))
        assert left == right, name


def test_run_requires_arguments():
    from investment_attribution.errors import UsageError

    with pytest.raises(UsageError) as excinfo:
        run_all(["x.csv"], method=None, data_dir=DATA_DIR, valuation_path=VALUATION, out_dir="o")
    assert excinfo.value.code == "COST_METHOD_REQUIRED"

    with pytest.raises(UsageError) as excinfo:
        run_all(["x.csv"], method="fifo", data_dir=DATA_DIR, out_dir="o")
    assert excinfo.value.code == "VALUATION_REQUIRED"

    with pytest.raises(UsageError) as excinfo:
        run_all(["x.csv"], method="fifo", data_dir=DATA_DIR, valuation_path=VALUATION)
    assert excinfo.value.code == "OUT_DIR_REQUIRED"


def test_run_has_no_second_computation_path():
    """run 只编排：它必须调用与逐命令相同的那批函数，不自建算法。"""
    source = (
        REPO_ROOT / "src" / "investment_attribution" / "pipeline" / "run_cmd.py"
    ).read_text(encoding="utf-8")
    for token in (
        "run_import",
        "run_cost",
        "run_pnl",
        "run_corporate_actions",
        "run_attribution",
        "run_merge",
        "run_report",
    ):
        assert token in source, token
    # 不得 import 成本 / 归因 / 合并引擎（那就是第二套路径的入口）
    for forbidden in ("build_holdings", "build_attribution", "build_merge", "build_actions"):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# CLI 契约
# ---------------------------------------------------------------------------


def test_cli_run_full_chain(tmp_path, capsys):
    code = cli.main(
        ["run"]
        + SCENARIO_FILES
        + [
            "--method",
            "fifo",
            "--valuation",
            VALUATION,
            "--out-dir",
            str(tmp_path / "chain"),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_DEGRADED
    assert "完整链路完成" in captured.err
    for name in ARTIFACT_NAMES:
        assert (tmp_path / "chain" / name).is_file(), name


def test_cli_run_wavg_and_twr_basis(tmp_path, capsys):
    code = cli.main(
        ["run"]
        + SCENARIO_FILES
        + [
            "--method",
            "wavg",
            "--valuation",
            VALUATION,
            "--twr-basis",
            "daily",
            "--out-dir",
            str(tmp_path / "chain"),
        ]
    )
    assert code == EXIT_DEGRADED
    attribution = json.loads(
        (tmp_path / "chain" / "attribution.json").read_text(encoding="utf-8")
    )
    assert attribution["method"] == "wavg"
    assert attribution["twr"]["basis"] == "daily"
    merged = json.loads((tmp_path / "chain" / "merged.json").read_text(encoding="utf-8"))
    assert merged["method"] == "wavg"


def test_cli_run_unknown_twr_basis_is_usage_error(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            ["run"]
            + SCENARIO_FILES
            + ["--method", "fifo", "--valuation", VALUATION, "--twr-basis", "weekly",
               "--out-dir", str(tmp_path / "chain")]
        )
    assert excinfo.value.code == EXIT_INPUT
