@echo off
rem Double-click: today's regime and the exact trade each strategy would place.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
set /p EQUITY=Account size in dollars (e.g. 2000):
%PY% -m pfo today --equity %EQUITY%
pause
