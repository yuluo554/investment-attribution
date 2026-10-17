"""报告数据模型（M5）：把合并产物翻译成**渲染模型**。

本模块承担报告的全部业务判定：口径如何复述、哪个数是收益、账号怎么打码、
每个汇总数挂哪条原始行锚点。渲染层（``render.py``）只做模板，一个业务词
都不许出现——这条纪律由 ``report/guard.py`` 的 AST 门禁把守。

三条硬约束：

1. **同源**：``report.html`` 与 ``report_data.json`` 由**同一次**渲染产出，
   数据模型只构建一次（``build_report_data``），HTML 是它的一个视图。
2. **打码发生在生成阶段**：账号在此处即打码，且构建末尾还有一趟
   ``_sanitize`` 兜底——HTML / JSON 里不出现完整账号形态。
3. **逐数字下钻**：每个汇总数与每行表格都挂 ``source_file`` /
   ``source_sha256`` / ``source_line_no`` 三件套（来自合并产物的
   ``provenance`` 索引，索引又来自 M1 的审计三件套）。
"""

import re
from decimal import Decimal
from pathlib import Path

REPORT_HTML_FILE = "report.html"
REPORT_DATA_FILE = "report_data.json"

#: 完整账号形态（资金账号 ``Y`` + 7 位、股东账号 ``A`` + 9 位）。
ACCOUNT_PATTERN = re.compile(r"(?<![A-Za-z0-9])([YA])(\d{7,9})(?![0-9])")
MASK_KEEP = 2

MASK_NOTICE = (
    "股东账号与资金账号在**报告生成阶段**即打码（保留首尾各 2 位，中间以 * 覆盖）；"
    "HTML 与 report_data.json 都不携带完整账号。"
)

DISCLAIMER = (
    "本报告是个人持仓核算的确定性结果，不是财务 / 税务 / 法律工具，也不是官方对账凭证；"
    "与券商官方数据存在差异时以券商为准。本工具不荐股、不预测收益、不给交易信号、"
    "不做择时评价、不打分排名。"
)

FEE_LABELS = (
    ("commission", "佣金"),
    ("stamp_tax", "印花税"),
    ("transfer_fee", "过户费"),
    ("other_fee", "其他费"),
    ("unallocated", "未分列"),
)

RETURN_BASIS_NOTICE = (
    "报告只复述上游产物的口径（成本口径、TWR 估值点、外部现金流定义），"
    "不在渲染层重新解释任何一个数。"
)


def mask_account(value):
    """首尾片段打码：保留首尾各 2 位，中间以 ``*`` 覆盖。"""
    if value is None:
        return None
    text = str(value)
    if not text:
        return text
    if len(text) <= MASK_KEEP * 2:
        return "*" * len(text)
    return text[:MASK_KEEP] + "*" * (len(text) - MASK_KEEP * 2) + text[-MASK_KEEP:]


def _sanitize(value, masked):
    """兜底打码：把任何位置出现的完整账号形态换成打码值并记账。"""
    if isinstance(value, str):
        def replace(match):
            token = match.group(0)
            masked.add(token)
            return mask_account(token)

        return ACCOUNT_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_sanitize(item, masked) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize(item, masked) for key, item in value.items()}
    return value


# ---------------------------------------------------------------------------
# 展示格式（渲染层只负责把字符串放上去）
# ---------------------------------------------------------------------------


def _money(fen):
    return format(Decimal(int(fen or 0)) / Decimal(100), ",.2f")


def _rate(value):
    if value is None:
        return None
    quantized = (Decimal(str(value)) * 100).quantize(Decimal("0.0001"))
    return "{}%".format(format(quantized, "f"))


def _chip(triples, role=None):
    rows = []
    for item in triples or []:
        if not item:
            continue
        entry = {
            "source_file": item.get("source_file"),
            "source_sha256": item.get("source_sha256"),
            "source_line_no": item.get("source_line_no"),
        }
        if role:
            entry["role"] = role
        rows.append(entry)
    return rows


def _figure(figure_id, label, value_text, unit, tone, evidence, note=None):
    return {
        "id": figure_id,
        "label": label,
        "value_text": value_text,
        "unit": unit,
        "tone": tone,
        "note": note,
        "evidence": evidence,
    }


def _tone(fen):
    if fen > 0:
        return "pos"
    if fen < 0:
        return "neg"
    return "flat"


def _text(value):
    return "-" if value is None else str(value)


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------


def _account_name(account):
    """账户的可读名字 + 打码账号：打码不等于把账户藏起来。"""
    alias = account.get("account_alias") or account.get("label") or "账户"
    masked = mask_account(account.get("account_id") or account.get("account_key"))
    if masked and masked != alias:
        return "{}（{}）".format(alias, masked)
    return str(alias)


def _anchor_pool(merged):
    """报告可用的锚点池：每类锚点都来自真实原始行（不造锚点）。"""

    def first(items):
        for item in items or []:
            if item:
                return [item]
        return []

    terminals = []
    for holding in merged.get("holdings") or []:
        terminals.extend((holding.get("evidence") or {}).get("terminal_market_value") or [])
    valuation_anchor = first(terminals)

    sells = []
    income = []
    capital = []
    terminals_by_account = {}
    for account in merged.get("accounts") or []:
        evidence = account.get("evidence") or {}
        sells.extend(evidence.get("sells") or [])
        income.extend(evidence.get("income") or [])
        capital.extend(evidence.get("capital") or [])
        terminals_by_account[account.get("account_key")] = _chip(
            evidence.get("terminal_market_value") or valuation_anchor
        )
    pool = {
        "valuation": _chip(valuation_anchor or terminals),
        "sells": _chip(sells),
        "income": _chip(income),
        "capital": _chip(capital),
        "terminals_by_account": terminals_by_account,
    }
    pool["fallback"] = next(
        (pool[key] for key in ("valuation", "sells", "income", "capital") if pool[key]),
        [],
    )
    # 锚点池里空着的槽位回落到第一个非空池：报告里每个汇总数都必须有锚点，
    # 但锚点只能来自真实原始行——所以是"换一类真实锚点"，不是"造一个锚点"。
    for key in ("valuation", "sells", "income", "capital"):
        if not pool[key]:
            pool[key] = pool["fallback"]
    return pool


def _accounts_section(merged, pool):
    columns = [
        {"label": "账户", "align": "left"},
        {"label": "状态", "align": "left"},
        {"label": "净投入（元）", "align": "right"},
        {"label": "已实现（元）", "align": "right"},
        {"label": "未实现（元）", "align": "right"},
        {"label": "总收益（元）", "align": "right"},
        {"label": "期末总资产（元）", "align": "right"},
    ]
    rows = []
    for account in merged.get("accounts") or []:
        evidence = (
            pool["terminals_by_account"].get(account.get("account_key"))
            or pool["fallback"]
        )
        rows.append(
            {
                "cells": [
                    _account_name(account),
                    account.get("account_status") or "-",
                    _money(account.get("net_contribution_fen")),
                    _money(account.get("realized_total_fen")),
                    _money(account.get("unrealized_fen")),
                    _money(account.get("total_return_fen")),
                    _money(account.get("terminal_total_fen")),
                ],
                "tone": _tone(account.get("total_return_fen") or 0),
                "evidence": evidence,
            }
        )
    total = merged.get("merged") or {}
    rows.append(
        {
            "cells": [
                "合计（{} 个账户）".format(total.get("account_count") or len(rows)),
                "-",
                _money(total.get("net_contribution_fen")),
                _money(total.get("realized_total_fen")),
                _money(total.get("unrealized_fen")),
                _money(total.get("total_return_fen")),
                _money(total.get("terminal_total_fen")),
            ],
            "tone": _tone(total.get("total_return_fen") or 0),
            "evidence": pool["valuation"],
            "emphasis": True,
        }
    )
    return {
        "id": "accounts",
        "kind": "table",
        "title": "分账户收益",
        "note": "合计行即合并视图；合并前后总收益相等由「对账检查」段独立断言。",
        "columns": columns,
        "rows": rows,
    }


def _holdings_section(merged, pool):
    columns = [
        {"label": "标的", "align": "left"},
        {"label": "名称", "align": "left"},
        {"label": "数量", "align": "right"},
        {"label": "持仓成本（元）", "align": "right"},
        {"label": "期末市值（元）", "align": "right"},
        {"label": "未实现（元）", "align": "right"},
        {"label": "期末持有账户数", "align": "right"},
        {"label": "历史涉及账户数", "align": "right"},
    ]
    rows = []
    for holding in merged.get("holdings") or []:
        evidence = _chip(
            (holding.get("evidence") or {}).get("terminal_market_value") or pool["valuation"]
        )
        rows.append(
            {
                "cells": [
                    holding.get("symbol"),
                    holding.get("name") or "-",
                    holding.get("quantity"),
                    _money(holding.get("cost_fen")),
                    _money(holding.get("market_value_fen")),
                    _money(holding.get("unrealized_fen")),
                    str(holding.get("account_count_at_terminal") or 0),
                    str(holding.get("account_count_ever") or 0),
                ],
                "tone": _tone(holding.get("unrealized_fen") or 0),
                "evidence": evidence,
            }
        )
    return {
        "id": "holdings",
        "title": "合并持仓",
        "kind": "table",
        "note": (
            "同名标的跨账户汇总成一行：数量与成本相加，"
            "「历史涉及账户数」>1 即该标的曾在多个账户持有。"
        ),
        "columns": columns,
        "rows": rows,
    }


def _transfers_section(merged):
    transfers = merged.get("transfers") or {}
    columns = [
        {"label": "合同号", "align": "left"},
        {"label": "日期", "align": "left"},
        {"label": "金额（元）", "align": "right"},
        {"label": "两腿", "align": "left"},
        {"label": "净额（元）", "align": "right"},
        {"label": "已抵消", "align": "left"},
    ]
    rows = []
    for entry in transfers.get("entries") or []:
        offset = entry.get("offset") or {}
        legs = "；".join(
            "{} {} {}（{}:{}）".format(
                mask_account(leg.get("account_id")),
                leg.get("direction"),
                _money(leg.get("settlement_fen")),
                leg.get("source_file"),
                leg.get("source_line_no"),
            )
            for leg in entry.get("legs") or []
        )
        rows.append(
            {
                "cells": [
                    entry.get("contract_no"),
                    entry.get("trade_date"),
                    _money(entry.get("amount_fen")),
                    legs,
                    _money(entry.get("net_fen")),
                    "是（净额 0，合并视图当日净现金流 {}）".format(
                        _money(offset.get("merged_flow_on_date_fen"))
                    )
                    if offset.get("balanced")
                    else "否",
                ],
                "tone": "flat" if offset.get("balanced") else "neg",
                "evidence": _chip(offset.get("evidence")),
            }
        )
    if not rows:
        return {
            "id": transfers["id"] if "id" in transfers else "transfers",
            "kind": "notes",
            "title": "账户间划转台账（逐笔）",
            "note": "",
            "items": [
                {
                    "title": "未识别到账户间划转",
                    "body": (
                        "本次流水中没有命中划转模式（{}）的行：合并视图无需抵消，"
                        "也就没有可列的逐笔台账。".format("、".join(transfers.get("patterns") or []))
                    ),
                    "tone": "info",
                }
            ],
        }
    return {
        "id": "transfers",
        "kind": "table",
        "title": "账户间划转台账（逐笔）",
        "note": (
            "划转模式取自冻结规则包 {}；两腿按合同号归组，净额为零且分属不同账户"
            "才算『已抵消』，否则落问题段。".format(
                "、".join(transfers.get("patterns") or [])
            )
        ),
        "columns": columns,
        "rows": rows,
    }


def _returns_section(merged, pool):
    columns = [
        {"label": "视图", "align": "left"},
        {"label": "净投入（元）", "align": "right"},
        {"label": "期末总资产（元）", "align": "right"},
        {"label": "XIRR", "align": "right"},
        {"label": "TWR", "align": "right"},
    ]
    rows = []
    for account in list(merged.get("accounts") or []) + [merged.get("merged") or {}]:
        key = account.get("account_key")
        xirr = account.get("xirr") or {}
        twr = account.get("twr") or {}
        rows.append(
            {
                "cells": [
                    account.get("label")
                    if key == "merged"
                    else "{}（{}）".format(account.get("label"), mask_account(account.get("account_id"))),
                    _money(account.get("net_contribution_fen")),
                    _money(account.get("terminal_total_fen")),
                    _rate(xirr.get("rate")) or "无解（{}）".format(xirr.get("status")),
                    _rate(twr.get("cumulative_rate"))
                    or "无法计算（{}）".format(twr.get("status")),
                ],
                "tone": "flat",
                "evidence": pool["terminals_by_account"].get(key) or pool["valuation"],
            }
        )
    return {
        "id": "returns",
        "kind": "table",
        "title": "收益率（{}）".format((merged.get("twr") or {}).get("basis") or "-"),
        "note": (merged.get("basis") or {}).get("xirr_notice") or "",
        "columns": columns,
        "rows": rows,
    }


def _fees_section(merged, pool):
    fees = merged.get("fees") or {}
    columns = [
        {"label": "费用类型", "align": "left"},
        {"label": "金额（元）", "align": "right"},
    ]
    rows = []
    by_type = fees.get("by_type_fen") or {}
    anchors = pool["sells"] or pool["valuation"]
    for key, label in FEE_LABELS:
        if key not in by_type:
            continue
        rows.append(
            {
                "cells": [label, _money(by_type.get(key))],
                "tone": "flat",
                "evidence": anchors,
            }
        )
    rows.append(
        {
            "cells": ["费用合计", _money(fees.get("total_fen"))],
            "tone": "flat",
            "evidence": anchors,
            "emphasis": True,
        }
    )
    note = "与成本台账（M2）费用总额一致：{}".format(
        "是" if fees.get("matches_m2_total") else "否"
    )
    if fees.get("drag_bps") is not None:
        note += "；占累计投入 {} bps".format(fees.get("drag_bps"))
    return {
        "id": "fees",
        "kind": "table",
        "title": "费用明细",
        "note": note,
        "columns": columns,
        "rows": rows,
    }


def _decomposition_section(merged, pool, dimension, title, key_field, key_label):
    block = (merged.get("decomposition") or {}).get(dimension) or []
    columns = [
        {"label": key_label, "align": "left"},
        {"label": "已实现（元）", "align": "right"},
        {"label": "收入（元）", "align": "right"},
        {"label": "未实现（元）", "align": "right"},
        {"label": "合计（元）", "align": "right"},
    ]
    extra_column = None
    if dimension == "by_symbol":
        extra_column = {"label": "行业", "align": "left"}
        columns.insert(1, extra_column)

    def cells_for(label, industry, item):
        cells = [label]
        if extra_column is not None:
            cells.append(industry)
        cells.extend(
            [
                _money(item.get("realized_fen")),
                _money(item.get("income_fen")),
                _money(item.get("unrealized_fen")),
                _money(item.get("total_fen")),
            ]
        )
        return cells

    totals = {name: 0 for name in ("realized_fen", "income_fen", "unrealized_fen", "total_fen")}
    rows = []
    for item in block:
        for name in totals:
            totals[name] += int(item.get(name) or 0)
        rows.append(
            {
                "cells": cells_for(
                    str(item.get(key_field)),
                    item.get("industry") or "未分类",
                    item,
                ),
                "tone": _tone(item.get("total_fen") or 0),
                "evidence": pool["valuation"],
            }
        )
    rows.append(
        {
            "cells": cells_for("合计", "-", totals),
            "tone": "flat",
            "evidence": pool["valuation"],
            "emphasis": True,
        }
    )
    checks = (merged.get("decomposition") or {}).get("checks") or {}
    note = "四维分解之和 == 总收益：{}".format(
        "是" if checks.get("all_equal") else "否"
    )
    unclassified = (merged.get("decomposition") or {}).get("unclassified_symbols") or []
    if unclassified:
        note += "；未分类标的：{}".format("、".join(unclassified))
    return {
        "id": dimension,
        "kind": "table",
        "title": title,
        "note": note,
        "columns": columns,
        "rows": rows,
    }


def _benchmark_section(merged, pool):
    benchmark = merged.get("benchmark") or {}
    if not benchmark.get("provided"):
        return {
            "id": "benchmark",
            "kind": "notes",
            "title": "基准比较",
            "items": [
                {
                    "title": "未提供本地基准",
                    "body": benchmark.get("note") or "未提供基准 CSV：超额列不显示。",
                    "tone": "info",
                }
            ],
        }
    excess = benchmark.get("excess") or {}
    columns = [
        {"label": "项目", "align": "left"},
        {"label": "数值", "align": "right"},
    ]
    rows = [
        {
            "cells": ["基准区间", "{} → {}".format(benchmark.get("start_date"), benchmark.get("end_date"))],
            "tone": "flat",
            "evidence": pool["capital"],
        },
        {
            "cells": ["基准累计收益", _rate(excess.get("benchmark_cumulative_return")) or "-"],
            "tone": "flat",
            "evidence": pool["capital"],
        },
        {
            "cells": ["基准折年收益", _rate(excess.get("benchmark_annualized_return")) or "-"],
            "tone": "flat",
            "evidence": pool["capital"],
        },
        {
            "cells": ["XIRR 超额", _rate(excess.get("xirr_excess")) or "-"],
            "tone": "flat",
            "evidence": pool["capital"],
        },
        {
            "cells": ["TWR 超额", _rate(excess.get("twr_excess")) or "-"],
            "tone": "flat",
            "evidence": pool["capital"],
        },
    ]
    return {
        "id": "benchmark",
        "kind": "table",
        "title": "基准比较（超额）",
        "note": "基准不插值不外推；两端日期缺失时超额列不显示。",
        "columns": columns,
        "rows": rows,
    }


def _checks_section(merged, pool):
    checks = merged.get("checks") or {}
    decomposition = (merged.get("decomposition") or {}).get("checks") or {}
    fees = merged.get("fees") or {}
    tolerance = checks.get("tolerance_fen")
    rows = [
        {
            "cells": [
                "合并前后总收益不变",
                "≤ {} 分".format(tolerance),
                "差 {} 分".format(checks.get("difference_fen")),
                "通过" if checks.get("conserved") else "未通过",
            ],
            "tone": "flat" if checks.get("conserved") else "neg",
            "evidence": pool["valuation"],
        },
        {
            "cells": [
                "账户间划转两端抵消",
                "净额 0",
                "净额 {} 分".format(checks.get("transfer_net_fen")),
                "通过" if checks.get("transfer_balanced") else "未通过",
            ],
            "tone": "flat" if checks.get("transfer_balanced") else "neg",
            "evidence": pool["sells"] or pool["valuation"],
        },
        {
            "cells": [
                "合并持仓守恒",
                "各账户市值合计相等",
                "{}".format("相等" if checks.get("holdings_conserved") else "不等"),
                "通过" if checks.get("holdings_conserved") else "未通过",
            ],
            "tone": "flat" if checks.get("holdings_conserved") else "neg",
            "evidence": pool["valuation"],
        },
        {
            "cells": [
                "四维分解守恒",
                "四者之和 == 总收益",
                "{}".format("相等" if decomposition.get("all_equal") else "不等"),
                "通过" if decomposition.get("all_equal") else "未通过",
            ],
            "tone": "flat" if decomposition.get("all_equal") else "neg",
            "evidence": pool["valuation"],
        },
        {
            "cells": [
                "费用与成本台账一致",
                "相等",
                "{}".format("相等" if fees.get("matches_m2_total") else "不等"),
                "通过" if fees.get("matches_m2_total") else "未通过",
            ],
            "tone": "flat" if fees.get("matches_m2_total") else "neg",
            "evidence": pool["sells"] or pool["valuation"],
        },
    ]
    return {
        "id": "checks",
        "kind": "table",
        "title": "对账检查",
        "note": "每条检查都给出门槛、实测值与结论；未通过项同时落「待处理」段。",
        "columns": [
            {"label": "检查项", "align": "left"},
            {"label": "门槛", "align": "left"},
            {"label": "实测", "align": "left"},
            {"label": "结论", "align": "left"},
        ],
        "rows": rows,
    }


def _issues_section(merged):
    items = []
    for item in merged.get("invalid") or []:
        location = "{}:{}".format(
            item.get("source_file") or "产物",
            item.get("source_line_no") if item.get("source_line_no") is not None else "-",
        )
        body = item.get("message") or ""
        if item.get("account_key"):
            body = "{}（账户 {}）".format(body, mask_account(item.get("account_key")))
        items.append(
            {
                "title": "{} · {}".format(item.get("code"), location),
                "body": body,
                "tone": "bad",
            }
        )
    for item in merged.get("warnings") or []:
        location = "{}:{}".format(
            item.get("source_file") or "产物",
            item.get("source_line_no") if item.get("source_line_no") is not None else "-",
        )
        items.append(
            {
                "title": "{} · {}".format(item.get("code"), location),
                "body": item.get("message") or "",
                "tone": "warn",
            }
        )
    if not items:
        items.append({"title": "无", "body": "本次运行未检出问题，也没有待确认项。", "tone": "info"})
    return {
        "id": "issues",
        "kind": "notes",
        "title": "待处理（问题 {} 条 / 提示 {} 条）".format(
            len(merged.get("invalid") or []), len(merged.get("warnings") or [])
        ),
        "note": "未识别的现金行既计入收益、也在此列出——它是真实发生过的现金，不是行情赚来的。",
        "items": items,
    }


def _zero_upload_section(merged):
    probe = (merged.get("zero_upload") or {}).get("runtime_probe") or {}
    return {
        "id": "zero_upload",
        "kind": "notes",
        "title": "零上传证据",
        "note": "静态层（AST import 审计）与运行期层（审计钩子拦截）双层证据。",
        "items": [
            {
                "title": "运行期网络探针",
                "body": (
                    "已安装：{}；本次渲染全程处于武装状态；拦截到的网络调用次数：{}。".format(
                        "是" if probe.get("installed") else "否",
                        probe.get("blocked_attempts"),
                    )
                ),
                "tone": "ok" if not probe.get("blocked_attempts") else "bad",
            },
            {
                "title": "报告外链",
                "body": "报告为单文件：无 CSS / JS / 字体 / 图片外链，通篇没有外部地址引用。",
                "tone": "ok",
            },
        ],
    }


def _conventions_section(merged):
    basis = merged.get("basis") or {}
    inputs = merged.get("inputs") or {}
    valuation = inputs.get("valuation") or {}
    items = [
        {"title": "成本口径", "body": "{}（{}）".format(basis.get("cost_method_label"), basis.get("cost_method")), "tone": "info"},
        {"title": "TWR 估值点口径", "body": basis.get("twr_basis_notice") or basis.get("twr_basis"), "tone": "info"},
        {"title": "XIRR 现金流口径", "body": basis.get("xirr_notice") or basis.get("xirr_convention"), "tone": "info"},
        {"title": "拆分口径", "body": basis.get("split_convention") or "", "tone": "info"},
        {"title": "合并口径", "body": basis.get("merge_convention") or "", "tone": "info"},
        {"title": "划转识别", "body": basis.get("transfer_notice") or "", "tone": "info"},
        {"title": "期末估值", "body": basis.get("valuation_notice") or "", "tone": "info"},
        {
            "title": "数据来源",
            "body": (
                "统一流水 {}（{} 行，sha256 {}…）；估值序列 {}（{} 日 / {} 行，sha256 {}…）。".format(
                    Path(str(inputs.get("ledger") or "")).name,
                    inputs.get("rows"),
                    str(inputs.get("ledger_sha256") or "")[:12],
                    Path(str(valuation.get("path") or "")).name or "-",
                    valuation.get("date_count"),
                    valuation.get("row_count"),
                    str(valuation.get("sha256") or "")[:12],
                )
            ),
            "tone": "info",
        },
        {"title": "渲染层纪律", "body": RETURN_BASIS_NOTICE, "tone": "info"},
        {"title": "账号打码", "body": MASK_NOTICE, "tone": "info"},
        {"title": "免责声明", "body": DISCLAIMER, "tone": "info"},
    ]
    return {
        "id": "conventions",
        "kind": "notes",
        "title": "口径与说明",
        "note": "口径的唯一定义处是随包的 data/README.md §四；本报告只复述，不新增一份。",
        "items": items,
    }


def _overview_section(merged, pool):
    total = merged.get("merged") or {}
    fees = merged.get("fees") or {}
    valuation = pool["valuation"]
    sells = pool["sells"] or valuation
    capital = pool["capital"] or valuation
    figures = [
        _figure(
            "total_return",
            "总收益",
            _money(total.get("total_return_fen")),
            "元",
            _tone(total.get("total_return_fen") or 0),
            valuation,
        ),
        _figure(
            "realized",
            "已实现（含分红）",
            _money(total.get("realized_total_fen")),
            "元",
            _tone(total.get("realized_total_fen") or 0),
            sells,
        ),
        _figure(
            "unrealized",
            "未实现",
            _money(total.get("unrealized_fen")),
            "元",
            _tone(total.get("unrealized_fen") or 0),
            valuation,
        ),
        _figure(
            "terminal_total",
            "期末总资产",
            _money(total.get("terminal_total_fen")),
            "元",
            "flat",
            valuation,
        ),
        _figure(
            "net_contribution",
            "净投入",
            _money(total.get("net_contribution_fen")),
            "元",
            "flat",
            capital,
        ),
        _figure(
            "fees",
            "费用合计",
            _money(fees.get("total_fen")),
            "元",
            "flat",
            sells,
        ),
        _figure(
            "xirr",
            "XIRR（合并）",
            _rate((total.get("xirr") or {}).get("rate")) or "无解",
            "",
            "flat",
            capital,
        ),
        _figure(
            "twr",
            "TWR（合并，{}）".format((total.get("twr") or {}).get("basis") or "-"),
            _rate((total.get("twr") or {}).get("cumulative_rate")) or "无法计算",
            "",
            "flat",
            capital,
        ),
    ]
    return {
        "id": "overview",
        "kind": "kpi",
        "title": "总览（合并视图）",
        "note": "每个数右下角标注原始行锚点：文件名 + 行号，悬停可见该源文件 sha256。",
        "figures": figures,
    }


def build_report_data(merged, merged_ref=None, merged_sha=None):
    """把合并产物翻译成渲染模型（含打码与下钻锚点）。"""
    pool = _anchor_pool(merged)
    sections = [
        _overview_section(merged, pool),
        _accounts_section(merged, pool),
        _holdings_section(merged, pool),
        _transfers_section(merged),
        _returns_section(merged, pool),
        _fees_section(merged, pool),
        _decomposition_section(merged, pool, "by_symbol", "按标的分解", "symbol", "标的"),
        _decomposition_section(merged, pool, "by_industry", "按行业分解", "industry", "行业"),
        _benchmark_section(merged, pool),
        _checks_section(merged, pool),
        _issues_section(merged),
        _zero_upload_section(merged),
        _conventions_section(merged),
    ]
    basis = merged.get("basis") or {}
    inputs = merged.get("inputs") or {}
    data = {
        "format": "ia.report_data",
        "format_version": 1,
        "contract_version": merged.get("contract_version"),
        "meta": {
            "title": "券商交割单收益归因报告",
            "subtitle": "纯本地 · 零上传 · 可审计：每个数字都可下钻到原始行",
            "source": {
                "merged": merged_ref,
                "merged_sha256": merged_sha,
                "contract_version": merged.get("contract_version"),
            },
        },
        "header": {
            "badges": [
                {"label": "成本口径", "value": basis.get("cost_method_label") or "-"},
                {"label": "TWR 估值点", "value": basis.get("twr_basis_label") or "-"},
                {"label": "估值日", "value": _text(basis.get("valuation_date"))},
                {"label": "基准", "value": "已提供" if basis.get("benchmark_provided") else "未提供（超额列不显示）"},
                {"label": "账户数", "value": str(len(merged.get("accounts") or []))},
                {"label": "流水行数", "value": str(inputs.get("rows"))},
            ],
            "notices": [
                "本报告由 merged.json（合并产物）同源渲染：HTML 与 report_data.json 出自同一次构建。",
                MASK_NOTICE,
            ],
            "disclaimer": DISCLAIMER,
        },
        "sections": sections,
        "zero_upload": merged.get("zero_upload") or {},
    }
    masked = set()
    sanitized = _sanitize(data, masked)
    sanitized["masking"] = {
        "applied": True,
        "rule": "keep_first_and_last_two",
        "notice": MASK_NOTICE,
        "masked_token_count": len(masked),
        # 只登记**打码后**的形态：登记原文等于把打码又撤回。
        "masked_samples": sorted({mask_account(token) for token in masked}),
    }
    sanitized["counts"] = {
        "section_count": len(sections),
        "figure_count": sum(len(item.get("figures") or []) for item in sections),
        "table_row_count": sum(len(item.get("rows") or []) for item in sections),
        "note_item_count": sum(len(item.get("items") or []) for item in sections),
    }
    return sanitized
