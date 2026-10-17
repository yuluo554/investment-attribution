"""B1 导入基准：导入产物 vs 导入真值的逐文件比对。

与 B2 / B3 / B4 同纪律：比对函数**只读两边产物**，不重算导入——它不读原始
交割单字节、不调 ``build_record``。把导入层算错的行、写错的原因码、数错的
计数传进来，这里必须给出违例，否则"逐行逐字段对齐真值"只是一句口号。

产物形态（``outcomes``）：``{"<source_file>": {...}}``，每项两种之一——

- 整份文件不可用：``{"status": "file_error", "file_error_code": "..."}``；
- 导入成功：``{"status": "ok", "rows": [...], "errors": [...],
  "fingerprint": {...}}``（``fingerprint`` 可选）。

**两处刻意不比**（比了就是拿两套词汇互相判红）：

- ``fingerprint.encoding`` / ``delimiter``：真值登记的是**声明形态**
  （``binary-xlsx`` / ``utf-8`` / 制表符），指纹登记的是**探测结果**
  （``xlsx`` / ``utf-8-sig`` / ``tab``）。两者都正确，只是词汇不同；
  字节一致性由 ``fingerprint.sha256`` 保证，那才是真判据。
- ``errors[].raw_line``：真值存的是**源文件原文**，导入台账存的是**解码后
  逐格重新拼接**（含引号的单元格不会重新加引号）。行级错误的判据是
  ``(source_line_no, reason_code)`` 与计数；逐行列值另有 ``rows`` 全字段等价。

本模块原先只内联在 ``tests/test_import.py`` 里；M5 抽出来，让
``bench --only B1`` 与测试共用同一份实现，而不是各写一套判据。
"""

EXPECTED_FORMAT = "ia.truth.import"


class B1Report(object):
    def __init__(self, violations, checked, tolerances):
        self.violations = violations
        self.checked = checked
        self.tolerances = tolerances

    @property
    def ok(self):
        return not self.violations

    def as_dict(self):
        return {
            "suite": "B1",
            "ok": self.ok,
            "checked": self.checked,
            "violation_count": len(self.violations),
            "tolerances": self.tolerances,
            "violations": self.violations,
        }


#: 逐字段比对时允许真值缺省的键（真值只写"输入 + 期望"能推出的东西）。
ROW_FIELDS = (
    "source_file",
    "source_sha256",
    "source_line_no",
    "account_id",
    "account_alias",
    "shareholder_account",
    "account_status",
    "account_pending_reason",
    "symbol",
    "name",
    "business",
    "trade_date",
    "trade_time",
    "quantity",
    "price",
    "amount",
    "settlement_amount",
    "commission",
    "stamp_tax",
    "transfer_fee",
    "other_fee",
    "direction",
    "contract_no",
    "trade_no",
    "extra",
)

#: 指纹里可以逐字段比对的键（编码 / 分隔符是两套词汇，见模块 docstring）。
FINGERPRINT_FIELDS = ("sha256", "unknown_columns")


def _row_violations(name, index, expected, actual, violations):
    for field in ROW_FIELDS:
        if field not in expected:
            continue
        if expected[field] != actual.get(field):
            violations.append(
                {
                    "check": "row_field",
                    "key": [name, index, field],
                    "expected": expected[field],
                    "actual": actual.get(field),
                }
            )


def _error_key(item):
    return (item["source_line_no"], item["reason_code"])


def compare(outcomes, truth_payload, data_dir=None, method=None):
    """逐文件比对导入产物与导入真值；返回 ``B1Report``。

    ``method`` 对导入层无意义（导入不依赖成本口径），保留参数只为与
    B2 / B3 / B4 的 ``compare(产物, 真值, data_dir[, method])`` 接口一致。
    """
    violations = []
    checked = 0
    if truth_payload.get("format") != EXPECTED_FORMAT:
        violations.append(
            {
                "check": "truth_format",
                "expected": EXPECTED_FORMAT,
                "actual": truth_payload.get("format"),
            }
        )
        return B1Report(violations, checked, {})

    entries = truth_payload.get("files") or []
    known = {entry["source_file"] for entry in entries}
    unknown = sorted(set(outcomes) - known)
    if unknown:
        violations.append({"check": "unexpected_file", "key": unknown})

    for entry in entries:
        name = entry["source_file"]
        actual = outcomes.get(name)
        if actual is None:
            violations.append({"check": "file_missing", "key": [name]})
            continue
        checked += 1
        status = entry["status"]
        actual_status = actual.get("status")
        if status == "file_error":
            # 整份不可用必须**真的**报同一原因码：静默吞掉更危险。
            if actual_status != "file_error":
                violations.append(
                    {
                        "check": "file_error_expected",
                        "key": [name],
                        "expected": entry.get("file_error_code"),
                        "actual": actual_status,
                    }
                )
                continue
            if actual.get("file_error_code") != entry.get("file_error_code"):
                violations.append(
                    {
                        "check": "file_error_code",
                        "key": [name],
                        "expected": entry.get("file_error_code"),
                        "actual": actual.get("file_error_code"),
                    }
                )
            continue

        if actual_status == "file_error":
            violations.append(
                {
                    "check": "unexpected_file_error",
                    "key": [name],
                    "expected": status,
                    "actual": actual.get("file_error_code"),
                }
            )
            continue

        rows = actual.get("rows") or []
        if len(rows) != entry.get("expected_row_count"):
            violations.append(
                {
                    "check": "row_count",
                    "key": [name],
                    "expected": entry.get("expected_row_count"),
                    "actual": len(rows),
                }
            )
        for index, expected_row in enumerate(entry.get("expected_rows") or []):
            if index >= len(rows):
                break
            _row_violations(name, index, expected_row, rows[index], violations)

        errors = actual.get("errors") or []
        expected_errors = entry.get("expected_row_errors") or []
        if len(errors) != entry.get("expected_error_count"):
            violations.append(
                {
                    "check": "error_count",
                    "key": [name],
                    "expected": entry.get("expected_error_count"),
                    "actual": len(errors),
                }
            )
        for index, expected_error in enumerate(expected_errors):
            if index >= len(errors):
                break
            if _error_key(expected_error) != _error_key(errors[index]):
                violations.append(
                    {
                        "check": "error_key",
                        "key": [name, index],
                        "expected": list(_error_key(expected_error)),
                        "actual": list(_error_key(errors[index])),
                    }
                )

        fingerprint = actual.get("fingerprint")
        if fingerprint is not None:
            for field in FINGERPRINT_FIELDS:
                if field not in entry:
                    continue
                if fingerprint.get(field) != entry[field]:
                    violations.append(
                        {
                            "check": "fingerprint_" + field,
                            "key": [name],
                            "expected": entry[field],
                            "actual": fingerprint.get(field),
                        }
                    )

    return B1Report(
        violations,
        checked,
        {
            "row_fields": len(ROW_FIELDS),
            "fingerprint_fields": len(FINGERPRINT_FIELDS),
        },
    )
