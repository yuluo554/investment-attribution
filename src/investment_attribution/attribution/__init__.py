"""收益归因（M4）：XIRR / TWR / 已实现未实现拆分 / 费用拖累 / 多维分解。

- ``xirr``       求解器（Newton–Raphson → 二分兜底，参数来自参数包）
- ``twr``        时间加权收益率（cashflow / daily 两种估值点口径）
- ``valuation``  估值序列输入（给价；数量做交叉核对）
- ``benchmark``  可选本地基准序列（缺了不显示超额列，命令仍成功）
- ``industries`` 行业映射（未分类兜底且可见）
- ``engine``     编排：现金流还原、拆分、分解、产物
- ``verify``     B4 逐账户/逐视图比对（供测试与 M5 ``bench`` 共用）

口径唯一定义处 = `data/README.md §4.2.1 / §4.3.1`。成本口径是**显式参数，
无默认值**；TWR 估值点口径的默认值来自参数包（既定口径，非遗漏）。
"""

from .benchmark import Benchmark, load_benchmark  # noqa: F401
from .engine import (  # noqa: F401
    ATTRIBUTION_FILE,
    AttributionResult,
    build_attribution,
)
from .industries import Industries, load_industries  # noqa: F401
from .twr import compute_twr  # noqa: F401
from .valuation import Valuation, load_valuation, market_value_fen  # noqa: F401
from .verify import B4Report, compare as compare_b4  # noqa: F401
from .xirr import (  # noqa: F401
    XIRR_NO_SOLUTION,
    XirrSolution,
    load_xirr_params,
    rate_text,
    solve_xirr,
)
