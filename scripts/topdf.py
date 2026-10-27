#!/usr/bin/env python3
"""HTML -> PDF，并用 ghostscript 把文字转曲（彻底规避打印乱码）。

转曲后 PDF 不含任何字体对象，任何打印机/驱动都不会因字体替换而出问题；
同时保持矢量，放大不糊。代价是文字不可选中、不可搜索。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import a4env  # noqa: E402


def outline(src: Path, dst: Path) -> None:
    cmd = [
        "gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER",
        "-sDEVICE=pdfwrite", "-dNoOutputFonts",
        "-dPDFSETTINGS=/prepress",
        f"-sOutputFile={dst}", str(src),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"ghostscript 失败: {r.stderr.strip()[:300]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="HTML -> 可打印 PDF")
    ap.add_argument("html")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--no-outline", action="store_true", help="不做文字转曲")
    args = ap.parse_args()

    a4env.prepare()
    from weasyprint import HTML

    src = Path(args.html)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix="a4pdf-"))
    tmp = tmp_dir / "raw.pdf"
    try:
        HTML(filename=str(src)).write_pdf(str(tmp))

        warnings: list[str] = []
        outlined = False
        if args.no_outline:
            shutil.copyfile(tmp, out)
            warnings.append("已跳过文字转曲，打印时可能因字体替换而乱码")
        elif shutil.which("gs"):
            try:
                outline(tmp, out)
                outlined = True
            except Exception as exc:  # noqa: BLE001
                shutil.copyfile(tmp, out)
                warnings.append(f"转曲失败，已输出未转曲版本：{exc}")
        else:
            shutil.copyfile(tmp, out)
            warnings.append("未找到 ghostscript，已输出未转曲版本；建议 brew install ghostscript")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(json.dumps({
        "ok": True, "pdf": str(out),
        "outlined": outlined,
        "bytes": out.stat().st_size,
        "warnings": warnings,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
