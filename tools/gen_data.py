#!/usr/bin/env python3
"""M1 合成数据生成器（标准库，确定性，可逐字节复现）。

纪律（见 plan/04 §二）：
- 随机源只用自研 splitmix64，固定 seed，禁用 ``random``；
- 不读系统时钟，日期与流水号全部由固定文本 / seed 推导；
- 写出：xlsx 用 ``ZIP_STORED`` + ``create_system=0`` + 固定 ``date_time``，
  文本固定编码与行尾，JSON 用 ``ensure_ascii=False`` + ``sort_keys`` + LF；
- ``--check``：两层守门——① 产物形态（结构 / 白名单 / 覆盖度 / 真值纪律）
  ② 重新生成到临时目录后与冻结产物逐字节比对。

用法::

    python tools/gen_data.py                 # 生成到 data/
    python tools/gen_data.py --check         # 形态 + 字节双层校验
    python tools/gen_data.py --shape-only    # 只做形态校验
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import escape

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"
GENERATOR_TOOL = "tools/gen_data.py"
CONTRACT_VERSION = "0.4.0-m3"
SEED = 0x9E3779B97F4A7C15  # splitmix64 标准增量常量，作为固定种子
MASK64 = (1 << 64) - 1

CENT = Decimal("0.01")
BOM = b"\xef\xbb\xbf"

# ---------------------------------------------------------------------------
# 假代码与假账号白名单（data/README.md §三）
# ---------------------------------------------------------------------------

SYMBOLS = {
    "699000": ("示例现金", "现金"),
    "699001": ("示例科技甲", "信息技术"),
    "699002": ("示例银行乙", "银行"),
    "699003": ("示例基金丙", "基金"),
    "699004": ("示例转债丁", "转债"),
    "699005": ("示例能源戊", "能源"),
    "699006": ("示例医药己", "医药"),
    "699007": ("示例材料庚", "材料"),
}

ACCOUNTS = {
    "Y0000001": ("示例账户一", "A000000001"),
    "Y0000002": ("示例账户二", "A000000002"),
    "Y0000003": ("示例账户三", "A000000003"),
    "Y0000004": ("示例账户四", "A000000004"),
}

# 白名单内但**未登记**到 accounts.json 的账号：用于验证"未登记落待确认"。
UNREGISTERED_ACCOUNTS = {
    "Y0000009": ("示例账户九", "A000000009"),
}

LONG_NOTE = "示例备注：" + "本字段用于验证超长备注在导入层的读取、保留与回放，" * 10

# 31 个覆盖度形态（与 data/rules/manifest.json 的 surfaces 顺序一致）
SURFACE_ORDER = (
    "import.htsec_xlsx",
    "import.huaxi_xls_tab_gb18030",
    "import.generic_csv_utf8",
    "import.generic_csv_gb18030",
    "import.cell_formula_shell",
    "import.missing_column",
    "import.misaligned_column",
    "import.empty_file",
    "import.header_only",
    "cost.same_day_multi_fill",
    "cost.partial_sell",
    "cost.clear_then_rebuy",
    "cost.after_split_sell",
    "cost.fifo_vs_wavg_divergence",
    "cost.fee_not_itemized",
    "corporate_action.dividend",
    "corporate_action.stock_dividend",
    "corporate_action.rights_issue",
    "corporate_action.split",
    "corporate_action.reinvested_dividend",
    "corporate_action.unrecognized_pending",
    "attribution.lump_sum",
    "attribution.dca",
    "attribution.midterm_redemption",
    "attribution.negative_return",
    "attribution.zero_return",
    "attribution.zero_terminal_value",
    "merge.two_accounts",
    "merge.three_accounts",
    "merge.internal_transfer",
    "merge.overlapping_symbol",
)


def evidence(sample, note):
    return {"sample": sample, "note": note}


COVERAGE = {
    "import.htsec_xlsx": [evidence("htsec-demo-001.xlsx", "海通 19 列 xlsx")],
    "import.huaxi_xls_tab_gb18030": [
        evidence("huaxi-demo-001.xls", 'Tab 分隔 + GB18030 + 全单元格 ="..." 壳')
    ],
    "import.generic_csv_utf8": [
        evidence("generic-demo-001.csv", "UTF-8 通用 CSV"),
        evidence("generic-demo-012-bom.csv", "UTF-8 BOM"),
    ],
    "import.generic_csv_gb18030": [
        evidence("generic-demo-002.csv", "GB18030 通用 CSV")
    ],
    "import.cell_formula_shell": [
        evidence("huaxi-demo-001.xls", '全单元格 ="..." 包裹'),
        evidence("generic-demo-003-formula.csv", "含一条非法壳行"),
    ],
    "import.missing_column": [
        evidence("generic-demo-004-missing-column.csv", "缺 成交数量 列")
    ],
    "import.misaligned_column": [
        evidence("generic-demo-005-misaligned.csv", "两条错位列行")
    ],
    "import.empty_file": [evidence("generic-demo-006-empty.csv", "0 字节")],
    "import.header_only": [
        evidence("generic-demo-007-header-only.csv", "仅表头无数据行")
    ],
    "cost.same_day_multi_fill": [
        evidence("htsec-demo-001.xlsx", "2025-01-06 699001 两笔买入")
    ],
    "cost.partial_sell": [
        evidence("htsec-demo-001.xlsx", "2025-03-05 699001 卖出 600，原持仓 1500")
    ],
    "cost.clear_then_rebuy": [
        evidence("htsec-demo-001.xlsx", "2025-03-05 清仓 699001，2025-04-08 回补 800")
    ],
    "cost.after_split_sell": [
        evidence("htsec-demo-001.xlsx", "2025-07-15 拆分 699003 后 2025-07-16 卖出")
    ],
    "cost.fifo_vs_wavg_divergence": [
        evidence("htsec-demo-001.xlsx", "699001 两批买入后部分卖出，FIFO 与加权成本不同")
    ],
    "cost.fee_not_itemized": [
        evidence(
            "generic-demo-015-no-fee-columns.csv",
            "费用四列缺席，仅能由发生金额反推并标注 derived_from_settlement",
        )
    ],
    "corporate_action.dividend": [
        evidence("htsec-demo-001.xlsx", "2025-05-20 红利入账 699002")
    ],
    "corporate_action.stock_dividend": [
        evidence("htsec-demo-001.xlsx", "2025-06-10 送股入账 699002")
    ],
    "corporate_action.rights_issue": [
        evidence("htsec-demo-001.xlsx", "2025-07-01 配股 699002")
    ],
    "corporate_action.split": [
        evidence("htsec-demo-001.xlsx", "2025-07-15 拆分 699003")
    ],
    "corporate_action.reinvested_dividend": [
        evidence("htsec-demo-001.xlsx", "2025-06-10 红利再投 699001")
    ],
    "corporate_action.unrecognized_pending": [
        evidence("htsec-demo-001.xlsx", "2025-08-01 其他业务甲（规则表无法识别）")
    ],
    "attribution.lump_sum": [
        evidence("huaxi-demo-001.xls", "2025-04-01 699006 单笔买入 1000 股")
    ],
    "attribution.dca": [
        evidence("huaxi-demo-001.xls", "699005 三笔定投 + 一笔回补")
    ],
    "attribution.midterm_redemption": [
        evidence("huaxi-demo-001.xls", "2025-06-20 699005 卖出 500")
    ],
    "attribution.negative_return": [
        evidence("huaxi-demo-001.xls", "699006 成本 17510.00，清仓回款 15729.25")
    ],
    "attribution.zero_return": [
        evidence("huaxi-demo-001.xls", "699007 买卖同价且费用列为 0")
    ],
    "attribution.zero_terminal_value": [
        evidence("huaxi-demo-001.xls", "2025-12-22 全部清仓，期末持仓为 0")
    ],
    "merge.two_accounts": [
        evidence("htsec-demo-001.xlsx", "账户一"),
        evidence("huaxi-demo-001.xls", "账户二"),
    ],
    "merge.three_accounts": [
        evidence("htsec-demo-001.xlsx", "账户一"),
        evidence("huaxi-demo-001.xls", "账户二"),
        evidence("generic-demo-013-account-c.csv", "账户三"),
    ],
    "merge.internal_transfer": [
        evidence("htsec-demo-001.xlsx", "2025-10-10 转出 20000.00"),
        evidence("huaxi-demo-001.xls", "同日转入 20000.00，合同号 TR-A-B-0001"),
    ],
    "merge.overlapping_symbol": [
        evidence("htsec-demo-001.xlsx", "699001 账户一持仓"),
        evidence("huaxi-demo-001.xls", "699001 账户二持仓"),
    ],
}

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


class SplitMix64:
    """自研确定性 RNG（splitmix64），不依赖 stdlib ``random``。"""

    def __init__(self, seed):
        self.state = seed & MASK64

    def next_u64(self):
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
        return (z ^ (z >> 31)) & MASK64

    def below(self, limit):
        return self.next_u64() % limit


def dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money_text(value):
    q = dec(value).quantize(CENT, rounding=ROUND_HALF_UP)
    if q == 0:
        q = Decimal("0.00")
    return format(q, "f")


def fen(value):
    return int((dec(value) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def canonical(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        d = value
    else:
        if value == "":
            return None
        d = Decimal(value)
    if d == 0:
        return "0"
    return format(d.normalize(), "f")


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(obj):
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": "))
    return (text + "\n").encode("utf-8")


def direction_of(business):
    if "买入" in business:
        return "buy"
    if "卖出" in business:
        return "sell"
    return "other"


# ---------------------------------------------------------------------------
# 统一流水模型
# ---------------------------------------------------------------------------

EXTRA_TYPES = {"cash_balance": "money", "share_balance": "quantity", "note": "text"}
MONEY_FIELDS = {
    "amount",
    "settlement_amount",
    "commission",
    "stamp_tax",
    "transfer_fee",
    "other_fee",
    "cash_balance",
}
NUMERIC_FIELDS = MONEY_FIELDS | {"quantity", "price", "share_balance"}


@dataclass
class Tx:
    account_id: str
    shareholder_account: str
    symbol: str
    name: str
    business: str
    trade_date: str
    trade_time: str
    quantity: str = "0"
    price: str = ""
    amount: str = "0.00"
    settlement_amount: str = "0.00"
    commission: str = "0.00"
    stamp_tax: str = "0.00"
    transfer_fee: str = "0.00"
    other_fee: str = "0.00"
    direction: str = "other"
    contract_no: str = ""
    trade_no: str = ""
    note: str = ""
    cash_balance: str = "0.00"
    share_balance: str = "0"


def account_meta(account_id):
    """返回 ``(alias, shareholder, registered)``；未知账号直接报错（白名单纪律）。"""
    if account_id in ACCOUNTS:
        alias, shareholder = ACCOUNTS[account_id]
        return alias, shareholder, True
    if account_id in UNREGISTERED_ACCOUNTS:
        alias, shareholder = UNREGISTERED_ACCOUNTS[account_id]
        return alias, shareholder, False
    raise KeyError("账号 {} 不在白名单内".format(account_id))


def _tx(account, trade_date, trade_time, business, symbol, **kwargs):
    name = SYMBOLS[symbol][0]
    alias, shareholder, _ = account_meta(account)
    return Tx(
        account_id=account,
        shareholder_account=shareholder,
        symbol=symbol,
        name=name,
        business=business,
        trade_date=trade_date,
        trade_time=trade_time,
        direction=direction_of(business),
        **kwargs
    )


def buy(trade_date, trade_time, symbol, quantity, price, account, comm="5.00", transfer="0.00", other="0.00", note=""):
    amount = money_text(dec(quantity) * dec(price))
    settle = money_text(-(dec(amount) + dec(comm) + dec(transfer) + dec(other)))
    return _tx(
        account,
        trade_date,
        trade_time,
        "证券买入",
        symbol,
        quantity=str(quantity),
        price=str(price),
        amount=amount,
        settlement_amount=settle,
        commission=comm,
        transfer_fee=transfer,
        other_fee=other,
        note=note,
    )


def sell(trade_date, trade_time, symbol, quantity, price, account, comm="5.00", stamp="0.00", transfer="0.00", other="0.00", note=""):
    amount = money_text(dec(quantity) * dec(price))
    settle = money_text(dec(amount) - dec(comm) - dec(stamp) - dec(transfer) - dec(other))
    return _tx(
        account,
        trade_date,
        trade_time,
        "证券卖出",
        symbol,
        quantity=str(quantity),
        price=str(price),
        amount=amount,
        settlement_amount=settle,
        commission=comm,
        stamp_tax=stamp,
        transfer_fee=transfer,
        other_fee=other,
        note=note,
    )


def event(trade_date, trade_time, business, symbol, account, quantity="0", price="", amount="0.00", settle="0.00", comm="0.00", stamp="0.00", transfer="0.00", other="0.00", note=""):
    return _tx(
        account,
        trade_date,
        trade_time,
        business,
        symbol,
        quantity=quantity,
        price=price,
        amount=amount,
        settlement_amount=settle,
        commission=comm,
        stamp_tax=stamp,
        transfer_fee=transfer,
        other_fee=other,
        note=note,
    )


def number_rows(rows, prefix, rng):
    used = set()
    for index, tx in enumerate(rows, 1):
        if not tx.contract_no:
            tx.contract_no = "{}-{:04d}".format(prefix, index)
        if not tx.trade_no:
            while True:
                candidate = "T-{}-{:04d}".format(prefix.split("-")[-1], rng.below(9000) + 1000)
                if candidate not in used:
                    break
            used.add(candidate)
            tx.trade_no = candidate


def attach_balances(rows):
    cash = Decimal("0")
    shares = {}
    for tx in rows:
        cash += dec(tx.settlement_amount or "0")
        quantity = dec(tx.quantity or "0")
        symbol = tx.symbol
        if "买入" in tx.business or tx.business in ("送股入账", "配股", "红利再投", "拆分"):
            shares[symbol] = shares.get(symbol, Decimal("0")) + quantity
        elif "卖出" in tx.business:
            shares[symbol] = shares.get(symbol, Decimal("0")) - quantity
        tx.cash_balance = money_text(cash)
        tx.share_balance = canonical(shares.get(symbol, Decimal("0")))


# ---------------------------------------------------------------------------
# 账户流水（刻意注入 31 形态所需场景）
# ---------------------------------------------------------------------------

TRANSFER_CONTRACT = "TR-A-B-0001"


def build_account_a():
    """账户一：同日多笔、部分卖出、清仓回补、拆分后卖出、五类公司行为、无法识别行。"""
    rows = [
        event("2025-01-02", "09:25:00", "银证转入", "699000", "Y0000001", settle="100000.00"),
        buy("2025-01-06", "09:31:02", "699001", 1000, "10.00", "Y0000001", transfer="0.10"),
        buy("2025-01-06", "14:01:11", "699001", 500, "10.20", "Y0000001", transfer="0.05"),
        buy("2025-02-10", "10:15:00", "699002", 2000, "6.50", "Y0000001", transfer="0.13"),
        sell("2025-03-05", "10:00:00", "699001", 600, "11.50", "Y0000001", stamp="6.90", transfer="0.02"),
        sell("2025-03-05", "10:00:30", "699001", 900, "11.55", "Y0000001", stamp="10.40", transfer="0.03"),
        buy("2025-04-08", "09:45:00", "699001", 800, "9.80", "Y0000001", transfer="0.08"),
        buy("2025-04-25", "09:40:00", "699003", 5000, "1.20", "Y0000001", transfer="0.06"),
        buy("2025-05-12", "09:35:00", "699004", 100, "105.50", "Y0000001", transfer="0.11"),
        event("2025-05-20", "09:30:00", "红利入账", "699002", "Y0000001", settle="300.00"),
        event("2025-06-10", "09:30:00", "送股入账", "699002", "Y0000001", quantity="200"),
        event(
            "2025-06-10",
            "09:30:05",
            "红利再投",
            "699001",
            "Y0000001",
            quantity="100",
            price="9.50",
            amount="950.00",
        ),
        event(
            "2025-07-01",
            "09:30:00",
            "配股",
            "699002",
            "Y0000001",
            quantity="300",
            price="5.80",
            amount="1740.00",
            settle="-1740.00",
        ),
        event("2025-07-15", "09:30:00", "拆分", "699003", "Y0000001", quantity="5000"),
        sell("2025-07-16", "10:00:00", "699003", 3000, "0.62", "Y0000001", stamp="1.86", transfer="0.02"),
        event("2025-08-01", "09:30:00", "其他业务甲", "699002", "Y0000001", settle="12.34"),
        event("2025-09-02", "09:30:00", "银证转入", "699000", "Y0000001", settle="50000.00"),
        buy("2025-09-02", "09:31:00", "699002", 1000, "7.10", "Y0000001", transfer="0.07"),
        event(
            "2025-10-10",
            "14:00:00",
            "内部划转",
            "699000",
            "Y0000001",
            settle="-20000.00",
            note="转出至示例账户二",
        ),
        sell("2025-11-14", "10:20:00", "699002", 500, "6.40", "Y0000001", stamp="3.20", transfer="0.03"),
        sell("2025-12-19", "10:00:00", "699002", 3000, "7.05", "Y0000001", stamp="21.15", transfer="0.21"),
        sell("2025-12-19", "10:01:00", "699001", 400, "10.00", "Y0000001", stamp="4.00", transfer="0.04"),
    ]
    rows[18].contract_no = TRANSFER_CONTRACT
    return rows


def build_account_b():
    """账户二：定投、单笔大额、中途赎回、负收益、零收益、期末持仓清零、与账户一重叠标的。"""
    rows = [
        event("2025-01-10", "09:20:00", "银证转入", "699000", "Y0000002", settle="80000.00"),
        buy("2025-01-15", "09:40:00", "699005", 300, "20.00", "Y0000002"),
        buy("2025-02-03", "09:50:00", "699005", 200, "20.50", "Y0000002", note=LONG_NOTE),
        buy("2025-03-03", "09:55:00", "699005", 200, "19.80", "Y0000002"),
        buy("2025-04-01", "09:35:00", "699006", 1000, "12.00", "Y0000002"),
        buy("2025-05-06", "09:42:00", "699001", 600, "10.50", "Y0000002"),
        sell("2025-06-20", "10:05:00", "699005", 500, "22.00", "Y0000002", stamp="11.00"),
        event("2025-07-10", "09:30:00", "红利入账", "699005", "Y0000002", settle="250.00"),
        buy("2025-08-15", "09:30:00", "699006", 500, "11.00", "Y0000002"),
        buy("2025-08-20", "09:40:00", "699007", 1000, "5.00", "Y0000002", comm="0.00"),
        event("2025-09-02", "09:30:00", "银证转入", "699000", "Y0000002", settle="30000.00"),
        event(
            "2025-10-10",
            "14:00:00",
            "内部划转",
            "699000",
            "Y0000002",
            settle="20000.00",
            note="由示例账户一转入",
        ),
        sell("2025-11-05", "09:40:00", "699007", 1000, "5.00", "Y0000002", comm="0.00"),
        buy("2025-11-11", "09:45:00", "699005", 200, "21.00", "Y0000002"),
        sell("2025-12-22", "10:00:00", "699005", 400, "20.00", "Y0000002", stamp="8.00"),
        sell("2025-12-22", "10:01:00", "699006", 1500, "10.50", "Y0000002", stamp="15.75"),
        sell("2025-12-22", "10:02:00", "699001", 600, "10.50", "Y0000002", stamp="6.30"),
    ]
    rows[11].contract_no = TRANSFER_CONTRACT
    return rows


def build_account_c():
    """账户三：用于 merge.three_accounts。"""
    return [
        event("2025-02-20", "10:00:00", "银证转入", "699000", "Y0000003", settle="20000.00"),
        buy("2025-03-12", "09:50:00", "699005", 100, "19.50", "Y0000003"),
        sell("2025-09-18", "10:10:00", "699005", 100, "21.50", "Y0000003", stamp="2.15"),
    ]


# ---------------------------------------------------------------------------
# 样例列定义
# ---------------------------------------------------------------------------

HTSEC_COLUMNS = [
    ("证券代码", "symbol"),
    ("证券名称", "name"),
    ("成交日期", "trade_date"),
    ("成交时间", "trade_time"),
    ("成交数量", "quantity"),
    ("成交价格", "price"),
    ("成交金额", "amount"),
    ("发生金额", "settlement_amount"),
    ("操作", "business"),
    ("合同号", "contract_no"),
    ("成交号", "trade_no"),
    ("手续费", "commission"),
    ("印花税", "stamp_tax"),
    ("过户费", "transfer_fee"),
    ("其他费", "other_fee"),
    ("资金本次余额", "cash_balance"),
    ("股份本次余额", "share_balance"),
    ("股东账号", "shareholder_account"),
    ("资金账号", "account_id"),
]

HUAXI_COLUMNS = [
    ("买卖日期", "trade_date"),
    ("成交时间", "trade_time"),
    ("业务名称", "business"),
    ("证券代码", "symbol"),
    ("证券名称", "name"),
    ("成交数量", "quantity"),
    ("成交价格", "price"),
    ("成交金额", "amount"),
    ("发生金额", "settlement_amount"),
    ("手续费", "commission"),
    ("印花税", "stamp_tax"),
    ("过户费", "transfer_fee"),
    ("其他费", "other_fee"),
    ("合同号", "contract_no"),
    ("成交号", "trade_no"),
    ("股东账号", "shareholder_account"),
    ("资金账号", "account_id"),
    ("资金本次余额", "cash_balance"),
    ("股份本次余额", "share_balance"),
    ("备注", "note"),
]

GENERIC_ZH_COLUMNS = [
    ("成交日期", "trade_date"),
    ("成交时间", "trade_time"),
    ("业务名称", "business"),
    ("证券代码", "symbol"),
    ("证券名称", "name"),
    ("成交数量", "quantity"),
    ("成交价格", "price"),
    ("成交金额", "amount"),
    ("发生金额", "settlement_amount"),
    ("手续费", "commission"),
    ("印花税", "stamp_tax"),
    ("过户费", "transfer_fee"),
    ("其他费", "other_fee"),
    ("买卖方向", "direction"),
    ("合同号", "contract_no"),
    ("成交号", "trade_no"),
    ("资金账号", "account_id"),
    ("股东账号", "shareholder_account"),
]

GENERIC_ZH_NOTE_COLUMNS = GENERIC_ZH_COLUMNS + [("备注", "note")]

# 通用中文列 + 资金/股份余额：成本场景里的账户三、四需要余额才能做 T+1 勾稽。
GENERIC_ZH_BALANCE_COLUMNS = GENERIC_ZH_COLUMNS + [
    ("资金本次余额", "cash_balance"),
    ("股份本次余额", "share_balance"),
]

GENERIC_EN_COLUMNS = [
    ("trade_date", "trade_date"),
    ("trade_time", "trade_time"),
    ("business", "business"),
    ("symbol", "symbol"),
    ("name", "name"),
    ("quantity", "quantity"),
    ("price", "price"),
    ("amount", "amount"),
    ("settlement_amount", "settlement_amount"),
    ("commission", "commission"),
    ("stamp_tax", "stamp_tax"),
    ("transfer_fee", "transfer_fee"),
    ("other_fee", "other_fee"),
    ("direction", "direction"),
    ("contract_no", "contract_no"),
    ("trade_no", "trade_no"),
    ("account_id", "account_id"),
    ("shareholder_account", "shareholder_account"),
]

DIRECTION_ZH = {"buy": "买入", "sell": "卖出", "other": "其他"}


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


@dataclass
class SampleSpec:
    name: str
    encoding: str
    delimiter: object
    columns: list
    rows: list
    xlsx: bool = False
    shell: bool = False
    line_ending: str = "\n"
    thousands: bool = False
    direction_style: str = "zh"
    extra_columns: list = field(default_factory=list)


@dataclass
class SampleOut:
    name: str
    data: bytes
    sha256: str
    encoding: object
    delimiter: object
    expected_rows: list
    expected_row_errors: list
    file_error_code: object
    unknown_columns: list

    @property
    def status(self):
        if self.file_error_code:
            return "file_error"
        if self.expected_row_errors:
            return "rows_rejected"
        return "ok"


def format_cell(tx, field_name, spec):
    if field_name == "direction":
        if spec.direction_style == "en":
            return tx.direction
        return DIRECTION_ZH.get(tx.direction, "其他")
    value = getattr(tx, field_name, "")
    if spec.thousands and field_name in MONEY_FIELDS and value:
        return format(Decimal(value), ",.2f")
    return value


def _encode_lines(rows, delimiter, line_ending):
    lines = []
    for row in rows:
        if delimiter == "\t":
            lines.append(delimiter.join(row))
        else:
            buf = io.StringIO()
            writer = csv.writer(
                buf,
                delimiter=delimiter,
                lineterminator=line_ending,
                quoting=csv.QUOTE_MINIMAL,
            )
            writer.writerow(row)
            text = buf.getvalue()
            lines.append(text[: -len(line_ending)] if line_ending else text)
    return lines


def render_delimited(spec):
    header = [h for h, _ in spec.columns] + [h for h, _ in spec.extra_columns]
    body = []
    for index, tx in enumerate(spec.rows):
        cells = [format_cell(tx, f, spec) for _, f in spec.columns]
        for _, values in spec.extra_columns:
            cells.append(values[index])
        if spec.shell:
            cells = ['="{}"'.format(cell) for cell in cells]
        body.append(cells)
    if spec.shell:
        header = ['="{}"'.format(cell) for cell in header]
    lines = _encode_lines([header] + body, spec.delimiter, spec.line_ending)
    text = spec.line_ending.join(lines) + spec.line_ending
    return text.encode(spec.encoding), lines


def _col_letter(index):
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _xml_cell(ref, value, numeric):
    escaped = escape(value)
    if numeric:
        return '<c r="{}"><v>{}</v></c>'.format(ref, escaped)
    return '<c r="{}" t="inlineStr"><is><t xml:space="preserve">{}</t></is></c>'.format(ref, escaped)


def render_xlsx(spec):
    header = [h for h, _ in spec.columns]
    rows_xml = []
    row_no = 1
    cells_xml = []
    for index, header_text in enumerate(header, 1):
        cells_xml.append(_xml_cell(_col_letter(index) + str(row_no), header_text, False))
    rows_xml.append('<row r="1">{}</row>'.format("".join(cells_xml)))
    for tx in spec.rows:
        row_no += 1
        cells_xml = []
        for index, (_, field_name) in enumerate(spec.columns, 1):
            value = format_cell(tx, field_name, spec)
            if value == "":
                continue
            numeric = field_name in NUMERIC_FIELDS
            cells_xml.append(
                _xml_cell(_col_letter(index) + str(row_no), value, numeric)
            )
        rows_xml.append('<row r="{}">{}</row>'.format(row_no, "".join(cells_xml)))

    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>{}</sheetData></worksheet>".format("".join(rows_xml))
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )
    entries = [
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", root_rels),
        ("xl/workbook.xml", workbook),
        ("xl/_rels/workbook.xml.rels", workbook_rels),
        ("xl/worksheets/sheet1.xml", sheet),
        ("xl/styles.xml", styles),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 0
            info.external_attr = 0o600 << 16
            archive.writestr(info, text.encode("utf-8"))
    return buffer.getvalue(), None


def render_sample(spec):
    if spec.xlsx:
        return render_xlsx(spec)
    return render_delimited(spec)


# ---------------------------------------------------------------------------
# 期望行
# ---------------------------------------------------------------------------


def expected_row(tx, spec, sha, line_no, row_index):
    present = {field_name for _, field_name in spec.columns}
    extra = {}
    for _, field_name in spec.columns:
        if field_name not in EXTRA_TYPES:
            continue
        kind = EXTRA_TYPES[field_name]
        raw = getattr(tx, field_name, "")
        if kind == "money":
            extra[field_name] = fen(raw or "0")
        elif kind == "quantity":
            extra[field_name] = canonical(raw or "0")
        else:
            extra[field_name] = raw or ""
    for header, values in spec.extra_columns:
        extra[header] = values[row_index]

    alias, shareholder, registered = account_meta(tx.account_id)
    return {
        "source_file": spec.name,
        "source_sha256": sha,
        "source_line_no": line_no,
        "account_id": tx.account_id,
        # 未登记账户没有别名可查——真值必须只写"输入 + 规则"能推出的东西，
        # 猜一个别名就等于把投机写进基准。
        "account_alias": alias if registered else None,
        "shareholder_account": shareholder,
        "account_status": "registered" if registered else "pending",
        "account_pending_reason": None if registered else "ACCOUNT_NOT_REGISTERED",
        "symbol": tx.symbol,
        "name": tx.name,
        "business": tx.business,
        "trade_date": tx.trade_date,
        "trade_time": tx.trade_time or None,
        "quantity": canonical(tx.quantity) if tx.quantity != "" else None,
        "price": canonical(tx.price) if tx.price != "" else None,
        "amount": fen(tx.amount) if tx.amount != "" else None,
        "settlement_amount": (
            fen(tx.settlement_amount) if tx.settlement_amount != "" else None
        ),
        "commission": fen(tx.commission) if "commission" in present else None,
        "stamp_tax": fen(tx.stamp_tax) if "stamp_tax" in present else None,
        "transfer_fee": fen(tx.transfer_fee) if "transfer_fee" in present else None,
        "other_fee": fen(tx.other_fee) if "other_fee" in present else None,
        "direction": tx.direction,
        "contract_no": tx.contract_no or None,
        "trade_no": tx.trade_no or None,
        "extra": extra,
    }


def sample_out(spec):
    data, _ = render_sample(spec)
    sha = sha256_hex(data)
    rows = [
        expected_row(tx, spec, sha, index + 2, index)
        for index, tx in enumerate(spec.rows)
    ]
    return SampleOut(
        name=spec.name,
        data=data,
        sha256=sha,
        encoding=spec.encoding,
        delimiter=spec.delimiter,
        expected_rows=rows,
        expected_row_errors=[],
        file_error_code=None,
        unknown_columns=[header for header, _ in spec.extra_columns],
    )


def defect_out(name, data, encoding, delimiter, expected_rows=(), errors=(), file_error=None, unknown_columns=()):
    sha = sha256_hex(data)
    return SampleOut(
        name=name,
        data=data,
        sha256=sha,
        encoding=encoding,
        delimiter=delimiter,
        expected_rows=list(expected_rows),
        expected_row_errors=list(errors),
        file_error_code=file_error,
        unknown_columns=list(unknown_columns),
    )


# ---------------------------------------------------------------------------
# 缺陷样例
# ---------------------------------------------------------------------------


def build_missing_column(rows):
    columns = [pair for pair in GENERIC_ZH_COLUMNS if pair[1] != "quantity"]
    spec = SampleSpec(
        name="generic-demo-004-missing-column.csv",
        encoding="utf-8",
        delimiter=",",
        columns=columns,
        rows=rows,
        direction_style="zh",
    )
    data, _ = render_delimited(spec)
    return defect_out(
        spec.name,
        data,
        "utf-8",
        "comma",
        file_error="MISSING_REQUIRED_COLUMN",
    )


def build_misaligned(rows):
    name = "generic-demo-005-misaligned.csv"
    spec = SampleSpec(
        name=name,
        encoding="utf-8",
        delimiter=",",
        columns=GENERIC_ZH_COLUMNS,
        rows=rows[:2],
        direction_style="zh",
    )
    header = [h for h, _ in GENERIC_ZH_COLUMNS]
    lines = _encode_lines([header], ",", "\n")
    for tx in rows[:2]:
        cells = [format_cell(tx, f, spec) for _, f in GENERIC_ZH_COLUMNS]
        lines += _encode_lines([cells], ",", "\n")
    bad_short = "2025-01-06,09:31:02,证券买入"
    bad_long = (
        "2025-01-06,09:31:02,证券买入,699001,示例科技甲,100,10.00,1000.00,"
        "-1005.00,5.00,0.00,0.00,0.00,买入,HT-A-9998,T-A-9998,Y0000001,"
        "A000000001,extra-one,extra-two"
    )
    lines += [bad_short, bad_long]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    sha = sha256_hex(data)
    expected = [expected_row(tx, spec, sha, index + 2, index) for index, tx in enumerate(rows[:2])]
    errors = [
        {
            "source_file": name,
            "source_sha256": sha,
            "source_line_no": 4,
            "reason_code": "COLUMN_COUNT_MISMATCH",
            "raw_line": bad_short,
        },
        {
            "source_file": name,
            "source_sha256": sha,
            "source_line_no": 5,
            "reason_code": "COLUMN_COUNT_MISMATCH",
            "raw_line": bad_long,
        },
    ]
    return defect_out(name, data, "utf-8", "comma", expected, errors)


def build_formula(rows):
    name = "generic-demo-003-formula.csv"
    spec = SampleSpec(
        name=name,
        encoding="utf-8",
        delimiter=",",
        columns=GENERIC_ZH_COLUMNS,
        rows=rows[:2],
        shell=True,
        direction_style="zh",
    )
    header = ['="{}"'.format(h) for h, _ in GENERIC_ZH_COLUMNS]
    lines = _encode_lines([header], ",", "\n")
    for tx in rows[:2]:
        cells = ['="{}"'.format(format_cell(tx, f, spec)) for _, f in GENERIC_ZH_COLUMNS]
        lines += _encode_lines([cells], ",", "\n")
    bad_cells = ['="{}"'.format(format_cell(rows[2], f, spec)) for _, f in GENERIC_ZH_COLUMNS]
    bad_cells[4] = '="示例"转债丁"'
    lines += _encode_lines([bad_cells], ",", "\n")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    sha = sha256_hex(data)
    expected = [expected_row(tx, spec, sha, index + 2, index) for index, tx in enumerate(rows[:2])]
    errors = [
        {
            "source_file": name,
            "source_sha256": sha,
            "source_line_no": 4,
            "reason_code": "INVALID_CELL_SHELL",
            "raw_line": lines[3],
        }
    ]
    return defect_out(name, data, "utf-8", "comma", expected, errors)


def build_empty():
    return defect_out(
        "generic-demo-006-empty.csv", b"", None, None, file_error="EMPTY_FILE"
    )


def build_header_only():
    header = [h for h, _ in GENERIC_ZH_COLUMNS]
    lines = _encode_lines([header], ",", "\n")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    return defect_out(
        "generic-demo-007-header-only.csv",
        data,
        "utf-8",
        "comma",
        file_error="HEADER_ONLY",
    )


def build_undecodable():
    data = b"\xff\xff\xff\xff"
    return defect_out(
        "generic-demo-010-undecodable.csv",
        data,
        None,
        None,
        file_error="ENCODING_UNDECODABLE",
    )


# ---------------------------------------------------------------------------
# 规则包
# ---------------------------------------------------------------------------


def build_aliases():
    aliases = {
        "symbol": ["证券代码", "股票代码", "代码", "symbol"],
        "name": ["证券名称", "股票名称", "名称", "name"],
        "trade_date": ["成交日期", "买卖日期", "发生日期", "日期", "trade_date"],
        "trade_time": ["成交时间", "时间", "trade_time"],
        "business": ["业务名称", "操作", "业务类型", "操作类型", "business"],
        "quantity": ["成交数量", "数量", "成交量", "quantity"],
        "price": ["成交价格", "成交价", "价格", "price"],
        "amount": ["成交金额", "成交额", "amount"],
        "settlement_amount": ["发生金额", "清算金额", "settlement_amount"],
        "commission": ["手续费", "佣金", "commission"],
        "stamp_tax": ["印花税", "stamp_tax"],
        "transfer_fee": ["过户费", "transfer_fee"],
        "other_fee": ["其他费", "其他费用", "other_fee"],
        "direction": ["买卖方向", "操作方向", "方向", "direction"],
        "contract_no": ["合同号", "合同编号", "委托编号", "contract_no"],
        "trade_no": ["成交号", "成交编号", "trade_no"],
        "account_id": ["资金账号", "资金账户", "account_id"],
        "shareholder_account": ["股东账号", "股东账户", "shareholder_account"],
        "cash_balance": ["资金本次余额", "资金余额", "cash_balance"],
        "share_balance": ["股份本次余额", "股份余额", "share_balance"],
        "note": ["备注", "note"],
    }
    return {
        "format": "ia.aliases",
        "format_version": 1,
        "contract_version": CONTRACT_VERSION,
        "canonical_fields": list(aliases),
        "required_fields": ["symbol", "trade_date", "business", "quantity"],
        "extra_fields": EXTRA_TYPES,
        "aliases": aliases,
        "direction_patterns": {
            "buy": ["证券买入", "买入", "buy"],
            "sell": ["证券卖出", "卖出", "sell"],
        },
        "unknown_column_policy": "keep_in_extra_and_record",
    }


def build_params():
    return {
        "format": "ia.params",
        "format_version": 1,
        "contract_version": CONTRACT_VERSION,
        "encoding_order": ["utf-8-sig", "utf-8", "gb18030"],
        "date_formats": ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"],
        "time_formats": ["%H:%M:%S", "%H:%M", "%H%M%S"],
        "money_unit": "fen",
        "money_max_scale": 2,
        "price_max_scale": 4,
        "quantity_max_scale": 4,
        "cost_methods": ["fifo", "wavg"],
        "xirr": {
            "initial_guess": "0.1",
            "rate_tolerance": "1e-12",
            "max_iterations": 100,
            "year_basis_days": 365,
            "bisection_lower": "-0.999999",
            "bisection_upper": "1000000",
            "npv_tolerance": "1e-6",
            "max_bisection_iterations": 200,
        },
        "twr": {
            "default_basis": "cashflow",
            "bases": ["cashflow", "daily"],
            "missing_valuation": "VALUATION_MISSING",
        },
        "fee_allocation": {
            "mode": "as_reported",
            "unallocated": "keep_separate",
            "note": "费用只按交割单实际列值入账；不臆造费率。",
        },
        "account_status_values": ["registered", "pending"],
        "tolerances_source": "rules/manifest.json#bench_thresholds",
    }


def build_corporate_actions():
    return {
        "format": "ia.corporate_actions",
        "format_version": 1,
        "contract_version": CONTRACT_VERSION,
        "unmatched_policy": "pending_confirmation",
        "non_action_patterns": ["银证转入", "银证转出", "内部划转", "银行转证券", "证券转银行"],
        "rules": [
            {
                "rule_id": "ca.dividend",
                "business_name_patterns": ["红利入账", "股息入账", "现金红利"],
                "event_type": "dividend",
                "expected_kinds": ["cash_event"],
                "cash_effect": "inflow",
                "share_factor": None,
                "cash_per_share": None,
                "tax_rule": "as_reported",
                "requires_manual_confirm": False,
                "evidence_hint": "现金红利只动资金余额，不改持仓；金额取交割单发生金额列",
            },
            {
                "rule_id": "ca.stock_dividend",
                "business_name_patterns": ["送股入账", "送股"],
                "event_type": "stock_dividend",
                "expected_kinds": ["receipt_free"],
                "cash_effect": "none",
                "share_factor": None,
                "cash_per_share": None,
                "tax_rule": "as_reported",
                "requires_manual_confirm": False,
                "evidence_hint": "份额增量取交割单数量列；零成本份额，总成本不变",
            },
            {
                "rule_id": "ca.rights_issue",
                "business_name_patterns": ["配股"],
                "event_type": "rights_issue",
                "expected_kinds": ["receipt_paid"],
                "cash_effect": "outflow",
                "share_factor": None,
                "cash_per_share": None,
                "tax_rule": "as_reported",
                "requires_manual_confirm": False,
                "evidence_hint": "配股价格取交割单成交价格列；对价 + 费用计入成本",
            },
            {
                "rule_id": "ca.split",
                "business_name_patterns": ["拆分", "份额折算"],
                "event_type": "split",
                "expected_kinds": ["receipt_free"],
                "cash_effect": "none",
                "share_factor": None,
                "cash_per_share": None,
                "tax_rule": "none",
                "requires_manual_confirm": False,
                "evidence_hint": "拆分比例取交割单数量增量；零成本份额，总成本不变",
            },
            {
                "rule_id": "ca.reinvested_dividend",
                "business_name_patterns": ["红利再投", "分红再投"],
                "event_type": "reinvested_dividend",
                "expected_kinds": ["receipt_valued"],
                "cash_effect": "none",
                "share_factor": None,
                "cash_per_share": None,
                "tax_rule": "as_reported",
                "requires_manual_confirm": False,
                "evidence_hint": "再投价格取交割单成交价格列；按对价计入成本，无现金流出",
            },
        ],
    }


def build_industries():
    return {
        "format": "ia.industries",
        "format_version": 1,
        "contract_version": CONTRACT_VERSION,
        "default": "未分类",
        "industries": {code: SYMBOLS[code][1] for code in sorted(SYMBOLS)},
        "note": "用户可在本表覆盖；未登记代码落 default 并在报告中可见。",
    }


def build_accounts():
    return {
        "format": "ia.accounts",
        "format_version": 1,
        "contract_version": CONTRACT_VERSION,
        "accounts": [
            {
                "account_id": account_id,
                "alias": ACCOUNTS[account_id][0],
                "shareholder_accounts": [ACCOUNTS[account_id][1]],
            }
            for account_id in sorted(ACCOUNTS)
        ],
        "note": "样例账户登记；用户真实账户用 --accounts 显式传入，覆盖本文件。",
    }


def build_manifest():
    surfaces = []
    for surface in SURFACE_ORDER:
        surfaces.append(
            {
                "surface": surface,
                "total": 1,
                "sampled": 1,
                "evidence": COVERAGE[surface],
            }
        )
    return {
        "format": "ia.manifest",
        "format_version": 1,
        "contract_version": CONTRACT_VERSION,
        "generator": {
            "tool": GENERATOR_TOOL,
            "seed": SEED,
            "frozen": True,
        },
        "bench_thresholds": {
            "position_quantity_tolerance_shares": 1,
            "position_amount_tolerance_cny": 0.01,
            "xirr_relative_tolerance": 0.0001,
            "split_conservation_tolerance_cny": 0.01,
            "transfer_offset_tolerance_cny": 0.01,
        },
        "coverage": {
            "note": (
                "total/sampled 逐形态登记，要求分子 = 分母。M1 的 sampled 指该形态已在样例中注入；"
                "导入形态的逐行列值期望已写入 data/truth/import_truth.json，"
                "成本形态的逐笔期望（双口径）已写入 data/truth/cost_truth.json（M2），"
                "公司行为形态的逐事件期望（含 before/after 与待确认集合）已写入 "
                "data/truth/actions_truth.json（M3），归因形态的期望值随 M4 追加。"
            ),
            "surfaces": surfaces,
        },
    }


# ---------------------------------------------------------------------------
# 成本真值（B2）：独立实现，不 import 引擎
# ---------------------------------------------------------------------------

COST_SCENARIO_FILES = (
    "htsec-demo-001.xlsx",
    "huaxi-demo-001.xls",
    "generic-demo-013-account-c.csv",
    "generic-demo-015-no-fee-columns.csv",
)
FEE_COLUMNS = ("commission", "stamp_tax", "transfer_fee", "other_fee")


def _frac(value):
    return Fraction(Decimal(str(value)))


def _half_up(value):
    """四舍五入到整数，.5 远离零（与 Decimal ROUND_HALF_UP 同义）。"""
    if value >= 0:
        return (2 * value.numerator + value.denominator) // (2 * value.denominator)
    positive = -value
    return -((2 * positive.numerator + positive.denominator) // (2 * positive.denominator))


def _frac_text(value):
    """规范十进制文本（最多 4 位小数）；分母不整除 10^4 即判生成器缺陷。"""
    scaled = value * 10000
    if scaled.denominator != 1:
        raise ValueError("数量精度超过 4 位小数：{}".format(value))
    return canonical(Decimal(scaled.numerator) / Decimal(10000))


def _row_kind(tx, amount_fen, settle_fen):
    quantity = _frac(tx.quantity or "0")
    if tx.direction == "buy":
        return "trade_buy"
    if tx.direction == "sell":
        return "trade_sell"
    if quantity < 0:
        return "unclassified"
    if quantity > 0:
        if amount_fen:
            if not settle_fen:
                return "receipt_valued"
            if settle_fen < 0:
                return "receipt_paid"
            return "unclassified"
        return "receipt_free"
    if settle_fen:
        return "cash_event"
    return "noop"


def _fees_for(tx, present_fields, kind, amount_fen, settle_fen):
    reported = {name: (fen(getattr(tx, name) or "0") if name in present_fields else None)
                for name in FEE_COLUMNS}
    missing = [name for name, value in reported.items() if value is None]
    if not missing:
        return sum(reported.values()), "as_reported", reported
    if len(missing) == len(FEE_COLUMNS):
        if kind in ("trade_buy", "trade_sell", "receipt_paid") and settle_fen is not None:
            base = (-amount_fen) if kind in ("trade_buy", "receipt_paid") else amount_fen
            implied = base - settle_fen
            if implied >= 0:
                return implied, "derived_from_settlement", reported
        return 0, "not_reported", reported
    return sum(value for value in reported.values() if value is not None), "partially_reported", reported


class _TruthBook(object):
    """真值侧账本：``Fraction`` 精确数量 + 整数分成本。"""

    def __init__(self, method):
        self.method = method
        self.lots = []  # [Fraction quantity, int cost_fen]
        self.quantity = Fraction(0)
        self.cost_fen = 0

    def add(self, quantity, cost_fen):
        if quantity <= 0:
            return
        self.quantity += quantity
        self.cost_fen += int(cost_fen)
        if self.method == "fifo":
            self.lots.append([quantity, int(cost_fen)])
        else:
            self.lots.append([quantity, int(cost_fen)])

    def add_free(self, quantity):
        if quantity <= 0:
            return
        if self.method == "fifo" and self.lots:
            total = self.quantity
            remaining = quantity
            for index, lot in enumerate(self.lots):
                if index == len(self.lots) - 1:
                    part = remaining
                else:
                    part = Fraction((quantity * lot[0] / total * 10000) // 1, 10000)
                lot[0] += part
                remaining -= part
            self.quantity += quantity
        else:
            self.quantity += quantity
            self.lots.append([quantity, 0])

    def take(self, quantity):
        matched = []
        if self.method == "fifo":
            remaining = quantity
            cost = 0
            while remaining > 0 and self.lots:
                lot = self.lots[0]
                if lot[0] <= remaining:
                    cost += lot[1]
                    matched.append([_frac_text(lot[0]), int(lot[1])])
                    remaining -= lot[0]
                    self.lots.pop(0)
                else:
                    part = _half_up(Fraction(lot[1]) * remaining / lot[0])
                    lot[0] -= remaining
                    lot[1] -= part
                    cost += part
                    matched.append([_frac_text(remaining), int(part)])
                    remaining = Fraction(0)
            self.quantity -= quantity
            if self.quantity < 0:
                self.quantity = Fraction(0)
            self.cost_fen = sum(lot[1] for lot in self.lots)
            return cost, matched
        held = self.quantity
        if quantity >= held:
            cost = self.cost_fen
            if held > 0:
                matched.append([_frac_text(held), int(cost)])
            self.quantity = Fraction(0)
            self.cost_fen = 0
            return cost, matched
        part = _half_up(Fraction(self.cost_fen) * quantity / held)
        self.quantity = held - quantity
        self.cost_fen -= part
        matched.append([_frac_text(quantity), int(part)])
        return part, matched


def _scenario_rows(cost_inputs):
    """把场景文件展开成引擎输入序（date + time + 行号）。"""
    rows = []
    for name in COST_SCENARIO_FILES:
        columns, txs = cost_inputs[name]
        present = {field_name for _, field_name in columns}
        for index, tx in enumerate(txs):
            amount_fen = fen(tx.amount) if tx.amount != "" else None
            settle_fen = fen(tx.settlement_amount) if tx.settlement_amount != "" else None
            quantity = _frac(tx.quantity or "0")
            kind = _row_kind(tx, amount_fen, settle_fen)
            fees_fen, fee_source, reported = _fees_for(
                tx, present, kind, amount_fen, settle_fen
            )
            rows.append(
                {
                    "source_file": name,
                    "source_line_no": index + 2,
                    "account_id": tx.account_id,
                    "symbol": tx.symbol,
                    "business": tx.business,
                    "trade_date": tx.trade_date,
                    "trade_time": tx.trade_time or "",
                    "kind": kind,
                    "quantity": quantity,
                    "amount_fen": amount_fen,
                    "settlement_fen": settle_fen,
                    "fees_fen": fees_fen,
                    "fee_source": fee_source,
                    "fee_detail": reported,
                    "cash_balance_fen": (
                        fen(tx.cash_balance) if "cash_balance" in present else None
                    ),
                    "share_balance": (
                        _frac(tx.share_balance) if "share_balance" in present else None
                    ),
                }
            )
    rows.sort(key=lambda row: (row["trade_date"], row["trade_time"], row["source_line_no"], row["source_file"]))
    return rows


def _simulate(rows, method):
    accounts = {}
    events = []
    snapshots = []
    current_date = None

    def snapshot(date):
        if date is None:
            return
        positions = []
        for account_key in sorted(accounts):
            entry = accounts[account_key]
            for symbol in sorted(entry["books"]):
                book = entry["books"][symbol]
                if book.quantity <= 0:
                    continue
                positions.append(
                    {
                        "account_id": entry["account_id"],
                        "symbol": symbol,
                        "quantity": _frac_text(book.quantity),
                        "cost_fen": int(book.cost_fen),
                    }
                )
        snapshots.append({"date": date, "positions": positions})

    for row in rows:
        if current_date is None:
            current_date = row["trade_date"]
        elif row["trade_date"] != current_date:
            snapshot(current_date)
            current_date = row["trade_date"]

        account_key = row["account_id"] or "unregistered"
        entry = accounts.setdefault(
            account_key,
            {
                "account_id": row["account_id"],
                "books": {},
                "realized_net_fen": 0,
                "fees_fen": 0,
                "cash_in_fen": 0,
                "cash_out_fen": 0,
                "net_cash_flow_fen": 0,
                "rows": 0,
                "opening_cash_fen": None,
                "cumulative_settle_fen": 0,
            },
        )
        entry["rows"] += 1
        book = entry["books"].get(row["symbol"])
        if book is None and row["kind"] in (
            "trade_buy",
            "trade_sell",
            "receipt_paid",
            "receipt_valued",
            "receipt_free",
        ):
            book = _TruthBook(method)
            entry["books"][row["symbol"]] = book

        before_quantity = book.quantity if book is not None else Fraction(0)
        before_cost = book.cost_fen if book is not None else 0
        realized = None
        matched = []
        if book is not None:
            if row["kind"] in ("trade_buy", "receipt_paid"):
                book.add(row["quantity"], (row["amount_fen"] or 0) + row["fees_fen"])
            elif row["kind"] == "receipt_valued":
                book.add(row["quantity"], row["amount_fen"] or 0)
            elif row["kind"] == "receipt_free":
                book.add_free(row["quantity"])
            elif row["kind"] == "trade_sell":
                cost, matched = book.take(row["quantity"])
                realized = ((row["amount_fen"] or 0) - row["fees_fen"]) - cost
                entry["realized_net_fen"] += realized

        settle = row["settlement_fen"] or 0
        entry["cumulative_settle_fen"] += settle
        entry["net_cash_flow_fen"] = entry["cumulative_settle_fen"]
        if settle > 0:
            entry["cash_in_fen"] += settle
        elif settle < 0:
            entry["cash_out_fen"] += -settle
        entry["fees_fen"] += row["fees_fen"]
        if row["cash_balance_fen"] is not None and entry["opening_cash_fen"] is None:
            entry["opening_cash_fen"] = row["cash_balance_fen"] - entry["cumulative_settle_fen"]

        expected_cash = (
            None
            if entry["opening_cash_fen"] is None
            else entry["opening_cash_fen"] + entry["cumulative_settle_fen"]
        )
        events.append(
            {
                "key": [row["source_file"], row["source_line_no"]],
                "symbol": row["symbol"],
                "account_id": row["account_id"],
                "kind": row["kind"],
                "position_before_quantity": _frac_text(before_quantity),
                "position_before_cost_fen": int(before_cost),
                "position_after_quantity": _frac_text(book.quantity if book is not None else Fraction(0)),
                "position_after_cost_fen": int(book.cost_fen if book is not None else 0),
                "realized_fen": realized,
                "matched_lots": matched,
                "expected_cash_balance_fen": expected_cash,
            }
        )

    snapshot(current_date)

    account_rows = []
    positions = []
    for account_key in sorted(accounts):
        entry = accounts[account_key]
        open_cost = 0
        for symbol in sorted(entry["books"]):
            book = entry["books"][symbol]
            open_cost += book.cost_fen if book.quantity > 0 else 0
            positions.append(
                {
                    "account_id": entry["account_id"],
                    "symbol": symbol,
                    "quantity": _frac_text(book.quantity),
                    "cost_fen": int(book.cost_fen),
                    "is_open": book.quantity > 0,
                }
            )
        account_rows.append(
            {
                "account_id": entry["account_id"],
                "rows": entry["rows"],
                "realized_net_fen": int(entry["realized_net_fen"]),
                "position_cost_fen": int(open_cost),
                "fees_fen": int(entry["fees_fen"]),
                "cash_in_fen": int(entry["cash_in_fen"]),
                "cash_out_fen": int(entry["cash_out_fen"]),
                "net_cash_flow_fen": int(entry["net_cash_flow_fen"]),
                "opening_cash_fen": entry["opening_cash_fen"],
            }
        )

    totals = {
        "position_cost_fen": sum(item["position_cost_fen"] for item in account_rows),
        "realized_net_fen": sum(item["realized_net_fen"] for item in account_rows),
        "fees_total_fen": sum(item["fees_fen"] for item in account_rows),
        "open_positions": sum(1 for item in positions if item["is_open"]),
    }
    return {
        "events": events,
        "accounts": account_rows,
        "positions": positions,
        "totals": totals,
        "daily_snapshots": snapshots,
    }


COST_SURFACE_EVIDENCE = {
    "cost.same_day_multi_fill": [
        ["htsec-demo-001.xlsx", 3],
        ["htsec-demo-001.xlsx", 4],
    ],
    "cost.partial_sell": [
        ["htsec-demo-001.xlsx", 6],
        ["htsec-demo-001.xlsx", 7],
    ],
    "cost.clear_then_rebuy": [
        ["htsec-demo-001.xlsx", 6],
        ["htsec-demo-001.xlsx", 7],
        ["htsec-demo-001.xlsx", 8],
    ],
    "cost.after_split_sell": [
        ["htsec-demo-001.xlsx", 15],
        ["htsec-demo-001.xlsx", 16],
    ],
    "cost.fifo_vs_wavg_divergence": [
        ["htsec-demo-001.xlsx", 3],
        ["htsec-demo-001.xlsx", 4],
        ["htsec-demo-001.xlsx", 6],
        ["htsec-demo-001.xlsx", 7],
    ],
    "cost.fee_not_itemized": [
        ["generic-demo-015-no-fee-columns.csv", 3],
        ["generic-demo-015-no-fee-columns.csv", 4],
    ],
}


def build_cost_truth(cost_inputs):
    rows = _scenario_rows(cost_inputs)
    fifo = _simulate(rows, "fifo")
    wavg = _simulate(rows, "wavg")

    fifo_events = {(tuple(e["key"])): e for e in fifo["events"]}
    divergences = []
    for event in wavg["events"]:
        first = fifo_events[tuple(event["key"])]
        if first["realized_fen"] is None and event["realized_fen"] is None:
            continue
        if first["realized_fen"] != event["realized_fen"]:
            divergences.append(
                {
                    "source_file": event["key"][0],
                    "source_line_no": event["key"][1],
                    "account_id": event["account_id"],
                    "symbol": event["symbol"],
                    "fifo_realized_fen": first["realized_fen"],
                    "wavg_realized_fen": event["realized_fen"],
                    "difference_fen": event["realized_fen"] - first["realized_fen"],
                }
            )

    row_facts = []
    for row in rows:
        row_facts.append(
            {
                "key": [row["source_file"], row["source_line_no"]],
                "account_id": row["account_id"],
                "symbol": row["symbol"],
                "business": row["business"],
                "trade_date": row["trade_date"],
                "trade_time": row["trade_time"] or None,
                "kind": row["kind"],
                "quantity": _frac_text(row["quantity"]),
                "amount_fen": row["amount_fen"],
                "settlement_fen": row["settlement_fen"],
                "fees_fen": row["fees_fen"],
                "fee_source": row["fee_source"],
                "fee_detail_fen": row["fee_detail"],
                "cash_balance_fen": row["cash_balance_fen"],
            }
        )

    return {
        "format": "ia.truth.cost",
        "format_version": 1,
        "contract_version": CONTRACT_VERSION,
        "generator": {"tool": GENERATOR_TOOL, "seed": SEED},
        "note": (
            "只存输入与期望，不含 observed_*；由 tools/gen_data.py 独立实现（Fraction 精确数量，"
            "整数分成本），不 import 引擎。scenario.files 是成本真值的导入范围：其余样例是这些"
            "账户的子集/副本，全部一起导入会重复计数。"
        ),
        "scenario": {
            "files": list(COST_SCENARIO_FILES),
            "row_count": len(rows),
            "cost_methods": ["fifo", "wavg"],
        },
        "surface_evidence": COST_SURFACE_EVIDENCE,
        "rows": row_facts,
        "methods": {"fifo": fifo, "wavg": wavg},
        "divergences": divergences,
    }


# ---------------------------------------------------------------------------
# 公司行为真值（B3）：独立实现，不 import 引擎
# ---------------------------------------------------------------------------

ACTIONS_SCENARIO_FILES = (
    "htsec-demo-001.xlsx",
    "huaxi-demo-001.xls",
    "generic-demo-013-account-c.csv",
    "generic-demo-015-no-fee-columns.csv",
)

# 规则表语义的第二份声明？不——这里只是**真值侧**的独立解释：
# 期望值必须能由「规则包内容 + 行形态」独立推出，而不是读引擎输出。
ACTION_RULES = (
    {
        "rule_id": "ca.dividend",
        "event_type": "dividend",
        "patterns": ("红利入账", "股息入账", "现金红利"),
        "expected_kinds": ("cash_event",),
        "cash_effect": "inflow",
        "action": "cash_only",
    },
    {
        "rule_id": "ca.stock_dividend",
        "event_type": "stock_dividend",
        "patterns": ("送股入账", "送股"),
        "expected_kinds": ("receipt_free",),
        "cash_effect": "none",
        "action": "free_shares",
    },
    {
        "rule_id": "ca.rights_issue",
        "event_type": "rights_issue",
        "patterns": ("配股",),
        "expected_kinds": ("receipt_paid",),
        "cash_effect": "outflow",
        "action": "paid_acquisition",
    },
    {
        "rule_id": "ca.split",
        "event_type": "split",
        "patterns": ("拆分", "份额折算"),
        "expected_kinds": ("receipt_free",),
        "cash_effect": "none",
        "action": "free_shares",
    },
    {
        "rule_id": "ca.reinvested_dividend",
        "event_type": "reinvested_dividend",
        "patterns": ("红利再投", "分红再投"),
        "expected_kinds": ("receipt_valued",),
        "cash_effect": "none",
        "action": "valued_acquisition",
    },
)

NON_ACTION_PATTERNS = ("银证转入", "银证转出", "内部划转", "银行转证券", "证券转银行")

ACTIONS_SURFACE_EVIDENCE = {
    "corporate_action.dividend": [
        ["htsec-demo-001.xlsx", 11],
        ["huaxi-demo-001.xls", 9],
        ["generic-demo-015-no-fee-columns.csv", 5],
    ],
    "corporate_action.stock_dividend": [["htsec-demo-001.xlsx", 12]],
    "corporate_action.rights_issue": [["htsec-demo-001.xlsx", 14]],
    "corporate_action.split": [["htsec-demo-001.xlsx", 15]],
    "corporate_action.reinvested_dividend": [["htsec-demo-001.xlsx", 13]],
    "corporate_action.unrecognized_pending": [["htsec-demo-001.xlsx", 17]],
}


def _unit_cost_text(cost_fen, quantity):
    """展示值：6 位小数四舍五入；权威值仍是整数分。"""
    if quantity <= 0:
        return None
    value = Fraction(cost_fen, 1) / quantity
    scaled = _half_up(value * 1000000)
    text = canonical(Decimal(scaled) / Decimal(1000000))
    return text


def _match_actions(business):
    text = business or ""
    matched = []
    for rule in ACTION_RULES:
        for pattern in rule["patterns"]:
            if text == pattern or pattern in text:
                matched.append((rule, pattern))
                break
    return matched


def _non_action(business):
    text = business or ""
    for pattern in NON_ACTION_PATTERNS:
        if text == pattern or pattern in text:
            return pattern
    return None


def _cash_effect_of(settle_fen):
    if settle_fen and settle_fen > 0:
        return "inflow"
    if settle_fen and settle_fen < 0:
        return "outflow"
    return "none"


def _truth_apply(book, kind, quantity, amount_fen, fees_fen):
    if kind in ("trade_buy", "receipt_paid"):
        book.add(quantity, (amount_fen or 0) + fees_fen)
    elif kind == "receipt_valued":
        book.add(quantity, amount_fen or 0)
    elif kind == "receipt_free":
        book.add_free(quantity)
    elif kind == "trade_sell":
        book.take(quantity)


def _state(book):
    quantity = book.quantity if book is not None else Fraction(0)
    cost = int(book.cost_fen) if book is not None else 0
    return {
        "quantity": _frac_text(quantity),
        "cost_fen": cost,
        "unit_cost": _unit_cost_text(cost, quantity),
    }


def _build_actions_truth_for(rows, method):
    """真值侧逐行复算：规则匹配 + 行形态推进 + 调整前后两组数。"""
    books = {}
    events = []
    pending = []
    non_actions = []
    by_type = {}
    other_rows = 0

    for row in rows:
        key = (row["account_id"], row["symbol"])
        if row["kind"] in ("trade_buy", "trade_sell", "receipt_paid",
                           "receipt_valued", "receipt_free"):
            book = books.get(key)
            if book is None:
                book = _TruthBook(method)
                books[key] = book
        else:
            book = books.get(key)

        if row["kind"] in ("trade_buy", "trade_sell"):
            _truth_apply(book, row["kind"], row["quantity"],
                         row["amount_fen"], row["fees_fen"])
            continue

        other_rows += 1
        before = _state(book)
        _truth_apply(book, row["kind"], row["quantity"],
                     row["amount_fen"], row["fees_fen"])
        after = _state(book)
        cash_effect = _cash_effect_of(row["settlement_fen"])
        matches = _match_actions(row["business"])
        item_key = [row["source_file"], row["source_line_no"]]

        if not matches:
            pattern = _non_action(row["business"])
            if pattern is not None:
                non_actions.append(
                    {
                        "source_file": row["source_file"],
                        "source_line_no": row["source_line_no"],
                        "business": row["business"],
                        "matched_pattern": pattern,
                    }
                )
            else:
                pending.append(
                    {
                        "source_file": row["source_file"],
                        "source_line_no": row["source_line_no"],
                        "reason": "NO_RULE_MATCH",
                        "kind": row["kind"],
                        "business": row["business"],
                    }
                )
            continue

        if len(matches) > 1:
            pending.append(
                {
                    "source_file": row["source_file"],
                    "source_line_no": row["source_line_no"],
                    "reason": "AMBIGUOUS_RULES",
                    "kind": row["kind"],
                    "business": row["business"],
                }
            )
            continue

        rule, pattern = matches[0]
        if row["kind"] not in rule["expected_kinds"]:
            pending.append(
                {
                    "source_file": row["source_file"],
                    "source_line_no": row["source_line_no"],
                    "reason": "KIND_NOT_EXPECTED",
                    "kind": row["kind"],
                    "business": row["business"],
                }
            )
            continue
        if cash_effect != rule["cash_effect"]:
            pending.append(
                {
                    "source_file": row["source_file"],
                    "source_line_no": row["source_line_no"],
                    "reason": "CASH_EFFECT_MISMATCH",
                    "kind": row["kind"],
                    "business": row["business"],
                }
            )
            continue

        by_type[rule["event_type"]] = by_type.get(rule["event_type"], 0) + 1
        events.append(
            {
                "key": item_key,
                "rule_id": rule["rule_id"],
                "event_type": rule["event_type"],
                "kind": row["kind"],
                "business": row["business"],
                "cash_effect": cash_effect,
                "source_file": row["source_file"],
                "source_line_no": row["source_line_no"],
                "before": before,
                "after": after,
            }
        )

    adjusted_positions = []
    for key in sorted(books):
        book = books[key]
        adjusted_positions.append(
            {
                "account_key": key[0],
                "symbol": key[1],
                "quantity": _frac_text(book.quantity),
                "cost_fen": int(book.cost_fen),
            }
        )

    return {
        "events": events,
        "pending": pending,
        "non_actions": non_actions,
        "adjusted_positions": adjusted_positions,
        "by_event_type": [
            {"event_type": name, "count": by_type[name]} for name in sorted(by_type)
        ],
        "other_rows": other_rows,
    }


def build_actions_truth(cost_inputs):
    rows = _scenario_rows(cost_inputs)
    fifo = _build_actions_truth_for(rows, "fifo")
    wavg = _build_actions_truth_for(rows, "wavg")

    return {
        "format": "ia.truth.actions",
        "format_version": 1,
        "contract_version": CONTRACT_VERSION,
        "generator": {"tool": GENERATOR_TOOL, "seed": SEED},
        "note": (
            "只存输入与期望，不含 observed_*；由 tools/gen_data.py 独立实现（Fraction 精确数量，"
            "整数分成本），不 import 引擎。scenario.files 与 cost_truth 同源，避免重复计数。"
            "pending 是**必须**进人工确认队列的行；non_actions 是规则表明确判定为非公司行为的资金划转。"
        ),
        "scenario": {
            "files": list(ACTIONS_SCENARIO_FILES),
            "row_count": len(rows),
            "cost_methods": ["fifo", "wavg"],
        },
        "surface_evidence": ACTIONS_SURFACE_EVIDENCE,
        "methods": {"fifo": fifo, "wavg": wavg},
    }


def build_samples(rng):
    account_a = build_account_a()
    account_b = build_account_b()
    account_c = build_account_c()

    number_rows(account_a, "HT-A", rng)
    number_rows(account_b, "HX-B", rng)
    number_rows(account_c, "GX-C", rng)
    attach_balances(account_a)
    attach_balances(account_b)
    attach_balances(account_c)

    specs = []
    specs.append(
        SampleSpec(
            name="htsec-demo-001.xlsx",
            encoding="binary-xlsx",
            delimiter=None,
            columns=HTSEC_COLUMNS,
            rows=account_a,
            xlsx=True,
        )
    )
    specs.append(
        SampleSpec(
            name="huaxi-demo-001.xls",
            encoding="gb18030",
            delimiter="\t",
            columns=HUAXI_COLUMNS,
            rows=account_b,
            shell=True,
            line_ending="\r\n",
        )
    )
    specs.append(
        SampleSpec(
            name="generic-demo-001.csv",
            encoding="utf-8",
            delimiter=",",
            columns=GENERIC_ZH_COLUMNS,
            rows=account_a[1:5],
        )
    )
    specs.append(
        SampleSpec(
            name="generic-demo-002.csv",
            encoding="gb18030",
            delimiter=",",
            columns=GENERIC_EN_COLUMNS,
            rows=[account_b[4], account_b[5], account_b[6]],
            direction_style="en",
        )
    )
    specs.append(
        SampleSpec(
            name="generic-demo-008-unknown-column.csv",
            encoding="utf-8",
            delimiter=",",
            columns=GENERIC_ZH_NOTE_COLUMNS,
            rows=[account_a[1], account_a[2]],
            extra_columns=[("自定义字段X", ["X-A", "X-B"])],
        )
    )
    specs.append(
        SampleSpec(
            name="generic-demo-009-thousands.csv",
            encoding="utf-8",
            delimiter=",",
            columns=GENERIC_ZH_NOTE_COLUMNS,
            rows=[account_b[2], account_b[3]],
            thousands=True,
        )
    )
    specs.append(
        SampleSpec(
            name="generic-demo-011.tsv",
            encoding="utf-8",
            delimiter="\t",
            columns=GENERIC_ZH_COLUMNS,
            rows=[account_a[5], account_a[6]],
        )
    )
    specs.append(
        SampleSpec(
            name="generic-demo-012-bom.csv",
            encoding="utf-8-sig",
            delimiter=",",
            columns=GENERIC_ZH_COLUMNS,
            rows=[account_a[8], account_a[9]],
        )
    )
    specs.append(
        SampleSpec(
            name="generic-demo-013-account-c.csv",
            encoding="utf-8",
            delimiter=",",
            columns=GENERIC_ZH_BALANCE_COLUMNS,
            rows=account_c,
        )
    )

    outs = [sample_out(spec) for spec in specs]
    outs.append(build_formula(account_a[4:7]))
    outs.append(build_missing_column(account_a[1:3]))
    outs.append(build_misaligned(account_a[3:7]))
    outs.append(build_empty())
    outs.append(build_header_only())
    outs.append(build_undecodable())
    outs.append(build_unregistered_account())
    outs.append(build_no_fee_columns())

    order = [
        "htsec-demo-001.xlsx",
        "huaxi-demo-001.xls",
        "generic-demo-001.csv",
        "generic-demo-002.csv",
        "generic-demo-003-formula.csv",
        "generic-demo-004-missing-column.csv",
        "generic-demo-005-misaligned.csv",
        "generic-demo-006-empty.csv",
        "generic-demo-007-header-only.csv",
        "generic-demo-008-unknown-column.csv",
        "generic-demo-009-thousands.csv",
        "generic-demo-010-undecodable.csv",
        "generic-demo-011.tsv",
        "generic-demo-012-bom.csv",
        "generic-demo-013-account-c.csv",
        "generic-demo-014-unregistered.csv",
        "generic-demo-015-no-fee-columns.csv",
    ]
    by_name = {out.name: out for out in outs}
    assert set(by_name) == set(order), set(by_name) ^ set(order)
    cost_inputs = {
        "htsec-demo-001.xlsx": (HTSEC_COLUMNS, account_a),
        "huaxi-demo-001.xls": (HUAXI_COLUMNS, account_b),
        "generic-demo-013-account-c.csv": (GENERIC_ZH_BALANCE_COLUMNS, account_c),
        "generic-demo-015-no-fee-columns.csv": (NO_FEE_COLUMNS, no_fee_rows()),
    }
    return [by_name[name] for name in order], cost_inputs


NO_FEE_COLUMNS = [pair for pair in GENERIC_ZH_BALANCE_COLUMNS if pair[1] not in FEE_COLUMNS]


def no_fee_rows():
    """费用四列全部缺席的行；账户用 Y0000004，自带独立资金余额链。"""
    rows = [
        event("2025-03-20", "09:20:00", "银证转入", "699000", "Y0000004", settle="30000.00"),
        buy("2025-03-24", "09:40:00", "699004", 200, "100.00", "Y0000004",
            comm="5.00", transfer="0.20"),
        sell("2025-03-28", "10:10:00", "699004", 100, "110.00", "Y0000004",
             comm="5.00", stamp="11.00", transfer="0.10"),
        event("2025-04-02", "09:30:00", "红利入账", "699004", "Y0000004", settle="50.00"),
    ]
    number_rows(rows, "GX-NF", SplitMix64(SEED ^ 0x0F))
    attach_balances(rows)
    return rows


def build_no_fee_columns():
    """费用列缺席：四项费用全为 ``None``，费用只能由发生金额反推并标注来源。"""
    spec = SampleSpec(
        name="generic-demo-015-no-fee-columns.csv",
        encoding="utf-8",
        delimiter=",",
        columns=NO_FEE_COLUMNS,
        rows=no_fee_rows(),
    )
    return sample_out(spec)


def build_unregistered_account():
    """账号在白名单内但未登记到 accounts.json：行必须入库并落"待确认"。"""
    name = "generic-demo-014-unregistered.csv"
    rows = [
        event("2025-02-14", "09:35:00", "银证转入", "699000", "Y0000009", settle="5000.00"),
        buy("2025-02-14", "09:40:00", "699006", 200, "12.50", "Y0000009"),
    ]
    number_rows(rows, "GX-D", SplitMix64(SEED ^ 0x0D))
    attach_balances(rows)
    spec = SampleSpec(
        name=name,
        encoding="utf-8",
        delimiter=",",
        columns=GENERIC_ZH_COLUMNS,
        rows=rows,
    )
    return sample_out(spec)


def build_valuation():
    header = "date,symbol,name,quantity,price"
    rows = [
        "2025-12-31,699001,示例科技甲,500,10.20",
        "2025-12-31,699003,示例基金丙,2000,0.65",
        "2025-12-31,699004,示例转债丁,100,108.00",
    ]
    data = ("\n".join([header] + rows) + "\n").encode("utf-8")
    return data


def build_truth(sample_outs):
    files = []
    for out in sample_outs:
        files.append(
            {
                "source_file": out.name,
                "sha256": out.sha256,
                "size_bytes": len(out.data),
                "encoding": out.encoding,
                "delimiter": out.delimiter,
                "status": out.status,
                "file_error_code": out.file_error_code,
                "unknown_columns": sorted(out.unknown_columns),
                "expected_row_count": len(out.expected_rows),
                "expected_rows": out.expected_rows,
                "expected_error_count": len(out.expected_row_errors),
                "expected_row_errors": out.expected_row_errors,
            }
        )
    return {
        "format": "ia.truth.import",
        "format_version": 1,
        "contract_version": CONTRACT_VERSION,
        "generator": {"tool": GENERATOR_TOOL, "seed": SEED},
        "note": (
            "只存输入与期望，不含 observed_*；本文件由 tools/gen_data.py 与样例同源生成。"
            "M1 覆盖导入层；成本 / 公司行为期望随 M2–M3 追加，归因随 M4 追加。"
        ),
        "files": files,
    }


def validate_model(sample_outs):
    problems = []
    for out in sample_outs:
        for row in out.expected_rows:
            symbol = row["symbol"]
            if symbol not in SYMBOLS:
                problems.append("{}: 代码 {} 不在白名单".format(out.name, symbol))
            if not row["name"].startswith("示例"):
                problems.append("{}: 名称 {} 缺 示例 前缀".format(out.name, row["name"]))
            account_id = row["account_id"]
            if account_id not in ACCOUNTS and account_id not in UNREGISTERED_ACCOUNTS:
                problems.append("{}: 资金账号 {} 不在白名单".format(out.name, account_id))
            elif row["shareholder_account"] != account_meta(account_id)[1]:
                problems.append("{}: 股东账号不匹配".format(out.name))
    if problems:
        raise ValueError("\n".join(problems))


# ---------------------------------------------------------------------------
# 生成与校验
# ---------------------------------------------------------------------------


def generate(data_dir):
    data_dir = Path(data_dir)
    for sub in ("rules", "samples", "truth"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)

    rng = SplitMix64(SEED)
    samples, cost_inputs = build_samples(rng)
    validate_model(samples)

    rules = {
        "accounts.json": build_accounts(),
        "aliases.json": build_aliases(),
        "corporate_actions.json": build_corporate_actions(),
        "industries.json": build_industries(),
        "manifest.json": build_manifest(),
        "params.json": build_params(),
    }
    written = []
    for name in sorted(rules):
        path = data_dir / "rules" / name
        payload = json_bytes(rules[name])
        path.write_bytes(payload)
        written.append((path, payload))

    for out in samples:
        path = data_dir / "samples" / out.name
        path.write_bytes(out.data)
        written.append((path, out.data))

    valuation = build_valuation()
    path = data_dir / "samples" / "valuation-demo-001.csv"
    path.write_bytes(valuation)
    written.append((path, valuation))

    truth_payload = json_bytes(build_truth(samples))
    path = data_dir / "truth" / "import_truth.json"
    path.write_bytes(truth_payload)
    written.append((path, truth_payload))

    cost_truth_payload = json_bytes(build_cost_truth(cost_inputs))
    path = data_dir / "truth" / "cost_truth.json"
    path.write_bytes(cost_truth_payload)
    written.append((path, cost_truth_payload))

    actions_truth_payload = json_bytes(build_actions_truth(cost_inputs))
    path = data_dir / "truth" / "actions_truth.json"
    path.write_bytes(actions_truth_payload)
    written.append((path, actions_truth_payload))

    return written


def _artifact_files(data_dir):
    data_dir = Path(data_dir)
    files = {}
    for sub in ("rules", "samples", "truth"):
        base = data_dir / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                files[str(path.relative_to(data_dir)).replace("\\", "/")] = path
    return files


def compare_artifacts(target_dir, regenerated_dir):
    target = _artifact_files(target_dir)
    fresh = _artifact_files(regenerated_dir)
    diffs = []
    for rel in sorted(set(target) | set(fresh)):
        if rel not in fresh:
            diffs.append("UNEXPECTED {}".format(rel))
        elif rel not in target:
            diffs.append("MISSING {}".format(rel))
        elif target[rel].read_bytes() != fresh[rel].read_bytes():
            diffs.append("MISMATCH {}".format(rel))
    return diffs


def _decode_sample(data_dir, name, encoding):
    raw = (Path(data_dir) / "samples" / name).read_bytes()
    if name.endswith(".xlsx"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return "\n".join(
                archive.read(member).decode("utf-8")
                for member in sorted(archive.namelist())
                if member.endswith(".xml")
            )
    if encoding is None:
        return None
    return raw.decode(encoding)


DIGIT8 = re.compile(r"(?<!\d)\d{8,}(?!\d)")
ACCOUNT_RE = re.compile(r"(?<![A-Za-z0-9])([YA])(\d{7,9})(?![0-9])")
WHITELIST_TOKEN = re.compile(r"(?<![A-Za-z0-9])[YA]0{6,9}[1-9](?![0-9])")


def scan_privacy(text, where):
    """隐私扫描：白名单账号先换成占位符，再查残余的 8 位以上纯数字串。

    白名单账号自身（A + 9 位数字）必然含 9 位数字串，不能一刀切判红；
    先把**精确白名单形态**摘掉，剩下的长数字串才是真问题。
    """
    issues = []
    scrubbed = WHITELIST_TOKEN.sub("<ACCOUNT>", text)
    for match in DIGIT8.finditer(scrubbed):
        issues.append("{}: 出现 8 位以上纯数字串 {!r}".format(where, match.group(0)))
    for match in ACCOUNT_RE.finditer(text):
        prefix, digits = match.group(1), match.group(2)
        if prefix == "Y" and len(digits) != 7:
            issues.append("{}: 资金账号形态非法 {!r}".format(where, match.group(0)))
        elif prefix == "A" and len(digits) != 9:
            issues.append("{}: 股东账号形态非法 {!r}".format(where, match.group(0)))
        elif not 1 <= int(digits) <= 9:
            issues.append("{}: 账号不在白名单 {!r}".format(where, match.group(0)))
    return issues


def _walk_json_strings(obj, skip_sha=True):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if skip_sha and "sha256" in key:
                continue
            for item in _walk_json_strings(value, skip_sha):
                yield item
    elif isinstance(obj, list):
        for value in obj:
            for item in _walk_json_strings(value, skip_sha):
                yield item
    elif isinstance(obj, str):
        yield obj


def shape_check(data_dir):
    data_dir = Path(data_dir)
    issues = []

    def load_json(rel):
        path = data_dir / rel
        if not path.is_file():
            issues.append("缺少文件 {}".format(rel))
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            issues.append("{} 不是合法 JSON：{}".format(rel, exc))
            return None

    manifest = load_json("rules/manifest.json")
    if manifest is not None:
        if manifest.get("format") != "ia.manifest":
            issues.append("manifest.format 不匹配")
        thresholds = manifest.get("bench_thresholds", {})
        for key in (
            "position_quantity_tolerance_shares",
            "position_amount_tolerance_cny",
            "xirr_relative_tolerance",
            "split_conservation_tolerance_cny",
            "transfer_offset_tolerance_cny",
        ):
            if not isinstance(thresholds.get(key), (int, float)) or thresholds.get(key, 0) <= 0:
                issues.append("manifest 门槛 {} 缺失或非正".format(key))
        surfaces = manifest.get("coverage", {}).get("surfaces", [])
        names = [item.get("surface") for item in surfaces]
        if tuple(names) != SURFACE_ORDER:
            issues.append("manifest 覆盖度形态集合与生成器不一致")
        for item in surfaces:
            surface = item.get("surface")
            total = item.get("total")
            sampled = item.get("sampled")
            if total != sampled:
                issues.append("{}: total {} != sampled {}".format(surface, total, sampled))
            if not total:
                issues.append("{}: total 为 0".format(surface))
            evidence_items = item.get("evidence") or []
            if not evidence_items:
                issues.append("{}: 缺 evidence".format(surface))
            for entry in evidence_items:
                sample = entry.get("sample")
                if not sample or not (data_dir / "samples" / sample).is_file():
                    issues.append("{}: evidence 指向的样例不存在 {!r}".format(surface, sample))

    aliases = load_json("rules/aliases.json")
    if aliases is not None:
        seen = {}
        for field_name, names in aliases.get("aliases", {}).items():
            for label in names:
                if label in seen and seen[label] != field_name:
                    issues.append("列名别名重复：{} 同时属于 {} 与 {}".format(label, seen[label], field_name))
                seen[label] = field_name
        for required in aliases.get("required_fields", []):
            if required not in aliases.get("aliases", {}):
                issues.append("required_field {} 没有别名表".format(required))
        if not aliases.get("direction_patterns", {}).get("buy"):
            issues.append("direction_patterns 缺 buy")

    params = load_json("rules/params.json")
    if params is not None:
        if params.get("encoding_order") != ["utf-8-sig", "utf-8", "gb18030"]:
            issues.append("encoding_order 必须为 K6 顺序")
        if params.get("money_unit") != "fen":
            issues.append("money_unit 必须为 fen")
        if params.get("xirr", {}).get("year_basis_days") != 365:
            issues.append("xirr.year_basis_days 必须为 365")

    accounts = load_json("rules/accounts.json")
    if accounts is not None:
        for item in accounts.get("accounts", []):
            account_id = item.get("account_id")
            if account_id not in ACCOUNTS:
                issues.append("账号 {} 不在白名单".format(account_id))
            for shareholder in item.get("shareholder_accounts", []):
                if not re.fullmatch(r"A00000000[1-9]", shareholder):
                    issues.append("股东账号 {} 不在白名单".format(shareholder))

    industries = load_json("rules/industries.json")
    if industries is not None:
        for code in SYMBOLS:
            if code not in industries.get("industries", {}):
                issues.append("行业表缺代码 {}".format(code))
        if industries.get("default") != "未分类":
            issues.append("行业表 default 必须为 未分类")

    actions = load_json("rules/corporate_actions.json")
    if actions is not None:
        event_types = {rule.get("event_type") for rule in actions.get("rules", [])}
        for required in (
            "dividend",
            "stock_dividend",
            "rights_issue",
            "split",
            "reinvested_dividend",
        ):
            if required not in event_types:
                issues.append("公司行为规则缺 event_type {}".format(required))
        if actions.get("unmatched_policy") != "pending_confirmation":
            issues.append("未识别公司行为必须进待确认队列")
        if not actions.get("non_action_patterns"):
            issues.append("公司行为规则缺 non_action_patterns（资金划转无法与非行为区分）")
        seen_patterns = {}
        for rule in actions.get("rules", []):
            rule_id = rule.get("rule_id")
            if not rule.get("expected_kinds"):
                issues.append("规则 {} 缺 expected_kinds".format(rule_id))
            if rule.get("cash_effect") not in ("inflow", "outflow", "none"):
                issues.append("规则 {} 的 cash_effect 非法".format(rule_id))
            for pattern in rule.get("business_name_patterns", []):
                if pattern in seen_patterns:
                    issues.append(
                        "业务名模式 {} 同时属于 {} 与 {}".format(
                            pattern, seen_patterns[pattern], rule_id
                        )
                    )
                seen_patterns[pattern] = rule_id

    truth = load_json("truth/import_truth.json")
    if truth is not None:
        if truth.get("format") != "ia.truth.import":
            issues.append("truth.format 不匹配")
        texts = []
        for entry in truth.get("files", []):
            name = entry.get("source_file")
            path = data_dir / "samples" / name
            if not path.is_file():
                issues.append("真值引用的样例不存在 {}".format(name))
                continue
            if sha256_hex(path.read_bytes()) != entry.get("sha256"):
                issues.append("样例字节与真值 sha256 不符 {}".format(name))
            if entry.get("expected_row_count") != len(entry.get("expected_rows", [])):
                issues.append("{}: expected_row_count 不一致".format(name))
            if entry.get("expected_error_count") != len(entry.get("expected_row_errors", [])):
                issues.append("{}: expected_error_count 不一致".format(name))
            status = entry.get("status")
            if status not in ("ok", "rows_rejected", "file_error"):
                issues.append("{}: 非法 status {}".format(name, status))
            if status == "file_error" and not entry.get("file_error_code"):
                issues.append("{}: file_error 缺 code".format(name))
            if status != "file_error" and entry.get("file_error_code"):
                issues.append("{}: 非 file_error 却带 code".format(name))
        for text in _walk_json_strings(truth):
            texts.append(text)
        for text in texts:
            issues += scan_privacy(text, "truth")

    # 样例隐私扫描（xlsx 只扫 XML 文本，不扫 zip 二进制）
    if truth is not None:
        for entry in truth.get("files", []):
            name = entry.get("source_file")
            try:
                text = _decode_sample(data_dir, name, entry.get("encoding"))
            except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
                issues.append("{}: 无法解码用于扫描：{}".format(name, exc))
                continue
            if text is not None:
                issues += scan_privacy(text, name)

    issues += _shape_check_cost_truth(data_dir, truth)
    issues += _shape_check_actions_truth(data_dir, truth)

    return issues


def _shape_check_cost_truth(data_dir, import_truth):
    """M2 真值纪律：双口径齐全、逐笔可复算、费用来源合法、无 observed_*。"""
    issues = []
    path = data_dir / "truth" / "cost_truth.json"
    if not path.is_file():
        return ["缺少文件 truth/cost_truth.json"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return ["truth/cost_truth.json 不是合法 JSON：{}".format(exc)]

    if payload.get("format") != "ia.truth.cost":
        issues.append("cost_truth.format 不匹配")
    scenario = payload.get("scenario") or {}
    files = scenario.get("files") or []
    if not files:
        issues.append("cost_truth.scenario.files 为空")
    for name in files:
        if not (data_dir / "samples" / name).is_file():
            issues.append("cost 真值引用的样例不存在 {}".format(name))
    methods = payload.get("methods") or {}
    if sorted(methods) != ["fifo", "wavg"]:
        issues.append("cost_truth.methods 必须同时含 fifo 与 wavg")
    for name, data in sorted(methods.items()):
        rows = payload.get("rows") or []
        if len(data.get("events", [])) != len(rows):
            issues.append("cost_truth.{}: events 数 {} != rows 数 {}".format(
                name, len(data.get("events", [])), len(rows)))
        positions = data.get("positions") or []
        open_cost = sum(item["cost_fen"] for item in positions if item["is_open"])
        if open_cost != data.get("totals", {}).get("position_cost_fen"):
            issues.append("cost_truth.{}: 未平仓成本之和不等于 totals".format(name))
        realized = sum(item["realized_net_fen"] for item in data.get("accounts", []))
        if realized != data.get("totals", {}).get("realized_net_fen"):
            issues.append("cost_truth.{}: 分账户已实现之和不等于 totals".format(name))
        for event in data.get("events", []):
            if event["kind"] == "trade_sell" and event["realized_fen"] is None:
                issues.append("cost_truth.{}: 卖出 {} 缺 realized".format(name, event["key"]))
            if event["position_after_cost_fen"] < 0:
                issues.append("cost_truth.{}: 持仓成本为负 {}".format(name, event["key"]))

    for row in payload.get("rows") or []:
        if row["fee_source"] not in (
            "as_reported",
            "derived_from_settlement",
            "partially_reported",
            "not_reported",
        ):
            issues.append("cost_truth: 非法 fee_source {}".format(row["fee_source"]))
        if row["fee_source"] in ("as_reported", "partially_reported"):
            detail = row["fee_detail_fen"]
            if sum(value for value in detail.values() if value is not None) != row["fees_fen"]:
                issues.append("cost_truth: 费用明细之和与 fees_fen 不符 {}".format(row["key"]))
        expected = row.get("cash_balance_fen")
        if expected is not None and not isinstance(expected, int):
            issues.append("cost_truth: cash_balance_fen 非整数 {}".format(row["key"]))

    # 每个成本形态必须指向真实存在的行，否则"逐笔期望"只是口头承诺
    keys = {tuple(row["key"]) for row in payload.get("rows") or []}
    evidence = payload.get("surface_evidence") or {}
    for surface in SURFACE_ORDER:
        if not surface.startswith("cost."):
            continue
        entries = evidence.get(surface) or []
        if not entries:
            issues.append("cost_truth.surface_evidence 缺 {}".format(surface))
            continue
        for key in entries:
            if tuple(key) not in keys:
                issues.append(
                    "cost_truth.surface_evidence {} 指向不存在的行 {}".format(surface, key)
                )

    if import_truth is not None:
        known = {entry["source_file"] for entry in import_truth.get("files", [])}
        for name in files:
            if name not in known:
                issues.append("cost 真值引用的样例未登记进导入真值 {}".format(name))
    return issues


def _shape_check_actions_truth(data_dir, import_truth):
    """M3 真值纪律：五类事件齐全、调整前后可逆、待确认 100% 落库、无 observed_*。"""
    issues = []
    path = data_dir / "truth" / "actions_truth.json"
    if not path.is_file():
        return ["缺少文件 truth/actions_truth.json"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return ["truth/actions_truth.json 不是合法 JSON：{}".format(exc)]

    if payload.get("format") != "ia.truth.actions":
        issues.append("actions_truth.format 不匹配")
    scenario = payload.get("scenario") or {}
    files = scenario.get("files") or []
    if not files:
        issues.append("actions_truth.scenario.files 为空")
    for name in files:
        if not (data_dir / "samples" / name).is_file():
            issues.append("actions 真值引用的样例不存在 {}".format(name))
    methods = payload.get("methods") or {}
    if sorted(methods) != ["fifo", "wavg"]:
        issues.append("actions_truth.methods 必须同时含 fifo 与 wavg")

    required_types = {rule["event_type"] for rule in ACTION_RULES}
    for name, data in sorted(methods.items()):
        counts = {item["event_type"]: item["count"] for item in data.get("by_event_type", [])}
        missing = sorted(required_types - set(counts))
        if missing:
            issues.append(
                "actions_truth.{}: 缺事件类型 {}".format(name, ", ".join(missing))
            )
        seen = set()
        for event in data.get("events", []):
            key = tuple(event["key"])
            if key in seen:
                issues.append("actions_truth.{}: 事件重复 {}".format(name, key))
            seen.add(key)
            before = event["before"]
            after = event["after"]
            if Decimal(before["quantity"]) > Decimal(after["quantity"]) and event["kind"] == "receipt_free":
                issues.append(
                    "actions_truth.{}: 零成本份额却减少数量 {}".format(name, key)
                )
            for side in ("before", "after"):
                state = event[side]
                if state["cost_fen"] < 0:
                    issues.append(
                        "actions_truth.{}: {} 成本为负 {}".format(name, side, key)
                    )
        pending = data.get("pending") or []
        if not pending:
            issues.append(
                "actions_truth.{}: 待确认队列为空——样例必须注入无法识别的行".format(name)
            )
        for item in pending:
            if not item.get("reason"):
                issues.append("actions_truth.{}: 待确认项缺 reason".format(name))

    # 每个公司行为形态必须指向真实存在的行：五类事件指向 events，
    # ``unrecognized_pending`` 指向 pending（待确认队列本身就是它的证据）。
    event_keys = set()
    pending_keys = set()
    for data in methods.values():
        event_keys |= {tuple(event["key"]) for event in data.get("events", [])}
        pending_keys |= {
            (item["source_file"], item["source_line_no"])
            for item in data.get("pending", [])
        }
    evidence = payload.get("surface_evidence") or {}
    for surface in SURFACE_ORDER:
        if not surface.startswith("corporate_action."):
            continue
        entries = evidence.get(surface) or []
        if not entries:
            issues.append("actions_truth.surface_evidence 缺 {}".format(surface))
            continue
        known = pending_keys if surface.endswith("unrecognized_pending") else event_keys
        for key in entries:
            if tuple(key) not in known:
                issues.append(
                    "actions_truth.surface_evidence {} 指向不存在的行 {}".format(
                        surface, key
                    )
                )

    if import_truth is not None:
        known = {entry["source_file"] for entry in import_truth.get("files", [])}
        for name in files:
            if name not in known:
                issues.append("actions 真值引用的样例未登记进导入真值 {}".format(name))
    return issues


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="gen_data.py",
        description="生成 M1 合成数据（规则包 / 样例 / 导入真值），并做形态与字节双层校验。",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_DATA_DIR),
        help="数据目录（缺省：仓库 data/）",
    )
    parser.add_argument("--check", action="store_true", help="形态校验 + 重生成逐字节比对")
    parser.add_argument("--shape-only", action="store_true", help="只做形态校验")
    args = parser.parse_args(argv)

    if args.check and args.shape_only:
        parser.error("--check 与 --shape-only 不能同时使用")

    data_dir = Path(args.out_dir).expanduser()
    if not data_dir.is_dir():
        sys.stderr.write("数据目录不存在：{}\n".format(data_dir))
        return 2

    if args.check:
        issues = shape_check(data_dir)
        if issues:
            sys.stderr.write("[shape] FAIL ({} 项)\n".format(len(issues)))
            for issue in issues:
                sys.stderr.write("  - {}\n".format(issue))
        else:
            sys.stderr.write("[shape] ok\n")

        temp_dir = Path(tempfile.mkdtemp(prefix="ia-gen-"))
        try:
            try:
                written = generate(temp_dir)
            except Exception as exc:
                sys.stderr.write("[bytes] 生成器自身失败：{}: {}\n".format(type(exc).__name__, exc))
                return 2
            diffs = compare_artifacts(data_dir, temp_dir)
        finally:
            shutil.rmtree(str(temp_dir), ignore_errors=True)

        if diffs:
            sys.stderr.write("[bytes] FAIL ({} 项)\n".format(len(diffs)))
            for diff in diffs:
                sys.stderr.write("  - {}\n".format(diff))
        else:
            sys.stderr.write("[bytes] ok ({} artifacts)\n".format(len(written)))
        return 0 if not issues and not diffs else 1

    if args.shape_only:
        issues = shape_check(data_dir)
        if issues:
            sys.stderr.write("[shape] FAIL ({} 项)\n".format(len(issues)))
            for issue in issues:
                sys.stderr.write("  - {}\n".format(issue))
            return 1
        sys.stderr.write("[shape] ok\n")
        return 0

    try:
        written = generate(data_dir)
    except Exception as exc:
        sys.stderr.write("生成失败：{}: {}\n".format(type(exc).__name__, exc))
        return 2
    sys.stderr.write("生成完成：{} 个文件写入 {}\n".format(len(written), data_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
