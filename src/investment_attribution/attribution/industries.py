"""行业映射（冻结表 ``data/rules/industries.json``，用户可覆盖）。

未登记代码落 ``default``（默认 ``未分类``），并且**必须在产物里可见**：
分解表里会以"未分类"独立成行，同时列出落在这一桶里的具体标的。
"看不见的兜底桶"等于静默猜测，本项目不接受。
"""

import hashlib
import json
from pathlib import Path

from ..errors import DataError

DEFAULT_INDUSTRY = "未分类"


class Industries(object):
    def __init__(self, path, sha256, mapping, default):
        self.path = path
        self.sha256 = sha256
        self.mapping = mapping
        self.default = default

    def of(self, symbol):
        return self.mapping.get(symbol, self.default)

    def unclassified(self, symbols):
        return sorted(
            symbol for symbol in symbols if symbol not in self.mapping
        )

    def describe(self):
        return {
            "path": self.path,
            "sha256": self.sha256,
            "default": self.default,
            "symbol_count": len(self.mapping),
        }


def load_industries(data_dir):
    """读取行业映射；缺文件或结构不全抛 ``DataError``（退出码 2）。"""
    path = Path(data_dir) / "rules" / "industries.json"
    if not path.is_file():
        raise DataError(
            "行业映射缺失：{}".format(path), code="INDUSTRIES_MISSING"
        )
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DataError(
            "行业映射不是合法 JSON：{}：{}".format(path, exc),
            code="INDUSTRIES_INVALID",
        )
    if not isinstance(payload, dict) or payload.get("format") != "ia.industries":
        raise DataError(
            "行业映射 format 必须为 ia.industries", code="INDUSTRIES_INVALID"
        )
    mapping = payload.get("industries")
    if not isinstance(mapping, dict) or not mapping:
        raise DataError(
            "行业映射缺 industries 段", code="INDUSTRIES_INCOMPLETE"
        )
    default = payload.get("default")
    if not isinstance(default, str) or not default:
        raise DataError(
            "行业映射缺 default（未分类兜底名）", code="INDUSTRIES_INCOMPLETE"
        )
    return Industries(
        path="rules/industries.json",
        sha256=hashlib.sha256(raw).hexdigest(),
        mapping={str(key): str(value) for key, value in mapping.items()},
        default=default,
    )
