"""确定性写盘：同输入双跑逐字节一致 + 契约字段齐全。"""

import json
import os

from context import DATA_DIR

from investment_attribution.serialize import dumps_json, write_json_atomic
from investment_attribution.manifest import load_manifest


def test_dumps_is_sorted_keyed_and_lf_terminated():
    text = dumps_json({"b": 1, "a": "中文"})
    assert text.endswith("\n")
    assert "\r" not in text
    assert '"a"' in text
    assert text.index('"a"') < text.index('"b"')
    # ensure_ascii=False：中文必须原样保留
    assert "中文" in text


def test_dumps_double_run_is_byte_identical():
    payload = {"z": [3, 2, 1], "a": {"m": "示例", "n": None}}
    assert dumps_json(payload) == dumps_json(payload)


def test_write_json_atomic_double_run_is_byte_identical(tmp_path):
    payload = {"format": "ia.test", "items": [{"id": 2}, {"id": 1}]}
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    write_json_atomic(first, payload)
    write_json_atomic(second, payload)
    assert first.read_bytes() == second.read_bytes()


def test_write_json_atomic_replaces_existing_file(tmp_path):
    target = tmp_path / "x.json"
    target.write_text("stale", encoding="utf-8")
    write_json_atomic(target, {"k": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"k": 1}


def test_manifest_declares_required_contracts():
    manifest = load_manifest(DATA_DIR)
    assert manifest["format"] == "ia.manifest"
    for key in (
        "position_quantity_tolerance_shares",
        "position_amount_tolerance_cny",
        "xirr_relative_tolerance",
        "split_conservation_tolerance_cny",
        "transfer_offset_tolerance_cny",
    ):
        assert manifest["bench_thresholds"][key] > 0
    surfaces = manifest["coverage"]["surfaces"]
    assert surfaces, "覆盖度登记不能为空"
    for item in surfaces:
        assert set(item) >= {"surface", "total", "sampled"}
        assert item["total"] == item["sampled"]
