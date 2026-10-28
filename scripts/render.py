#!/usr/bin/env python3
"""Markdown 概要 -> A4 分页 HTML，字号按目标页数自动降级。

输入是「已整理好的摘要 Markdown」，不是原始全文。
支持的 Markdown 子集：`#` / `##` / `###` 标题、段落、`- ` 列表、GFM 表格、
`**粗体**`、行内 `code`、`>` 提示块（`> !` 为警示块）、`---` 分隔线。
额外语法：`[[C]]` / `[[NA]]` / `[[ND]]` 生成枚举色块。
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a4env  # noqa: E402

# (字号pt, 行距, 单元格内边距px) —— 从宽松到紧凑（降序，选档逻辑依赖这个顺序）
TIERS = [
    (12.6, 1.82, 6.2),
    (11.8, 1.76, 5.8),
    (11.1, 1.70, 5.4),
    (10.4, 1.65, 4.8),
    (9.8, 1.60, 4.2),
    (9.3, 1.55, 3.8),
    (8.8, 1.50, 3.4),
    (8.4, 1.45, 3.0),
    (8.0, 1.40, 2.6),
    (7.7, 1.36, 2.2),
]

A4_W_PT, A4_H_PT = 595.28, 841.89  # A4 尺寸（pt），verify.py 用同一组值

FONT_STACK = ('"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", '
              '"Noto Sans CJK SC", "Source Han Sans SC", sans-serif')

CSS = """
@page {{ size: A4; margin: {margin}; }}
* {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ margin: 0; font-family: {font};
        font-size: {fs}pt; line-height: {lh}; color: #1a2330; }}
h1 {{ font-size: {h1}pt; margin: 0 0 3px; color: #14293f; letter-spacing: .2px; }}
.sub {{ font-size: {small}pt; color: #6b7885; margin: 0 0 8px; }}
.sub b {{ color: #14293f; }}
h2 {{ font-size: {h2}pt; margin: 9px 0 5px; padding: 4px 8px;
      background: #1a3a6b; color: #fff; break-after: avoid; }}
h3 {{ font-size: {h3}pt; margin: 7px 0 3px; padding: 2px 0 2px 7px;
      border-left: 3px solid #1a3a6b; background: #eef2f8; color: #1a3a6b;
      break-after: avoid; }}
p {{ margin: 0 0 4px; }}
ul, ol {{ margin: 2px 0 4px; padding-left: 16px; }}
li {{ margin: 0 0 1.5px; }}
table {{ width: 100%; border-collapse: collapse; margin: 0 0 4px; }}
tr {{ break-inside: avoid; }}
thead {{ display: table-header-group; }}
th, td {{ border: 0.5pt solid #d4dbe4; padding: {pad}px 5px;
          vertical-align: top; text-align: left; }}
th {{ background: #e9eef5; font-weight: 600; color: #14293f; font-size: {small}pt; }}
.note {{ font-size: {small}pt; color: #33414f; background: #f5f7fb;
         border-left: 2px solid #b9c4d6; padding: 4px 8px; margin: 4px 0 3px; }}
.warn {{ font-size: {small}pt; color: #8a3d14; background: #fff7f2;
         border-left: 2px solid #e0a071; padding: 4px 8px; margin: 4px 0 3px; }}
.chip {{ border-radius: 3px; padding: 0 4px; font-weight: 700;
         font-size: {small}pt; white-space: nowrap; }}
.c  {{ background: #cfe8d4; border: .5pt solid #7bb389; color: #1d5c2e; }}
.nd {{ background: #f6d8d8; border: .5pt solid #d18a8a; color: #8a2f2f; }}
.e  {{ background: #fde9c8; border: .5pt solid #d9a45b; color: #8a5a12; }}
.na {{ background: #e2e5ea; border: .5pt solid #b9c4d6; color: #4a5563; }}
code {{ font-family: "SF Mono", Menlo, monospace; font-size: {small}pt;
        background: #f1f4f8; padding: 0 2px; border-radius: 2px; }}
hr {{ border: none; border-top: .5pt solid #dde3ea; margin: 6px 0; }}
.foot {{ margin-top: 8px; font-size: {tiny}pt; color: #808c99;
         border-top: .5pt solid #dde3ea; padding-top: 4px; }}
pre {{ font-family: "SF Mono", Menlo, monospace; font-size: {small}pt;
       line-height: 1.15; background: #f4f6f9; border-left: 2px solid #c9d4e0;
       padding: 3px 7px; margin: 3px 0 4px; white-space: pre; overflow: hidden; }}
/* 语义着色行：表格整行按判级上色（速查表靠颜色扫读） */
tr.row-c  td {{ background: #cfe8d4; }}
tr.row-nd td {{ background: #f6d8d8; }}
tr.row-e  td {{ background: #fde9c8; }}
tr.row-na td {{ background: #e2e5ea; }}
"""

# 分栏：标题与页脚留在栏外，只有正文进栏
COLS_CSS = """
.cols {{ column-count: {n}; column-gap: {gap}mm; column-fill: balance; }}
.cols h2, .cols h3 {{ break-after: avoid; }}
/* 决策树等宽块不能跨栏：实测会被切成两截，右栏顶部出现孤立的 ``└ 否 → E`` */
.cols pre {{ break-inside: avoid; }}
/* 语义分栏：每页一组并排栏，整组不跨页。
   用 table 而不是 flex：实测 flex + break-inside 在 weasyprint 下会叠字。 */
table.pg {{ width: 100%; border-collapse: separate; border-spacing: {gap}mm 0;
            table-layout: fixed; break-inside: avoid; }}
table.pg td {{ vertical-align: top; padding: 0; border: none; }}
.cols tr {{ break-inside: avoid; }}
.cols table {{ width: 100%; }}
.brk {{ break-after: column; height: 0; margin: 0; padding: 0; }}
"""


def inline(text: str) -> str:
    t = htmllib.escape(text, quote=False)
    # 允许在 Markdown 里直接写 &lt; &gt; &amp; 这类实体（表格里写比较符常用）
    t = (t.replace("&amp;lt;", "&lt;").replace("&amp;gt;", "&gt;")
          .replace("&amp;amp;", "&amp;"))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`]+?)`", r"<code>\1</code>", t)
    for key, cls in (("C", "c"), ("NA", "na"), ("ND", "nd"), ("E", "e")):
        t = t.replace(f"[[{key}]]", f'<span class="chip {cls}">{key}</span>')
    return t


# GFM 分隔行：首尾竖线可省，因此不能锚定 \|。
# 形如 `|---|:--:|`、`---|---`、`| --- | --- |` 均合法。
SEP = re.compile(r"^\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?$")
# 表头行：以 | 开头（GFM 实际要求），或者下一行是分隔行时允许不带首竖线。
ROW_START = re.compile(r"^\|")
# 有序列表项：`1. ` / `2) `，但排除 `2025. ` 这类年份/编号开头（后面必须紧跟非数字）。
OL = re.compile(r"^\d{1,2}[.)]\s+")


def is_table_start(lines: list[str], i: int) -> bool:
    """第 i 行是表头且第 i+1 行是分隔行。

    容忍首竖线缺失（`a | b` + `--- | ---`），但**分隔行必须真的分了列**（含 `|`）。
    纯 `---` 是水平分隔线而不是表格：否则「含竖线的段落」+ 下一行 `---` 会被认成
    表格，整段文字变成一张只有表头、没有数据的空表格（`SEP` 正则对两者都匹配）。
    """
    if i + 1 >= len(lines):
        return False
    head, sep = lines[i].strip(), lines[i + 1].strip()
    if not sep or not SEP.match(sep):
        return False
    # 表头以 `|` 开头是最强信号；否则要求分隔行确实分了列
    return ROW_START.match(head) is not None or "|" in sep


def split_row(line: str) -> list[str]:
    """按 | 切单元格，但保留 \\| 转义（GFM 里表示单元格内的竖线）。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, buf, i = [], [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|")
            i += 2
        elif s[i] == "|":
            cells.append("".join(buf))
            buf, i = [], i + 1
        else:
            buf.append(s[i])
            i += 1
    cells.append("".join(buf))
    return [c.strip() for c in cells]


# 中文句末/分割标点：出现这些字符的行是自然语言句子，不是表格行
SENT_PUNCT = re.compile(r"[。！？；]")
# 表格单元格的合理长度上限（超过就当句子；SKILL.md 硬约束「每格 ≤ 30 字」）
CELL_MAX = 30


def is_table_row(line: str, ncol: int | None = None) -> bool:
    """这一行看起来是不是表格数据行。

    不能只看「含 |」：表格后紧跟的说明文字里常有 `A | B 二选一`、`|x|` 绝对值、
    并列词语等，一律吞进表格会把真实段落变成一行数据。

    判据按信号强度排序，**强信号必须先判**：

    1. 首尾带 `|` —— GFM 表格行的标准写法，无条件认定。
       它必须排在「句末标点 / 单格长度」之前：否则单元格里写一个句号
       （`| 张三 | 研发 | 已完成。 |`）整行就会被当成句子，表格从这里断掉，
       剩下的行带着竖线原文变成段落——打印出来是裸 Markdown。
    2. 带句末标点 → 是自然语言句子
    3. 单格超过 CELL_MAX 字 → 表格里不放长句
    4. 列数与表头一致 → 表格行
    5. 至少 2 个分隔竖线且每格都有内容

    2–5 只对「不带首尾竖线」的写法生效：那才是「可能是表格行、也可能是段落」
    的模糊地带。此情形请用空行分隔，见 SKILL.md。
    """
    s = line.strip()
    if not s:
        return False
    # 强信号优先：首尾竖线是 GFM 表格行的标准写法，直接认定，不再看标点/长度
    if s.startswith("|") or s.endswith("|"):
        return True
    # 以下只对不带首尾竖线的模糊写法生效
    if SENT_PUNCT.search(s):
        return False  # 带句末标点 → 是句子
    cells = split_row(s)
    if max((len(c) for c in cells), default=0) > CELL_MAX:
        return False  # 单格过长 → 是句子（表格里不该放长句）
    if ncol is not None and len(cells) == ncol:
        return True
    return len(cells) >= 2 and all(c.strip() for c in cells)


def parse_table(lines: list[str], i: int) -> tuple[str, int]:
    header = split_row(lines[i])
    ncol = len(header)
    i += 2
    rows = []
    while i < len(lines) and lines[i].strip() and is_table_row(lines[i], ncol):
        r = split_row(lines[i])
        if len(r) < ncol:  # 缺列补齐，否则 weasyprint 会渲染出残缺表格
            r += [""] * (ncol - len(r))
        elif len(r) > ncol:  # 多出来的并进末格，别丢数据
            r = r[: ncol - 1] + [" ".join(r[ncol - 1:])]
        rows.append(r)
        i += 1
    out = ["<table><thead><tr>"]
    out += [f"<th>{inline(h)}</th>" for h in header]
    out.append("</tr></thead><tbody>")
    for r in rows:
        # 整行着色：行里任一处出现判级标签，就把该行染成对应底色（速查表靠颜色扫读）
        joined = " ".join(r)
        cls = ""
        for key in ("C", "ND", "E", "NA"):
            if f"[[{key}]]" in joined:
                cls = f' class="row-{key.lower()}"'
                break
        out.append(f"<tr{cls}>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out), i


def to_html(md: str, given_title: str | None = None) -> tuple[str, str | None]:
    """返回 (正文HTML, 文档标题或None)。

    传了 given_title（命令行 --title）时，正文里的 `#` 全部降为 `##` 小节；
    其中与 given_title 完全相同的那个直接丢弃——否则页顶大标题和第一个小节
    会显示同一句话（SKILL.md 的调用模板正是这种写法）。
    没传时第一个 `#` 当文档标题，后续 `#` 降为 `##`，不会静默丢标题。
    """
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    doc_title: str | None = None
    i, n = 0, len(lines)

    while i < n:
        raw = lines[i]
        line = raw.strip()

        if not line:
            i += 1
            continue

        if line == "---":
            out.append("<hr>")
            i += 1
            continue

        if line.startswith("```"):
            # 围栏代码块：原样保留缩进与制表符，用于决策树/ASCII 图
            i += 1
            code = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i].rstrip())
                i += 1
            i += 1  # 跳过结尾围栏
            body_code = htmllib.escape("\n".join(code), quote=False)
            out.append(f"<pre>{body_code}</pre>")
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            lvl, text = len(m.group(1)), m.group(2)
            if lvl == 1:
                # 多个 `#` 不能只有一个能活：首个当文档标题（仅当 --title 未指定），
                # 其余的降为二级小节，否则静默丢标题。
                if doc_title is None and given_title is None:
                    doc_title = text
                elif given_title is not None and text.strip() == given_title.strip():
                    pass  # 与 --title 重复：丢掉，不要渲染成小节
                else:
                    out.append(f"<h2>{inline(text)}</h2>")
            elif lvl == 2:
                out.append(f"<h2>{inline(text)}</h2>")
            else:
                out.append(f"<h3>{inline(text)}</h3>")
            i += 1
            continue

        if is_table_start(lines, i):
            html_tbl, i = parse_table(lines, i)
            out.append(html_tbl)
            continue

        if line.startswith(">"):
            # 连续的 > 行按是否以 ! 开头切块：普通行合成一个灰底块，
            # 带 ! 的行单独成橙底警示块，两者相邻时不互相染色。
            blocks: list[tuple[bool, list[str]]] = []
            while i < n and lines[i].strip().startswith(">"):
                piece = lines[i].strip().lstrip(">").strip()
                i += 1
                if not piece:
                    continue
                warn = piece.startswith("!")
                if warn:
                    piece = piece.lstrip("!").strip()
                if blocks and blocks[-1][0] == warn:
                    blocks[-1][1].append(piece)
                else:
                    blocks.append((warn, [piece]))
            for warn, pieces in blocks:
                cls = "warn" if warn else "note"
                out.append(f'<div class="{cls}">{inline(" ".join(pieces))}</div>')
            continue

        if OL.match(line) or re.match(r"^[-*]\s+", line):
            ordered = bool(OL.match(line))
            tag = "ol" if ordered else "ul"
            items = []
            while i < n:
                ln = lines[i].strip()
                if OL.match(ln) or re.match(r"^[-*]\s+", ln):
                    items.append(re.sub(r"^(?:[-*]|\d+\.)\s+", "", ln))
                    i += 1
                elif ln and items and not re.match(r"^(#|\||>|---)", ln) \
                        and not is_table_start(lines, i):
                    # 只有缩进续行才归入上一项；顶格的独立段落不再被吞。
                    if lines[i].startswith((" ", "\t")):
                        items[-1] += " " + ln
                        i += 1
                    else:
                        break
                else:
                    break
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue

        # 段落：遇空行或任何一个块级起始就停（独立成段，不再跨行拼接）。
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r"^(#|\||>|---$)", lines[i].strip()) \
                and not OL.match(lines[i].strip()) \
                and not re.match(r"^[-*]\s+", lines[i].strip()) \
                and not is_table_start(lines, i):
            if lines[i].startswith((" ", "\t")):  # 缩进行视为上一段的续行
                buf.append(lines[i].strip())
                i += 1
            else:
                break
        out.append(f"<p>{inline(' '.join(buf))}</p>")

    return "\n".join(out), doc_title




# ---------------------------------------------------------------- 语义分栏（B）
# 目标：小节整块落在一栏内（对照样本的核心质量点），同时不浪费版面。
# 做法：按栏宽实测每节高度 → 顺序装栏 → 显式输出「每页一组并排栏」。
# 不用 CSS column-fill 均衡：实测它会把内容均分，栏缝会切在小节中间。

def split_sections(body_html: str) -> list:
    """按顶层 <h2> 切块。h3 属于所属小节，不单独成块。"""
    return [x for x in re.split(r"(?=<h2)", body_html) if x.strip()]


def _fragment_height(frag: str, width_pt: float, tier) -> float:
    """这一块在「栏宽」下渲染有多高（pt）。"""
    from weasyprint import HTML

    fs, lh, pad = tier
    css = CSS.format(
        font=FONT_STACK, fs=fs, lh=lh, pad=pad, margin="0",
        h1=round(fs * 1.66, 1), h2=round(fs * 1.13, 1), h3=round(fs * 1.04, 1),
        small=round(fs * 0.90, 1), tiny=round(fs * 0.84, 1),
    )
    page = f"@page {{ size: {width_pt}pt 4000pt; margin: 0 }}"
    html = (f'<html><head><meta charset="utf-8"><style>{page}{css}</style></head>'
            f"<body>{frag}</body></html>")
    doc = HTML(string=html).render()
    bottom = 0.0

    def walk(b):
        nonlocal bottom
        for c in getattr(b, "children", []) or []:
            bottom = max(bottom, c.position_y + c.height)
            walk(c)

    for pg in doc.pages:
        walk(pg._page_box)
    return bottom * 0.75  # CSS px -> pt


def pack_sections(body_html: str, tier, columns: int, col_h_pt: float) -> tuple:
    """把正文切成「每页一组并排栏」，小节不跨栏、两栏尽量等高。

    切分目标是「最小化最高那一栏」：贪心填满一栏会让长小节把另一栏留空，
    整组反而变高、甚至挤到下一页。对 2 栏就是找让 max(左,右) 最小的切点。

    返回 (按页分组的小节列表, 是否成功)。
    """
    sections = split_sections(body_html)
    if len(sections) <= 1:
        return None, False
    width = body_width_pt(columns)
    heights = [_fragment_height(sec, width, tier) for sec in sections]
    if max(heights) > col_h_pt * 0.97:
        return None, False        # 有整节超过一栏高度，交给自然分栏

    def best_split(items, hs):
        """在 items 里找一个切分，使最长一栏尽量矮；尽量一页装完。"""
        n = len(items)
        if n <= 1:
            return [items], [sum(hs)]
        best = None
        for k in range(1, n):
            left, right = hs[:k], hs[k:]
            if sum(right) > col_h_pt:       # 右侧放不下一页，跳过
                continue
            peak = max(sum(left), sum(right))
            if best is None or peak < best[0]:
                best = (peak, k)
        if best is None:
            return None, None
        k = best[1]
        return [items[:k], items[k:]], [sum(hs[:k]), sum(hs[k:])]

    pages, start = [], 0
    guard = 0
    while start < len(sections) and guard < 200:
        guard += 1
        # 先试「剩下的能一页装下」，装不下就二分压缩到能装下的最长前缀
        rest, rest_h = sections[start:], heights[start:]
        cols, col_hs = best_split(rest, rest_h) if sum(rest_h) <= col_h_pt * 2 else (None, None)
        if cols is None:
            # 从后往前找最长前缀能装下
            for end in range(len(rest), 0, -1):
                c, ch = best_split(rest[:end], rest_h[:end])
                if c is not None:
                    cols, col_hs = c, ch
                    break
            if cols is None:
                return None, False
        pages.append(cols)
        start += sum(len(c) for c in cols)
    return pages, True


def render_packed(pages: list, columns: int) -> str:
    """把分好页的小节渲染成并排栏 HTML。每页一组，整组不跨页。"""
    out = []
    for cols in pages:
        cells = "".join(f'<td>{"".join(secs)}</td>' for secs in cols)
        out.append(f'<table class="pg"><tr>{cells}</tr></table>')
    return "".join(out)


# 分栏时页面边距收紧：双栏版面密度高，留白压力小（对照样本实测值）
COLUMN_MARGIN = "7mm 7mm 4mm 7mm"
SINGLE_MARGIN = "10mm 11mm 10mm 11mm"
COLUMN_GAP_MM = 5.0
READABLE_FLOOR = 8.4   # 分栏时的字号下限（pt），低于此值优先保证页数再考虑


def block_stats(body_html: str) -> dict:
    """统计正文形态，用来判断该不该分栏。"""
    import re as _re

    paras = _re.findall(r"<p>(.*?)</p>", body_html, _re.S)
    items = _re.findall(r"<li>(.*?)</li>", body_html, _re.S)
    rows = len(_re.findall(r"<tr>", body_html))
    tables = _re.findall(r"<table>.*?</table>", body_html, _re.S)
    max_cols = 0
    for t in tables:
        max_cols = max(max_cols, t.count("<th>"))
    strip = lambda x: _re.sub(r"<[^>]+>", "", x)  # noqa: E731

    # 每行的字符数：条目列表与短段落都算「短行」
    item_lens = [len(strip(x)) for x in items]
    # 「行长」只统计列表条目：段落/提示块本来就该按栏宽自然折行，
    # 把它们算进来会把长句判据整体带偏（实测一份标准速查会被误判成"散文型"）。
    lines = item_lens or [len(strip(x)) for x in paras]
    long_lines = [n for n in lines if n > 40]
    lines.sort()
    p80 = lines[int(len(lines) * 0.8)] if lines else 0
    # 「一条占一行」只对列表条目有意义：段落/提示块本来就该按栏宽自然折行，
    # 把它们算进来会把目标行长抬到 50+ 字，逼出过小字号。
    il = sorted(item_lens)
    p50_item = il[int(len(il) * 0.5)] if il else 0
    p60_item = il[int(len(il) * 0.6)] if il else 0
    return {
        "p80_len": p80,
        "item_lens": item_lens,
        "p50_item": p50_item,
        "p60_item": p60_item,
        "items": len(items),
        "paras": len(paras),
        "rows": rows,
        "max_cols": max_cols,
        "lines": len(lines),
        "long_ratio": (len(long_lines) / len(lines)) if lines else 0.0,
        "avg_len": (sum(lines) / len(lines)) if lines else 0.0,
    }


def columns_disallowed(body_html: str) -> str | None:
    """明确不适合分栏的情形（快速否决，返回原因）。其余交给实测决定。

    这里只拦「结构上放不进栏」的情况，不猜哪种更好——
    猜不准：同样一份条目型速查，条目平均 42 字，按「短行才划算」的
    阈值会被判成该用单栏，但实测双栏 1 页 / 单栏 2 页。所以好坏一律实测。
    """
    st = block_stats(body_html)
    if st["max_cols"] >= 5:
        return f"有 {st['max_cols']} 列的宽表格，栏宽撑不下"
    if st["lines"] == 0:
        return "没有可判定的正文行"
    return None


def column_warnings(body_html: str, columns: int, auto: bool = False) -> list:
    """分栏可能踩的坑，渲染前就提示。auto=True 表示栏数是自动选的，措辞随之调整。"""
    st = block_stats(body_html)
    warn = []
    if st["max_cols"] > 4:
        warn.append(
            f"分栏下表格只有 {body_width_pt(columns):.0f}pt 宽，当前有 {st['max_cols']} 列，"
            "很可能撑爆或挤成多行；建议改成单栏，或把宽表拆成 ≤4 列"
        )
    if st["long_ratio"] > 0.35:
        if auto:
            warn.append(
                "内容是散文型（多数行长于 40 字）：双栏只是为了塞进目标页数，"
                "横向换行会更累；不急着压页就用 --columns 1"
            )
        else:
            warn.append(
                "超过 1/3 的行是 40 字以上长句，双栏省不到纸还会更难读；建议 --columns 1"
            )
    # 等宽块（决策树）超过栏宽会溢出到邻栏
    import re as _re
    for pre in _re.findall(r"<pre>(.*?)</pre>", body_html, _re.S):
        widest = max((len(l) for l in _re.sub(r"<[^>]+>", "", pre).splitlines()), default=0)
        if widest * 0.6 * 8 > body_width_pt(columns):
            warn.append(f"等宽块有 {widest} 字符宽的一行，可能溢出栏宽；建议压到 30 字符内")
            break
    return warn


def chars_per_line(width_pt: float, fs: float) -> float:
    """一行大约能放几个中文字（字符宽 ≈ 字号）。"""
    return width_pt / fs


def body_width_pt(columns: int) -> float:
    """正文可用宽度（pt）。分栏时每栏宽度 = (总宽 - 栏间距)/栏数。"""
    import re as _re
    margin = COLUMN_MARGIN if columns > 1 else SINGLE_MARGIN
    side = float(_re.findall(r"([\d.]+)mm", margin)[1])  # 上右下左 → 取右
    total = A4_W_PT - 2 * side * 72 / 25.4
    if columns <= 1:
        return total
    gap = COLUMN_GAP_MM * 72 / 25.4
    return (total - gap * (columns - 1)) / columns


def build_page(body: str, title: str, subtitle: str, footer: str, tier,
               columns: int = 1, span_headings: bool = False,
               prepacked: bool = False) -> str:
    fs, lh, pad = tier
    margin = COLUMN_MARGIN if columns > 1 else SINGLE_MARGIN
    css = CSS.format(
        font=FONT_STACK, fs=fs, lh=lh, pad=pad, margin=margin,
        h1=round(fs * 1.66, 1), h2=round(fs * 1.13, 1), h3=round(fs * 1.04, 1),
        small=round(fs * 0.90, 1), tiny=round(fs * 0.84, 1),
    )
    if columns > 1:
        css += COLS_CSS.format(n=columns, gap=COLUMN_GAP_MM)
        if span_headings:
            # 小节标题通栏：保证小节不从栏目中间开始，代价是每节末尾留白变多
            css += ".cols h2 { column-span: all; }\n"
    # subtitle / footer 直接来自命令行参数（常含 `< > &` 等文件名/口径符号），
    # 必须转义；否则会被当成 HTML 标签吃掉后面的文字、甚至注入样式破版。
    sub = f'<div class="sub">{inline(subtitle)}</div>' if subtitle else ""
    foot = f'<div class="foot">{inline(footer)}</div>' if footer else ""
    # 标题与页脚留在栏外，保持全页宽（对照样本就是这么做的）。
    # prepacked=True 时 body 自己就是「并排栏表格」，不能再套一层 .cols，
    # 否则表格会被 CSS 多栏二次切分，页数虚增。
    inner = f'<div class="cols">{body}</div>' if (columns > 1 and not prepacked) else body
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{htmllib.escape(title)}</title><style>{css}</style></head><body>"
        f"<h1>{inline(title)}</h1>{sub}{inner}{foot}</body></html>"
    )


def count_pages(html_text: str) -> int:
    from weasyprint import HTML

    return len(HTML(string=html_text).render().pages)


def main() -> int:
    ap = argparse.ArgumentParser(description="Markdown -> A4 HTML（自动字号）")
    ap.add_argument("md", help="摘要 Markdown")
    ap.add_argument("-o", "--out", required=True, help="输出 HTML")
    ap.add_argument("--target-pages", type=int, required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--footer", default="")
    ap.add_argument("--columns", type=int, default=1, choices=(1, 2, 3),
                    help="正文分栏数。条目型内容用 2，散文/宽表格用 1（默认 1）")
    ap.add_argument("--span-headings", dest="span_headings", action="store_true",
                    default=None,
                    help="分栏时让 ## 小节标题通栏，保证小节不被栏缝切断（分栏时默认开启）")
    ap.add_argument("--no-span-headings", dest="span_headings", action="store_false",
                    help="关闭通栏标题：版面更紧，但小节可能被栏缝切开")
    ap.add_argument("--pack-sections", action="store_true",
                    help="语义分栏：按实测高度把小节整块装进各栏（小节绝不跨栏，且尽量省页）")
    ap.add_argument("--auto-columns", action="store_true",
                    help="按内容形态自动判断是否分栏（条目占比高才开 2 栏）")
    args = ap.parse_args()
    if args.target_pages < 1:
        ap.error("--target-pages 必须 ≥ 1")

    a4env.prepare()
    warn = []
    # 缺中文字体时 weasyprint 静默渲染成豆腐块，verify 也查不出来（转曲后只剩路径）。
    # 在渲染阶段就挑明，否则会交出一份满纸方块却"全部检查通过"的 PDF。
    if a4env.missing_cjk_font():
        warn.append(
            "系统缺少中文字体（fc-list :lang=zh 为空），PDF 里的中文会显示成豆腐块；"
            "Linux 请装 fonts-noto-cjk"
        )
    md = Path(args.md).read_text(encoding="utf-8")
    body, doc_title = to_html(md, given_title=args.title)
    title = args.title or doc_title or "摘要"
    # 标题没被正文提供也没被指定时提示，避免交出「摘要 / 摘要」这种空壳标题
    if not (args.title or doc_title):
        warn.append("outline 里没有 `#` 且未传 --title，标题回落为「摘要」")

    def solve(cols: int) -> dict:
        """给定栏数，跑完选档与语义分栏，返回完整方案。"""
        w = []
        st = block_stats(body)
        width = body_width_pt(cols)
        usable = width - 12      # 列表缩进
        span = args.span_headings if args.span_headings is not None else cols > 1
        if args.pack_sections or cols > 1:
            span = False if args.pack_sections else span

        def wrapped_extra(fs: float) -> int:
            """该字号下条目因折行多出来的行数（段落自然折行不算）。"""
            if cols <= 1:
                return 0
            cpl = chars_per_line(usable, fs)
            return (sum(max(0, -(-n // int(cpl)) - 1) for n in st["item_lens"])
                    if cpl >= 1 else 0)

        trail, scored = [], []
        for tier in TIERS:
            n = count_pages(build_page(body, title, args.subtitle, args.footer, tier,
                                       columns=cols, span_headings=span))
            waste = wrapped_extra(tier[0])
            trail.append({"font_pt": tier[0], "pages": n, "wrap_extra": waste,
                          "chars_per_line": round(chars_per_line(usable, tier[0]), 1)})
            if n <= args.target_pages:
                scored.append((n, waste, -tier[0], tier))

        if scored:
            floor = READABLE_FLOOR if cols > 1 else 0.0
            scored.sort(key=lambda x: (x[0], 0 if x[3][0] >= floor else 1, x[1], x[2]))
            n_best, _, _, tier = scored[0]
        else:
            b = min(trail, key=lambda t: (t["pages"], t["wrap_extra"], -t["font_pt"]))
            tier = next(t for t in TIERS if t[0] == b["font_pt"])
            n_best = b["pages"]

        if cols > 1 and tier[0] >= 10.0:
            w.append(
                f"分栏下正文用了 {tier[0]:.1f}pt，每行只放得下 "
                f"{chars_per_line(usable, tier[0]):.0f} 字，条目会折行；"
                "想要更紧凑可加大 --target-pages 让字号降下来"
            )

        body_out, packed_pages = body, 0
        if cols > 1 and args.pack_sections:
            # 语义分栏：栏高必须扣掉「页眉+页脚」实测高度，
            # 估高了会把整组挤到下一页、首页只剩标题。
            page_h = A4_H_PT - (7 + 4) * 72 / 25.4
            head = (f'<h1>{inline(title)}</h1>'
                    + (f'<div class="sub">{inline(args.subtitle)}</div>'
                       if args.subtitle else ""))
            foot = (f'<div class="foot">{inline(args.footer)}</div>'
                    if args.footer else "")
            full = body_width_pt(1)
            h_head = _fragment_height(head, full, tier) if head else 0.0
            h_foot = _fragment_height(foot, full, tier) if foot else 0.0
            col_h = page_h - h_head - h_foot - 8
            groups, ok = pack_sections(body, tier, cols, col_h)
            if ok:
                cand = render_packed(groups, cols)
                n_cand = count_pages(build_page(cand, title, args.subtitle, args.footer,
                                                tier, columns=cols, prepacked=True))
                if n_cand <= n_best:
                    body_out, n_best, packed_pages = cand, n_cand, len(groups)

        return {"columns": cols, "tier": tier, "pages": n_best, "body": body_out,
                "packed": packed_pages, "trail": trail, "warn": w, "span": span,
                "overflow": n_best > args.target_pages}

    # --auto-columns：不靠启发式猜，直接把两种栏数都算一遍，按结果选。
    # 猜不准的实例：条目平均 42 字的一份速查，按「短行才划算」会判成单栏，
    # 但实测双栏 1 页 / 单栏 2 页。
    blocked = columns_disallowed(body) if args.auto_columns else None
    if args.auto_columns:
        if blocked:
            plan = solve(1)
            warn.append(f"自动分栏：{blocked}，按单栏排版")
            for c in (2, 3):
                warn += column_warnings(body, c)
        else:
            one, two = solve(1), solve(2)
            # 单栏是默认阅读体验，双栏必须赢得明显才换：
            #   页数更少，或 页数相同但字号至少大 1.5pt（跨两档）。
            # 否则会出现「散文在双栏里字号只大 0.7pt 就改成双栏」这种亏本买卖。
            if two["pages"] < one["pages"]:
                plan = two
            elif two["pages"] == one["pages"] and two["tier"][0] >= one["tier"][0] + 1.5:
                plan = two
            else:
                plan = one
            plan["trail"] = one["trail"]
            if plan["columns"] > 1:
                warn += column_warnings(body, plan["columns"], auto=True)
    else:
        plan = solve(args.columns)
        if args.columns > 1:
            warn += column_warnings(body, args.columns)

    columns, chosen = plan["columns"], plan["tier"]
    pages, body, packed_note = plan["pages"], plan["body"], plan["packed"]
    # span 必须沿用 solve() 内部实际使用的那一个：重算会与测量条件不一致，
    # 出现「render 说 1 页、实际 PDF 2 页」这种静默错误。
    span = plan["span"]
    warn += plan["warn"]

    html_text = build_page(body, title, args.subtitle, args.footer, chosen,
                           columns=columns, span_headings=span,
                           prepacked=bool(packed_note))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "html": str(out),
        "title": title,
        "columns": columns,
        "span_headings": span,
        "packed_pages": packed_note,
        "font_pt": chosen[0],
        "pages": pages,
        "target": args.target_pages,
        "overflow": pages > args.target_pages,
        "warnings": warn,
        "trail": plan["trail"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
