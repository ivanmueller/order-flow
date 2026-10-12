@echo off
rem Copies this project (code, docs, every data folder, the spend ledger) to a USB drive, default E:
rem Double-click it, or from a prompt name another drive: backup_to_usb.bat F:
set "REPO=%~dp0"
set "DRIVE=%~1"
if "%DRIVE%"=="" set "DRIVE=E:"
powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO%scripts\backup_to_usb.ps1" -Drive %DRIVE%
pause
