@echo off
title Report: last session of any type
cd /d "%~dp0.."
py -3 tools/race_report.py --any %*
pause
