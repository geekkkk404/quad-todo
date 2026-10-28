@echo off
rem QuadTodo autostart toggle. All logic (and Chinese text) lives in
rem autostart.ps1 so this cmd file stays pure ASCII - codepage-proof.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1"
pause
