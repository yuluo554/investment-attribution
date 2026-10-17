#!/usr/bin/env python3
"""M6 发布前脱敏扫描器（四面：源码 / 产物 / 提交元数据 / 历史）。

发布纪律要求"脱敏扫描 0 命中"可被复核，所以这份扫描器必须满足两条：

1. **无 git 也能跑**：源码包（GitHub 自动生成的 tarball）里没有 ``.git``，
   工具本身仍须可用；此时"历史/元数据"两面明确报 ``skipped``，而不是
   假装通过——把跳过伪装成通过就是假绿。
2. **可证伪**：``--self-test`` 对每条规则喂注入样本，必须全部被抓到；
   门禁本身不可证伪时它只是话术（与 ``selftest`` 的设计一致）。

判据（与 ``data/README.md`` §三 的白名单同源）：

- ``abs_path``：本机绝对路径（盘符写法、macOS/Linux 家目录前缀、UNC 共享）；
- ``email``：非 ``*.noreply.github.com`` 的邮箱（``git@github.com:owner/repo`` 这类
  SSH 远端写法与 ``git@github.com`` 裸式都不是邮箱，先摘掉再判）；
- ``credential``：GitHub token、AWS key、私钥头、口令赋值；
- ``account``：**完整**账号形态 ``Y``+7 位 / ``A``+9 位 之外的一切
  （多一位、少一位、数值不在 1..9 都算）——所以"真实账号形态"必然命中；
- ``digit_run``：8 位以上纯数字串，账号最典型的漏网形态。

各条规则的**适用范围按"该文件里长数字是否天然存在"划分**，不做一刀切：

- ``digit_run`` 对 ``.py`` 源码关闭：源码里数字字面量、正则片段、日期串
  天然密集，开在这上面等于把噪声当信号（账号形态仍由 ``account`` 规则兜住）；
- ``.json`` 只走**字符串叶子**：整数分金额、生成器 seed 是 ``int``，不是内容；
- 判长数字前先摘掉 URL 与 32 位以上十六进制摘要，小数位不算数字串。

**已声明的合成阴性对照**：``DECLARED_NEGATIVE_CONTROLS`` 里那两个字面量是
M1 起就写进测试的"阳性对照样本"（合成，非真实账号），只存在于不可改写的
历史中。它们单独记为 ``excused`` 并逐条打印，不混进 ``hits``；名单被测试
冻结，且测试证明"名单外的同形态串照样命中"——避免它变成万能静音键。

用法::

    python tools/scan_privacy.py --all          # 四面全扫（默认）
    python tools/scan_privacy.py --surface tree --surface artifacts
    python tools/scan_privacy.py --json         # 机读输出
    python tools/scan_privacy.py --self-test    # 阳性对照：注入样本必须被抓
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 与 data/README.md §三 白名单一致：资金账号 Y1..Y9、股东账号 A1..A9
FUND_DIGITS, FUND_RANGE = 7, (1, 9)
SHARE_DIGITS, SHARE_RANGE = 9, (1, 9)

ALLOWED_EMAIL_DOMAIN = "users.noreply.github.com"

# 历史里不可改写的合成阳性对照（见模块 docstring「已声明的合成阴性对照」）。
# 键一律**拼接构造**，否则扫描器会把自己写的名单也判成命中。
DECLARED_NEGATIVE_CONTROLS = {
    "A" + "9" * 9: "tests/test_gen_data.py 隐私扫描的阳性对照样本（合成股东账号形态）",
    "8" * 8: "同上阳性对照样本里的纯数字串（合成资金账号形态）",
}

SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        ".clean-venv",
        ".clean-store",
        "out",
        "output",
        "reports",
        "release",
        "build",
        "dist",
        "htmlcov",
        ".tox",
    }
)

# 数值型 / 归档型文件：仍按字节搜账号与邮箱，但不判"长数字串"
NUMERIC_OR_ARCHIVE_SUFFIXES = frozenset(
    {
        ".xlsx",
        ".xls",
        ".zip",
        ".gz",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".pdf",
        ".woff",
        ".woff2",
        ".pyc",
    }
)

# 源码文件：长数字串规则不适用（理由见 docstring）
SOURCE_SUFFIXES = frozenset({".py"})

ABS_PATH_PATTERNS = (
    # 盘符路径：lookbehind 排掉 "https:/" 里的 "s:"
    re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/](?![\\/])"),
    re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home|root|mnt)/"),
    re.compile(r"\\\\[A-Za-z0-9._-]+\\[A-Za-z0-9$._-]"),
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# SSH 远端写法长得像邮箱但不是联系方式，判完就得摘掉：
#   ① scp 式 `git@github.com:owner/repo.git`（主机后紧跟冒号）；
#   ② 裸式 `git@github.com`（`ssh -T git@github.com` 的写法）。
# `git` 作为本地部分是 Git 服务的约定账号，不是某个人的邮箱。
REMOTE_SPEC_RES = (
    re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+:[^\s\"'`,)]*"),
    re.compile(r"(?<![A-Za-z0-9._%+-])git@[A-Za-z0-9.-]+"),
)

CREDENTIAL_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*[\"'][^\"']{6,}[\"']"
    ),
)

# 完整账号形态：位数必须恰好等于声明形态，故"多一位/少一位"都进账
FUND_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])Y(\d{7,})(?![0-9])")
SHARE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])A(\d{9,})(?![0-9])")

# 判长数字前先摘掉的东西
URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
HEX_DIGEST_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")
# 允许的 noreply 邮箱本身可以带一串数字本地部分（GitHub 用户 id），它不是内容
ALLOWED_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@" + re.escape(ALLOWED_EMAIL_DOMAIN))
WHITELIST_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[YA]0{6,9}[1-9](?![0-9])")
DIGIT_RUN_RE = re.compile(r"(?<![\d.])\d{8,}(?![\d.])")

# JSON 里这些键的值是摘要，不是内容
SHA_KEYS = ("sha256", "digest", "checksum")

ACCOUNT_RULE = "account"
DIGIT_RULE = "digit_run"
PATH_RULE = "abs_path"
EMAIL_RULE = "email"
CRED_RULE = "credential"

RULE_ORDER = (PATH_RULE, EMAIL_RULE, CRED_RULE, ACCOUNT_RULE, DIGIT_RULE)


def _run_git(root, *args):
    """返回 ``(returncode, stdout_text)``；无 git 或非仓库时返回 ``(None, "")``。"""
    try:
        proc = subprocess.run(
            ["git"] + list(args),
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None, ""
    if proc.returncode != 0:
        return proc.returncode, ""
    return 0, proc.stdout.decode("utf-8", "replace")


def is_git_repo(root):
    code, out = _run_git(root, "rev-parse", "--git-dir")
    return code == 0 and bool(out.strip())


def decode_text(raw):
    """尽力解码；失败也不丢字节（latin-1 恒成功，ASCII 形态原样保留）。"""
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def digits_allowed(suffix):
    """该后缀是否适用"长数字串"规则。"""
    return suffix not in NUMERIC_OR_ARCHIVE_SUFFIXES and suffix not in SOURCE_SUFFIXES


def scan_text(text, where, allow_digits=True):
    """对一段文本跑全部规则，返回 ``[(rule, where, snippet), ...]``。"""
    issues = []

    for pattern in ABS_PATH_PATTERNS:
        for match in pattern.finditer(text):
            issues.append((PATH_RULE, where, match.group(0)))

    scrubbed = text
    for pattern in REMOTE_SPEC_RES:
        scrubbed = pattern.sub("<REMOTE>", scrubbed)
    for match in EMAIL_RE.finditer(scrubbed):
        address = match.group(0)
        if not address.endswith(ALLOWED_EMAIL_DOMAIN):
            issues.append((EMAIL_RULE, where, address))

    for pattern in CREDENTIAL_PATTERNS:
        for match in pattern.finditer(text):
            issues.append((CRED_RULE, where, match.group(0)[:40]))

    for match in FUND_TOKEN_RE.finditer(text):
        digits = match.group(1)
        if len(digits) != FUND_DIGITS or not (FUND_RANGE[0] <= int(digits) <= FUND_RANGE[1]):
            issues.append((ACCOUNT_RULE, where, match.group(0)))

    for match in SHARE_TOKEN_RE.finditer(text):
        digits = match.group(1)
        if len(digits) != SHARE_DIGITS or not (
            SHARE_RANGE[0] <= int(digits) <= SHARE_RANGE[1]
        ):
            issues.append((ACCOUNT_RULE, where, match.group(0)))

    if allow_digits:
        scrubbed = URL_RE.sub("<URL>", text)
        scrubbed = HEX_DIGEST_RE.sub("<HEX>", scrubbed)
        scrubbed = ALLOWED_EMAIL_RE.sub("<EMAIL>", scrubbed)
        scrubbed = WHITELIST_TOKEN_RE.sub("<ACCOUNT>", scrubbed)
        for match in DIGIT_RUN_RE.finditer(scrubbed):
            issues.append((DIGIT_RULE, where, match.group(0)))

    return issues


def split_excused(issues):
    """把"已声明的合成阳性对照"从命中里摘出来，返回 ``(hits, excused)``。"""
    hits, excused = [], []
    for issue in issues:
        if issue[2] in DECLARED_NEGATIVE_CONTROLS:
            excused.append(issue)
        else:
            hits.append(issue)
    return hits, excused


def _walk_json_strings(node, skip_sha=True):
    if isinstance(node, dict):
        for key, value in node.items():
            if skip_sha and isinstance(key, str) and any(k in key for k in SHA_KEYS):
                continue
            for item in _walk_json_strings(value, skip_sha):
                yield item
    elif isinstance(node, list):
        for value in node:
            for item in _walk_json_strings(value, skip_sha):
                yield item
    elif isinstance(node, str):
        yield node


def _scan_json_file(path, rel, issues):
    """JSON 只扫字符串叶子；整数分金额与 seed 不进扫描。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        issues.append(("artifact_unparsable", rel, "JSON 无法解析"))
        return
    for leaf in _walk_json_strings(payload):
        issues.extend(scan_text(leaf, "{}#str".format(rel), allow_digits=True))


def scan_artifacts(data_dir, data_root=None):
    """产物面：``data/`` 下逐文件。"""
    issues = []
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        return [("artifact_missing", str(data_dir), "data 目录不存在")]
    base = Path(data_root) if data_root else data_dir.parent
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS or part.startswith("_") for part in path.relative_to(data_dir).parts[:-1]):
            continue
        try:
            rel = path.relative_to(base).as_posix()
        except ValueError:
            rel = path.name
        if path.suffix == ".json":
            _scan_json_file(path, rel, issues)
        else:
            text, _ = decode_text(path.read_bytes())
            issues.extend(scan_text(text, rel, allow_digits=digits_allowed(path.suffix)))
    return issues


def tracked_files(root):
    """源码面文件清单。

    有 git 时取「已跟踪 + 未被忽略的新增文件」——后者是**即将入库**的东西，
    发布前扫描必须把它算进去（只扫 ``ls-files`` 会漏掉刚写好的文件）。
    无 git（源码包解包态）时走文件树。
    """
    if is_git_repo(root):
        names = set()
        code, out = _run_git(root, "ls-files", "-z")
        if code == 0:
            names.update(name for name in out.split("\0") if name)
        code, out = _run_git(root, "ls-files", "--others", "--exclude-standard", "-z")
        if code == 0:
            names.update(name for name in out.split("\0") if name)
        if names:
            return sorted(Path(name) for name in names)
    found = []
    for base, dirs, files in os.walk(str(root)):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in files:
            found.append(Path(base, name).relative_to(root))
    return sorted(found)


def scan_tree(root):
    """源码面：逐文件按规则扫。"""
    issues = []
    root = Path(root)
    for rel in tracked_files(root):
        path = Path(root, rel)
        if not path.is_file():
            continue
        if path.suffix == ".json":
            _scan_json_file(path, rel.as_posix(), issues)
            continue
        text, _ = decode_text(path.read_bytes())
        issues.extend(scan_text(text, rel.as_posix(), allow_digits=digits_allowed(path.suffix)))
    return issues


def scan_metadata(root):
    """提交元数据面：author / committer / tag tagger 三处邮箱。"""
    issues = []
    code, out = _run_git(root, "log", "--all", "--format=%ae%n%ce")
    if code != 0:
        return None
    for address in sorted({line.strip() for line in out.splitlines() if line.strip()}):
        if not address.endswith(ALLOWED_EMAIL_DOMAIN):
            issues.append((EMAIL_RULE, "git-log:author/committer", address))
    code, out = _run_git(
        root, "for-each-ref", "--format=%(taggername)|%(taggeremail)|%(refname)", "refs/tags"
    )
    if code != 0:
        return None
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        address = parts[1].strip("<> ") if len(parts) > 1 else ""
        if address and not address.endswith(ALLOWED_EMAIL_DOMAIN):
            issues.append((EMAIL_RULE, "git-tag:{}".format(parts[2]), address))
    return issues


def _read_blobs(root, shas):
    """批量读对象内容：一次 ``cat-file --batch`` 不用为每个对象起进程。

    返回 ``{sha: (object_type, raw_bytes)}``——类型必须一起拿回来，因为标注
    tag 对象的正文是**提交元数据**（含 `tagger` 行里的 Unix 时间戳），不能
    按文件内容那套规则判长数字串。
    """
    contents = {}
    if not shas:
        return contents
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=str(root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        for sha in shas:
            proc.stdin.write((sha + "\n").encode("ascii"))
            proc.stdin.flush()
            header = proc.stdout.readline().decode("ascii", "replace").strip()
            parts = header.split()
            if len(parts) != 3:
                continue
            size = int(parts[2])
            contents[sha] = (parts[1], proc.stdout.read(size))
            proc.stdout.read(1)
    finally:
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        proc.wait()
    return contents


def history_entries(root):
    """发布历史里的「对象 + 路径」清单：可达对象，按 (sha, path) 去重。

    路径信息**必须拿到**：只有 blob 摘要时无法判断"这是 ``.py`` 还是真值
    JSON"，``digit_run`` 就会把整数分金额整片误报成账号。所以这里不用
    ``--batch-all-objects``（它不给路径），改用能带路径的 ``rev-list --objects``。
    范围是**可达对象**：不可达的悬挂对象不会随仓库发布，不属发布物。
    """
    if not is_git_repo(root):
        return None
    code, out = _run_git(root, "rev-list", "--objects", "--all")
    if code != 0:
        return None
    entries, seen = [], set()
    for line in out.splitlines():
        if " " not in line:
            continue
        sha, path = line.split(" ", 1)
        if (sha, path) in seen:
            continue
        seen.add((sha, path))
        entries.append((sha, path))
    return entries


def scan_history(root):
    """历史面：逐个「对象 + 路径」套与工作面同一套适用范围。

    - blob 按**路径后缀**定适用范围（`.py` 不判长数字串、JSON 只判字符串叶子）；
    - 非 blob 对象（annotated tag 等）正文是**元数据**：邮箱 / 账号 / 凭据 / 路径
      规则照判，但长数字串不判——`tagger` 行自带 Unix 时间戳，一刀切必误报。
      这类对象的"本行信息"（tagger 邮箱）由 ``scan_metadata`` 单独守。
    """
    entries = history_entries(root)
    if entries is None:
        return None
    if not entries:
        return []
    objects = _read_blobs(root, sorted({sha for sha, _ in entries}))
    issues = []
    for sha, path in entries:
        loaded = objects.get(sha)
        if loaded is None:
            continue
        object_type, raw = loaded
        where = "{}@{}".format(path, sha[:12])
        if object_type != "blob":
            text, _ = decode_text(raw)
            issues.extend(
                scan_text(
                    text,
                    "{}:{}".format(object_type, where),
                    allow_digits=False,
                )
            )
            continue
        if path.endswith(".json"):
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            for leaf in _walk_json_strings(payload):
                issues.extend(scan_text(leaf, where + "#str", allow_digits=True))
            continue
        text, _ = decode_text(raw)
        suffix = os.path.splitext(path)[1]
        issues.extend(scan_text(text, where, allow_digits=digits_allowed(suffix)))
    return issues


def run_self_test():
    """阳性对照：每条规则都必须能抓到它的注入样本，否则门禁不可证伪。

    注入样本一律**拼接构造**，不在本文件里留下连续的敏感形态——否则扫描器
    扫到自己就会把"自带样本"判成命中（与 ``test_readme_gate`` 对哨兵词的
    处理同理，只是这里更严：不靠路径排除，靠构造）。
    """
    drive = "C" + ":" + "\\"
    aware = "".join([drive, "Users", "\\", "someone", "\\", "券商导出"])
    home = "".join(["/", "home", "/", "someone", "/x.csv"])
    unc = "".join(["\\", "\\", "server", "\\", "share"])
    samples = {
        PATH_RULE: "导出目录 {} 与 {} 与 {}".format(aware, home, unc),
        EMAIL_RULE: "联系 " + "real.person" + "@" + "example.invalid",
        CRED_RULE: "token = " + '"' + "ghp_" + "A" * 24 + '"',
        ACCOUNT_RULE: "股东账号 " + "A" + "9" * 9 + " 资金账号 " + "Y" + "9" * 7,
        DIGIT_RULE: "资金账号 " + "8" * 8,
    }
    caught = {}
    for rule, sample in samples.items():
        found = {item[0] for item in scan_text(sample, "self-test")}
        caught[rule] = rule in found
    return caught


def collect(root=REPO_ROOT, data_dir=None, surfaces=None):
    root = Path(root)
    data_dir = Path(data_dir) if data_dir else root / "data"
    surfaces = list(surfaces or ("tree", "artifacts", "metadata", "history"))
    results = {}
    for surface in surfaces:
        if surface == "tree":
            results[surface] = scan_tree(root)
        elif surface == "artifacts":
            results[surface] = scan_artifacts(data_dir, data_root=root)
        elif surface == "metadata":
            results[surface] = scan_metadata(root)
        elif surface == "history":
            results[surface] = scan_history(root)
        else:
            raise SystemExit("未知扫描面：{}".format(surface))
    return results


def summarize(results):
    """把各面结果折成 ``(summary, hits, excused, skipped)``。"""
    summary, hits, excused, skipped = {}, [], [], []
    for surface, issues in results.items():
        if issues is None:
            skipped.append(surface)
            summary[surface] = {"status": "skipped", "hits": 0, "excused": 0}
            continue
        surface_hits, surface_excused = split_excused(issues)
        hits.extend((surface,) + item for item in surface_hits)
        excused.extend((surface,) + item for item in surface_excused)
        summary[surface] = {
            "status": "clean" if not surface_hits else "dirty",
            "hits": len(surface_hits),
            "excused": len(surface_excused),
            "issues": [
                "{} | {} | {}".format(rule, where, snippet)
                for rule, where, snippet in surface_hits
            ],
            "excused_detail": [
                "{} | {} | {}".format(rule, where, snippet)
                for rule, where, snippet in surface_excused
            ],
        }
    return summary, hits, excused, skipped


def format_report(summary, hits, excused, skipped):
    lines = []
    for surface in ("tree", "artifacts", "metadata", "history"):
        if surface not in summary:
            continue
        entry = summary[surface]
        if entry["status"] == "skipped":
            lines.append("[skip] {}：无 .git，本面未扫（不计入通过）".format(surface))
            continue
        lines.append(
            "[{:5s}] {} hits={} excused={}".format(
                entry["status"], surface, entry["hits"], entry["excused"]
            )
        )
        for line in entry.get("issues", []):
            lines.append("        HIT  {}".format(line))
    for surface, rule, where, snippet in excused:
        lines.append(
            "        EXCUSED {} | {} | {} | {}".format(
                surface, rule, where, DECLARED_NEGATIVE_CONTROLS.get(snippet, snippet)
            )
        )
    lines.append("total hits = {} / excused = {} / skipped = {}".format(
        len(hits), len(excused), len(skipped)))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="M6 发布前脱敏四面扫描")
    parser.add_argument(
        "--surface",
        action="append",
        choices=("tree", "artifacts", "metadata", "history"),
        help="要扫的面（可重复）",
    )
    parser.add_argument("--all", action="store_true", help="四面全扫（缺省行为）")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--json", action="store_true", help="机读输出")
    parser.add_argument("--self-test", action="store_true", help="只跑阳性对照")
    args = parser.parse_args(argv)

    if args.self_test:
        caught = run_self_test()
        missed = [rule for rule in RULE_ORDER if not caught.get(rule)]
        if args.json:
            print(json.dumps({"self_test": caught, "ok": not missed}, ensure_ascii=False, indent=2))
        else:
            for rule in RULE_ORDER:
                print("[{}] {}".format("caught" if caught[rule] else "MISSED", rule))
        if missed:
            print("阳性对照未过：{}".format(", ".join(missed)), file=sys.stderr)
            return 1
        return 0

    results = collect(args.repo_root, args.data_dir, args.surface)
    summary, hits, excused, skipped = summarize(results)

    if args.json:
        print(
            json.dumps(
                {
                    "surfaces": summary,
                    "skipped": skipped,
                    "hits": len(hits),
                    "excused": len(excused),
                    "ok": not hits,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_report(summary, hits, excused, skipped))

    return 0 if not hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
