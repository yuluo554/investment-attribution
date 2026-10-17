# investment-attribution · 券商交割单收益归因工作台

把券商客户端导出的交割单还原成一本**可审计的投资账**：持仓成本、已实现/未实现收益、
分红送转还原、多账户合并、XIRR/TWR 归因，每个数字都可下钻到原始行。

> **不做投资建议，只做账。** 所有输出均为历史核算结果，不构成任何投资建议。
> 纯本地、零上传、零遥测、不登录券商、不联网行情。断网可跑完整链路。

**状态：v1.0.0 正式发布（2026-10-17）。** 里程碑 M0–M6 全部完成，可用版本见
[GitHub Releases](https://github.com/yuluo554/investment-attribution/releases)（附源码包与 SHA256）。
十个子命令全部落地：`import`（海通 xlsx / 华西 Tab+GB18030 两类真实格式 + 通用 CSV/TSV）、
`cost` / `pnl`（FIFO / 移动加权双口径、费用入账、资金与股份余额勾稽、
每日持仓快照）、`corporate-actions`（规则表匹配 + 人工确认队列 +
调整前后可逆复算）、`attribution`（XIRR / TWR 双估值点口径 /
已实现未实现拆分 / 费用拖累 / 标的·行业·账户·年度四维分解 / 可选本地基准）、
`merge`（跨券商合并视图 + 逐笔划转台账 + 两端剔除）、
`report`（单文件本地 HTML + 机读 `report_data.json`，同源渲染、
逐数字下钻、生成阶段打码、零外链）、`run`（一条命令跑完整链路）、
`bench`（内置 `B1`–`B5` 五套基准）、`selftest`（可证伪自检）。
配套：确定性合成数据生成器（`tools/gen_data.py --check` 双层守门）、
发布前脱敏四面扫描（`tools/scan_privacy.py`）、
**零上传静态门禁 + 运行期 socket 探针**与确定性写盘。
骨架期的"阶段未就绪"（退出码 `3`）已随 M5 全部退役。

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
| `corporate-actions --method fifo\|wavg` | 公司行为事件台账 + 人工确认队列（口径必填） | M3 ✅ |
| `attribution --method fifo\|wavg` | XIRR / TWR / 收益分解 / 费用拖累（口径必填） | M4 ✅ |
| `merge --method fifo\|wavg` | 多账户合并与账户间划转抵消（口径必填） | M5 ✅ |
| `report` | 单文件本地 HTML 报告 + `report_data.json` | M5 ✅ |
| `run --method fifo\|wavg` | 一条命令跑完整链路 | M5 ✅ |
| `bench [--only B1]` | 内置基准套件 B1–B5 | M5 ✅ |
| `selftest` | 可证伪自检 | M0 ✅ |


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

### `corporate-actions` 用法与退出码

```bash
# 口径同样必填：调整前后两组数要落到某个成本口径上
python -m investment_attribution corporate-actions --method fifo \
    --ledger out/ledger.json --out-dir out/ca/

# 用人工确认记录把"规则表认不出"的行入账（记录必须可追溯）
python -m investment_attribution corporate-actions --method fifo \
    --ledger out/ledger.json --out out/ca/actions.json --confirm my-confirmations.json
```

产物（全部经 `serialize.write_json_atomic` 落盘）：

| 文件 | 内容 |
|---|---|
| `actions.json` | 事件台账（`rule_id` + 命中模式 + 原始行证据 + before/after 调整）+ 待确认段 + 非行为段 + 规则包页眉 |
| `pending.json` | 待确认队列（原始行、行形态、现金方向、候选规则、原因码） |

`corporate-actions` 退出码：`0` 全部 `other` 行被唯一确定或明确判为非行为；
`1` 数据可用但有待确认行或调整问题；`2` 用法或输入不可用
（**未指定 `--method`**、`--ledger` 缺失、规则包损坏、确认记录非法）。

规则表能确定的自动入账并逐条挂依据；不能确定的一律进待确认队列，
**"跳过"不是默认路径**。每条事件都记 `before` / `after` 的份额、总成本与单位成本，
断言 `before + 声明增量 == after`；调整只作用于派生台账，
`ledger.json` 字节前后一致。

### `attribution` 用法与退出码

```bash
# 随包的估值序列样例覆盖"成本场景四件套"，所以导入同一组流水再归因
python -m investment_attribution import \
    data/samples/htsec-demo-001.xlsx data/samples/huaxi-demo-001.xls \
    data/samples/generic-demo-013-account-c.csv \
    data/samples/generic-demo-015-no-fee-columns.csv --out-dir out/attr-src/

# 估值序列是本地 CSV：date,symbol,name,quantity,price（可多日）
# quantity 会与台账持仓交叉核对（容差 1 股），不是给多少信多少
python -m investment_attribution attribution --method fifo \
    --ledger out/attr-src/ledger.json \
    --valuation data/samples/valuation-demo-001.csv \
    --out-dir out/attr/

# 只要 XIRR、指定 TWR 估值点口径、并给出可选本地基准
python -m investment_attribution attribution --method wavg \
    --ledger out/attr-src/ledger.json \
    --valuation data/samples/valuation-demo-001.csv \
    --twr-basis daily --benchmark my-bench.csv --xirr --twr \
    --out out/attr/attribution.json
```

> 换用自己的流水时必须换用自己的估值文件：随包估值样例是那四份样例的持仓，
> 拿它去核另一本台账会（正确地）落 `VALUATION_QUANTITY_MISMATCH`。

产物（全部经 `serialize.write_json_atomic` 落盘）：

| 文件 | 内容 |
|---|---|
| `attribution.json` | 页眉口径（成本口径 + TWR 估值点口径 + 基准是否提供 + 估值日）+ XIRR 段 + TWR 段 + 收益拆分 + 费用拖累 + 四维分解 + 问题段 |

`attribution` 退出码：`0` 归因完成且无问题；`1` 数据可用但检出问题
（**持有标的缺估值日价格** `VALUATION_MISSING`、拆分守恒不平、分解不平、
估值表数量与台账不符、规则表未识别的现金行等）；`2` 用法或输入不可用
（**未指定 `--method`**、`--ledger` 或 `--valuation` 缺失、估值表形态不符、
估值日早于流水末日、`--twr-basis` 非法）。

`--benchmark` 是**可选**的：本地缺基准文件时超额列不显示，命令仍 `exit 0`；
基准不做插值也不外推，两端日期缺失落 `BENCHMARK_DATE_MISSING`。
估值序列、现金流与 XIRR/TWR 口径见 [`data/README.md` §4.2.1 / §4.3.1](data/README.md)。

### `merge` 用法与退出码

```bash
# 合并**不重算成本**：输入是上一步的 attribution.json，本步只加一层增量
python -m investment_attribution merge --method fifo \
    --ledger out/attr-src/ledger.json \
    --attribution out/attr/attribution.json --out-dir out/merge/
```

产物（经 `serialize.write_json_atomic` 落盘）：

| 文件 | 内容 |
|---|---|
| `merged.json` | 合并视图（分账户明细 + 合计）+ **逐笔划转台账**（合同号 / 两腿 / 已抵消证据）+ 合并持仓（同名标的跨账户汇总）+ 对账检查 + 问题段 |

`merge` 退出码：`0` 划转两端全部抵消且合并前后总收益不变；
`1` 合并完成但检出问题（单腿划转、划转净额不为零、合并前后总收益不等、
上游归因产物自带问题）；`2` 用法或输入不可用
（**未指定 `--method`**、`--ledger` 或 `--attribution` 缺失、
`--method` 与归因产物口径不一致）。

划转**不写第二份业务名表**：模式取自冻结规则包
`rules/corporate_actions.json` 的 `transfer_patterns`。两腿按**合同号**归组，
净额为零且分属不同账户才算"已抵消"；只导入一端时只剩一腿，落
`TRANSFER_LEG_UNPAIRED` 并 `exit 1`——**不假装抵消成功**。
口径见 [`data/README.md` §4.6](data/README.md)。

### `report` 用法与退出码

```bash
# 报告只有一份输入：上一步的 merged.json（口径来自产物，不在 CLI 再复述一遍）
python -m investment_attribution report --merged out/merge/merged.json --out-dir out/report/
```

产物（同一次渲染产出，**同源**）：

| 文件 | 内容 |
|---|---|
| `report.html` | 单文件报告：内嵌样式、无外链、无脚本，断网双击即可看，可直接打印为 PDF |
| `report_data.json` | 机读版报告数据：同样的段落 / 汇总数 / 表格 / 锚点 |

`report` 退出码：`0` 报告生成完成且上游产物无问题；`1` 报告生成完成但上游产物
带 `invalid`（问题如实呈现在报告里，退出码同时告知脚本调用方）；
`2` 用法或输入不可用（**未指定 `--merged`**、文件缺失、形态不符）。

四条硬约束（都有测试）：

1. **零外链**：出口前对 HTML 做静态扫描，任何 `http://` / `https://` / `//`
   外部引用都会让命令报错且**不落半份产物**；报告里没有 CSS / JS / 字体 / 图片外链。
2. **零网络**：构建与渲染全程处于运行期武装探针下，拦截次数写进两份产物。
3. **生成阶段打码**：股东账号与资金账号在**生成报告数据时**就打码（保留首尾
   各 2 位），HTML 与 `report_data.json` 都不携带完整账号。
4. **逐数字下钻**：每个汇总数与每行表格都挂
   `source_file` / `source_sha256` / `source_line_no` 三件套，锚点只来自上游
   产物的 provenance 索引，**不造锚点**。

渲染层**零业务判定**（成本口径 / 事件类型 / 收益口径一个词都不出现），
由 `report/guard.py` 的 AST 门禁把守（三条 + 正/负对照），
`selftest --strict` 里也有这两条检查。

### `run` 用法与退出码

```bash
# 一条命令：import → cost → pnl --fees → corporate-actions → attribution → merge → report
python -m investment_attribution run \
    data/samples/htsec-demo-001.xlsx data/samples/huaxi-demo-001.xls \
    data/samples/generic-demo-013-account-c.csv \
    data/samples/generic-demo-015-no-fee-columns.csv \
    --method fifo --valuation data/samples/valuation-demo-001.csv --out-dir out/chain/
```

`run` **只是编排**：产物与逐命令调用**逐字节一致**（测试按同一目录、同一
路径字符串断言）。口径参数与 `cost` / `attribution` 统一叫 `--method`
（必填，无默认值），`--valuation` 必填，`--out-dir` 必填。

`run` 退出码取各步退出码的最大值（`0` / `1`）：任何一步降级都如实反映在总码上
（样例含一条无法识别的现金行，故 `corporate-actions` 与 `run` 均为 `1`——
这是**数据结论**，不是失败）。任一步用法或输入不可用（`2`）直接中断。

### `bench` 用法与退出码

```bash
python -m investment_attribution bench              # B1–B5 全跑
python -m investment_attribution bench --only B5    # 只跑合并层
```

`bench` 在临时目录里按 CI 的同一套流程自跑随包样例，跑完即清理。
它**只做编排与失败码汇总**，判据是各模块下已经就位的那一份 `verify.py`：

| 套件 | 判据模块 | 比什么 |
|---|---|---|
| `B1` | `ingest/verify.py` | 逐行列值 / 行级错误 / 文件指纹 vs 导入真值 |
| `B2` | `ledger/verify.py` | 双口径逐笔持仓与已实现 vs 成本真值 |
| `B3` | `actions/verify.py` | 逐事件类型 / 调整前后 / 待确认队列 vs 真值 |
| `B4` | `attribution/verify.py` | XIRR / TWR / 拆分 / 四维分解 vs 真值 |
| `B5` | `merge/verify.py` | 合并前后总收益 / 划转台账 / 合并持仓 vs 真值 |

比对函数**只读两边产物**（不重算导入、不重算成本、不重算归因），所以
"注入缺陷必须报未达标"是可证伪的——每个套件在测试里都有注入缺陷的探活用例。

`bench` 退出码：`0` 全部请求套件达标；`1` 有套件未达标；`2` 用法错误
（未识别的套件名，如 `--only B9`）。

## 退出码语义

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 降级完成 / 已检出问题（各子命令 README 段落会写明具体语义） |
| `2` | 用法错误或输入不可用（参数错、文件缺失、数据目录不存在） |

骨架期的退出码 `3`（阶段未就绪）**已在 M5 全部退役**：十条子命令全部落地后，
CLI 不再有"未就绪"这条出口，`stages.py` 登记表与未就绪异常类一并删除——
不留"看起来还会拦截"的幽灵代码。


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

- **零上传**：不发起任何网络请求，覆盖**静态 + 运行期两层**。
  静态层由 `tests/test_netguard.py` 对全部包源码做 AST import 审计；
  运行期层由 `netguard.armed_runtime_probe()` 装 `sys.addaudithook`，
  凡命中 `socket.` / `ssl.` / `urllib.` / `ftplib.` / `smtplib` / `telnetlib` /
  `http.client.` 前缀的审计事件一律**拦截并抛错**，拦截次数写进产物
  `zero_upload.runtime_probe`。阳性/阴性对照齐备（真 socket 被拦且留痕；
  解除武装时不拦、不留痕），`selftest --strict` 含该探针检查。
- **报告阶段打码**：股东账号、资金账号在生成阶段即打码，HTML / JSON 从不携带完整账号。
- **真实交割单永不入仓**：样例一律程序化合成，使用假代码段 `699000–699099`
  与 `示例` 名称前缀、假账号白名单；真实件放 `_private/`（已忽略）。
- **发布前脱敏四面扫描**：`tools/scan_privacy.py` 按「源码 / 产物 / 提交元数据 /
  历史」四面扫本机绝对路径、非 noreply 邮箱、凭据串、白名单外账号形态与
  8 位以上纯数字串。`--self-test` 对每条规则喂注入样本（不可证伪时门禁只是话术）；
  无 `.git` 的源码包解包态下，历史与元数据两面显式报 `skipped` 而非假通过。
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
| TWR 相对误差（与 XIRR 共用门槛） | < 0.01% |
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
src/investment_attribution/ingest/   导入适配层（编码 / 壳清洗 / 列名归一 / 行级台账 / B1 比对）
src/investment_attribution/ledger/   成本引擎（FIFO / 移动加权 / 勾稽 / 快照 / B2 比对）
src/investment_attribution/actions/  公司行为（规则表匹配 / 待确认队列 / 调整可逆 / B3 比对）
src/investment_attribution/attribution/ 收益归因（估值表 / XIRR / TWR / 基准 / 行业 / 四维分解 / B4 比对）
src/investment_attribution/merge/    多账户合并（合并视图 / 逐笔划转台账 / 两端剔除 / B5 比对）
src/investment_attribution/report/   本地报告（数据模型 / 零判定渲染层 / 零判定门禁与零外链扫描 / 编排）
src/investment_attribution/pipeline/ 编排层（import / cost / pnl / corporate-actions / attribution / merge / report / run / bench）
data/rules/                   契约清单 + 冻结规则包（别名 / 参数 / 公司行为 / 划转模式 / 行业 / 账户）
data/samples/                 合成交割单样例（多格式多编码，含刻意缺陷）+ 多日估值序列
data/truth/                   真值台账（导入 + 成本 + 公司行为 + 归因 + 合并；只存输入 + 期望）
data/README.md                依据查证、数据纪律、数值口径（唯一来源）
plan/                         计划文档与里程碑留档
tests/                        契约、自检、零上传门禁（静态 + 运行期）、确定性写盘、
                              导入层 / 成本引擎 / 公司行为 / 归因 / 合并真值对齐、
                              报告同源与零判定门禁、B1–B5 基准总入口、run 逐字节一致、
                              发布前脱敏四面扫描的正负对照
tools/                        gen_data.py（确定性生成器）、scan_privacy.py（发布前脱敏扫描）
```

## 开发状态与计划

见 [`plan/00-README总览.md`](plan/00-README总览.md)（决策记录、承诺-证据台账、里程碑）
与 `plan/02`–`06`（架构、模块详设、数据计划、里程碑、交付对标）。
本次发布的完整记录见 [`plan/RELEASE-M6.md`](plan/RELEASE-M6.md)
（发布身份、三件套、脱敏四面扫描、可复现证据、CI 四矩阵）。
