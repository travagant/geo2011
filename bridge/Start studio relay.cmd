@echo off
rem Double-click: the channel for a network where every tunnel dies. Nothing
rem listens on this PC - the program asks a public post box for commands and
rem answers them. ASCII only: Russian text comes from relay.py.
setlocal
cd /d "%~dp0"
set "STUDIO="
if exist "C:\AstraForge\data" set "STUDIO=C:\AstraForge"
if not defined STUDIO if exist "D:\AstraForge\data" set "STUDIO=D:\AstraForge"
if not defined STUDIO if exist "C:\AstraForge\runtime" set "STUDIO=C:\AstraForge"
if not defined STUDIO if exist "D:\AstraForge\runtime" set "STUDIO=D:\AstraForge"
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE ( where python >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE if defined STUDIO if exist "%STUDIO%\runtime\ComfyUI\.venv\Scripts\python.exe" set PYEXE="%STUDIO%\runtime\ComfyUI\.venv\Scripts\python.exe"
if not defined PYEXE if defined STUDIO if exist "%STUDIO%\runtime\comfy\.venv\Scripts\python.exe" set PYEXE="%STUDIO%\runtime\comfy\.venv\Scripts\python.exe"
if not defined PYEXE if defined STUDIO if exist "%STUDIO%\runtime\python\python.exe" set PYEXE="%STUDIO%\runtime\python\python.exe"
if not defined PYEXE (
  echo.
  echo Python not found. Install Python 3.10+ from python.org
  echo and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)
echo studio: %STUDIO%
echo python: %PYEXE%
echo.
title AstraForge - Arena post box
%PYEXE% "%~dp0bridge\relay.py" --studio "%STUDIO%"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo ---- the post box stopped, code %RC% ----
  echo settings: bridge\.link\ntfy.txt
) else (
  echo ---- post box closed ----
)
pause
