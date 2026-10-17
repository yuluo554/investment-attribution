"""退出码契约与 CLI 失败路径（入口级，不只看函数是否抛异常）。

M5 之后退出码只剩 0 / 1 / 2：骨架期的 ``3 = 阶段未就绪`` 随四条命令落地
整体退役，本文件不再有"未就绪"用例，取而代之的是四条新命令的失败路径。
"""

import json

import pytest

from context import DATA_DIR, REPO_ROOT, SRC_ROOT

from investment_attribution import cli
from investment_attribution.exit_codes import (
    EXIT_DEGRADED,
    EXIT_INPUT,
    EXIT_OK,
)


def run_cli(argv, capsys=None):
    return cli.main(argv)


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == EXIT_OK


def test_no_command_is_usage_error(capsys):
    code = cli.main([])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "usage" in captured.err.lower()


def test_unknown_command_is_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["nope"])
    assert excinfo.value.code == EXIT_INPUT


def test_every_subcommand_is_parseable(capsys):
    """十条子命令都能被解析（不因缺参数就崩在 argparse 之外）。"""
    for command in cli.SUBCOMMANDS:
        argv = [command]
        if command in ("import", "run"):
            argv.append("x.csv")
        code = cli.main(argv)
        captured = capsys.readouterr()
        assert code != 3, (command, code)
        assert "STAGE_NOT_READY" not in captured.err, command
        if command == "selftest":
            assert code == EXIT_OK, command
        elif command == "bench":
            # bench 自带样例自跑：无参数就是把它跑完（0 达标 / 1 有套件未达标）
            assert code in (EXIT_OK, EXIT_DEGRADED), command
        else:
            assert code == EXIT_INPUT, (command, code)


def test_no_command_returns_stage_not_ready(capsys):
    """退出码 3 已完全消失：任何命令的任何失败路径都不再返回 3。"""
    for command in ("merge", "report", "bench", "run"):
        argv = [command]
        if command == "run":
            argv.append("x.csv")
        code = cli.main(argv)
        captured = capsys.readouterr()
        assert code != 3, (command, captured.err)
        assert "STAGE_NOT_READY" not in captured.err, command


def test_merge_requires_method_ledger_and_attribution(capsys):
    code = cli.main(["merge", "--ledger", "x.json", "--attribution", "y.json"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "COST_METHOD_REQUIRED" in captured.err

    code = cli.main(
        ["merge", "--method", "fifo", "--attribution", "y.json"]
    )
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "LEDGER_REQUIRED" in captured.err

    code = cli.main(["merge", "--method", "fifo", "--ledger", "x.json"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "ATTRIBUTION_REQUIRED" in captured.err


def test_report_requires_merged(capsys):
    code = cli.main(["report"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "MERGED_REQUIRED" in captured.err


def test_run_requires_method_valuation_and_out_dir(capsys):
    code = cli.main(["run", "x.csv", "--valuation", "v.csv", "--out-dir", "o"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "COST_METHOD_REQUIRED" in captured.err

    code = cli.main(["run", "x.csv", "--method", "fifo", "--out-dir", "o"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "VALUATION_REQUIRED" in captured.err

    code = cli.main(["run", "x.csv", "--method", "fifo", "--valuation", "v.csv"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "OUT_DIR_REQUIRED" in captured.err


def test_run_without_positional_input_is_argparse_usage_error(capsys):
    """``run`` 有位置参数：不带输入是 argparse 的 2，不是别的码。"""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["run", "--method", "fifo", "--valuation", "v.csv"])
    assert excinfo.value.code == EXIT_INPUT


def test_bench_rejects_unknown_suite(capsys):
    code = cli.main(["bench", "--only", "B9"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "BENCH_SUITE_UNKNOWN" in captured.err


def test_attribution_is_no_longer_stage_blocked(capsys):
    code = cli.main(["attribution", "--ledger", "does-not-exist.json"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "STAGE_NOT_READY" not in captured.err
    assert "COST_METHOD_REQUIRED" in captured.err


def test_corporate_actions_is_no_longer_stage_blocked(capsys):
    code = cli.main(["corporate-actions", "--ledger", "does-not-exist.json"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "STAGE_NOT_READY" not in captured.err
    assert "COST_METHOD_REQUIRED" in captured.err


def test_cost_and_pnl_are_no_longer_stage_blocked(capsys):
    for command in ("cost", "pnl"):
        code = cli.main([command, "--ledger", "does-not-exist.json"])
        captured = capsys.readouterr()
        assert code == EXIT_INPUT, command
        assert "STAGE_NOT_READY" not in captured.err
        assert "COST_METHOD_REQUIRED" in captured.err


def test_cost_without_method_is_usage_error(tmp_path, capsys):
    """K7：成本口径无默认值——不传 ``--method`` 必须 exit 2。"""
    code = cli.main(["cost", "--ledger", str(tmp_path / "ledger.json")])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "COST_METHOD_REQUIRED" in captured.err


def test_import_is_no_longer_stage_blocked(capsys):
    code = cli.main(["import", "does-not-exist.csv"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "STAGE_NOT_READY" not in captured.err
    assert "FILE_NOT_FOUND" in captured.err


def test_missing_data_dir_returns_two(tmp_path):
    code = cli.main(["--data-dir", str(tmp_path / "does-not-exist"), "selftest"])
    assert code == EXIT_INPUT


def test_selftest_on_empty_dir_fails_and_is_falsifiable(tmp_path):
    """自检必须可证伪：数据目录为空 → manifest 检查必须红，退出码非 0。"""
    code = cli.main(["--data-dir", str(tmp_path), "selftest", "--strict"])
    assert code == EXIT_INPUT


def test_selftest_on_real_data_passes(capsys):
    code = cli.main(["--data-dir", str(DATA_DIR), "selftest"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == EXIT_OK
    assert payload["ok"] is True
    assert payload["failed_count"] == 0
    assert payload["format"] == "ia.selftest"


def test_selftest_json_out_written(tmp_path, capsys):
    out = tmp_path / "selftest.json"
    code = cli.main(["--data-dir", str(DATA_DIR), "selftest", "--json-out", str(out)])
    assert code == EXIT_OK
    assert out.is_file()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True


def test_selftest_covers_renderer_gate(capsys):
    """自检里必须有渲染层零判定门禁这一条（离开开发机也能证）。"""
    code = cli.main(["--data-dir", str(DATA_DIR), "selftest"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    names = {check["name"] for check in payload["checks"]}
    assert "renderer_no_decisions" in names
    assert "report_zero_external_links" in names
    assert code == EXIT_OK


def test_exit_code_values_are_only_0_1_2():
    from investment_attribution import exit_codes

    assert exit_codes.ALL_EXIT_CODES == (EXIT_OK, EXIT_DEGRADED, EXIT_INPUT)
    assert set(exit_codes.ALL_EXIT_CODES) == {0, 1, 2}
