"""未就绪登记表与 CLI 分派的双向一致性。

防两类漂移：登记了但 CLI 已经实现（幽灵拦截），或 CLI 有命令但登记表
与实现都没有（静默空壳）。
"""

from context import SRC_ROOT

from investment_attribution import stages
from investment_attribution.cli import HANDLERS, SUBCOMMANDS


def test_every_registered_command_is_a_known_subcommand():
    assert set(stages.STAGE_NOT_READY) <= set(SUBCOMMANDS)


def test_no_command_is_both_implemented_and_blocked():
    assert not (set(stages.STAGE_NOT_READY) & set(HANDLERS))


def test_every_subcommand_is_either_ready_or_registered():
    covered = set(HANDLERS) | set(stages.STAGE_NOT_READY)
    assert set(SUBCOMMANDS) == covered, set(SUBCOMMANDS) ^ covered


def test_selftest_is_never_stage_blocked():
    assert "selftest" not in stages.STAGE_NOT_READY


def test_import_is_never_stage_blocked_again():
    """防复活断言：import 已于 M1 落地，不得重新登记为未就绪。"""
    assert "import" not in stages.STAGE_NOT_READY
    assert "import" in HANDLERS


def test_cost_and_pnl_are_never_stage_blocked_again():
    """防复活断言：cost / pnl 已于 M2 落地。"""
    for command in ("cost", "pnl"):
        assert command not in stages.STAGE_NOT_READY
        assert command in HANDLERS


def test_corporate_actions_is_never_stage_blocked_again():
    """防复活断言：corporate-actions 已于 M3 落地。"""
    assert "corporate-actions" not in stages.STAGE_NOT_READY
    assert "corporate-actions" in HANDLERS


def test_attribution_is_never_stage_blocked_again():
    """防复活断言：attribution 已于 M4 落地。"""
    assert "attribution" not in stages.STAGE_NOT_READY
    assert "attribution" in HANDLERS


def test_require_ready_returns_none_for_attribution():
    assert stages.require_ready("attribution") is None


def test_merge_and_report_are_still_stage_blocked():
    """M5 的两条命令必须仍被显式拦截（不能提前放行）。"""
    for command in ("merge", "report", "run", "bench"):
        assert command in stages.STAGE_NOT_READY, command
        assert command not in HANDLERS, command


def test_require_ready_returns_none_for_corporate_actions():
    assert stages.require_ready("corporate-actions") is None


def test_require_ready_returns_none_for_selftest():
    assert stages.require_ready("selftest") is None


def test_require_ready_returns_none_for_import():
    assert stages.require_ready("import") is None
