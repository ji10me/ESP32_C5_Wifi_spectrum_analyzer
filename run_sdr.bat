@echo off
rem ESP32-C5 Wi-Fi SDR (Fluent UI). Optional: COM port, --freq MHz, --sweep 2.4^|5, --samples N, --light, --lang ja^|en
cd /d "%~dp0"
python sdr_fluent.py %*
