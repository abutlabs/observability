#!/usr/bin/env bash
# Build the course site: the viewer (site/) with the markdown it renders (content/,
# exercises/) copied in for real, and a content hash on every stylesheet and script
# reference so a new deploy is never mixed with a cached old one. The GitHub Pages
# workflow (.github/workflows/pages.yml) and a local preview both run this.
#
#   learning-observability/tools/build.sh [OUT]     # default: learning-observability/_site
#
# Preview what Pages will serve, under a sub-path as on abutlabs.github.io/observability/:
#
#   learning-observability/tools/build.sh
#   cd learning-observability && python3 -m http.server 8000 --bind 127.0.0.1
#   open http://127.0.0.1:8000/_site/
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/_site}"

python3 "$ROOT/tools/check.py"

rm -rf "$OUT"
mkdir -p "$OUT"
cp -R "$ROOT/site/." "$OUT/"
# site/content and site/exercises are symlinks for local work; ship real copies
rm -rf "$OUT/content" "$OUT/exercises"
cp -R "$ROOT/content" "$OUT/content"
cp -R "$ROOT/exercises" "$OUT/exercises"
find "$OUT" \( -name '__pycache__' -o -name '.DS_Store' \) -prune -exec rm -rf {} +

python3 "$ROOT/tools/stamp_assets.py" "$OUT"
echo "built $OUT"
