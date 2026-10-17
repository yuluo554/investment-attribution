# HANDOFF-M6 — 从 M5 合并与报告到 M6 发布

> **当前对话已完成 M5**。本文件是 M6 的交接物：读完即可开工，不依赖对话记忆。
> 库内一律相对路径；启动命令由用户侧拼绝对路径。

## 一、当前进度与证据

### 已完成（M5，2026-10-17）

| 项 | 位置 | 证据 |
|---|---|---|
| 多账户合并（不重算成本，吃 `attribution.json`） | `src/investment_attribution/merge/engine.py` | `tests/test_merge.py` B5 段 |
| 逐笔划转台账（合同号 / 两腿 / 已抵消证据） | 同上 `_transfer_ledger` | `test_transfer_ledger_is_per_entry_with_both_legs` |
| 划转模式随规则包下发 | `data/rules/corporate_actions.json#transfer_patterns` | `test_transfer_patterns_come_from_the_rule_pack` |
| 合并持仓按标的汇总 + 账户计数 | `merge/engine.py::_merged_holdings` | `test_overlapping_symbol_is_aggregated_with_account_counts` |
| B5 比对（只读两边产物） | `merge/verify.py` | `test_b5_*` 九条可证伪探活 |
| 报告数据模型（口径复述 / 打码 / 下钻锚点） | `report/model.py` | `test_report.py` 全部 |
| 单文件 HTML 渲染层（零判定） | `report/render.py` | `test_report.py` 门禁段 |
| 渲染层零判定 AST 门禁 + 零外链出口扫描 | `report/guard.py` | 三条阳性对照 + 阴性对照 + 「贴判定必须红」对照 |
| 报告编排与退出码 | `report/engine.py` | `test_cli_report_*` |
| B1 判据抽成模块（测试与 bench 共用） | `ingest/verify.py` | `test_every_sample_matches_truth_ledger` 改调它 + `test_bench.py` |
| `merge` / `report` / `run` / `bench` 四条子命令 | `pipeline/merge_cmd.py` `report_cmd.py` `run_cmd.py` `bench_cmd.py` | `test_merge.py` / `test_report.py` / `test_run.py` / `test_bench.py` |
| 退出码 3 整体退役（删登记表与未就绪异常类） | `exit_codes.py` / `errors.py` / 已删 `stages.py` | `test_stage_registry.py`（含"源码无 `STAGE_NOT_READY` 残留"） |
| B5 真值（合并前后总收益 + 划转台账 + 合并持仓 + 3 条手工算例 + 4 形态期望） | `data/truth/merge_truth.json` | `tests/test_gen_data.py` M5 段 10 条 |
| 契约版本升档 + 规则包新增划转模式 | `tools/gen_data.py`（`0.6.0-m5`） | `test_contract_version_is_m5` |
| CI 阶段契约步骤整段删除，换四条新冒烟 | `.github/workflows/ci.yml` | **未实跑**（无远端），命令已在本机逐条实跑 |

### 本地验证结论（全部实跑）

- `py -3.8 -m pytest` 与 `py -3.12 -m pytest` 均为 **419 passed**
- `py -3.12 tools/gen_data.py --check` → `[shape] ok` + `[bytes] ok (29 artifacts)`，exit 0
- `py -3.8 / -3.12 -m investment_attribution selftest --strict` → 均 exit 0；
  新增检查 `renderer_no_decisions`（`0 business decisions in report renderer`）与
  `report_zero_external_links`（`clean=0 dirty=2`，含阳性对照）
- `bench` → **B1 17 / B2 92 / B3 16 / B4 58 / B5 18 项比对，0 违例**，exit 0
- 合并实跑（成本场景四件套 / 4 账户 / 估值日 2025-12-31 / fifo）：
  - 合并前后总收益 `798438` 分（7984.38 元），`difference_fen == 0`，门槛 1 分
  - 划转 `TR-A-B-0001` 2025-10-10 / 20000.00 元 / 2 腿 / 净额 0 / 已抵消
  - 合并持仓 3 个标的；699001 `account_count_ever == 2`、`at_terminal == 1`
  - `zero_upload.runtime_probe.blocked_attempts == 0`
- 报告实跑：13 个段落 / 8 个汇总数 / 41 行表格；外链 0 处；运行期拦截 0 次；
  4 处账号在生成阶段打码；HTML 与 JSON 均无 `[YA]\d{7,9}` 形态
- `run` 实跑：7 步；`import/cost/pnl/attribution/merge/report = 0`、
  `corporate-actions = 1`（样例含待确认行）→ 总退出码 `1`；13 份产物齐全
- `run` 与逐命令**逐字节一致**：`test_run_matches_per_command_byte_for_byte`（同目录同路径字符串）
- CI 步骤本机等价复跑：merge 0 / report 0 / 零外链与打码 grep 无命中 / run 1 /
  bench 0 / `bench --only B9` 2
- **未接远端，CI 待首推验证**

### M5 已知偏差（写清以免被当成 bug）

1. **`run` 在随包样例上返回 1，不是 0**。样例刻意注入一条规则表认不出的现金行
   （`其他业务甲`），`corporate-actions` 按契约返回 1（有待确认行），`run` 取各步
   最大值故为 1。这是**数据结论**；改成 0 才是隐瞒问题。要验 `exit 0` 用
   `merge` / `report` 两条命令。
2. **B1 不比 `errors[].raw_line` 与 `fingerprint.encoding/delimiter`**。真值的
   `raw_line` 是**源文件原文**、导入台账是**解码后逐格重新拼接**（含引号的单元格
   不会重新加引号）；真值的 `encoding/delimiter` 是**声明形态**（`binary-xlsx` /
   `utf-8` / 制表符）、指纹里是**探测结果**（`xlsx` / `utf-8-sig` / `tab`）。
   两侧都正确，只是词汇不同。字节一致性由 `fingerprint.sha256` 守，行级错误由
   `(source_line_no, reason_code)` 守。**若 M6 想收紧这项，正确做法是把
   `import_errors.json` 的 `raw_line` 改成真源文件原文**（需要动 `ingest/tables.py`
   让分隔文本读取器保留原始行文本），而不是把两套词汇硬对齐。
3. **报告的输入路径按调用方传入原样记录**。`run` 用 `Path` 规范化拼接中间产物路径，
   因此做逐字节比对时 `--ledger` 要传与 `run` 相同形态的路径。
4. **报告退出码跟随上游**：`merged.json` 有 `invalid` 时报告仍生成，但退出码为 1。
5. **报告不含图表**。本期是表格 + KPI 卡片，不引入任何前端依赖；若要图表，
   必须仍是零外链（内联 SVG 或 CSS 画，不许引图表库）。

## 二、下一棒待办（M6 · 发布）

### 必做（照 `plan/05` M6 段与 `plan/06` §五 发布检查单执行）

1. **脱敏四步 + 产物扫描 + 提交元数据邮箱检查**
   - 源码 / 产物 / 提交元数据 / 历史四面各扫一遍；
   - 检查 `git log` 的 author / committer / tag tagger 邮箱是否要改；
   - 确认 `data/samples/` 全是合成样例（生成器 `--check` 就是这条的守门）。
2. **建仓**：`gh repo create yuluo554/investment-attribution --public`
3. **CI 首推**（预算诊断轮）：push 后看 `ci.yml` 四矩阵（windows/ubuntu × 3.8/3.12）。
   本机未验的部分正是**跨 OS 差异**：报告里的路径字符串形态、`grep -qE` 的
   portable 行为、`set +e` 退出码断言。首推大概率要在这里调一两轮。
4. **`plan/RELEASE-M6.md`**（**必须在 tag 之前落盘并进 tag 树**）
5. **annotated tag + GitHub Release 附产物**（源码包 + sha256）
6. **干净 clone 复核**（`git clone --no-local` + 新 venv + 全量测试 + README 逐条）
   与**发布物本体复核**（下载附件算 sha256 与留档一致）

### 不要做

- 不要在 M6 改任何口径（`data/README.md §四` 已是唯一定义处，改它要重生成整个 `data/`）
- 不要为了让 CI 变绿而放宽断言（本机全绿，CI 若红要查环境差异而不是删测试）
- 不要把 `bench` 的中间产物改成仓库内固定目录（它自跑自清，是设计而非偶然）
- 不要删除 `plan/` 下任何 HANDOFF（留档链本身就是交付物）

## 三、既定口径清单（动了会打挂测试）

| 编号 | 约定 | 备注 |
|---|---|---|
| **K1** | 退出码：0 成功 / 1 降级或检出问题 / 2 用法或输入不可用 | **3 已完全退役**（`ALL_EXIT_CODES == (0, 1, 2)`，`stages.py` 与未就绪异常类已删） |
| **K2** | JSON 冻结契约：`ensure_ascii=False` + `sort_keys=True` + `indent=2` + LF + 结尾换行 | 所有产物字节 |
| **K3** | 写盘唯一入口 `serialize.write_json_atomic` | 绕过即失去确定性 |
| **K4** | 数据目录：显式 `--data-dir` > `IA_DATA_DIR` > 源码树探测；**不做 CWD 上溯** | `run --out-dir` 也因此**必填** |
| **K5** | 审计行必带 `source_file` / `source_sha256` / `source_line_no` | M2/M3/M4/M5 产物与报告锚点全部继承 |
| **K6** | 编码探测顺序 `UTF-8(BOM) → UTF-8 → GB18030`，不猜测、不静默替换 | `params.encoding_order` 锁定 |
| **K7** | 口径参数无默认值 | `--method`（cost / pnl / corporate-actions / attribution / merge / run）已锁；`--twr-basis` 有默认值 `cashflow`（`params.twr.default_basis`，既定口径不是遗漏） |
| **K8** | 数值口径唯一来源 = `data/README.md §四` | M5 细则在 **§4.6**（合并与报告口径） |
| **K9** | 假代码段 `699000–699099`、名称 `示例` 前缀、资金账号 `Y0000001–Y0000009`、股东账号 `A000000001–A000000009` | 白名单扫描锁定；报告打码只保留首尾各 2 位 |
| **K10** | 真值台账只存"输入 + 期望"；出现 `observed_*` **键**即红 | 五份真值都有测试 |
| **K11** | 冻结产物禁手改，改需求走生成器整目录重生成 | `data/rules/`、`data/samples/`、`data/truth/`（当前 29 个产物） |
| **K12** | ~~未落地命令返回 3~~ **本条目已随退出码 3 退役而失效** | 分派完备性改由 `test_stage_registry.py` 的 `SUBCOMMANDS ⊆ HANDLERS 且无多余分派` 守住 |

## 四、M5 新增、容易被踩的约定

| 约定 | 说明 |
|---|---|
| **退出码 3 已不存在** | 源码里出现 `STAGE_NOT_READY` 或 `StageNotReady` 会被 `test_stage_registry.py` 判红；别再写"阶段未就绪"的活代码 |
| **`merge` 需要 `--attribution`** | 合并**不重算成本**；`--method` 必须与归因产物口径一致，不一致是 exit 2 |
| **`report` 不接受 `--method`** | 报告只吃 `merged.json`，口径从产物复述到页眉；在 CLI 再传一遍口径会被当成"两个地方写口径" |
| **渲染层零判定是硬门禁** | 往 `report/render.py` 里加任何业务词（成本口径 / 事件类型 / 收益口径）都会红；判定要放进 `report/model.py` |
| **报告出口扫描对 `//` 也判红** | 报告 HTML 里连注释、CSS、文案都不能出现 `//`（协议相对地址会被当外链）。写文案用" / "而不是"//" |
| **`bench` 自跑自清** | 它用临时目录跑随包样例，跑完即删；`workdir=` 参数只为测试留档用 |
| **`run` 的口径参数叫 `--method`** | 老的 `--cost-method` 已改名；README 与 CI 都已同步 |
| **`run` 的退出码 = 各步最大值** | 样例上是 1（待确认行）；这是数据结论，不是失败 |
| **`_merge_reports` 把逐口径结论并成套件级** | bench 的 B3/B4/B5 各自跑 fifo + wavg 两遍，违例逐条带 `method` 标签 |
| **报告打码顺带登记"打码了几处"** | 只登记**打码后**的形态（`masking.masked_samples`）；登记原文等于把打码又撤回 |
| **契约版本每里程碑升一档** | M5 为 `0.6.0-m5`；改它必须整目录重生成再 `--check` |
| **归因产物字段名已被测试锁定** | 真值里叫 `realized_sells_fen`，产物里叫 `realized_from_sells_fen`，靠 `verify.AMOUNT_FIELD_MAP` 映射 |

## 五、M5 产物里 M6 直接可用的东西

| 产物 | 内容 | M6 用途 |
|---|---|---|
| `merged.json` | 合并视图 + 逐笔划转台账 + 合并持仓 + `checks` + `provenance` | 发布留档的示例产物；Release 附件的候选 |
| `report.html` / `report_data.json` | 单文件报告 + 机读版（同源） | Release 附件（演示报告可附一张截图或 HTML 本体） |
| `bench` 输出 | B1–B5 逐套件 `checked` / `violation_count` | 发布留档的"基准可复跑"证据 |
| `selftest --strict` 输出 | 9 条检查明细（含运行期探针与渲染门禁） | 干净 clone 复核的第一步 |
| `tools/gen_data.py --check` | 形态 + 29 产物逐字节 | 干净 clone 复核的第二步 |

## 六、本机环境坑（只写实测过的）

- **两条 Python 通道**：`py -3.8` 与 `py -3.12`。两条都要跑全量测试。
- **用 `py` 不用 `python`**：本机 `python` 解析到 WindowsApps 占位符，直接返回 **49**，什么都不做。
  本次实测又踩了一次：用 `python - <<'PY'` 跑内联脚本时静默失败（exit 49）。
- **中文控制台**：跑命令加 `PYTHONUTF8=1`。
- **Git Bash 参数改写**：以 `/` 开头的参数会被 MSYS 转写成 Windows 路径。临时目录用**仓库内相对路径**。
- **Bash heredoc 会吞反斜杠**：`<<'PY' ... PY` 里的 `\\` 与续行符会被改写。要写含反斜杠的
  脚本/文档，用编辑器工具落文件再执行（本次改 `plan/06` 就是这么做的：写 `_tmp_patch06.py`
  → `py -3.12 _tmp_patch06.py` → 删掉）。
- **pytest 汇总行**：`addopts = "-q"` 已在 `pyproject.toml`；取"N passed"请用
  `py -3.12 -m pytest 2>&1 | tr '\r' '\n' | tail -3`。
- **pytest 7.4.4** 已装；`testpaths = tests`。
- **`tests/context.py` 把 `src/` 插入 `sys.path`**：源码态无需 `pip install -e .`。
- **`test_no_placeholder_sentinels_in_sources`**：`src/` 与 `tests/` 里不得出现
  `NotImplementedError` 等占位词。写抽象接口时用 `ValueError` 或具体实现。
- **`data/` 由生成器整体重写**：改了 `CONTRACT_VERSION` 或规则包结构后必须
  `py -3.12 tools/gen_data.py` 重生成，再 `--check` 确认字节一致（当前 **29 个产物**）。
- **`out/` 是临时目录**：验证完随手 `rm -rf out`，别让它进 git status。
- **`test_run.py` 里的 `__import__("pathlib").Path(...)` 不是笔误**：它让逐命令那一路
  与 `run` 内部拼接出的路径字符串**一模一样**；改成传普通字符串就会让逐字节断言失去意义。

## 七、M6 DoD 逐项 checklist

- [ ] 三件套齐：tag / Release 附产物 / `plan/RELEASE-M6.md`（留档须在 tag 前落盘）
- [ ] 干净环境 clone 按 README 逐条跑通
- [ ] CI 四矩阵绿（push 与 tag 各一条 run 均绿）
- [ ] 下载件 sha256 与留档一致
- [ ] 脱敏扫描 0 命中（含 tag tagger 邮箱）
- [ ] README 状态行转为正式版
- [ ] `plan/00` 里程碑表 M6 行翻 ✅；`plan/06` §五 检查单逐项打勾

## 八、关键命令速查

```bash
# 测试（双通道；注意末行汇总取法）
PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 py -3.8 -m pytest 2>&1 | tr '\r' '\n' | tail -3
PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 py -3.12 -m pytest 2>&1 | tr '\r' '\n' | tail -3

# 数据守门（改真值/契约版本后必跑）
PYTHONUTF8=1 py -3.12 tools/gen_data.py
PYTHONUTF8=1 py -3.12 tools/gen_data.py --check

# 自检
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution selftest --strict

# 完整链路（一条命令出 13 份产物）
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution run \
    data/samples/htsec-demo-001.xlsx data/samples/huaxi-demo-001.xls \
    data/samples/generic-demo-013-account-c.csv \
    data/samples/generic-demo-015-no-fee-columns.csv \
    --method fifo --valuation data/samples/valuation-demo-001.csv --out-dir out/chain/; echo $?

# 分步（合并 / 报告）
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution import \
    data/samples/htsec-demo-001.xlsx data/samples/huaxi-demo-001.xls \
    data/samples/generic-demo-013-account-c.csv \
    data/samples/generic-demo-015-no-fee-columns.csv --out-dir out/attr-src/
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution attribution --method fifo \
    --ledger out/attr-src/ledger.json \
    --valuation data/samples/valuation-demo-001.csv --out-dir out/attr
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution merge --method fifo \
    --ledger out/attr-src/ledger.json --attribution out/attr/attribution.json --out-dir out/merge
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution report \
    --merged out/merge/merged.json --out-dir out/report

# 基准套件
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution bench
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution bench --only B5
```

## 九、下一对话启动提示词

```
/goal 读取 "<项目绝对路径>\plan\HANDOFF-M6.md" 继续完成任务。
```
