#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# One-time setup: a private venv with PyMuPDF, OpenCV, numpy and the MCP client.
# Kept outside the skills folder so the skill index does not scan it.
set -e
VENV="${SECTRACE_VENV:-/opt/data/tools/sectrace-venv}"
if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c "import pymupdf, cv2, numpy, mcp" 2>/dev/null; then
  echo "ready: $VENV"; exit 0
fi
PY=/usr/bin/python3
[ -x "$PY" ] || PY=python3
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q pymupdf numpy opencv-python-headless mcp
"$VENV/bin/python" -c "import pymupdf, cv2, numpy, mcp; print('ready:', '$VENV', 'pymupdf', pymupdf.VersionBind, 'opencv', cv2.__version__)"
