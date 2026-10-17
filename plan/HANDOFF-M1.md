# HANDOFF-M1 — 从 M0 骨架到 M1 数据先行

> **当前对话已完成 M0**。本文件是 M1 的交接物：读完即可开工，不依赖对话记忆。
> 库内一律相对路径；启动命令由用户侧拼绝对路径。

## 一、当前进度与证据

### 已完成（M0，2026-10-17）

| 项 | 位置 | 证据 |
|---|---|---|
| CLI 骨架（七子命令 + `run` / `bench` / `selftest`） | `src/investment_attribution/cli.py` | `tests/test_cli_contract.py` |
| 退出码契约 0/1/2/3 | `src/investment_attribution/exit_codes.py` | 同上；README 退出码表 |
| 领域异常继承 `ValueError` | `src/investment_attribution/errors.py` | CLI `except IAError` 转译 |
| 路径解析（显式 > `IA_DATA_DIR` > 源码树，不落 CWD） | `src/investment_attribution/paths.py` | `test_cli_contract.py::test_missing_data_dir_returns_two` |
| 确定性 JSON 写盘（唯一入口） | `src/investment_attribution/serialize.py` | `tests/test_serialize.py` |
| 契约清单加载与结构校验 | `src/investment_attribution/manifest.py` | `tests/test_serialize.py` |
| 可证伪自检 | `src/investment_attribution/selftest.py` | 空目录 exit 2 / 真实目录 exit 0 |
| 零上传静态门禁（AST） | `src/investment_attribution/netguard.py` | `tests/test_netguard.py`（正/负对照） |
| 阶段未就绪登记表 | `src/investment_attribution/stages.py` | `tests/test_stage_registry.py` |
| 契约清单（门槛 + 30 形态覆盖度） | `data/rules/manifest.json` | `tests/test_serialize.py` |
| 计划文档 00–06 | `plan/` | 本轮落盘 |
| CI 预置（3.8/3.12 × windows/ubuntu） | `.github/workflows/ci.yml` | **未推送，未实跑** |

### 本地验证结论

- `py -3.8 -m pytest` 与 `py -3.12 -m pytest` 均为 **33 passed**
- `python -m investment_attribution selftest --strict` → `exit 0`，6 项检查全绿
- `python -m investment_attribution import x.csv` → `exit 3`（阶段未就绪，不产出结果）
- **未接远端，CI 待首推验证**

## 二、下一棒待办（M1）

### 必做

1. **写 `tools/gen_data.py`**（标准库，确定性）
   - 自研 splitmix64 类 RNG，固定 seed，不读时钟、禁 `random`
   - 产出 `data/rules/`：`aliases.json`、`corporate_actions.json`、
     `params.json`、`industries.json`
   - 产出 `data/samples/`：海通 xlsx、华西 `.xls`（Tab 分隔 + GB18030 + `="..."`）、
     UTF-8 CSV、GB18030 CSV、各类异常形态
   - 产出 `data/truth/`：真值台账（只存输入 + 期望）
   - 填充 `data/rules/manifest.json` 的 `coverage.surfaces[*].total/sampled`
   - `--check`：重生成到临时目录，与冻结产物逐字节比对

2. **实现 `import` 子命令**
   - 编码探测白名单：`UTF-8(BOM) → UTF-8 → GB18030`，全失败报错
   - `="..."` 整格剥离，剥离后非法落 `invalid`
   - 列名归一（冻结别名表），未登记列记 `unknown`，错位行不入库
   - 行级错误台账（原始行文本 + 行号 + 原因）
   - SHA-256 文件去重（改名 / 改扩展名识别为同一件）
   - 账户—股东账号映射（不自动猜测）

3. **退役 `STAGE_NOT_READY["import"]`** —— 五处同步：
   `stages.py` 常量表 → `cli.py` 分派（加 `HANDLERS["import"]`）→
   README 退出码/命令表 → `plan/00` 决策记录 → 测试负断言。

4. **补测试**
   - `gen_data --check` 两层守门（产物形态 + 重生成逐字节）
   - 两类真实格式 100% 入库、逐行列值可回放
   - 重复导入 0 重复流水
   - 异常行逐条可回放
   - 样例白名单扫描（代码段 / `示例` 前缀 / 假账号）

### 不要做

- 不要在 M1 实现成本引擎（M2）、公司行为（M3）、归因（M4）、报告（M5）
- 不要为凑覆盖率造假规则/假用例
- 不要把真实交割单或真实账号写进任何入库文件

## 三、既定口径清单（动了会打挂测试）

| 编号 | 约定 | 改动影响 |
|---|---|---|
| **K1** | 退出码：0 成功 / 1 降级或检出问题 / 2 用法或输入不可用 / 3 阶段未就绪 | 五处同步，且 3 是临时语义 |
| **K2** | JSON 冻结契约：`ensure_ascii=False` + `sort_keys=True` + `indent=2` + LF + 结尾换行 | 所有产物字节，改则全部双跑测试红 |
| **K3** | 写盘唯一入口 `serialize.write_json_atomic`（原子 rename） | 绕过即失去确定性与防中断保证 |
| **K4** | 数据目录优先级：显式 `--data-dir` > `IA_DATA_DIR` > 源码树探测；**不做 CWD 上溯** | 运维与测试路径语义 |
| **K5** | 审计行必带 `source_file` / `source_sha256` / `source_line_no`（源文件行号，非数组下标） | 排序键与下钻能力 |
| **K6** | 编码探测顺序 `UTF-8(BOM) → UTF-8 → GB18030`，不猜测、不静默替换 | 导入层全部样例 |
| **K7** | `--cost-method` 无默认值，必须显式 fifo/wavg | CLI 契约与报告页眉 |
| **K8** | 数值口径唯一来源 = `data/README.md §四` | 任何第二份口径表述都算缺陷 |
| **K9** | 假代码段 `699000–699099`、名称 `示例` 前缀、资金账号 `Y0000001–Y0000009`、股东账号 `A000000001–A000000009` | 样例与真值全部依赖 |
| **K10** | 真值台账只存"输入 + 期望"；出现 `observed_*` 即红 | 基准有效性 |
| **K11** | 冻结产物禁手改，改需求走生成器整目录重生成 | 所有 `data/` 产物 |
| **K12** | 未落地命令必须返回 3，禁止返回 0 或伪造空结果 | 防假绿 |

## 四、本机环境坑（只写实测过的）

- **本机有两条 Python 通道**：`py -3.8` 与 `py -3.12`（`py -0p` 确认）。两条都要跑全量测试。
- **用 `py` 不用 `python`**：Windows 上 `python` 可能是商店占位符。
- **中文控制台**：跑命令加 `py -X utf8` 或 `PYTHONUTF8=1`；程序入口已有 stream 编码探测重配，
  但 CI 与子进程通路仍以显式编码为准。
- **Git Bash 参数改写**：以 `/` 开头的参数会被 MSYS 转写成 Windows 路径；涉及路径时用
  包含形式或 `cd` 后再执行。
- **pytest 7.4.4** 已装；`testpaths = tests`，无需额外配置。
- **`tests/context.py` 会把 `src/` 插入 `sys.path`**：源码态无需 `pip install -e .` 即可跑测试；
  安装后的通路由 `test_readme_gate.py` 单独验。

## 五、M1 DoD 逐项 checklist

- [ ] `tools/gen_data.py --check` 位级一致（两层：产物形态 + 重生成比对）
- [ ] 海通 xlsx 样例 100% 入库，逐行列值可回放
- [ ] 华西 `.xls`（Tab + GB18030 + `="..."`）样例 100% 入库
- [ ] 重复导入 0 重复流水；改名 / 改扩展名识别为同一件
- [ ] 异常行逐条可回放（原始行文本 + 行号 + 原因）
- [ ] 编码探测三分支测试：UTF-8 / GB18030 / 双失败报错
- [ ] 未登记列记 `unknown`（行仍可用）；错位列不入库
- [ ] 账户映射未登记落"待确认"，不并入默认账户
- [ ] `coverage.surfaces` 的 9 个 `import.*` 形态 `total == sampled`
- [ ] 样例白名单扫描通过
- [ ] `STAGE_NOT_READY["import"]` 退役，五处同步
- [ ] 3.8 / 3.12 双解释器全量 pytest 通过
- [ ] `selftest --strict` 仍 exit 0
- [ ] 写 `plan/HANDOFF-M2.md` 并 commit

## 六、关键命令速查

```bash
# 测试（双通道）
PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 py -3.8 -m pytest -q
PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 py -3.12 -m pytest -q

# 自检
PYTHONPATH=src py -3.12 -m investment_attribution selftest --strict

# 未就绪契约（应为 3）
PYTHONPATH=src py -3.12 -m investment_attribution import x.csv; echo $?

# M1 开工后
py -3.12 tools/gen_data.py --check
PYTHONPATH=src py -3.12 -m investment_attribution import data/samples/htsec-demo-001.xlsx --out-dir out/
```

## 七、下一对话启动提示词

```
/goal 读取 "<项目绝对路径>\plan\HANDOFF-M1.md" 继续完成任务。
```
