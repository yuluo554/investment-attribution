"""带**物理行号**的分隔文本解析。

标准库 ``csv.reader`` 不返回行号，而审计行必须带源文件行号；此外
``csv`` 对引号内换行会把一个逻辑行拆成多行，行号会错位。这里实现一个
最小 RFC 4180 解析器：返回 ``[(line_no, cells)]``，``line_no`` 是记录
**起始**的物理行号（1 基），引号内换行会让下一行的行号跳号——这正是
源文件里的真实位置。

宽松点只有两个，且都对应真实导出行为：
- 引号内的 ``""`` 转义为 ``"``；
- 引号内允许换行与任意分隔符。
未被引号包裹的字段一律原样保留（含首尾空格），因为裁剪会掩盖错位。
"""


def parse_rows(text, delimiter):
    rows = []
    fields = []
    field = []
    in_quotes = False
    line_no = 1
    record_line = 1
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if in_quotes:
            if char == '"':
                if index + 1 < length and text[index + 1] == '"':
                    field.append('"')
                    index += 2
                    continue
                in_quotes = False
                index += 1
                continue
            if char == "\n":
                line_no += 1
            field.append(char)
            index += 1
            continue

        if char == '"' and not field:
            in_quotes = True
            index += 1
            continue
        if char == delimiter:
            fields.append("".join(field))
            field = []
            index += 1
            continue
        if char == "\r":
            if index + 1 < length and text[index + 1] == "\n":
                index += 1
            char = "\n"
        if char == "\n":
            fields.append("".join(field))
            rows.append((record_line, fields))
            fields = []
            field = []
            index += 1
            line_no += 1
            record_line = line_no
            continue
        field.append(char)
        index += 1

    if field or fields:
        fields.append("".join(field))
        rows.append((record_line, fields))
    return rows
