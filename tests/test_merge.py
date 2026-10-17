"""M5 多账户合并：B5 真值对齐、逐笔划转台账、合并前后守恒、可证伪探活。

真值来自 ``data/truth/merge_truth.json``（生成器独立实现：``Fraction`` 精确
数量 + 整数分成本，期末市值按估值表价格独立复算）。B5 比对函数只读两边产物，
所以"注入缺陷必须报未达标"是可证伪的。
"""

import json

import pytest

from context import DATA_DIR, REPO_ROOT

from investment_attribution import cli
from investment_attribution.attribution import build_attribution
from investment_attribution.errors import InputError, UsageError
from investment_attribution.exit_codes import EXIT_DEGRADED, EXIT_INPUT, EXIT_OK
from investment_attribution.ingest import import_files
from investment_attribution.merge import (
    MERGED_FILE,
    build_merge,
    compare_b5,
    load_transfer_patterns,
)
from investment_attribution.pipeline.merge_cmd import run_merge

SAMPLES = DATA_DIR / "samples"
TRUTH = json.loads((DATA_DIR / "truth" / "merge_truth.json").read_text(encoding="utf-8"))
COST_TRUTH = json.loads((DATA_DIR / "truth" / "cost_truth.json").read_text(encoding="utf-8"))
ATTRIBUTION_TRUTH = json.loads(
    (DATA_DIR / "truth" / "attribution_truth.json").read_text(encoding="utf-8")
)
SCENARIO_FILES = TRUTH["scenario"]["files"]
VALUATION = str(SAMPLES / TRUTH["scenario"]["valuation"])
METHODS = ("fifo", "wavg")
TRANSFER_CONTRACT = TRUTH["scenario"]["transfer_contract"]


@pytest.fixture(scope="module")
def ledger_path(tmp_path_factory):
    target = tmp_path_factory.mktemp("m5")
    import_files(
        [str(SAMPLES / name) for name in SCENARIO_FILES],
        DATA_DIR,
        out_dir=str(target),
    )
    return str(target / "ledger.json")


@pytest.fixture(scope="module")
def attribution_path(ledger_path, tmp_path_factory):
    target = tmp_path_factory.mktemp("m5-attr") / "attribution.json"
    build_attribution(
        ledger_path, "fifo", DATA_DIR, valuation_path=VALUATION, out_path=str(target)
    )
    return str(target)


@pytest.fixture(scope="module")
def merged(ledger_path, attribution_path):
    return build_merge(
        ledger_path,
        "fifo",
        DATA_DIR,
        attribution_path=attribution_path,
    ).artifact


# ---------------------------------------------------------------------------
# B5 真值对齐
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_b5_matches_truth(ledger_path, method):
    """逐账户 / 合并视图 / 划转 / 合并持仓四块都对上真值。"""
    merged = build_merge(ledger_path, method, DATA_DIR, attribution_payload=_artifact(ledger_path, method))
    report = compare_b5(merged.artifact, TRUTH, DATA_DIR, method)
    assert report.ok is True, report.as_dict()["violations"]
    assert report.checked >= 9


def _artifact(ledger_path, method):
    return build_attribution(
        ledger_path, method, DATA_DIR, valuation_path=VALUATION, sections=("xirr", "twr")
    ).artifact


def test_merged_view_total_return_equals_sum_of_accounts(merged):
    """合并前后总收益不变：各账户之和 == 合并视图，差 ≤ 门槛。"""
    checks = merged["checks"]
    assert checks["difference_fen"] == 0
    assert checks["conserved"] is True
    assert checks["total_return_after_fen"] == checks["total_return_before_fen"]
    assert checks["total_return_after_fen"] == merged["merged"]["total_return_fen"]


def test_transfer_ledger_is_per_entry_with_both_legs(merged):
    """逐笔划转台账：合同号 + 两腿 + 已抵消证据三样都要有。"""
    entries = merged["transfers"]["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["contract_no"] == TRANSFER_CONTRACT
    assert entry["trade_date"] == "2025-10-10"
    assert entry["amount_fen"] == 2000000
    assert entry["leg_count"] == 2
    assert entry["net_fen"] == 0
    assert entry["offset"]["balanced"] is True
    assert entry["offset"]["distinct_accounts"] == 2
    assert entry["offset"]["merged_flow_on_date_fen"] == 0

    directions = sorted(leg["direction"] for leg in entry["legs"])
    assert directions == ["in", "out"]
    accounts = sorted(leg["account_id"] for leg in entry["legs"])
    assert accounts == ["Y0000001", "Y0000002"]
    for leg in entry["legs"]:
        assert leg["source_file"] and leg["source_line_no"]
        assert len(leg["source_sha256"]) == 64
    # 已抵消证据逐腿可下钻：两腿都指向真实原始行
    evidence = entry["offset"]["evidence"]
    assert {(item["source_file"], item["source_line_no"]) for item in evidence} == {
        ("htsec-demo-001.xlsx", 20),
        ("huaxi-demo-001.xls", 13),
    }


def test_transfer_patterns_come_from_the_rule_pack(merged):
    """划转模式来自冻结规则包，代码里没有第二份业务名表。"""
    patterns = load_transfer_patterns(DATA_DIR)
    assert patterns == tuple(TRUTH["scenario"]["transfer_patterns"])
    assert merged["transfers"]["patterns"] == list(patterns)
    rules = json.loads(
        (DATA_DIR / "rules" / "corporate_actions.json").read_text(encoding="utf-8")
    )
    assert rules["transfer_patterns"] == list(patterns)
    assert set(patterns) <= set(rules["non_action_patterns"])


def test_overlapping_symbol_is_aggregated_with_account_counts(merged):
    """同名标的重叠持仓：合并成一行，账户计数如实显示。"""
    holdings = {item["symbol"]: item for item in merged["holdings"]}
    overlap = holdings["699001"]
    assert overlap["account_count_ever"] == 2
    assert overlap["accounts_ever"] == ["Y0000001", "Y0000002"]
    assert overlap["account_count_at_terminal"] == 1
    assert overlap["quantity"] == TRUTH["methods"]["fifo"]["merged_holdings"][0]["quantity"]


def test_merged_holdings_are_conserved(merged):
    """合并持仓市值合计 == 各账户持仓市值合计（汇总不丢不重）。"""
    assert merged["checks"]["holdings_conserved"] is True
    total = sum(item["market_value_fen"] for item in merged["holdings"])
    per_account = sum(
        account["terminal_market_value_fen"] for account in merged["accounts"]
    )
    assert total == per_account


def test_merge_carries_every_account_and_shareholder(merged):
    """分账户明细齐全，股东账号可用于报告阶段打码。"""
    ids = [account["account_id"] for account in merged["accounts"]]
    assert ids == ["Y0000001", "Y0000002", "Y0000003", "Y0000004"]
    merged_shareholders = merged["merged"]["shareholder_accounts"]
    assert merged_shareholders == ["A000000001", "A000000002", "A000000003", "A000000004"]
    for account in merged["accounts"]:
        assert len(account["shareholder_accounts"]) == 1
        assert account["shareholder_accounts"][0].startswith("A")


def test_merge_does_not_touch_inputs(ledger_path, attribution_path, tmp_path):
    """合并只读输入：流水与归因产物字节前后一致。"""
    before = (open(ledger_path, "rb").read(), open(attribution_path, "rb").read())
    build_merge(
        ledger_path,
        "fifo",
        DATA_DIR,
        attribution_path=attribution_path,
        out_path=str(tmp_path / MERGED_FILE),
    )
    after = (open(ledger_path, "rb").read(), open(attribution_path, "rb").read())
    assert before == after


def test_merge_output_is_byte_identical_across_runs(ledger_path, attribution_path, tmp_path):
    first = tmp_path / "a" / MERGED_FILE
    second = tmp_path / "b" / MERGED_FILE
    first.parent.mkdir()
    second.parent.mkdir()
    build_merge(ledger_path, "fifo", DATA_DIR, attribution_path=attribution_path, out_path=str(first))
    build_merge(ledger_path, "fifo", DATA_DIR, attribution_path=attribution_path, out_path=str(second))
    assert first.read_bytes() == second.read_bytes()


def test_merge_is_method_scoped(ledger_path, attribution_path, tmp_path):
    """合并按**显式口径**走：产出跟着口径走，且与归因产物口径必须一致。"""
    fifo = build_merge(ledger_path, "fifo", DATA_DIR, attribution_path=attribution_path)
    wavg_artifact = _artifact(ledger_path, "wavg")
    wavg = build_merge(ledger_path, "wavg", DATA_DIR, attribution_payload=wavg_artifact)
    assert fifo.artifact["method"] == "fifo"
    assert wavg.artifact["method"] == "wavg"
    with pytest.raises(UsageError) as excinfo:
        build_merge(ledger_path, "wavg", DATA_DIR, attribution_path=attribution_path)
    assert excinfo.value.code == "METHOD_MISMATCH_WITH_ATTRIBUTION"


def test_merge_requires_attribution_input(ledger_path):
    with pytest.raises(UsageError) as excinfo:
        build_merge(ledger_path, "fifo", DATA_DIR)
    assert excinfo.value.code == "ATTRIBUTION_REQUIRED"


def test_merge_rejects_non_attribution_input(ledger_path, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "ia.ledger"}), encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        build_merge(ledger_path, "fifo", DATA_DIR, attribution_path=str(bad))
    assert excinfo.value.code == "ATTRIBUTION_FORMAT_MISMATCH"


def test_merge_missing_attribution_file_is_input_error(ledger_path, tmp_path):
    with pytest.raises(InputError) as excinfo:
        build_merge(
            ledger_path, "fifo", DATA_DIR, attribution_path=str(tmp_path / "nope.json")
        )
    assert excinfo.value.code == "ATTRIBUTION_MISSING"


# ---------------------------------------------------------------------------
# 可证伪探活：把缺陷注入产物，B5 必须报未达标
# ---------------------------------------------------------------------------


def test_b5_detects_wrong_total_return(merged):
    damaged = json.loads(json.dumps(merged))
    damaged["merged"]["total_return_fen"] += 100
    assert compare_b5(damaged, TRUTH, DATA_DIR, "fifo").ok is False


def test_b5_detects_conservation_break(merged):
    damaged = json.loads(json.dumps(merged))
    damaged["checks"]["difference_fen"] = 99
    damaged["checks"]["conserved"] = False
    assert compare_b5(damaged, TRUTH, DATA_DIR, "fifo").ok is False


def test_b5_detects_dropped_transfer_leg(merged):
    damaged = json.loads(json.dumps(merged))
    damaged["transfers"]["entries"][0]["legs"] = damaged["transfers"]["entries"][0]["legs"][:1]
    damaged["transfers"]["entries"][0]["leg_count"] = 1
    assert compare_b5(damaged, TRUTH, DATA_DIR, "fifo").ok is False


def test_b5_detects_dropped_transfer_entry(merged):
    damaged = json.loads(json.dumps(merged))
    damaged["transfers"]["entries"] = []
    assert compare_b5(damaged, TRUTH, DATA_DIR, "fifo").ok is False


def test_b5_detects_holdings_aggregation_error(merged):
    damaged = json.loads(json.dumps(merged))
    damaged["holdings"] = [item for item in damaged["holdings"] if item["symbol"] != "699001"]
    assert compare_b5(damaged, TRUTH, DATA_DIR, "fifo").ok is False


def test_b5_detects_wrong_method_label(merged):
    damaged = json.loads(json.dumps(merged))
    damaged["method"] = "wavg"
    assert compare_b5(damaged, TRUTH, DATA_DIR, "fifo").ok is False


def test_b5_detects_missing_account(merged):
    damaged = json.loads(json.dumps(merged))
    damaged["accounts"] = [item for item in damaged["accounts"] if item["account_id"] != "Y0000003"]
    assert compare_b5(damaged, TRUTH, DATA_DIR, "fifo").ok is False


def test_b5_detects_wrong_quantity(merged):
    damaged = json.loads(json.dumps(merged))
    damaged["holdings"][0]["quantity"] = "1"
    assert compare_b5(damaged, TRUTH, DATA_DIR, "fifo").ok is False


# ---------------------------------------------------------------------------
# 单腿划转不假装已抵消
# ---------------------------------------------------------------------------


def test_single_leg_transfer_is_reported_not_offset(tmp_path):
    """只导入账户一：划转只剩一腿，必须落问题并 exit 1。"""
    import_files(
        [str(SAMPLES / "htsec-demo-001.xlsx")], DATA_DIR, out_dir=str(tmp_path)
    )
    ledger = str(tmp_path / "ledger.json")
    artifact = _artifact(ledger, "fifo")
    merged = build_merge(ledger, "fifo", DATA_DIR, attribution_payload=artifact)
    codes = {item["code"] for item in merged.artifact["invalid"]}
    assert "TRANSFER_LEG_UNPAIRED" in codes
    assert merged.artifact["checks"]["transfer_balanced"] is False
    assert merged.artifact["checks"]["conserved"] is True
    assert merged.exit_code == EXIT_DEGRADED


def test_transfer_net_not_zero_is_reported():
    """净额不为零的"划转"不构成抵消（构造两腿金额不等）。"""
    from investment_attribution.merge.engine import _transfer_ledger

    rows = [
        {
            "direction": "other",
            "business": "内部划转",
            "contract_no": "TR-X",
            "account_id": "Y0000001",
            "account_alias": "示例账户一",
            "settlement_amount": -100000,
            "trade_date": "2025-10-10",
            "source_file": "a.csv",
            "source_sha256": "a" * 64,
            "source_line_no": 2,
        },
        {
            "direction": "other",
            "business": "内部划转",
            "contract_no": "TR-X",
            "account_id": "Y0000002",
            "account_alias": "示例账户二",
            "settlement_amount": 90000,
            "trade_date": "2025-10-10",
            "source_file": "b.csv",
            "source_sha256": "b" * 64,
            "source_line_no": 2,
        },
    ]
    entries, problems = _transfer_ledger(rows, ("内部划转",), {}, [])
    assert entries[0]["offset"]["balanced"] is False
    assert {item["code"] for item in problems} == {"TRANSFER_NET_NOT_ZERO"}


def test_transfer_same_account_is_reported():
    from investment_attribution.merge.engine import _transfer_ledger

    def leg(sign):
        return {
            "direction": "other",
            "business": "内部划转",
            "contract_no": "TR-Y",
            "account_id": "Y0000001",
            "account_alias": "示例账户一",
            "settlement_amount": sign * 100000,
            "trade_date": "2025-10-10",
            "source_file": "a.csv",
            "source_sha256": "a" * 64,
            "source_line_no": 2,
        }

    entries, problems = _transfer_ledger([leg(-1), leg(1)], ("内部划转",), {}, [])
    assert entries[0]["offset"]["balanced"] is False
    assert {item["code"] for item in problems} == {"TRANSFER_SAME_ACCOUNT"}


def test_transfer_without_contract_is_reported():
    from investment_attribution.merge.engine import _transfer_ledger

    rows = [
        {
            "direction": "other",
            "business": "内部划转",
            "contract_no": "",
            "account_id": "Y0000001",
            "account_alias": "示例账户一",
            "settlement_amount": -100000,
            "trade_date": "2025-10-10",
            "source_file": "a.csv",
            "source_sha256": "a" * 64,
            "source_line_no": 3,
        }
    ]
    entries, problems = _transfer_ledger(rows, ("内部划转",), {}, [])
    assert entries == []
    assert {item["code"] for item in problems} == {"TRANSFER_CONTRACT_MISSING"}


def test_plain_cash_transfer_is_not_an_internal_transfer():
    """银证转入不是账户间划转：划转台账里不得出现它。"""
    from investment_attribution.merge.engine import _transfer_ledger

    rows = [
        {
            "direction": "other",
            "business": "银证转入",
            "contract_no": "HT-A-0001",
            "account_id": "Y0000001",
            "account_alias": "示例账户一",
            "settlement_amount": 10000000,
            "trade_date": "2025-01-02",
            "source_file": "a.csv",
            "source_sha256": "a" * 64,
            "source_line_no": 2,
        }
    ]
    entries, problems = _transfer_ledger(rows, ("内部划转",), {}, [])
    assert entries == []
    assert problems == []


# ---------------------------------------------------------------------------
# 真值与生成器纪律
# ---------------------------------------------------------------------------


def test_merge_truth_scenario_matches_cost_scenario():
    assert TRUTH["scenario"]["files"] == COST_TRUTH["scenario"]["files"]
    assert TRUTH["scenario"]["row_count"] == COST_TRUTH["scenario"]["row_count"]
    assert TRUTH["scenario"]["valuation"] == ATTRIBUTION_TRUTH["scenario"]["valuation"]
    assert sorted(TRUTH["methods"]) == ["fifo", "wavg"]


def test_merge_truth_covers_all_four_surfaces():
    manifest = json.loads(
        (DATA_DIR / "rules" / "manifest.json").read_text(encoding="utf-8")
    )
    surfaces = {
        item["surface"]
        for item in manifest["coverage"]["surfaces"]
        if item["surface"].startswith("merge.")
    }
    assert surfaces == set(TRUTH["surface_evidence"])
    assert len(surfaces) == 4
    for surface, item in TRUTH["surface_evidence"].items():
        assert item["keys"], surface
        assert item["expect"], surface


def test_merge_truth_transfer_entries_are_balanced():
    for entry in TRUTH["transfer_ledger"]:
        assert entry["net_fen"] == 0
        assert entry["leg_count"] == len(entry["legs"]) >= 2
        assert len({leg["account_id"] for leg in entry["legs"]}) == entry["leg_count"]


def test_merge_truth_has_manual_examples():
    examples = {item["name"]: item for item in TRUTH["manual_examples"]}
    assert len(examples) >= 3
    assert examples["two_legs_cancel_exactly"]["expect"]["net_fen"] == 0
    assert examples["single_leg_never_offsets"]["expect"]["balanced"] is False
    assert examples["same_symbol_two_accounts"]["expect"]["account_count_ever"] == 2
    for item in examples.values():
        assert item["derivation"]


def test_generator_emits_merge_truth_without_engine():
    source = (REPO_ROOT / "tools" / "gen_data.py").read_text(encoding="utf-8")
    assert "investment_attribution" not in source
    assert TRUTH["format"] == "ia.truth.merge"
    assert TRUTH["contract_version"] == "0.6.0-m5"


# ---------------------------------------------------------------------------
# CLI 契约
# ---------------------------------------------------------------------------


def test_cli_merge_writes_artifact(ledger_path, attribution_path, tmp_path, capsys):
    code = cli.main(
        [
            "merge",
            "--method",
            "fifo",
            "--ledger",
            ledger_path,
            "--attribution",
            attribution_path,
            "--out-dir",
            str(tmp_path / "merged"),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "多账户合并完成" in captured.err
    payload = json.loads((tmp_path / "merged" / MERGED_FILE).read_text(encoding="utf-8"))
    assert payload["format"] == "ia.merge"
    assert payload["checks"]["conserved"] is True


def test_run_merge_helper_writes_only_merged(ledger_path, attribution_path, tmp_path):
    result = run_merge(
        "fifo",
        ledger_path=ledger_path,
        attribution_path=attribution_path,
        data_dir=DATA_DIR,
        out=str(tmp_path / "m.json"),
    )
    assert result.exit_code == EXIT_OK
    assert (tmp_path / "m.json").is_file()
