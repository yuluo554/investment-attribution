"""文档计数守门：`plan/` 里写的「N passed」必须等于实测的全量收集数。

背因（D2）：`plan/` 里曾写 **429 passed**，而实测是 **431** —— 测试数会随实现漂移，
而文档里的数字属于发布证据的一部分，漂了就是假话。本仓纪律本来就写着
「不写会随实现漂移的硬数字；确需数字以末态回填」，这条门禁把那份纪律钉成判据：

- 真跑一遍 `pytest --collect-only`，取**全量**收集数（本文件自身也在内，
  所以本文件同样计入：**加一条测试就要同步 `plan/` 里的数字**）；
- `plan/` 里声明的「末态 / 发布物」计数（`FINAL_DOCS`）只允许等于该实测数；
- 各里程碑当时的历史快照数（`HISTORICAL`）按名单放行，但名单里每个数都必须
  **在 `plan/` 里真的存在** —— 否则名单会退化成万能静音键（本仓脱敏门禁
  「excused 名单被测试冻结」是同一个思路）。
"""

import os
import re
import subprocess
import sys

from context import REPO_ROOT

PLAN_DIR = REPO_ROOT / "plan"

# 声明「末态 / 发布物」计数的文件面（各里程碑 HANDOFF 快照不在此列）。
FINAL_DOCS = (
    "00-README总览.md",
    "05-里程碑.md",
    "06-交付对标.md",
    "HANDOFF-M7.md",
    "RELEASE-M6.md",
)

# 允许留在 plan/ 里的历史快照计数（各里程碑当时的实测数，不是末态声明）。
# 每个数都必须在 plan/ 里真的存在，否则本门禁红（防名单堆积）。
HISTORICAL = {
    33: "M1",
    111: "M2",
    161: "M3",
    240: "M4",
    310: "M5",
    419: "M6（tag 前那版留档）",
}

COUNT_RE = re.compile(r"(\d+)\s+passed")


def measure_collected():
    """真跑一遍收集，返回全量收集数（纯净环境：不继承 PYTHONPATH 与 addopts）。"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    assert proc.returncode == 0, proc.stdout
    match = re.search(r"(\d+)\s+tests?\s+collected", proc.stdout)
    assert match, proc.stdout[-2000:]
    return int(match.group(1))


def declared_counts():
    """返回 `{plan 文件名: {该文件里声明的计数}}`。"""
    counts = {}
    for path in sorted(PLAN_DIR.glob("*.md")):
        counts[path.name] = {int(n) for n in COUNT_RE.findall(path.read_text(encoding="utf-8"))}
    return counts


def test_plan_declared_counts_match_measured_suite():
    measured = measure_collected()
    counts = declared_counts()

    final_numbers = set()
    for name in FINAL_DOCS:
        assert name in counts, "plan/ 缺少末态声明文件：{}".format(name)
        final_numbers |= counts[name]

    declared = final_numbers - set(HISTORICAL)
    assert declared == {measured}, (
        "plan/ 的末态计数声明 {} 与实测收集数 {} 不一致；"
        "加/删测试后请同步 plan/ 里对应的数字".format(sorted(declared), measured)
    )

    everything = set()
    for numbers in counts.values():
        everything |= numbers
    missing = sorted(set(HISTORICAL) - everything)
    assert not missing, "HISTORICAL 名单里的 {} 在 plan/ 里已不存在，请从名单里删掉".format(missing)
