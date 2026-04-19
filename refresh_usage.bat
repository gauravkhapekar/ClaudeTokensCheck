@echo off
REM refresh_usage.bat — Windows
REM Regenerates live_usage.js from your ~/.claude/projects/ session logs.
REM Usage:  refresh_usage.bat [--range week|month|last30|quarter|year|alltime]
REM Double-click or run from cmd/PowerShell.

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM Prefer py launcher (comes with official Python installer), fall back to python
where py >nul 2>&1 && set "PYTHON=py" || (
  where python >nul 2>&1 && set "PYTHON=python" || (
    where python3 >nul 2>&1 && set "PYTHON=python3" || (
      echo Error: Python not found. Install Python 3.10+ from https://python.org and try again.
      pause
      exit /b 1
    )
  )
)

echo Using: %PYTHON%
echo.

if "%~1"=="" (
  %PYTHON% generate_usage_data.py
) else (
  %PYTHON% generate_usage_data.py %*
)

echo.
echo Done. Open Token Usage.html in your browser and reload.
pause
