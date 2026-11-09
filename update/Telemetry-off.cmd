@echo off
rem Turn off the reports and the learning journal AstraForge sends to keys.dops.media.
rem
rem This is the switch the author documented in PRIVACY.md ("About -> Reports and
rem learning"), not a workaround: the same PUT /api/telemetry {errors, learning}.
rem The subscription check and the update check stay as they are - they decide whether
rem the studio updates, and they are not telemetry.
rem
rem Usage:  Telemetry-off.cmd            switch both off
rem         Telemetry-off.cmd --check    only show the state
rem         Telemetry-off.cmd --on       switch both back on
setlocal
cd /d "%~dp0"
title AstraForge - reports and learning journal

if defined PORT (
  set "ARGS=--port %PORT%"
) else (
  set "ARGS="
)

set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE ( where python >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE (
  for %%R in (C D E F) do (
    if not defined PYEXE if exist "%%R:\AstraForge\runtime\engine\.venv\Scripts\python.exe" set "PYEXE="%%R:\AstraForge\runtime\engine\.venv\Scripts\python.exe""
  )
)
if not defined PYEXE (
  echo Python not found. Install Python 3.12 from python.org and run this again.
  pause
  exit /b 2
)

echo.
echo The studio must be running: the switch is a call to its own API.
echo.
%PYEXE% "%~dp0telemetry_off.py" %ARGS% %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (
  echo Done. The studio applies it immediately, no restart needed.
) else (
  echo The studio did not answer. Start it, or pass the port:  Telemetry-off.cmd --port 3211
)
echo.
pause
exit /b %CODE%
