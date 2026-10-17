"""子命令分派的完备性。

骨架期这里管的是"未就绪登记表与 CLI 分派的双向一致"；M5 把四条命令
（``merge`` / ``report`` / ``run`` / ``bench``）全部落地、退出码 3 退役、
登记表与 ``StageNotReady`` 一并删除之后，剩下的是**更强**的一条：

    SUBCOMMANDS 里的每条命令都必须在 HANDLERS 里有实现，且没有多余分派。

"登记了但没实现"（幽灵拦截）与"有命令但没分派"（静默空壳）两类漂移都被
这一条断言挡住；不再需要一张会过期的未就绪表。
"""

from context import SRC_ROOT

from investment_attribution.cli import HANDLERS, SUBCOMMANDS


def test_every_subcommand_has_a_handler():
    missing = sorted(set(SUBCOMMANDS) - set(HANDLERS))
    assert missing == [], missing


def test_no_handler_without_a_subcommand():
    extra = sorted(set(HANDLERS) - set(SUBCOMMANDS))
    assert extra == [], extra


def test_selftest_is_dispatchable():
    assert "selftest" in HANDLERS


def test_milestone_commands_are_dispatchable():
    """M5 之后不再有"未落地"命令：四条命令都在分派表里。"""
    for command in ("merge", "report", "run", "bench"):
        assert command in HANDLERS, command


def test_stage_registry_module_is_gone():
    """退出码 3 退役后不得留下幽灵登记表：留着它就会骗人。"""
    assert not (SRC_ROOT / "investment_attribution" / "stages.py").is_file()


def test_no_stage_not_ready_reference_in_sources():
    """源码里不得再出现 ``STAGE_NOT_READY`` 或 ``StageNotReady``。"""
    offenders = []
    for path in sorted((SRC_ROOT / "investment_attribution").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "STAGE_NOT_READY" in text or "StageNotReady" in text:
            offenders.append(path.name)
    assert offenders == [], offenders
