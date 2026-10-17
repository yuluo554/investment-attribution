# HANDOFF-M3 — 从 M2 成本引擎到 M3 公司行为

> **当前对话已完成 M2**。本文件是 M3 的交接物：读完即可开工，不依赖对话记忆。
> 库内一律相对路径；启动命令由用户侧拼绝对路径。

## 一、当前进度与证据

### 已完成（M2，2026-10-17）

| 项 | 位置 | 证据 |
|---|---|---|
| 成本引擎（FIFO / WAVG 共享骨架） | `src/investment_attribution/ledger/positions.py` | `tests/test_cost.py` |
| 行语义（按形态归类，整数分） | `src/investment_attribution/ledger/model.py` | 同上 |
| 编排：排序 / 分账户 / 勾稽 / 快照 / 产物 | `src/investment_attribution/ledger/engine.py` | 同上 |
| B2 逐笔比对（供测试与 M5 `bench` 共用） | `src/investment_attribution/ledger/verify.py` | `test_b2_benchmark_*` 四条 |
| `cost` / `pnl` 编排 | `src/investment_attribution/pipeline/cost_cmd.py` | `test_cost.py` |
| B2 真值（双口径逐笔 + 快照 + 差异） | `data/truth/cost_truth.json` | `tests/test_gen_data.py` 6 条 |
| 费用未分列样例（第 31 形态） | `data/samples/generic-demo-015-no-fee-columns.csv` | `test_unitemized_fees_are_derived_and_labelled` |
| `STAGE_NOT_READY["cost"]` / `["pnl"]` 退役（五处同步） | `stages.py` / `cli.py` / README / `plan/00` / 测试 | `test_stage_registry.py` + `test_cli_contract.py` |

### 本地验证结论

- `py -3.8 -m pytest` 与 `py -3.12 -m pytest` 均为 **161 passed**（本地实跑）
- `py -3.12 tools/gen_data.py --check` → `[shape] ok` + `[bytes] ok (26 artifacts)`，exit 0
- `py -3.8 / -3.12 -m investment_attribution selftest --strict` → 均 exit 0
- 成本场景 4 份样例 / 46 行，双口径逐笔对齐真值，**0 违例、0 invalid**
- **未接远端，CI 待首推验证**（CI 已补成本冒烟与"缺口径必须 exit 2"两步，未实跑）

## 二、下一棒待办（M3 · 公司行为）

### 必做

1. **`actions/` 模块**：规则表匹配 + 人工确认队列
   - 规则表 = `data/rules/corporate_actions.json`（冻结产物，5 类事件 + `unmatched_policy`）
   - 匹配对象 = 统一流水里 `direction == "other"` 的行；**M2 已把它们归成
     `receipt_paid` / `receipt_valued` / `receipt_free` / `cash_event` / `noop`**
   - 能确定 → 自动入账并挂依据（`rule_id` + 原始行）；不能确定 → 进待确认队列

2. **除权前后两组数**：份额 / 单位成本 / 总成本 before-after，可逆复算

3. **不回写原始流水**：调整只作用于派生台账（与 M2 同一纪律）

4. **`corporate-actions` 子命令 + 退役 `STAGE_NOT_READY`**（五处同步，同 M1/M2 流程）

5. **B3 真值**：5 类事件的逐笔期望追加进 `data/truth/`（建议新文件 `actions_truth.json`）

6. **"无法识别"必须 100% 进待确认队列**：样例里已有 `其他业务甲`（2025-08-01，699002）

### 不要做

- 不要在归因（M4）、报告（M5）上开工
- 不要在渲染层或 CLI 里写业务口径
- 不要给 M2 的行语义加业务名分支——M3 的规则表应叠在 `kind` 之上，而不是改 `kind`

## 三、既定口径清单（动了会打挂测试）

| 编号 | 约定 | 备注 |
|---|---|---|
| **K1** | 退出码：0 成功 / 1 降级或检出问题 / 2 用法或输入不可用 / 3 阶段未就绪 | `import` / `cost` / `pnl` 已退役 3，现均为 0/1/2 |
| **K2** | JSON 冻结契约：`ensure_ascii=False` + `sort_keys=True` + `indent=2` + LF + 结尾换行 | 所有产物字节 |
| **K3** | 写盘唯一入口 `serialize.write_json_atomic` | 绕过即失去确定性 |
| **K4** | 数据目录：显式 `--data-dir` > `IA_DATA_DIR` > 源码树探测；**不做 CWD 上溯** | |
| **K5** | 审计行必带 `source_file` / `source_sha256` / `source_line_no` | M2 派生台账（事件 / invalid）已继承 |
| **K6** | 编码探测顺序 `UTF-8(BOM) → UTF-8 → GB18030`，不猜测、不静默替换 | 已由 `params.encoding_order` 锁定 |
| **K7** | `--cost-method` 无默认值 | M2 已锁；M3 若新增口径参数沿用同一纪律 |
| **K8** | 数值口径唯一来源 = `data/README.md §四` | 不得写第二份；M2 细则在 §4.1.1 |
| **K9** | 假代码段 `699000–699099`、名称 `示例` 前缀、资金账号 `Y0000001–Y0000009`、股东账号 `A000000001–A000000009` | 白名单扫描锁定；M2 新增 `Y0000004` 已登记 |
| **K10** | 真值台账只存"输入 + 期望"；出现 `observed_*` **键**即红 | 两份真值都有测试 |
| **K11** | 冻结产物禁手改，改需求走生成器整目录重生成 | `data/rules/`、`data/samples/`、`data/truth/` |
| **K12** | 未落地命令必须返回 3，禁止返回 0 或伪造空结果 | |

## 四、M2 新增、容易被踩的约定

| 约定 | 说明 |
|---|---|
| **成本 = 整数分** | 单位成本只是展示值（6 位小数，四舍五入）；权威值永远是 `cost_fen`。M3 调整成本时也只改整数分 |
| **部分卖出按比例分摊** | `allocate_cost`：整批取尽时不除法，直接取全额，保证守恒。M3 改成本后仍要满足"各批之和 = 总成本" |
| **零成本份额** | `add_free`：FIFO 下按比例放大各批数量、总成本不变，余数归最后一批。M3 的送转 / 拆分应复用同一条路径 |
| **行语义不认业务名** | M2 只看方向 / 数量 / 对价 / 现金流向。M3 的规则表叠在 `kind` 之上，**不要回头给 `kind` 加业务名分支** |
| **`settle_fen` 优先用交割单列值** | 缺失才用 `成交金额±费用` 推导；推导值与列值不等落 `SETTLEMENT_MISMATCH`，但**仍以列值为准** |
| **费用来源四态** | `as_reported` / `partially_reported` / `derived_from_settlement` / `not_reported`。判"未分列"只能看四项是否**全为 `None`** |
| **未登记账户单独成组** | `account_key` 用 `unregistered:<file>:<shareholder>`；M3 分账户台账同样不能并进默认账户 |
| **每日快照只记数量 > 0** | 已清仓标的不会留在快照里；日期按"有流水的交易日"，不补空日 |
| **`--ledger` 显式必填** | 不做"猜 out/ledger.json"的隐式兜底 |
| **生成器不 import 引擎** | `tools/gen_data.py` 里成本真值是独立实现（`Fraction` + 整数分）。M3 真值同样要独立复算 |
| **成本真值有场景范围** | `cost_truth.scenario.files` 只含 4 份样例；其余是副本/子集，**全部一起导入会重复计数** |

## 五、M2 样例里已注入、M3 直接可用的事实

| 事实 | 位置 | 用途 |
|---|---|---|
| 红利入账 699002（300.00） | `htsec-demo-001.xlsx` 2025-05-20 | 分红事件 |
| 送股入账 699002（200 股） | 2025-06-10 | 送转事件（零成本份额） |
| 红利再投 699001（100 股 @9.50） | 2025-06-10 09:30:05 | 分红再投 |
| 配股 699002（300 股 @5.80，-1740.00） | 2025-07-01 | 配股事件 |
| 拆分 699003（+5000 股） | 2025-07-15 | 拆分事件（零成本份额） |
| **其他业务甲 699002（+12.34）** | 2025-08-01 | **规则表无法识别 → 必须进待确认** |
| 红利入账 699005（250.00） | `huaxi-demo-001.xls` 2025-07-10 | 第二账户的分红 |
| 内部划转（TR-A-B-0001，±20000.00） | 两文件 2025-10-10 | M5 划转抵消；M3 不要当收入处理 |

> **注意**：`coverage.surfaces` 里 6 个 `corporate_action.*` 形态仍只登记
> "形态已注入样例"（`total == sampled == 1`）。M3 必须把**逐笔期望值**写进
> 真值库，否则"逐类事件对齐"无从断言。

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
- **成本场景 fixture 用 module scope**：`tests/test_cost.py` 的 `ledger_path` 只导入一次，
  新增成本测试直接复用，别在函数里重复 import。

## 七、M3 DoD 逐项 checklist

- [ ] B3 逐类事件对齐真值（5 类：分红 / 送转 / 配股 / 拆分 / 分红再投）
- [ ] 无法识别行 100% 进待确认队列且产物可见（`其他业务甲`）
- [ ] 调整前后两组数（份额 / 单位成本 / 总成本）可逆复算
- [ ] 调整不回写原始流水（测试断言：`ledger.json` 字节前后一致）
- [ ] 事件台账逐条挂依据（`rule_id` + `source_file` + `source_line_no`）
- [ ] `STAGE_NOT_READY["corporate-actions"]` 退役，五处同步
- [ ] 产物页眉标注本次口径 / 本次规则包版本
- [ ] 3.8 / 3.12 双解释器全量 pytest 通过
- [ ] `selftest --strict` 仍 exit 0
- [ ] `tools/gen_data.py --check` 仍全绿（追加真值后必须重生成并重新冻结）
- [ ] 写 `plan/HANDOFF-M4.md` 并 commit

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

# 导入 + 成本（已可用）
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution import \
    data/samples/htsec-demo-001.xlsx data/samples/huaxi-demo-001.xls --out-dir out/
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution cost \
    --method fifo --ledger out/ledger.json --out-dir out/fifo

# 未就绪契约（应为 3）
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution corporate-actions --ledger out/ledger.json; echo $?

# M3 开工后
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution corporate-actions \
    --ledger out/ledger.json --out out/actions.json
```

## 九、下一对话启动提示词

```
/goal 读取 "<项目绝对路径>\plan\HANDOFF-M3.md" 继续完成任务。
```
