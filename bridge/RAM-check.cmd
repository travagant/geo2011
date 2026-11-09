@echo off
rem Windows said "os error 1455: the paging file is too small". That is the commit
rem limit (RAM + page file), not free RAM: Windows charges a model against it before
rem the file is read, so RAM can look free while the allocation is refused.
rem
rem This file prints the numbers for "Views" (MV-Adapter: SDXL 6.6 GB + adapter
rem 3.4 GB in RAM), offers to unload Ollama, and can raise the page file.
rem
rem Usage:  RAM-check.cmd                 verdict for Views
rem         RAM-check.cmd --need 14000    any other task, MB it loads
rem         RAM-check.cmd --json          the numbers for a script
rem
rem With 200 GB free on a disk, option 4 (32 GB page file) is the safe choice: the
rem commit limit becomes RAM + 32 GB, and no task can run out of it.
setlocal
cd /d "%~dp0"
title AstraForge bridge - memory check (os error 1455)

set "STUDIO="
for %%R in (C D E) do (
  if not defined STUDIO if exist "%%R:\AstraForge\data" set "STUDIO=%%R:\AstraForge"
  if not defined STUDIO if exist "%%R:\AstraForge\runtime" set "STUDIO=%%R:\AstraForge"
)

set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE ( where python >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE if defined STUDIO if exist "%STUDIO%\runtime\engine\.venv\Scripts\python.exe" set "PYEXE="%STUDIO%\runtime\engine\.venv\Scripts\python.exe""
if not defined PYEXE if defined STUDIO if exist "%STUDIO%\runtime\ComfyUI\.venv\Scripts\python.exe" set "PYEXE="%STUDIO%\runtime\ComfyUI\.venv\Scripts\python.exe""
if not defined PYEXE (
  echo Python not found. Install Python 3.12 from python.org, or run this from
  echo the machine with the studio.
  pause
  exit /b 2
)

echo.
%PYEXE% "%~dp0ram_check.py" %*
set "CODE=%ERRORLEVEL%"
if "%CODE%"=="1" goto menu
if "%CODE%"=="0" echo.
pause
exit /b %CODE%

:menu
echo.
choice /c 12345q /n /m "1 unload Ollama | 2 automatic page file | 3 by calculation | 4 page file 32 GB | 5 studio tuning | q quit: "
if errorlevel 6 goto quit
if errorlevel 5 goto tune
if errorlevel 4 goto big
if errorlevel 3 goto fixed
if errorlevel 2 goto auto
if errorlevel 1 goto free

:free
echo.
%PYEXE% "%~dp0ram_check.py" --free-ollama
goto menu

:auto
echo.
echo Asking for administrator rights: Windows will size the page file itself.
set "ENC="
for /f "usebackq delims=" %%E in (`%PYEXE% "%~dp0ram_check.py" --pagefile-auto`) do set "ENC=%%E"
if not defined ENC goto hand
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-EncodedCommand',%ENC%"
echo.
echo After the administrator window closes, restart Windows: the size applies on boot.
goto menu

:fixed
echo.
echo Asking for administrator rights: a page file sized to the shortfall.
set "ENC="
for /f "usebackq delims=" %%E in (`%PYEXE% "%~dp0ram_check.py" --pagefile-fixed`) do set "ENC=%%E"
if not defined ENC goto hand
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-EncodedCommand',%ENC%"
echo.
echo After the administrator window closes, restart Windows: the size applies on boot.
goto menu

:big
echo.
echo Asking for administrator rights: a fixed 32 GB page file on the disk with most room.
echo With room on the disk this is the safe choice: commit limit = RAM + 32 GB.
set "ENC="
for /f "usebackq delims=" %%E in (`%PYEXE% "%~dp0ram_check.py" --pagefile-fixed 32768`) do set "ENC=%%E"
if not defined ENC goto hand
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-EncodedCommand',%ENC%"
echo.
echo After the administrator window closes, restart Windows: the size applies on boot.
goto menu

:hand
echo Could not work out the command. Change the page file by hand:
echo   Win+R, sysdm.cpl, Advanced, Performance Settings, Advanced, Virtual memory.
goto menu

:tune
echo.
echo The engine keeps weights in RAM between jobs when the PC has 32 GB or more,
echo and Ollama holds a model for five minutes after the last answer. Both off:
setx ASTRA_ENGINE_KEEP_RAM 0 >nul && echo   ASTRA_ENGINE_KEEP_RAM = 0
setx OLLAMA_KEEP_ALIVE 0 >nul && echo   OLLAMA_KEEP_ALIVE = 0
echo.
echo Restart the studio: the engine reads these variables when it starts.
goto menu

:quit
echo.
echo To keep chat models out of memory while generating:  setx OLLAMA_KEEP_ALIVE 0
echo.
pause
exit /b 0
