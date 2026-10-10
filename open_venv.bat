@echo off
rem Opens PowerShell as administrator in this repo with the .venv activated.
rem Keep this file in the repo root (C:\order-flow). Double-click it; accept the UAC prompt.
set "REPO=%~dp0"
set "REPO=%REPO:~0,-1%"
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-ExecutionPolicy','Bypass','-File','\"%REPO%\scripts\venv_session.ps1\"'"
