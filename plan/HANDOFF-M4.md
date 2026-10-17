# HANDOFF-M4 — 从 M3 公司行为到 M4 收益归因

> **当前对话已完成 M3**。本文件是 M4 的交接物：读完即可开工，不依赖对话记忆。
> 库内一律相对路径；启动命令由用户侧拼绝对路径。

## 一、当前进度与证据

### 已完成（M3，2026-10-17）

| 项 | 位置 | 证据 |
|---|---|---|
| 规则包加载 + 数据驱动匹配 | `src/investment_attribution/actions/rules.py` | `tests/test_actions.py` 规则包 6 条 |
| 公司行为引擎（事件台账 / 待确认 / 非行为 / 调整前后） | `src/investment_attribution/actions/engine.py` | 同上 |
| B3 逐类比对（供测试与 M5 `bench` 共用） | `src/investment_attribution/actions/verify.py` | `test_b3_*` 五条（含四条可证伪探活） |
| `corporate-actions` 编排 | `src/investment_attribution/pipeline/actions_cmd.py` | `test_actions.py` CLI 段 |
| B3 真值（双口径逐事件 before/after + 待确认 + 非行为） | `data/truth/actions_truth.json` | `tests/test_gen_data.py` 8 条 |
| 规则包新增判定字段 | `data/rules/corporate_actions.json` | `expected_kinds` / `cash_effect` / `non_action_patterns` |
| `STAGE_NOT_READY["corporate-actions"]` 退役（五处同步） | `stages.py` / `cli.py` / README / `plan/00` / 测试 | `test_stage_registry.py` + `test_cli_contract.py` |
| CI 补公司行为冒烟 + "缺口径必须 exit 2" + 阶段契约改指 attribution | `.github/workflows/ci.yml` | **未实跑**（无远端） |

### 本地验证结论

- `py -3.8 -m pytest` 与 `py -3.12 -m pytest` 均为 **240 passed**（本地实跑）
- `py -3.12 tools/gen_data.py --check` → `[shape] ok` + `[bytes] ok (27 artifacts)`，exit 0
- `py -3.8 / -3.12 -m investment_attribution selftest --strict` → 均 exit 0
- 公司行为场景 4 份样例 / 46 行：16 条 `other` 行 = **7 事件 + 1 待确认 + 8 非行为**，
  双口径逐事件对齐真值，可逆校验 7 条 0 失败
- **未接远端，CI 待首推验证**

## 二、下一棒待办（M4 · 收益归因）

### 必做

1. **`attribution/` 模块**：XIRR / TWR / 已实现未实现拆分 / 费用拖累 / 多维分解
   - XIRR：Newton–Raphson（`r0 = 0.1`，`|Δr| < 1e-12`，最多 100 次）→ 不收敛退二分
     （`[-0.999999, 1e6]`，`|NPV| < 1e-6`，最多 200 次）；无解抛 `XIRR_NO_SOLUTION` 且 exit 1，
     **不输出近似值、不打印 NaN**
   - TWR：`--twr-basis cashflow|daily`，默认 `cashflow`；两种都不插值，缺市值日
     报 `VALUATION_MISSING`
   - 现金流符号：买入/转入为负，卖出/分红/转出/期末市值为正；期末市值记在估值日
2. **期末市值输入**：`data/samples/valuation-demo-001.csv` 已就位（2025-12-31，3 个标的），
   格式 `date,symbol,name,quantity,price`
3. **可选本地基准 CSV**：缺失时超额列不显示、命令仍 `exit 0`
4. **行业映射**：`data/rules/industries.json` 已就位，未分类落 `未分类` 且可见
5. **运行期 socket 探针**（四层审计第三层；M0 只有静态 import 审计）
6. **`attribution` 子命令 + 退役 `STAGE_NOT_READY`**（五处同步，同 M1/M2/M3 流程）
7. **B4 真值**：建议新文件 `data/truth/attribution_truth.json`，含每账户 / 合并视图的
   XIRR 期望值 + **一份独立手工算例**作为第二判据

### 不要做

- 不要在合并（M5）、报告（M5）上开工
- 不要在渲染层或 CLI 里写业务口径
- 不要改 `actions/` 的匹配语义或 `ledger/` 的行形态分类——M4 只消费它们

## 三、既定口径清单（动了会打挂测试）

| 编号 | 约定 | 备注 |
|---|---|---|
| **K1** | 退出码：0 成功 / 1 降级或检出问题 / 2 用法或输入不可用 / 3 阶段未就绪 | `import` / `cost` / `pnl` / `corporate-actions` 已退役 3 |
| **K2** | JSON 冻结契约：`ensure_ascii=False` + `sort_keys=True` + `indent=2` + LF + 结尾换行 | 所有产物字节 |
| **K3** | 写盘唯一入口 `serialize.write_json_atomic` | 绕过即失去确定性 |
| **K4** | 数据目录：显式 `--data-dir` > `IA_DATA_DIR` > 源码树探测；**不做 CWD 上溯** | |
| **K5** | 审计行必带 `source_file` / `source_sha256` / `source_line_no` | M2 / M3 产物已继承 |
| **K6** | 编码探测顺序 `UTF-8(BOM) → UTF-8 → GB18030`，不猜测、不静默替换 | `params.encoding_order` 锁定 |
| **K7** | 口径参数无默认值 | `--method`（cost / pnl / corporate-actions）已锁；M4 的 `--twr-basis` **有默认值** `cashflow`（`params.twr.default_basis`），这是既定口径不是遗漏 |
| **K8** | 数值口径唯一来源 = `data/README.md §四` | 不得写第二份；M2 细则 §4.1.1，M3 细则 §4.1.2，XIRR/TWR 在 §4.2 / §4.3 |
| **K9** | 假代码段 `699000–699099`、名称 `示例` 前缀、资金账号 `Y0000001–Y0000009`、股东账号 `A000000001–A000000009` | 白名单扫描锁定 |
| **K10** | 真值台账只存"输入 + 期望"；出现 `observed_*` **键**即红 | 三份真值都有测试 |
| **K11** | 冻结产物禁手改，改需求走生成器整目录重生成 | `data/rules/`、`data/samples/`、`data/truth/` |
| **K12** | 未落地命令必须返回 3，禁止返回 0 或伪造空结果 | 现只剩 `attribution` / `merge` / `report` / `run` / `bench` |

## 四、M3 新增、容易被踩的约定

| 约定 | 说明 |
|---|---|
| **规则包是唯一业务名来源** | 引擎里没有第二份业务名表。要加新事件类型 → 改生成器 `build_corporate_actions()` 并整目录重生成 |
| **`expected_kinds` + `cash_effect` 是判定字段** | 业务名命中还不够：行形态必须落在 `expected_kinds`、现金方向必须等于 `cash_effect`，否则进待确认。给规则加模式时必须同步这两个字段，否则该规则永远只能进待确认 |
| **子串匹配** | `pattern in business`，所以 `红利` 会命中 `红利再投`。模式之间**不得重复**（`CA_PATTERN_DUPLICATE`），但**子串重叠**只能在运行时表现为 `AMBIGUOUS_RULES` → 待确认 |
| **`other` 行三分桶守恒** | 事件 + 待确认 + 非行为 == 总数；不等落 `ACTION_BUCKETS_UNBALANCED`。新增分支时必须更新分桶，否则守恒断言会红 |
| **待确认行仍推进持仓** | 份额 / 成本是交割单的机械事实，"待确认"只表示事件类型未定。所以 `adjusted_positions` 恒等于 M2 `holdings.json` 的持仓（`test_adjusted_positions_equal_m2_holdings`） |
| **人工确认只能解决"待确认"** | 规则表已自动判定的行、判为非行为的行、无法归类的行，都不能被确认记录覆盖（分别 `CA_CONFIRMATION_NOT_PENDING` / `CA_CONFIRMATION_INVALID`）。确认记录必须有 `decided_by` / `decided_at` / `basis` |
| **确认记录不可指向不存在的行** | `CA_CONFIRMATION_UNKNOWN_ROW`，`exit 2` |
| **非行为 ≠ 收入** | 银证转入/转出、内部划转落 `non_actions`，不进事件台账。M4 算 XIRR 时必须把它们当**外部现金流**（转入为正投入、转出为负投入），不要当收益 |
| **公司行为不另立成本口径** | 调整复用 `make_book` / `add` / `add_free`，与 M2 同一实现。M4 的已实现/未实现必须从 M2 账本取，不要从 M3 事件重算 |
| **真值独立实现** | `tools/gen_data.py` 里 B3 用 `_TruthBook`（`Fraction` + 整数分），不 import 引擎。M4 的 XIRR 真值同样要独立复算，且需一份手工算例 |

## 五、M3 样例里已注入、M4 直接可用的事实

| 事实 | 位置 | 用途 |
|---|---|---|
| 账户一 / 二 / 三 + 账户四（无费用列） | `htsec-demo-001.xlsx` / `huaxi-demo-001.xls` / `generic-demo-013-account-c.csv` / `generic-demo-015-no-fee-columns.csv` | 分账户 XIRR |
| 定投（699005 三笔 + 一笔回补） | `huaxi-demo-001.xls` | TWR 分段、XIRR 多笔现金流 |
| 单笔大额投入（699006 1000 股 @12.00） | 2025-04-01 | 一次性投入对照 |
| 中途大额赎回（699005 卖出 500 @22.00） | 2025-06-20 | TWR 分段点 |
| 负收益（699006 成本 17510.00，清仓回款 15729.25） | 2025-12-22 | 负 XIRR |
| 零收益（699007 买卖同价、费用 0） | 2025-08-20 / 2025-11-05 | XIRR ≈ 0 的边界 |
| 期末持仓清零 | 2025-12-22 | 期末市值为 0 的边界 |
| 期末市值样例 | `data/samples/valuation-demo-001.csv` | 2025-12-31 估值日 |
| 内部划转 ±20000.00（TR-A-B-0001） | 两文件 2025-10-10 | M5 划转抵消；M4 单账户 XIRR 里是外部现金流 |
| 现金分红 300.00 / 250.00 / 50.00 | 2025-05-20 / 2025-07-10 / 2025-04-02 | XIRR 正现金流 |

> **注意**：`coverage.surfaces` 里 6 个 `attribution.*` 形态仍只登记
> "形态已注入样例"（`total == sampled == 1`）。M4 必须把**逐项期望值**写进
> 真值库，否则"与 Excel 手工对照达标"无从断言。

## 六、本机环境坑（只写实测过的）

- **两条 Python 通道**：`py -3.8` 与 `py -3.12`。两条都要跑全量测试。
- **用 `py` 不用 `python`**：Windows 上 `python` 可能是商店占位符。
- **中文控制台**：跑命令加 `PYTHONUTF8=1`。
- **Git Bash 参数改写**：以 `/` 开头的参数会被 MSYS 转写成 Windows 路径。临时目录用**仓库内相对路径**。
- **pytest 7.4.4** 已装；`testpaths = tests`。
- **`tests/context.py` 把 `src/` 插入 `sys.path`**：源码态无需 `pip install -e .`。
- **`tests/test_gen_data.py` 会把 `tools/` 插进 `sys.path`**：引擎代码不要反向依赖 `tools/`。
- **`test_no_placeholder_sentinels_in_sources`**：`src/` 与 `tests/` 里不得出现
  `NotImplementedError` 等占位词。写抽象接口时用 `ValueError` 或具体实现。
- **成本 / 公司行为场景 fixture 用 module scope**：`tests/test_cost.py` 的
  `ledger_path` 与 `tests/test_actions.py` 的 `ledger_path` 各自只导入一次。
- **`data/` 由生成器整体重写**：改了 `CONTRACT_VERSION` 或规则包结构后必须
  `py -3.12 tools/gen_data.py` 重生成，再 `--check` 确认字节一致。

## 七、M4 DoD 逐项 checklist

- [ ] B4 与 Excel `XIRR` 手工对照：相对误差 < 0.01%（`manifest.bench_thresholds.xirr_relative_tolerance`）
- [ ] 分账户 XIRR 与合并视图 XIRR 都对真值
- [ ] TWR 两种估值点口径都能跑；`cashflow` 为默认；缺市值日报 `VALUATION_MISSING`
- [ ] 已实现 + 未实现 = 总收益（≤ 0.01 元），拆分守恒
- [ ] 费用拖累可复算（费用总额 = M2 `totals.fees_total_fen`）
- [ ] 按标的 / 行业 / 账户 / 年度分解之和等于总计
- [ ] 无基准 CSV 时仍 `exit 0`，超额列不显示
- [ ] 双跑字节一致
- [ ] 运行期 socket 探针有正/负对照
- [ ] `STAGE_NOT_READY["attribution"]` 退役，五处同步
- [ ] 产物页眉标注本次口径（成本口径 + TWR 估值点口径 + 基准是否提供）
- [ ] 3.8 / 3.12 双解释器全量 pytest 通过
- [ ] `selftest --strict` 仍 exit 0
- [ ] `tools/gen_data.py --check` 仍全绿
- [ ] 写 `plan/HANDOFF-M5.md` 并 commit

## 八、关键命令速查

```bash
# 测试（双通道）
PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 py -3.8 -m pytest -q
PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 py -3.12 -m pytest -q

# 数据守门（改真值后必跑）
PYTHONUTF8=1 py -3.12 tools/gen_data.py
PYTHONUTF8=1 py -3.12 tools/gen_data.py --check

# 自检
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution selftest --strict

# 导入 + 成本 + 公司行为（已可用）
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution import \
    data/samples/htsec-demo-001.xlsx data/samples/huaxi-demo-001.xls --out-dir out/
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution cost \
    --method fifo --ledger out/ledger.json --out-dir out/fifo
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution corporate-actions \
    --method fifo --ledger out/ledger.json --out-dir out/ca

# 未就绪契约（应为 3）
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution attribution; echo $?

# M4 开工后（注意：当前 `attribution` 的参数表只有 --xirr / --twr / --twr-basis /
# --benchmark / --out，**没有 --ledger 与 --valuation**，M4 需要先补这两个参数）
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution attribution \
    --xirr --twr --ledger out/ledger.json \
    --valuation data/samples/valuation-demo-001.csv --out out/attribution.json
```

## 九、下一对话启动提示词

```
/goal 读取 "<项目绝对路径>\plan\HANDOFF-M4.md" 继续完成任务。
```
