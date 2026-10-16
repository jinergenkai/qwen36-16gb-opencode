@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-qwen.ps1" -Model orig %*
pause
