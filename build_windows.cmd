@echo off
setlocal
cd /d "%~dp0"
py -3.14 "%~dp0build_windows.py" %*
if errorlevel 1 (
    echo Build failed. Check Python 3.14.6 and the preceding error.
    pause
    exit /b 1
)
echo Build completed. See artifacts.
pause
