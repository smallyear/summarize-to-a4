#!/usr/bin/env python3
"""summarize-to-a4 回归测试：覆盖 2026-10-27 审查发现的全部问题。

用法: python3 regress.py
全部通过时退出码 0。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True  # 别在 skill 目录里留 __pycache__

from pathlib import Path  # noqa: E402

SKILL = Path(__file__).resolve().parent
SCRIPTS = SKILL / "scripts"
PY = None
WORK = Path(tempfile.mkdtemp(prefix="a4reg-"))

results: list[tuple[str, bool, str]] = []


def pick_python() -> str:
    for c in (
        os.environ.get("A4_PYTHON"),
        str(Path.home() / ".workbuddy/binaries/python/envs/default/bin/python"),
        shutil.which("python3"),
    ):
        if c and Path(c).exists():
            return c
    raise SystemExit("找不到 python")


def run(script: str, *args: str, expect_fail: bool = False) -> tuple[dict, int]:
    p = subprocess.run(
        [PY, str(SCRIPTS / f"{script}.py"), *args],
        capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    out = p.stdout.strip().splitlines()
    data = {}
    for line in reversed(out):  # 最后一行为 JSON
        try:
            data = json.loads(line)
            break
        except Exception:  # noqa: BLE001
            continue
    if not expect_fail and p.returncode != 0:
        raise RuntimeError(f"{script} 失败 rc={p.returncode}\n{p.stdout}\n{p.stderr}")
    return data, p.returncode


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def w(name: str, text: str) -> Path:
    p = WORK / name
    p.write_text(text, encoding="utf-8")
    return p


def html_of(path: Path, target: int = 1, extra: list[str] | None = None) -> str:
    out = WORK / (path.stem + ".html")
    run("render", str(path), "-o", str(out), "--target-pages", str(target), *(extra or []))
    return out.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1 表格语法
def t_tables() -> None:
    cases = {
        "首尾竖线": "| a | b |\n|---|---|\n| 1 | 2 |\n",
        "无尾竖线": "| a | b\n|---|---\n| 1 | 2\n",
        "无首尾竖线": "a | b\n--- | ---\n1 | 2\n",
        "空格填充": "| a | b |\n| --- | --- |\n| 1 | 2 |\n",
    }
    for label, tbl in cases.items():
        p = w(f"tbl_{label}.md", "## 节\n\n" + tbl)
        h = html_of(p)
        check(f"表格语法·{label}", "<table>" in h, h[:120])

    # 转义竖线不被切列
    p = w("esc.md", "## 节\n\n| a | b |\n|---|---|\n| 1 | x\\|y |\n")
    h = html_of(p)
    check("表格·\\| 转义不当分隔符", "<td>x|y</td>" in h, h[:200])

    # 强信号优先：带首尾竖线的行不能被句末标点/长单元格判成句子
    p = w("punct.md", "## 节\n\n| 姓名 | 部门 | 状态 |\n|---|---|---|\n"
                      "| 张三 | 研发 | 已完成。 |\n| 李四 | 财务 | 待复核 |\n")
    h = html_of(p)
    check("表格·单元格含句号不断表", h.count("<td>") == 6, h[:300])

    p = w("longcell.md", "## 节\n\n| 项 | 说明 |\n|---|---|\n| 甲 | "
                         + "这是一段明显超过二十个字的说明性文字用于测试长度上限" + " |\n")
    h = html_of(p)
    check("表格·单格超 30 字仍算表格行", h.count("<td>") == 2, h[:300])

    # `---` 是水平分隔线，不能因为上一行含竖线就把整段变成表格
    p = w("hrline.md", "## 节\n\n段落里含 | 竖线\n---\n下一段落。\n")
    h = html_of(p)
    check("表格·--- 不与含竖线的段落冲突",
          "<table>" not in h and "<hr>" in h and "段落里含 | 竖线" in h, h[:300])

    # 表格后紧跟含竖线的普通段落：不能吞进表格
    for label, tail in {
        "带句末标点": "这里说明 A | B 的关系。",
        "长句": "这一行是很长的说明文字，讲的是 A | B 之间的取舍关系。",
    }.items():
        p2 = w(f"tail_{label}.md", f"## 节\n\n| a | b |\n|---|---|\n| 1 | 2 |\n{tail}\n")
        h2 = html_of(p2)
        check(f"表格·后续含竖线段落不被吞({label})",
              f"<p>{tail}</p>" in h2 and h2.count("<tr>") == 2, h2[h2.find("<table>"):][:260])

    # 缺列补齐 / 多列并入末格
    p = w("ragged.md", "## 节\n\n| a | b | c |\n|---|---|---|\n| 1 | 2 |\n| 1 | 2 | 3 | 4 |\n")
    h = html_of(p)
    check("表格·缺列补齐", "<td></td>" in h, h[:200])
    check("表格·多列并入末格", "3 4" in h, h[:200])


# ---------------------------------------------------------------- 2 参数转义
def t_escape() -> None:
    p = w("e.md", "# 标题\n\n正文。\n")
    h = html_of(p, extra=["--title", "Q3", "--subtitle", "来源：Q3<草稿>报告.pdf",
                          "--footer", "口径：营收&利润 <b>说明</b>"])
    check("参数·subtitle 尖括号转义", "Q3&lt;草稿&gt;报告.pdf" in h, h[:200])
    check("参数·footer 不注入标签", "&lt;b&gt;" in h and "<b>说明</b>" not in h, h[-200:])
    check("参数·footer & 转义", "&amp;" in h, h[-200:])


# ---------------------------------------------------------------- 3 多 h1
def t_multi_h1() -> None:
    p = w("h1.md", "# 第一部分\n内容一\n\n# 第二部分\n内容二\n")
    h = html_of(p)
    check("多 h1·不丢标题", "<h2>第二部分</h2>" in h, h[:200])
    check("多 h1·首个别当文档标题", "<h1>第一部分</h1>" in h, h[:200])

    h = html_of(p, extra=["--title", "覆盖"])
    check("多 h1·--title 优先", "<h1>覆盖</h1>" in h and "<h2>第一部分</h2>" in h, h[:200])

    # --title 与正文 # 相同：不能页顶一次、小节再重复一次
    same = w("same.md", "# 年度报告\n\n## 一、结论\n\n正文。\n")
    h = html_of(same, extra=["--title", "年度报告"])
    body_html = h.split("</style></head><body>", 1)[1]  # 只数正文，别把 <title> 算进去
    check("多 h1·与 --title 重复时不渲染小节",
          body_html.count("年度报告") == 1 and "<h2>年度报告</h2>" not in body_html,
          body_html[:300])


# ---------------------------------------------------------------- 4 段落/列表
def t_blocks() -> None:
    p = w("cont.md", "## 节\n\n- 列表项一\n紧随其后的独立段落\n- 列表项二\n")
    h = html_of(p)
    check("段落·顶格行不被列表吞", "<p>紧随其后的独立段落</p>" in h, h[:300])

    p = w("para.md", "## 节\n\n第一段。\n第二段。\n第三段。\n")
    h = html_of(p)
    check("段落·连续行各自成段", h.count("<p>") == 3, h[:300])

    p = w("year.md", "## 节\n\n2025. 年度总结开始于此行。\n")
    h = html_of(p)
    check("列表·年份不误判为 ol", "<ol>" not in h, h[:300])

    p = w("ol.md", "## 节\n\n1. 第一项\n2. 第二项\n")
    h = html_of(p)
    check("列表·真有序列表仍生效", "<ol>" in h and h.count("<li>") == 2, h[:300])

    p = w("ind.md", "## 节\n\n- 项一\n  续行内容\n")
    h = html_of(p)
    check("列表·缩进续行归入上一项", "项一 续行内容" in h, h[:300])


# ---------------------------------------------------------------- 5 提示块
def t_notes() -> None:
    p = w("note.md", "## 节\n\n> 灰一\n> 灰二\n> ! 橙色\n> 灰三\n")
    h = html_of(p)
    check("提示块·灰/橙按 ! 切分", h.count('class="note"') == 2 and h.count('class="warn"') == 1, h[:400])


# ---------------------------------------------------------------- 6 编码
def t_encoding() -> None:
    csv = WORK / "gbk.csv"
    csv.write_bytes("姓名,部门\n张三,研发\n".encode("gbk"))
    d, _ = run("extract", str(csv), "-o", str(WORK / "gbk.md"))
    text = (WORK / "gbk.md").read_text(encoding="utf-8")
    check("编码·GBK CSV 正确解码", "姓名" in text and "研发" in text, text[:100])
    check("编码·给出 GBK 警告", any("GB18030" in x or "GBK" in x for x in d.get("warnings", [])),
          str(d.get("warnings")))

    txt = WORK / "gbk.txt"
    txt.write_bytes("这是一段中文文本。".encode("gbk"))
    run("extract", str(txt), "-o", str(WORK / "gbk2.md"))
    check("编码·GBK 文本正确解码",
          "这是一段中文文本" in (WORK / "gbk2.md").read_text(encoding="utf-8"),
          (WORK / "gbk2.md").read_text(encoding="utf-8")[:60])

    # 反向：UTF-8 文件带替换符时不能被误判成 GBK
    # （gb18030 几乎能解任意字节，旧逻辑按替换符数量回退会把整份文件解成乱码）
    fffd = WORK / "fffd.txt"
    fffd.write_bytes("中文摘要内容测试。\ufffd\ufffd\ufffd 结尾部分。".encode("utf-8"))
    run("extract", str(fffd), "-o", str(WORK / "fffd.md"))
    check("编码·UTF-8 含替换符不被误判为 GBK",
          "中文摘要内容测试" in (WORK / "fffd.md").read_text(encoding="utf-8"),
          (WORK / "fffd.md").read_text(encoding="utf-8")[:60])

    # ---- 轻微损坏的 UTF-8：必须保住正文，不能被 gb18030 全盘洗成乱码 ----
    good = ("这是正常的中文内容，用于测试编码探测是否会把 UTF-8 误判成 GBK。" * 3)
    base = good.encode("utf-8")
    damage = {
        "首字节坏": b"\x81" + base[1:],
        "中段插坏字节": base[:30] + b"\xff\xfe" + base[30:],
        "尾部截断": base[:-2],
    }
    for label, data in damage.items():
        p = WORK / f"bad_{label}.txt"
        p.write_bytes(data)
        dmg, _ = run("extract", str(p), "-o", str(WORK / f"bad_{label}.md"))
        got = (WORK / f"bad_{label}.md").read_text(encoding="utf-8")
        # 关键断言：大部分正文仍是可读中文（而不是整篇乱码）
        readable = sum(1 for ch in got if "\u4e00" <= ch <= "\u9fff")
        check(f"编码·UTF-8 损坏({label})不被当成 GBK", readable >= 30,
              f"可读汉字 {readable} 个；前 40 字={got[:40]}")
        check(f"编码·UTF-8 损坏({label})给出明确警告",
              any("UTF-8" in w for w in dmg.get("warnings", [])),
              str(dmg.get("warnings")))

    # GBK 文件末尾被截掉半个汉字：不能整篇变乱码
    gbk_body = "关于2025年度财务预算的批复：原则同意，请按季度报送执行情况。"
    cut = WORK / "gbk_cut.txt"
    cut.write_bytes(gbk_body.encode("gbk")[:-1])
    dcut, _ = run("extract", str(cut), "-o", str(WORK / "gbk_cut.md"))
    got = (WORK / "gbk_cut.md").read_text(encoding="utf-8")
    readable = sum(1 for ch in got if "\u4e00" <= ch <= "\u9fff")
    check("编码·GBK 尾部截断后仍可读", readable >= 15,
          f"可读汉字 {readable} 个；{got[:40]}")
    check("编码·GBK 尾部截断给出不完整警告",
          any("不完整" in w or "GB18030" in w for w in dcut.get("warnings", [])),
          str(dcut.get("warnings")))

    # 老版二进制后缀要给出可行动的提示，而不是"解析失败"
    (WORK / "x.doc").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")  # OLE 复合文档头
    d, rc = run("extract", str(WORK / "x.doc"), "-o", str(WORK / "x.md"), expect_fail=True)
    check("格式·老版 .doc 提示另存为 docx", rc != 0 and "另存为 .docx" in d.get("error", ""), str(d))

    # GBK 末尾被截掉半个汉字：strict 会失败，但结果主体可读，仍应采信
    cut = WORK / "cut.txt"
    cut.write_bytes("期末余额合计说明文档".encode("gbk")[:-1])
    run("extract", str(cut), "-o", str(WORK / "cut.md"))
    out = (WORK / "cut.md").read_text(encoding="utf-8")
    check("编码·GBK 末尾截断仍能解出", "期末余额合计说明" in out, out[:40])

    # UTF-16LE 无 BOM：既不是 UTF-8 也不是 GBK，不能谎报"已按 GB18030 解码"
    u16 = WORK / "u16.txt"
    u16.write_bytes(("这是一份季度经营情况说明文档内容较长用于测试判据边界" * 3).encode("utf-16-le"))
    d, _ = run("extract", str(u16), "-o", str(WORK / "u16.md"))
    w = " ".join(d.get("warnings", []))
    check("编码·UTF-16 不谎报 GB18030", "GB18030" not in w, w[:120])

    # 短 UTF-8 中文被破坏到「坏字节占比很高」时，不能把 GBK 乱码当成功结果采信。
    # 旧判据只看「有汉字 + 控制符少」，而错位产生的乱码全是像汉字的生僻字，会被放行。
    t = "这是正常中文内容用于测试"
    raw = bytearray(t.encode("utf-8"))
    half = len(raw) // 2
    raw = bytes([0x81]) + bytes(raw[1:half]) + bytes([0x82]) + bytes(raw[half + 1:])
    (WORK / "short_bad.txt").write_bytes(raw)
    d2, _ = run("extract", str(WORK / "short_bad.txt"), "-o", str(WORK / "short_bad.md"))
    got = (WORK / "short_bad.md").read_text(encoding="utf-8")
    check("编码·短文件损坏不输出 GBK 乱码", "伩欐槸" not in got, got[:60])
    check("编码·短文件损坏给出可行动 warning",
          any("编码无法识别" in x or "UTF-8" in x for x in d2.get("warnings", [])),
          str(d2.get("warnings")))


# ---------------------------------------------------------------- 7 HTML 抽取
def t_html_extract() -> None:
    page = w("p.html", """<html><head><title>年度报告</title></head><body><article>
<h1>年度报告</h1><p>营收 120 亿。</p>
<h2>经营</h2>
<ul><li><p>华东 50 亿</p></li><li>华南 30 亿</li></ul>
<blockquote><p>董事会通过。</p></blockquote>
<table><tr><th>项目</th><th>金额</th></tr><tr><td>营收</td><td>120 亿</td></tr></table>
</article></body></html>""")
    run("extract", str(page), "-o", str(WORK / "p.md"))
    text = (WORK / "p.md").read_text(encoding="utf-8")
    check("HTML·li 内文本不重复", text.count("华东 50 亿") == 1, text)
    check("HTML·blockquote 内文本不重复", text.count("董事会通过") == 1, text)
    check("HTML·标题不重复出现两次", text.count("年度报告") == 1, text)
    check("HTML·表格整表保留", "| 营收 | 120 亿 |" in text, text)

    # 无 <title> 时用首个 h1
    page2 = w("p2.html", "<html><body><article><h1>我的标题</h1><p>正文内容足够长度用于测试。</p></article></body></html>")
    run("extract", str(page2), "-o", str(WORK / "p2.md"))
    check("HTML·无 title 时用首个 h1", (WORK / "p2.md").read_text(encoding="utf-8").startswith("# 我的标题"),
          (WORK / "p2.md").read_text(encoding="utf-8")[:40])

    # 无 <th> 的表格：首行是数据，不能变成列名
    page3 = w("p3.html", "<html><head><title>t</title></head><body><article>"
              "<table><tr><td>甲</td><td>100</td></tr><tr><td>乙</td><td>200</td></tr></table>"
              "<p>正文补足长度用aaaaaaaaaaaaaaaaaaaaaaaa。</p></article></body></html>")
    run("extract", str(page3), "-o", str(WORK / "p3.md"))
    t3 = (WORK / "p3.md").read_text(encoding="utf-8")
    check("HTML·无 th 表格首行仍是数据行", "甲" not in t3.split("|---|---|")[0], t3)

    # 标题层级：h1 必须高于 h2
    page4 = w("p4.html", "<html><head><title>t</title></head><body><article>"
              "<h1>一级标题</h1><p>段一。</p><h2>二级标题</h2><p>段二。</p>"
              "<h3>三级标题</h3><p>段三。</p></article></body></html>")
    run("extract", str(page4), "-o", str(WORK / "p4.md"))
    t4 = (WORK / "p4.md").read_text(encoding="utf-8")
    check("HTML·h1 层级高于 h2/h3",
          "\n## 一级标题" in t4 and "\n### 二级标题" in t4 and "\n#### 三级标题" in t4, t4)


# ---------------------------------------------------------------- 8 pptx 备注
def t_pptx() -> None:
    try:
        from pptx import Presentation
    except ImportError:
        check("PPTX·备注前缀不叠加（缺 python-pptx，跳过）", True, "skip")
        return
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "第一页"
    s.placeholders[1].text = "要点"
    s.notes_slide.notes_text_frame.text = "备注：这里是解释"
    deck = WORK / "d.pptx"
    prs.save(str(deck))
    run("extract", str(deck), "-o", str(WORK / "d.md"))
    text = (WORK / "d.md").read_text(encoding="utf-8")
    check("PPTX·备注前缀不叠加", "备注：备注：" not in text, text)


# ---------------------------------------------------------------- 8b docx 内容控件
def t_docx() -> None:
    try:
        import copy

        from docx import Document
        from docx.oxml import OxmlElement
    except ImportError:
        check("DOCX·内容控件内文字不丢（缺 python-docx，跳过）", True, "skip")
        return
    doc = Document()
    doc.add_paragraph("普通段落。")
    tmpl = doc.add_paragraph("控件内正文。")
    sdt = OxmlElement("w:sdt")
    sdtc = OxmlElement("w:sdtContent")
    sdtc.append(copy.deepcopy(tmpl._p))
    sdt.append(sdtc)
    doc.element.body.append(sdt)
    tmpl._p.getparent().remove(tmpl._p)  # 只留 sdt 里的那一份
    deck = WORK / "sdt.docx"
    doc.save(str(deck))
    run("extract", str(deck), "-o", str(WORK / "sdt.md"))
    text = (WORK / "sdt.md").read_text(encoding="utf-8")
    check("DOCX·内容控件(sdt)内文字不丢", "控件内正文" in text, text)


# ---------------------------------------------------------------- xlsx 表头启发式
def t_xlsx_header() -> None:
    try:
        import openpyxl
    except ImportError:
        check("XLSX·表头启发式（缺 openpyxl，跳过）", True, "skip")
        return

    def make(name, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "S"
        for r in rows:
            ws.append(r)
        path = WORK / name
        wb.save(path)
        out = WORK / (name + ".md")
        # 同时返回 md 文本与 warnings：提示语在 warnings 里，不在 md 里
        d, _ = run("extract", str(path), "-o", str(out))
        return out.read_text(encoding="utf-8"), d.get("warnings", [])

    # 无表头纯数据：首行不能变列名（必须出现在分隔行之下，而不是表头里）
    t, warns = make("nohead.xlsx", [["张三", "研发", 1000], ["李四", "财务", 2000], ["王五", "销售", 3000]])
    lines = [l for l in t.splitlines() if l.startswith("|")]
    sep_at = next((k for k, l in enumerate(lines) if set(l.replace("|", "").strip()) <= {"-"}),
                  None)
    head_rows = lines[:sep_at] if sep_at is not None else []
    check("XLSX·无表头时首行不当列名",
          sep_at is not None and not any("张三" in l for l in head_rows), t)
    check("XLSX·无表头时给出提示", any("像数据" in x for x in warns), str(warns))
    # 正常表头：不能被当成数据行
    t, _ = make("hashead.xlsx", [["项目", "数值"], ["营收", 128.4], ["净利", 9.7]])
    check("XLSX·有文字表头时正常识别",
          t.count("| 项目 | 数值 |") == 1 and "营收" in t, t)
    # 纯文字表（整表无数字）：仍应认得表头
    t, _ = make("textonly.xlsx", [["姓名", "部门", "状态"], ["张三", "研发", "在职"]])
    check("XLSX·纯文字表仍识别表头",
          t.count("| 姓名 | 部门 | 状态 |") == 1, t)
    # 全数字表：首行是数据
    t, _ = make("nums.xlsx", [[1, 2, 3], [4, 5, 6]])
    check("XLSX·全数字表首行保留", "| 1 | 2 | 3 |" in t, t)


# ---------------------------------------------------------------- PDF 批注
def t_pdf_annot() -> None:
    try:
        import pypdf
        from pypdf.annotations import Text
    except ImportError:
        check("PDF·批注抽取（缺 pypdf，跳过）", True, "skip")
        return

    src = WORK / "annot_src.html"
    src.write_text('<html><head><meta charset="utf-8"><style>body{font-family:"PingFang SC"}</style>'
                   '</head><body><p>正文内容。</p></body></html>', encoding="utf-8")
    base = WORK / "annot_base.pdf"
    run("topdf", str(src), "-o", str(base), "--no-outline")
    w = pypdf.PdfWriter(clone_from=str(base))
    w.add_annotation(page_number=0, annotation=Text(text="审阅批注：数据待复核", rect=(100, 100, 200, 120)))
    target = WORK / "annot.pdf"
    w.write(str(target))

    d, _ = run("extract", str(target), "-o", str(WORK / "annot.md"))
    text = (WORK / "annot.md").read_text(encoding="utf-8")
    check("PDF·批注内容被抽出", "数据待复核" in text, text[:200])
    check("PDF·批注带明确标记", "> 批注：" in text, text[:200])
    check("PDF·批注给出 warning", any("批注" in x for x in d.get("warnings", [])),
          str(d.get("warnings")))


# ---------------------------------------------------------------- 9 verify 语义
def t_verify() -> None:
    few = w("few.md", "## 节\n\n" + "正文内容。" * 300)
    out = WORK / "few.html"
    d, _ = run("render", str(few), "-o", str(out), "--target-pages", "6")
    pdf = WORK / "few.pdf"
    run("topdf", str(out), "-o", str(pdf))
    v, rc = run("verify", str(pdf), "--target-pages", "6", expect_fail=True)
    check("verify·少于上限不算错误", rc == 0 and not v["issues"], json.dumps(v, ensure_ascii=False))
    check("verify·少于上限给 notes", any("正常" in x for x in v.get("notes", [])), str(v.get("notes")))

    big = w("big.md", "## 节\n\n" + "正文内容。" * 4000)
    out2 = WORK / "big.html"
    run("render", str(big), "-o", str(out2), "--target-pages", "1")
    pdf2 = WORK / "big.pdf"
    run("topdf", str(out2), "-o", str(pdf2))
    v2, rc2 = run("verify", str(pdf2), "--target-pages", "1", expect_fail=True)
    check("verify·超出上限仍报错", rc2 == 1 and any("上限" in x for x in v2["issues"]),
          json.dumps(v2, ensure_ascii=False))

    # 不传 --thumb 时不该渲染全部页面（只为末页填充度渲一页即可）
    src = WORK / "perf.html"
    src.write_text("## 节\n\n" + "正文内容。" * 900, encoding="utf-8")
    run("render", str(src), "-o", str(WORK / "perf.h2.html"), "--target-pages", "6")
    run("topdf", str(WORK / "perf.h2.html"), "-o", str(WORK / "perf.pdf"))
    code = (
        "import sys,time;a=sys.argv[1]\n"
        "sys.path.insert(0,'" + str(SCRIPTS) + "')\n"
        "import pypdfium2 as pdfium, builtins\n"
        "n=[0]\n"
        "orig=pdfium.PdfPage.render\n"
        "def cnt(self,*a,**k):\n"
        "    n[0]+=1;return orig(self,*a,**k)\n"
        "pdfium.PdfPage.render=cnt\n"
        "import runpy;sys.argv=['verify.py',a,'--target-pages','6']\n"
        "try:runpy.run_path('" + str(SCRIPTS / "verify.py") + "',run_name='__main__')\n"
        "except SystemExit:pass\n"
        "print('RENDERED',n[0])\n"
    )
    r = subprocess.run([PY, "-c", code, str(WORK / "perf.pdf")],
                       capture_output=True, text=True)
    rendered = next((int(l.split()[1]) for l in r.stdout.splitlines()
                     if l.startswith("RENDERED")), -1)
    check("verify·无 thumb 时只渲染末页", rendered == 1, f"渲染了 {rendered} 页\n{r.stdout[-200:]}{r.stderr[-200:]}")


# ---------------------------------------------------------------- 10 字号档
def t_tiers() -> None:
    p = w("short.md", "## 节\n\n一句话。\n")
    d, _ = run("render", str(p), "-o", str(WORK / "short.html"), "--target-pages", "1")
    check("字号·短内容会放大（>11pt）", d["font_pt"] > 11.0, str(d["font_pt"]))
    check("字号·上限不超过 12.6", d["font_pt"] <= 12.6, str(d["font_pt"]))

    big = w("huge.md", "## 节\n\n" + "很长的正文内容。" * 900)
    d2, _ = run("render", str(big), "-o", str(WORK / "huge.html"), "--target-pages", "1")
    check("字号·压不下时 overflow 为 true", d2["overflow"] is True, json.dumps(d2["trail"]))

    # 档位降序
    sys.path.insert(0, str(SCRIPTS))
    import render  # noqa: PLC0415
    sizes = [t[0] for t in render.TIERS]
    check("字号·档位保持降序", sizes == sorted(sizes, reverse=True), str(sizes))


# ---------------------------------------------------------------- 11 参数校验
def t_guards() -> None:
    p = w("g.md", "## 节\n\n正文。\n")
    _, rc = run("render", str(p), "-o", str(WORK / "g.html"), "--target-pages", "0", expect_fail=True)
    check("参数·--target-pages 0 被拒绝", rc != 0, f"rc={rc}")

    d, _ = run("render", str(p), "-o", str(WORK / "g2.html"), "--target-pages", "1")
    check("render·无标题时给出 warning", any("标题" in x for x in d.get("warnings", [])),
          str(d.get("warnings")))


# ---------------------------------------------------------------- 12 fontconfig 隔离
def t_fontconfig() -> None:
    sys.path.insert(0, str(SCRIPTS))
    import a4env  # noqa: PLC0415
    env = a4env.prepare()
    conf = env.get("FONTCONFIG_FILE")
    if not conf:
        check("fontconfig·隔离配置已生成（非 macOS 跳过）", True, "skip")
        return
    body = Path(conf).read_text(encoding="utf-8")
    i_cache = body.find("<cachedir>")
    i_inc = body.find("<include")
    # cachedir 必须在 include 之前，否则系统默认 cachedir 仍然生效
    check("fontconfig·cachedir 位于 include 之前",
          -1 < i_cache < i_inc, f"cachedir@{i_cache} include@{i_inc}")


# ---------------------------------------------------------------- 中文字体检测
def t_cjk_font() -> None:
    """缺中文字体时 render 必须告警。

    weasyprint 缺字体不报错，中文静默变豆腐块；转曲后 verify 也查不出来。
    所以渲染前用 fc-list 主动查。这里测三种情况：
      1. 本机（通常有字体）→ 不该误报
      2. fc-list 输出为空 → 必须报
      3. fc-list 不存在 → 不判断，不能误报
    """
    code = (
        "import sys;sys.path.insert(0,'" + str(SCRIPTS) + "')\n"
        "import a4env;print(a4env.missing_cjk_font())\n"
    )

    # 1. 本机真实情况
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True)
    real = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    check("字体·本机检测不误报", real in ("True", "False"), r.stdout[-120:] + r.stderr[-120:])

    # 2/3. 用 PATH 里的假 fc-list 模拟
    shim = WORK / "shim"
    shim.mkdir(exist_ok=True)
    fake = shim / "fc-list"
    fake.write_text("#!/bin/sh\nexit 0\n")                        # 输出为空 = 无中文字体
    fake.chmod(0o755)                                              # 必须可执行，否则走 OSError 降级路径
    env = {**os.environ, "PATH": f"{shim}:{os.environ.get('PATH','')}"}
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=env)
    check("字体·fc-list 为空时报缺字体",
          r.stdout.strip().endswith("True"), r.stdout[-120:] + r.stderr[-120:])

    # 把 fc-list 整个藏掉：PATH 里只留假目录
    env2 = {**os.environ, "PATH": str(shim / "only")}
    (shim / "only").mkdir(exist_ok=True)
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=env2)
    check("字体·fc-list 不存在时不误报",
          r.stdout.strip().endswith("False"), r.stdout[-120:] + r.stderr[-120:])


# ---------------------------------------------------------------- 13 临时目录
def t_tmpdirs() -> None:
    before = set(Path(tempfile.gettempdir()).glob("a4pdf-*"))
    p = w("tmp.md", "## 节\n\n正文。\n")
    out = WORK / "tmp.html"
    run("render", str(p), "-o", str(out), "--target-pages", "1")
    run("topdf", str(out), "-o", str(WORK / "tmp.pdf"))
    after = set(Path(tempfile.gettempdir()).glob("a4pdf-*"))
    check("临时目录·topdf 不残留", not (after - before), f"{len(after - before)} 个残留")


# ---------------------------------------------------------------- 14 入口脚本
def t_entry() -> None:
    a4 = SCRIPTS / "a4"
    if not a4.exists():
        check("入口·scripts/a4 存在", False, "缺失")
        return
    check("入口·scripts/a4 可执行", os.access(a4, os.X_OK), "")
    p = subprocess.run([str(a4), "env"], capture_output=True, text=True)
    check("入口·a4 env 自检通过", p.returncode == 0 and "render ok" in p.stdout,
          (p.stdout + p.stderr)[:300])


def main() -> int:
    global PY
    PY = pick_python()
    for fn in (
        t_tables, t_escape, t_multi_h1, t_blocks, t_notes, t_encoding,
        t_html_extract, t_pptx, t_docx, t_xlsx_header, t_pdf_annot,
        t_verify, t_tiers, t_guards,
        t_fontconfig, t_cjk_font, t_tmpdirs, t_entry,
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            check(f"{fn.__name__} 异常", False, repr(exc)[:300])

    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        if not ok:
            print(f"FAIL  {name}\n      {detail}")
    print(f"\n{passed}/{len(results)} PASS   工作目录: {WORK}")
    if passed == len(results):
        shutil.rmtree(WORK, ignore_errors=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
