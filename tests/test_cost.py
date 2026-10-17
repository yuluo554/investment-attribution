"""M2 成本引擎：双口径对齐真值、费用入账、T+1 勾稽、快照确定性。

真值来自 ``data/truth/cost_truth.json``（生成器独立实现，不 import 引擎），
所以"引擎对着真值跑"不是"自己比自己"。另一半是负向断言：该红的地方必须红。
"""

import json
import shutil

import pytest

from context import DATA_DIR, REPO_ROOT

from investment_attribution import cli
from investment_attribution.errors import IAError, InputError, UsageError
from investment_attribution.exit_codes import EXIT_DEGRADED, EXIT_INPUT, EXIT_OK
from investment_attribution.ingest import import_files
from investment_attribution.ledger import build_holdings, build_pnl
from investment_attribution.ledger.engine import require_method
from investment_attribution.ledger.model import analyze_row, sorted_rows
from investment_attribution.pipeline.cost_cmd import run_cost, run_pnl
from investment_attribution.serialize import dumps_json

SAMPLES = DATA_DIR / "samples"
TRUTH = json.loads(
    (DATA_DIR / "truth" / "cost_truth.json").read_text(encoding="utf-8")
)
SCENARIO_FILES = TRUTH["scenario"]["files"]


@pytest.fixture(scope="module")
def ledger_path(tmp_path_factory):
    target = tmp_path_factory.mktemp("m2")
    result = import_files(
        [str(SAMPLES / name) for name in SCENARIO_FILES],
        DATA_DIR,
        out_dir=str(target),
    )
    return str(target / "ledger.json")


def holdings_for(ledger_path, method):
    return build_holdings(ledger_path, method, DATA_DIR)


# ---------------------------------------------------------------------------
# B2 逐笔对齐（FIFO 与 wavg 各一遍）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ("fifo", "wavg"))
def test_every_event_matches_truth(ledger_path, method):
    result = holdings_for(ledger_path, method)
    truth = TRUTH["methods"][method]
    assert len(result.holdings["events"]) == len(truth["events"])

    engine_events = {
        (event["source_file"], event["source_line_no"]): event
        for event in result.holdings["events"]
    }
    for expected in truth["events"]:
        key = tuple(expected["key"])
        actual = engine_events[key]
        assert actual["kind"] == expected["kind"], key
        assert actual["position"]["quantity_before"] == expected["position_before_quantity"], key
        assert actual["position"]["cost_before_fen"] == expected["position_before_cost_fen"], key
        assert actual["position"]["quantity_after"] == expected["position_after_quantity"], key
        assert actual["position"]["cost_after_fen"] == expected["position_after_cost_fen"], key
        realized = (actual["realized"] or {}).get("realized_fen")
        assert realized == expected["realized_fen"], key


@pytest.mark.parametrize("method", ("fifo", "wavg"))
def test_positions_and_totals_match_truth(ledger_path, method):
    holdings = holdings_for(ledger_path, method).holdings
    truth = TRUTH["methods"][method]

    assert holdings["totals"]["position_cost_fen"] == truth["totals"]["position_cost_fen"]
    assert holdings["totals"]["realized_net_fen"] == truth["totals"]["realized_net_fen"]
    assert holdings["totals"]["fees_total_fen"] == truth["totals"]["fees_total_fen"]
    assert holdings["totals"]["open_positions"] == truth["totals"]["open_positions"]

    engine_positions = {
        (item["account_id"], item["symbol"]): item for item in holdings["positions"]
    }
    for expected in truth["positions"]:
        key = (expected["account_id"], expected["symbol"])
        actual = engine_positions[key]
        assert actual["quantity"] == expected["quantity"], key
        assert actual["cost_fen"] == expected["cost_fen"], key
        assert actual["is_open"] is expected["is_open"], key


@pytest.mark.parametrize("method", ("fifo", "wavg"))
def test_account_rows_match_truth(ledger_path, method):
    holdings = holdings_for(ledger_path, method).holdings
    truth = TRUTH["methods"][method]
    engine_accounts = {item["account_id"]: item for item in holdings["accounts"]}
    for expected in truth["accounts"]:
        actual = engine_accounts[expected["account_id"]]
        assert actual["realized_net_fen"] == expected["realized_net_fen"]
        assert actual["position_cost_fen"] == expected["position_cost_fen"]
        assert actual["fees_fen"] != {}
        assert actual["net_cash_flow_fen"] == expected["net_cash_flow_fen"]
        assert actual["opening_cash_fen"] == expected["opening_cash_fen"]


@pytest.mark.parametrize("method", ("fifo", "wavg"))
def test_daily_snapshots_match_truth(ledger_path, method):
    holdings = holdings_for(ledger_path, method).holdings
    truth = TRUTH["methods"][method]
    engine = {
        snap["date"]: [
            (item["account_id"], item["symbol"], item["quantity"], item["cost_fen"])
            for item in snap["positions"]
        ]
        for snap in holdings["daily_snapshots"]
    }
    expected = {
        snap["date"]: [
            (item["account_id"], item["symbol"], item["quantity"], item["cost_fen"])
            for item in snap["positions"]
        ]
        for snap in truth["daily_snapshots"]
    }
    assert engine == expected


# ---------------------------------------------------------------------------
# 双口径差异可解释到具体成交笔
# ---------------------------------------------------------------------------


def test_divergences_are_explained_by_specific_trades(ledger_path):
    fifo = {
        (e["source_file"], e["source_line_no"]): e
        for e in holdings_for(ledger_path, "fifo").holdings["events"]
    }
    wavg = {
        (e["source_file"], e["source_line_no"]): e
        for e in holdings_for(ledger_path, "wavg").holdings["events"]
    }
    assert TRUTH["divergences"], "构造样例必须存在双口径差异"
    for item in TRUTH["divergences"]:
        key = (item["source_file"], item["source_line_no"])
        assert fifo[key]["realized"]["realized_fen"] == item["fifo_realized_fen"], key
        assert wavg[key]["realized"]["realized_fen"] == item["wavg_realized_fen"], key
        # 差异必须是"同一笔卖出、取的成本不同"，不是数量或笔数不同
        assert fifo[key]["kind"] == wavg[key]["kind"] == "trade_sell", key
        assert fifo[key]["quantity"] == wavg[key]["quantity"], key


def test_dual_method_totals_differ_and_are_both_deterministic(ledger_path):
    fifo = holdings_for(ledger_path, "fifo").holdings
    wavg = holdings_for(ledger_path, "wavg").holdings
    assert fifo["totals"]["realized_net_fen"] != wavg["totals"]["realized_net_fen"]
    assert dumps_json(fifo) == dumps_json(holdings_for(ledger_path, "fifo").holdings)


# ---------------------------------------------------------------------------
# 口径切换不修改原始流水
# ---------------------------------------------------------------------------


def test_switching_method_does_not_touch_ledger_bytes(ledger_path):
    from pathlib import Path

    before = Path(ledger_path).read_bytes()
    for method in ("fifo", "wavg", "fifo"):
        build_holdings(ledger_path, method, DATA_DIR)
    assert Path(ledger_path).read_bytes() == before


def test_holdings_declares_method_and_never_defaults():
    with pytest.raises(UsageError) as excinfo:
        require_method(None, {"params": {"cost_methods": ["fifo", "wavg"]}})
    assert excinfo.value.code == "COST_METHOD_REQUIRED"

    with pytest.raises(UsageError) as excinfo:
        require_method("lifo", {"params": {"cost_methods": ["fifo", "wavg"]}})
    assert excinfo.value.code == "COST_METHOD_UNKNOWN"


def test_cli_without_method_exits_two(ledger_path, capsys):
    code = cli.main(["cost", "--ledger", ledger_path])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "COST_METHOD_REQUIRED" in captured.err


def test_cli_cost_and_pnl_succeed(ledger_path, tmp_path, capsys):
    code = cli.main(
        ["cost", "--method", "fifo", "--ledger", ledger_path, "--out-dir", str(tmp_path / "c")]
    )
    assert code == EXIT_OK
    payload = json.loads((tmp_path / "c" / "holdings.json").read_text(encoding="utf-8"))
    assert payload["method"] == "fifo"
    assert "先进先出" in payload["method_label"]
    assert "先进先出" in payload["method_notice"]

    code = cli.main(
        ["pnl", "--method", "wavg", "--ledger", ledger_path, "--out-dir", str(tmp_path / "p"), "--fees"]
    )
    assert code == EXIT_OK
    pnl = json.loads((tmp_path / "p" / "pnl.json").read_text(encoding="utf-8"))
    assert pnl["method"] == "wavg"
    assert pnl["fee_detail"]
    assert pnl["fees"]["by_type_fen"]["commission"] > 0


def test_cli_cost_missing_ledger_exits_two(capsys):
    code = cli.main(["cost", "--method", "fifo"])
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "LEDGER_REQUIRED" in captured.err


def test_cli_out_pointing_at_directory_is_usage_error(ledger_path, tmp_path, capsys):
    """``--out`` 指向目录必须给用法错误，不能抛 traceback。"""
    target = tmp_path / "outdir"
    target.mkdir()
    code = cli.main(
        ["cost", "--method", "fifo", "--ledger", ledger_path, "--out", str(target)]
    )
    captured = capsys.readouterr()
    assert code == EXIT_INPUT
    assert "OUT_TARGET_IS_DIRECTORY" in captured.err
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# 费用：按实际列值入账；未分列按显式规则并标注
# ---------------------------------------------------------------------------


def test_fees_are_taken_from_reported_columns(ledger_path):
    holdings = holdings_for(ledger_path, "fifo").holdings
    by_key = {
        (event["source_file"], event["source_line_no"]): event
        for event in holdings["events"]
    }
    # 海通 2025-01-06 买入 1000 股：佣金 5.00 / 过户费 0.10
    buy = by_key[("htsec-demo-001.xlsx", 3)]
    assert buy["fee_source"] == "as_reported"
    assert buy["fees_fen"] == 510
    assert buy["fee_detail"]["commission"] == 500
    assert buy["fee_detail"]["transfer_fee"] == 10


def test_unitemized_fees_are_derived_and_labelled(ledger_path):
    holdings = holdings_for(ledger_path, "fifo").holdings
    derived = [
        event
        for event in holdings["events"]
        if event["source_file"] == "generic-demo-015-no-fee-columns.csv"
        and event["kind"] in ("trade_buy", "trade_sell")
    ]
    assert derived
    for event in derived:
        assert event["fee_source"] == "derived_from_settlement"
        assert event["fees_fen"] > 0
        assert event["fee_detail"]["unallocated_fen"] == event["fees_fen"]
        assert event["fee_detail"]["commission"] is None
    # 同一文件里的现金事件不臆造费用
    cash = [
        event
        for event in holdings["events"]
        if event["source_file"] == "generic-demo-015-no-fee-columns.csv"
        and event["kind"] == "cash_event"
    ]
    assert all(event["fee_source"] == "not_reported" for event in cash)
    assert all(event["fees_fen"] == 0 for event in cash)


def test_buy_fees_enter_cost_and_sell_fees_reduce_proceeds(ledger_path):
    fifo = holdings_for(ledger_path, "fifo").holdings
    key = lambda event: (event["source_file"], event["source_line_no"])
    by_key = {key(event): event for event in fifo["events"]}
    buy = by_key[("htsec-demo-001.xlsx", 3)]
    assert buy["position"]["cost_after_fen"] == buy["amount_fen"] + buy["fees_fen"]
    sell = by_key[("htsec-demo-001.xlsx", 6)]
    expected = sell["amount_fen"] - sell["fees_fen"] - sell["realized"]["cost_fen"]
    assert sell["realized"]["realized_fen"] == expected
    assert sell["realized"]["proceeds_fen"] == sell["amount_fen"] - sell["fees_fen"]


# ---------------------------------------------------------------------------
# T+1 / 资金余额勾稽
# ---------------------------------------------------------------------------


def test_cash_balance_reconciles_on_clean_samples(ledger_path):
    holdings = holdings_for(ledger_path, "fifo").holdings
    assert holdings["invalid"] == []
    for account in holdings["accounts"]:
        assert account["cash_check"]["status"] == "ok"
        assert account["cash_check"]["mismatch"] == 0
        assert account["cash_check"]["checked"] > 0


def _rewrite_cash_balance(source_ledger, target_ledger, mutate):
    payload = json.loads(open(str(source_ledger), encoding="utf-8").read())
    mutate(payload["rows"])
    open(str(target_ledger), "w", encoding="utf-8").write(dumps_json(payload))
    return str(target_ledger)


def test_cash_balance_mismatch_lands_in_invalid(ledger_path, tmp_path):
    def mutate(rows):
        target = next(
            row
            for row in rows
            if row["source_file"] == "htsec-demo-001.xlsx" and row["source_line_no"] == 5
        )
        target["extra"]["cash_balance"] += 1

    broken = _rewrite_cash_balance(ledger_path, tmp_path / "broken.json", mutate)
    result = build_holdings(broken, "fifo", DATA_DIR)
    assert result.degraded is True
    codes = [item["code"] for item in result.holdings["invalid"]]
    assert "CASH_BALANCE_MISMATCH" in codes
    (problem,) = [
        item for item in result.holdings["invalid"] if item["code"] == "CASH_BALANCE_MISMATCH"
    ]
    assert problem["source_file"] == "htsec-demo-001.xlsx"
    assert problem["source_line_no"] == 5
    assert problem["difference_fen"] == 1
    assert "expected_cash_fen" in problem and "reported_cash_fen" in problem


def test_share_balance_mismatch_lands_in_invalid(ledger_path, tmp_path):
    def mutate(rows):
        target = next(
            row
            for row in rows
            if row["source_file"] == "htsec-demo-001.xlsx" and row["source_line_no"] == 4
        )
        target["extra"]["share_balance"] = "99999"

    broken = _rewrite_cash_balance(ledger_path, tmp_path / "shares.json", mutate)
    result = build_holdings(broken, "fifo", DATA_DIR)
    codes = [item["code"] for item in result.holdings["invalid"]]
    assert "SHARE_BALANCE_MISMATCH" in codes


def test_settlement_mismatch_is_detected():
    row = {
        "source_file": "x.csv",
        "source_sha256": "a" * 64,
        "source_line_no": 2,
        "account_id": "Y0000001",
        "symbol": "699001",
        "business": "证券买入",
        "trade_date": "2025-01-06",
        "trade_time": "09:31:02",
        "quantity": "100",
        "price": "10",
        "amount": 100000,
        "settlement_amount": -100000,
        "commission": 500,
        "stamp_tax": 0,
        "transfer_fee": 0,
        "other_fee": 0,
        "direction": "buy",
        "extra": {},
    }
    analysis = analyze_row(row, 0)
    codes = analysis.codes
    assert "SETTLEMENT_MISMATCH" in codes
    assert analysis.settle_fen == -100000


def test_insufficient_position_is_flagged(tmp_path):
    rows = [
        {
            "source_file": "x.csv",
            "source_sha256": "a" * 64,
            "source_line_no": 2,
            "account_id": "Y0000001",
            "symbol": "699001",
            "business": "证券买入",
            "trade_date": "2025-01-06",
            "trade_time": "09:31:02",
            "quantity": "100",
            "price": "10",
            "amount": 100000,
            "settlement_amount": -100500,
            "commission": 500,
            "stamp_tax": 0,
            "transfer_fee": 0,
            "other_fee": 0,
            "direction": "buy",
            "extra": {"cash_balance": 0, "share_balance": "100"},
        },
        {
            "source_file": "x.csv",
            "source_sha256": "a" * 64,
            "source_line_no": 3,
            "account_id": "Y0000001",
            "symbol": "699001",
            "business": "证券卖出",
            "trade_date": "2025-01-07",
            "trade_time": "09:31:02",
            "quantity": "300",
            "price": "11",
            "amount": 330000,
            "settlement_amount": 328900,
            "commission": 500,
            "stamp_tax": 600,
            "transfer_fee": 0,
            "other_fee": 0,
            "direction": "sell",
            "extra": {"cash_balance": 0, "share_balance": "0"},
        },
    ]
    ledger = tmp_path / "partial.json"
    ledger.write_text(
        dumps_json({"format": "ia.ledger", "format_version": 1, "rows": rows}),
        encoding="utf-8",
    )
    result = build_holdings(str(ledger), "fifo", DATA_DIR)
    codes = [item["code"] for item in result.holdings["invalid"]]
    assert "INSUFFICIENT_POSITION" in codes


# ---------------------------------------------------------------------------
# 快照与产物确定性
# ---------------------------------------------------------------------------


def test_snapshots_are_byte_identical_across_runs(ledger_path, tmp_path):
    for name in ("a", "b"):
        run_cost(
            "fifo",
            ledger_path=ledger_path,
            data_dir=DATA_DIR,
            out_dir=str(tmp_path / name),
        )
    for name in ("holdings.json", "snapshots.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name


def test_snapshot_dates_follow_trading_rows(ledger_path):
    holdings = holdings_for(ledger_path, "fifo").holdings
    dates = [snap["date"] for snap in holdings["daily_snapshots"]]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))
    ledger_dates = {
        row["trade_date"]
        for row in json.loads(open(str(ledger_path), encoding="utf-8").read())["rows"]
    }
    assert set(dates) == ledger_dates


def test_snapshot_has_no_zero_quantity_rows(ledger_path):
    holdings = holdings_for(ledger_path, "fifo").holdings
    for snap in holdings["daily_snapshots"]:
        for item in snap["positions"]:
            assert item["quantity"] != "0", snap["date"]
        assert snap["total_cost_fen"] == sum(item["cost_fen"] for item in snap["positions"])
        assert snap["position_count"] == len(snap["positions"])


# ---------------------------------------------------------------------------
# 未登记账户、行级归类、输入形态
# ---------------------------------------------------------------------------


def test_pending_account_is_its_own_group(tmp_path):
    result = import_files(
        [str(SAMPLES / "generic-demo-014-unregistered.csv")], DATA_DIR
    )
    target = tmp_path / "ledger.json"
    target.write_text(dumps_json(result.ledger), encoding="utf-8")
    outcome = build_holdings(str(target), "fifo", DATA_DIR)
    assert outcome.exit_code == EXIT_DEGRADED
    assert [account["account_id"] for account in outcome.holdings["accounts"]] == ["Y0000009"]
    assert outcome.holdings["accounts"][0]["account_status"] == "pending"
    assert any(item["code"] == "ACCOUNT_PENDING" for item in outcome.holdings["warnings"])


def test_row_kinds_cover_the_five_acquisition_and_cash_shapes():
    base = {
        "source_file": "x.csv",
        "source_sha256": "a" * 64,
        "source_line_no": 2,
        "account_id": "Y0000001",
        "symbol": "699001",
        "trade_date": "2025-01-06",
        "trade_time": "09:31:02",
        "price": None,
        "amount": None,
        "settlement_amount": None,
        "commission": 0,
        "stamp_tax": 0,
        "transfer_fee": 0,
        "other_fee": 0,
        "extra": {},
    }

    def analyze(**overrides):
        row = dict(base)
        row.update(overrides)
        return analyze_row(row, 0).kind

    assert analyze(business="证券买入", direction="buy", quantity="100") == "trade_buy"
    assert analyze(business="证券卖出", direction="sell", quantity="100") == "trade_sell"
    assert (
        analyze(business="配股", direction="other", quantity="100", amount=50000,
                settlement_amount=-50000)
        == "receipt_paid"
    )
    assert (
        analyze(business="红利再投", direction="other", quantity="100", amount=50000)
        == "receipt_valued"
    )
    assert analyze(business="送股入账", direction="other", quantity="100") == "receipt_free"
    assert (
        analyze(business="银证转入", direction="other", quantity="0",
                settlement_amount=100000)
        == "cash_event"
    )
    assert analyze(business="其他业务", direction="other", quantity="0") == "noop"


def test_negative_quantity_is_invalid_not_silently_used():
    row = {
        "source_file": "x.csv",
        "source_sha256": "a" * 64,
        "source_line_no": 2,
        "account_id": "Y0000001",
        "symbol": "699001",
        "business": "证券买入",
        "trade_date": "2025-01-06",
        "trade_time": "09:31:02",
        "quantity": "-100",
        "price": "10",
        "amount": 100000,
        "settlement_amount": -100500,
        "commission": 500,
        "stamp_tax": 0,
        "transfer_fee": 0,
        "other_fee": 0,
        "direction": "buy",
        "extra": {},
    }
    analysis = analyze_row(row, 0)
    assert "NEGATIVE_QUANTITY" in analysis.codes
    assert analysis.applicable is False


def test_missing_ledger_file_is_input_error(tmp_path):
    with pytest.raises(InputError) as excinfo:
        build_holdings(str(tmp_path / "nope.json"), "fifo", DATA_DIR)
    assert excinfo.value.code == "LEDGER_MISSING"


def test_wrong_ledger_format_is_input_error(tmp_path):
    target = tmp_path / "bad.json"
    target.write_text(dumps_json({"format": "ia.other", "rows": []}), encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        build_holdings(str(target), "fifo", DATA_DIR)
    assert excinfo.value.code == "LEDGER_FORMAT_MISMATCH"


def test_sort_key_is_date_time_line(ledger_path):
    payload = json.loads(open(str(ledger_path), encoding="utf-8").read())
    ordered = sorted_rows(payload["rows"])
    keys = [
        (row["trade_date"], row["trade_time"] or "", row["source_line_no"])
        for row in ordered
    ]
    assert keys == sorted(keys)


def test_build_pnl_agrees_with_holdings(ledger_path):
    result = build_holdings(ledger_path, "wavg", DATA_DIR)
    _, pnl = build_pnl(ledger_path, "wavg", DATA_DIR, include_fees=True)
    assert pnl["realized"]["net_fen"] == result.holdings["totals"]["realized_net_fen"]
    assert pnl["open_positions"]["cost_fen"] == result.holdings["totals"]["position_cost_fen"]
    assert pnl["fees"]["total_fen"] == result.holdings["totals"]["fees_total_fen"]
    per_symbol = sum(item["realized_net_fen"] for item in pnl["per_symbol"])
    assert per_symbol == pnl["realized"]["net_fen"]


# ---------------------------------------------------------------------------
# B2 基准：达标 + 可证伪探活
# ---------------------------------------------------------------------------


def test_b2_benchmark_passes_on_real_engine_output(ledger_path):
    from investment_attribution.ledger import compare_b2

    holdings_by_method = {
        method: build_holdings(ledger_path, method, DATA_DIR).holdings
        for method in ("fifo", "wavg")
    }
    report = compare_b2(holdings_by_method, TRUTH, DATA_DIR)
    assert report.ok, report.as_dict()
    assert report.checked == sum(
        len(TRUTH["methods"][method]["events"]) for method in ("fifo", "wavg")
    )
    assert report.tolerances["quantity_shares"] == "1"
    assert report.tolerances["amount_fen"] == "1"


def test_b2_benchmark_detects_injected_cost_defect(ledger_path):
    """可证伪探活：把某一批次的成本改错 1 分，B2 必须报未达标。"""
    from investment_attribution.ledger import compare_b2

    holdings_by_method = {
        method: build_holdings(ledger_path, method, DATA_DIR).holdings
        for method in ("fifo", "wavg")
    }
    fifo = holdings_by_method["fifo"]
    target = next(
        event
        for event in fifo["events"]
        if event["kind"] == "trade_buy" and event["position"]["quantity_after"] != "0"
    )
    target["position"]["cost_after_fen"] += 500
    report = compare_b2(holdings_by_method, TRUTH, DATA_DIR)
    assert report.ok is False
    assert any(item["check"] == "cost_after" for item in report.violations)


def test_b2_benchmark_detects_injected_quantity_defect(ledger_path):
    from investment_attribution.ledger import compare_b2

    holdings_by_method = {
        method: build_holdings(ledger_path, method, DATA_DIR).holdings
        for method in ("fifo", "wavg")
    }
    position = next(item for item in holdings_by_method["wavg"]["positions"] if item["is_open"])
    position["quantity"] = str(int(position["quantity"]) + 5)
    report = compare_b2(holdings_by_method, TRUTH, DATA_DIR)
    assert report.ok is False
    assert any(item["check"] == "position_quantity" for item in report.violations)


def test_b2_benchmark_detects_injected_snapshot_defect(ledger_path):
    from investment_attribution.ledger import compare_b2

    holdings_by_method = {
        method: build_holdings(ledger_path, method, DATA_DIR).holdings
        for method in ("fifo", "wavg")
    }
    snapshot = next(
        snap
        for snap in holdings_by_method["fifo"]["daily_snapshots"]
        if snap["positions"]
    )
    snapshot["positions"][0]["cost_fen"] += 1000
    report = compare_b2(holdings_by_method, TRUTH, DATA_DIR)
    assert report.ok is False
    assert any(item["check"] == "snapshot_cost" for item in report.violations)


def test_b2_benchmark_detects_missing_method(ledger_path):
    from investment_attribution.ledger import compare_b2

    wr = build_holdings(ledger_path, "wavg", DATA_DIR).holdings
    report = compare_b2({"fifo": wr}, TRUTH, DATA_DIR)
    assert report.ok is False
    assert any(item["check"] == "method_missing" for item in report.violations)
