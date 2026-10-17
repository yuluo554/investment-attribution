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


def test_require_ready_returns_none_for_selftest():
    assert stages.require_ready("selftest") is None
