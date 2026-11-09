@echo off
rem Double-click: find out which torch or extension lacks Pascal. Writes cuda-report.txt next to this file.
setlocal
cd /d "%~dp0"
rem The studio may be on C: or D: - take whichever actually has data or runtime.
set "STUDIO="
if exist "C:\AstraForge\data" set "STUDIO=C:\AstraForge"
if not defined STUDIO if exist "D:\AstraForge\data" set "STUDIO=D:\AstraForge"
if not defined STUDIO if exist "C:\AstraForge\runtime" set "STUDIO=C:\AstraForge"
if not defined STUDIO if exist "D:\AstraForge\runtime" set "STUDIO=D:\AstraForge"
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE ( where python >nul 2>&1 && set "PYEXE=python" )
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
%PYEXE% "%~dp0cuda_check.py" check --report "%~dp0cuda-report.txt"
echo.
echo ---- done, code %ERRORLEVEL% ----
pause
