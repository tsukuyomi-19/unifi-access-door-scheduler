@echo off
REM Door Unlock Calendar Event Checker
REM This batch file runs the DoorUnlocks.py program and logs execution information

echo =========================================== >> door_unlock_log.txt
echo Door Unlock Script Started: %date% at %time% >> door_unlock_log.txt
echo. >> door_unlock_log.txt

REM Change to the directory where the script is located
cd /d "%~dp0"

REM Run the Python script
echo Running UbiquitiDoorController.py... >> door_unlock_log.txt
python UbiquitiDoorController.py >> door_unlock_log.txt 2>&1

REM Check if the script ran successfully
if %ERRORLEVEL% EQU 0 (
    echo Script executed successfully >> door_unlock_log.txt
) else (
    echo ERROR: Script failed with error code %ERRORLEVEL% >> door_unlock_log.txt
)

echo. >> door_unlock_log.txt
echo Door Unlock Script Finished: %date% at %time% >> door_unlock_log.txt
echo =========================================== >> door_unlock_log.txt
echo. >> door_unlock_log.txt