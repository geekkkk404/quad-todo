@echo off
rem Upload latest portable zip in dist\ to the Gitee release. Prompts for token.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0upload-gitee-release.ps1"
pause
