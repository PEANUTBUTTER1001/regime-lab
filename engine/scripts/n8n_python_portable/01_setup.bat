@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (set "PYTHON=python") else (set "PYTHON=py -3")
%PYTHON% -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11 or later is required'"
if errorlevel 1 goto fail
%PYTHON% -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo Setup complete. Run 02_demo.bat for an offline test.
pause
exit /b 0
:fail
echo Setup failed. Install Python 3.11+ from python.org and check your Internet connection.
pause
exit /b 1
