"""测试共用的路径与夹具。"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
DATA_DIR = REPO_ROOT / "data"

# 允许不装包直接跑（源码态通路）；安装后的通路由 README 门禁单独验。
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# 测试不得写开发者真实主目录：把可能被写入的环境变量重定向到仓库外。
os.environ.setdefault("IA_DATA_DIR", str(DATA_DIR))
