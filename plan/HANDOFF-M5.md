# HANDOFF-M5 — 从 M4 收益归因到 M5 合并与报告

> **当前对话已完成 M4**。本文件是 M5 的交接物：读完即可开工，不依赖对话记忆。
> 库内一律相对路径；启动命令由用户侧拼绝对路径。

## 一、当前进度与证据

### 已完成（M4，2026-10-17）

| 项 | 位置 | 证据 |
|---|---|---|
| 估值序列输入（本地 CSV，多日，数量交叉核对） | `src/investment_attribution/attribution/valuation.py` | `tests/test_attribution.py` 估值段 5 条 |
| XIRR 求解器（Newton 主解 + 二分兜底，参数全来自 `params.xirr`） | `src/investment_attribution/attribution/xirr.py` | 闭式解算例 + 无解不造数 + 确定性 + 序列过短 |
| TWR（两种估值点口径，不插值） | `src/investment_attribution/attribution/twr.py` | `test_twr_both_bases_match_truth` 等 4 条 |
| 可选本地基准（缺失 → 超额列不显示，仍 exit 0） | `src/investment_attribution/attribution/benchmark.py` | `test_benchmark_*` 4 条 |
| 行业映射（未分类可见） | `src/investment_attribution/attribution/industries.py` | `test_industry_map_reports_unclassified` 等 2 条 |
| 归因引擎（现金流判定 / 拆分 / 费用拖累 / 四维分解 / 产物页眉） | `src/investment_attribution/attribution/engine.py` | 见下方"本地验证结论" |
| B4 比对（供测试与 M5 `bench` 共用） | `src/investment_attribution/attribution/verify.py` | `test_b4_detects_*` 五条可证伪探活 |
| `attribution` 编排与退出码 | `src/investment_attribution/pipeline/attribution_cmd.py` | `test_cli_attribution_*` 6 条 |
| 运行期零上传探针（`sys.addaudithook`，**不 import socket**） | `src/investment_attribution/netguard.py` | `tests/test_netguard.py` 运行期 4 条（正/负对照） |
| `selftest` 新增运行期探针检查 | `src/investment_attribution/selftest.py` | `selftest --strict` 里 `runtime_socket_probe` |
| B4 真值（每账户/合并视图 XIRR + 两种 TWR 口径 + 拆分 + 四维分解 + 6 形态锚点） | `data/truth/attribution_truth.json` | `tests/test_gen_data.py` M4 段 8 条 |
| 多日估值序列样例（37 日 × 7 标的 / 169 行） | `data/samples/valuation-demo-001.csv` | `test_valuation_sample_matches_ledger_end_state` |
| `STAGE_NOT_READY["attribution"]` 退役（五处同步） | `stages.py` / `cli.py` / README / `plan/00` / 测试 | `test_stage_registry.py` + `test_cli_contract.py` |
| CI 补归因冒烟 + 阶段契约改指 M5 四命令 | `.github/workflows/ci.yml` | **未实跑**（无远端），命令已在本机逐条实跑 |

### 本地验证结论（全部实跑）

- `py -3.8 -m pytest` 与 `py -3.12 -m pytest` 均为 **310 passed**
- `py -3.12 tools/gen_data.py --check` → `[shape] ok` + `[bytes] ok (28 artifacts)`，exit 0
- `py -3.8 / -3.12 -m investment_attribution selftest --strict` → 均 exit 0，
  `runtime_socket_probe` 明细 `blocked=1 recorded=1 disarm_ok=True`
- 归因实跑（成本场景四件套导入 46 行 / 4 账户 / 估值日 2025-12-31）：
  - 合并视图 XIRR `0.032746269716`、TWR（cashflow 口径）`0.039444016905`
  - 总收益 `7984.38` 元，拆分守恒差 `0` 分（`pnl.checks.max_difference_fen == 0`）
  - 四维分解四项均为 `798438` 分（`decomposition.checks.all_equal == true`）
  - 费用 `242.96` 元，`matches_m2_total == true`；`zero_upload.runtime_probe.blocked_attempts == 0`
- CI 步骤本机等价复跑（用 `py -3.12`）：导入 0 / 归因 fifo 0 / 归因 wavg+daily 0 /
  缺口径 2 / 缺估值 2 / 非法 `--twr-basis` 2 / `merge`·`report`·`bench`·`run x.csv` 均 3
- **未接远端，CI 待首推验证**

### M4 已知偏差（写清以免被当成 bug）

1. **TWR 两种估值点口径在数据完整时给出同一个值**。时间加权收益与分段方式无关，
   多插零现金流的估值点不改变连乘结果。两口径的真实差别是**需要哪些日期的估值**
   与**缺值时在哪报错**：`cashflow` 只要现金流日，`daily` 要求每个交易日快照日。
   `test_twr_daily_requires_every_snapshot_date` 用"只给现金流日"的估值表把差别钉住。
2. **外部现金流 = 账户边界上的资金划转**，成交与分红不进外部流。曾把买入也计入，
   结果账户一净投入变成 `-123176` 分而实际赚 `6823.88` 分——同一笔资本被计两次。
   口径写在 `data/README.md §4.2.1`；`期末总资产 − 净投入 == 已实现 + 未实现`
   对四个账户精确到分成立。
3. **估值样例对应当"成本场景四件套"**（`htsec` / `huaxi` / `generic-demo-013` /
   `generic-demo-015`）。只导入其中一部分再核估值表会（正确地）落
   `VALUATION_QUANTITY_MISMATCH`——交叉核对在按设计工作。README 与 CI 已改成
   导入同一组四份流水。

## 二、下一棒待办（M5 · 多账户合并与本地 HTML 报告）

### 必做

1. **`merge/` 模块**：跨券商合并视图（分账户明细 + 合计）+ **划转识别与两端剔除**
   - 划转两腿：合同号 `TR-A-B-0001`，2025-10-10 账户一转出 20000.00 / 账户二转入 20000.00
   - 合并前后总收益不变（≤ 0.01 元，门槛 `manifest.bench_thresholds.transfer_offset_tolerance_cny`）
   - 需要产出**逐笔划转台账**（M4 的合并视图只把现金流按日期相加，不列逐笔）
2. **`report/` 模块**：单文件本地 HTML + 机读 `report_data.json`，**同一次渲染产出**（同源）
   - 零外链（0 个 `http(s)://` / `//` 外部引用）+ 零网络（渲染全程处于武装探针下）
   - 报告阶段打码：股东账号 / 资金账号在**生成阶段**即打码，HTML / JSON 不含完整账号
   - 逐数字下钻：每个汇总数带 `source_file` / `source_sha256` / `source_line_no`
   - 报告双跑 sha256 相同
3. **渲染层零判定 AST 门禁**（三条 + 正/负对照）：渲染层不得出现业务判定
   （成本口径选择、事件类型判定、收益口径）
4. **`merge` / `report` / `run` / `bench` 四个子命令落地**，
   退役四条 `STAGE_NOT_READY`（**五处同步** × 4 条，见 §三 K12）
5. **`bench` 接线**：`--only B1..B5`
   - **B2 / B3 / B4 的比对函数已就位**：`ledger/verify.py`、`actions/verify.py`、
     `attribution/verify.py`，三者接口一致（`compare(产物, 真值, data_dir[, method])`
     → `Report.ok` / `.as_dict()`），阈值都从 `manifest.bench_thresholds` 取
   - **B1 没有模块**：导入侧比对现在只内联在 `tests/test_import.py::test_every_sample_matches_truth_ledger`
     里。`bench` 要跑 B1 就得先把这段抽成 `ingest/verify.py`，并沿用同一条纪律：
     **只读两边产物、不重算导入**（否则"注入缺陷必须报未达标"不可证伪）
   - **B5 由 M5 新写**（合并前后总收益 + 划转台账 + 合并持仓）
   - `bench` 只做总入口与失败码汇总，不得在 `bench` 里长第二套计算路径
6. **B5 真值**：建议新文件 `data/truth/merge_truth.json`（合并前后总收益、逐笔划转清单、
   合并持仓），形态期望写进去（`merge.*` 4 个形态已登记在 `coverage.surfaces`）
7. **`run` 与逐命令产物逐字节一致**：`run` 只是编排，不得有第二套计算路径

### parser 现状（M5 要补参数）

CLI 里 M5 四命令的 parser **已存在但参数不全**，落地前先看 `src/investment_attribution/cli.py`：

| 命令 | 现有参数 | 缺什么 |
|---|---|---|
| `merge` | `--accounts` / `--out` | **缺 `--ledger`（必填）与 `--method`（必填，无默认值）** |
| `report` | `--merged` / `--out` / `--json-out` | 需明确输入到底是 `attribution.json` 还是 `merged.json`；`--method` 需被复述到页眉 |
| `run` | `input...` / `--cost-method` / `--out-dir` | `--cost-method` 无默认值要对齐 `--method` 命名（现有其它命令用 `--method`，这里用 `--cost-method`，**要么统一要么在 README 写明**）；还需 `--valuation` |
| `bench` | `--only` | 需要 `--ledger` / `--valuation` 或直接吃样例（建议直接吃 `data/samples` 自跑，与 CI 一致） |

> 注意：`run` 的 parser 有**位置参数**，所以 `python -m investment_attribution run`（不带参数）
> 是 argparse 的 `exit 2`，**不是** 3。要测"阶段未就绪"必须传一个占位输入（如 `run x.csv`）。
> CI 与 `tests/test_cli_contract.py` 现都按这个事实写。

### 不要做

- 不要在 M5 改 `attribution/` 的现金流口径或 `ledger/` 的行形态分类
- 不要重新实现 B2–B4 比对逻辑（`ledger/` / `actions/` / `attribution/` 下三份 `verify.py`
  已是唯一实现）；B1 需要的是**从测试里抽出来**，不是再写一份
- 不要把业务判定写进渲染层（M5 自带 AST 门禁会红）
- 不要在报告里外链任何 CSS / JS / 字体 / CDN

## 三、既定口径清单（动了会打挂测试）

| 编号 | 约定 | 备注 |
|---|---|---|
| **K1** | 退出码：0 成功 / 1 降级或检出问题 / 2 用法或输入不可用 / 3 阶段未就绪 | `import` / `cost` / `pnl` / `corporate-actions` / `attribution` 已退役 3 |
| **K2** | JSON 冻结契约：`ensure_ascii=False` + `sort_keys=True` + `indent=2` + LF + 结尾换行 | 所有产物字节 |
| **K3** | 写盘唯一入口 `serialize.write_json_atomic` | 绕过即失去确定性 |
| **K4** | 数据目录：显式 `--data-dir` > `IA_DATA_DIR` > 源码树探测；**不做 CWD 上溯** | |
| **K5** | 审计行必带 `source_file` / `source_sha256` / `source_line_no` | M2 / M3 / M4 产物已继承 |
| **K6** | 编码探测顺序 `UTF-8(BOM) → UTF-8 → GB18030`，不猜测、不静默替换 | `params.encoding_order` 锁定 |
| **K7** | 口径参数无默认值 | `--method`（cost / pnl / corporate-actions / attribution）已锁；M4 的 `--twr-basis` **有默认值** `cashflow`（`params.twr.default_basis`），这是既定口径不是遗漏 |
| **K8** | 数值口径唯一来源 = `data/README.md §四` | M2 细则 §4.1.1，M3 细则 §4.1.2，M4 细则 §4.2.1 / §4.3.1；M5 的合并抵消需在 §4 补一节 |
| **K9** | 假代码段 `699000–699099`、名称 `示例` 前缀、资金账号 `Y0000001–Y0000009`、股东账号 `A000000001–A000000009` | 白名单扫描锁定 |
| **K10** | 真值台账只存"输入 + 期望"；出现 `observed_*` **键**即红 | 四份真值都有测试 |
| **K11** | 冻结产物禁手改，改需求走生成器整目录重生成 | `data/rules/`、`data/samples/`、`data/truth/` |
| **K12** | 未落地命令必须返回 3，禁止返回 0 或伪造空结果 | 现只剩 `merge` / `report` / `run` / `bench`；M5 结束时退出码 3 应完全消失 |

**退役五处同步（M5 要连做四次）**：① `stages.py` 删条目 → ② `cli.py` 注册
`HANDLERS` → ③ README 命令表与退出码表 → ④ `plan/00` 决策记录 → ⑤
`test_stage_registry.py` 防复活断言 + `test_cli_contract.py` 改负断言
（⑤ 之外还有第六处：`.github/workflows/ci.yml` 的阶段契约步骤，
M5 结束时该步骤应整段删除）。

## 四、M4 新增、容易被踩的约定

| 约定 | 说明 |
|---|---|
| **运行期探针已在 `main()` 常驻** | `main()` 把整套命令包在 `armed_runtime_probe()` 里；M5 的 HTML 渲染不需要再自己装探针，**但也不得绕过 `main()` 直接调 handler**（会失去武装状态） |
| **探针不 import socket** | 静态 AST 门禁禁止包源码 import 任何网络库。要探测网络只能靠 `sys.addaudithook` 的事件名前缀，不要为了写测试去 `import socket`（测试文件在 `tests/` 下不受门禁约束，但源码里不行） |
| **`_check_valuation_quantities` 是组合口径** | 它把**所有账户**持仓相加后与估值表声明比对（估值表一行 = 全组合一行）。不要改成"逐账户比对"，那样每行都会假报 |
| **合并视图不需要抵消逻辑** | 内部划转两腿同日相反，按日期相加自然相消。M5 要做的是**列出**逐笔划转并给"已抵消"证据，不是去修加法 |
| **拆分守恒走两条独立算法** | 左边现金流口径（期末总资产 − 净投入）、右边成本口径（已实现 + 未实现），差 > 1 分落 `SPLIT_NOT_CONSERVED`。M5 合并后的守恒要沿用同一对算法，不要只搬一个数 |
| **待确认现金行既是收入也是 warning** | `UNCLASSIFIED_CASH_ROW` 落 `warnings` 不计 `invalid`，命令仍 exit 0。样例里恒有一条，报告"待处理"计数必须把它算进去 |
| **基准缺失是正常路径** | `--benchmark` 不给 → `benchmark.provided == false`、超额列不显示、`exit 0`。不要把它做成 `exit 1` |
| **真值独立实现** | M4 真值用 `Decimal`（prec 40）+ 纯二分，与引擎的浮点 Newton 互为判据。M5 真值同样要独立实现，并附一份手工算例 |
| **契约版本每里程碑升一档** | M4 为 `0.5.0-m4`；M5 改 `CONTRACT_VERSION = "0.6.0-m5"` 后必须整目录重生成再 `--check` |
| **归因产物字段名已被测试锁定** | 真值里叫 `realized_sells_fen`，产物里叫 `realized_from_sells_fen`，靠 `verify.AMOUNT_FIELD_MAP` 映射。M5 报告直接消费产物字段，别用真值字段名 |

## 五、M4 产物里 M5 直接可用的东西

`attribution.json` 顶层键：

| 键 | 内容 | M5 用途 |
|---|---|---|
| `basis` | 成本口径 + TWR 估值点口径 + 基准是否提供 + 估值日 + 三条 convention 说明 | **报告页眉原样复述**（口径必须可见） |
| `inputs` | 流水 sha256 / 估值表描述 / 行业映射 / xirr·twr 参数原文 / sections | 报告"数据来源"段与下钻锚点 |
| `pnl.accounts[]` | 每账户：`net_contribution_fen` / `realized_total_fen` / `unrealized_fen` / `terminal_market_value_fen` / `terminal_cash_fen` / `total_return_fen` / `difference_fen` / `positions[]` | 合并视图的分账户明细 |
| `pnl.merged` | 与账户同构的合计行 | 报告首页大字报 |
| `pnl.checks` | `conserved` / `max_difference_fen` / `tolerance_fen` | 报告"对账通过"标记 |
| `fees` | `total_fen` / `by_type_fen` / `by_account` / `contributions_fen` / `drag_bps` / `matches_m2_total` | 费用段 |
| `decomposition` | `by_symbol` / `by_industry` / `by_account` / `by_year` + `checks.all_equal` + `unclassified_symbols` | 分解表 + "未分类"可见性 |
| `benchmark` | `provided` + 超额列（提供时） | 基准段（缺失时整段隐藏） |
| `xirr.accounts[]` / `twr.accounts[]` | 每账户与合并视图的率值、净投入、期末值、TWR 分段表 | 收益率表与净值曲线 |
| `invalid` / `warnings` | 问题段（退出码 1 的依据）与提示段 | 报告"待处理"计数与清单 |
| `zero_upload.runtime_probe` | `installed` / `armed` / `blocked_attempts` | 报告"零上传"证据段 |

> `merge` 的输入建议直接吃 `attribution.json`（它已含分账户与合并两套数），
> 再加"逐笔划转台账"这一层增量，不要从 `holdings.json` 重新走一遍成本引擎。

## 六、M5 样例里已注入、直接可用的事实

| 事实 | 位置 | 用途 |
|---|---|---|
| 账户一 / 二 / 三 / 四 | `htsec-demo-001.xlsx` / `huaxi-demo-001.xls` / `generic-demo-013-account-c.csv` / `generic-demo-015-no-fee-columns.csv` | 三账户以上合并 |
| 内部划转 ±20000.00（合同号 `TR-A-B-0001`） | 两文件 2025-10-10 | 划转识别与两端剔除 |
| 同名标的重叠持仓（699001） | 账户一 + 账户二 | 合并持仓去重/汇总 |
| 未登记账户样例 | `generic-demo-014-unregistered.csv` | 打码与"待确认账户"可见性 |
| 成本场景四件套真值 | `data/truth/cost_truth.json` | B2（`bench --only B2`） |
| 公司行为真值 | `data/truth/actions_truth.json` | B3 |
| 归因真值 | `data/truth/attribution_truth.json` | B4 |
| 多日估值序列 | `data/samples/valuation-demo-001.csv` | 报告净值曲线 |

> `merge.*` 4 个形态已登记在 `coverage.surfaces`（`merge.two_accounts` /
> `merge.three_accounts` / `merge.internal_transfer` / `merge.overlapping_symbol`），
> 但**还没有 `merge_truth.json`**。M5 必须把逐项期望值写进真值库，否则
> "合并前后总收益不变"无从断言。

## 七、本机环境坑（只写实测过的）

- **两条 Python 通道**：`py -3.8` 与 `py -3.12`。两条都要跑全量测试。
- **用 `py` 不用 `python`**：本机 `python` 解析到 WindowsApps 占位符，直接返回 **49**，
  什么都不做。用 `python` 跑任何东西前先 `py -3.12 --version` 自查。
- **中文控制台**：跑命令加 `PYTHONUTF8=1`。
- **Git Bash 参数改写**：以 `/` 开头的参数会被 MSYS 转写成 Windows 路径。临时目录用**仓库内相对路径**。
- **Bash heredoc 会吞反斜杠**：`<<'PY' ... PY` 里的 `\\` 与续行符会被改写（实测丢了
  多行命令的续行）。要写含反斜杠的脚本/文档，用编辑器工具落文件再执行，不要走 heredoc。
- **pytest 汇总行**：`addopts = "-q"` 已在 `pyproject.toml`；再叠 `-q` 会让末行汇总被
  进度行覆盖，取"N passed"请用 `py -3.12 -m pytest 2>&1 | tr '\r' '\n' | tail -3`。
- **pytest 7.4.4** 已装；`testpaths = tests`。
- **`tests/context.py` 把 `src/` 插入 `sys.path`**：源码态无需 `pip install -e .`。
- **`tests/test_gen_data.py` 会把 `tools/` 插进 `sys.path`**：引擎代码不要反向依赖 `tools/`。
- **`test_no_placeholder_sentinels_in_sources`**：`src/` 与 `tests/` 里不得出现
  `NotImplementedError` 等占位词。写抽象接口时用 `ValueError` 或具体实现。
- **`data/` 由生成器整体重写**：改了 `CONTRACT_VERSION` 或规则包结构后必须
  `py -3.12 tools/gen_data.py` 重生成，再 `--check` 确认字节一致（当前 28 个产物）。
- **`out/` 是临时目录**：验证完随手 `rm -rf out`，别让它进 git status。

## 八、M5 DoD 逐项 checklist

- [ ] B5 合并前后总收益不变（≤ 0.01 元，`transfer_offset_tolerance_cny`）
- [ ] 逐笔划转台账可查（合同号 + 两腿 + 已抵消证据）
- [ ] 0 外链静态扫描（报告里没有任何 `http://` / `https://` / `//` 外部引用）
- [ ] 断网全流程 `exit 0`（整套链路在武装探针下跑完，`blocked_attempts == 0`）
- [ ] 报告双跑 sha256 相同
- [ ] 无完整账号形态（打码发生在生成阶段）
- [ ] 每个汇总数可下钻到原始行（`source_file` / `source_sha256` / `source_line_no`）
- [ ] `run` 与逐命令产物逐字节一致
- [ ] 渲染层零判定 AST 门禁（三条 + 正/负对照）
- [ ] `merge` / `report` / `run` / `bench` 四条 `STAGE_NOT_READY` 退役，
      五处同步 + CI 阶段契约步骤**整段删除**
- [ ] 4 个 `merge.*` 形态逐项期望写进 `data/truth/merge_truth.json`
- [ ] 契约版本升到 `0.6.0-m5`，`gen_data.py --check` 仍全绿
- [ ] 3.8 / 3.12 双解释器全量 pytest 通过
- [ ] `selftest --strict` 仍 exit 0
- [ ] 写 `plan/RELEASE-M6.md` 之前的 `plan/HANDOFF-M6.md` 并 commit

## 九、关键命令速查

```bash
# 测试（双通道；注意末行汇总取法）
PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 py -3.8 -m pytest 2>&1 | tr '\r' '\n' | tail -3
PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 py -3.12 -m pytest 2>&1 | tr '\r' '\n' | tail -3

# 数据守门（改真值/契约版本后必跑）
PYTHONUTF8=1 py -3.12 tools/gen_data.py
PYTHONUTF8=1 py -3.12 tools/gen_data.py --check

# 自检
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution selftest --strict

# 归因（已可用；注意导入"成本场景四件套"）
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution import \
    data/samples/htsec-demo-001.xlsx data/samples/huaxi-demo-001.xls \
    data/samples/generic-demo-013-account-c.csv \
    data/samples/generic-demo-015-no-fee-columns.csv --out-dir out/attr-src/
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution attribution \
    --method fifo --ledger out/attr-src/ledger.json \
    --valuation data/samples/valuation-demo-001.csv --out-dir out/attr

# 未就绪契约（应为 3；run 必须带位置参数，否则是 argparse 的 2）
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution merge; echo $?
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution report; echo $?
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution bench; echo $?
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution run x.csv; echo $?
```

## 十、下一对话启动提示词

```
/goal 读取 "<项目绝对路径>\plan\HANDOFF-M5.md" 继续完成任务。
```
