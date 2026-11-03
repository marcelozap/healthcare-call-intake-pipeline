#!/bin/bash
# ============================================================
#  Mac: double-click this first.
#  Installs what's needed, builds the database, runs the tests.
#  The window stays open at the end so you can read it.
# ============================================================
cd "$(dirname "$0")" || exit 1

echo
echo " ============================================================"
echo "  STEP 1 of 4 - checking Python"
echo " ============================================================"
if ! command -v python3 >/dev/null 2>&1; then
    echo
    echo "  Python 3 is not installed."
    echo "  Install it from python.org, then run this again."
    echo
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi
python3 --version
echo

echo " ============================================================"
echo "  STEP 2 of 4 - installing packages (takes a minute)"
echo " ============================================================"
# --user avoids needing sudo. --break-system-packages is needed on newer
# macOS Pythons that protect the system site-packages; harmless if not.
python3 -m pip install --quiet --user fastapi uvicorn scikit-learn pandas pytest 2>/dev/null \
  || python3 -m pip install --quiet --user --break-system-packages fastapi uvicorn scikit-learn pandas pytest
if [ $? -ne 0 ]; then
    echo
    echo "  Install failed. Copy everything above and send it to Claude."
    echo
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi
echo "  done."
echo

echo " ============================================================"
echo "  STEP 3 of 4 - building the database"
echo " ============================================================"
python3 run.py --check
if [ $? -ne 0 ]; then
    echo
    echo "  Build failed. Copy everything above and send it to Claude."
    echo
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi
echo

echo " ============================================================"
echo "  STEP 4 of 4 - running the tests"
echo " ============================================================"
python3 -m pytest tests/ -q
echo

echo " ============================================================"
echo "  FINISHED"
echo " ============================================================"
echo
echo "  You want to have seen:"
echo "     \"all checks passed\""
echo "     \"49 passed\""
echo
echo "  The database is now at:  data/outreach.db"
echo
echo "  Next: double-click  \"2 - START THE APP.command\""
echo
read -n 1 -s -r -p "Press any key to close..."
