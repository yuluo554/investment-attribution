"""多账户合并（M5）：跨账户合并视图 + 逐笔划转台账 + 两端剔除证据。

- ``engine``  合并入口：吃 M4 的 ``attribution.json``，加一层划转台账与守恒断言
- ``verify``  B5 逐项比对（供测试与 ``bench --only B5`` 共用）

口径唯一定义处 = `data/README.md §4.6`。成本口径是**显式参数，无默认值**；
划转模式来自冻结规则包 ``transfer_patterns``，代码里没有第二份业务名表。
"""

from .engine import (  # noqa: F401
    MERGED_FILE,
    MergeResult,
    build_merge,
    load_transfer_patterns,
)
from .verify import B5Report, compare as compare_b5  # noqa: F401
