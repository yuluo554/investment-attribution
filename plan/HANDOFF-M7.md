# HANDOFF-M7 — 从 M6 已发布到后续扩展

> **M6 已完成并发布**（2026-10-17，tag `v1.0.0`）。本文件不是"下一棒必做"的任务书——
> `plan/05` 的里程碑表到 M6 为止，**没有 M7 业务里程碑**。它是一份**收尾交接**：
> 读完即可知道发布态是什么、还能做什么、动手前必须先读什么。
> 库内一律相对路径；启动命令由用户侧拼绝对路径。

## 一、发布态（已完成，勿重做）

| 项 | 值 | 证据 |
|---|---|---|
| 公开仓库 | <https://github.com/yuluo554/investment-attribution>（public） | `gh repo view` |
| Release | <https://github.com/yuluo554/investment-attribution/releases/tag/v1.0.0> | 两个附件均 `uploaded` |
| tag | `v1.0.0`（annotated）→ commit `7da7c269aba3289726fdd982c1bf76c106659dd7` | `git rev-parse v1.0.0^{commit}` |
| 附件 | `investment-attribution-v1.0.0.tar.gz`（336748 字节，sha256 `684a4913d96fa3ddab81ff25f7f167ef9c177d8f9a8297366ab52aa52f3c3498`）+ `SHA256SUMS` | 下载后 `sha256sum -c` 为 OK，与本地留档件逐字节相同 |
| 发布留档 | `plan/RELEASE-M6.md`（tag 前落盘；§七 为 tag 后分栏） | 本文件同目录 |
| CI | 3 次推送 → 3 条 run 四矩阵全绿（`{windows,ubuntu} × {3.8,3.12}`） | <https://github.com/yuluo554/investment-attribution/actions>（run id 逐条见 `RELEASE-M6.md §七`） |
| 测试 / 基准 | 双解释器各 429 passed；B1–B5 共 201 项比对 0 违例 | `plan/RELEASE-M6.md §四` |
| 版本 | 包 `1.0.0`（`pyproject` 与 `__version__` 一致） | — |
| 数据契约版本 | `0.6.0-m5`（**M6 刻意未升档**，理由见下） | `data/rules/manifest.json` |

## 二、本机环境坑（M6 实测新增，前六条见 `HANDOFF-M6.md §六`）

- **首次 push 会被 OAuth App 的 `workflow` scope 拦下**：token scope 只有
  `gist / read:org / repo`，推送含 `.github/workflows/` 的提交时 GitHub 拒绝
  （`refusing to allow an OAuth App to create or update workflow`）。
  **解法：远端改用 SSH**（`git@github.com:yuluo554/investment-attribution.git`，
  本机 `id_ed25519` 已注册且 `ssh -T git@github.com` 认到 `yuluo554`）。
  不要去刷新 token scope——那是账号级动作，不需要。
- **`curl` 抓 GitHub Release 附件会被重置连接**（`Recv failure: Connection was reset`）。
  取发布附件用 `gh release download v1.0.0 --dir <dir> --clobber`。
- **`gh repo create --source=. --remote=origin --push` 会在 push 失败后留下已建好的空仓库**，
  别以为创建也失败了；用 `gh repo view <nwo> --json isEmpty` 复核。
- **GitHub Release 自动生成的 "Source code" 包字节不稳定**，不能作为校验对象；
  手工附件用 `git archive --format=tar --prefix=<name>/ <tag> | gzip -9n`，
  连建两次 sha256 相同（`git archive` 用 commit 时间戳、`gzip -n` 不写文件名与时间）。
- **`.clean-store/` 与 `.clean-venv/` 已在 `.gitignore` 里预留**：干净 clone 复核按这两个名字
  放在仓库内，不污染 `git status`，也不会被 `git archive` 打进发布包。
- **`actions/checkout` 在 tag 事件上会把标注 tag 退化成轻量 tag**：它执行的是
  `git fetch --no-tags origin +<commit>:refs/tags/<tag>`，把 commit 直接塞进 tag ref，
  **标注 tag 对象压根没进库**。后果：tag run 里 `git for-each-ref refs/tags` 拿到空 tagger、
  脱敏扫描的历史面看不到 tag 对象——日志上的 `hits=0` 是"没扫到"，不是"扫了没问题"。
  CI 已加 `Restore annotated tag objects` 步骤：按 `+refs/tags/*:refs/tags/*` 强制重取，
  并断言 `git cat-file -t refs/tags/$GITHUB_REF_NAME` == `tag`（退化成轻量 tag 就直接红）。
  **要验 tag tagger 邮箱，必须在本地有标注 tag 对象的库里跑。**
- **判据要按对象类型分流，而不是按"懒得区分"**：标注 tag 对象的正文是元数据
  （`tagger` 行自带 10 位 Unix 时间戳），对它开 `digit_run` 必误报；但**不能因此跳过它**——
  跳过的代价是 tag 消息里塞个真账号也没人管。正确做法：邮箱 / 账号 / 凭据 / 路径规则照判，
  只把长数字串关掉。`tests/test_privacy_scan.py::test_history_scan_covers_the_annotated_tag_object`
  锁住"tag 对象必须真的被扫到"。
- **发布顺序不能反**：`plan/RELEASE-M6.md` 必须**先落盘再打 tag**（它要进 tag 树），
  而 §七 的发布后事实只能在 tag 之后写——所以正常节奏是"写留档 → commit → push →
  等 CI 绿 → 打 tag → push tag → 建 Release → 附产物 → 下载复核 → 追加 §七 → commit"。
  发布前就想把 §七 填满，只能靠编。

## 三、发布口径里的三个"别改"

1. **数据契约版本仍是 `0.6.0-m5`**——M6 没有改动任何数据契约面，刻意不升档。
   若将来真的动了规则包结构 / 样例形态 / 真值形态，才按 K11 升档 + 整目录重生成 + `--check`。
2. **脱敏扫描的例外名单是冻结的**（`tools/scan_privacy.py::DECLARED_NEGATIVE_CONTROLS`，
   两条：`A`+9 个 9、8 个 8）。那是 M1 起测试里的**合成阳性对照样本**字面量，
   只存在于不可改写的历史里。名单被 `tests/test_privacy_scan.py` 冻结，且另有测试证明
   "名单外的同形态串照样命中"——**别为了让某次扫描变绿往里加条目**，
   要加就得同时改冻结断言，那正是"有人被逼着解释"的机制。
3. **CI 的 `fetch-depth: 0` 不能去掉**：浅克隆只给 tip 树，脱敏扫描的历史面会静默缩水成
   "只扫了最新一版"。仓库很小，全历史几乎不花时间。

## 四、扩展项 Backlog（要做的话从这挑）

| 扩展项 | 起点 | 主要难点 | 会碰哪些既定口径 |
|---|---|---|---|
| 基金对账单 PDF / 截图 OCR | 新模块 `src/investment_attribution/ingest/pdf/` | 模板无穷，必须给出"覆盖度不承诺 100%"的诚实声明 | K5 三件套（审计行必须有来源文件 + sha256 + 行号）；PDF 没有"行号"概念，需要先在 `data/README.md` 定义等价锚点 |
| 更多券商适配 | `data/rules/aliases.json` + `ingest/rules.py` | 每家列名各异 | K6 编码探测顺序、§3.2/§3.3 假代码与假账号白名单（新增样例必须先登记白名单） |
| 报告图表 | `report/render.py`（**零判定硬门禁**） | 零外链 + 渲染层不许出现业务词 | **判定必须放 `report/model.py`**，`render.py` 里加任何业务词都会红 |
| 非 editable wheel | `pyproject.toml` 打包 `data/` | 数据目录语义 | K4 数据目录解析（不做 CWD 上溯）；改了要重跑干净 clone 复核 |
| 更多成本口径（如 LIFO） | `ledger/engine.py` + `data/README.md §四` | 真值要独立复算 | **K8：数值口径唯一定义处是 `data/README.md §四`**；新增口径必须同时扩真值台账与覆盖度 `coverage.surfaces` |

**动手前的硬约束（顺序别反）**：

1. 先读 `data/README.md §三/§四`（数据纪律 + 数值口径唯一定义处）；
2. 涉及合成数据的，先改 `tools/gen_data.py` 再整目录重生成，最后 `--check` 必须形态 + 逐字节全绿；
3. 新增覆盖形态要同步 `coverage.surfaces`（`total == sampled`，测试锁定）；
4. 新增子命令只需保证"分派完备性"（`SUBCOMMANDS ⊆ HANDLERS` 且无多余分派）——
   退出码 `3` 已整体退役，**别再写"阶段未就绪"的活代码**；
5. 收尾一律：双解释器全量 pytest + `gen_data.py --check` + `scan_privacy.py --all` 三样全绿再 commit。

## 五、关键命令速查

```bash
# 三样基线（改任何东西之后都要跑）
PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 py -3.8 -m pytest 2>&1 | tr '\r' '\n' | tail -3
PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 py -3.12 -m pytest 2>&1 | tr '\r' '\n' | tail -3
PYTHONUTF8=1 py -3.12 tools/gen_data.py --check
PYTHONUTF8=1 py -3.12 tools/scan_privacy.py --self-test
PYTHONUTF8=1 py -3.12 tools/scan_privacy.py --all

# 完整链路
PYTHONPATH=src PYTHONUTF8=1 py -3.12 -m investment_attribution run \
    data/samples/htsec-demo-001.xlsx data/samples/huaxi-demo-001.xls \
    data/samples/generic-demo-013-account-c.csv \
    data/samples/generic-demo-015-no-fee-columns.csv \
    --method fifo --valuation data/samples/valuation-demo-001.csv --out-dir out/chain/; echo $?

# 发布物复核（重算 tag 的源码包 sha256）
git archive --format=tar --prefix=investment-attribution-1.0.0/ v1.0.0 | gzip -9n \
    | sha256sum   # 期望 684a4913d96fa3ddab81ff25f7f167ef9c177d8f9a8297366ab52aa52f3c3498

# CI 状态
gh run list --limit 5
gh run view <run-id>
```

## 六、状态清单

- [x] 三件套齐（tag / Release 附产物 / `plan/RELEASE-M6.md`）
- [x] 干净 clone 双路按 README 逐条跑通
- [x] CI 三次推送三条 run 全绿（含 tag run）
- [x] 下载件 sha256 与留档一致（且与本地留档件逐字节相同）
- [x] 脱敏四面扫描 0 命中（含 tag tagger 邮箱）
- [x] README 状态行转为正式版（`v1.0.0 正式发布`）
- [x] `plan/00` 里程碑表 M6 行 ✅；`plan/06 §五` 检查单逐项打勾
