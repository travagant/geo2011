@echo off
rem Double-click. Find out which torch or extension has no code for this GPU (GTX 1080 Ti = Pascal = sm_61).
setlocal
cd /d "%~dp0"
title AstraForge bridge - CUDA check

rem Find the studio: a folder is the studio only if it has data or runtime inside.
rem The name proves nothing - D:\AstraForge can be the unzipped bridge on its own.
set "STUDIO="
for %%R in (C D E) do (
  if not defined STUDIO if exist "%%R:\AstraForge\data" set "STUDIO=%%R:\AstraForge"
  if not defined STUDIO if exist "%%R:\AstraForge\runtime" set "STUDIO=%%R:\AstraForge"
  if not defined STUDIO if exist "%%R:\Program Files\AstraForge\data" set "STUDIO=%%R:\Program Files\AstraForge"
)

rem Python that runs this helper. The studio's own venv works if nothing else does.
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE ( where python >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE if defined STUDIO if exist "%STUDIO%\runtime\ComfyUI\.venv\Scripts\python.exe" set PYEXE="%STUDIO%\runtime\ComfyUI\.venv\Scripts\python.exe"
if not defined PYEXE if defined STUDIO if exist "%STUDIO%\runtime\comfy\.venv\Scripts\python.exe" set PYEXE="%STUDIO%\runtime\comfy\.venv\Scripts\python.exe"
if not defined PYEXE if defined STUDIO if exist "%STUDIO%\runtime\python\python.exe" set PYEXE="%STUDIO%\runtime\python\python.exe"
if not defined PYEXE (
  echo.
  echo Python not found. Install Python 3.12 from python.org
  echo and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)
echo helper python: %PYEXE%
echo The studio itself is searched for in the next step, on C:, D: and E:.
echo.
%PYEXE% "%~dp0cuda_check.py" check --report "%~dp0cuda-report.txt"
set "CODE=%ERRORLEVEL%"
echo.
echo ---- done, code %CODE% ----
echo Report written to %~dp0cuda-report.txt
echo If it says the fix did not help, send that file over.
echo.
pause
