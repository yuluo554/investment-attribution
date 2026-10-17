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
| 三件套齐：tag / Release 附产物 / 发布留档 | ✅ | tag `v1.0.0` + Release 两个附件（§七）+ 本文件在 tag 前落盘并进 tag 树 |
| 干净环境 clone 按 README 逐条跑通 | ✅ | §4.6 |
| CI 四矩阵绿（push 与 tag 各一条 run 均绿） | ✅ | §五、§七：3 次推送 → 3 条 run 全绿，tag run 在列 |
| 下载件 sha256 与留档一致 | ✅ | §七：下载件与本地留档件逐字节相同，sha256 一致 |
| 脱敏扫描 0 命中（含 tag tagger 邮箱） | ✅ | §三（四面 0 命中）+ §七（tagger 邮箱同为 noreply） |
| README 状态行转为正式版 | ✅ | `README.md`：`**状态：v1.0.0 正式发布（2026-10-17）。**` |
| `plan/00` 里程碑表 M6 行翻 ✅；`plan/06` §五 检查单逐项打勾 | ✅ | `plan/00`、`plan/05`、`plan/06` 与本文件同批回写 |

## 七、发布后分栏（tag 之后追加）

> 本节由 tag 之后的追加提交补齐。tag 树里的这一节留下的是**待补**状态，不是遗漏——
> 发布纪律要求留档在 tag 前落盘，而 tag 之后才产生的事实只能在 tag 之后写。
> 本节内容**不改写 §一–§六**。

### 7.1 事实

| 事实 | 值 |
|---|---|
| tag 指向的 commit | `7da7c269aba3289726fdd982c1bf76c106659dd7`（`M6 发布留档：RELEASE-M6.md 落盘（tag 前）+ plan 回写`） |
| tag tagger 邮箱 | `yuluo554 <282769740+yuluo554@users.noreply.github.com>`——与全部 commit 的 author / committer 同源，非个人邮箱 |
| 推送事件 | **3 次推送 → 3 条 run**：`8e9b9e6`(main) / `7da7c26`(main) / `v1.0.0`(tag)，事件数 = run 数 |
| run · `8e9b9e6` | <https://github.com/yuluo554/investment-attribution/actions/runs/39035251944> — 四矩阵全绿（py3.8/ubuntu 30s、py3.8/windows 1m32s、py3.12/windows 43s、py3.12/ubuntu 26s） |
| run · `7da7c26` | <https://github.com/yuluo554/investment-attribution/actions/runs/39036102893> — 四矩阵全绿（1m25s） |
| run · tag `v1.0.0` | <https://github.com/yuluo554/investment-attribution/actions/runs/39036251965> — 四矩阵全绿（py3.8/ubuntu 39s、py3.8/windows 1m15s、py3.12/windows 52s、py3.12/ubuntu 25s） |
| Release | <https://github.com/yuluo554/investment-attribution/releases/tag/v1.0.0>（`isDraft=false`、`isPrerelease=false`） |
| 附件 | `investment-attribution-v1.0.0.tar.gz`（336748 字节）+ `SHA256SUMS`（103 字节），均 `state = uploaded` |
| 附件 sha256 | `684a4913d96fa3ddab81ff25f7f167ef9c177d8f9a8297366ab52aa52f3c3498` |
| 重复构建可复现 | 同一 tag 连建两次 sha256 完全相同（`git archive --format=tar --prefix=investment-attribution-1.0.0/ v1.0.0 \| gzip -9n`）——`git archive` 取 commit 时间戳、`gzip -n` 不写文件名与时间，故字节稳定 |
| 真下载复核 | `gh release download v1.0.0` 取回两个附件：`sha256sum -c SHA256SUMS` → `OK`；下载件与本地留档件 `cmp` → **逐字节相同**；两次复算均为 `684a4913…c3498`，与上面记录一致 |
| 本地留档副本 | 仓库内 `release/`（已 gitignore，不入仓）：`investment-attribution-v1.0.0.tar.gz` + `SHA256SUMS` + `download-check/`（下载复核用） |

**tag 树与 main 的差异**：tag `v1.0.0` 指向 `7da7c26`，其 `RELEASE-M6.md` 的 §七 是待补状态；
本节内容随 tag 之后的追加提交进入 `main`。这是发布纪律要求的形态，不是"两头不一致的漏改"。

### 7.2 发布后查出的两个问题（都记清，不用"后来修好了"糊过去）

**问题 A：CI 的 tag run 其实没看到标注 tag 对象，"tagger 邮箱"这一项没被 CI 覆盖。**

`actions/checkout` 对 tag 事件执行的是
`git fetch --no-tags --prune origin +<commit>:refs/tags/<tag>`——把 **commit** 直接塞进
tag ref，于是本地只有一个**轻量 tag**，标注 tag 对象根本没进库。后果是 tag run 里：

- 历史面看不到 tag 对象（它压根不在可达对象里）；
- 元数据面的 `git for-each-ref refs/tags` 拿到的是空 tagger。

所以 tag run 日志里的 `history hits=0 / metadata hits=0` **不是"历史干净"的证明**，
至少对 tag 对象那一部分不是——这正是"扫了但没扫到"的假绿形态。

- **tag tagger 邮箱是怎么验的**：本地（有标注 tag 对象）实跑
  `git for-each-ref --format='%(taggername)|%(taggeremail)' refs/tags` →
  `yuluo554|<282769740+yuluo554@users.noreply.github.com>`，非个人邮箱。
- **缺口怎么补的**：CI 增加一步 `Restore annotated tag objects`，按
  `git fetch --force origin '+refs/tags/*:refs/tags/*'` 强制重取，并**要求
  `git cat-file -t refs/tags/$GITHUB_REF_NAME` 必须等于 `tag`**——它一旦再退化成轻量 tag，
  CI 会直接红，而不是安静地少扫一截。本机已复现该退化（checkout 式 fetch 后
  `cat-file -t` 返回 `commit`，refspec 重取后返回 `tag`）。

**问题 B：`v1.0.0` 里那版脱敏扫描器，在**带标注 tag 的 clone** 上会报 1 条误命中。**

标注 tag 对象的正文里有一行 `tagger <name> <email> <unix 秒> <tz>`，那串 10 位 Unix
时间戳会被 `digit_run` 规则命中。`v1.0.0`（`7da7c26`）那版把非 blob 对象也按文件内容判，
于是：

```
$ git clone <repo> && cd investment-attribution && python tools/scan_privacy.py --all
[clean] tree hits=0 excused=0
[clean] artifacts hits=0 excused=0
[clean] metadata hits=0 excused=0
[dirty] history hits=1 excused=5
        HIT  digit_run | v1.0.0@4bef938f5dc7 | <tagger 行里的 10 位 Unix 秒>
total hits = 1 / excused = 5 / skipped = 0        # exit 1
```

（上面那段是**复现输出**，不是随手编的示例；那串被命中的 10 位数字是 `tagger` 行的时间戳，
此处按占位写，免得本文档自己又给扫描器送一条 `digit_run`。）

**影响范围（不含糊）**：只影响"拿 git clone 了带这个标注 tag 的仓库"的人，且只影响
`--all` 的 `history` 面——**发布包（tarball）里没有 `.git`，历史与元数据两面本来就报
`skipped`，所以下载附件的人不受影响**；tree / artifacts / metadata 三面也不受影响。
但**发出去的隐私门禁自己误报，比不报更糟**（门禁一旦噪声大于信号就会被关掉），
所以这条按缺陷记，不按"设计如此"记。

- **怎么修的（落在 main，不在 tag 里）**：`scan_history` 按对象类型分流——blob 按
  **路径后缀**定适用范围，非 blob 对象（标注 tag 等）按**元数据**处理：邮箱 / 账号 /
  凭据 / 绝对路径规则照判，**只有长数字串不判**（时间戳天然是 10 位）。为此
  `_read_blobs` 改成连对象类型一起取回。同时补了一条测试
  `test_history_scan_covers_the_annotated_tag_object`：**tag 对象必须真的被扫到**
  （而不是为了躲开时间戳干脆跳过）——跳过的代价是 tag 消息里塞个真账号也没人管。
- **为什么没有重打 tag**：`v1.0.0` 已经发布且三件套证据（三条 run、附件 sha256、
  真下载复核）都已落档，重打会把已记录的 tag→commit 事实作废、并让"发布前落盘的留档"
  与 tag 树再度错位；而这个缺陷既不改变 `data/`、也不改变任何口径与产物，
  仅在一种明确的用法下多报一条。**选择记录偏差 + 在 main 修 + 让 CI 能抓住复发**，
  而不是把已发布的 tag 悄悄挪走。若将来确有需要，正确做法是发 `v1.0.1`，
  而不是覆盖 `v1.0.0`。

### 7.3 修复后的复跑（main，2026-10-17）

```
$ python tools/scan_privacy.py --self-test      # 五条规则阳性对照全 caught
$ python tools/scan_privacy.py --all
[clean] tree hits=0 excused=0
[clean] artifacts hits=0 excused=0
[clean] metadata hits=0 excused=0
[clean] history hits=0 excused=5
total hits = 0 / excused = 5 / skipped = 0      # exit 0
```

`py -3.8` / `py -3.12` 全量 pytest 各 429 passed；`tools/gen_data.py --check` 形态 +
29 产物逐字节不变。修的是**判据与覆盖面**，没碰任何数据、口径或产物。

### 7.4 发布物本体复核（发布后追加，2026-10-17）

在 tag 之后的 `main`（`8d46d79`）上另起一棵**全新 clone + 全新 venv**，独立复跑一遍，
并核对发布物本体：

| 复核项 | 命令 | 结果 |
|---|---|---|
| 远端状态 | `git log --oneline -1` / `git ls-files \| wc -l` / `git status --porcelain` | `8d46d79`；125 个入仓文件；**0 处未提交改动**（历史 = 工作树） |
| tag 类型与 tagger | `git cat-file -t refs/tags/v1.0.0` / `git for-each-ref` | `tag`（**标注 tag**，普通 clone 会连带取到）；tagger = `yuluo554 <282769740+yuluo554@users.noreply.github.com>` |
| 提交数 | `git rev-list --count HEAD` | 10（M0–M6 共 10 个 commit） |
| 全量测试 | 新 venv + `pip install -e ".[dev]"` + `pytest` | **431 passed** |
| 数据守门 | `tools/gen_data.py --check` | `[shape] ok` + `[bytes] ok (29 artifacts)` |
| 脱敏四面（**含标注 tag 对象**） | `tools/scan_privacy.py --all` | `total hits = 0 / excused = 5 / skipped = 0`，exit 0 —— 7.2 的缺陷 B 在 tag 对象真实存在的库里已不复现 |
| 自检 | `selftest --strict` | exit 0 |
| 基准 | `bench` | B1–B5 全达标，exit 0 |
| **发布物本体 = tag 树** | 附件解包目录 vs `git archive v1.0.0` 解包目录 `diff -r` | **逐文件逐字节相同**（附件顶层目录 `investment-attribution-1.0.0/`） |

至此 M6 三件套（tag / Release 附产物 / 发布留档）与 DoD 六项全部闭合，且**每一项都指得出在哪验的**。
