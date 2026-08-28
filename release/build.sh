#!/usr/bin/env bash
# Rebuild the portable `release/d3tool-dist` folder from the current source.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build_stage release/d3tool-dist
mkdir -p build_stage release/d3tool-dist/docs
cp -r d3tool build_stage/d3tool
find build_stage/d3tool -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
cat > build_stage/__main__.py <<'PYEOF'
#!/usr/bin/env python3
import sys
from d3tool.cli import main
if __name__ == "__main__":
    sys.exit(main())
PYEOF
python3 -m zipapp build_stage -p "/usr/bin/env python3" -o release/d3tool-dist/d3tool
chmod +x release/d3tool-dist/d3tool
cat > release/d3tool-dist/d3tool.bat <<'BATEOF'
@echo off
where py >nul 2>nul && (py -3 "%~dp0d3tool" %*) || (python "%~dp0d3tool" %*)
BATEOF
cat > release/d3tool-dist/run.sh <<'SHEOF'
#!/usr/bin/env bash
exec "$(dirname "$0")/d3tool" "$@"
SHEOF
chmod +x release/d3tool-dist/run.sh
cp README.txt release/d3tool-dist/README.txt 2>/dev/null || true
cp docs/FORMATS.md release/d3tool-dist/docs/FORMATS.md
cp docs/README.md release/d3tool-dist/docs/README.md 2>/dev/null || true
# a top-level README for the release folder
cat > release/d3tool-dist/README.txt <<'TXTE'
d3tool — Disciples 3 / dis3tool reverse-engineering toolkit

Run `./d3tool <command>` (Linux/macOS) or `d3tool.bat` (Windows).
Requires Python 3.8+ on PATH.  See docs/FORMATS.md for format notes.

  d3tool --help
  d3tool analyze <unit-folder>
  d3tool export <unit>.gltf -o out
  d3tool export-gl <unit>.g -a <unit>.a
  d3tool bundle <unit-folder> -o out
  d3tool validate <file>.gltf
  d3tool import <unit>.g
TXTE
rm -rf build_stage
echo "built release/d3tool-dist"
