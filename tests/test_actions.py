"""M3 公司行为：B3 逐类对齐、待确认 100% 落库、调整可逆、不回写原始流水。

真值来自 ``data/truth/actions_truth.json``（生成器独立实现，不 import 引擎）。
另一半是负向断言：规则表认不出 / 行形态不符 / 多规则命中都必须进待确认，
把引擎改错时 B3 必须变红。
"""

import json
import shutil

import pytest

from context import DATA_DIR, REPO_ROOT

from investment_attribution import cli
from investment_attribution.actions import (
    ACTIONS_FILE,
    PENDING_FILE,
    build_actions,
    compare_b3,
    load_confirmations,
    load_corporate_action_rules,
)
from investment_attribution.errors import DataError, IAError, InputError, UsageError
from investment_attribution.exit_codes import EXIT_DEGRADED, EXIT_INPUT, EXIT_OK
from investment_attribution.ingest import import_files
from investment_attribution.ledger import build_holdings
from investment_attribution.pipeline.actions_cmd import run_corporate_actions
from investment_attribution.serialize import dumps_json

SAMPLES = DATA_DIR / "samples"
TRUTH = json.loads(
    (DATA_DIR / "truth" / "actions_truth.json").read_text(encoding="utf-8")
)
SCENARIO_FILES = TRUTH["scenario"]["files"]
METHODS = ("fifo", "wavg")


@pytest.fixture(scope="module")
def ledger_path(tmp_path_factory):
    target = tmp_path_factory.mktemp("m3")
    result = import_files(
        [str(SAMPLES / name) for name in SCENARIO_FILES],
        DATA_DIR,
        out_dir=str(target),
    )
    return str(target / "ledger.json")


def actions_for(ledger_path, method):
    return build_actions(ledger_path, method, DATA_DIR)


def by_key(items):
    return {(item["source_file"], item["source_line_no"]): item for item in items}


# ---------------------------------------------------------------------------
# B3 逐类事件对齐
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_b3_matches_truth(ledger_path, method):
    result = actions_for(ledger_path, method)
    report = compare_b3(result.actions, TRUTH, DATA_DIR, method)
    assert report.ok, report.as_dict()
    assert report.checked > 0


@pytest.mark.parametrize("method", METHODS)
def test_every_truth_event_is_present_with_expected_type(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    engine = by_key(actions["events"])
    for expected in TRUTH["methods"][method]["events"]:
        key = tuple(expected["key"])
        actual = engine[key]
        assert actual["event_type"] == expected["event_type"], key
        assert actual["rule_id"] == expected["rule_id"], key
        assert actual["kind"] == expected["kind"], key
        assert actual["cash_effect"] == expected["cash_effect"], key
        assert actual["adjustment"]["before"] == expected["before"], key
        assert actual["adjustment"]["after"] == expected["after"], key


@pytest.mark.parametrize("method", METHODS)
def test_all_five_event_types_are_covered(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    counts = actions["summary"]["by_event_type"]
    for event_type in (
        "dividend",
        "stock_dividend",
        "rights_issue",
        "split",
        "reinvested_dividend",
    ):
        assert counts.get(event_type, 0) >= 1, event_type


@pytest.mark.parametrize("method", METHODS)
def test_adjusted_positions_match_truth(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    engine = {
        (item["account_key"], item["symbol"]): item for item in actions["adjusted_positions"]
    }
    for expected in TRUTH["methods"][method]["adjusted_positions"]:
        actual = engine[(expected["account_key"], expected["symbol"])]
        assert actual["quantity"] == expected["quantity"]
        assert actual["cost_fen"] == expected["cost_fen"]


# ---------------------------------------------------------------------------
# 五类事件的业务语义（不只是"数对了"，还要"数为什么对"）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_dividend_moves_cash_but_not_position(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    dividends = [
        event for event in actions["events"] if event["event_type"] == "dividend"
    ]
    assert dividends
    for event in dividends:
        assert event["adjustment"]["action"] == "cash_only"
        assert event["adjustment"]["quantity_delta"] == "0"
        assert event["adjustment"]["cost_delta_fen"] == 0
        assert event["cash_effect"] == "inflow"
        assert event["adjustment"]["before"] == event["adjustment"]["after"]


@pytest.mark.parametrize("method", METHODS)
def test_stock_dividend_and_split_are_zero_cost_share_increases(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    for event in actions["events"]:
        if event["event_type"] not in ("stock_dividend", "split"):
            continue
        before = event["adjustment"]["before"]
        after = event["adjustment"]["after"]
        assert event["adjustment"]["action"] == "free_shares"
        assert event["adjustment"]["cost_delta_fen"] == 0
        assert int(after["quantity"]) > int(before["quantity"])
        # 零成本份额：总成本守恒，单位成本被摊薄
        assert after["cost_fen"] == before["cost_fen"]
        assert float(after["unit_cost"]) < float(before["unit_cost"])


@pytest.mark.parametrize("method", METHODS)
def test_rights_issue_adds_cost_by_consideration(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    events = [event for event in actions["events"] if event["event_type"] == "rights_issue"]
    assert len(events) == 1
    event = events[0]
    assert event["adjustment"]["action"] == "paid_acquisition"
    assert event["cash_effect"] == "outflow"
    # 配股 300 股 @5.80，无费用 → 成本增量 174000 分
    assert event["adjustment"]["cost_delta_fen"] == 174000
    assert event["adjustment"]["quantity_delta"] == "300"
    before, after = event["adjustment"]["before"], event["adjustment"]["after"]
    assert after["cost_fen"] - before["cost_fen"] == 174000
    assert int(after["quantity"]) - int(before["quantity"]) == 300


@pytest.mark.parametrize("method", METHODS)
def test_reinvested_dividend_adds_cost_without_cash_outflow(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    events = [
        event
        for event in actions["events"]
        if event["event_type"] == "reinvested_dividend"
    ]
    assert len(events) == 1
    event = events[0]
    assert event["adjustment"]["action"] == "valued_acquisition"
    assert event["cash_effect"] == "none"
    assert event["settlement_fen"] == 0
    # 红利再投 100 股 @9.50 → 成本增量 95000 分，但现金流出为 0
    assert event["adjustment"]["cost_delta_fen"] == 95000


# ---------------------------------------------------------------------------
# 无法识别 → 100% 进待确认队列
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_unrecognized_row_lands_in_pending_queue(ledger_path, method):
    result = actions_for(ledger_path, method)
    pending = by_key(result.pending["items"])
    key = ("htsec-demo-001.xlsx", 17)
    assert key in pending, pending
    item = pending[key]
    assert item["business"] == "其他业务甲"
    assert item["reason"] == "NO_RULE_MATCH"
    assert item["kind"] == "cash_event"
    assert item["candidates"] == []
    # 原始行证据齐全：文件名 + 行号 + 账户
    assert item["evidence"]["source_file"] == "htsec-demo-001.xlsx"
    assert item["evidence"]["source_line_no"] == 17
    assert item["account_id"] == "Y0000001"


@pytest.mark.parametrize("method", METHODS)
def test_pending_queue_equals_truth(ledger_path, method):
    result = actions_for(ledger_path, method)
    engine = set(by_key(result.pending["items"]))
    expected = {
        (item["source_file"], item["source_line_no"])
        for item in TRUTH["methods"][method]["pending"]
    }
    assert engine == expected


@pytest.mark.parametrize("method", METHODS)
def test_other_rows_are_fully_bucketed(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    summary = actions["summary"]
    assert summary["buckets_balanced"] is True
    assert (
        summary["action_events"] + summary["pending"] + summary["non_action_rows"]
        == summary["other_rows"]
    )
    assert summary["action_events"] == len(actions["events"])
    assert summary["pending"] == len(actions["pending"])
    assert summary["non_action_rows"] == len(actions["non_actions"])


@pytest.mark.parametrize("method", METHODS)
def test_internal_transfer_is_non_action_not_income(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    transfers = [
        item for item in actions["non_actions"] if item["matched_pattern"] == "内部划转"
    ]
    assert len(transfers) == 2
    for item in transfers:
        assert item["settlement_fen"] != 0
        assert item["kind"] == "cash_event"
    # 划转不得出现在事件台账里（不是公司行为，也不是收入）
    assert not any(
        event["business"] == "内部划转" for event in actions["events"]
    )


@pytest.mark.parametrize("method", METHODS)
def test_pending_rows_are_visible_in_summary(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    assert actions["summary"]["pending"] == 1
    assert actions["pending"][0]["business"] == "其他业务甲"


# ---------------------------------------------------------------------------
# 调整可逆：before + 声明增量 == after
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_every_adjustment_is_reversible(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    assert actions["summary"]["reversibility"]["failed"] == 0
    assert actions["summary"]["reversibility"]["checked"] == len(actions["events"])
    for event in actions["events"]:
        adjustment = event["adjustment"]
        assert adjustment["reversible"] is True, event["source_line_no"]
        before, after = adjustment["before"], adjustment["after"]
        assert (
            int(before["quantity"]) + int(adjustment["quantity_delta"])
            == int(after["quantity"])
        ), event["source_line_no"]
        assert (
            before["cost_fen"] + adjustment["cost_delta_fen"] == after["cost_fen"]
        ), event["source_line_no"]


@pytest.mark.parametrize("method", METHODS)
def test_split_conserves_total_cost(ledger_path, method):
    """拆分后总成本不变（split_conservation 门槛的引擎侧断言）。"""
    actions = actions_for(ledger_path, method).actions
    split = next(event for event in actions["events"] if event["event_type"] == "split")
    assert split["adjustment"]["cost_delta_fen"] == 0
    assert split["adjustment"]["before"]["cost_fen"] == split["adjustment"]["after"]["cost_fen"]


# ---------------------------------------------------------------------------
# 不回写原始流水
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_actions_do_not_touch_ledger_bytes(ledger_path, method):
    from pathlib import Path

    before = Path(ledger_path).read_bytes()
    build_actions(ledger_path, method, DATA_DIR)
    assert Path(ledger_path).read_bytes() == before


def test_actions_are_byte_deterministic(ledger_path):
    first = actions_for(ledger_path, "fifo")
    second = actions_for(ledger_path, "fifo")
    assert dumps_json(first.actions) == dumps_json(second.actions)
    assert dumps_json(first.pending) == dumps_json(second.pending)


# ---------------------------------------------------------------------------
# 依据行：rule_id + source_file + source_line_no
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_every_event_carries_rule_and_source_evidence(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    for event in actions["events"]:
        assert event["rule_id"]
        assert event["evidence"]["rule_id"] == event["rule_id"]
        assert event["evidence"]["source_file"] == event["source_file"]
        assert event["evidence"]["source_line_no"] == event["source_line_no"]
        assert event["evidence"]["evidence_hint"]
        assert event["matched_pattern"]
        # 命中模式必须真的是业务名的子串
        assert event["matched_pattern"] in event["business"]


@pytest.mark.parametrize("method", METHODS)
def test_header_declares_method_and_rule_pack(ledger_path, method):
    actions = actions_for(ledger_path, method).actions
    assert actions["method"] == method
    assert actions["method_label"]
    assert "口径" in actions["method_notice"]
    pack = actions["rule_pack"]
    assert pack["path"] == "rules/corporate_actions.json"
    assert pack["rule_count"] == 5
    assert len(pack["sha256"]) == 64
    assert pack["unmatched_policy"] == "pending_confirmation"
    assert set(pack["rule_ids"]) == {
        "ca.dividend",
        "ca.stock_dividend",
        "ca.rights_issue",
        "ca.split",
        "ca.reinvested_dividend",
    }


# ---------------------------------------------------------------------------
# 规则包：数据驱动，不写第二份业务名表
# ---------------------------------------------------------------------------


def test_rules_declare_kind_and_cash_expectations():
    rules = load_corporate_action_rules(DATA_DIR)
    for rule in rules.rules:
        assert rule["expected_kinds"]
        assert rule["cash_effect"] in ("inflow", "outflow", "none")
        assert rule["requires_manual_confirm"] is False
    assert rules.non_action_pattern("银证转入") == "银证转入"
    assert rules.non_action_pattern("其他业务甲") is None


def test_rule_matching_uses_patterns_from_pack():
    rules = load_corporate_action_rules(DATA_DIR)
    assert [match.rule_id for match in rules.match("红利入账")] == ["ca.dividend"]
    assert [match.rule_id for match in rules.match("红利再投")] == [
        "ca.reinvested_dividend"
    ]
    assert rules.match("其他业务甲") == []


def test_missing_rule_pack_is_data_error(tmp_path):
    with pytest.raises(DataError) as excinfo:
        load_corporate_action_rules(str(tmp_path))
    assert excinfo.value.code == "CA_RULES_MISSING"


def test_rule_pack_without_expected_kinds_is_data_error(tmp_path):
    source = json.loads(
        (DATA_DIR / "rules" / "corporate_actions.json").read_text(encoding="utf-8")
    )
    source["rules"][0].pop("expected_kinds")
    target = tmp_path / "rules"
    target.mkdir()
    (target / "corporate_actions.json").write_text(
        dumps_json(source), encoding="utf-8"
    )
    with pytest.raises(DataError) as excinfo:
        load_corporate_action_rules(str(tmp_path))
    assert excinfo.value.code == "CA_RULE_MALFORMED"


def test_rule_pack_with_duplicate_pattern_is_data_error(tmp_path):
    source = json.loads(
        (DATA_DIR / "rules" / "corporate_actions.json").read_text(encoding="utf-8")
    )
    source["rules"][1]["business_name_patterns"].append("红利入账")
    target = tmp_path / "rules"
    target.mkdir()
    (target / "corporate_actions.json").write_text(
        dumps_json(source), encoding="utf-8"
    )
    with pytest.raises(DataError) as excinfo:
        load_corporate_action_rules(str(tmp_path))
    assert excinfo.value.code == "CA_PATTERN_DUPLICATE"


def test_rule_pack_with_ambiguous_pattern_lands_in_pending(ledger_path, tmp_path):
    """同一业务名命中两条规则 → 歧义 → 待确认，附全部候选，不猜。"""
    source = json.loads(
        (DATA_DIR / "rules" / "corporate_actions.json").read_text(encoding="utf-8")
    )
    # "红利" 只挂到分红规则，但它也是 "红利再投" 的子串：
    # 子串匹配下 2025-06-10 的红利再投行会同时命中两条规则。
    source["rules"][0]["business_name_patterns"].append("红利")
    data_dir = tmp_path / "data"
    shutil.copytree(str(DATA_DIR), str(data_dir))
    (data_dir / "rules" / "corporate_actions.json").write_text(
        dumps_json(source), encoding="utf-8"
    )

    result = build_actions(ledger_path, "fifo", str(data_dir))
    item = by_key(result.pending["items"])[("htsec-demo-001.xlsx", 13)]
    assert item["reason"] == "AMBIGUOUS_RULES"
    assert {candidate["rule_id"] for candidate in item["candidates"]} == {
        "ca.dividend",
        "ca.reinvested_dividend",
    }
    assert result.exit_code == EXIT_DEGRADED


def test_rule_pack_with_kind_mismatch_lands_in_pending(ledger_path, tmp_path):
    """业务名命中送股规则，但该行是 cash_event → 行形态不符 → 待确认。"""
    source = json.loads(
        (DATA_DIR / "rules" / "corporate_actions.json").read_text(encoding="utf-8")
    )
    source["rules"][1]["business_name_patterns"].append("其他业务甲")
    data_dir = tmp_path / "data"
    shutil.copytree(str(DATA_DIR), str(data_dir))
    (data_dir / "rules" / "corporate_actions.json").write_text(
        dumps_json(source), encoding="utf-8"
    )
    result = build_actions(ledger_path, "fifo", str(data_dir))
    item = by_key(result.pending["items"])[("htsec-demo-001.xlsx", 17)]
    assert item["reason"] == "KIND_NOT_EXPECTED"
    assert item["candidates"][0]["rule_id"] == "ca.stock_dividend"


def test_cash_effect_mismatch_lands_in_pending(tmp_path):
    """行形态对得上、现金方向对不上 → 待确认（不按半条规则入账）。"""
    data_dir = tmp_path / "data"
    shutil.copytree(str(DATA_DIR), str(data_dir))
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        dumps_json(
            {
                "format": "ia.ledger",
                "format_version": 1,
                "rows": [
                    {
                        "source_file": "crafted.csv",
                        "source_sha256": "a" * 64,
                        "source_line_no": 2,
                        "account_id": "Y0000001",
                        "account_alias": "示例账户一",
                        "account_status": "registered",
                        "symbol": "699001",
                        "name": "示例科技甲",
                        "business": "红利入账",
                        "trade_date": "2025-05-20",
                        "trade_time": "09:30:00",
                        "quantity": "0",
                        "price": None,
                        "amount": 0,
                        "settlement_amount": -500,
                        "commission": 0,
                        "stamp_tax": 0,
                        "transfer_fee": 0,
                        "other_fee": 0,
                        "direction": "other",
                        "extra": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = build_actions(str(ledger), "fifo", str(data_dir))
    assert result.pending["items"][0]["reason"] == "CASH_EFFECT_MISMATCH"
    assert result.actions["events"] == []


# ---------------------------------------------------------------------------
# 人工确认记录
# ---------------------------------------------------------------------------


def _confirmation_payload(rule_id="ca.dividend", line_no=17):
    return {
        "format": "ia.confirmations",
        "format_version": 1,
        "confirmations": [
            {
                "source_file": "htsec-demo-001.xlsx",
                "source_line_no": line_no,
                "rule_id": rule_id,
                "decided_by": "示例用户",
                "decided_at": "2026-10-17T10:00:00",
                "basis": "券商 App 显示为现金红利，按分红入账",
            }
        ],
    }


def test_confirmation_record_moves_pending_row_into_events(ledger_path, tmp_path):
    confirm = tmp_path / "confirm.json"
    confirm.write_text(dumps_json(_confirmation_payload()), encoding="utf-8")
    result = build_actions(
        ledger_path, "fifo", DATA_DIR, confirm_path=str(confirm)
    )
    actions = result.actions
    assert actions["summary"]["pending"] == 0
    assert actions["summary"]["confirmed_by_record"] == 1
    event = by_key(actions["events"])[("htsec-demo-001.xlsx", 17)]
    assert event["rule_id"] == "ca.dividend"
    assert event["confirmation"]["decided_by"] == "示例用户"
    assert event["confirmation"]["basis"]
    assert actions["inputs"]["confirmations"]["count"] == 1
    assert result.exit_code == EXIT_OK


def test_confirmation_can_contradict_rule_table(ledger_path, tmp_path):
    """规则表没命中时，人工确认是唯一入口；确认后必须挂确认依据。"""
    confirm = tmp_path / "confirm.json"
    confirm.write_text(
        dumps_json(_confirmation_payload(rule_id="ca.dividend")), encoding="utf-8"
    )
    result = build_actions(ledger_path, "fifo", DATA_DIR, confirm_path=str(confirm))
    assert result.pending["items"] == []


def test_confirmation_resolves_ambiguous_match(ledger_path, tmp_path):
    """规则表命中多条 → 待确认；人工确认指定其中一条即可入账。"""
    source = json.loads(
        (DATA_DIR / "rules" / "corporate_actions.json").read_text(encoding="utf-8")
    )
    source["rules"][0]["business_name_patterns"].append("红利")
    data_dir = tmp_path / "data"
    shutil.copytree(str(DATA_DIR), str(data_dir))
    (data_dir / "rules" / "corporate_actions.json").write_text(
        dumps_json(source), encoding="utf-8"
    )
    payload = {
        "format": "ia.confirmations",
        "format_version": 1,
        "confirmations": [
            {
                "source_file": "htsec-demo-001.xlsx",
                "source_line_no": 13,
                "rule_id": "ca.reinvested_dividend",
                "decided_by": "示例用户",
                "decided_at": "2026-10-17T10:00:00",
                "basis": "券商 App 显示红利再投",
            }
        ],
    }
    confirm = tmp_path / "confirm.json"
    confirm.write_text(dumps_json(payload), encoding="utf-8")
    result = build_actions(ledger_path, "fifo", str(data_dir), confirm_path=str(confirm))
    assert ("htsec-demo-001.xlsx", 13) not in by_key(result.pending["items"])
    event = by_key(result.actions["events"])[("htsec-demo-001.xlsx", 13)]
    assert event["rule_id"] == "ca.reinvested_dividend"
    assert event["matched_pattern"] == "红利再投"
    assert event["confirmation"]["basis"]


def test_confirmation_resolves_rule_requiring_manual_confirm(ledger_path, tmp_path):
    """规则标了 requires_manual_confirm → 默认进待确认；确认后才入账。"""
    source = json.loads(
        (DATA_DIR / "rules" / "corporate_actions.json").read_text(encoding="utf-8")
    )
    for rule in source["rules"]:
        if rule["rule_id"] == "ca.split":
            rule["requires_manual_confirm"] = True
    data_dir = tmp_path / "data"
    shutil.copytree(str(DATA_DIR), str(data_dir))
    (data_dir / "rules" / "corporate_actions.json").write_text(
        dumps_json(source), encoding="utf-8"
    )
    result = build_actions(ledger_path, "fifo", str(data_dir))
    item = by_key(result.pending["items"])[("htsec-demo-001.xlsx", 15)]
    assert item["reason"] == "RULE_REQUIRES_CONFIRM"

    payload = {
        "format": "ia.confirmations",
        "format_version": 1,
        "confirmations": [
            {
                "source_file": "htsec-demo-001.xlsx",
                "source_line_no": 15,
                "rule_id": "ca.split",
                "decided_by": "示例用户",
                "decided_at": "2026-10-17T10:00:00",
                "basis": "对账单标注份额折算",
            }
        ],
    }
    confirm = tmp_path / "confirm.json"
    confirm.write_text(dumps_json(payload), encoding="utf-8")
    confirmed = build_actions(
        ledger_path, "fifo", str(data_dir), confirm_path=str(confirm)
    )
    event = by_key(confirmed.actions["events"])[("htsec-demo-001.xlsx", 15)]
    assert event["rule_id"] == "ca.split"
    assert event["confirmation"]["decided_by"] == "示例用户"


def test_confirmation_with_mismatched_kind_is_rejected(ledger_path, tmp_path):
    """确认记录选送股规则，但该行是 cash_event（无数量）→ 输入不可用。"""
    confirm = tmp_path / "confirm.json"
    confirm.write_text(
        dumps_json(_confirmation_payload(rule_id="ca.stock_dividend")), encoding="utf-8"
    )
    with pytest.raises(InputError) as excinfo:
        build_actions(ledger_path, "fifo", DATA_DIR, confirm_path=str(confirm))
    assert excinfo.value.code == "CA_CONFIRMATION_INVALID"


def test_confirmation_for_unknown_row_is_rejected(ledger_path, tmp_path):
    confirm = tmp_path / "confirm.json"
    confirm.write_text(
        dumps_json(_confirmation_payload(line_no=9999)), encoding="utf-8"
    )
    with pytest.raises(InputError) as excinfo:
        build_actions(ledger_path, "fifo", DATA_DIR, confirm_path=str(confirm))
    assert excinfo.value.code == "CA_CONFIRMATION_UNKNOWN_ROW"


def test_confirmation_for_already_recognized_row_is_rejected(ledger_path, tmp_path):
    confirm = tmp_path / "confirm.json"
    confirm.write_text(
        dumps_json(_confirmation_payload(line_no=11)), encoding="utf-8"
    )
    with pytest.raises(InputError) as excinfo:
        build_actions(ledger_path, "fifo", DATA_DIR, confirm_path=str(confirm))
    assert excinfo.value.code == "CA_CONFIRMATION_NOT_PENDING"


def test_confirmation_missing_audit_fields_is_rejected(tmp_path):
    rules = load_corporate_action_rules(DATA_DIR)
    payload = _confirmation_payload()
    payload["confirmations"][0].pop("decided_by")
    target = tmp_path / "confirm.json"
    target.write_text(dumps_json(payload), encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        load_confirmations(str(target), rules)
    assert excinfo.value.code == "CA_CONFIRMATION_INCOMPLETE"


def test_confirmation_with_unknown_rule_is_rejected(tmp_path):
    rules = load_corporate_action_rules(DATA_DIR)
    payload = _confirmation_payload(rule_id="ca.nope")
    target = tmp_path / "confirm.json"
    target.write_text(dumps_json(payload), encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        load_confirmations(str(target), rules)
    assert excinfo.value.code == "CA_CONFIRMATION_UNKNOWN_RULE"


# ---------------------------------------------------------------------------
# CLI 契约
# ---------------------------------------------------------------------------


def test_cli_corporate_actions_succeeds_and_writes_both_artifacts(ledger_path, tmp_path, capsys):
    code = cli.main(
        [
            "corporate-actions",
            "--method",
            "fifo",
            "--ledger",
            ledger_path,
            "--out-dir",
            str(tmp_path / "ca"),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_DEGRADED  # 存在待确认行 → 降级完成
    assert "待确认" in captured.err
    actions = json.loads(
        (tmp_path / "ca" / ACTIONS_FILE).read_text(encoding="utf-8")
    )
    pending = json.loads(
        (tmp_path / "ca" / PENDING_FILE).read_text(encoding="utf-8")
    )
    assert actions["format"] == "ia.actions"
    assert pending["format"] == "ia.pending"
    assert pending["items"]


def test_cli_corporate_actions_without_method_exits_two(ledger_path, capsys):
    code = cli.main(["corporate-actions", "--ledger", ledger_path])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "COST_METHOD_REQUIRED" in captured.err


def test_cli_corporate_actions_without_ledger_exits_two(capsys):
    code = cli.main(["corporate-actions", "--method", "fifo"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "LEDGER_REQUIRED" in captured.err


def test_cli_corporate_actions_is_no_longer_stage_blocked(capsys):
    code = cli.main(["corporate-actions", "--ledger", "does-not-exist.json"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "STAGE_NOT_READY" not in captured.err


def test_cli_out_pointing_at_directory_is_usage_error(ledger_path, tmp_path, capsys):
    target = tmp_path / "outdir"
    target.mkdir()
    code = cli.main(
        [
            "corporate-actions",
            "--method",
            "fifo",
            "--ledger",
            ledger_path,
            "--out",
            str(target),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "OUT_TARGET_IS_DIRECTORY" in captured.err
    assert "Traceback" not in captured.err


def test_run_corporate_actions_returns_result(ledger_path, tmp_path):
    result = run_corporate_actions(
        "wavg", ledger_path=ledger_path, out_dir=str(tmp_path / "ca")
    )
    assert result.method == "wavg"
    assert result.degraded is True
    assert (tmp_path / "ca" / ACTIONS_FILE).is_file()


# ---------------------------------------------------------------------------
# B3 可证伪探活
# ---------------------------------------------------------------------------


def test_b3_detects_injected_event_type_defect(ledger_path):
    result = actions_for(ledger_path, "fifo")
    event = result.actions["events"][0]
    event["event_type"] = "split"
    report = compare_b3(result.actions, TRUTH, DATA_DIR, "fifo")
    assert report.ok is False
    assert any(item["check"] == "event_event_type" for item in report.violations)


def test_b3_detects_injected_before_quantity_defect(ledger_path):
    result = actions_for(ledger_path, "fifo")
    event = next(
        item for item in result.actions["events"] if item["event_type"] == "split"
    )
    event["adjustment"]["before"]["quantity"] = "9999"
    report = compare_b3(result.actions, TRUTH, DATA_DIR, "fifo")
    assert report.ok is False
    assert any(
        item["check"] == "adjustment_before_quantity" for item in report.violations
    )


def test_b3_detects_dropped_pending_row(ledger_path):
    result = actions_for(ledger_path, "fifo")
    result.actions["pending"] = []
    report = compare_b3(result.actions, TRUTH, DATA_DIR, "fifo")
    assert report.ok is False
    assert any(item["check"] == "pending_set" for item in report.violations)


def test_b3_detects_missing_evidence(ledger_path):
    result = actions_for(ledger_path, "fifo")
    result.actions["events"][0]["evidence"].pop("source_line_no")
    report = compare_b3(result.actions, TRUTH, DATA_DIR, "fifo")
    assert report.ok is False
    assert any(item["check"] == "evidence_source_line_no" for item in report.violations)


def test_b3_detects_wrong_method(ledger_path):
    result = actions_for(ledger_path, "fifo")
    report = compare_b3(result.actions, TRUTH, DATA_DIR, "wavg")
    assert report.ok is False
    assert any(item["check"] == "method_label" for item in report.violations)


# ---------------------------------------------------------------------------
# 与 M2 账本同源：公司行为不改变成交流水的成本结论
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_adjusted_positions_equal_m2_holdings(ledger_path, method):
    """M3 不另立成本口径：调整后台账必须与 M2 holdings 完全一致。"""
    actions = actions_for(ledger_path, method).actions
    holdings = build_holdings(ledger_path, method, DATA_DIR).holdings
    engine = {
        (item["account_key"], item["symbol"]): (item["quantity"], item["cost_fen"])
        for item in actions["adjusted_positions"]
    }
    expected = {
        (item["account_key"], item["symbol"]): (item["quantity"], item["cost_fen"])
        for item in holdings["positions"]
    }
    assert engine == expected
