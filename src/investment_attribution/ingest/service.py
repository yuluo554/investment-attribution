"""文件级编排：去重、账户映射、行级台账、产物落盘。

顺序固定，确保同一输入双跑逐字节一致：

1. 逐个文件算 SHA-256 → **同内容即同一件**（改名 / 改扩展名都识别）；
2. 读表（按字节形态判别）→ 校表头 → 逐行转统一流水；
3. 行级失败只进台账，不中断整份文件；
4. 产物按固定排序键输出，全部经 ``serialize.write_json_atomic`` 落盘。

整份文件不可用（缺列 / 空文件 / 编码失败 / 仅表头）时**异常上抛**，
CLI 转 ``exit 2``，本轮不写任何产物——避免"少了一个账户却看起来完整"。
"""

import hashlib
import os
from pathlib import Path

from ..errors import InputError
from ..manifest import load_manifest
from ..serialize import write_json_atomic
from .accounts import AccountRegistry
from .records import RowError, build_record
from .rules import load_rules
from .schema import normalize_header, validate_header
from .tables import read_table

LEDGER_FILE = "ledger.json"
ERRORS_FILE = "import_errors.json"
FINGERPRINTS_FILE = "fingerprints.json"
ACCOUNT_MAP_FILE = "account_map.json"


class ImportResult(object):
    def __init__(self, ledger, errors, fingerprints, account_map, paths, degraded):
        self.ledger = ledger
        self.errors = errors
        self.fingerprints = fingerprints
        self.account_map = account_map
        self.paths = paths
        self.degraded = degraded

    @property
    def exit_code(self):
        from ..exit_codes import EXIT_DEGRADED, EXIT_OK

        return EXIT_DEGRADED if self.degraded else EXIT_OK


def _read_bytes(path):
    try:
        with open(path, "rb") as stream:
            return stream.read()
    except FileNotFoundError:
        raise InputError("输入文件不存在：{}".format(path), code="FILE_NOT_FOUND")
    except IsADirectoryError:
        raise InputError("输入路径是目录而不是文件：{}".format(path), code="FILE_NOT_FOUND")
    except OSError as exc:
        raise InputError(
            "输入文件读取失败：{}：{}".format(path, exc), code="FILE_UNREADABLE"
        )


def _raw_line(cells, delimiter):
    if delimiter:
        return delimiter.join(cells)
    return " | ".join(cells)


def _normalize_errors(errors):
    return sorted(
        errors,
        key=lambda item: (
            item["source_file"],
            item["source_line_no"],
            item["reason_code"],
        ),
    )


def _sort_rows(rows):
    return sorted(
        rows,
        key=lambda row: (
            row["trade_date"],
            row["trade_time"] or "",
            row["source_file"],
            row["source_line_no"],
        ),
    )


def import_files(
    files,
    data_dir,
    out_dir=None,
    accounts_path=None,
    ledger_path=None,
    errors_path=None,
    imported_at=None,
):
    """导入若干交割单文件，返回 ``ImportResult``。

    参数语义遵守 K4/C6：``out_dir`` 显式才落盘；``ledger_path`` /
    ``errors_path`` 显式则覆盖 ``out_dir`` 下的默认文件名。

    ``imported_at`` 是**显式传入**的导入时间戳，缺省为 ``None``：引擎不读
    系统时钟（plan/02 §五），指纹台账里写 ``null`` 比写一个会漂移的时间更
    可复现；需要留痕时由调用方传入固定值。
    """
    rules = load_rules(data_dir)

    registry_accounts = dict(rules["accounts"])
    if accounts_path:
        from .rules import load_accounts, read_json_file

        payload = read_json_file(accounts_path, "ACCOUNTS_MISSING")
        registry_accounts.update(load_accounts(payload))

    registry = AccountRegistry(registry_accounts)

    contract_version = load_manifest(data_dir)["contract_version"]

    rows = []
    errors = []
    fingerprints = []
    seen_by_sha = {}
    total_inputs = 0

    for raw_path in files:
        total_inputs += 1
        path = Path(raw_path)
        raw = _read_bytes(path)
        sha = hashlib.sha256(raw).hexdigest()

        if sha in seen_by_sha:
            fingerprints.append(
                {
                    "sha256": sha,
                    "source_file": path.name,
                    "size_bytes": len(raw),
                    "encoding": None,
                    "delimiter": None,
                    "table_kind": None,
                    "unknown_columns": [],
                    "rows_total": 0,
                    "rows_accepted": 0,
                    "rows_rejected": 0,
                    "status": "duplicate",
                    "duplicate_of": seen_by_sha[sha],
                    "imported_at": imported_at,
                }
            )
            continue

        table = read_table(raw, path.name)
        header = normalize_header(table.header, rules["alias_lookup"])
        validate_header(header, rules["required_fields"], path.name)
        if not table.rows:
            raise InputError(
                "{}: 只有表头没有数据行（不可用的交割单）".format(path.name),
                code="HEADER_ONLY",
            )

        accepted = 0
        rejected = 0
        for line_no, cells in table.rows:
            if not any((cell or "").strip() for cell in cells):
                continue
            if len(cells) != header.width:
                errors.append(
                    {
                        "source_file": path.name,
                        "source_sha256": sha,
                        "source_line_no": line_no,
                        "reason_code": "COLUMN_COUNT_MISMATCH",
                        "message": "列数 {} 与表头 {} 不一致".format(len(cells), header.width),
                        "raw_line": _raw_line(cells, "\t" if table.delimiter == "tab" else ","),
                    }
                )
                rejected += 1
                continue
            context = {
                "source_file": path.name,
                "source_sha256": sha,
                "line_no": line_no,
                "raw_line": _raw_line(cells, "\t" if table.delimiter == "tab" else ","),
                "account_registry": registry,
                "rules": rules,
            }
            try:
                rows.append(build_record(cells, header, context))
            except RowError as exc:
                errors.append(
                    {
                        "source_file": path.name,
                        "source_sha256": sha,
                        "source_line_no": line_no,
                        "reason_code": exc.code,
                        "message": str(exc),
                        "raw_line": context["raw_line"],
                    }
                )
                rejected += 1
                continue
            accepted += 1

        seen_by_sha[sha] = path.name
        fingerprints.append(
            {
                "sha256": sha,
                "source_file": path.name,
                "size_bytes": len(raw),
                "encoding": table.encoding,
                "delimiter": table.delimiter,
                "table_kind": table.kind,
                "unknown_columns": [label for _, label in header.unknown_columns],
                "rows_total": accepted + rejected,
                "rows_accepted": accepted,
                "rows_rejected": rejected,
                "status": "imported",
                "duplicate_of": None,
                "imported_at": imported_at,
            }
        )

    rows = _sort_rows(rows)
    errors = _normalize_errors(errors)
    # 指纹按 (source_file, sha256) 排序输出：同一批文件的字节稳定性不应
    # 取决于命令行里文件的先后顺序。duplicate_of 仍记录真实的首见文件。
    fingerprints = sorted(
        fingerprints, key=lambda item: (item["source_file"], item["sha256"])
    )

    account_map = _build_account_map(registry, rows)
    pending_rows = sum(1 for row in rows if row["account_status"] == "pending")
    duplicates = [item for item in fingerprints if item["status"] == "duplicate"]

    ledger = {
        "format": "ia.ledger",
        "format_version": 1,
        "contract_version": contract_version,
        "summary": {
            "files_input": total_inputs,
            "files_imported": sum(1 for item in fingerprints if item["status"] == "imported"),
            "files_duplicate": len(duplicates),
            "rows_total": len(rows),
            "rows_accepted": len(rows),
            "rows_rejected": len(errors),
            "rows_pending_account": pending_rows,
            "row_error_count": len(errors),
        },
        "rows": rows,
    }

    errors_payload = {
        "format": "ia.import_errors",
        "format_version": 1,
        "contract_version": contract_version,
        "summary": {
            "error_count": len(errors),
            "by_reason": _count_by(errors, "reason_code"),
        },
        "errors": errors,
    }

    fingerprints_payload = {
        "format": "ia.fingerprints",
        "format_version": 1,
        "contract_version": contract_version,
        "files": fingerprints,
        "duplicates_skipped": [
            {"source_file": item["source_file"], "duplicate_of": item["duplicate_of"]}
            for item in duplicates
        ],
    }

    degraded = bool(errors) or pending_rows > 0

    paths = {}
    if out_dir:
        os.makedirs(str(out_dir), exist_ok=True)
    ledger_target = ledger_path or (Path(out_dir) / LEDGER_FILE if out_dir else None)
    errors_target = errors_path or (Path(out_dir) / ERRORS_FILE if out_dir else None)
    if ledger_target:
        write_json_atomic(ledger_target, ledger)
        paths["ledger"] = str(ledger_target)
    if errors_target:
        write_json_atomic(errors_target, errors_payload)
        paths["errors"] = str(errors_target)
    if out_dir:
        fingerprint_target = Path(out_dir) / FINGERPRINTS_FILE
        account_target = Path(out_dir) / ACCOUNT_MAP_FILE
        write_json_atomic(fingerprint_target, fingerprints_payload)
        write_json_atomic(account_target, account_map)
        paths["fingerprints"] = str(fingerprint_target)
        paths["account_map"] = str(account_target)

    return ImportResult(ledger, errors_payload, fingerprints_payload, account_map, paths, degraded)


def _count_by(items, key):
    counts = {}
    for item in sorted(items, key=lambda entry: entry[key]):
        counts[item[key]] = counts.get(item[key], 0) + 1
    return counts


def _build_account_map(registry, rows):
    observed = {}
    for row in rows:
        account_id = row["account_id"]
        key = account_id or "<未提供资金账号>"
        entry = observed.setdefault(
            key,
            {
                "account_id": account_id,
                "alias": row["account_alias"],
                "shareholder_accounts": set(),
                "status": row["account_status"],
                "pending_reason": row["account_pending_reason"],
                "rows": 0,
            },
        )
        if row["shareholder_account"]:
            entry["shareholder_accounts"].add(row["shareholder_account"])
        if row["account_status"] == "pending":
            entry["status"] = "pending"
            entry["pending_reason"] = row["account_pending_reason"] or entry["pending_reason"]
        entry["rows"] += 1

    accounts = []
    for key in sorted(observed):
        entry = observed[key]
        accounts.append(
            {
                "account_id": entry["account_id"],
                "alias": entry["alias"],
                "shareholder_accounts": sorted(entry["shareholder_accounts"]),
                "status": entry["status"],
                "pending_reason": entry["pending_reason"],
                "rows": entry["rows"],
            }
        )

    pending = [entry for entry in accounts if entry["status"] == "pending"]
    return {
        "format": "ia.account_map",
        "format_version": 1,
        "accounts": accounts,
        "pending": pending,
        "note": "未登记账户落待确认，不并入默认账户；请用 --accounts 显式登记。",
    }
