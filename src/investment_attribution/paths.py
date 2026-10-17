"""路径解析。

优先级：显式参数 > 环境变量 ``IA_DATA_DIR`` > 源码树探测。
**不做 CWD 上溯兜底**——CWD 兜底会让"在哪个目录敲命令"改变结果，
是典型的隐式副作用。

源码树安装（``pip install -e .``）与直接 ``python -m`` 是本期支持的两条通路；
非 editable 的 wheel 安装不在承诺范围（见 README「已知环境问题」）。
"""

import os
from pathlib import Path

from .errors import DataError

ENV_DATA_DIR = "IA_DATA_DIR"
_MARKERS = ("pyproject.toml",)
_MAX_WALK_UP = 5


def _looks_like_repo_root(path):
    return all((path / marker).is_file() for marker in _MARKERS) and (path / "data").is_dir()


def find_repo_root(start=None):
    """从 ``start`` 逐级向上寻找含 pyproject.toml 与 data/ 的仓库根。"""
    current = Path(start) if start is not None else Path(__file__).resolve()
    if current.is_file():
        current = current.parent
    for _ in range(_MAX_WALK_UP + 1):
        if _looks_like_repo_root(current):
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def default_data_dir():
    """解析默认数据目录；解析不到时抛 ``DataError``（带可执行指引）。"""
    explicit = os.environ.get(ENV_DATA_DIR)
    if explicit:
        return Path(explicit).expanduser()

    root = find_repo_root()
    if root is None:
        raise DataError(
            "找不到随包数据目录。请在仓库源码树内运行，或显式传 --data-dir，"
            "或设置环境变量 {}。".format(ENV_DATA_DIR),
            code="DATA_DIR_NOT_FOUND",
        )
    return root / "data"


def resolve_data_dir(value=None):
    """把 CLI 参数/环境变量/源码树探测收口成一个入口。"""
    if value is None:
        return default_data_dir()
    path = Path(value).expanduser()
    if not path.is_dir():
        raise DataError(
            "数据目录不存在：{}".format(path),
            code="DATA_DIR_NOT_FOUND",
        )
    return path
