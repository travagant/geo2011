@echo off
rem GTX 1080 Ti = Pascal, compute 6.1. The studio's installer gives every machine
rem with any video card a cu128 torch, and cu128 has no sm_61 kernels: the card is
rem seen, then the first kernel dies with "no kernel image is available".
rem
rem This does two things and hands the wheel work to the tested tool:
rem   1. patches the studio's installer (Install.ps1 and engine.ps1) so a
rem      reinstall keeps using cu126, generation engine included;
rem   2. runs CUDA-fix-torch.cmd, which puts cu126 torch in and proves it on the GPU.
setlocal
cd /d "%~dp0"
title AstraForge bridge - GTX 1080 Ti (Pascal) fix

set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE ( where python >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE for %%R in (C D E) do if not defined PYEXE if exist "%%R:\AstraForge\runtime\ComfyUI\.venv\Scripts\python.exe" set "PYEXE=""%%R:\AstraForge\runtime\ComfyUI\.venv\Scripts\python.exe""
if not defined PYEXE (
  echo.
  echo Python not found. Install Python 3.12 from python.org and tick
  echo "Add python.exe to PATH", or run this on the machine with the studio.
  echo.
  pause
  exit /b 1
)

echo.
echo === 1/2  installer: pick torch by the card, stop calling it too old
%PYEXE% "%~dp0pascal.py" --auto
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo The installer was not patched ^(see above^). Continuing anyway: the wheel
  echo itself is fixed in step 2, this only stops a reinstall from undoing it.
)

echo.
echo === 2/2  torch: install a build that actually contains sm_61
call "%~dp0CUDA-fix-torch.cmd"

rem ComfyUI reads these at start; setx keeps them across reboots and for every
rem process the studio spawns. alloc conf fights fragmentation of the 11 GB,
rem arch list is what any C++ extension must be compiled for on this card.
setx PYTORCH_CUDA_ALLOC_CONF "expandable_segments:True" >nul
setx TORCH_CUDA_ARCH_LIST "6.1" >nul

echo.
echo Done. Variables set for this user: PYTORCH_CUDA_ALLOC_CONF, TORCH_CUDA_ARCH_LIST
echo.
echo Start AstraForge and watch the log: it must say "Total VRAM 11264 MB" and must
echo not say "no kernel image is available".
echo.
echo If the Hunyuan3D texture stage dies with that same message, the compiled
echo rasterizer kernel is the last piece: run  py -3 cuda_check.py fix-rasterizer
echo ^(builds it under TORCH_CUDA_ARCH_LIST=6.1, takes a few minutes^).
echo.
pause
