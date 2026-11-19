#!/usr/bin/env python3
"""从 source.md 抽出「可核对的原子事实」，供模型逐条对照查前后矛盾。

为什么需要它：长文档里数字散落在几十页上，靠记忆通读一定漏；模型还会
为了「显得认真」编出矛盾。这里只做机械抽取，不做判断——是不是真矛盾
留给模型按举证制确认（每条必须给两处页码+短引）。

能力边界（别高估它）：能抓「指标名被逐字重复而值不同」，抓不到「同一件
事换个说法」。110 页问询函实测：7 处 P0 里它只报出 1 处（周转率 0.70/0.47），
其余靠人工勾稽。它的真实价值是当定位表——把几十页上的数字摆到一张纸上。

用法:
  a4 facts <source.md> -o <facts.md> [--max-rows 200]

stdout 打印一行 JSON 统计。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 数字：允许千分位与小数；前面不能紧贴数字或小数点（避免从 3.14 里切出 14）
NUM = r"(?<![\d.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
# 单位按「长的在前」排列，否则 万元 会被切成 万
UNIT = (
    r"(万亿元|亿元|万元|百分点|亿|万|千|百|%|％|倍|元|人|年|月|日|天|"
    r"小时|分钟|秒|个|次|项|家|台|套|张|页|条|公斤|kg|t|mm|pt|℃)"
)
MENTION_RE = re.compile(NUM + r"\s*" + UNIT)

# 长的在前：先认 Y年M月D日，再退到 Y年M月、Y年，避免只抓到半截
# 「应收账款周转率为 0.70」这种没有单位的指标值，MENTION_RE 抽不到——
# 而本次实测的 7 处 P0 里，「周转率 0.70 vs 0.47」正是这一类。
BARE_NUM = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d.])\d+(?:\.\d+)?")
RATIO_TAIL = ("率", "比率", "倍数", "系数")

DATE_RES = [
    re.compile(r"\d{4}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}\s*日?"),
    re.compile(r"\d{4}\s*[-/年.]\s*\d{1,2}(?!\d)\s*月?"),
    re.compile(r"(?<!\d)\d{1,2}\s*月\s*\d{1,2}\s*日"),
    re.compile(r"第[一二三四五六七八九十\d]+季度|Q[1-4]|(?:19|20)\d{2}\s*年"),
]

DEF_RE = re.compile(r"(是指|指的是|定义为|口径|统称|简称|含义为|认定为|属于)")
CONC_RE = re.compile(r"(综上|因此|由此可见|可见|结论|应当|必须|不得|禁止|建议|总体而言|总的来说|原则上)")

MULT = {
    "万": 1e4, "万元": 1e4, "亿": 1e8, "亿元": 1e8, "万亿": 1e12, "万亿元": 1e12,
    "千": 1e3, "百": 1e2, "百分点": 1.0, "%": 1.0, "％": 1.0,
}
# 这些单位太泛（年月日个次），同一名称不同值几乎是正常表述，不进「疑似矛盾」
WEAK_UNITS = {"年", "月", "日", "天", "个", "次", "项", "家", "台", "套", "张", "页", "条", "人"}
MONEY_UNITS = {"元", "万元", "亿元", "万亿", "万亿元", "万", "亿"}

PUNCT = re.compile(r"[\s\u3000|\*_`#>，。、；：！？（）()\[\]【】“”\"'’,.!?;:·\-–—/和与及的之对为]")


def norm_value(num: str, unit: str) -> float | None:
    try:
        base = float(num.replace(",", ""))
    except ValueError:
        return None
    return base * MULT.get(unit, 1.0)


# 名称后常跟的动词/介词：剥掉才能让「预算」和「预算追加至」归到同一个名字下
SUFFIX_STRIP = (
    "追加至", "上调至", "下调至", "调整至", "增加至", "减少至", "提升至", "下降至",
    "增至", "降至", "达到", "约为", "共计", "总计", "合计", "合计为",
    "至", "为", "约", "共", "有", "达", "是", "在", "按",
)


def strip_suffix(key: str) -> str:
    changed = True
    while changed and key:
        changed = False
        for tok in SUFFIX_STRIP:
            if key.endswith(tok) and len(key) > len(tok):
                key = key[: -len(tok)]
                changed = True
                break
    return key


# 泛词：出现在数字左边的语法成分，不是指标名。
# 不排除的后果很实测：110 页公告报出 184 行「同名不同值」，前几名是
# 「比例」27 行、「应收账款余额」22 行、「确认营业收入」20 行——全是把整篇
# 数字塌进一个桶，一条有用的都没有。真正的矛盾靠人工勾稽和浮点验算。
GENERIC_KEYS = (
    "比例", "占比", "比重", "余额", "金额", "收入", "营业收入", "费用", "成本",
    "毛利", "毛利率", "净额", "总额", "小计", "合计", "共计", "同比", "环比",
    "增长", "增加", "下降", "减少", "计提", "确认", "折算", "万元", "亿元", "元",
    "以上", "以下", "其中", "其他", "本期", "上期", "期末", "期初", "当年",
    "分别", "单位", "说明", "备注", "项目", "指标", "名称", "日期", "期间",
    "此外", "同时", "本次", "上述", "目前", "亿元", "万元", "亿", "万",
    "年", "月", "日", "期", "支付", "收取", "发生", "实现", "达到",
    # 散文里的主谓残片：出现在数字左边纯属语序，不是指标名
    "你公司", "公司", "截至", "截止", "获取", "签订", "如上表", "如下", "内付",
    "价值", "年报", "报告期", "公告", "情况", "情形",
)

# 名称归一化：同一指标常被写成「占比/比重/比例」「应收账款余额/应收账款账面余额」，
# 不归一就永远聚不到一起（实测漏掉了「3 年以上应收占比 61.17% vs 62.21%」这类）。
KEY_NORMALIZE = (
    ("账面余额", "余额"), ("期末余额", "余额"), ("期初余额", "余额"),
    ("占比", "比例"), ("比重", "比例"), ("比率", "比例"),
    ("账面价值", "价值"), ("账面", ""), ("期末", ""), ("期初", ""),
    ("本期", ""), ("上期", ""), ("年末", ""), ("年度", ""),
)


def normalize_key(key: str) -> str:
    for a, b in KEY_NORMALIZE:
        key = key.replace(a, b)
    return key


def is_generic(key: str) -> bool:
    if not key:
        return True
    return any(key == g or key.endswith(g) for g in GENERIC_KEYS)


def left_key(text: str, start: int) -> str:
    """数字左侧最近的「名称」：取 14 字窗口里最后一串非标点片段。"""
    window = text[max(0, start - 14):start]
    parts = [p for p in PUNCT.split(window) if p]
    if not parts:
        return ""
    key = normalize_key(strip_suffix(parts[-1]))
    if not key or any(ch.isdigit() for ch in key):
        return ""
    key = key[-8:]
    if len(key) < 2 or is_generic(key):   # 单字（多为公式里的 A/B/C）没有信息量
        return ""
    return key


def sentence_of(line: str, start: int, width: int = 64) -> str:
    """取数字所在的那一句，超长则围绕数字截断。"""
    seg_start = 0
    for m in re.finditer(r"[。；;!！?？]", line):
        if m.end() <= start:
            seg_start = m.end()
        else:
            break
    seg = line[seg_start:].split("。")[0]
    seg = seg.replace("|", " ").strip()
    if len(seg) <= width:
        return seg
    pos = max(0, start - seg_start - width // 3)
    cut = seg[pos:pos + width]
    return ("…" if pos > 0 else "") + cut + ("…" if pos + width < len(seg) else "")


def merge_suffix_keys(raw: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """把互为后缀的名称并成一组。

    「本项目总投入 380 万」和「综上，总投入 320 万」会被切成 目总投入 / 总投入
    两个键——不合并就漏掉最典型的那类矛盾。后缀关系足够把这类切歪的键收回来，
    又不会把毫无关系的名字并到一起。
    """
    keys = sorted(raw, key=len)
    alias = {k: k for k in keys}

    def find(k: str) -> str:
        while alias[k] != k:
            k = alias[k]
        return k

    for i, a in enumerate(keys):
        if a in GENERIC_KEYS:
            continue
        for b in keys[:i]:            # b 更短
            if len(b) < 2 or b in GENERIC_KEYS:
                continue
            if a.endswith(b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    alias[ra] = rb
                break

    merged: dict[str, list[dict]] = {}
    for k, items in raw.items():
        merged.setdefault(find(k), []).extend(items)
    # 组名取组内出现最多、其次最短的键，避免显示被切歪的长键
    out: dict[str, list[dict]] = {}
    for root, items in merged.items():
        counts: dict[str, int] = {}
        for it in items:
            counts[it["name"]] = counts.get(it["name"], 0) + 1
        best = min(counts, key=lambda k: (-counts[k], len(k)))
        out[best] = items
    return out


def fmt_num(v: float, unit: str) -> str:
    if unit in MONEY_UNITS:
        if v >= 1e12:
            return f"{v / 1e12:g} 万亿"
        if v >= 1e8:
            return f"{v / 1e8:g} 亿"
        if v >= 1e4:
            return f"{v / 1e4:g} 万"
    return f"{v:g}"


def table(rows: list[list[str]], head: list[str]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join(c.replace("|", "/") for c in r) + " |")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="从 source.md 抽原子事实供核对")
    ap.add_argument("source")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--max-rows", type=int, default=200, help="每张表最多输出多少行")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.exists():
        print(json.dumps({"ok": False, "error": f"文件不存在: {src}"}, ensure_ascii=False))
        return 2
    text = src.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    mentions = []   # dict: name, value, unit, raw, page, sent
    dates = []
    defs = []
    concs = []
    page = "-"

    for ln, line in enumerate(lines, 1):
        stripped = line.strip()
        mpage = re.match(r"^#{1,3}\s*(第\s*\d+\s*页[^#]*)$", stripped)
        if mpage:
            page = mpage.group(1).strip()
            continue
        if not stripped or set(stripped) <= set("|-: "):
            continue

        for m in MENTION_RE.finditer(line):
            num, unit = m.group(1), m.group(2)
            val = norm_value(num, unit)
            if val is None:
                continue
            mentions.append({
                "name": left_key(line, m.start()),
                "value": val, "unit": unit, "raw": f"{num}{unit}",
                "page": page, "sent": sentence_of(line, m.start()),
            })

        # 裸比率：没带单位的指标值，只认「…率/倍数/系数 + 数字」这个形态
        unit_spans = [m.span() for m in MENTION_RE.finditer(line)]
        for m in BARE_NUM.finditer(line):
            span = m.span()
            if any(span[0] >= a and span[1] <= b for a, b in unit_spans):
                continue
            key = left_key(line, m.start())
            if not key or not key.endswith(RATIO_TAIL):
                continue
            val = norm_value(m.group(0), "")
            if val is None:
                continue
            mentions.append({"name": key, "value": val, "unit": "比率",
                             "raw": m.group(0), "page": page,
                             "sent": sentence_of(line, m.start())})

        taken: list[tuple[int, int]] = []
        for dr in DATE_RES:
            for dm in dr.finditer(line):
                span = dm.span()
                if any(span[0] < e and s < span[1] for s, e in taken):
                    continue
                taken.append(span)
                dates.append({"raw": dm.group(0).strip(), "page": page,
                              "sent": sentence_of(line, dm.start())})

        if DEF_RE.search(line):
            defs.append({"page": page, "sent": stripped[:100]})
        elif CONC_RE.search(line):
            concs.append({"page": page, "sent": stripped[:100]})

    # 同名不同值：同一名称出现两次以上且归一化数值不同 -> 优先核对
    raw_groups: dict[str, list[dict]] = {}
    for mm in mentions:
        if not mm["name"] or mm["unit"] in WEAK_UNITS:
            continue
        raw_groups.setdefault(mm["name"], []).append(mm)
    groups = merge_suffix_keys(raw_groups)

    conflicts = []
    for name, items in groups.items():
        vals = {round(i["value"], 6) for i in items}
        if len(items) < 2 or not 2 <= len(vals) <= 3:
            # 同一条目下出现 4 个以上不同值，多半是计提阶梯、名录或利率表
            # 被并进了一个名称，不是矛盾。
            continue
        for i in items:
            conflicts.append([
                name, i["raw"], fmt_num(i["value"], i["unit"]), i["page"], i["sent"],
            ])

    # 数值清单按值排序：相同值相邻，方便看出同一数值被两种说法描述了
    money = sorted(
        [m for m in mentions if m["unit"] not in WEAK_UNITS],
        key=lambda x: (x["value"], x["page"]),
    )

    out: list[str] = []
    out.append("# 事实清单（机械抽取，供核对）")
    out.append("")
    out.append(f"> 源：`{src.name}` · {len(mentions)} 处数字 · {len(dates)} 个日期 · "
               f"仅抽阿拉伯数字，中文数字（三百万）不在其中。")
    out.append("> 同一个数值写成 1.10 亿和 11,049.36 万属正常（单位混用），"
               "不单列成「疑点」——实测那种配对 27 组里 0 组有用。")
    out.append("")

    out.append("## 一、同名多值（疑似矛盾，优先核对）")
    out.append("")
    if conflicts:
        out.extend(table(conflicts[:args.max_rows],
                         ["名称", "原文", "归一化", "位置", "上下文"]))
        out.append("")
        out.append("> 同一名称下只列 2–3 个不同值；出现 4 个以上多半是计提阶梯、"
                   "利率表、客户名录被并进了一个名称，已丢弃。")
    else:
        out.append("未发现同一名称对应不同数值的直接冲突。")
        out.append("")
        out.append("> 这一节只能抓「指标名被逐字重复」的情形（如"
                   "「应收账款周转率为 0.70 / 0.47」）。同一件事换一种说法"
                   "（「占年度销售总额比例」vs「前五名收入占比」）抓不到，"
                   "那部分仍要人工勾稽。")
    out.append("")

    out.append("## 二、金额、百分比与比率值（按值排序）")
    out.append("")
    if money:
        out.extend(table(
            [[m["raw"], fmt_num(m["value"], m["unit"]), m["page"], m["sent"]]
             for m in money[:args.max_rows]],
            ["原文", "归一化", "位置", "上下文"]))
    else:
        out.append("（未抽到）")
    out.append("")

    out.append("## 三、日期与期限")
    out.append("")
    if dates:
        out.extend(table([[d["raw"], d["page"], d["sent"]] for d in dates[:args.max_rows]],
                         ["日期", "位置", "上下文"]))
    else:
        out.append("（未抽到）")
    out.append("")

    out.append("## 四、定义与口径")
    out.append("")
    if defs:
        out.extend(table([[d["page"], d["sent"]] for d in defs[:args.max_rows]],
                         ["位置", "原句"]))
    else:
        out.append("（未抽到）")
    out.append("")

    out.append("## 五、结论性断言")
    out.append("")
    if concs:
        out.extend(table([[c["page"], c["sent"]] for c in concs[:args.max_rows]],
                         ["位置", "原句"]))
    else:
        out.append("（未抽到）")
    out.append("")

    out.append("---")
    out.append("")
    out.append("这张表只做归类，不下结论。核对时按举证制：每条疑点必须能引出"
               "**两处位置 + 原文短引**，引不出就不要写进 errata。")
    out.append("")

    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out), encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "out": str(dst),
        "mentions": len(mentions),
        "money_or_pct": len(money),
        "dates": len(dates),
        "definitions": len(defs),
        "conclusions": len(concs),
        "same_name_diff_value": len(conflicts),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
