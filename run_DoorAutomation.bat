@echo off
cd /D "%~dp0"

REM Run the Python script
echo Starting DoorAutomation.py from %CD%...
python "DoorAutomation.py"
echo DoorAutomation.py has finished or was stopped.

REM pause