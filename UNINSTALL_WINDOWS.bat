@echo off
setlocal
cd /d "%~dp0"
echo.
echo This removes UHV Sentinel but RETAINS your recorded data and settings.
echo Close UHV Sentinel before continuing.
choice /M "Continue uninstall"
if errorlevel 2 exit /b 0
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 installer.py uninstall
) else (
    python installer.py uninstall
)
if errorlevel 1 (
    echo Uninstallation failed.
    pause
    exit /b 1
)
pause
