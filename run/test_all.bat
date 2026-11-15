@echo off
title Tests
cd /d "%~dp0.."
py -3 tools/test_bridge.py
py -3 tools/test_race_report.py
pause
