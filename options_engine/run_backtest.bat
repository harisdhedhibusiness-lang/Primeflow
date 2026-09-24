@echo off
rem Double-click: download fresh SPY/VIX history, run the full backtest, open the report.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m pfo fetch
if errorlevel 1 (
  echo Download failed. See README section "If the download fails".
  pause
  exit /b 1
)
%PY% -m pfo backtest
pause
