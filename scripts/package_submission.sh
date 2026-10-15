#!/usr/bin/env bash
# 打包笔试提交附件（≤30MB）：提交说明 + README + AI 使用与验证记录 + 测试说明 + 评测结果 + 截图 + 源码快照。
# 用法：scripts/package_submission.sh [输出目录]   默认输出到 ~/Desktop
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-$HOME/Desktop}"
STAMP="$(date +%Y%m%d-%H%M)"
NAME="明筛_自然语言选股与策略解释器_提交材料_${STAMP}"
WORK="$(mktemp -d)/${NAME}"
mkdir -p "$WORK/docs" "$WORK/eval" "$WORK/screenshots"

cp "$ROOT/docs/提交说明.md" "$WORK/00_提交说明.md"
cp "$ROOT/README.md" "$WORK/01_README.md"
cp "$ROOT/docs/AI_USAGE.md" "$WORK/02_AI使用与验证记录.md"
cp "$ROOT/docs/TESTING.md" "$WORK/03_测试说明.md"
cp "$ROOT/eval/cases.json" "$WORK/eval/"
ls "$ROOT/eval/results/"*.json >/dev/null 2>&1 && cp "$ROOT/eval/results/"*.json "$WORK/eval/"
[ -d "$ROOT/docs/screenshots" ] && cp "$ROOT/docs/screenshots/"* "$WORK/screenshots/" 2>/dev/null || true
[ -f "$ROOT/docs/demo.mp4" ] && cp "$ROOT/docs/demo.mp4" "$WORK/04_演示视频.mp4"
# 源码快照：只含 Git 跟踪的文件（不含 .env、数据快照、台账）
git -C "$ROOT" archive --format=zip --prefix=nl-stock-screener/ -o "$WORK/05_源码快照.zip" HEAD

mkdir -p "$OUT_DIR"
( cd "$(dirname "$WORK")" && zip -qr "$OUT_DIR/${NAME}.zip" "$NAME" )
SIZE=$(du -m "$OUT_DIR/${NAME}.zip" | cut -f1)
echo "已生成：$OUT_DIR/${NAME}.zip（${SIZE} MB）"
[ "$SIZE" -le 30 ] || { echo "超过 30MB，请压缩演示视频后重打包" >&2; exit 1; }
