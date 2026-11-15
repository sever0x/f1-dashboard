@echo off
title Dashboard only, no recording
cd /d "%~dp0.."
py -3 f1_bridge.py --no-record %*
pause
