@echo off
setlocal
cd /d "%~dp0"
echo.
echo Installing or reinstalling UHV Sentinel for this Windows user...
echo This does NOT delete past settings or pressure recordings.
echo.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 installer.py install
) else (
    python installer.py install
)
if errorlevel 1 (
    echo.
    echo Installation FAILED. See error messages above.
    pause
    exit /b 1
)
echo.
echo Installation complete.
pause
