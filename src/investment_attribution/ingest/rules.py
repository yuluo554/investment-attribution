"""冻结规则包加载（``data/rules/*.json``）。

规则包是 M1 由 ``tools/gen_data.py`` 产出的**冻结产物**：只读不写、
缺字段即报 ``DataError``。代码里不得写第二份别名表 / 编码顺序 / 账号白名单。
"""

import json
from pathlib import Path

from ..errors import DataError

RULE_FILES = ("aliases.json", "params.json", "accounts.json")


def read_json_file(path_value, code):
    path = Path(path_value)
    if not path.is_file():
        raise DataError("文件缺失：{}".format(path), code=code)
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except ValueError as exc:
        raise DataError(
            "不是合法 JSON：{}：{}".format(path, exc), code=code + "_INVALID"
        )


def read_json(data_dir, relative, code):
    return read_json_file(Path(data_dir) / relative, code)


def _require(payload, key, code):
    if key not in payload:
        raise DataError("规则包缺字段 {}（{}）".format(key, code), code=code)
    return payload[key]


def load_rules(data_dir):
    """加载别名表、参数包、账户登记；结构不全即失败（不静默降级）。"""
    aliases = read_json(data_dir, "rules/aliases.json", "ALIASES_MISSING")
    params = read_json(data_dir, "rules/params.json", "PARAMS_MISSING")
    accounts = read_json(data_dir, "rules/accounts.json", "ACCOUNTS_MISSING")

    alias_map = _require(aliases, "aliases", "ALIASES_INCOMPLETE")
    required = _require(aliases, "required_fields", "ALIASES_INCOMPLETE")
    direction_patterns = _require(aliases, "direction_patterns", "ALIASES_INCOMPLETE")
    extra_fields = aliases.get("extra_fields", {})

    lookup = {}
    for field_name, labels in alias_map.items():
        for label in labels:
            normalized = label.strip()
            if normalized in lookup and lookup[normalized] != field_name:
                raise DataError(
                    "列名别名重复：{} 同时映射到 {} 与 {}".format(
                        normalized, lookup[normalized], field_name
                    ),
                    code="ALIASES_CONFLICT",
                )
            lookup[normalized] = field_name

    for field_name in required:
        if field_name not in alias_map:
            raise DataError(
                "必填字段 {} 没有别名表".format(field_name), code="ALIASES_INCOMPLETE"
            )

    order = _require(params, "encoding_order", "PARAMS_INCOMPLETE")
    if order != ["utf-8-sig", "utf-8", "gb18030"]:
        raise DataError(
            "params.encoding_order 必须为 K6 冻结顺序，实际 {!r}".format(order),
            code="ENCODING_ORDER_DRIFT",
        )

    directory = Path(data_dir)
    return {
        "data_dir": directory,
        "alias_lookup": lookup,
        "canonical_fields": list(alias_map),
        "required_fields": list(required),
        "extra_fields": dict(extra_fields),
        "direction_buy": tuple(direction_patterns.get("buy", ())),
        "direction_sell": tuple(direction_patterns.get("sell", ())),
        "date_formats": tuple(params.get("date_formats", ("%Y-%m-%d",))),
        "time_formats": tuple(params.get("time_formats", ("%H:%M:%S",))),
        "money_max_scale": int(params.get("money_max_scale", 2)),
        "price_max_scale": int(params.get("price_max_scale", 4)),
        "quantity_max_scale": int(params.get("quantity_max_scale", 4)),
        "accounts": load_accounts(accounts),
    }


def load_accounts(payload):
    """账户登记表：``{account_id: {alias, shareholder_accounts}}``。

    校验账号形态（``Y`` + 7 位 / ``A`` + 9 位）——登记表写错会让整条
    账户映射失真，属于"能提前抓住就别留到报告里"的缺陷。
    """
    entries = _require(payload, "accounts", "ACCOUNTS_INCOMPLETE")
    registry = {}
    for entry in entries:
        account_id = entry.get("account_id")
        if not account_id or not (account_id.startswith("Y") and account_id[1:].isdigit()):
            raise DataError(
                "账户登记表出现非法资金账号 {!r}".format(account_id),
                code="ACCOUNTS_INVALID",
            )
        if account_id in registry:
            raise DataError(
                "账户登记表资金账号重复：{}".format(account_id), code="ACCOUNTS_INVALID"
            )
        shareholders = entry.get("shareholder_accounts") or []
        for shareholder in shareholders:
            if not (shareholder.startswith("A") and shareholder[1:].isdigit()):
                raise DataError(
                    "账户登记表出现非法股东账号 {!r}".format(shareholder),
                    code="ACCOUNTS_INVALID",
                )
        registry[account_id] = {
            "alias": entry.get("alias") or account_id,
            "shareholder_accounts": list(shareholders),
        }
    return registry
