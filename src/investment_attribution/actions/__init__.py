"""公司行为还原（M3）：规则表匹配 + 人工确认队列 + 调整可逆。

- ``rules``  冻结规则包加载与业务名匹配（不写第二份业务名表）
- ``engine`` 编排：按 M2 行形态推进账本、挂 ``rule_id`` + 原始行、记 before/after
- ``verify`` B3 逐类事件比对（供测试与 M5 ``bench`` 共用）

口径唯一定义处 = `data/README.md §4.1.2`。成本口径是**显式参数，无默认值**。
"""

from .engine import (  # noqa: F401
    ACTIONS_FILE,
    PENDING_FILE,
    ActionsResult,
    build_actions,
    load_confirmations,
)
from .rules import (  # noqa: F401
    EVENT_TYPES,
    CorporateActionRules,
    load_corporate_action_rules,
)
from .verify import B3Report, compare as compare_b3  # noqa: F401
