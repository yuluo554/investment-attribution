# investment-attribution · 券商交割单收益归因工作台

把券商客户端导出的交割单还原成一本**可审计的投资账**：持仓成本、已实现/未实现收益、
分红送转还原、多账户合并、XIRR/TWR 归因，每个数字都可下钻到原始行。

> **不做投资建议，只做账。** 所有输出均为历史核算结果，不构成任何投资建议。
> 纯本地、零上传、零遥测、不登录券商、不联网行情。断网可跑完整链路。

**状态：M2 成本引擎（2026-10-17）。** 当前仓库提供可运行的 CLI 契约、
`import` 子命令（海通 xlsx / 华西 Tab+GB18030 两类真实格式 + 通用 CSV/TSV）、
`cost` / `pnl` 子命令（FIFO / 移动加权双口径、费用入账、资金与股份余额勾稽、
每日持仓快照）、确定性合成数据生成器（`tools/gen_data.py --check` 双层守门）、
可证伪自检、零上传静态门禁与确定性写盘；其余业务子命令按里程碑逐个落地，
未落地时以退出码 `3` 显式拦截，**不输出伪造结果**。

---

## 这是什么 / 不是什么

| 是 | 不是 |
|---|---|
| 把你自己导出的交割单算成持仓台账与收益归因 | 券商接口、自动同步、代客交易 |
| 纯本地 CLI + 单文件 HTML 报告 | 云端账本、账号体系、订阅服务 |
| 双成本口径（FIFO / 移动加权）可切换 | 只给一个不写口径的成本价 |
| 公司行为（分红/送转/配股/拆分/分红再投）还原 | 猜一个税率就自动入账 |
| 多券商、多账户合并与账户间划转抵消 | 消费流水记账、预算、月度收支 |

## 环境要求

- Python **3.8+**（开发与 CI 覆盖 3.8 与 3.12 两条通道）
- 核心零第三方依赖；测试期只需 `pytest`

## 快速开始

```bash
git clone https://github.com/yuluo554/investment-attribution.git
cd investment-attribution
python -m pip install -e ".[dev]"
python -m investment_attribution selftest
```

也可以不安装，直接在源码树内运行：

```bash
PYTHONPATH=src python -m investment_attribution selftest
```

Windows（Git Bash / PowerShell 均可）：

```powershell
py -3.12 -m pip install -e ".[dev]"
py -3.12 -m investment_attribution selftest
```

安装后会同时提供控制台脚本 `investment-attribution`：

```bash
investment-attribution selftest
```

运行测试：

```bash
python -m pytest
```

## 命令一览

| 子命令 | 作用 | 状态 |
|---|---|---|
| `import` | 多券商交割单导入 → 统一流水 + 行级错误台账 | M1 ✅ |
| `cost --method fifo\|wavg` | 持仓成本台账（口径必填，无默认值） | M2 ✅ |
| `pnl [--fees]` | 已实现/未实现收益与费用三件套 | M2 ✅ |
| `corporate-actions` | 公司行为事件台账 + 人工确认队列 | M3 |
| `attribution [--xirr] [--twr]` | XIRR / TWR / 收益分解 | M4 |
| `merge` | 多账户合并与账户间划转抵消 | M5 |
| `report` | 单文件本地 HTML 报告 + `report_data.json` | M5 |
| `run` | 一条命令跑完整链路 | M5 |
| `bench [--only B1]` | 内置基准套件 B1–B5 | M5 |
| `selftest` | 可证伪自检（**当前可用**） | M0 ✅ |

### `import` 用法与退出码

```bash
# 两类已实测格式一起导入（xlsx / .xls 实为 Tab+GB18030 文本）
python -m investment_attribution import \
    data/samples/htsec-demo-001.xlsx data/samples/huaxi-demo-001.xls \
    --out-dir out/

# 指定统一流水 / 错误台账路径（覆盖 --out-dir 默认名）
python -m investment_attribution import data/samples/htsec-demo-001.xlsx \
    --out out/ledger.json --errors out/import_errors.json

# 用自己的账户登记表覆盖随包登记表
python -m investment_attribution import data/samples/htsec-demo-001.xlsx \
    --accounts my-accounts.json --out-dir out/
```

产物（全部经 `serialize.write_json_atomic` 落盘）：

| 文件 | 内容 |
|---|---|
| `ledger.json` | 统一流水（每行带 `source_file` / `source_sha256` / `source_line_no`） |
| `import_errors.json` | 行级错误台账（原始行文本 + 源文件行号 + 原因码） |
| `fingerprints.json` | 文件指纹与去重记录（改名 / 改扩展名识别为同一件） |
| `account_map.json` | 账户—股东账号映射；未登记账户落"待确认" |

`import` 退出码：`0` 全部入库；`1` 有行级失败或有未登记账户；
`2` 整份文件不可用（缺必填列 / 空文件 / 编码探测失败 / 仅表头 / 文件缺失）。
**整份文件不可用时不写任何产物**，避免留下"少了一个账户却看起来完整"的半份流水。

### `cost` / `pnl` 用法与退出码

```bash
# 口径必须显式指定（fifo / wavg），没有默认值
python -m investment_attribution cost --method fifo \
    --ledger out/ledger.json --out-dir out/fifo/

# 产物路径可直接指定；--fees 让 pnl 额外输出逐笔费用拆列
python -m investment_attribution cost --method wavg \
    --ledger out/ledger.json --out out/wavg-holdings.json

python -m investment_attribution pnl --method fifo \
    --ledger out/ledger.json --out-dir out/fifo/ --fees
```

产物（全部经 `serialize.write_json_atomic` 落盘）：

| 文件 | 内容 |
|---|---|
| `holdings.json` | 持仓台账（批次明细 / 逐笔结转 / 每日快照 / 勾稽问题）+ 口径页眉 |
| `snapshots.json` | 每日持仓快照（与台账同源，单独落盘便于下游消费） |
| `pnl.json` | 已实现 / 未实现 / 费用口径（`--fees` 时含逐笔费用拆列） |

`cost` / `pnl` 退出码：`0` 账本建成且无勾稽问题；`1` 数据可用但检出问题
（资金 / 股份余额勾稽不平、超卖、未登记账户、无法归类行）；
`2` 用法或输入不可用（**未指定 `--method`**、`--ledger` 缺失、流水形态不符）。

口径切换**不修改原始流水**：流水只读，成本 = 流水 + 口径参数的派生视图，
测试断言 `ledger.json` 字节前后一致。

## 退出码语义

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 降级完成 / 已检出问题（各子命令 README 段落会写明具体语义） |
| `2` | 用法错误或输入不可用（参数错、文件缺失、数据目录不存在） |
| `3` | **阶段未就绪**——该模块尚未落地，本次不产出任何结果 |

`3` 是骨架期的临时语义，随模块落地逐个退役；退役时同步常量表、CLI 分派、
本表、决策记录与测试。**M1 已退役 `import`**（现为 0 / 1 / 2 三档），
**M2 已退役 `cost` / `pnl`**（同为 0 / 1 / 2 三档），其余子命令仍返回 `3`。

## 数据目录

- 缺省：源码树内的 `data/`（安装为 editable 时同样有效）
- 也可用 `--data-dir DIR` 或环境变量 `IA_DATA_DIR` 显式指定
- 不做 CWD 上溯——避免"在哪个目录敲命令"改变结果

契约清单位于 `data/rules/manifest.json`，承载基准门槛与覆盖度登记。
M1 起，合成样例、列名别名表、公司行为规则表、参数包与真值台账统一由
`tools/` 下的生成器产出，配 `--check` 位级守门，**禁止手改**：

```bash
python tools/gen_data.py            # 生成 data/rules data/samples data/truth
python tools/gen_data.py --check    # 形态校验 + 重生成逐字节比对（两层守门）
```

## 隐私与安全

- **零上传**：不发起任何网络请求。静态层面由 `tests/test_netguard.py` 对
  全部包源码做 AST import 审计（阳性/阴性对照齐备）；运行期 socket 拦截
  随 M4 落地，**当前措辞只覆盖"本项目代码无网络调用路径"**，不宣称已做动态拦截。
- **报告阶段打码**：股东账号、资金账号在生成阶段即打码，HTML / JSON 从不携带完整账号。
- **真实交割单永不入仓**：样例一律程序化合成，使用假代码段 `699000–699099`
  与 `示例` 名称前缀、假账号白名单；真实件放 `_private/`（已忽略）。
- **不登录券商**：不接收、不存储、不传输任何券商账号或密码。

完整口径见 [`data/README.md`](data/README.md) §三、§五。

## 口径声明

成本口径、XIRR 算法与收敛条件、TWR 估值点、误差容忍度**只在
[`data/README.md` §四](data/README.md) 定义一次**；引擎、报告、基准全部引用同一份，
任何其他文件不得手写第二份。

当前契约门槛（随 `data/rules/manifest.json` 下发）：

| 门槛 | 值 |
|---|---|
| 持仓数量容忍度 | ≤ 1 股 |
| 持仓金额容忍度 | ≤ 0.01 元 |
| XIRR 相对误差 | < 0.01% |
| 已实现+未实现 拆分守恒 | ≤ 0.01 元 |
| 合并前后总收益（划转抵消） | ≤ 0.01 元 |

## 覆盖度声明

首期覆盖：**海通（.xlsx）** 与 **华西（.xls 实为 Tab 分隔文本、GB18030、
`="..."` 单元格前缀）** 两类已实测格式 + 通用 CSV/TSV 列名映射。
**不承诺"支持全部券商"**：未适配券商落 `unknown` 并列清单；基金 CSV / PDF
交割单 / 截图 OCR 为扩展项，不进首期 DoD。

逐形态覆盖度以 `data/rules/manifest.json` 的 `coverage` 段为准，
登记 `total / sampled` 且要求分子 = 分母（测试锁定）。当前共 **31 个形态**：
导入 9 类、成本 6 类（M2 新增"费用未分列"）、公司行为 6 类、归因 6 类、合并 4 类。

## 已知环境问题

- 首期支持**源码树 / editable 安装**。非 editable 的 wheel 安装下，`data/`
  不在包内，需要用 `--data-dir` 显式指定；本期不承诺 wheel 开箱可用。
- Windows 控制台若为 GBK，程序入口会按 stream 编码探测自动重配 UTF-8；
  用 `py -X utf8` 运行同样可行。

## 免责声明

本工具是个人持仓核算辅助工具，**不是财务/税务/法律工具，也不是官方对账凭证**。
输出为基于导出文件的确定性核算结果；与券商官方数据存在差异时以券商为准。
本工具**不荐股、不预测收益、不给交易信号、不做择时评价、不打分排名**。

## 许可

[MIT](LICENSE)。

## 仓库结构

```
src/investment_attribution/   CLI、基础设施层与业务模块
src/investment_attribution/ingest/   导入适配层（编码 / 壳清洗 / 列名归一 / 行级台账）
src/investment_attribution/ledger/   成本引擎（FIFO / 移动加权 / 勾稽 / 快照 / B2 比对）
src/investment_attribution/pipeline/ 编排层（import / cost / pnl）
data/rules/                   契约清单 + 冻结规则包（别名 / 参数 / 公司行为 / 行业 / 账户）
data/samples/                 合成交割单样例（多格式多编码，含刻意缺陷）
data/truth/                   真值台账（导入 + 成本；只存输入 + 期望）
data/README.md                依据查证、数据纪律、数值口径（唯一来源）
plan/                         计划文档与里程碑留档
tests/                        契约、自检、零上传门禁、确定性写盘、导入层、成本引擎与真值对齐
tools/                        gen_data.py（确定性生成器）
```

## 开发状态与计划

见 [`plan/00-README总览.md`](plan/00-README总览.md)（决策记录、承诺-证据台账、里程碑）
与 `plan/02`–`06`（架构、模块详设、数据计划、里程碑、交付对标）。
