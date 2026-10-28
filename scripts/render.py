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

FONT_STACK = ('"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", '
              '"Noto Sans CJK SC", "Source Han Sans SC", sans-serif')

CSS = """
@page {{ size: A4; margin: 10mm 11mm 10mm 11mm; }}
* {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ margin: 0; font-family: {font};
        font-size: {fs}pt; line-height: {lh}; color: #1a2330; }}
h1 {{ font-size: {h1}pt; margin: 0 0 3px; color: #14293f; letter-spacing: .2px; }}
.sub {{ font-size: {small}pt; color: #6b7885; margin: 0 0 8px; }}
.sub b {{ color: #14293f; }}
h2 {{ font-size: {h2}pt; margin: 10px 0 4px; padding: 3px 0 3px 7px;
      border-left: 3px solid #1f4e79; background: #f2f5f9; color: #14293f;
      break-after: avoid; }}
h3 {{ font-size: {h3}pt; margin: 7px 0 3px; color: #1f4e79; break-after: avoid; }}
p {{ margin: 0 0 4px; }}
ul, ol {{ margin: 2px 0 4px; padding-left: 16px; }}
li {{ margin: 0 0 1.5px; }}
table {{ width: 100%; border-collapse: collapse; margin: 0 0 4px; }}
tr {{ break-inside: avoid; }}
thead {{ display: table-header-group; }}
th, td {{ border: 0.5pt solid #d4dbe4; padding: {pad}px 5px;
          vertical-align: top; text-align: left; }}
th {{ background: #e9eef5; font-weight: 600; color: #14293f; font-size: {small}pt; }}
.note {{ font-size: {small}pt; color: #5c6a78; background: #fafbfd;
         border-left: 2px solid #c9d4e0; padding: 4px 8px; margin: 4px 0 3px; }}
.warn {{ font-size: {small}pt; color: #8a3d14; background: #fff7f2;
         border-left: 2px solid #e0a071; padding: 4px 8px; margin: 4px 0 3px; }}
.chip {{ border-radius: 3px; padding: 0 4px; font-weight: 700;
         font-size: {small}pt; white-space: nowrap; }}
.c  {{ background: #fff3d1; border: .5pt solid #e0b84d; color: #8a5c00; }}
.na {{ background: #e6f0fb; border: .5pt solid #8db4dd; color: #17527f; }}
.nd {{ background: #fdeade; border: .5pt solid #dda57e; color: #96431a; }}
code {{ font-family: "SF Mono", Menlo, monospace; font-size: {small}pt;
        background: #f1f4f8; padding: 0 2px; border-radius: 2px; }}
hr {{ border: none; border-top: .5pt solid #dde3ea; margin: 6px 0; }}
.foot {{ margin-top: 8px; font-size: {tiny}pt; color: #808c99;
         border-top: .5pt solid #dde3ea; padding-top: 4px; }}
"""


def inline(text: str) -> str:
    t = htmllib.escape(text, quote=False)
    # 允许在 Markdown 里直接写 &lt; &gt; &amp; 这类实体（表格里写比较符常用）
    t = (t.replace("&amp;lt;", "&lt;").replace("&amp;gt;", "&gt;")
          .replace("&amp;amp;", "&amp;"))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`]+?)`", r"<code>\1</code>", t)
    for key, cls in (("C", "c"), ("NA", "na"), ("ND", "nd")):
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
        out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
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


def build_page(body: str, title: str, subtitle: str, footer: str, tier) -> str:
    fs, lh, pad = tier
    css = CSS.format(
        font=FONT_STACK, fs=fs, lh=lh, pad=pad,
        h1=round(fs * 1.66, 1), h2=round(fs * 1.13, 1), h3=round(fs * 1.04, 1),
        small=round(fs * 0.90, 1), tiny=round(fs * 0.84, 1),
    )
    # subtitle / footer 直接来自命令行参数（常含 `< > &` 等文件名/口径符号），
    # 必须转义；否则会被当成 HTML 标签吃掉后面的文字、甚至注入样式破版。
    sub = f'<div class="sub">{inline(subtitle)}</div>' if subtitle else ""
    foot = f'<div class="foot">{inline(footer)}</div>' if footer else ""
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{htmllib.escape(title)}</title><style>{css}</style></head><body>"
        f"<h1>{inline(title)}</h1>{sub}{body}{foot}</body></html>"
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

    # target_pages 是上限，字号是手段：从最大档往下试，取第一个塞得进 target 的档，
    # 即「不超过 N 页」前提下用尽可能大的字号。全档都塞不进时才退到页数最少的档。
    trail = []
    chosen, pages = None, None
    for tier in TIERS:
        n = count_pages(build_page(body, title, args.subtitle, args.footer, tier))
        trail.append({"font_pt": tier[0], "pages": n})
        if n <= args.target_pages:
            chosen, pages = tier, n
            break
    if chosen is None:
        best = min(trail, key=lambda t: (t["pages"], -t["font_pt"]))
        chosen = next(t for t in TIERS if t[0] == best["font_pt"])
        pages = best["pages"]

    html_text = build_page(body, title, args.subtitle, args.footer, chosen)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "html": str(out),
        "title": title,
        "font_pt": chosen[0],
        "pages": pages,
        "target": args.target_pages,
        "overflow": pages > args.target_pages,
        "warnings": warn,
        "trail": trail,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
