"""M1 数据层守门：生成器 --check 两层 + 样例白名单 + 真值纪律。"""

import json
import subprocess
import sys

from context import DATA_DIR, REPO_ROOT

TOOLS_DIR = REPO_ROOT / "tools"
GEN_DATA = TOOLS_DIR / "gen_data.py"

sys.path.insert(0, str(TOOLS_DIR))
import gen_data  # noqa: E402

TRUTH = json.loads(
    (DATA_DIR / "truth" / "import_truth.json").read_text(encoding="utf-8")
)
COST_TRUTH = json.loads(
    (DATA_DIR / "truth" / "cost_truth.json").read_text(encoding="utf-8")
)
ACTIONS_TRUTH = json.loads(
    (DATA_DIR / "truth" / "actions_truth.json").read_text(encoding="utf-8")
)


def run_generator(*args):
    return subprocess.run(
        [sys.executable, str(GEN_DATA)] + list(args),
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )


def test_check_passes_on_frozen_artifacts():
    """两层守门：产物形态 + 重生成逐字节一致。"""
    proc = run_generator("--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[shape] ok" in proc.stderr
    assert "[bytes] ok" in proc.stderr


def test_check_detects_hand_edited_artifact(tmp_path):
    """冻结产物被手改必须变红——否则 ``--check`` 是假门。"""
    generator = gen_data
    generator.generate(tmp_path)
    target = tmp_path / "rules" / "aliases.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["hand_edit"] = True
    target.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(GEN_DATA), "--out-dir", str(tmp_path), "--check"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    assert proc.returncode == 1
    assert "MISMATCH" in proc.stderr
    assert "rules/aliases.json" in proc.stderr


def test_check_detects_missing_artifact(tmp_path):
    gen_data.generate(tmp_path)
    (tmp_path / "samples" / "generic-demo-013-account-c.csv").unlink()
    proc = subprocess.run(
        [sys.executable, str(GEN_DATA), "--out-dir", str(tmp_path), "--check"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    assert proc.returncode == 1
    assert "MISSING samples/generic-demo-013-account-c.csv" in proc.stderr


def test_generation_is_deterministic_across_runs(tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    gen_data.generate(first)
    gen_data.generate(second)
    assert gen_data.compare_artifacts(first, second) == []


def test_manifest_surfaces_are_fully_sampled():
    manifest = json.loads(
        (DATA_DIR / "rules" / "manifest.json").read_text(encoding="utf-8")
    )
    surfaces = manifest["coverage"]["surfaces"]
    assert len(surfaces) == 31
    for item in surfaces:
        assert item["total"] == item["sampled"] == 1, item["surface"]
        assert item["evidence"], item["surface"]
        for entry in item["evidence"]:
            assert (DATA_DIR / "samples" / entry["sample"]).is_file(), entry["sample"]


def test_all_import_surfaces_are_present():
    manifest = json.loads(
        (DATA_DIR / "rules" / "manifest.json").read_text(encoding="utf-8")
    )
    names = [item["surface"] for item in manifest["coverage"]["surfaces"]]
    import_surfaces = [name for name in names if name.startswith("import.")]
    assert len(import_surfaces) == 9, import_surfaces


def test_truth_has_no_observed_fields():
    """真值库存"输入 + 期望"；任何 ``observed_*`` 键即红。

    只看**键**不看正文：note 里写"不含 observed_*"是说明，不是缺陷。
    """
    def keys(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                yield key
                for item in keys(value):
                    yield item
        elif isinstance(obj, list):
            for value in obj:
                for item in keys(value):
                    yield item

    offenders = [key for key in keys(TRUTH) if key.startswith("observed")]
    assert offenders == []


def test_truth_covers_every_sample():
    listed = {entry["source_file"] for entry in TRUTH["files"]}
    on_disk = {
        path.name
        for path in (DATA_DIR / "samples").iterdir()
        if path.is_file() and path.name != "valuation-demo-001.csv"
    }
    assert listed == on_disk


def test_truth_hashes_match_sample_bytes():
    for entry in TRUTH["files"]:
        raw = (DATA_DIR / "samples" / entry["source_file"]).read_bytes()
        assert gen_data.sha256_hex(raw) == entry["sha256"], entry["source_file"]
        assert len(raw) == entry["size_bytes"], entry["source_file"]


def test_samples_use_whitelisted_codes_and_names():
    for entry in TRUTH["files"]:
        for row in entry["expected_rows"]:
            assert 699000 <= int(row["symbol"]) <= 699099, row["symbol"]
            assert row["name"] is None or row["name"].startswith("示例"), row["name"]


def test_samples_use_whitelisted_accounts():
    import re

    account_re = re.compile(r"^Y000000\d$")
    shareholder_re = re.compile(r"^A00000000\d$")
    for entry in TRUTH["files"]:
        for row in entry["expected_rows"]:
            if row["account_id"]:
                assert account_re.match(row["account_id"]), row["account_id"]
            if row["shareholder_account"]:
                assert shareholder_re.match(row["shareholder_account"]), row[
                    "shareholder_account"
                ]


def test_sample_privacy_scan_has_no_stray_long_digits():
    """白名单账号之外不得出现 8 位以上纯数字串。"""
    for entry in TRUTH["files"]:
        text = gen_data._decode_sample(DATA_DIR, entry["source_file"], entry["encoding"])
        if text is None:
            continue
        assert gen_data.scan_privacy(text, entry["source_file"]) == [], entry["source_file"]


def test_privacy_scan_positive_control():
    """阳性对照：真实账号形态必须被抓到，否则扫描是假门。"""
    issues = gen_data.scan_privacy("资金账号 88888888 股东账号 A999999999", "synthetic")
    assert issues


def test_truth_lists_expected_row_errors_for_defect_samples():
    by_name = {entry["source_file"]: entry for entry in TRUTH["files"]}
    assert by_name["generic-demo-005-misaligned.csv"]["expected_error_count"] == 2
    assert by_name["generic-demo-003-formula.csv"]["expected_error_count"] == 1
    for name in ("generic-demo-004-missing-column.csv", "generic-demo-006-empty.csv",
                 "generic-demo-007-header-only.csv", "generic-demo-010-undecodable.csv"):
        assert by_name[name]["status"] == "file_error", name


def test_generator_forbidden_imports():
    """生成器不得依赖 stdlib random / 网络（确定性纪律）。"""
    from investment_attribution.netguard import scan_network_imports

    assert scan_network_imports(str(TOOLS_DIR)) == []
    source = GEN_DATA.read_text(encoding="utf-8")
    assert "import random" not in source
    assert "from random" not in source
    assert "datetime.now" not in source
    assert "time.time" not in source


# ---------------------------------------------------------------------------
# M2 成本真值（B2）
# ---------------------------------------------------------------------------


def test_cost_truth_is_generated_not_imported_from_engine():
    """真值纪律：生成器不得 import 引擎，否则"独立复算"变成自己比自己。"""
    source = GEN_DATA.read_text(encoding="utf-8")
    assert "investment_attribution" not in source
    assert COST_TRUTH["format"] == "ia.truth.cost"
    assert sorted(COST_TRUTH["methods"]) == ["fifo", "wavg"]


def test_cost_truth_has_no_observed_fields():
    def keys(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                yield key
                for item in keys(value):
                    yield item
        elif isinstance(obj, list):
            for value in obj:
                for item in keys(value):
                    yield item

    offenders = [key for key in keys(COST_TRUTH) if key.startswith("observed")]
    assert offenders == []


def test_cost_truth_scenario_files_exist_and_are_registered():
    files = COST_TRUTH["scenario"]["files"]
    assert files
    known = {entry["source_file"] for entry in TRUTH["files"]}
    for name in files:
        assert (DATA_DIR / "samples" / name).is_file(), name
        assert name in known, name


def test_cost_truth_row_count_matches_events():
    rows = COST_TRUTH["rows"]
    assert COST_TRUTH["scenario"]["row_count"] == len(rows)
    for method, data in COST_TRUTH["methods"].items():
        assert len(data["events"]) == len(rows), method
        assert len({tuple(event["key"]) for event in data["events"]}) == len(rows), method


def test_cost_truth_internal_arithmetic_holds():
    """真值自身的守恒：未平仓成本之和、已实现之和、费用来源合法性。"""
    for method, data in COST_TRUTH["methods"].items():
        open_cost = sum(item["cost_fen"] for item in data["positions"] if item["is_open"])
        assert open_cost == data["totals"]["position_cost_fen"], method
        realized = sum(item["realized_net_fen"] for item in data["accounts"])
        assert realized == data["totals"]["realized_net_fen"], method
        for event in data["events"]:
            assert event["position_after_cost_fen"] >= 0, (method, event["key"])
            if event["kind"] == "trade_sell":
                assert event["realized_fen"] is not None, (method, event["key"])
    allowed = {"as_reported", "derived_from_settlement", "partially_reported", "not_reported"}
    for row in COST_TRUTH["rows"]:
        assert row["fee_source"] in allowed, row["key"]


def test_cost_truth_has_a_genuine_divergence_case():
    """双口径必须真的分叉；否则"差异可解释到笔"无从断言。"""
    divergences = COST_TRUTH["divergences"]
    assert divergences
    for item in divergences:
        assert item["fifo_realized_fen"] != item["wavg_realized_fen"]
        assert item["difference_fen"] == (
            item["wavg_realized_fen"] - item["fifo_realized_fen"]
        )
        assert item["symbol"].startswith("699")


def test_cost_truth_uses_both_itemized_and_derived_fees():
    sources = {row["fee_source"] for row in COST_TRUTH["rows"]}
    assert "as_reported" in sources
    assert "derived_from_settlement" in sources


def test_every_cost_surface_points_at_real_rows():
    """每个成本形态必须指向真实存在的行；否则"逐笔期望"只是口头承诺。"""
    keys = {tuple(row["key"]) for row in COST_TRUTH["rows"]}
    evidence = COST_TRUTH["surface_evidence"]
    manifest = json.loads(
        (DATA_DIR / "rules" / "manifest.json").read_text(encoding="utf-8")
    )
    cost_surfaces = [
        item["surface"]
        for item in manifest["coverage"]["surfaces"]
        if item["surface"].startswith("cost.")
    ]
    assert set(cost_surfaces) == set(evidence)
    for surface in cost_surfaces:
        assert evidence[surface], surface
        for key in evidence[surface]:
            assert tuple(key) in keys, (surface, key)


# ---------------------------------------------------------------------------
# M3 公司行为真值（B3）
# ---------------------------------------------------------------------------


def test_actions_truth_is_generated_not_imported_from_engine():
    """真值纪律：生成器不得 import 引擎，否则"独立复算"变成自己比自己。"""
    source = GEN_DATA.read_text(encoding="utf-8")
    assert "investment_attribution" not in source
    assert ACTIONS_TRUTH["format"] == "ia.truth.actions"
    assert sorted(ACTIONS_TRUTH["methods"]) == ["fifo", "wavg"]


def test_actions_truth_has_no_observed_fields():
    def keys(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                yield key
                for item in keys(value):
                    yield item
        elif isinstance(obj, list):
            for value in obj:
                for item in keys(value):
                    yield item

    offenders = [key for key in keys(ACTIONS_TRUTH) if key.startswith("observed")]
    assert offenders == []


def test_actions_truth_scenario_matches_cost_scenario():
    """两份真值必须同范围：否则"同一次导入"下的结论无法互相印证。"""
    assert ACTIONS_TRUTH["scenario"]["files"] == COST_TRUTH["scenario"]["files"]
    assert ACTIONS_TRUTH["scenario"]["row_count"] == COST_TRUTH["scenario"]["row_count"]


def test_actions_truth_covers_all_five_event_types():
    expected = {
        "dividend",
        "stock_dividend",
        "rights_issue",
        "split",
        "reinvested_dividend",
    }
    for method, data in ACTIONS_TRUTH["methods"].items():
        counts = {item["event_type"]: item["count"] for item in data["by_event_type"]}
        assert expected <= set(counts), (method, counts)
        assert counts["dividend"] >= 2, (method, counts)


def test_actions_truth_always_has_a_pending_row():
    """"无法识别必须进待确认"在真值里必须可见，否则该纪律无法断言。"""
    for method, data in ACTIONS_TRUTH["methods"].items():
        assert data["pending"], method
        assert any(item["reason"] == "NO_RULE_MATCH" for item in data["pending"]), method


def test_actions_truth_adjustments_are_reversible():
    """before + 行形态增量 == after；零成本份额总成本守恒。"""
    for method, data in ACTIONS_TRUTH["methods"].items():
        for event in data["events"]:
            before, after = event["before"], event["after"]
            if event["kind"] == "receipt_free":
                assert after["cost_fen"] == before["cost_fen"], (method, event["key"])
                assert float(after["quantity"]) > float(before["quantity"])
            elif event["kind"] == "cash_event":
                assert before == after, (method, event["key"])
            elif event["kind"] in ("receipt_paid", "receipt_valued"):
                assert after["cost_fen"] > before["cost_fen"], (method, event["key"])


def test_every_corporate_action_surface_points_at_real_rows():
    keys = set()
    pending_keys = set()
    for data in ACTIONS_TRUTH["methods"].values():
        keys |= {tuple(event["key"]) for event in data["events"]}
        pending_keys |= {
            (item["source_file"], item["source_line_no"]) for item in data["pending"]
        }
    manifest = json.loads(
        (DATA_DIR / "rules" / "manifest.json").read_text(encoding="utf-8")
    )
    surfaces = [
        item["surface"]
        for item in manifest["coverage"]["surfaces"]
        if item["surface"].startswith("corporate_action.")
    ]
    evidence = ACTIONS_TRUTH["surface_evidence"]
    assert set(surfaces) == set(evidence)
    for surface in surfaces:
        assert evidence[surface], surface
        known = pending_keys if surface.endswith("unrecognized_pending") else keys
        for key in evidence[surface]:
            assert tuple(key) in known, (surface, key)


def test_rules_pack_declares_non_action_patterns():
    rules = json.loads(
        (DATA_DIR / "rules" / "corporate_actions.json").read_text(encoding="utf-8")
    )
    assert rules["non_action_patterns"]
    assert "内部划转" in rules["non_action_patterns"]
    for rule in rules["rules"]:
        assert rule["expected_kinds"]
        assert rule["cash_effect"] in ("inflow", "outflow", "none")
