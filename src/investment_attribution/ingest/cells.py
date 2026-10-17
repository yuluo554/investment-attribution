"""单元格壳清洗（``="..."``）。

真实券商导出常见两种形态：

- 整格包裹：``="699001"`` → ``699001``
- CSV 里被二次转义：``"=\\"699001\\""``（分隔文本读取后仍是 ``="699001"``）

纪律：**只剥离整格包裹**。剥离后仍然含引号（例如 ``="示例"转债丁"``）
一律判 ``INVALID_CELL_SHELL``，不做宽松修补——宽松修补会把坏单元格
悄悄变成合法值，导入层的事故就再也查不出来。
"""


def strip_shell(value):
    """返回 ``(clean, ok)``；不含壳的值原样返回且 ``ok=True``。"""
    if value is None:
        return "", True
    text = value.strip()
    if len(text) < 3 or not text.startswith('="') or not text.endswith('"'):
        return value, True
    inner = text[2:-1]
    if '"' in inner:
        return value, False
    return inner, True


def strip_shell_header(value):
    clean, ok = strip_shell(value)
    return clean.strip() if ok else clean
