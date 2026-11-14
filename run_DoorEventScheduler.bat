@echo off
REM Change to the script's directory
cd /D "%~dp0"

REM Run the Python script
echo Starting DoorEventScheduler.py from %CD%...
python "DoorEventScheduler.py"
echo DoorEventScheduler.py has finished or was stopped.
REM pause