"""导入适配层：编码探测、单元格壳清洗、列名归一、行级台账。

分层：

- ``encoding``  编码探测（K6：UTF-8(BOM) → UTF-8 → GB18030，不猜测）
- ``cells``     ``="..."`` 整格壳清洗（不宽松修补）
- ``tables``    xlsx / 分隔文本读取（按文件形态判别，不看扩展名）
- ``schema``    列名别名归一 + 表头结构校验
- ``values``    日期 / 时间 / 数量 / 金额 / 方向解析
- ``records``   单行 → 统一流水；失败抛 ``RowError``（带原因码）
- ``service``   文件级编排：去重、账户映射、产物落盘

本层只读输入文件，不写回、不联网、不读时钟。
"""

from .service import ImportResult, import_files  # noqa: F401
