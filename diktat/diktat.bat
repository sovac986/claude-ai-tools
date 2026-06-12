@echo off
REM Rucno konzolno pokretanje diktata (za debug). Servisni mod: start_diktat.ps1
REM (automatski se pali kroz Claude Code SessionStart hook).
cd /d %~dp0
python hr_diktat.py
pause
