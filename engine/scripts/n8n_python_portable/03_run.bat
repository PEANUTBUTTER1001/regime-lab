@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" runner.py run --prompt-secrets
if errorlevel 1 goto fail
start "" "%~dp0output\report.html"
pause
exit /b 0
:fail
echo Run failed. Read the message above and README.md.
pause
exit /b 1
