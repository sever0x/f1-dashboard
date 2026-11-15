@echo off
title List of recordings
cd /d "%~dp0.."
py -3 tools/race_report.py --list %*
pause
