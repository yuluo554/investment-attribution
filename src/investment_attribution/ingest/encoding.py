"""编码探测（K6）。

顺序写死：``UTF-8(BOM) → UTF-8 → GB18030``，全部失败即报错。
**不猜测、不静默替换字符**：任何一次 ``errors="replace"`` 都会让坏字节
变成看似正常的中文，导入层就再也不可能发现输入已损坏。
"""

from ..errors import InputError

UTF8_BOM = b"\xef\xbb\xbf"
CANDIDATES = ("utf-8-sig", "utf-8", "gb18030")


def decode_bytes(raw, source="<bytes>"):
    """返回 ``(text, encoding)``；三分支全失败抛 ``InputError``。"""
    if raw is None:
        raise InputError("{}: 内容为空指针".format(source), code="ENCODING_UNDECODABLE")
    for encoding in CANDIDATES:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise InputError(
        "{}: 编码探测失败（已按 {} 依次尝试，均无法无损解码）".format(
            source, " → ".join(CANDIDATES)
        ),
        code="ENCODING_UNDECODABLE",
    )


def has_bom(raw):
    return raw.startswith(UTF8_BOM)
