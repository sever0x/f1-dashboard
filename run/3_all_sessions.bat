@echo off
title Dashboard + recording of all sessions
cd /d "%~dp0.."
py -3 f1_bridge.py --record-all-sessions %*
pause
