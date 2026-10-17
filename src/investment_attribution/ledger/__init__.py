"""成本引擎（M2）：FIFO / 移动加权、费用入账、余额勾稽、每日快照。

- ``model``     行级语义（按行形态归类；金额整数分；不改状态）
- ``positions`` 持仓批次账（FIFO）与移动加权账（WAVG），共享接口
- ``engine``    编排：排序、分账户、勾稽、快照、产物

口径唯一定义处 = `data/README.md §4.1`。成本口径是**显式参数，无默认值**。
"""

from .engine import (  # noqa: F401
    HOLDINGS_FILE,
    SNAPSHOTS_FILE,
    LedgerResult,
    build_holdings,
    build_pnl,
    load_cost_params,
    read_ledger_file,
    require_method,
)
from .verify import B2Report, compare as compare_b2  # noqa: F401
