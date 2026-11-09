@echo off
rem Memory fixes for AstraForge 0.6.12 - applied to the STUDIO, not to the bridge.
rem
rem 1. The page-file message now prints the numbers (RAM, page file, what is left).
rem 2. keep_ram() stops holding 10 GB of SDXL + MV-Adapter in RAM on a 32 GB PC
rem    when the free commit does not allow it.
rem 3. The engine reports commit_free in /system_stats.
rem 4. "Views" check the free memory before the job and say the shortfall.
rem 5. ASTRA_COMFY_LOWVRAM=0|auto switches ComfyUI's memory mode without editing files.
rem
rem Usage:  Apply-memory-patch.cmd              apply
rem         Apply-memory-patch.cmd --check      show what would change, change nothing
rem         Apply-memory-patch.cmd --revert     restore the .bak copies
setlocal
cd /d "%~dp0"
title AstraForge - memory patch for the studio

set "STUDIO="
for %%R in (C D E F) do (
  if not defined STUDIO if exist "%%R:\AstraForge\server\views.mjs" set "STUDIO=%%R:\AstraForge"
)
if not defined STUDIO (
  echo.
  echo Studio not found: no AstraForge\server\views.mjs on C:, D:, E: or F:.
  echo Pass the folder yourself:  Apply-memory-patch.cmd --studio D:\AstraForge
  echo.
  pause
  exit /b 2
)

set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE ( where python >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE if exist "%STUDIO%\runtime\engine\.venv\Scripts\python.exe" set "PYEXE="%STUDIO%\runtime\engine\.venv\Scripts\python.exe""
if not defined PYEXE (
  echo.
  echo Python not found. Install Python 3.12 from python.org and run this again.
  echo.
  pause
  exit /b 2
)

echo.
echo Close the studio first: it keeps server\views.mjs and runtime.mjs in memory.
echo.
%PYEXE% "%~dp0memory_patch.py" --studio "%STUDIO%" %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (
  echo Done. Start the studio again.
  echo The engine reads its settings at start, so the 32 GB RAM rule applies only after that.
) else if "%CODE%"=="1" (
  echo Partly applied: some lines differ in your version ^(see "skipped" above^).
  echo Undo at any time with:  Apply-memory-patch.cmd --revert
) else (
  echo Nothing applied, code %CODE%. See the messages above.
)
echo.
pause
exit /b %CODE%
