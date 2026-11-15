@echo off
title Report: last race (or a dropped file)
cd /d "%~dp0.."
py -3 tools/race_report.py %*
pause
