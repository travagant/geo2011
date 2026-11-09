@echo off
rem fix-torch -- see bridge\\README.md ("CUDA: why the studio says no kernel image")
setlocal
cd /d "%~dp0"
set "STUDIO=D:\AstraForge"
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE ( where python >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE if exist "%STUDIO%\runtime\comfy\.venv\Scripts\python.exe" set PYEXE="%STUDIO%\runtime\comfy\.venv\Scripts\python.exe"
if not defined PYEXE (
  echo.
  echo Python not found. Install Python 3.10+ from python.org,
  echo tick "Add python.exe to PATH", run this file again.
  echo.
  pause
  exit /b 1
)
%PYEXE% "%~dp0cuda_check.py" fix-torch --studio "%STUDIO%" 
echo.
echo ---- done, code %ERRORLEVEL% ----
pause
