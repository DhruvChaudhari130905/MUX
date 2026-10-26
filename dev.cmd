@echo off
rem MUX launcher for Windows, no WSL needed.
rem   dev.cmd                 start the API (:8000) and the web app (:3000) after first-run setup
rem   dev.cmd -Demo           web app only, demo mode (no backend, no API keys)
rem   dev.cmd -ServerOnly / -WebOnly / -SetupOnly / -SkipSetup   see scripts\dev.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev.ps1" %*
