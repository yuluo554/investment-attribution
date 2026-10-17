# HANDOFF-M2 — 从 M1 数据先行到 M2 成本引擎

> **当前对话已完成 M1**。本文件是 M2 的交接物：读完即可开工，不依赖对话记忆。
> 库内一律相对路径；启动命令由用户侧拼绝对路径。

## 一、当前进度与证据

### 已完成（M1，2026-10-17）

| 项 | 位置 | 证据 |
|---|---|---|
| 确定性生成器（splitmix64 / 固定 seed / 不读时钟） | `tools/gen_data.py` | `tests/test_gen_data.py`；`--check` 双层 |
| 冻结规则包 6 个 | `data/rules/*.json` | 生成器产出，`test_gen_data.py` 锁形态 |
| 16 个合成样例（含 6 类刻意缺陷） | `data/samples/` | 同上 |
| 导入真值台账 | `data/truth/import_truth.json` | 16 文件 / 63 行 / 3 行级错误 / 4 类整份不可用 |
| 导入适配层 | `src/investment_attribution/ingest/` | `tests/test_import.py` |
| `import` 编排 | `src/investment_attribution/pipeline/import_cmd.py` | 同上 |
| `STAGE_NOT_READY["import"]` 退役（五处同步） | `stages.py` / `cli.py` / README / `plan/00` / 测试 | `test_stage_registry.py` 防复活断言 |

### 本地验证结论

- `py -3.8 -m pytest` 与 `py -3.12 -m pytest` 均为 **111 passed**（本地实跑）
- `py -3.12 tools/gen_data.py --check` → `[shape] ok` + `[bytes] ok (24 artifacts)`，exit 0
- `py -3.12 -m investment_attribution selftest --strict` → exit 0，6 项全绿
- 两类真实格式：海通 22 行 / 华西 17 行，**0 行拒绝**
- **未接远端，CI 待首推验证**（CI 已补 `gen_data --check` 与导入冒烟两步，未实跑）

## 二、下一棒待办（M2 · 成本引擎）

### 必做

1. **`ledger/` 成本引擎**：FIFO 与移动加权两套实现，共享同一账本骨架
   - 输入 = M1 的 `ledger.json`（统一流水）
   - 排序键固定 `(trade_date, trade_time, source_line_no)`（plan/02 §五）
   - 金额全程**整数分**；数量用规范十进制文本（M1 已按此落盘）
   - 口径 = 显式参数，**无默认值**（K7）

2. **费用三件套入账**：买入费用计入成本、卖出费用冲减已实现收益；
   未分列时按 `params.json` 显式分摊规则并标注（`data/README §4.1`）

3. **T+1 与资金余额勾稽**：用 `extra.cash_balance`（M1 已在样例里逐行算好）
   勾稽 `settlement_amount` 累计；勾稽不平落 `invalid` 并列台账

4. **每日持仓快照**：由成交流水确定性推导，同输入 → 同字节

5. **`cost` / `pnl` 子命令 + 退役两条 `STAGE_NOT_READY`**（五处同步，同 M1 流程）

6. **B2 真值用例**：把成本 5 类形态的**逐笔期望值**追加进 `data/truth/`

### 不要做

- 不要在公司行为（M3）、归因（M4）、报告（M5）上开工
- 不要在渲染层或 CLI 里写业务口径

## 三、既定口径清单（动了会打挂测试）

| 编号 | 约定 | 备注 |
|---|---|---|
| **K1** | 退出码：0 成功 / 1 降级或检出问题 / 2 用法或输入不可用 / 3 阶段未就绪 | `import` 已退役 3，现为 0/1/2 |
| **K2** | JSON 冻结契约：`ensure_ascii=False` + `sort_keys=True` + `indent=2` + LF + 结尾换行 | 所有产物字节 |
| **K3** | 写盘唯一入口 `serialize.write_json_atomic` | 绕过即失去确定性 |
| **K4** | 数据目录：显式 `--data-dir` > `IA_DATA_DIR` > 源码树探测；**不做 CWD 上溯** | |
| **K5** | 审计行必带 `source_file` / `source_sha256` / `source_line_no`（源文件行号，非数组下标） | M1 已落地，M2 派生台账必须继承 |
| **K6** | 编码探测顺序 `UTF-8(BOM) → UTF-8 → GB18030`，不猜测、不静默替换 | 已由 `params.encoding_order` 锁定 |
| **K7** | `--cost-method` 无默认值，必须显式 fifo/wavg | M2 的主要验收点 |
| **K8** | 数值口径唯一来源 = `data/README.md §四` | 不得写第二份 |
| **K9** | 假代码段 `699000–699099`、名称 `示例` 前缀、资金账号 `Y0000001–Y0000009`、股东账号 `A000000001–A000000009` | 已由白名单扫描锁定 |
| **K10** | 真值台账只存"输入 + 期望"；出现 `observed_*` **键**即红 | 注意：不要用全文字符串匹配（note 里会出现该词） |
| **K11** | 冻结产物禁手改，改需求走生成器整目录重生成 | `data/rules/`、`data/samples/`、`data/truth/` |
| **K12** | 未落地命令必须返回 3，禁止返回 0 或伪造空结果 | |

## 四、M1 新增、容易被踩的约定

| 约定 | 说明 |
|---|---|
| **金额单位 = 整数分** | `ledger.json` 里 `amount` / `settlement_amount` / 四项费用 / `extra.cash_balance` 全是整数分；M2 用整数累加，不要中途转 float |
| **数量 / 价格为字符串** | 规范十进制文本（去尾零），`"1000"`、`"10.2"`；比较数量用 `Decimal`，不要 `float` |
| **`None` 与 `0` 必须分开** | 金额列缺失 = `None`，值为 0 = `0`。M2 判"费用未分列"只能看 `None` |
| **`source_line_no` 是物理行号** | 华西样例用 `\r\n` 且带 `="..."` 壳，行号没有偏移；但 M2 若再次读文件，务必仍按源文件行号 |
| **`fingerprints.json` 按文件名排序** | 保证命令行文件顺序不影响产物字节 |
| **未登记账户行仍入库** | `account_status == "pending"`，`account_alias is None`。M2 要做**分账户**台账时必须把 pending 单独分组，**不能并进默认账户** |
| **`extra` 是自由字段袋** | 目前有 `cash_balance`（分）、`share_balance`（数量）、`note`（文本）、未登记列。M2 读现金余额走 `row["extra"]["cash_balance"]` |
| **整份文件不可用时不写任何产物** | `import_files` 先读全部文件再落盘；M2 的 `cost` 也应保持"要么全写要么不写" |
| **生成器不是引擎** | `tools/gen_data.py` 里**故意**自己算了一份期望值（真值纪律要求与引擎独立），M2 不要 import 它 |

## 五、M1 样例里已注入、M2 直接可用的事实

| 事实 | 位置 | 用途 |
|---|---|---|
| 同日两笔买入（699001，2025-01-06） | `htsec-demo-001.xlsx` 第 2、3 行 | 同日多笔 |
| 部分卖出（699001，卖 600，原持仓 1500） | 第 5 行 | 部分卖出 |
| 清仓后再买入（699001 卖 900 后 2025-04-08 买 800） | 第 6、7 行 | 清仓回补 |
| 拆分后卖出（699003 2025-07-15 拆 5000，次日卖 3000） | 第 14、15 行 | 送转后卖出 |
| FIFO 与加权成本不同的构造（699001 两批价 10.00 / 10.20 后部分卖出） | 第 2、3、5 行 | 双口径差异 |
| 每行都带 `cash_balance` / `share_balance` | 全表 | T+1 与余额勾稽 |
| 费用四件套分列 | 海通 19 列 | 费用分摊 |
| `699007` 买卖同价且费用为 0 | `huaxi-demo-001.xls` | 零收益 |

> **注意**：M1 的 `coverage.surfaces` 对成本 5 类只登记了"形态已注入样例"
> （`total == sampled == 1`）。M2 必须把**逐笔期望值**写进真值库，否则
> "双口径逐笔对齐"无从断言。

## 六、本机环境坑（只写实测过的）

- **两条 Python 通道**：`py -3.8` 与 `py -3.12`（`py -0p` 确认）。两条都要跑全量测试。
- **用 `py` 不用 `python`**：Windows 上 `python` 可能是商店占位符。
- **中文控制台**：跑命令加 `PYTHONUTF8=1`。
- **Git Bash 参数改写**：以 `/` 开头的参数会被 MSYS 转写成 Windows 路径
  （实测：`/tmp/...` 传给 Python 变成 `\tmp\...`）。临时目录用**仓库内相对路径**。
- **pytest 7.4.4** 已装；`testpaths = tests`。
- **`tests/context.py` 把 `src/` 插入 `sys.path`**：源码态无需 `pip install -e .`。
- **`tests/test_gen_data.py` 会把 `tools/` 插进 `sys.path`**：M2 若要复用生成器的
  常量做断言，注意别让引擎代码反向依赖 `tools/`。

## 七、M2 DoD 逐项 checklist

- [ ] B2 逐笔对齐真值（FIFO 与移动加权各跑一遍）
- [ ] 持仓数量误差 ≤ 1 股、金额误差 ≤ 0.01 元
- [ ] 双口径差异可解释到**具体成交笔**
- [ ] 每日快照双跑字节一致
- [ ] 口径切换**不修改原始流水**（测试断言：`ledger.json` 字节前后一致）
- [ ] 费用按交割单实际列值入账，未分列时按 `params.json` 规则并标注
- [ ] T+1 / 资金余额勾稽不平落 `invalid` 且逐条可回放
- [ ] `--cost-method` 缺省必须报错（exit 2），不得有默认值
- [ ] `STAGE_NOT_READY["cost"]` / `["pnl"]` 退役，五处同步
- [ ] 3.8 / 3.12 双解释器全量 pytest 通过
- [ ] `selftest --strict` 仍 exit 0
- [ ] `tools/gen_data.py --check` 仍全绿（追加真值后必须重生成并重新冻结）
- [ ] 写 `plan/HANDOFF-M3.md` 并 commit

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

# 已可用：导入
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution import \
    data/samples/htsec-demo-001.xlsx data/samples/huaxi-demo-001.xls --out-dir out/

# 未就绪契约（应为 3）
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution cost --method fifo; echo $?

# M2 开工后
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution cost \
    --method fifo --ledger out/ledger.json --out out/holdings.json
```

## 九、下一对话启动提示词

```
/goal 读取 "<项目绝对路径>\plan\HANDOFF-M2.md" 继续完成任务。
```
