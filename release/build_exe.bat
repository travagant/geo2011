@echo off
rem Build the standalone d3tool.exe with PyInstaller (installs it if missing).
rem Double-click or run from a terminal; needs any Python 3.8+ on PATH.
setlocal
cd /d "%~dp0.."
where py >nul 2>nul && (py -3 release\build_exe.py %*) || (python release\build_exe.py %*)
