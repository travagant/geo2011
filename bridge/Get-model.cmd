@echo off
rem Download the Defiant 9B GGUF (Q4_K_M) from Hugging Face and register it in
rem Ollama, so the studio can pick it in Connections -> Ollama model.
rem
rem Resume: a broken 7 GB download continues where it stopped, just run it again.
rem Verified: size and sha256 are checked before the file is given to Ollama.
rem
rem Usage:  Get-model.cmd              (the preset: defiant = Q4_K_M, 6.98 GB)
rem         Get-model.cmd defiant-xs   (IQ4_XS, 520 MB smaller)
rem         Get-model.cmd <huggingface link> --file name.gguf
setlocal
cd /d "%~dp0"
title AstraForge bridge - download a GGUF model

set "PRESET=%~1"
if "%PRESET%"=="" set "PRESET=defiant"

rem Studio first: its data folder is where models belong, and its venv always
rem has a python even when the system one was never installed.
set "STUDIO="
for %%R in (C D E) do (
  if not defined STUDIO if exist "%%R:\AstraForge\data" set "STUDIO=%%R:\AstraForge"
  if not defined STUDIO if exist "%%R:\AstraForge\runtime" set "STUDIO=%%R:\AstraForge"
)

set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE ( where python >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE if defined STUDIO if exist "%STUDIO%\runtime\ComfyUI\.venv\Scripts\python.exe" set "PYEXE=""%STUDIO%\runtime\ComfyUI\.venv\Scripts\python.exe""
if not defined PYEXE if defined STUDIO if exist "%STUDIO%\runtime\python\python.exe" set "PYEXE=""%STUDIO%\runtime\python\python.exe""
if not defined PYEXE (
  echo.
  echo Python not found. Install Python 3.12 from python.org, or run this from
  echo the machine with the studio.
  echo.
  pause
  exit /b 1
)

echo.
echo model  : %PRESET%
if defined STUDIO echo studio : %STUDIO%
echo.
rem %2..%9 are passed through, so --file / --name / --no-ollama keep working
if defined STUDIO (
  %PYEXE% "%~dp0get_model.py" %PRESET% --studio "%STUDIO%" %2 %3 %4 %5 %6 %7 %8 %9
) else (
  %PYEXE% "%~dp0get_model.py" %PRESET% %2 %3 %4 %5 %6 %7 %8 %9
)
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" (
  echo Not finished (code %CODE%). If it was a broken connection, run this file
  echo again: the partial file is kept and the download continues from there.
  pause
  exit /b %CODE%
)
echo Done. In the studio: Connections -> Ollama model -> pick the name printed above.
echo Keep it unloaded while generating:  setx OLLAMA_KEEP_ALIVE 0
echo.
pause
