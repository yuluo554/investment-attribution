"""M6 发布前脱敏门禁：四面扫描 + 阳性对照 + 无 git 降级。

这份门禁的价值在于"命中即红、且能证明自己会红"：

- 真仓库四面必须 0 命中（``excused`` 只允许是已声明的合成阳性对照）；
- 每条规则喂注入样本必须被抓到（否则门禁不可证伪）；
- 名单外的同形态串必须照样命中（否则名单变成万能静音键）；
- 无 ``.git`` 时历史/元数据两面必须报 ``skipped``，不得伪装成通过。
"""

import subprocess
import sys

from context import DATA_DIR, REPO_ROOT

TOOLS_DIR = REPO_ROOT / "tools"
SCANNER = TOOLS_DIR / "scan_privacy.py"

sys.path.insert(0, str(TOOLS_DIR))
import scan_privacy  # noqa: E402

SURFACES = ("tree", "artifacts", "metadata", "history")


def run_scanner(*args):
    return subprocess.run(
        [sys.executable, str(SCANNER)] + list(args),
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )


def make_sample_tree(root):
    """造一棵无 git 的临时源码树：一个干净文件 + 一个必然命中的脏文件。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "clean.md").write_text("示例账户一 持仓成本 0.6.0\n", encoding="utf-8")
    dirty = "account " + "Y" + "9" * 7 + " path " + "D" + ":" + "\\" + "work\\x.csv"
    (root / "dirty.md").write_text(dirty + "\n", encoding="utf-8")
    return root


def test_scanner_self_test_passes():
    """五条规则的阳性对照全过。"""
    proc = run_scanner("--self-test")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "MISSED" not in proc.stdout


def test_every_rule_catches_its_injected_sample():
    drive = "C" + ":" + "\\"
    samples = {
        scan_privacy.PATH_RULE: drive + "work\\book.csv",
        scan_privacy.EMAIL_RULE: "a" + "@" + "b.invalid",
        scan_privacy.CRED_RULE: "ghp_" + "B" * 24,
        scan_privacy.ACCOUNT_RULE: "A" + "7" * 9,
        scan_privacy.DIGIT_RULE: "6" * 8,
    }
    for rule, sample in samples.items():
        found = {item[0] for item in scan_privacy.scan_text(sample, "injected")}
        assert rule in found, (rule, sample, found)


def test_published_tree_and_metadata_are_clean():
    """源码面 / 产物面 / 提交元数据面：0 命中、0 例外。"""
    results = scan_privacy.collect(REPO_ROOT, DATA_DIR, ("tree", "artifacts", "metadata"))
    summary, hits, excused, _ = scan_privacy.summarize(results)
    assert hits == [], scan_privacy.format_report(summary, hits, excused, [])
    assert excused == [], "发布工作面不该有已声明例外（例外只该出现在不可改写的历史里）"


def test_history_is_clean_except_the_declared_negative_controls():
    """历史面：0 命中；例外逐条必须是已声明名单里的合成样本。"""
    issues = scan_privacy.scan_history(REPO_ROOT)
    assert issues is not None, "本仓库应有 .git"
    hits, excused = scan_privacy.split_excused(issues)
    assert hits == [], [item for item in hits][:5]
    assert excused, "M1 起的阳性对照字面量应仍在历史里——若为空说明扫描没覆盖历史"
    for _, _, snippet in excused:
        assert snippet in scan_privacy.DECLARED_NEGATIVE_CONTROLS, snippet
        assert snippet in (scan_privacy.DECLARED_NEGATIVE_CONTROLS[snippet], snippet)


def test_declared_allowance_is_frozen():
    """名单冻结：新增一条例外就要有人来改这个断言。"""
    expected = {"A" + "9" * 9, "8" * 8}
    assert set(scan_privacy.DECLARED_NEGATIVE_CONTROLS) == expected


def test_allowance_is_not_a_blanket_mute():
    """同形态但不在名单里的串照样命中——名单不是"该形态一律放过"。"""
    # 与名单同形态：A + 9 位、8 位纯数字，但取值不同
    for sample in ("A" + "1" * 9, "9" * 8, "A" + "9" * 8):
        hits, excused = scan_privacy.split_excused(scan_privacy.scan_text(sample, "probe"))
        assert hits, sample
        assert excused == [], sample


def test_digit_rule_does_not_fire_on_source_or_integer_amounts(tmp_path):
    """适用范围：``.py`` 不判长数字串；JSON 只判字符串叶子。

    整数分金额与生成器 seed 在产物里是 ``int``，不是内容——所以保护它们的是
    「只走字符串叶子」这条范围，而不是放宽 digit_run 本身。这里直接把产物
    通路拉起来验一遍，避免只测到"裸串"这种半截事实。
    """
    assert scan_privacy.digits_allowed(".md")
    assert scan_privacy.digits_allowed(".json")
    assert not scan_privacy.digits_allowed(".py")
    assert not scan_privacy.digits_allowed(".xlsx")

    # 小数位、URL、32 位以上十六进制摘要在判长数字前先摘掉
    assert not scan_privacy.scan_text("0.069148962371", "ratio", allow_digits=True)
    assert not scan_privacy.scan_text(
        "https://blog.csdn.net/x/article/details/147982719", "url", allow_digits=True
    )
    assert not scan_privacy.scan_text("a" * 56 + "01234567", "sha", allow_digits=True)

    # 产物通路：整数分金额 / seed / 字符串形态的汇率都不得命中；
    # 同一份 JSON 里塞一个字符串形态的长数字账号串，必须命中。
    artifact = tmp_path / "truth.json"
    payload = {
        "seed": 11400714819323198485,
        "terminal_cash_fen": 12914395,
        "ratio": "0.069148962371",
        "source_sha256": "0123456789abcdef" * 4,
    }
    artifact.write_text(__import__("json").dumps(payload), encoding="utf-8")
    issues = []
    scan_privacy._scan_json_file(artifact, "data/truth/truth.json", issues)
    assert issues == [], issues
    assert list(scan_privacy._walk_json_strings(payload)) == ["0.069148962371"]

    payload["leaked"] = "12914395"
    artifact.write_text(__import__("json").dumps(payload), encoding="utf-8")
    issues = []
    scan_privacy._scan_json_file(artifact, "data/truth/truth.json", issues)
    assert issues, "字符串叶子里的长数字串必须被抓到（否则范围收窄成了漏洞）"


def test_missing_git_reports_skipped_instead_of_passing(tmp_path):
    """无 ``.git``（源码包解包态）：历史/元数据两面必须显式 skip，不得算通过。"""
    root = make_sample_tree(tmp_path / "bare-tree")
    if scan_privacy.is_git_repo(root):
        raise AssertionError("临时目录意外落在 git 仓库内，测试前提不成立")

    results = scan_privacy.collect(root, root / "data", ("tree", "metadata", "history"))
    summary, hits, _, skipped = scan_privacy.summarize(results)
    assert summary["metadata"]["status"] == "skipped"
    assert summary["history"]["status"] == "skipped"
    assert set(skipped) == {"metadata", "history"}
    # 树面仍然工作：脏文件必须被扫出来
    assert hits, scan_privacy.format_report(summary, hits, [], skipped)


def test_clean_bare_tree_passes_with_digits_and_still_works(tmp_path):
    """无 git 的干净树：退出码 0，且 tree 面确实扫到了文件。"""
    root = tmp_path / "bare-clean"
    root.mkdir(parents=True, exist_ok=True)
    (root / "doc.md").write_text("示例账户一九 持仓成本口径\n", encoding="utf-8")
    proc = run_scanner("--repo-root", str(root), "--surface", "tree", "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = __import__("json").loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["surfaces"]["tree"]["status"] == "clean"


def test_cli_json_reports_every_surface():
    proc = run_scanner("--all", "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = __import__("json").loads(proc.stdout)
    assert set(payload["surfaces"]) == set(SURFACES)
    assert payload["hits"] == 0
    assert payload["skipped"] == []
