#!/bin/bash
# ============================================================
#  Mac: double-click this to start the app.
#  Your browser opens automatically.
#  Close this Terminal window (or press Ctrl+C) to stop it.
# ============================================================
cd "$(dirname "$0")" || exit 1

echo
echo " ============================================================"
echo "  STARTING - your browser will open in a few seconds"
echo " ============================================================"
echo
echo "   App:  http://localhost:8000"
echo "   API:  http://localhost:8000/docs"
echo
echo "   Leave this window open. Ctrl+C stops the server."
echo

# Give the server a moment to boot, then open the browser.
( sleep 4 && open http://localhost:8000 ) &

python3 run.py

echo
echo "  Server stopped."
read -n 1 -s -r -p "Press any key to close..."
