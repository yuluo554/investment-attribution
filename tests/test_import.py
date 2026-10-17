"""M1 导入层：两类真实格式、异常行回放、去重、账户映射、编码三分支。

一半是对着**真值台账**的逐字段比对（真值由独立生成器产出，不是引擎自己写
一份观测再比自己），另一半是对着设计纪律的负向断言（该红的地方必须红）。
"""

import json

import pytest

from context import DATA_DIR, REPO_ROOT

from investment_attribution.errors import IAError, InputError
from investment_attribution.exit_codes import EXIT_DEGRADED, EXIT_OK
from investment_attribution.ingest import import_files
from investment_attribution.ingest.encoding import decode_bytes
from investment_attribution.ingest.records import RowError
from investment_attribution.ingest.tables import is_xlsx, read_table
from investment_attribution.ingest.verify import compare as compare_b1

SAMPLES = DATA_DIR / "samples"
TRUTH = json.loads(
    (DATA_DIR / "truth" / "import_truth.json").read_text(encoding="utf-8")
)
TRUTH_BY_FILE = {entry["source_file"]: entry for entry in TRUTH["files"]}


def sample(name):
    return str(SAMPLES / name)


def import_one(name):
    return import_files([sample(name)], DATA_DIR)


# ---------------------------------------------------------------------------
# 两类真实格式 100% 入库 + 逐行列值可回放
# ---------------------------------------------------------------------------


def test_htsec_xlsx_imports_every_row_without_error():
    result = import_one("htsec-demo-001.xlsx")
    summary = result.ledger["summary"]
    assert summary["rows_accepted"] == 22
    assert summary["rows_rejected"] == 0
    assert result.exit_code == EXIT_OK


def test_huaxi_tab_gb18030_imports_every_row_without_error():
    result = import_one("huaxi-demo-001.xls")
    summary = result.ledger["summary"]
    assert summary["rows_accepted"] == 17
    assert summary["rows_rejected"] == 0
    assert result.exit_code == EXIT_OK


@pytest.mark.parametrize("name", sorted(TRUTH_BY_FILE))
def test_every_sample_matches_truth_ledger(name):
    """逐行逐字段比对真值；``source_line_no`` 用源文件行号而非数组下标。

    判据实现已抽到 ``ingest/verify.py``：本测试与 ``bench --only B1`` 共用
    同一份比对（两个实现各写一套，迟早会分叉成"测试过、基准不过"）。
    """
    try:
        result = import_one(name)
    except IAError as exc:
        outcomes = {name: {"status": "file_error", "file_error_code": exc.code}}
    else:
        fingerprint = result.fingerprints["files"][0]
        outcomes = {
            name: {
                "status": "ok",
                "rows": result.ledger["rows"],
                "errors": result.errors["errors"],
                "fingerprint": {
                    "sha256": fingerprint["sha256"],
                    "unknown_columns": fingerprint["unknown_columns"],
                },
            }
        }
    report = compare_b1(
        outcomes,
        {"format": TRUTH["format"], "files": [TRUTH_BY_FILE[name]]},
        DATA_DIR,
    )
    assert report.ok is True, report.as_dict()["violations"]


def test_huaxi_file_is_tab_delimited_gb18030_and_shell_wrapped():
    """华西样例的三处"骗人"必须同时成立，否则样例失去适配素材价值。"""
    raw = (SAMPLES / "huaxi-demo-001.xls").read_bytes()
    assert not is_xlsx(raw)
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    text = raw.decode("gb18030")
    assert text.startswith('="买卖日期"\t="成交时间"')
    table = read_table(raw, "huaxi-demo-001.xls")
    assert table.delimiter == "tab"
    assert table.encoding == "gb18030"


def test_xlsx_magic_wins_over_extension():
    """把 xlsx 改名成 .csv：解析仍走 xlsx，因为判别看字节形态。"""
    raw = (SAMPLES / "htsec-demo-001.xlsx").read_bytes()
    table = read_table(raw, "misleading-extension.csv")
    assert table.kind == "xlsx"
    assert len(table.header) == 19


def test_tab_text_with_csv_extension_still_parses():
    raw = (SAMPLES / "huaxi-demo-001.xls").read_bytes()
    table = read_table(raw, "misleading-extension.csv")
    assert table.kind == "delimited"
    assert table.delimiter == "tab"
    assert len(table.header) == 20


def test_ledger_rows_carry_audit_triple():
    """K5：审计三件套必须逐行齐全，且行号指向源文件。"""
    result = import_one("htsec-demo-001.xlsx")
    for row in result.ledger["rows"]:
        assert row["source_file"] == "htsec-demo-001.xlsx"
        assert len(row["source_sha256"]) == 64
        assert isinstance(row["source_line_no"], int)
        assert row["source_line_no"] >= 2


def test_ledger_is_sorted_by_date_time_line():
    result = import_files([sample("htsec-demo-001.xlsx"), sample("huaxi-demo-001.xls")], DATA_DIR)
    keys = [
        (row["trade_date"], row["trade_time"] or "", row["source_file"], row["source_line_no"])
        for row in result.ledger["rows"]
    ]
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# 重复导入 0 重复流水；改名 / 改扩展名识别为同一件
# ---------------------------------------------------------------------------


def test_reimport_same_file_has_zero_duplicate_rows():
    result = import_files([sample("htsec-demo-001.xlsx"), sample("htsec-demo-001.xlsx")], DATA_DIR)
    assert len(result.ledger["rows"]) == 22
    assert result.ledger["summary"]["files_duplicate"] == 1


def test_renamed_and_reextensioned_copies_are_deduped(tmp_path):
    original = (SAMPLES / "htsec-demo-001.xlsx").read_bytes()
    renamed = tmp_path / "renamed-copy.csv"
    other_ext = tmp_path / "another-name.xls"
    renamed.write_bytes(original)
    other_ext.write_bytes(original)

    result = import_files(
        [sample("htsec-demo-001.xlsx"), str(renamed), str(other_ext)], DATA_DIR
    )
    assert len(result.ledger["rows"]) == 22
    assert result.ledger["summary"]["files_duplicate"] == 2
    duplicates = result.fingerprints["duplicates_skipped"]
    assert {item["source_file"] for item in duplicates} == {"renamed-copy.csv", "another-name.xls"}
    for item in duplicates:
        assert item["duplicate_of"] == "htsec-demo-001.xlsx"


def test_dedup_is_by_content_not_by_name(tmp_path):
    """同名不同内容必须都入库——反过来用文件名去重会把两份真账合并成一份。"""
    first = tmp_path / "same-name.csv"
    first.write_bytes((SAMPLES / "generic-demo-001.csv").read_bytes())
    second = tmp_path / "sub"
    second.mkdir()
    twin = second / "same-name.csv"
    twin.write_bytes((SAMPLES / "generic-demo-011.tsv").read_bytes())

    result = import_files([str(first), str(twin)], DATA_DIR)
    assert result.ledger["summary"]["files_duplicate"] == 0
    assert len(result.ledger["rows"]) == 6


# ---------------------------------------------------------------------------
# 异常行逐条可回放
# ---------------------------------------------------------------------------


def test_misaligned_rows_are_replayable():
    result = import_one("generic-demo-005-misaligned.csv")
    assert result.exit_code == EXIT_DEGRADED
    errors = result.errors["errors"]
    assert len(errors) == 2
    for item in errors:
        assert item["reason_code"] == "COLUMN_COUNT_MISMATCH"
        assert item["source_line_no"] in (4, 5)
        assert item["raw_line"]
        assert item["source_file"] == "generic-demo-005-misaligned.csv"
        assert len(item["source_sha256"]) == 64


def test_invalid_cell_shell_is_rejected_not_repaired():
    result = import_one("generic-demo-003-formula.csv")
    assert result.exit_code == EXIT_DEGRADED
    assert len(result.ledger["rows"]) == 2
    (error,) = result.errors["errors"]
    assert error["reason_code"] == "INVALID_CELL_SHELL"
    assert "示例" in error["raw_line"]


def test_bad_row_does_not_abort_the_whole_file():
    result = import_one("generic-demo-003-formula.csv")
    assert result.ledger["summary"]["files_imported"] == 1
    assert result.ledger["summary"]["rows_accepted"] == 2
    assert result.ledger["summary"]["rows_rejected"] == 1


# ---------------------------------------------------------------------------
# 编码探测三分支
# ---------------------------------------------------------------------------


def test_encoding_branch_utf8():
    text, encoding = decode_bytes("成交日期,证券代码\n".encode("utf-8"))
    assert encoding == "utf-8-sig"
    assert "成交日期" in text


def test_encoding_branch_utf8_bom():
    text, encoding = decode_bytes(b"\xef\xbb\xbf" + "成交日期".encode("utf-8"))
    assert encoding == "utf-8-sig"
    assert text.startswith("成交日期")


def test_encoding_branch_gb18030():
    text, encoding = decode_bytes("成交日期,证券代码\n".encode("gb18030"))
    assert encoding == "gb18030"
    assert "成交日期" in text


def test_encoding_both_fail_raises_input_error():
    with pytest.raises(InputError) as excinfo:
        decode_bytes(b"\xff\xff\xff\xff", "bad.csv")
    assert excinfo.value.code == "ENCODING_UNDECODABLE"


def test_undecodable_sample_is_file_level_error():
    result = _expect_file_error("generic-demo-010-undecodable.csv", "ENCODING_UNDECODABLE")
    assert result is None


def test_encoding_does_not_silently_replace_characters():
    """GB18030 能解的字节不得被 UTF-8 解码器用替换字符蒙混过去。"""
    raw = "示例能源戊".encode("gb18030")
    text, encoding = decode_bytes(raw)
    assert encoding == "gb18030"
    assert text == "示例能源戊"
    assert "\ufffd" not in text


# ---------------------------------------------------------------------------
# 未登记列 / 缺列 / 空文件 / 仅表头
# ---------------------------------------------------------------------------


def test_unknown_column_is_kept_in_extra_and_row_still_usable():
    entry = TRUTH_BY_FILE["generic-demo-008-unknown-column.csv"]
    result = import_one("generic-demo-008-unknown-column.csv")
    assert result.ledger["summary"]["rows_accepted"] == 2
    assert "自定义字段X" in result.ledger["rows"][0]["extra"]
    assert entry["unknown_columns"] == ["自定义字段X"]
    fingerprint = result.fingerprints["files"][0]
    assert fingerprint["rows_accepted"] == 2
    assert fingerprint["unknown_columns"] == ["自定义字段X"]


def test_xlsx_null_cells_do_not_cause_misalignment(tmp_path):
    """xlsx 缺格按坐标补空，不得误判成错位列（分隔文本才该判错位）。"""
    from investment_attribution.ingest.tables import read_xlsx

    raw = (SAMPLES / "htsec-demo-001.xlsx").read_bytes()
    table = read_xlsx(raw, "htsec-demo-001.xlsx")
    width = len(table.header)
    assert all(len(cells) == width for _, cells in table.rows)
    assert table.rows


@pytest.mark.parametrize(
    "name,code",
    [
        ("generic-demo-004-missing-column.csv", "MISSING_REQUIRED_COLUMN"),
        ("generic-demo-006-empty.csv", "EMPTY_FILE"),
        ("generic-demo-007-header-only.csv", "HEADER_ONLY"),
        ("generic-demo-010-undecodable.csv", "ENCODING_UNDECODABLE"),
    ],
)
def test_file_level_failures_return_two_and_write_nothing(tmp_path, name, code):
    with pytest.raises(IAError) as excinfo:
        import_files([sample(name)], DATA_DIR, out_dir=str(tmp_path))
    assert excinfo.value.code == code
    assert list(tmp_path.iterdir()) == []


def _expect_file_error(name, code):
    with pytest.raises(IAError) as excinfo:
        import_one(name)
    assert excinfo.value.code == code
    return None


def test_missing_input_file_is_input_error():
    with pytest.raises(InputError) as excinfo:
        import_files([str(SAMPLES / "no-such-file.csv")], DATA_DIR)
    assert excinfo.value.code == "FILE_NOT_FOUND"


# ---------------------------------------------------------------------------
# 账户映射：未登记落待确认，不并入默认账户
# ---------------------------------------------------------------------------


def test_unregistered_account_is_pending_not_merged():
    result = import_one("generic-demo-014-unregistered.csv")
    assert result.exit_code == EXIT_DEGRADED
    rows = result.ledger["rows"]
    assert rows, "未登记账户的行仍要入库（否则用户看不见自己的数据丢了）"
    for row in rows:
        assert row["account_id"] == "Y0000009"
        assert row["account_status"] == "pending"
        assert row["account_pending_reason"] == "ACCOUNT_NOT_REGISTERED"
        assert row["account_alias"] is None
    pending = result.account_map["pending"]
    assert [item["account_id"] for item in pending] == ["Y0000009"]


def test_registered_accounts_are_marked_registered():
    result = import_one("htsec-demo-001.xlsx")
    assert result.account_map["pending"] == []
    for row in result.ledger["rows"]:
        assert row["account_status"] == "registered"
        assert row["account_alias"] == "示例账户一"


def test_explicit_accounts_file_can_register_pending_account(tmp_path):
    registry = {
        "format": "ia.accounts",
        "format_version": 1,
        "accounts": [
            {
                "account_id": "Y0000009",
                "alias": "示例账户九",
                "shareholder_accounts": ["A000000009"],
            }
        ],
    }
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps(registry, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    result = import_files(
        [sample("generic-demo-014-unregistered.csv")], DATA_DIR, accounts_path=str(path)
    )
    assert result.degraded is False
    assert result.account_map["pending"] == []
    for row in result.ledger["rows"]:
        assert row["account_status"] == "registered"
        assert row["account_alias"] == "示例账户九"


def test_account_file_without_account_id_is_pending():
    """缺资金账号的行不能挂到默认账户。"""
    from investment_attribution.ingest.accounts import AccountRegistry

    registry = AccountRegistry({})
    resolution = registry.resolve(None, "A000000001")
    assert resolution.status == "pending"
    assert resolution.account_id is None
    assert resolution.pending_reason == "ACCOUNT_NOT_IN_FILE"


def test_shareholder_mismatch_goes_pending():
    from investment_attribution.ingest.accounts import AccountRegistry

    registry = AccountRegistry(
        {"Y0000001": {"alias": "示例账户一", "shareholder_accounts": ["A000000001"]}}
    )
    resolution = registry.resolve("Y0000001", "A000000002")
    assert resolution.status == "pending"
    assert resolution.pending_reason == "SHAREHOLDER_ACCOUNT_MISMATCH"


# ---------------------------------------------------------------------------
# 产物：落盘路径、确定性、退出码
# ---------------------------------------------------------------------------


def test_out_dir_writes_four_artifacts(tmp_path):
    result = import_files(
        [sample("htsec-demo-001.xlsx"), sample("huaxi-demo-001.xls")],
        DATA_DIR,
        out_dir=str(tmp_path),
    )
    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == [
        "account_map.json",
        "fingerprints.json",
        "import_errors.json",
        "ledger.json",
    ]
    assert set(result.paths) == {"ledger", "errors", "fingerprints", "account_map"}


def test_double_run_is_byte_identical(tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    for target in (first, second):
        target.mkdir()
        import_files(
            [sample("htsec-demo-001.xlsx"), sample("huaxi-demo-001.xls")],
            DATA_DIR,
            out_dir=str(target),
        )
    for name in ("ledger.json", "import_errors.json", "fingerprints.json", "account_map.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_output_does_not_depend_on_argument_order(tmp_path):
    """同一批文件换个命令行顺序，产物必须逐字节一致。"""
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    files = [sample("htsec-demo-001.xlsx"), sample("huaxi-demo-001.xls")]
    import_files(files, DATA_DIR, out_dir=str(first))
    import_files(list(reversed(files)), DATA_DIR, out_dir=str(second))
    for name in ("ledger.json", "import_errors.json", "fingerprints.json", "account_map.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_explicit_out_and_errors_paths_override_out_dir(tmp_path):
    ledger = tmp_path / "custom-ledger.json"
    errors = tmp_path / "custom-errors.json"
    result = import_files(
        [sample("generic-demo-005-misaligned.csv")],
        DATA_DIR,
        out_dir=str(tmp_path / "out"),
        ledger_path=str(ledger),
        errors_path=str(errors),
    )
    assert ledger.is_file()
    assert errors.is_file()
    assert result.paths["ledger"] == str(ledger)
    assert result.paths["errors"] == str(errors)


def test_clean_import_is_exit_zero_and_not_degraded():
    result = import_files(
        [sample("htsec-demo-001.xlsx"), sample("huaxi-demo-001.xls")], DATA_DIR
    )
    assert result.degraded is False
    assert result.exit_code == EXIT_OK


def test_row_error_makes_import_degraded():
    result = import_one("generic-demo-005-misaligned.csv")
    assert result.degraded is True
    assert result.exit_code == EXIT_DEGRADED


def test_errors_are_sorted_and_carry_by_reason_counts():
    result = import_one("generic-demo-005-misaligned.csv")
    payload = result.errors
    assert payload["format"] == "ia.import_errors"
    assert payload["summary"]["error_count"] == 2
    assert payload["summary"]["by_reason"] == {"COLUMN_COUNT_MISMATCH": 2}
    lines = [item["source_line_no"] for item in payload["errors"]]
    assert lines == sorted(lines)


def test_ledger_shape_declares_contract_version():
    result = import_one("htsec-demo-001.xlsx")
    manifest = json.loads((DATA_DIR / "rules" / "manifest.json").read_text(encoding="utf-8"))
    assert result.ledger["contract_version"] == manifest["contract_version"]
    assert result.ledger["format"] == "ia.ledger"
    assert result.ledger["format_version"] == 1


# ---------------------------------------------------------------------------
# 值与壳的单元级守门
# ---------------------------------------------------------------------------


def test_thousands_separator_is_parsed_as_money():
    result = import_one("generic-demo-009-thousands.csv")
    row = result.ledger["rows"][0]
    assert row["amount"] == 410000  # 4100.00 元 → 整数分
    assert row["settlement_amount"] == -410500


def test_shell_strip_only_strips_whole_cell():
    from investment_attribution.ingest.cells import strip_shell

    assert strip_shell('="699001"') == ("699001", True)
    assert strip_shell("699001") == ("699001", True)
    clean, ok = strip_shell('="示例"转债丁"')
    assert ok is False
    assert clean == '="示例"转债丁"'


def test_date_and_time_normalization():
    from investment_attribution.ingest.values import parse_date, parse_time

    assert parse_date("2025/1/6") == "2025-01-06"
    assert parse_date("20250106") == "2025-01-06"
    assert parse_time("093102") == "09:31:02"
    assert parse_time("") is None


def test_quantity_and_price_keep_decimal_precision():
    from investment_attribution.ingest.values import parse_price, parse_quantity

    assert parse_quantity("1000") == "1000"
    assert parse_quantity("1000.0000") == "1000"
    assert parse_price("10.20") == "10.2"
    assert parse_price("") is None


def test_row_error_is_domain_error():
    """行级异常必须继承项目异常基类，否则 CLI 抓不住会逃逸成 traceback。"""
    assert issubclass(RowError, IAError)
