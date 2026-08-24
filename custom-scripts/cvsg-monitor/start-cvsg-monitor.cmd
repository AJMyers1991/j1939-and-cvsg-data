@echo off
setlocal
cd /d "%~dp0"
set "CVSG_LAUNCHED_BY_CMD=1"

rem Keep the 91-character dashboard from wrapping and breaking fixed row positions.
mode con: cols=100 lines=40 >nul 2>&1

echo Starting the PACCAR / Kenworth CVSG monitor...

where python.exe >nul 2>&1
if not errorlevel 1 (
    python.exe "%~dp0cvsg.py" %*
) else (
    where py.exe >nul 2>&1
    if errorlevel 1 (
        echo.
        echo Python 3.11 was not found. Install Python 3.11 and try again.
        pause
        exit /b 1
    )
    py.exe -3.11 "%~dp0cvsg.py" %*
)

set "CVSG_EXIT_CODE=%ERRORLEVEL%"
if not "%CVSG_EXIT_CODE%"=="0" (
    echo.
    echo The CVSG monitor exited with code %CVSG_EXIT_CODE%.
    pause
)
exit /b %CVSG_EXIT_CODE%
