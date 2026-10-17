"""本地报告（M5）：单文件 HTML + 机读 ``report_data.json``，同源渲染。

- ``model``   报告数据模型：全部业务判定（口径复述、打码、下钻锚点）
- ``render``  单文件 HTML 渲染层：零判定（AST 门禁把守）
- ``guard``   渲染层零判定门禁 + 零外链出口扫描
- ``engine``  编排：读合并产物 → 构建 → 渲染 → 扫描 → 落盘

口径唯一定义处 = `data/README.md §四`。报告只复述口径，不新增一份。
"""

from .engine import (  # noqa: F401
    ReportResult,
    build_report,
    describe_report,
    read_merged_file,
)
from .guard import (  # noqa: F401
    RULES as RENDER_DECISION_RULES,
    scan_external_references,
    scan_renderer,
    scan_source,
)
from .model import (  # noqa: F401
    REPORT_DATA_FILE,
    REPORT_HTML_FILE,
    build_report_data,
    mask_account,
)
from .render import render_html  # noqa: F401
