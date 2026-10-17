"""契约清单（hand-maintained contract，非冻结生成物）。

本文件只承载**跨模块共享的契约**：
- ``bench_thresholds``：基准门槛（数据而非代码，随 manifest 下发）
- ``coverage``：覆盖度登记骨架（M1 由生成器填满，要求分子 = 分母）
- ``contract_version``：口径版本号

**不是**冻结数据产物：M1 起的合成样例、列名别名表、公司行为规则表、
参数包与真值台账一律由 ``tools/`` 下的生成器产出并配 ``--check`` 位级守门。
"""

import json
from pathlib import Path

from .errors import DataError

MANIFEST_RELATIVE_PATH = Path("rules") / "manifest.json"
REQUIRED_THRESHOLDS = (
    "position_quantity_tolerance_shares",
    "position_amount_tolerance_cny",
    "xirr_relative_tolerance",
    "split_conservation_tolerance_cny",
    "transfer_offset_tolerance_cny",
)
REQUIRED_TOP_LEVEL = ("format", "format_version", "contract_version", "bench_thresholds", "coverage")


def manifest_path(data_dir):
    return Path(data_dir) / MANIFEST_RELATIVE_PATH


def load_manifest(data_dir):
    """读取并校验契约清单；任何结构缺陷都抛 ``DataError``（不静默降级）。"""
    path = manifest_path(data_dir)
    if not path.is_file():
        raise DataError(
            "契约清单缺失：{}".format(path),
            code="MANIFEST_MISSING",
        )
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except ValueError as exc:
        raise DataError(
            "契约清单不是合法 JSON：{}".format(exc),
            code="MANIFEST_INVALID_JSON",
        )

    if not isinstance(payload, dict):
        raise DataError("契约清单顶层必须是对象", code="MANIFEST_INVALID_SHAPE")

    missing = [key for key in REQUIRED_TOP_LEVEL if key not in payload]
    if missing:
        raise DataError(
            "契约清单缺字段：{}".format(", ".join(sorted(missing))),
            code="MANIFEST_MISSING_KEYS",
        )

    if payload["format"] != "ia.manifest":
        raise DataError(
            "契约清单 format 不匹配：{}".format(payload["format"]),
            code="MANIFEST_FORMAT_MISMATCH",
        )

    thresholds = payload["bench_thresholds"]
    if not isinstance(thresholds, dict):
        raise DataError("bench_thresholds 必须是对象", code="MANIFEST_INVALID_SHAPE")
    missing = [key for key in REQUIRED_THRESHOLDS if key not in thresholds]
    if missing:
        raise DataError(
            "bench_thresholds 缺字段：{}".format(", ".join(sorted(missing))),
            code="MANIFEST_MISSING_THRESHOLDS",
        )
    for key in REQUIRED_THRESHOLDS:
        value = thresholds[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise DataError(
                "门槛 {} 必须是非负数值，实际为 {!r}".format(key, value),
                code="MANIFEST_INVALID_THRESHOLD",
            )

    coverage = payload["coverage"]
    if not isinstance(coverage, dict) or "surfaces" not in coverage:
        raise DataError(
            "coverage 必须含 surfaces 数组",
            code="MANIFEST_INVALID_COVERAGE",
        )
    if not isinstance(coverage["surfaces"], list):
        raise DataError("coverage.surfaces 必须是数组", code="MANIFEST_INVALID_COVERAGE")

    return payload
