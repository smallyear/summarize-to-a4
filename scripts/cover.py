#!/usr/bin/env python3
"""比对 source.md 与 outline.md，列出「原文有、摘要没有」的原子事实。

定位是**提示，不是错误**。B/C 类内容本来就该丢，这个工具只负责让「不小心
漏掉一条 A 类判据」这件事变得看得见。

为什么需要它：skill 里没有任何覆盖校验——`verify.py` 只看页数/A4/字体/末页
填充度，A/B/C 分级是纯纪律。实测一份 43 页的标注规范，摘要漏掉了「当年新
任命董事若披露在上一年年报中仍判 C」这条会改变分类结果的规则，而 verify
返回 issues 为空、notes 为空，没人会发现。

用法:
  a4 cover <source.md> <outline.md> [--max-rows 40]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import facts as F  # noqa: E402  复用同一套抽取逻辑，别再写一遍

PAGE_HEAD = re.compile(r"^#{1,3}\s*第\s*\d+\s*页")
# 摘要里可以合法省略、但读者不会误事的「量」：太小的数不值得单独列一行
TRIVIAL_MAX = 0.0

DATE_PARTS = re.compile(r"(\d{2,4})\s*[-/年.]\s*(\d{1,2})\s*(?:[-/月.]\s*(\d{1,2}))?")


def date_key(raw: str) -> tuple[int, ...]:
    m = DATE_PARTS.search(raw)
    if not m:
        return (abs(hash(raw)) % 10**9,)  # 认不出的（如 Q3）退化为字面匹配
    y, mo, d = m.group(1), m.group(2), m.group(3)
    year = int(y) if len(y) == 4 else 2000 + int(y)
    return (year, int(mo), int(d) if d else 0)


def han_only(text: str) -> str:
    return "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")


HAN_RUN = re.compile(r"[\u4e00-\u9fff]{4,}")
# 表格里的单元格通常只写数字，单位在表头：「余额（万元）」「计提比例」
UNIT_HINT = re.compile(r"(万亿元|亿元|万元|百分点|%|％|倍|元|人|天|年|月)")
UNIT_DECL = re.compile(r"单位[:：]\s*(?:人民币)?\s*(万亿元|亿元|万元|元)")


def split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def cell_unit(cell: str) -> str:
    m = UNIT_HINT.search(cell)
    return m.group(1) if m else ""


def key_term(sentence: str) -> str:
    """句子里最长的一串连续汉字，当作这句的关键术语。

    摘要必然改写，字符 n-gram 覆盖率在这类文档上几乎没有区分度（实测 16 句
    里 15 句低于 20%，包括明明覆盖到的）。改看「关键术语还在不在」：改写得
    再狠，术语一般会留；整句丢掉时术语也一定不在。
    """
    runs = HAN_RUN.findall(sentence)
    return max(runs, key=len) if runs else ""


def cover_ratio(sentence: str, target: str, n: int = 4) -> float:
    """字符 n-gram 覆盖率：摘要里有多少比例的 4 字片段出现。

    句级比对没有可靠的字面匹配（改写、压缩都合法），所以用 n-gram 覆盖度当
    近似。阈值定在 0.55：低于它的句子，摘要里基本没有对应表述。
    """
    a = han_only(sentence)
    if len(a) < n:
        return 1.0 if a and a in target else 0.0
    grams = {a[i:i + n] for i in range(len(a) - n + 1)}
    if not grams:
        return 1.0
    hit = sum(1 for g in grams if g in target)
    return hit / len(grams)


def family(unit: str) -> str:
    if unit in ("%", "％", "百分点"):
        return "pct"
    if unit in F.MONEY_UNITS:
        return "money"
    if unit == "比率":
        return "ratio"
    return "other"


def num_covered(value: float, unit: str, pool: list[tuple[float, str]]) -> bool:
    fam = family(unit)
    for v, u in pool:
        if family(u) != fam:
            continue
        base = max(abs(value), abs(v), 1e-9)
        if abs(value - v) / base <= 0.005:
            return True
    return False


def collect(path: Path):
    """把一份 md 摊成可比对的三类事实。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    nums, dates, sents = [], [], []
    page = "-"
    col_units: list[str] = []
    default_unit = ""
    rows = text.splitlines()
    for idx, line in enumerate(rows):
        stripped = line.strip()
        mpage = PAGE_HEAD.match(stripped)
        if mpage:
            page = mpage.group(0).lstrip("# ").strip()
            col_units, default_unit = [], ""
            continue
        if not stripped or set(stripped) <= set("|-: "):
            continue

        decl = UNIT_DECL.search(stripped)
        if decl:
            default_unit = decl.group(1)

        # 表头行：下一行是分隔行。逐列记下单位，供数据行用。
        if stripped.startswith("|") and idx + 1 < len(rows) and \
                re.match(r"^\|?\s*:?-{1,}", rows[idx + 1].strip()):
            col_units = [cell_unit(c) or default_unit for c in split_cells(stripped)]
            continue
        in_table = stripped.startswith("|")

        spans = [m.span() for m in F.MENTION_RE.finditer(line)]
        for m in F.MENTION_RE.finditer(line):
            val = F.norm_value(m.group(1), m.group(2))
            if val is None:
                continue
            if m.group(2) in F.WEAK_UNITS:   # 2 个、3 年、24 年这种，不是要核对的事实
                continue
            nums.append({"value": val, "unit": m.group(2), "raw": m.group(0).strip(),
                         "page": page, "sent": F.sentence_of(line, m.start())})
        cells = split_cells(line) if in_table else []
        for m in F.BARE_NUM.finditer(line):
            s, e = m.span()
            if any(s >= a and e <= b for a, b in spans):
                continue

            # 表格里「余额（万元）」那类：单位在表头，单元格只有数字。
            # 只认「纯数字格」——否则「客户 1」里的 1 会被当成 1 万元。
            if in_table:
                col = line[:m.start()].count("|") - (1 if stripped.startswith("|") else 0)
                cell = cells[col] if 0 <= col < len(cells) else ""
                unit = col_units[col] if 0 <= col < len(col_units) else default_unit
                pure = not re.sub(r"[\d,.\s%％]", "", cell)
                if unit and pure:
                    val = F.norm_value(m.group(0), unit)
                    if val is not None:
                        nums.append({"value": val, "unit": unit, "raw": m.group(0),
                                     "page": page, "sent": stripped[:110]})
                    continue

            key = F.left_key(line, m.start())
            if not key or not key.endswith(F.RATIO_TAIL):
                continue
            val = F.norm_value(m.group(0), "")
            if val is None:
                continue
            nums.append({"value": val, "unit": "比率", "raw": m.group(0),
                         "page": page, "sent": F.sentence_of(line, m.start())})

        day_hits: list[tuple[int, int]] = []
        for dr in F.DATE_RES:
            for dm in dr.finditer(line):
                # 示例编号里的年份（AR2024/5、Code 8173 AR2024）不是事实
                if re.search(r"AR\s*$", line[:dm.start()]):
                    continue
                span = dm.span()
                if any(span[0] < e and s_ < span[1] for s_, e in day_hits):
                    continue          # 2024.1.1 已被更长的匹配吃掉
                day_hits.append(span)
                dates.append({"raw": dm.group(0).strip(), "page": page,
                              "sent": F.sentence_of(line, dm.start())})

        if (F.DEF_RE.search(line) or F.CONC_RE.search(line)) and len(han_only(stripped)) >= 12:
            sents.append({"page": page, "sent": stripped[:110]})
    return nums, dates, sents, text


def table(rows: list[list[str]], head: list[str]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join(c.replace("|", "/") for c in r) + " |")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="source 有、outline 没有的事实")
    ap.add_argument("source")
    ap.add_argument("outline")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--max-rows", type=int, default=40, help="每张表最多多少行")
    ap.add_argument("--min-len", type=int, default=12, help="只比对不少于 N 个汉字的句子")
    args = ap.parse_args()

    src, out = Path(args.source), Path(args.outline)
    for p in (src, out):
        if not p.exists():
            print(json.dumps({"ok": False, "error": f"文件不存在: {p}"}, ensure_ascii=False))
            return 2

    s_nums, s_dates, s_sents, _ = collect(src)
    o_nums, o_dates, _, o_text = collect(out)
    o_pool = [(n["value"], n["unit"]) for n in o_nums]
    o_date_keys = {date_key(d["raw"]) for d in o_dates}
    o_flat = re.sub(r"\s+", "", o_text)

    miss_nums = [n for n in s_nums if not num_covered(n["value"], n["unit"], o_pool)]
    # 按量级降序：50 行上限时，先看到的是最material的那些
    miss_nums.sort(key=lambda n: -abs(n["value"]))
    miss_dates = [d for d in s_dates if date_key(d["raw"]) not in o_date_keys]

    sents = [s for s in s_sents if len(han_only(s["sent"])) >= args.min_len]

    lines: list[str] = ["# 覆盖比对（source 有、outline 没有）", ""]
    lines.append(f"> `{src.name}` vs `{out.name}` · 原文 {len(s_nums)} 处数值 / "
                 f"{len(s_dates)} 个日期 / {len(sents)} 句判据 · "
                 f"未覆盖 {len(miss_nums)} 处数值 / {len(miss_dates)} 个日期")
    lines.append("")
    lines.append("**这是提示清单，不是错误清单。** B/C 类本来就该丢；下面每一条只需回答一句"
                 "「它是故意删的，还是漏了」。")
    lines.append("")

    lines.append("## 一、日期未覆盖")
    lines.append("")
    if miss_dates:
        lines.extend(table([[d["raw"], d["page"], d["sent"]] for d in miss_dates[:args.max_rows]],
                           ["日期", "位置", "上下文"]))
    else:
        lines.append("原文的日期全部能在摘要里找到。")
    lines.append("")

    lines.append("## 二、数值未覆盖（按量级降序）")
    lines.append("")
    if miss_nums:
        lines.extend(table([[n["raw"], f"{n['value']:g}", n["page"], n["sent"]]
                            for n in miss_nums[:args.max_rows]],
                           ["原文", "归一化", "位置", "上下文"]))
    else:
        lines.append("原文的数值全部能在摘要里找到。")
    lines.append("")

    lines.append("## 三、判据句清单（不判覆盖，请逐条勾）")
    lines.append("")
    lines.append("中文摘要必然改写，机器判「这句覆盖了没有」只会制造假阳性——"
                 "这条不做比对，只把原文的规范性句子摆到一页上，省得你翻几十页。")
    lines.append("")
    if sents:
        lines.extend(table([[s["page"], s["sent"]] for s in sents[:args.max_rows]],
                           ["位置", "原句"]))
        lines.append("")
        tail = f"共 {len(sents)} 句" if len(sents) <= args.max_rows else \
            f"共 {len(sents)} 句，此处只列前 {args.max_rows} 句（--max-rows 可调）"
        lines.append(f"> {tail}。逐条对着摘要勾一遍，别只看条数。")
    else:
        lines.append("（原文没有可识别的规范性句子）")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 怎么用")
    lines.append("")
    lines.append("逐条判一句：**这条是 A 类（结论/判据/关键数字/例外），还是 B/C 类？**")
    lines.append("")
    lines.append("- A 类且摘要没有 → 回第 3 步补进去（这是真漏）")
    lines.append("- B/C 类 → 不管它，这次比对就算过")
    lines.append("- 判不明白 → 宁可补一行短句，也别让读者去猜")
    lines.append("")

    body = "\n".join(lines)
    if args.out:
        dst = Path(args.out)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)

    print(json.dumps({
        "ok": True,
        "out": args.out,
        "source": {"numbers": len(s_nums), "dates": len(s_dates), "sentences": len(sents)},
        "uncovered": {"numbers": len(miss_nums), "dates": len(miss_dates)},
        "sentence_checklist": len(sents),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
