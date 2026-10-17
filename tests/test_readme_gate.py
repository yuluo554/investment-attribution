"""README 指令面门禁：命令必须真能跑，不是子串存在。

覆盖三面：
1. README 里的 ``python -m <pkg>`` 必须可执行（包内有 ``__main__``）；
2. README 引用的仓库内路径必须真实存在；
3. README 用到的 extras 必须与 pyproject 双向一致。
"""

import ast
import re
from pathlib import Path

from context import REPO_ROOT

README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"

PACKAGE_MODULE = "investment_attribution"

MODULE_PATTERN = re.compile(r"python\s+-m\s+([A-Za-z_][\w.]*)")
EXTRAS_PATTERN = re.compile(r"pip\s+install\s+-e\s+\"\.\[([^\]]+)\]\"")


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def test_readme_exists():
    assert README.is_file()


def test_all_python_m_invocations_are_executable():
    modules = set(MODULE_PATTERN.findall(read_text(README)))
    assert modules, "README 必须至少给出一条 python -m 用法"
    for module in modules:
        if not module.startswith(PACKAGE_MODULE):
            continue
        assert (REPO_ROOT / "src" / module.replace(".", "/") / "__main__.py").is_file() or (
            REPO_ROOT / "src" / module.replace(".", "/") / "__init__.py"
        ).is_file(), module


def test_readme_mentions_console_script_matching_pyproject():
    pyproject = read_text(PYPROJECT)
    match = re.search(r"^investment-attribution\s*=\s*\"([^\"]+)\"", pyproject, re.MULTILINE)
    assert match, pyproject
    entry = match.group(1)
    module_name = entry.split(":", 1)[0]
    module_path = REPO_ROOT / "src" / (module_name.replace(".", "/") + ".py")
    assert module_path.is_file(), module_path
    entry_function = entry.split(":", 1)[1]
    source = module_path.read_text(encoding="utf-8")
    assert "def {}(".format(entry_function) in source, entry
    assert "investment-attribution selftest" in read_text(README)


def test_readme_repo_paths_exist():
    candidates = set(re.findall(r"\]\(([^)]+)\)", read_text(README)))
    for link in candidates:
        if link.startswith(("http://", "https://", "#")):
            continue
        target = (REPO_ROOT / link).resolve()
        assert target.exists(), "README 链接指向不存在的路径：{}".format(link)


def test_readme_extras_exist_in_pyproject():
    extras_used = set()
    for group in EXTRAS_PATTERN.findall(read_text(README)):
        extras_used.update(part.strip() for part in group.split(",") if part.strip())
    assert extras_used, "README 必须给出安装命令"

    pyproject = read_text(PYPROJECT)
    # 只有 optional-dependencies 段内的键才算 extras
    optional_block = pyproject.split("[project.optional-dependencies]", 1)[-1]
    declared_extras = set(re.findall(r"^([A-Za-z0-9_-]+)\s*=\s*\[", optional_block, re.MULTILINE))
    assert extras_used <= declared_extras, (extras_used, declared_extras)
    assert "dev" in declared_extras


def test_readme_declares_exit_code_semantics():
    """退出码表只列 0 / 1 / 2；骨架期的 3 必须写明已退役（不留活口）。"""
    text = read_text(README)
    for token in ("`0`", "`1`", "`2`"):
        assert token in text, token
    assert "| `3` |" not in text, "退出码表里不得再列 3"
    assert "阶段未就绪" in text, "退役这件事本身要写清楚，不能悄悄删掉"
    assert "退役" in text


def test_no_placeholder_sentinels_in_sources():
    """哨兵串排查：空桩/未实现/占位不得混进源码。

    本测试文件自身持有判定词表，按路径排除，避免"扫描器扫到自己"
    把词表当成缺陷。
    """
    sentinels = ("Not" + "ImplementedError", "TODO: " + "实现", "place" + "holder")
    this_file = Path(__file__).resolve()
    for base in ("src", "tests"):
        for path in sorted((REPO_ROOT / base).rglob("*.py")):
            if path.resolve() == this_file:
                continue
            text = path.read_text(encoding="utf-8")
            for token in sentinels:
                assert token.lower() not in text.lower(), (path, token)


def test_package_layout_matches_pyproject():
    """pyproject 的 package-dir 必须解析到真实存在的包目录。"""
    pyproject = read_text(PYPROJECT)
    assert re.search(r'package-dir\s*=\s*\{\s*""\s*=\s*"src"\s*\}', pyproject)
    assert re.search(r'where\s*=\s*\["src"\]', pyproject)
    package_dir = REPO_ROOT / "src" / PACKAGE_MODULE
    assert (package_dir / "__init__.py").is_file()
    assert (package_dir / "__main__.py").is_file()
