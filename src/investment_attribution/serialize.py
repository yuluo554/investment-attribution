"""确定性序列化与原子写盘（全项目唯一写盘通路）。

K2 冻结契约：``json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)``
+ LF 行尾 + 结尾换行。任何子命令写 JSON 都必须经过本模块，禁止各写各的
``open() + json.dump()``——否则产物字节形态会随写法漂移。
"""

import json
import os
import tempfile

from .errors import UsageError

JSON_INDENT = 2
JSON_NEWLINE = "\n"


def dumps_json(obj):
    """把对象序列化成冻结形态的文本（不含写盘）。"""
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        indent=JSON_INDENT,
        separators=(",", ": "),
    ) + JSON_NEWLINE


def write_atomic(path, text, encoding="utf-8"):
    """先写同目录临时文件再 rename，防中断留下半个产物。"""
    target = os.fspath(path)
    if os.path.isdir(target):
        raise UsageError(
            "输出路径是目录而不是文件：{}".format(target),
            code="OUT_TARGET_IS_DIRECTORY",
        )
    directory = os.path.dirname(os.path.abspath(target))
    if not os.path.isdir(directory):
        raise UsageError(
            "输出目录不存在：{}".format(directory),
            code="OUT_DIR_NOT_FOUND",
        )
    handle, temp_path = tempfile.mkstemp(
        prefix=".ia-tmp-", suffix=".part", dir=directory
    )
    try:
        with os.fdopen(handle, "w", encoding=encoding, newline=JSON_NEWLINE) as stream:
            stream.write(text)
        os.replace(temp_path, target)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def write_json_atomic(path, obj):
    """按冻结契约写 JSON。"""
    write_atomic(path, dumps_json(obj))
