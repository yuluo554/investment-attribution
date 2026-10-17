"""退出码契约与 CLI 失败路径（入口级，不只看函数是否抛异常）。"""

import json
import os
import sys

import pytest

from context import DATA_DIR, REPO_ROOT, SRC_ROOT

from investment_attribution import cli
from investment_attribution.exit_codes import (
    EXIT_DEGRADED,
    EXIT_INPUT,
    EXIT_OK,
    EXIT_STAGE_NOT_READY,
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


def test_stage_not_ready_commands_return_three(capsys):
    for command in ("import", "cost", "pnl", "corporate-actions", "attribution", "merge", "report", "run", "bench"):
        argv = [command]
        if command in ("import", "run"):
            argv.append("x.csv")
        code = cli.main(argv)
        captured = capsys.readouterr()
        assert code == EXIT_STAGE_NOT_READY, command
        assert "STAGE_NOT_READY" in captured.err


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


def test_exit_code_values_are_distinct():
    values = {EXIT_OK, EXIT_DEGRADED, EXIT_INPUT, EXIT_STAGE_NOT_READY}
    assert values == {0, 1, 2, 3}
