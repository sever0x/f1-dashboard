@echo off
title Dashboard + all sessions, all packets
cd /d "%~dp0.."
py -3 f1_bridge.py --record-all-sessions --record-full-packets %*
pause
