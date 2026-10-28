#!/usr/bin/env bash
# 把 summarize-to-a4 打包成 workbuddy 可上传的两级目录结构。
#
# 两件事：
# 1. workbuddy 只接受「根目录/二级目录/文件」两级，而仓库里有
#    .github/workflows/、.workbuddy/memory/、__pycache__/ 三级嵌套，
#    直接上传会被拒。本脚本只挑 skill 运行时需要的文件。
# 2. workbuddy 上传还要求 SKILL.md frontmatter 里有 version / display_name /
#    display_name_en / description_zh / description_en 这些展示字段，
#    但它们不是 Agent Skills 规范字段（skills-ref 会报 Unexpected fields）。
#    所以源 SKILL.md 保持规范纯净，打包时才注入这些字段。
#
# 用法:
#   ./pack.sh              打包到 ./dist/
#   ./pack.sh /path/to/out 打包到指定目录
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${1:-$SKILL_DIR/dist}"
cd "$SKILL_DIR"

# 必需文件缺失检查
for f in SKILL.md scripts/a4 scripts/render.py scripts/topdf.py \
         scripts/verify.py scripts/extract.py scripts/a4env.py; do
  [ -f "$f" ] || { echo "错误：缺少 $f" >&2; exit 1; }
done

# 重建输出目录
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/references" "$OUT_DIR/scripts"

# 根目录文件（regress.py 与 requirements.txt 保留，方便别人复跑测试、装依赖）
for f in README.md LICENSE requirements.txt regress.py; do
  [ -f "$f" ] && cp "$f" "$OUT_DIR/"
done

# SKILL.md 单独处理：注入 workbuddy 展示字段
python3 - "$SKILL_DIR/SKILL.md" "$OUT_DIR/SKILL.md" <<'PY'
import re, sys

src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
if not m:
    sys.exit("SKILL.md 的 frontmatter 格式不对")
fm, body = m.group(1), m.group(2)

def get(key):
    mm = re.search(rf"^{key}:\s*(.*)$", fm, re.M)
    return mm.group(1).strip() if mm else ""

name, desc = get("name"), get("description")
license_, compat = get("license"), get("compatibility")

front = f"""---
name: {name}
version: "1.0.0"
display_name: "一纸通览"
display_name_en: "Summarize to A4"
description: {desc}
description_zh: 把任意来源（文本、网页、PPTX、DOCX、PDF、XLSX、CSV）压成尽量少纸张的可打印 A4 速览。文字转曲任何打印机不乱码，10 档字号自动适配页数，条目型内容可用双栏省一半纸。
description_en: "Compress any source (text, web, PPTX, DOCX, PDF, XLSX, CSV) into a printable A4 PDF with as few pages as possible. Vector-outlined output never garbles on any printer; 10 font tiers auto-fit the page count; two-column layout halves paper for list-style content."
license: {license_}
compatibility: {compat}
---
"""
open(dst, "w", encoding="utf-8").write(front + body)
PY

# 二级目录
cp references/density-rules.md references/layout-spec.md "$OUT_DIR/references/" 2>/dev/null || true
cp scripts/a4 scripts/a4env.py scripts/extract.py scripts/render.py \
   scripts/topdf.py scripts/verify.py "$OUT_DIR/scripts/"
chmod +x "$OUT_DIR/scripts/a4"

# 验证：不允许出现三级目录（find -mindepth 2 找到的就是三级目录）
bad="$(find "$OUT_DIR" -mindepth 2 -type d 2>/dev/null || true)"
if [ -n "$bad" ]; then
  echo "错误：打包结果存在三级目录：" >&2
  echo "$bad" >&2
  exit 1
fi

# 生成同名 zip（zip 内不含外层目录，解压后 SKILL.md 就在根；用 Python 跨平台，不依赖系统 zip）
ZIP_FILE="${OUT_DIR%/}.zip"
rm -f "$ZIP_FILE"
python3 - "$OUT_DIR" "$ZIP_FILE" <<'PY'
import os, sys, zipfile

src, dst = sys.argv[1], sys.argv[2]
SKIP_FILES = {".DS_Store"}
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f in SKIP_FILES:
                continue
            p = os.path.join(root, f)
            z.write(p, os.path.relpath(p, src))
print(f"  zip: {dst} ({os.path.getsize(dst)} bytes)")
PY

echo "✅ 打包完成: $OUT_DIR"
echo
echo "文件清单:"
find "$OUT_DIR" -type f | sed "s|$OUT_DIR/||" | sort
