@echo off
title Dashboard + race recording, all packets
cd /d "%~dp0.."
py -3 f1_bridge.py --record-full-packets %*
pause
