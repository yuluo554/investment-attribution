"""M5 本地报告：同源渲染、零外链、报告阶段打码、逐数字下钻、双跑一致，
以及渲染层零判定 AST 门禁（三条 + 正/负对照）。
"""

import hashlib
import json
import re

import pytest

from context import DATA_DIR, REPO_ROOT, SRC_ROOT

from investment_attribution import cli
from investment_attribution.attribution import build_attribution
from investment_attribution.errors import InputError, UsageError
from investment_attribution.exit_codes import EXIT_INPUT, EXIT_OK
from investment_attribution.ingest import import_files
from investment_attribution.merge import build_merge
from investment_attribution.pipeline.report_cmd import run_report
from investment_attribution.report import (
    REPORT_DATA_FILE,
    REPORT_HTML_FILE,
    RENDER_DECISION_RULES,
    build_report_data,
    read_merged_file,
    render_html,
    scan_external_references,
    scan_renderer,
    scan_source,
)
from investment_attribution.serialize import dumps_json

SAMPLES = DATA_DIR / "samples"
COST_TRUTH = json.loads((DATA_DIR / "truth" / "cost_truth.json").read_text(encoding="utf-8"))
ATTRIBUTION_TRUTH = json.loads(
    (DATA_DIR / "truth" / "attribution_truth.json").read_text(encoding="utf-8")
)
SCENARIO_FILES = COST_TRUTH["scenario"]["files"]
VALUATION = str(SAMPLES / ATTRIBUTION_TRUTH["scenario"]["valuation"])

#: 完整账号形态（资金账号 Y+7 位 / 股东账号 A+9 位）。
ACCOUNT_PATTERN = re.compile(r"[YA]\d{7,9}")


@pytest.fixture(scope="module")
def merged_path(tmp_path_factory):
    work = tmp_path_factory.mktemp("m5-report")
    import_files(
        [str(SAMPLES / name) for name in SCENARIO_FILES], DATA_DIR, out_dir=str(work)
    )
    ledger = str(work / "ledger.json")
    attribution = work / "attribution.json"
    build_attribution(
        ledger, "fifo", DATA_DIR, valuation_path=VALUATION, out_path=str(attribution)
    )
    merged = work / "merged.json"
    build_merge(
        ledger, "fifo", DATA_DIR, attribution_path=str(attribution), out_path=str(merged)
    )
    return str(merged)


@pytest.fixture(scope="module")
def report(merged_path, tmp_path_factory):
    out = tmp_path_factory.mktemp("m5-report-out")
    return run_report(
        merged_path=merged_path,
        out=str(out / REPORT_HTML_FILE),
        json_out=str(out / REPORT_DATA_FILE),
    )


# ---------------------------------------------------------------------------
# 同源：HTML 与 JSON 出自同一次构建
# ---------------------------------------------------------------------------


def test_html_is_rendered_from_the_same_model(report):
    assert report.html == render_html(report.data)
    assert report.data["format"] == "ia.report_data"
    assert report.data["counts"]["section_count"] == len(report.data["sections"])


def test_report_data_serializes_to_the_written_bytes(report):
    raw = open(report.paths["report_data"], "rb").read()
    assert raw.decode("utf-8") == dumps_json(report.data)


def test_report_and_merge_paths_are_reported(report):
    assert report.paths["report_html"].endswith(REPORT_HTML_FILE)
    assert report.paths["report_data"].endswith(REPORT_DATA_FILE)


def test_report_is_byte_identical_across_runs(merged_path, tmp_path):
    """报告双跑 sha256 相同：不含时钟、不含随机、不含目录序。"""
    first = run_report(merged_path=merged_path, out_dir=str(tmp_path / "a"))
    second = run_report(merged_path=merged_path, out_dir=str(tmp_path / "b"))
    first_html = (tmp_path / "a" / REPORT_HTML_FILE).read_bytes()
    second_html = (tmp_path / "b" / REPORT_HTML_FILE).read_bytes()
    assert hashlib.sha256(first_html).hexdigest() == hashlib.sha256(second_html).hexdigest()
    assert first_html == second_html
    assert first.html == second.html
    assert dumps_json(first.data) == dumps_json(second.data)


def test_missing_merged_is_input_error(tmp_path):
    with pytest.raises(InputError) as excinfo:
        run_report(merged_path=str(tmp_path / "nope.json"))
    assert excinfo.value.code == "MERGED_MISSING"


def test_non_merge_input_is_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "ia.attribution"}), encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        run_report(merged_path=str(bad))
    assert excinfo.value.code == "MERGED_FORMAT_MISMATCH"


def test_report_requires_merged_argument():
    with pytest.raises(UsageError) as excinfo:
        run_report()
    assert excinfo.value.code == "MERGED_REQUIRED"


# ---------------------------------------------------------------------------
# 零外链：静态扫描 + 出口扫描（含阳性对照）
# ---------------------------------------------------------------------------


def test_report_contains_no_external_reference(report):
    for name, text in (
        ("html", report.html),
        ("json", dumps_json(report.data)),
    ):
        assert scan_external_references(text) == [], (name, scan_external_references(text)[:2])


def test_external_scan_detects_every_marker():
    """阳性对照：三种形态（http / https / 协议相对）都必须被抓到。"""
    for marker in ("http://a.example/x", "https://a.example/x", '<img src="//a.example/x">'):
        found = scan_external_references("前文 {} 后文".format(marker))
        assert found, marker
        assert found[0]["marker"] in marker


def test_external_scan_negative_control_passes_clean_text():
    clean = "<p>已实现 1550.00 元 / 未实现 1616.21 元 / 合计 7984.38 元</p>"
    assert scan_external_references(clean) == []


def test_engine_refuses_to_write_a_report_with_external_reference(monkeypatch, merged_path, tmp_path):
    """注入外链必须被出口扫描拦下，且不落半份产物。"""
    import investment_attribution.report.engine as engine

    monkeypatch.setattr(engine, "render_html", lambda data: '<a href="http://x.example/y">x</a>')
    with pytest.raises(Exception) as excinfo:
        engine.build_report(
            merged_path,
            out=str(tmp_path / REPORT_HTML_FILE),
            json_out=str(tmp_path / REPORT_DATA_FILE),
        )
    assert getattr(excinfo.value, "code", "") == "REPORT_EXTERNAL_REFERENCE"
    assert not (tmp_path / REPORT_HTML_FILE).exists()
    assert not (tmp_path / REPORT_DATA_FILE).exists()


# ---------------------------------------------------------------------------
# 打码发生在生成阶段
# ---------------------------------------------------------------------------


def test_no_full_account_in_report_outputs(report):
    for name, text in (
        ("html", report.html),
        ("json", dumps_json(report.data)),
    ):
        assert ACCOUNT_PATTERN.findall(text) == [], name


def test_masked_accounts_are_visible_and_counted(report):
    masking = report.data["masking"]
    assert masking["applied"] is True
    assert masking["masked_token_count"] >= 4
    assert all("*" in token for token in masking["masked_samples"])
    # 打码后的形态不得还能还原出完整账号
    joined = "".join(masking["masked_samples"])
    assert ACCOUNT_PATTERN.findall(joined) == []


def test_mask_rule_keeps_head_and_tail():
    from investment_attribution.report.model import mask_account

    assert mask_account("Y0000001") == "Y0****01"
    assert mask_account("A000000001") == "A0******01"
    assert "*" in mask_account("Y0000009")
    assert mask_account(None) is None


def test_report_still_shows_account_aliases(report):
    """打码不等于把账户藏起来：别名仍在，账仍可读。"""
    text = report.html
    assert "示例账户一" in text
    assert "Y0****01" in text


def test_inputs_keep_full_accounts_out_of_the_report_only(report, merged_path):
    """上游审计产物保留完整账号（它是审计链），报告两份产物不保留。"""
    merged, _sha = read_merged_file(merged_path)
    assert merged["accounts"][0]["account_id"] == "Y0000001"
    assert ACCOUNT_PATTERN.findall(dumps_json(report.data)) == []


# ---------------------------------------------------------------------------
# 逐数字下钻：每个汇总数与每行表格都挂审计三件套
# ---------------------------------------------------------------------------


def _triples(items):
    return [
        item
        for item in items or []
        if item.get("source_file") and item.get("source_sha256") and item.get("source_line_no")
    ]


def test_every_summary_figure_carries_audit_triple(report):
    figures = [
        figure
        for section in report.data["sections"]
        for figure in section.get("figures") or []
    ]
    assert figures, "报告必须有汇总数字"
    for figure in figures:
        assert _triples(figure["evidence"]), figure["id"]


def test_every_table_row_carries_audit_triple(report):
    rows = [
        row
        for section in report.data["sections"]
        for row in section.get("rows") or []
    ]
    assert rows
    for row in rows:
        assert _triples(row["evidence"]), row["cells"]


def test_drilldown_triples_point_at_real_source_lines(report, merged_path):
    """锚点必须指向真实存在的行：文件存在、行号在文件范围内、sha 对得上。"""
    merged, _sha = read_merged_file(merged_path)
    provenance = merged["provenance"]
    assert provenance
    for key, item in provenance.items():
        assert len(item["source_sha256"]) == 64
        assert isinstance(item["source_line_no"], int)
        sample = SAMPLES / item["source_file"]
        if sample.is_file():
            body = sample.read_bytes()
            assert (
                hashlib.sha256(body).hexdigest() == item["source_sha256"]
            ), item["source_file"]

    # 报告里出现的锚点都能在上游 provenance 里找到同一条
    known = {
        (item["source_file"], item["source_line_no"], item["source_sha256"])
        for item in provenance.values()
    }
    seen = 0
    for section in report.data["sections"]:
        pools = [figure["evidence"] for figure in section.get("figures") or []]
        pools += [row["evidence"] for row in section.get("rows") or []]
        for pool in pools:
            for anchor in _triples(pool):
                key = (anchor["source_file"], anchor["source_line_no"], anchor["source_sha256"])
                assert key in known, key
                seen += 1
    assert seen > 20


def test_transfer_drilldown_reaches_both_legs(report):
    transfers = next(
        section for section in report.data["sections"] if section["id"] == "transfers"
    )
    anchors = {
        (anchor["source_file"], anchor["source_line_no"])
        for anchor in _triples(transfers["rows"][0]["evidence"])
    }
    assert ("htsec-demo-001.xlsx", 20) in anchors


# ---------------------------------------------------------------------------
# 内容齐备：口径可见、待处理可见、零上传证据在
# ---------------------------------------------------------------------------


def test_report_header_restates_the_basis(report):
    badges = {item["label"]: item["value"] for item in report.data["header"]["badges"]}
    assert badges["成本口径"].startswith("先进先出")
    assert badges["TWR 估值点"]
    assert badges["估值日"] == "2025-12-31"
    assert "流水行数" in badges


def test_report_sections_cover_the_do_list(report):
    ids = [section["id"] for section in report.data["sections"]]
    for expected in (
        "overview",
        "accounts",
        "holdings",
        "transfers",
        "returns",
        "fees",
        "by_symbol",
        "by_industry",
        "checks",
        "issues",
        "zero_upload",
        "conventions",
    ):
        assert expected in ids, expected


def test_report_shows_pending_rows_counted(report):
    """待处理段必须把待确认现金行算进去（样例里恒有一条）。"""
    issues = next(section for section in report.data["sections"] if section["id"] == "issues")
    assert "提示 1 条" in issues["title"]
    assert any("UNCLASSIFIED_CASH_ROW" in item["title"] for item in issues["items"])


def test_report_records_zero_upload_probe(report):
    probe = report.data["zero_upload"]["render_probe"]
    assert probe["armed"] is True
    assert probe["blocked_attempts"] == 0
    upstream = report.data["zero_upload"]["runtime_probe"]
    assert upstream["blocked_attempts"] == 0
    section = next(
        section for section in report.data["sections"] if section["id"] == "zero_upload"
    )
    assert len(section["items"]) >= 3


def test_rendering_adds_no_network_attempt():
    """渲染是纯字符串构建：武装状态下渲染一遍，探针记账不得增加。"""
    from investment_attribution.netguard import armed_runtime_probe

    model = {
        "meta": {"title": "t", "subtitle": "s"},
        "header": {"badges": [], "notices": [], "disclaimer": "d"},
        "sections": [],
        "counts": {},
    }
    with armed_runtime_probe() as probe:
        render_html(model)
    assert probe.attempts == []


# ---------------------------------------------------------------------------
# 渲染层零判定 AST 门禁：三条 + 正/负对照
# ---------------------------------------------------------------------------


def test_renderer_passes_the_zero_decision_gate():
    assert scan_renderer(SRC_ROOT / "investment_attribution") == []


def test_gate_declares_three_rules():
    assert [name for name, _ in RENDER_DECISION_RULES] == [
        "R1_cost_method",
        "R2_event_type",
        "R3_return_basis",
    ]


def test_gate_catches_cost_method_decision():
    source = 'def render(data):\n    if data["cost_method"] == "fifo":\n        return "x"\n    return "y"\n'
    rules = {item["rule"] for item in scan_source(source)}
    assert "R1_cost_method" in rules


def test_gate_catches_event_type_decision():
    source = 'def render(row):\n    if row["event_type"] == "dividend":\n        return "d"\n'
    rules = {item["rule"] for item in scan_source(source)}
    assert "R2_event_type" in rules


def test_gate_catches_return_basis_decision():
    source = "def render(pnl):\n    return pnl['realized_fen'] + pnl['unrealized_fen']\n"
    rules = {item["rule"] for item in scan_source(source)}
    assert "R3_return_basis" in rules


def test_gate_catches_attribute_access():
    source = "def render(row):\n    return row.event_type\n"
    assert scan_source(source)


def test_gate_negative_control_allows_dumb_templates():
    """阴性对照：纯模板（迭代 + 拼接 + 转义 + 字符串方法）不得报红。"""
    source = (
        "from html import escape\n"
        "def render(data):\n"
        "    parts = []\n"
        "    for section in data.get('sections') or []:\n"
        "        parts.append('<h2>{}</h2>'.format(escape(section.get('title'))))\n"
        "    return ','.join(parts).strip()\n"
    )
    assert scan_source(source) == []


def test_gate_positive_control_is_a_real_gate():
    """阳性对照必须是"会红"的：把判定片段贴进真渲染层，门禁必须失败。"""
    source = (SRC_ROOT / "investment_attribution" / "report" / "render.py").read_text(
        encoding="utf-8"
    )
    injected = source + "\n\ndef _sneak(data):\n    return data['xirr']\n"
    assert scan_source(injected)


# ---------------------------------------------------------------------------
# CLI 契约
# ---------------------------------------------------------------------------


def test_cli_report_writes_both_artifacts(merged_path, tmp_path, capsys):
    code = cli.main(
        [
            "report",
            "--merged",
            merged_path,
            "--out-dir",
            str(tmp_path / "rep"),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "报告完成" in captured.err
    assert (tmp_path / "rep" / REPORT_HTML_FILE).is_file()
    assert (tmp_path / "rep" / REPORT_DATA_FILE).is_file()


def test_cli_report_with_explicit_paths(merged_path, tmp_path, capsys):
    html = tmp_path / "out" / "my.html"
    payload = tmp_path / "out" / "my.json"
    code = cli.main(
        [
            "report",
            "--merged",
            merged_path,
            "--out",
            str(html),
            "--json-out",
            str(payload),
        ]
    )
    assert code == EXIT_OK
    assert html.is_file() and payload.is_file()


def test_build_report_data_masks_directly(merged_path):
    """打码在**数据模型构建**这一步就发生，不是渲染时补一下。"""
    merged, sha = read_merged_file(merged_path)
    data = build_report_data(merged, merged_path, sha)
    assert ACCOUNT_PATTERN.findall(dumps_json(data)) == []
