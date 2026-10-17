"""列名归一与表头结构校验。

规则（plan/03 §M1）：

- 列名走**冻结别名表**；未登记列记 ``unknown``，行仍然可用；
- 必填列缺失 → **整份文件不可用**（``exit 2``），因为无法判定列语义；
- **列对齐不可信的行不入库**：列数与会话表头不一致即落行级台账。

表头本身也过一遍 ``="..."`` 壳清洗——华西样例连表头都是壳。
"""

from ..errors import InputError
from .cells import strip_shell_header


class Header(object):
    def __init__(self, field_names, unknown_columns, raw):
        self.field_names = field_names  # [(index, canonical_field)]，按列顺序
        self.unknown_columns = unknown_columns  # [(index, raw_header)]
        self.raw = raw

    @property
    def width(self):
        return len(self.raw)

    def __repr__(self):
        return "<Header width={} known={} unknown={}>".format(
            self.width, len(self.field_names), len(self.unknown_columns)
        )


def normalize_header(raw_cells, alias_lookup):
    """把原始表头映射成 ``(index, canonical_field)`` 列表。"""
    field_names = []
    unknown = []
    for index, raw in enumerate(raw_cells):
        label = strip_shell_header(raw if raw is not None else "").strip()
        if not label:
            continue
        field_name = alias_lookup.get(label)
        if field_name is None:
            unknown.append((index, label))
        else:
            field_names.append((index, field_name))
    return Header(field_names, unknown, list(raw_cells))


def required_fields_present(header, required_fields):
    """返回缺失的必填列名列表（空列表 = 齐全）。"""
    present = {field_name for _, field_name in header.field_names}
    return [name for name in required_fields if name not in present]


def validate_header(header, required_fields, source):
    missing = required_fields_present(header, required_fields)
    if missing:
        raise InputError(
            "{}: 缺必填列 {}".format(source, ", ".join(missing)),
            code="MISSING_REQUIRED_COLUMN",
        )
    seen = {}
    for index, field_name in header.field_names:
        if field_name in seen:
            raise InputError(
                "{}: 列 {} 出现在第 {} 与第 {} 列（重复列无法判定语义）".format(
                    source, field_name, seen[field_name] + 1, index + 1
                ),
                code="DUPLICATE_COLUMN",
            )
        seen[field_name] = index
