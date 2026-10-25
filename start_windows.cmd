@echo off
setlocal
cd /d "%~dp0"
if exist "EnterpriseXmlAnalyzer.exe" (
    start "" "%~dp0EnterpriseXmlAnalyzer.exe"
    exit /b
)
py -3.14 -c "import sys; raise SystemExit(0 if sys.version_info[:3] == (3,14,6) else 2)" >nul 2>&1
if errorlevel 1 (
    echo Python 3.14.6 with Tcl/Tk is required.
    echo Use the Windows standalone ZIP, or install Python 3.14.6.
    pause
    exit /b 2
)
py -3.14 "%~dp0run.py"
if errorlevel 1 pause
