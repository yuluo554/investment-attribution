"""M4 收益归因：B4 真值对齐、XIRR 手工算例、TWR 两种估值点口径、拆分守恒、
费用拖累、四维分解、基准可选、运行期探针与可证伪探活。

真值来自 ``data/truth/attribution_truth.json``（生成器独立实现：Decimal 高精度，
XIRR 用纯二分，与引擎的牛顿迭代互为判据）。手工算例用闭式解，是第二判据。
"""

import json

import pytest

from context import DATA_DIR, REPO_ROOT

from investment_attribution import cli
from investment_attribution.attribution import (
    build_attribution,
    compare_b4,
    load_benchmark,
    load_industries,
    load_valuation,
    load_xirr_params,
    market_value_fen,
    solve_xirr,
)
from investment_attribution.errors import DegradedError, InputError
from investment_attribution.exit_codes import EXIT_DEGRADED, EXIT_INPUT, EXIT_OK
from investment_attribution.ingest import import_files
from investment_attribution.ledger import build_holdings
from investment_attribution.pipeline.attribution_cmd import run_attribution
from investment_attribution.serialize import dumps_json

SAMPLES = DATA_DIR / "samples"
TRUTH = json.loads(
    (DATA_DIR / "truth" / "attribution_truth.json").read_text(encoding="utf-8")
)
SCENARIO_FILES = TRUTH["scenario"]["files"]
VALUATION = str(SAMPLES / TRUTH["scenario"]["valuation"])
METHODS = ("fifo", "wavg")


@pytest.fixture(scope="module")
def ledger_path(tmp_path_factory):
    target = tmp_path_factory.mktemp("m4")
    import_files(
        [str(SAMPLES / name) for name in SCENARIO_FILES],
        DATA_DIR,
        out_dir=str(target),
    )
    return str(target / "ledger.json")


@pytest.fixture(scope="module")
def artifact(ledger_path):
    return build_attribution(
        ledger_path, "fifo", DATA_DIR, valuation_path=VALUATION
    ).artifact


def by_key(items, field="account_key"):
    return {item[field]: item for item in items}


# ---------------------------------------------------------------------------
# XIRR：手工算例（闭式解）+ 无解不造数 + 确定性
# ---------------------------------------------------------------------------


def test_xirr_reproduces_hand_worked_examples():
    """闭式解算例：一年 +10%、两年 +10%、一年 -10%、零收益。"""
    params = load_xirr_params(DATA_DIR)
    for example in TRUTH["manual_examples"]:
        solution = solve_xirr(
            [(date, amount) for date, amount in example["cash_flows"]], params
        )
        expected = float(example["expected_rate"])
        assert abs(solution.rate - expected) < 1e-9, (
            example["name"],
            solution.rate,
            expected,
        )


def test_xirr_no_solution_is_degraded_not_fabricated():
    params = load_xirr_params(DATA_DIR)
    with pytest.raises(DegradedError) as excinfo:
        solve_xirr(
            [("2025-01-01", -1000000), ("2025-06-01", -500000)], params
        )
    assert excinfo.value.code == "XIRR_NO_SOLUTION"
    assert excinfo.value.exit_code == EXIT_DEGRADED


def test_xirr_is_deterministic():
    params = load_xirr_params(DATA_DIR)
    flows = [
        ("2025-01-02", -10000000),
        ("2025-09-02", -5000000),
        ("2025-10-10", 2000000),
        ("2025-12-31", 13682388),
    ]
    first = solve_xirr(flows, params)
    second = solve_xirr(flows, params)
    assert first.rate == second.rate
    assert first.solver == second.solver


def test_xirr_rejects_short_series():
    params = load_xirr_params(DATA_DIR)
    with pytest.raises(DegradedError):
        solve_xirr([("2025-01-01", 100)], params)


# ---------------------------------------------------------------------------
# B4：与真值逐账户 / 逐视图对齐（双口径）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_b4_matches_truth(ledger_path, method):
    result = build_attribution(
        ledger_path, method, DATA_DIR, valuation_path=VALUATION
    )
    report = compare_b4(result.artifact, TRUTH, DATA_DIR, method)
    assert report.ok, report.as_dict()
    assert report.checked > 0


@pytest.mark.parametrize("method", METHODS)
def test_account_and_merged_xirr_hit_truth_to_high_precision(ledger_path, method):
    """相对误差要远小于门槛（1e-4），否则"对齐真值"就是凑出来的。"""
    from decimal import Decimal

    artifact_payload = build_attribution(
        ledger_path, method, DATA_DIR, valuation_path=VALUATION
    ).artifact
    engine = by_key(artifact_payload["xirr"]["accounts"])
    for expected in list(TRUTH["views"]["accounts"]) + [TRUTH["views"]["merged"]]:
        key = expected["account_key"]
        actual = Decimal(str(engine[key]["rate"]))
        target = Decimal(str(expected["xirr"]))
        relative = abs(actual - target) / max(abs(target), Decimal("1e-9"))
        assert relative < Decimal("1e-9"), (key, actual, target)


@pytest.mark.parametrize("basis", ("cashflow", "daily"))
def test_twr_both_bases_match_truth(ledger_path, basis):
    artifact_payload = build_attribution(
        ledger_path, "fifo", DATA_DIR, valuation_path=VALUATION, twr_basis=basis
    ).artifact
    engine = by_key(artifact_payload["twr"]["accounts"])
    for expected in list(TRUTH["views"]["accounts"]) + [TRUTH["views"]["merged"]]:
        key = expected["account_key"]
        assert engine[key]["status"] == "ok", (key, engine[key]["status"])
        assert engine[key]["cumulative_rate"] == expected["twr"][basis], key


def test_twr_default_basis_comes_from_params(ledger_path):
    params = json.loads((DATA_DIR / "rules" / "params.json").read_text(encoding="utf-8"))
    artifact_payload = build_attribution(
        ledger_path, "fifo", DATA_DIR, valuation_path=VALUATION
    ).artifact
    assert artifact_payload["twr"]["basis"] == params["twr"]["default_basis"]
    assert artifact_payload["twr"]["basis"] == "cashflow"


def test_twr_reports_missing_valuation_day(ledger_path, tmp_path):
    """缺市值日必须报 VALUATION_MISSING，TWR 不输出近似值（也不静默丢段）。"""
    partial = tmp_path / "partial.csv"
    partial.write_text(
        "date,symbol,name,quantity,price\n2025-12-31,699001,示例科技甲,500,10.20\n"
        "2025-12-31,699003,示例基金丙,7000,0.65\n"
        "2025-12-31,699004,示例转债丁,200,108.00\n",
        encoding="utf-8",
    )
    result = build_attribution(
        ledger_path, "fifo", DATA_DIR, valuation_path=str(partial), sections=("twr",)
    )
    assert result.exit_code == EXIT_DEGRADED
    codes = {item["code"] for item in result.artifact["invalid"]}
    assert "VALUATION_MISSING" in codes
    views = {view["account_key"]: view for view in result.artifact["twr"]["accounts"]}
    # 中间估值点缺价：持有期间的账户与合并视图必须报缺，且不输出近似值
    assert views["Y0000001"]["status"] == "valuation_missing"
    assert views["merged"]["status"] == "valuation_missing"
    for view in views.values():
        if view["status"] == "valuation_missing":
            assert view["cumulative_rate"] is None
            assert view["segments"] == []


def test_twr_daily_requires_every_snapshot_date(ledger_path, tmp_path):
    """daily 口径要每天的估值点：只给现金流日的估值表必须报缺。"""
    rows = (SAMPLES / "valuation-demo-001.csv").read_text(encoding="utf-8").splitlines()
    header, body = rows[0], rows[1:]
    flow_dates = {
        "2025-01-02",
        "2025-01-10",
        "2025-02-20",
        "2025-03-20",
        "2025-09-02",
        "2025-10-10",
        "2025-12-31",
    }
    kept = [line for line in body if line.split(",")[0] in flow_dates]
    target = tmp_path / "flow-only.csv"
    target.write_text("\n".join([header] + kept) + "\n", encoding="utf-8")
    cashflow = build_attribution(
        ledger_path, "fifo", DATA_DIR, valuation_path=str(target), sections=("twr",), twr_basis="cashflow"
    )
    daily = build_attribution(
        ledger_path, "fifo", DATA_DIR, valuation_path=str(target), sections=("twr",), twr_basis="daily"
    )
    assert any(item["status"] == "ok" for item in cashflow.artifact["twr"]["accounts"])
    assert all(item["status"] == "valuation_missing" for item in daily.artifact["twr"]["accounts"])


# ---------------------------------------------------------------------------
# 拆分守恒 / 费用拖累 / 分解守恒
# ---------------------------------------------------------------------------


def test_split_conservation_holds_for_every_view(artifact):
    for item in artifact["pnl"]["accounts"] + [artifact["pnl"]["merged"]]:
        assert item["conserved"] is True, item
        assert item["difference_fen"] == 0, item
        assert (
            item["realized_total_fen"] + item["unrealized_fen"]
            == item["total_return_fen"]
        )
        assert (
            item["terminal_total_fen"] - item["net_contribution_fen"]
            == item["total_return_fen"]
        )


def test_fee_total_matches_m2_ledger(ledger_path, artifact):
    holdings = build_holdings(ledger_path, "fifo", DATA_DIR).holdings
    assert artifact["fees"]["total_fen"] == holdings["totals"]["fees_total_fen"]
    assert artifact["fees"]["by_type_fen"] == holdings["totals"]["fees_fen"]
    assert artifact["fees"]["matches_m2_total"] is True


def test_fee_drag_is_recomputable(artifact):
    total = artifact["fees"]["total_fen"]
    per_account = sum(item["fees_fen"] for item in artifact["fees"]["by_account"])
    assert total == per_account
    assert artifact["fees"]["drag_bps"] == (
        total * 10000 // artifact["fees"]["contributions_fen"]
    )


def test_four_dimension_decomposition_sums_to_total(artifact):
    checks = artifact["decomposition"]["checks"]
    assert checks["all_equal"] is True, checks
    for key in ("by_symbol_fen", "by_industry_fen", "by_account_fen", "by_year_fen"):
        assert checks[key] == checks["merged_total_fen"], key
    industries = {item["industry"] for item in artifact["decomposition"]["by_industry"]}
    assert industries, "行业分解不能为空"


def test_industry_map_reports_unclassified(artifact):
    assert artifact["decomposition"]["unclassified_symbols"] == []
    industries = load_industries(DATA_DIR)
    assert industries.of("699001") == "信息技术"
    assert industries.of("699099") == "未分类"
    assert industries.unclassified(["699001", "699099"]) == ["699099"]


def test_unclassified_symbol_lands_in_visible_bucket(ledger_path, tmp_path):
    """未登记行业代码必须落"未分类"且可见（不静默丢弃）。"""
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        dumps_json(
            {
                "format": "ia.ledger",
                "format_version": 1,
                "rows": [
                    _crafted_row(2, "2025-03-03", "699099", "银证转入", settlement=1000000),
                    _crafted_row(
                        3,
                        "2025-03-04",
                        "699099",
                        "证券买入",
                        quantity="100",
                        direction="buy",
                        price="10.00",
                        amount=100000,
                        settlement=-1000500,
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    valuation = tmp_path / "valuation.csv"
    valuation.write_text(
        "date,symbol,name,quantity,price\n2025-03-04,699099,示例科技甲,100,10.5\n",
        encoding="utf-8",
    )
    result = build_attribution(
        str(ledger), "fifo", DATA_DIR, valuation_path=str(valuation)
    )
    buckets = {
        item["industry"]: item for item in result.artifact["decomposition"]["by_industry"]
    }
    assert "未分类" in buckets
    assert result.artifact["decomposition"]["unclassified_symbols"] == ["699099"]


def test_multi_year_decomposition_splits_by_year(tmp_path):
    """跨年度分解要真的分桶，且各桶之和等于总收益。"""
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        dumps_json(
            {
                "format": "ia.ledger",
                "format_version": 1,
                "rows": [
                    _crafted_row(2, "2024-06-03", "699001", "银证转入", settlement=1000000),
                    _crafted_row(
                        3,
                        "2024-06-05",
                        "699001",
                        "证券买入",
                        quantity="100",
                        direction="buy",
                        price="10.00",
                        amount=100000,
                        settlement=-100500,
                        commission=500,
                    ),
                    _crafted_row(
                        4,
                        "2025-03-05",
                        "699001",
                        "证券卖出",
                        quantity="100",
                        direction="sell",
                        price="11.00",
                        amount=110000,
                        settlement=108400,
                        commission=500,
                        stamp_tax=1100,
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    valuation = tmp_path / "valuation.csv"
    valuation.write_text(
        "date,symbol,name,quantity,price\n"
        "2024-06-05,699001,示例科技甲,100,10.00\n"
        "2025-03-05,699001,示例科技甲,100,11.00\n"
        "2025-12-31,699001,示例科技甲,0,1.00\n",
        encoding="utf-8",
    )
    result = build_attribution(
        str(ledger), "fifo", DATA_DIR, valuation_path=str(valuation)
    )
    years = {item["year"]: item for item in result.artifact["decomposition"]["by_year"]}
    assert set(years) == {"2024", "2025"}
    assert years["2025"]["realized_fen"] == 108400 - 100500
    assert years["2024"]["total_fen"] == 0
    assert years["2025"]["total_fen"] == 108400 - 100500
    assert result.artifact["decomposition"]["checks"]["all_equal"] is True


# ---------------------------------------------------------------------------
# 现金流语义：划转是外部现金流、待确认行进位可见、成交不进现金流
# ---------------------------------------------------------------------------


def test_internal_transfer_is_external_flow_per_account(artifact):
    first = by_key(artifact["xirr"]["accounts"])["Y0000001"]
    second = by_key(artifact["xirr"]["accounts"])["Y0000002"]
    out_leg = [
        row
        for row in first["cash_flows"]
        if row["business"] == "内部划转" and row["date"] == "2025-10-10"
    ]
    in_leg = [
        row
        for row in second["cash_flows"]
        if row["business"] == "内部划转" and row["date"] == "2025-10-10"
    ]
    assert out_leg and out_leg[0]["amount_fen"] == 2000000
    assert in_leg and in_leg[0]["amount_fen"] == -2000000


def test_internal_transfer_cancels_in_merged_view(artifact):
    merged = by_key(artifact["xirr"]["accounts"])["merged"]
    same_day = [row for row in merged["cash_flows"] if row["date"] == "2025-10-10"]
    assert sum(row["amount_fen"] for row in same_day) == 0


def test_trades_and_dividends_are_not_external_flows(artifact):
    for view in artifact["xirr"]["accounts"]:
        for row in view["cash_flows"]:
            assert row["kind"] in ("capital_in", "capital_out", "opening_cash", "terminal_value")


def test_unclassified_cash_row_is_income_and_visible(artifact):
    merged = artifact["pnl"]["merged"]
    assert merged["other_income_fen"] == 1234
    warnings = {item["code"] for item in artifact["warnings"]}
    assert "UNCLASSIFIED_CASH_ROW" in warnings
    # 它是收益的一部分，但绝不是外部现金流
    assert all(
        row["business"] != "其他业务甲"
        for view in artifact["xirr"]["accounts"]
        for row in view["cash_flows"]
    )


def test_reinvested_dividend_counts_as_income_not_cash_flow(artifact):
    merged = artifact["pnl"]["merged"]
    assert merged["reinvested_income_fen"] == 95000
    assert merged["dividend_income_fen"] == 60000


# ---------------------------------------------------------------------------
# 基准（可选）
# ---------------------------------------------------------------------------


def test_benchmark_absent_is_success_without_excess_columns(artifact):
    assert artifact["benchmark"]["provided"] is False
    assert "excess" not in artifact["benchmark"]
    blob = dumps_json(artifact)
    assert "xirr_excess" not in blob
    assert "twr_excess" not in blob


def test_benchmark_provided_computes_excess(ledger_path, tmp_path):
    benchmark = tmp_path / "benchmark.csv"
    benchmark.write_text(
        "date,value\n"
        "2025-01-02,1000.00\n"
        "2025-01-10,1010.00\n"
        "2025-02-20,1020.00\n"
        "2025-03-20,1030.00\n"
        "2025-09-02,1050.00\n"
        "2025-10-10,1060.00\n"
        "2025-12-31,1100.00\n",
        encoding="utf-8",
    )
    result = build_attribution(
        ledger_path,
        "fifo",
        DATA_DIR,
        valuation_path=VALUATION,
        benchmark_path=str(benchmark),
    )
    section = result.artifact["benchmark"]
    assert section["provided"] is True
    assert section["excess"]["window"]["start_date"] == "2025-01-02"
    assert section["excess"]["window"]["end_date"] == "2025-12-31"
    assert section["excess"]["xirr_excess"] is not None
    assert section["excess"]["twr_excess"] is not None
    # 超额 = 组合收益 − 基准收益，可复算
    from decimal import Decimal

    xirr = Decimal(by_key(result.artifact["xirr"]["accounts"])["merged"]["rate"])
    bench = Decimal(section["excess"]["benchmark_annualized_return"])
    assert Decimal(section["excess"]["xirr_excess"]) == (xirr - bench).quantize(
        Decimal("0.000000000001")
    )


def test_benchmark_missing_dates_is_reported(ledger_path, tmp_path):
    benchmark = tmp_path / "benchmark.csv"
    benchmark.write_text(
        "date,value\n2025-06-02,1000.00\n2025-12-31,1100.00\n", encoding="utf-8"
    )
    result = build_attribution(
        ledger_path,
        "fifo",
        DATA_DIR,
        valuation_path=VALUATION,
        benchmark_path=str(benchmark),
    )
    assert result.exit_code == EXIT_DEGRADED
    codes = {item["code"] for item in result.artifact["invalid"]}
    assert "BENCHMARK_DATE_MISSING" in codes
    assert result.artifact["benchmark"]["excess"] is None


def test_benchmark_file_absent_from_disk_is_input_error(tmp_path):
    with pytest.raises(InputError) as excinfo:
        load_benchmark(str(tmp_path / "none.csv"))
    assert excinfo.value.code == "BENCHMARK_MISSING"


# ---------------------------------------------------------------------------
# 估值输入校验
# ---------------------------------------------------------------------------


def test_valuation_quantity_mismatch_is_reported(ledger_path, tmp_path):
    valuation = tmp_path / "wrong-quantity.csv"
    valuation.write_text(
        "date,symbol,name,quantity,price\n2025-12-31,699001,示例科技甲,9999,10.20\n",
        encoding="utf-8",
    )
    result = build_attribution(
        ledger_path, "fifo", DATA_DIR, valuation_path=str(valuation)
    )
    codes = {item["code"] for item in result.artifact["invalid"]}
    assert "VALUATION_QUANTITY_MISMATCH" in codes
    assert result.exit_code == EXIT_DEGRADED


def test_valuation_missing_file_is_input_error(tmp_path):
    with pytest.raises(InputError) as excinfo:
        load_valuation(str(tmp_path / "nope.csv"))
    assert excinfo.value.code == "VALUATION_MISSING"


def test_valuation_duplicate_row_is_input_error(tmp_path):
    target = tmp_path / "dup.csv"
    target.write_text(
        "date,symbol,quantity,price\n2025-12-31,699001,1,10.00\n2025-12-31,699001,1,10.00\n",
        encoding="utf-8",
    )
    with pytest.raises(InputError) as excinfo:
        load_valuation(str(target))
    assert excinfo.value.code == "VALUATION_DUPLICATE_ROW"


def test_valuation_invalid_price_is_input_error(tmp_path):
    target = tmp_path / "bad.csv"
    target.write_text("date,symbol,price\n2025-12-31,699001,-1\n", encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        load_valuation(str(target))
    assert excinfo.value.code == "VALUATION_ROW_INVALID"


def test_valuation_before_ledger_end_is_input_error(ledger_path, tmp_path):
    target = tmp_path / "early.csv"
    target.write_text(
        "date,symbol,price\n2025-06-30,699001,10.00\n", encoding="utf-8"
    )
    with pytest.raises(InputError) as excinfo:
        build_attribution(ledger_path, "fifo", DATA_DIR, valuation_path=str(target))
    assert excinfo.value.code == "VALUATION_BEFORE_LEDGER_END"


def test_market_value_rounds_half_up():
    assert market_value_fen("3", "3.335") == 1001
    assert market_value_fen("1", "10") == 1000


# ---------------------------------------------------------------------------
# 产物形态 / 确定性 / 只读
# ---------------------------------------------------------------------------


def test_artifact_declares_basis_in_header(artifact):
    basis = artifact["basis"]
    assert basis["cost_method"] == "fifo"
    assert basis["twr_basis"] == "cashflow"
    assert basis["benchmark_provided"] is False
    assert basis["valuation_date"] == "2025-12-31"
    assert artifact["method_label"]
    assert "口径" in artifact["basis"]["twr_basis_notice"]


def test_artifact_carries_source_evidence(artifact):
    for view in artifact["xirr"]["accounts"]:
        for row in view["cash_flows"]:
            if row["kind"] in ("capital_in", "capital_out"):
                assert row["source_file"] and row["source_line_no"]


def test_runtime_probe_recorded_in_artifact(artifact):
    probe = artifact["zero_upload"]["runtime_probe"]
    assert probe["installed"] is True
    assert probe["armed"] is True
    assert probe["blocked_attempts"] == 0


def test_output_is_byte_identical_across_runs(ledger_path, tmp_path):
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    for target in (first, second):
        build_attribution(
            ledger_path, "fifo", DATA_DIR, valuation_path=VALUATION, out_path=str(target)
        )
    assert first.read_bytes() == second.read_bytes()


def test_attribution_does_not_touch_ledger_or_valuation_bytes(ledger_path):
    from pathlib import Path

    ledger_before = Path(ledger_path).read_bytes()
    valuation_before = Path(VALUATION).read_bytes()
    build_attribution(ledger_path, "fifo", DATA_DIR, valuation_path=VALUATION)
    assert Path(ledger_path).read_bytes() == ledger_before
    assert Path(VALUATION).read_bytes() == valuation_before


# ---------------------------------------------------------------------------
# B4 可证伪探活
# ---------------------------------------------------------------------------


def test_b4_detects_injected_xirr_defect(artifact):
    damaged = json.loads(dumps_json(artifact))
    damaged["xirr"]["accounts"][0]["rate"] = "0.5"
    report = compare_b4(damaged, TRUTH, DATA_DIR, "fifo")
    assert report.ok is False
    assert any(item["check"] == "relative" for item in report.violations)


def test_b4_detects_injected_split_defect(artifact):
    damaged = json.loads(dumps_json(artifact))
    damaged["pnl"]["accounts"][0]["unrealized_fen"] += 10000
    report = compare_b4(damaged, TRUTH, DATA_DIR, "fifo")
    assert report.ok is False
    assert any(item["check"] == "amount" for item in report.violations)


def test_b4_detects_dropped_account(artifact):
    damaged = json.loads(dumps_json(artifact))
    damaged["pnl"]["accounts"] = damaged["pnl"]["accounts"][1:]
    report = compare_b4(damaged, TRUTH, DATA_DIR, "fifo")
    assert report.ok is False
    assert any(item["check"] == "pnl_missing" for item in report.violations)


def test_b4_detects_wrong_method(artifact):
    report = compare_b4(artifact, TRUTH, DATA_DIR, "wavg")
    assert report.ok is False
    assert any(item["check"] == "method_label" for item in report.violations)


def test_b4_detects_broken_decomposition(artifact):
    damaged = json.loads(dumps_json(artifact))
    damaged["decomposition"]["by_symbol"][0]["total_fen"] += 100
    report = compare_b4(damaged, TRUTH, DATA_DIR, "fifo")
    assert report.ok is False


# ---------------------------------------------------------------------------
# CLI 契约
# ---------------------------------------------------------------------------


def test_cli_attribution_succeeds_and_writes_artifact(ledger_path, tmp_path, capsys):
    code = cli.main(
        [
            "attribution",
            "--method",
            "fifo",
            "--ledger",
            ledger_path,
            "--valuation",
            VALUATION,
            "--out-dir",
            str(tmp_path / "attr"),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "收益归因完成" in captured.err
    payload = json.loads((tmp_path / "attr" / "attribution.json").read_text(encoding="utf-8"))
    assert payload["format"] == "ia.attribution"
    assert payload["xirr"]["accounts"] and payload["twr"]["accounts"]


def test_cli_attribution_section_selectors(ledger_path, tmp_path):
    result = run_attribution(
        "fifo",
        ledger_path=ledger_path,
        data_dir=DATA_DIR,
        valuation_path=VALUATION,
        sections=("xirr",),
        out=str(tmp_path / "xirr-only.json"),
    )
    assert "xirr" in result.artifact
    assert "twr" not in result.artifact
    result = run_attribution(
        "fifo",
        ledger_path=ledger_path,
        data_dir=DATA_DIR,
        valuation_path=VALUATION,
        sections=("twr",),
        out=str(tmp_path / "twr-only.json"),
    )
    assert "twr" in result.artifact
    assert "xirr" not in result.artifact


def test_cli_attribution_without_method_exits_two(ledger_path, capsys):
    code = cli.main(
        ["attribution", "--ledger", ledger_path, "--valuation", VALUATION]
    )
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "COST_METHOD_REQUIRED" in captured.err


def test_cli_attribution_without_ledger_exits_two(capsys):
    code = cli.main(["attribution", "--method", "fifo", "--valuation", VALUATION])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "LEDGER_REQUIRED" in captured.err


def test_cli_attribution_without_valuation_exits_two(ledger_path, capsys):
    code = cli.main(
        ["attribution", "--method", "fifo", "--ledger", ledger_path]
    )
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "VALUATION_REQUIRED" in captured.err


def test_cli_attribution_is_no_longer_stage_blocked(capsys):
    code = cli.main(["attribution", "--method", "fifo", "--ledger", "does-not-exist.json"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "STAGE_NOT_READY" not in captured.err


def test_cli_unknown_twr_basis_is_usage_error(ledger_path):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "attribution",
                "--method",
                "fifo",
                "--ledger",
                ledger_path,
                "--valuation",
                VALUATION,
                "--twr-basis",
                "weekly",
            ]
        )
    assert excinfo.value.code == EXIT_INPUT


# ---------------------------------------------------------------------------
# 构造用助手
# ---------------------------------------------------------------------------


def _crafted_row(
    line,
    date,
    symbol,
    business,
    quantity="0",
    direction="other",
    price=None,
    amount=None,
    settlement=None,
    commission=0,
    stamp_tax=0,
    transfer_fee=0,
    other_fee=0,
    extra=None,
):
    return {
        "source_file": "crafted.csv",
        "source_sha256": "a" * 64,
        "source_line_no": line,
        "account_id": "Y0000001",
        "account_alias": "示例账户一",
        "account_status": "registered",
        "shareholder_account": "A000000001",
        "symbol": symbol,
        "name": "示例科技甲",
        "business": business,
        "trade_date": date,
        "trade_time": "09:30:00",
        "quantity": quantity,
        "price": price,
        "amount": amount,
        "settlement_amount": settlement,
        "commission": commission,
        "stamp_tax": stamp_tax,
        "transfer_fee": transfer_fee,
        "other_fee": other_fee,
        "direction": direction,
        "extra": extra or {},
    }
