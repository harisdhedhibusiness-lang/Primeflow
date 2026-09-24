#!/bin/bash
# Double-click on a Mac: download fresh SPY/VIX history, run the full backtest, open the report.
cd "$(dirname "$0")" || exit 1
python3 -m pfo fetch || { echo 'Download failed. See README section "If the download fails".'; read -r; exit 1; }
python3 -m pfo backtest
