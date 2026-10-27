#!/usr/bin/env python3
"""交付前自检：页数、纸张、字体残留、末页填充度，并生成缩略图。

任何一项不达标都会写进 issues，调用方应据此决定重排还是提示用户。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

A4 = (595.28, 841.89)
TOL = 2.0


def fill_ratio(image) -> float:
    """末页内容占页面高度的比例（粗估，用于发现'只溢出几行'）。"""
    gray = image.convert("L")
    w, h = gray.size
    px = gray.load()
    step = max(1, w // 400)
    top, bottom = None, None
    for y in range(h):
        row_has_ink = False
        for x in range(0, w, step):
            if px[x, y] < 245:
                row_has_ink = True
                break
        if row_has_ink:
            if top is None:
                top = y
            bottom = y
    if top is None:
        return 0.0
    return round((bottom - top) / h, 3)


def main() -> int:
    ap = argparse.ArgumentParser(description="打印版 PDF 自检")
    ap.add_argument("pdf")
    ap.add_argument("--target-pages", type=int, default=None)
    ap.add_argument("--thumb", default=None, help="缩略图输出路径（PNG）")
    args = ap.parse_args()

    from pypdf import PdfReader
    import pypdfium2 as pdfium

    path = Path(args.pdf)
    reader = PdfReader(str(path))
    issues: list[str] = []
    notes: list[str] = []

    n = len(reader.pages)
    # --target-pages 是「上限」不是「配额」：少于目标页数是正常的（内容本来就少），
    # 只有超出上限才是问题。把 n != target 当错误会诱导去砍不该砍的内容或注水凑页。
    if args.target_pages and n > args.target_pages:
        issues.append(f"页数 {n} > 目标上限 {args.target_pages}")
    elif args.target_pages and n < args.target_pages:
        notes.append(f"页数 {n} 少于目标上限 {args.target_pages}（内容本身放得下，属正常）")

    fonts = set()
    sizes = []
    for pg in reader.pages:
        mb = pg.mediabox
        sizes.append((round(float(mb.width), 2), round(float(mb.height), 2)))
        f = pg.get("/Resources", {}).get("/Font")
        if f:
            fonts.update(str(k) for k in f.keys())

    for w, h in sizes:
        if abs(w - A4[0]) > TOL or abs(h - A4[1]) > TOL:
            issues.append(f"页面尺寸 {w}×{h}pt 不是 A4")
            break
    if fonts:
        issues.append(f"PDF 仍含 {len(fonts)} 个字体对象（未转曲，打印可能乱码）")

    pdf = pdfium.PdfDocument(str(path))
    # 只为末页填充度渲一页；完整截图只在要生成缩略图时才做。
    # 6 页 PDF 全渲染约 0.037s、单页 0.005s —— 量不大，但没必要每次自检都付。
    last_page = pdf[n - 1].render(scale=100 / 72).to_pil()
    last_fill = fill_ratio(last_page)
    if n > 1 and last_fill < 0.25:
        # 这是「还能再少一页」的优化提示，不是缺陷：
        # --target-pages 是上限，字号取「塞得下的最大档」，末页因此可能本就稀疏。
        # 真要压，就传 --target-pages (n-1) 逼字号降一档，而不是把 ok 判成 false。
        notes.append(
            f"末页仅填充 {int(last_fill * 100)}%，可传 --target-pages {n - 1} 再试一版"
        )

    if args.thumb:
        from PIL import Image

        images = [last_page] if n == 1 else [
            pdf[i].render(scale=100 / 72).to_pil() for i in range(n - 1)
        ] + [last_page]
        gap, bg, max_cols = 12, "white", 3
        w, h = images[0].size
        cols = min(max_cols, len(images))
        rows = (len(images) + cols - 1) // cols
        canvas = Image.new(
            "RGB", (w * cols + gap * (cols + 1), h * rows + gap * (rows + 1)), bg
        )
        for i, im in enumerate(images):
            r, c = divmod(i, cols)
            canvas.paste(im, (gap + c * (w + gap), gap + r * (h + gap)))
        canvas.save(args.thumb)
        del images

    print(json.dumps({
        "ok": not issues,
        "pages": n,
        "page_size_pt": sizes[0],
        "pages_mm": [round(sizes[0][0] * 25.4 / 72, 1), round(sizes[0][1] * 25.4 / 72, 1)],
        "fonts_remaining": len(fonts),
        "last_page_fill": last_fill,
        "issues": issues,
        "notes": notes,
        "thumb": args.thumb,
    }, ensure_ascii=False))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
