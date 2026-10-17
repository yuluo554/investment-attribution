# RELEASE-M6 — investment-attribution v1.0.0 发布留档

> **发布纪律**（`plan/00` 决策记录）：本文件在 **tag 之前**落盘并进 tag 树。
> tag 之后才产生的事实（tag 指向的 commit、CI run id、附件 sha256、真下载复核）
> 统一记在 **§七「发布后分栏」**，以追加提交补齐，**不回头改写 §一–§六**——
> 发布前写的东西只写发布前能验的东西。

## 一、发布身份

| 项 | 值 |
|---|---|
| 仓库 | <https://github.com/yuluo554/investment-attribution>（**public**） |
| 署名 | `yuluo554` |
| tag | `v1.0.0`（annotated，tag 指向的 commit 见 §七） |
| 内容基线 | `8e9b9e6` — M6 发布准备：四面脱敏扫描 + 版本正式化 + CI 全历史检出 |
| 包版本 | `1.0.0`（`pyproject.toml` 与 `investment_attribution.__version__` 一致） |
| 日期 | 2026-10-17 |
| 许可 | MIT（`LICENSE` 文件与 `pyproject` 声明一致） |
| Python | 3.8 / 3.12 双通道（CI 四矩阵逐一验证） |
| 数据契约版本 | `0.6.0-m5` |

**为什么数据契约版本没有跟着升到 `0.7.0-m6`**：M6 是发布里程碑，**没有改动任何
数据契约面**（规则包结构、样例形态、真值形态、覆盖度登记都没变）。按 K11，改契约
版本必须整目录重生成 `data/` 并重新 `--check`——为一次无内容变化的发布整目录重写
29 个冻结产物，只会让 M1–M5 的冻结字节白掉一次。所以这里明确记一笔：**不升是决定，
不是漏做**。口径与契约仍以 `data/README.md §四` 为唯一定义处（K8）。

## 二、三件套

| # | 件 | 位置 / 值 |
|---|---|---|
| ① | git tag | `v1.0.0`（annotated；tagger 身份见 §三） |
| ② | GitHub Release **附产物** | 手工上传附件：`investment-attribution-v1.0.0.tar.gz`（源码包，`git archive` 自 tag 产出）+ `SHA256SUMS`（本地留副本；sha256 见 §七） |
| ③ | 发布留档 | 本文件（tag 前落盘，已进 tag 树） |

附件口径说明：**不用 GitHub 自动生成的 "Source code" 压缩包**——那两个包的字节
不承诺稳定（压缩实现与时间戳由 GitHub 控制），无法作为"下载件 sha256 与留档一致"
的判据。手工件由 `git archive` 从 tag 确定性产出，sha256 可复算。

## 三、发布前脱敏（四面）

工具：`tools/scan_privacy.py`（stdlib，无 `.git` 也能跑）。
判据五条：本机绝对路径 / 非 noreply 邮箱 / 凭据串 / 白名单外账号形态 / 8 位以上纯数字串。
适用范围按"该文件里长数字是否天然存在"划分：`.py` 源码不判长数字串（数字字面量与
正则片段天然密集），JSON 只走字符串叶子（整数分金额与生成器 seed 是 `int`），
判长数字前先摘 URL 与 32 位以上十六进制摘要、排除小数位。

```
$ python tools/scan_privacy.py --self-test
[caught] abs_path
[caught] email
[caught] credential
[caught] account
[caught] digit_run

$ python tools/scan_privacy.py --all
[clean] tree hits=0 excused=0
[clean] artifacts hits=0 excused=0
[clean] metadata hits=0 excused=0
[clean] history hits=0 excused=5
        EXCUSED history | account | tests/test_gen_data.py@d7f6b3ef1a55 | 合成阳性对照样本
        EXCUSED history | account | tests/test_gen_data.py@f686398f7302 | 合成阳性对照样本
        EXCUSED history | account | tests/test_gen_data.py@11da5286e241 | 合成阳性对照样本
        EXCUSED history | account | tests/test_gen_data.py@79b8172c9c95 | 合成阳性对照样本
        EXCUSED history | account | tests/test_gen_data.py@b24e099c0a26 | 合成阳性对照样本
total hits = 0 / excused = 5 / skipped = 0
exit 0
```

| 面 | 结果 | 说明 |
|---|---|---|
| 源码 | 0 命中 | 入仓文件（已跟踪 + 未忽略的新增）逐文件扫 |
| 产物 | 0 命中 | `data/` 下 JSON 走字符串叶子，样例按字节解码扫 |
| 提交元数据 | 0 命中 | 全部 7 个 commit 的 author / committer 邮箱均为 `282769740+yuluo554@users.noreply.github.com`；tag tagger 邮箱同源（§七复核） |
| 历史 | 0 命中 / 5 例外 | 遍历全部可达对象（含各历史版本），按路径后缀套同一套适用范围 |

**5 条例外是什么**：`tests/test_gen_data.py` 在 M1–M5 各版本里带着一个
**阳性对照样本**（`gen_data.scan_privacy` 的假门测试要求"真实账号形态必须被抓到"），
其字面量属于白名单外的账号形态，因此被 `account` 规则命中。它是合成样本、不是真实
账号；当前工作树里该样本已改为**拼接构造**（发布物不自己带这种字面量），但
**历史不可改写**，所以它在旧 blob 里仍然存在。工具把这 5 条单列为 `excused`
并逐条打印，`hits` 计数不含它们；名单被 `tests/test_privacy_scan.py` 冻结，
且另有测试证明"名单外的同形态串照样命中"——避免名单退化成万能静音键。

**假数据纪律的守门**（不是本工具，但同属这一节）：合成样例的白名单一直由
`test_gen_data.py::test_samples_use_whitelisted_codes_and_names` /
`test_samples_use_whitelisted_accounts` / `test_sample_privacy_scan_has_no_stray_long_digits`
与生成器 `--check` 双层守住。

## 四、可复现性证据（发布前实跑）

### 4.1 测试

| 通道 | 结果 |
|---|---|
| `py -3.8 -m pytest` | **429 passed** |
| `py -3.12 -m pytest` | **429 passed** |

含本次新增的 `tests/test_privacy_scan.py`（10 条：真仓库四面 0 命中、五条规则阳性
对照、名单冻结、"名单外同形态照样命中"、无 `.git` 时历史/元数据两面报 `skipped`
而不是假通过）。

### 4.2 数据守门（改真值 / 契约版本后必跑）

```
$ python tools/gen_data.py --check
[shape] ok
[bytes] ok (29 artifacts)
exit 0
```

### 4.3 自检（9 条检查，两条形态各跑一遍）

`manifest_load` / `manifest_file_present` / `coverage_declared` / `thresholds_positive` /
`no_network_imports` / `runtime_socket_probe` / `renderer_no_decisions` /
`report_zero_external_links` / `repo_root` → `ok: true`，`version: 1.0.0`，
`python -m investment_attribution selftest --strict` 与
`investment-attribution selftest --strict` 两种入口均 `exit 0`。

### 4.4 基准套件 B1–B5

```
基准套件完成：5 个套件，全部达标
  B1 导入层   17 项比对 / 0 条违例 / 达标
  B2 成本层   92 项比对 / 0 条违例 / 达标
  B3 公司行为 16 项比对 / 0 条违例 / 达标
  B4 收益归因 58 项比对 / 0 条违例 / 达标
  B5 合并层   18 项比对 / 0 条违例 / 达标
exit 0
```

合计 **201 项比对 / 0 违例**。

### 4.5 完整链路与报告口径

`run` 一条命令（4 份样例 + 估值序列 / `--method fifo` / 估值日 2025-12-31）：

- 退出码 **1**：样例刻意含一条规则表认不出的现金行（`其他业务甲`），
  `corporate-actions` 按契约返回"有待确认行"。这是**数据结论**，不是失败——把
  它改成 0 才是隐瞒问题。
- 13 份产物齐全：`ledger / holdings / pnl / actions / pending / fingerprints /
  account_map / import_errors / snapshots / attribution / merged /
  report.html / report_data.json`。
- 报告：13 个段落 / 8 个汇总数 / 41 行表格 / 15 条注记；外链 **0** 处；
  运行期探针 `blocked_attempts = 0`。
- 打码：生成阶段打码 4 处（`Y0****01`–`Y0****04`）；`report.html` 与
  `report_data.json` 均无 `[YA][0-9]{7,9}` 形态。
- 合并勾稽：合并前后总收益均为 `798438` 分（7984.38 元），`difference_fen = 0`
  （门槛 1 分）；4 个账户；合并持仓 3 个标的（`699001` 曾跨 2 账户、期末 1 账户）；
  划转台账 1 笔：`TR-A-B-0001` / 2025-10-10 / 20000.00 元 / 2 腿 / 净额 0 /
  两腿属不同账户 → 已抵消，`unbalanced_count = 0`。

### 4.6 干净 clone 复核

| 步骤 | 命令 | 结果 |
|---|---|---|
| 克隆（远端） | `git clone https://github.com/yuluo554/investment-attribution.git` | `8e9b9e6`，123 个入仓文件，`git status` 干净 |
| 克隆（本地 `--no-local`） | `git clone --no-local . <dir>` | `8e9b9e6` |
| 新 venv | `py -3.12 -m venv` | Python 3.12.10 |
| 安装 | `pip install -e ".[dev]"` | ok；`__version__ == 1.0.0` |
| 全量测试 | `python -m pytest` | 远端 clone **429 passed**；`--no-local` clone **429 passed** |
| README 逐条 | `selftest` / `selftest --strict` / console script / `gen_data.py --check` / `scan_privacy.py --all` / `bench` / `run` | 全部按 §4.1–§4.5 复现（exit 0 / 0 / 0 / 0 / 0 / 0，`run` 为 1） |

`tools/gen_data.py --check` 在无安装（仅 `PYTHONPATH`）与安装后两种形态下都可跑；
`tools/scan_privacy.py` 在同一棵树上四面复跑同为 0 命中。

## 五、CI

`.github/workflows/ci.yml`，四矩阵 `{windows,ubuntu} × {3.8,3.12}`，`fail-fast: false`。

发布前本机未验的部分正是**跨 OS 差异**（报告里的路径字符串形态、`grep -qE` 的
portable 行为、`set +e` 退出码断言），首推一轮即全绿，**没有出现需要修的差异**。

一处刻意的强化：`actions/checkout@v4` 改为 **`fetch-depth: 0`**。浅克隆只给 tip 树，
脱敏扫描的"历史面"会静默缩水成"只扫了最新一版"——那正是假绿。仓库只有 7 个 commit，
取全历史的代价可以忽略。

CI 步骤（24 步）覆盖：安装 → 全量测试 → **脱敏四面扫描** → 两种入口的自检 →
生成器双层守门 → 六个子命令的冒烟与"口径必填 = exit 2"负断言 →
报告零外链与打码 grep → `run` 全链路（断言 exit 1 且 13 份产物齐全）→ B1–B5 基准。

运行记录（run id 属 tag 之后的事实）见 §七。

## 六、M6 DoD 逐项

| DoD | 状态 | 在哪验的 |
|---|---|---|
| 三件套齐：tag / Release 附产物 / 发布留档 | 🟡 留档已落盘，tag 与 Release 见 §七 | §二、§七 |
| 干净环境 clone 按 README 逐条跑通 | ✅ | §4.6 |
| CI 四矩阵绿（push 与 tag 各一条 run 均绿） | 🟡 push run 已绿，tag run 见 §七 | §五、§七 |
| 下载件 sha256 与留档一致 | ⬜ | §七 |
| 脱敏扫描 0 命中（含 tag tagger 邮箱） | 🟡 四面 0 命中已验；tagger 邮箱见 §七 | §三、§七 |
| README 状态行转为正式版 | ✅ | `README.md`：`**状态：v1.0.0 正式发布（2026-10-17）。**` |
| `plan/00` 里程碑表 M6 行翻 ✅；`plan/06` §五 检查单逐项打勾 | 🟡 与 tag 相关项待 §七 | `plan/00`、`plan/06` |

## 七、发布后分栏（tag 之后追加）

> 本节由 tag 之后的追加提交补齐；tag 树里的这一节是**待补**状态，不是遗漏。

| 事实 | 值 |
|---|---|
| tag 指向的 commit | ⬜ 待补 |
| push run（`main`） | <https://github.com/yuluo554/investment-attribution/actions/runs/39035251944> — 四矩阵全绿（py3.8/ubuntu 30s、py3.8/windows 1m32s、py3.12/windows 43s、py3.12/ubuntu 26s） |
| tag run | ⬜ 待补 |
| tag tagger 邮箱 | ⬜ 待补 |
| 附件文件名 / 大小 | ⬜ 待补 |
| 附件 sha256（本地留档） | ⬜ 待补 |
| 真下载复核（下载后复算 sha256） | ⬜ 待补 |

（push run <https://github.com/yuluo554/investment-attribution/actions/runs/39035251944> 在 §一 的内容基线上触发，属发布前可验，故已在此节先行落值。）
