@echo off
rem Double-click: starts the local studio link and the cloudflared tunnel.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0studio-link.ps1"
