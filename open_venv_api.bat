@echo off
rem Same as open_venv.bat, then checks the data APIs: the Databento key in .env (never printed),
rem a free Databento call, and the ThetaData Terminal on 127.0.0.1:25503 (started if a jar is found).
set "REPO=%~dp0"
set "REPO=%REPO:~0,-1%"
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-ExecutionPolicy','Bypass','-File','\"%REPO%\scripts\api_session.ps1\"'"
