@echo off
rem Double-click this file. It starts the local link server, opens a tunnel and
rem prints the line to paste into the chat. ASCII only: the Russian messages come
rem from link_up.py, which prints them through the Windows console API.
setlocal
cd /d "%~dp0"
title AstraForge - Arena link
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE ( where python >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE if exist "D:\AstraForge\runtime\comfy\.venv\Scripts\python.exe" set PYEXE="D:\AstraForge\runtime\comfy\.venv\Scripts\python.exe"
if not defined PYEXE (
  echo.
  echo Python not found. Install Python 3.10+ from python.org
  echo and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)
echo python: %PYEXE%
echo.
%PYEXE% "%~dp0link_up.py" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo ---- link did not come up, code %RC% ----
  echo report: bridge\.link\last-attempt.txt
) else (
  echo ---- link closed ----
)
pause
