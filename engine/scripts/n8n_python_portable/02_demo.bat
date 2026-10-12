@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" runner.py demo
if errorlevel 1 goto fail
start "" "%~dp0demo-output\report.html"
pause
exit /b 0
:fail
echo Demo failed. Run 01_setup.bat first.
pause
exit /b 1
