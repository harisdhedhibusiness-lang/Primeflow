#!/bin/bash
# Double-click on a Mac: today's regime and the exact trade each strategy would place.
cd "$(dirname "$0")" || exit 1
read -r -p "Account size in dollars (e.g. 2000): " EQUITY
python3 -m pfo today --equity "${EQUITY:-100}"
read -r -p "Press Enter to close."
