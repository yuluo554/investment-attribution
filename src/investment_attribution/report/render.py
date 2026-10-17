"""单文件 HTML 渲染层（M5）：只做模板，不做判定。

本文件是**零判定**的：口径、事件类型、收益构成一个词都不出现。所有需要判断
的东西都已经在 ``report/model.py`` 里算完，这里只把字符串摆上去。判定词表
由 ``report/guard.py`` 的 AST 门禁把守，正/负对照在 ``tests/test_report.py``。

三条硬约束：

- **零外链**：单文件、内嵌样式、没有内联脚本，通篇不含任何外部地址引用，
  断网打开完整可用；
- **可打印**：直接打开或打印为 PDF 都不缺字体与样式；
- **逐数字下钻**：锚点渲染成 ``文件:行号`` 的小标签，悬停显示源文件 sha256。
"""

from html import escape

HTML_FILE_FORMAT = "ia.report_html"

_HEAD = (
    '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>{title}</title>\n<style>\n{css}\n</style>\n</head>\n<body>\n"
)

_FOOT = "</body>\n</html>\n"

_CSS = """
:root {
  --ink: #1c2333; --muted: #5b6478; --line: #d9dee8; --bg: #f6f7fb;
  --card: #ffffff; --pos: #b4231f; --neg: #12734b; --accent: #2a4b8d;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px 20px 64px; background: var(--bg); color: var(--ink);
  font-family: "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", sans-serif;
  font-size: 14px; line-height: 1.6;
}
.wrap { max-width: 1180px; margin: 0 auto; }
header.top {
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 20px 22px; margin-bottom: 18px;
}
h1 { margin: 0 0 4px; font-size: 22px; letter-spacing: 0.5px; }
.sub { color: var(--muted); margin-bottom: 14px; }
.badges { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.badge {
  border: 1px solid var(--line); border-radius: 999px; padding: 3px 12px;
  background: #fbfcff; font-size: 12.5px;
}
.badge b { color: var(--accent); font-weight: 600; }
.notice { color: var(--muted); font-size: 12.5px; margin: 4px 0; }
section {
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 18px 20px; margin-bottom: 16px;
}
h2 { margin: 0 0 6px; font-size: 16.5px; }
.note { color: var(--muted); font-size: 12.5px; margin: 0 0 12px; }
.grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }
.card { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; background: #fcfdff; }
.card .lbl { color: var(--muted); font-size: 12.5px; }
.card .val { font-size: 21px; font-weight: 600; margin-top: 2px; }
.card .unit { font-size: 12.5px; color: var(--muted); margin-left: 3px; font-weight: 400; }
.pos .val { color: var(--pos); }
.neg .val { color: var(--neg); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { border-bottom: 1px solid var(--line); padding: 7px 8px; vertical-align: top; }
th { text-align: left; color: var(--muted); font-weight: 600; background: #f9fafd; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.emphasis td { font-weight: 600; background: #fbfcff; }
tr.pos td.num { color: var(--pos); }
tr.neg td.num { color: var(--neg); }
.chip {
  display: inline-block; border: 1px solid var(--line); border-radius: 4px;
  padding: 0 6px; font-size: 11.5px; color: var(--muted); background: #fbfcff;
  font-family: Consolas, "Courier New", monospace; white-space: nowrap;
}
.ev { width: 210px; }
.notes .item { border-left: 3px solid var(--line); padding: 6px 0 6px 12px; margin-bottom: 8px; }
.notes .item.bad { border-left-color: var(--pos); }
.notes .item.warn { border-left-color: #c58a00; }
.notes .item.ok { border-left-color: var(--neg); }
.notes .t { font-weight: 600; }
.notes .b { color: #333b4d; }
footer { color: var(--muted); font-size: 12px; text-align: center; padding-top: 8px; }
@media print {
  body { background: #fff; padding: 0; }
  section, header.top { break-inside: avoid; border-color: #bbb; }
}
"""


def _esc(value):
    return escape("" if value is None else str(value))


def _chip(items):
    if not items:
        return ""
    first = items[0] or {}
    label = "{}:{}".format(first.get("source_file") or "-", first.get("source_line_no"))
    more = "" if len(items) <= 1 else " +{}".format(len(items) - 1)
    title = "源文件 sha256：{}".format(first.get("source_sha256") or "-")
    return '<span class="chip" title="{}">{}{}</span>'.format(
        _esc(title), _esc(label), _esc(more)
    )


def _badges(items):
    parts = []
    for item in items or []:
        parts.append(
            '<span class="badge">{}</span>'.format(
                "<b>{}</b>".format(_esc(item.get("value"))) if item.get("value") is not None else _esc(item.get("label"))
            )
        )
    return '<div class="badges">{}</div>'.format("".join(parts))


def _figure_card(figure):
    tone = figure.get("tone") or "flat"
    return (
        '<div class="card {tone}">'
        '<div class="lbl">{label}</div>'
        '<div class="val">{value}<span class="unit">{unit}</span></div>'
        '<div class="lbl">{chip}</div>'
        "</div>"
    ).format(
        tone=_esc(tone),
        label=_esc(figure.get("label")),
        value=_esc(figure.get("value_text")),
        unit=_esc(figure.get("unit")),
        chip=_chip(figure.get("evidence")),
    )


def _kpi(section):
    return (
        '<section id="{sid}">\n<h2>{title}</h2>\n<p class="note">{note}</p>\n'
        '<div class="grid">{cards}</div>\n</section>\n'
    ).format(
        sid=_esc(section.get("id")),
        title=_esc(section.get("title")),
        note=_esc(section.get("note")),
        cards="".join(_figure_card(item) for item in section.get("figures") or []),
    )


def _table(section):
    head = ['<th class="hdr">#</th>']
    for column in section.get("columns") or []:
        align = "num" if column.get("align") == "right" else "txt"
        head.append('<th class="{}">{}</th>'.format(align, _esc(column.get("label"))))
    head.append('<th class="ev">原始行锚点</th>')
    body = []
    for index, row in enumerate(section.get("rows") or [], 1):
        classes = []
        if row.get("tone"):
            classes.append(row["tone"])
        if row.get("emphasis"):
            classes.append("emphasis")
        cells = ['<td class="num">{}</td>'.format(index)]
        for position, cell in enumerate(row.get("cells") or []):
            column = (section.get("columns") or [{}])[position] if position < len(section.get("columns") or []) else {}
            align = "num" if column.get("align") == "right" else "txt"
            cells.append('<td class="{}">{}</td>'.format(align, _esc(cell)))
        cells.append('<td class="ev">{}</td>'.format(_chip(row.get("evidence"))))
        body.append('<tr class="{}">{}</tr>'.format(" ".join(classes), "".join(cells)))
    return (
        '<section id="{sid}">\n<h2>{title}</h2>\n<p class="note">{note}</p>\n'
        "<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>\n</section>\n"
    ).format(
        sid=_esc(section.get("id")),
        title=_esc(section.get("title")),
        note=_esc(section.get("note")),
        head="".join(head),
        body="".join(body),
    )


def _notes(section):
    items = []
    for item in section.get("items") or []:
        items.append(
            (
                '<div class="item {tone}"><div class="t">{title}</div>'
                '<div class="b">{body}</div></div>'
            ).format(
                tone=_esc(item.get("tone") or "info"),
                title=_esc(item.get("title")),
                body=_esc(item.get("body")),
            )
        )
    return (
        '<section id="{sid}">\n<h2>{title}</h2>\n<p class="note">{note}</p>\n'
        '<div class="notes">{items}</div>\n</section>\n'
    ).format(
        sid=_esc(section.get("id")),
        title=_esc(section.get("title")),
        note=_esc(section.get("note")),
        items="".join(items),
    )


_RENDERERS = {
    "kpi": _kpi,
    "table": _table,
    "notes": _notes,
}


def _section(section):
    renderer = _RENDERERS.get(section.get("kind"))
    if renderer is None:
        return ""
    return renderer(section)


def render_html(payload):
    """把渲染模型渲染成单文件 HTML 文本（确定性：同输入同字节）。"""
    meta = payload.get("meta") or {}
    head_block = payload.get("header") or {}
    counts = payload.get("counts") or {}
    parts = [
        _HEAD.format(title=_esc(meta.get("title")), css=_CSS),
        '<div class="wrap">\n',
        '<header class="top">\n<h1>{}</h1>\n<div class="sub">{}</div>\n'.format(
            _esc(meta.get("title")), _esc(meta.get("subtitle"))
        ),
        _badges(head_block.get("badges")),
        "".join('<p class="notice">{}</p>\n'.format(_esc(text)) for text in head_block.get("notices") or []),
        "</header>\n",
    ]
    for section in payload.get("sections") or []:
        parts.append(_section(section))
    parts.append(
        '<footer>{} 个段落 / {} 个汇总数 / {} 行表格 / {} 条说明；'
        "每个汇总数都挂原始行锚点（文件名 + 行号 + 源文件 sha256）。</footer>\n".format(
            _esc(counts.get("section_count")),
            _esc(counts.get("figure_count")),
            _esc(counts.get("table_row_count")),
            _esc(counts.get("note_item_count")),
        )
    )
    parts.append("</div>\n")
    parts.append(_FOOT)
    return "".join(parts)
