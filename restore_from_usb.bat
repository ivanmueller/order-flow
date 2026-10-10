@echo off
rem Run on the laptop from the USB copy: copies the project to C:\order-flow (or the folder you name),
rem checks every file, builds .venv and runs a quick test. Usage: restore_from_usb.bat [D:\somewhere\order-flow]
set "HERE=%~dp0"
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=C:\order-flow"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%scripts\restore_from_usb.ps1" -Target "%TARGET%"
pause
