# investment-attribution · 券商交割单收益归因工作台

把券商客户端导出的交割单还原成一本**可审计的投资账**：持仓成本、已实现/未实现收益、
分红送转还原、多账户合并、XIRR/TWR 归因，每个数字都可下钻到原始行。

> **不做投资建议，只做账。** 所有输出均为历史核算结果，不构成任何投资建议。
> 纯本地、零上传、零遥测、不登录券商、不联网行情。断网可跑完整链路。

**状态：M0 骨架（2026-10-17）。** 当前仓库提供可运行的 CLI 契约、
可证伪自检、零上传静态门禁与确定性写盘；七个业务子命令按里程碑逐个落地，
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
| `import` | 多券商交割单导入 → 统一流水 + 行级错误台账 | M1 |
| `cost --method fifo\|wavg` | 持仓成本台账（口径必填，无默认值） | M2 |
| `pnl [--fees]` | 已实现/未实现收益与费用三件套 | M2 |
| `corporate-actions` | 公司行为事件台账 + 人工确认队列 | M3 |
| `attribution [--xirr] [--twr]` | XIRR / TWR / 收益分解 | M4 |
| `merge` | 多账户合并与账户间划转抵消 | M5 |
| `report` | 单文件本地 HTML 报告 + `report_data.json` | M5 |
| `run` | 一条命令跑完整链路 | M5 |
| `bench [--only B1]` | 内置基准套件 B1–B5 | M5 |
| `selftest` | 可证伪自检（**当前可用**） | M0 ✅ |

## 退出码语义

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 降级完成 / 已检出问题（各子命令 README 段落会写明具体语义） |
| `2` | 用法错误或输入不可用（参数错、文件缺失、数据目录不存在） |
| `3` | **阶段未就绪**——该模块尚未落地，本次不产出任何结果 |

`3` 是骨架期的临时语义，随模块落地逐个退役；退役时同步常量表、CLI 分派、
本表、决策记录与测试。

## 数据目录

- 缺省：源码树内的 `data/`（安装为 editable 时同样有效）
- 也可用 `--data-dir DIR` 或环境变量 `IA_DATA_DIR` 显式指定
- 不做 CWD 上溯——避免"在哪个目录敲命令"改变结果

契约清单位于 `data/rules/manifest.json`，承载基准门槛与覆盖度登记。
M1 起，合成样例、列名别名表、公司行为规则表、参数包与真值台账统一由
`tools/` 下的生成器产出，配 `--check` 位级守门，**禁止手改**。

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
登记 `total / sampled` 且要求分子 = 分母（测试锁定）。

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
src/investment_attribution/   CLI 与核心模块
data/rules/manifest.json      契约清单（门槛 + 覆盖度登记）
data/README.md                依据查证、数据纪律、数值口径（唯一来源）
plan/                         计划文档与里程碑留档
tests/                        契约、自检、零上传门禁、确定性写盘
tools/                        生成器与发布工具（M1 起）
```

## 开发状态与计划

见 [`plan/00-README总览.md`](plan/00-README总览.md)（决策记录、承诺-证据台账、里程碑）
与 `plan/02`–`06`（架构、模块详设、数据计划、里程碑、交付对标）。
