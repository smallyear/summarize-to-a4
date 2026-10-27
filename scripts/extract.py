#!/usr/bin/env python3
"""任意格式输入 -> Markdown 中间态。

支持: .pptx/.ppt  .docx  .pdf  .xlsx/.xlsm  .csv/.tsv  .html/.htm  http(s)://  .md/.txt
输出 Markdown 到 -o，并向 stdout 打印一行 JSON 统计信息。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MAX_CHARS_DEFAULT = 200_000
CHARS_PER_PAGE = 1100  # 中文正文约 1100 字/页（1800 会让中文文本的源页数系统性偏低）


def est_pages(chars: int) -> int:
    return max(1, round(chars / CHARS_PER_PAGE + 0.49))


def _is_common_hanzi(ch: str) -> bool:
    """是否 GB2312 一级汉字（3755 个常用字，0xB0A1–0xD7F9）。

    这是区分「真中文」与「UTF-8 被当 GBK 解出的乱码」的关键：
    乱码是字节错位产生的生僻字（伩欐槸姝ｅ父涓枃…），几乎不在常用字区；
    真实中文文本的常用字命中率实测为 1.00，而乱码最高只到 0.16。
    """
    try:
        b = ch.encode("gb2312")
        return len(b) == 2 and 0xB0 <= b[0] <= 0xD7
    except (UnicodeEncodeError, LookupError):
        return False


def gb_readable(text: str) -> bool:
    """gb18030 解出来的结果是不是可读中文。

    gb18030 几乎能解任意字节序列，**strict 成功不代表解对了**：UTF-16 等非 GBK
    的字节流会 strict 通过，但解出一串夹杂控制符的生僻汉字。必须验一下再采信，
    否则会输出满纸乱码还报「已按 GB18030 解码」，看起来像是成功的。
    """
    if not text:
        return False
    n = len(text)
    bad = text.count("\ufffd")
    # 短文本里 1 个坏字就超过 5%，但不代表整体不可读（GBK 末尾被截掉半个
    # 汉字正是这种情形）。所以先看绝对个数，超过 2 个再按比例判。
    if bad > 2 and bad / n >= 0.05:
        return False
    if bad / n >= 0.25:
        return False
    cjk = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    if not cjk:
        return False
    ctrl = sum(1 for ch in text if (ord(ch) < 0x20 and ch not in "\r\n\t") or ch == "\x7f")
    if ctrl / n >= 0.02:
        return False
    # 常用字命中率：真中文接近 1.0，UTF-8 被误当 GBK 的乱码 < 0.2
    common = sum(1 for c in cjk if _is_common_hanzi(c)) / len(cjk)
    return common >= 0.35


def read_text(path: Path):
    """读文本，返回 (文本, 警告列表)。

    三级判据：
    1. UTF-8 严格解通 → 就是 UTF-8（带 U+FFFD 字符的话原样保留并提示）。
    2. 解不通时先看「UTF-8 容错解码的替换符占比」：占比低说明本质是 UTF-8，
       只是夹了少数坏字节 —— 保留 UTF-8 结果，让个别字符变 U+FFFD。
    3. 占比高才回退 GB18030（Windows 导出的 GBK 文件）。

    为什么不能只看 strict：gb18030 几乎能解任意字节序列，UTF-8 文件坏一两个
    字节就会 strict 失败，此时直接回退 gb18030 会把整篇中文解成乱码，
    却仍报「已按 GB18030 解码」，看起来是成功的。

    阈值为实测所得：真 GBK 文本的替换符占比 0.63–0.75，
    损坏的 UTF-8 只有 0.02–0.06，差值 10 倍以上。len < 10 时样本太短、
    占比不稳，直接信任 gb18030。
    """
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace"), []

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    else:
        bad = text.count("\ufffd")
        if bad:
            return text, [f"原文含 {bad} 个无法显示的字符（U+FFFD），已原样保留"]
        return text, []

    # UTF-8 strict 失败：先判断是「UTF-8 轻微损坏」还是「真 GBK」
    salvage = raw.decode("utf-8-sig", "replace")
    bad = salvage.count("\ufffd")
    if len(salvage) >= 10 and bad / len(salvage) < 0.25:
        return salvage, [
            f"文件不是合法 UTF-8（{bad} 处坏字节），已按 UTF-8 容错解码，"
            "个别字符会显示为乱码；如源文件实际是 GBK，请另存为 UTF-8 后重试"
        ]

    try:
        gb = raw.decode("gb18030")
    except UnicodeDecodeError:
        # GBK 文件末尾被截掉半个汉字时 strict 会失败，用 replace 再解一次
        gb = raw.decode("gb18030", "replace")
    if gb_readable(gb):
        gb_bad = gb.count("\ufffd")
        tail = f"；末尾 {gb_bad} 处字节不完整，末字符可能缺失" if gb_bad else ""
        return gb, [f"文件不是 UTF-8，已按 GB18030(GBK) 解码{tail}"]

    return salvage, ["编码无法识别，已按 UTF-8 容错解码，部分字符可能乱码"]


def md_table(rows: list[list[str]], header: list[str] | None = None) -> str:
    if not rows and not header:
        return ""
    width = max(len(r) for r in ([header] if header else []) + rows)
    def pad(r):
        r = [str(c or "").replace("|", "\\|").replace("\n", " ").strip() for c in r]
        return r + [""] * (width - len(r))
    out = []
    if header:
        out.append("| " + " | ".join(pad(header)) + " |")
        out.append("|" + "|".join(["---"] * width) + "|")
    for r in rows:
        out.append("| " + " | ".join(pad(r)) + " |")
    return "\n".join(out)


def clean(text: str) -> str:
    text = text.replace("\u000b", " ").replace("\uf0b7", "- ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def docx_page_count(path: Path) -> int | None:
    """读 docProps/app.xml 里 Word 最后一次保存的页数；没有就返回 None。"""
    try:
        with zipfile.ZipFile(str(path)) as z:
            xml = z.read("docProps/app.xml").decode("utf-8", "ignore")
        m = re.search(r"<Pages>(\d+)</Pages>", xml)
        if m and int(m.group(1)) > 0:
            return int(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def inject_pptx_notes(raw: str, notes: dict[int, str]) -> str:
    """把 python-pptx 抽到的备注插回 slidep 输出对应页的段末。"""
    lines = raw.split("\n")
    starts = []
    for ln, line in enumerate(lines):
        m = re.match(r"^##\s*第\s*(\d+)\s*页", line)
        if m:
            starts.append((int(m.group(1)), ln))
    for page, ln in reversed(starts):
        if page not in notes:
            continue
        end = next((l for _, l in starts if l > ln), len(lines))
        ins = end
        while ins > ln + 1 and not lines[ins - 1].strip():
            ins -= 1
        lines.insert(ins, f"\n> {note_line(notes[page])}")
    return "\n".join(lines)


def note_line(text: str) -> str:
    """备注统一前缀，但不叠成「备注：备注：」。"""
    t = clean(text)
    t = re.sub(r"^备注\s*[:：]\s*", "", t)
    return f"备注：{t}"


# ---------------------------------------------------------------- readers
def from_pptx(path: Path):
    """优先用 slidep（结构化、带表格与坐标），失败回退 python-pptx。"""
    warnings = []
    out_dir = tempfile.mkdtemp(prefix="slidep-")
    try:
        r = subprocess.run(
            ["slidep", "inspect", str(path), "--out", out_dir],
            capture_output=True, text=True, timeout=180,
        )
        md_file = Path(out_dir) / "slides.md"
        if r.returncode == 0 and md_file.exists():
            raw = md_file.read_text(encoding="utf-8")
            n = len(re.findall(r"^## 第 (\d+) 页", raw, flags=re.M))
            # slidep 不输出备注页，用 python-pptx 单独补齐
            try:
                from pptx import Presentation

                prs = Presentation(str(path))
                notes = {}
                for i, slide in enumerate(prs.slides, 1):
                    if slide.has_notes_slide:
                        t = clean(slide.notes_slide.notes_text_frame.text)
                        if t:
                            notes[i] = t
                if notes:
                    raw = inject_pptx_notes(raw, notes)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"slidep 成功但备注页抽取失败，本次结果不含备注: {exc}")
            return raw, n, warnings
        warnings.append(f"slidep 失败，回退 python-pptx: {r.stderr.strip()[:200]}")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"slidep 不可用，回退 python-pptx: {exc}")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    from pptx import Presentation

    prs = Presentation(str(path))
    buf = []
    for i, slide in enumerate(prs.slides, 1):
        buf.append(f"\n## 第 {i} 页\n")
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                for para in shape.text_frame.paragraphs:
                    t = clean("".join(r.text for r in para.runs))
                    if t:
                        buf.append(f"- {t}")
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [[c.text for c in row.cells] for row in shape.table.rows]
                if rows:
                    buf.append("")
                    buf.append(md_table(rows[1:], rows[0]))
        if slide.has_notes_slide:
            note = clean(slide.notes_slide.notes_text_frame.text)
            if note:
                buf.append(f"\n> {note_line(note)}")
    return "\n".join(buf), len(prs.slides), warnings


def heading_level(p) -> int | None:
    """识别标题层级：英文 Heading N / 中文「标题 N」/ Title，再退到 outlineLvl。"""
    style = (p.style.name or "").strip()
    m = re.match(r"(?:heading|标题)\s*(\d)", style, re.I)
    if m:
        return int(m.group(1))
    if re.match(r"^(title|标题)$", style, re.I):
        return 1
    if re.match(r"^(subtitle|副标题)$", style, re.I):
        return 3
    try:
        ol = p.paragraph_format.outline_level
        if ol is not None and int(ol) < 9:  # 9 = body text，不是标题
            return int(ol) + 1
    except Exception:  # noqa: BLE001
        pass
    return None


def from_docx(path: Path):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    doc = Document(str(path))
    buf: list[str] = []

    # w:sdt / w:txbxContent 是容器：公文与合同模板常把整篇正文包在内容控件里，
    # 只遍历 body 直接子元素会把里面的段落和表格整块静默丢掉。这里下钻。
    CONTAINERS = (qn("w:sdt"), qn("w:sdtContent"), qn("w:txbxContent"))

    def walk(el):
        for child in el.iterchildren():
            if child.tag == qn("w:p"):
                p = Paragraph(child, doc)
                txt = clean(p.text)
                if not txt:
                    continue
                lvl = heading_level(p)
                if lvl:
                    buf.append(f"\n{'#' * min(lvl + 1, 6)} {txt}")
                elif "list" in (p.style.name or "").lower():
                    buf.append(f"- {txt}")
                else:
                    buf.append(txt)
            elif child.tag == qn("w:tbl"):
                t = Table(child, doc)
                rows = [[clean(c.text) for c in r.cells] for r in t.rows]
                if rows:
                    buf.append("")
                    buf.append(md_table(rows[1:], rows[0]))
                    buf.append("")
            elif child.tag in CONTAINERS:
                walk(child)

    walk(doc.element.body)
    return "\n".join(buf), docx_page_count(path), []


def pdf_annotations(path: Path) -> dict[int, list[str]]:
    """读 PDF 的 /Annots（批注/高亮/便签），按页号分组。

    pypdfium2 的 get_text_bounded() 不返回注释，审阅批注里的
    「这里数据要复核」这类信息会静默丢失，所以单独用 pypdf 补。
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
    except Exception:  # noqa: BLE001
        return {}
    out: dict[int, list[str]] = {}
    for page_no, page in enumerate(reader.pages, 1):
        annots = page.get("/Annots")
        if not annots:
            continue
        texts = []
        for ref in annots:
            try:
                obj = ref.get_object()
                contents = obj.get("/Contents")
                if contents:
                    t = clean(str(contents))
                    if t and t not in texts:
                        texts.append(t)
            except Exception:  # noqa: BLE001
                continue
        if texts:
            out[page_no] = texts
    return out


def from_pdf(path: Path):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    buf, warnings = [], []
    annots = pdf_annotations(path)
    empty = 0
    for i in range(len(pdf)):
        txt = pdf[i].get_textpage().get_text_bounded()
        txt = "\n".join(clean(l) for l in (txt or "").splitlines() if clean(l))
        if not txt:
            empty += 1
        page_no = i + 1
        buf.append(f"\n## 第 {page_no} 页\n\n{txt}")
        # 批注常是审阅意见（「此处数据待核」），属于 A 类信息，必须带给模型
        for t in annots.get(page_no, []):
            buf.append(f"\n> 批注：{t}")
    if empty:
        warnings.append(
            f"{empty}/{len(pdf)} 页无可抽取文字，可能是扫描件，需要 OCR 或多模态读图"
        )
    if annots:
        n_annot = sum(len(v) for v in annots.values())
        warnings.append(f"已抽取 {n_annot} 条 PDF 批注（以「> 批注：」标记，属 A 类信息）")
    return "\n".join(buf), len(pdf), warnings


def looks_like_header(grid: list[list[str]]) -> bool:
    """推测首行是不是表头。

    只看首行会把一条真实记录变成列名（3 行数据只剩 2 行，静默丢数据）。
    但要小心反向误判：纯文字表（如「姓名/部门/状态」全无数字）不能被当成
    无表头，否则表头会变成一行空单元格，同样难看。

    判定顺序：
    1. 首行有空单元格 → 不像表头（表头很少留空）
    2. 首行含纯数字格 → 是数据行
    3. 首行全是短文字（每格 ≤ 12 字）→ 算表头
    4. 否则：首行是文字而下面有数字 → 表头；再否则当无表头
    """
    if len(grid) < 2:
        return True  # 只有一行，当表头不会丢任何数据行
    head = [c.strip() for c in grid[0]]
    if not all(head):
        return False  # 首行有空单元格 —— 更像数据
    if any(re.fullmatch(r"[-+]?\d+(\.\d+)?%?", c) for c in head):
        return False  # 首行含纯数字 —— 是数据行
    # 全是短文字：典型表头（即便整表都没有数字）
    if all(len(c) <= 12 for c in head) and all(re.search(r"[^\W\d_]", c) for c in head):
        return True
    body = grid[1:]
    return any(re.search(r"\d", cell) for row in body for cell in row)


def from_xlsx(path: Path, max_rows: int = 200):
    import openpyxl

    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    buf, warnings = [], []
    for ws in wb.worksheets:
        grid = []
        for row in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v).strip() for v in row]
            if any(vals):
                grid.append(vals)
        if not grid:
            continue
        buf.append(f"\n## 工作表：{ws.title}\n")
        if looks_like_header(grid):
            header, body = grid[0], grid[1:]
        else:
            header, body = [""] * len(grid[0]), grid
            warnings.append(f"工作表 {ws.title} 首行像数据而非表头，已整表保留（空表头）")
        if len(body) > max_rows:
            warnings.append(f"工作表 {ws.title} 共 {len(body)} 行，仅取前 {max_rows} 行")
            body = body[:max_rows]
        buf.append(md_table(body, header))
    wb.close()
    return "\n".join(buf), None, warnings


def from_csv(path: Path):
    raw, warnings = read_text(path)
    if path.suffix.lower() in (".tsv", ".tab"):
        delim = "\t"  # 后缀已声明分隔符，不要让 sniff 改判
    else:
        delim = ","
        try:
            delim = csv.Sniffer().sniff(raw[:4096], delimiters=",\t;").delimiter
        except Exception:  # noqa: BLE001
            pass
    rows = [r for r in csv.reader(io.StringIO(raw), delimiter=delim) if any(c.strip() for c in r)]
    if not rows:
        return "", None, warnings + ["CSV 为空"]
    return md_table(rows[1:], rows[0]), None, warnings


BLOCKS = ["h1", "h2", "h3", "h4", "p", "li", "table", "blockquote", "pre"]


def _own_text(el) -> str:
    """只取元素的直接文本（不包含内部块级子元素的文字），避免父子重复。"""
    parts = []
    for node in el.children:
        if getattr(node, "name", None) in BLOCKS:
            continue
        if getattr(node, "get_text", None):
            parts.append(node.get_text(" "))
        else:
            parts.append(str(node))
    return clean(" ".join(parts))


def from_html(src: str):
    import requests
    from bs4 import BeautifulSoup
    from bs4.element import Tag

    warnings: list[str] = []
    if re.match(r"^https?://", src):
        r = requests.get(src, timeout=30, headers={"User-Agent": "Mozilla/5.0 (a4-summary)"})
        r.raise_for_status()
        r.encoding = r.apparent_encoding or r.encoding
        html = r.text
        title_hint = r.url
    else:
        html, warnings = read_text(Path(src))
        title_hint = Path(src).name

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"]):
        tag.decompose()

    page_title = clean(soup.title.get_text()) if soup.title else ""
    root = soup.find("article") or soup.find("main") or soup.body or soup
    if not page_title:
        # 无 <title> 时用首个 h1 当文档标题（否则会退成文件名，标题只能靠 ### 小节补）
        h1 = root.find("h1")
        page_title = clean(h1.get_text(" ")) if h1 else ""

    lines: list[str] = []
    seen_title = False

    def emit(text: str, kind: str) -> None:
        nonlocal seen_title
        if not text:
            return
        if kind == "h1":
            if not seen_title and text == page_title:
                seen_title = True  # 正文首个 h1 与 <title> 重复，跳过
                return
            lines.append(f"\n## {text}")  # 文档大标题已用掉 `#`，h1 降为小节
        elif kind == "h2":
            lines.append(f"\n### {text}")
        elif kind in ("h3", "h4"):
            lines.append(f"\n#### {text}")
        elif kind == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)

    def walk(el, in_block: bool = False) -> None:
        for node in el.children:
            if not isinstance(node, Tag):
                # 裸文本：只在不在块内时补位（块内的行内文字已由 _own_text 收进）
                t = clean(str(node))
                if t and not in_block:
                    emit(t, "p")
                continue
            name = node.name
            if name in BLOCKS:
                if name == "table":
                    if node.find_parent("table"):  # 嵌套表格交给外层处理
                        continue
                    rows, is_head = [], []
                    for tr in node.find_all("tr"):
                        cells = tr.find_all(["td", "th"], recursive=False)
                        if cells:
                            rows.append([clean(c.get_text(" ")) for c in cells])
                            is_head.append(any(c.name == "th" for c in cells))
                    if rows:
                        # 只有首行确实是 th 才当表头。否则整表都是数据，拿首行当
                        # 表头会把一条真实记录变成列名；这里给一个等宽空表头，
                        # 保住 GFM 结构的同时一行数据都不丢。
                        if is_head[0]:
                            lines.extend(["", md_table(rows[1:], rows[0])])
                        else:
                            lines.extend(["", md_table(rows, [""] * len(rows[0]))])
                    continue
                # 先输出自身的直接文本（不含内部块），再递归内部块：
                # li>p / blockquote>p 只输出一次。
                emit(_own_text(node), name)
                walk(node, in_block=True)
            elif node.find(BLOCKS):
                walk(node, in_block)  # 纯容器 div/section：继续下钻，不输出自身
            elif not in_block:
                emit(clean(node.get_text(" ")), "p")  # 顶层行内元素

    walk(root)
    body = "\n".join(lines)
    # 兜底只看「实质内容量」：按行数判断会把只有一段、一张表的小文档误判为空，
    # 反而用全文本重写一遍（标题、表格结构都会丢）。
    if len(re.sub(r"\s", "", body)) < 20:
        body = "\n".join(clean(l) for l in root.get_text("\n").splitlines() if clean(l))
    title = page_title or title_hint
    return f"# {title}\n\n{body}", None, warnings


def from_text(path: Path):
    text, warnings = read_text(path)
    return text, None, warnings


# ---------------------------------------------------------------- main
SUFFIX = {
    ".pptx": "pptx",
    ".docx": "docx",
    ".pdf": "pdf",
    ".xlsx": "xlsx", ".xlsm": "xlsx",
    ".csv": "csv", ".tsv": "csv", ".tab": "csv",
    ".html": "html", ".htm": "html",
    ".md": "text", ".markdown": "text", ".txt": "text", ".log": "text", ".json": "text",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="任意格式 -> Markdown")
    ap.add_argument("input", help="文件路径或 http(s) URL")
    ap.add_argument("-o", "--out", required=True, help="输出 Markdown 路径")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS_DEFAULT)
    args = ap.parse_args()

    src = args.input
    warnings: list[str] = []
    pages = None

    if re.match(r"^https?://", src):
        fmt = "html"
    else:
        p = Path(src).expanduser()
        if not p.exists():
            print(json.dumps({"ok": False, "error": f"文件不存在: {p}"}, ensure_ascii=False))
            return 2
        fmt = SUFFIX.get(p.suffix.lower())
        if not fmt:
            legacy = {".doc": ".docx", ".ppt": ".pptx", ".xls": ".xlsx"}.get(p.suffix.lower())
            hint = f"；这是老版二进制格式，请先另存为 {legacy}" if legacy else ""
            print(json.dumps(
                {"ok": False, "error": f"不支持的格式 {p.suffix}；支持 pptx/docx/pdf/xlsx/csv/html/md/txt{hint}"},
                ensure_ascii=False))
            return 2

    try:
        if fmt == "pptx":
            md, pages, w = from_pptx(Path(src))
        elif fmt == "docx":
            md, pages, w = from_docx(Path(src))
        elif fmt == "pdf":
            md, pages, w = from_pdf(Path(src))
        elif fmt == "xlsx":
            md, pages, w = from_xlsx(Path(src))
        elif fmt == "csv":
            md, pages, w = from_csv(Path(src))
        elif fmt == "html":
            md, pages, w = from_html(src)
        else:
            md, pages, w = from_text(Path(src))
        warnings += w
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"解析失败: {exc}"}, ensure_ascii=False))
        return 3

    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    if len(md) > args.max_chars:
        cut = md.rfind("\n", 0, args.max_chars)
        if cut < args.max_chars * 0.8:  # 找不到合适换行就硬截
            cut = args.max_chars
        md = md[:cut]
        warnings.append(f"内容超过 {args.max_chars} 字符，已按行截断（可能丢失尾部信息）")
    if len(md) < 100:
        if (pages or 1) > 1:
            warnings.append("多页文档只抽到不足 100 字，可能是扫描件/纯图片/受保护文档，建议改用 OCR 或多模态读图")
        else:
            warnings.append("抽取到的文字不足 100 字，请确认文件内容是否完整")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")

    pages_est = pages if pages else est_pages(len(md))
    print(json.dumps({
        "ok": True,
        "format": fmt,
        "source_pages": pages_est,
        "pages_estimated": pages is None,
        "chars": len(md),
        "out": str(out),
        "warnings": warnings,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
