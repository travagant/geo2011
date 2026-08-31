@echo off
rem Build the standalone d3tool.exe with PyInstaller (installs it if missing).
rem Double-click or run from a terminal; needs any Python 3.8+ on PATH.
setlocal
cd /d "%~dp0.."
rem Not a `&& ... ||` chain: that would re-run the build whenever it exits
rem non-zero, so a failed PyInstaller run would be attempted twice.
set "D3PY="
where py >nul 2>nul && set "D3PY=py -3"
if not defined D3PY (
    where python >nul 2>nul && set "D3PY=python"
)
if not defined D3PY (
    echo build_exe: no Python 3 found on PATH 1>&2
    exit /b 9009
)
%D3PY% release\build_exe.py %*
exit /b %ERRORLEVEL%
