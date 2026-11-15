@echo off
title Dashboard + race recording
cd /d "%~dp0.."
py -3 f1_bridge.py %*
pause
