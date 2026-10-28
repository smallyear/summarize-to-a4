#!/usr/bin/env bash
# 把 summarize-to-a4 打包成 workbuddy 可上传的两级目录结构。
#
# workbuddy 只接受「根目录/二级目录/文件」两级，而本仓库里有
# .github/workflows/、.workbuddy/memory/、__pycache__/ 这些三级嵌套，
# 直接上传会被拒。本脚本只挑 skill 运行时需要的文件，生成干净包。
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
for f in SKILL.md README.md LICENSE requirements.txt regress.py; do
  [ -f "$f" ] && cp "$f" "$OUT_DIR/"
done

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

echo "✅ 打包完成: $OUT_DIR"
echo
echo "文件清单:"
find "$OUT_DIR" -type f | sed "s|$OUT_DIR/||" | sort
