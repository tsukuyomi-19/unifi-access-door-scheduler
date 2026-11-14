# DoorEventScheduler.py

import os
import json
import time
import subprocess
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import math
import traceback
from notifications import send_success_email, send_failure_email

# --- Script Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
TASKS_FILE_PATH = os.path.join(SCRIPT_DIR, "door_unlock_tasks.json")
PATH_TO_DOOR_CONTROLLER_SCRIPT = os.path.join(SCRIPT_DIR, "UbiquitiDoorController.py")
PYTHON_EXECUTABLE = "python"
CHECK_INTERVAL_SCHEDULER_SECONDS = 60
UNLOCK_LEAD_TIME_MINUTES = 5
LOG_FILE_SCHEDULER = os.path.join(LOG_DIR, f'door_event_scheduler_{datetime.now().strftime("%Y%m%d")}.log')
LOCAL_TIMEZONE_FOR_SHUTDOWN = "America/New_York"
SHUTDOWN_HOUR_LOCAL = 22

def log_message(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"{timestamp} - {level} - {message}\n"
    print(full_message.strip())
    try:
        with open(LOG_FILE_SCHEDULER, "a", encoding="utf-8") as f:
            f.write(full_message)
    except Exception as e:
        print(f"CRITICAL: Error writing to log file {LOG_FILE_SCHEDULER}: {e}")

def load_tasks():
    if not os.path.exists(TASKS_FILE_PATH):
        return []
    try:
        with open(TASKS_FILE_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip(): return []
            tasks = json.loads(content)
        if not isinstance(tasks, list):
            save_tasks([])
            return []
        return tasks
    except (json.JSONDecodeError, FileNotFoundError):
        return []
    except Exception as e:
        log_message(f"Error loading tasks: {e}", "ERROR")
        return []

def save_tasks(tasks):
    try:
        with open(TASKS_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, indent=4)
    except Exception as e:
        log_message(f"Error saving tasks: {e}", "ERROR")

def call_door_unlock_script(action, doors_str, duration_minutes_str):
    cmd = [PYTHON_EXECUTABLE, PATH_TO_DOOR_CONTROLLER_SCRIPT, "--action", action, "--doors", doors_str, "--duration_minutes", duration_minutes_str]
    log_message(f"DEBUG: Executing command: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=420)
        stdout = (result.stdout[:1000] + '...').strip() if len(result.stdout) > 1000 else result.stdout.strip()
        stderr = (result.stderr[:1000] + '...').strip() if len(result.stderr) > 1000 else result.stderr.strip()
        if stdout: log_message(f"DEBUG: Door controller stdout:\n{stdout}")
        if stderr: log_message(f"DEBUG: Door controller stderr:\n{stderr}")

        if result.returncode == 0:
            return True, "Ubiquiti script reported success."
        else:
            return False, f"Error Code {result.returncode}:\n{stderr or stdout}"
    except Exception as e:
        log_message(f"ERROR: Exception executing door controller script: {e}", "ERROR")
        return False, str(e)

def run_scheduler():
    log_message("DoorEventScheduler started.")
    log_message(f"Monitoring task file: {TASKS_FILE_PATH}")
    log_message(f"Unlock lead time: {UNLOCK_LEAD_TIME_MINUTES} minutes.")
    log_message(f"Nightly shutdown scheduled for {SHUTDOWN_HOUR_LOCAL}:00 {LOCAL_TIMEZONE_FOR_SHUTDOWN}.")

    if not os.path.exists(PATH_TO_DOOR_CONTROLLER_SCRIPT):
        log_message(f"CRITICAL ERROR: Selenium script not found at '{PATH_TO_DOOR_CONTROLLER_SCRIPT}'.", "CRITICAL")
        send_failure_email(
            {"event_name": "Scheduler Startup"},
            f"Scheduler cannot start because UbiquitiDoorController.py was not found at:\n{PATH_TO_DOOR_CONTROLLER_SCRIPT}"
        )
        return

    while True:
        try:
            log_message("Scheduler check cycle starting...")
            tasks = load_tasks()
            if not tasks:
                log_message("No tasks currently in schedule.")

            now_utc = datetime.now(timezone.utc)
            tasks_modified = False

            for task in tasks:
                try:
                    if task.get("status") == "pending_unlock":
                        unlock_start_utc = datetime.fromisoformat(task.get("unlock_start_time_utc").replace("Z", "+00:00"))
                        trigger_unlock_time = unlock_start_utc - timedelta(minutes=UNLOCK_LEAD_TIME_MINUTES)

                        if now_utc >= trigger_unlock_time:
                            event_name = task.get("event_name", "N/A")
                            doors = task.get("doors_to_unlock", "N/A")
                            task_id = task.get("task_id", "N/A")
                            lock_end_utc = datetime.fromisoformat(task.get("lock_end_time_utc").replace("Z", "+00:00"))

                            log_message(f"ACTION: Triggering UNLOCK for task {task_id} ('{event_name}').")

                            if now_utc > lock_end_utc + timedelta(minutes=1):
                                task["status"] = "missed_window"
                                log_message(f"WARNING: Task {task_id} missed its window.", "WARNING")
                                tasks_modified = True
                                continue

                            duration_seconds = (lock_end_utc - unlock_start_utc).total_seconds()
                            duration_minutes = math.ceil(duration_seconds / 60.0)

                            if duration_minutes <= 0:
                                error_msg = f"Calculated duration is non-positive ({duration_minutes} mins)."
                                task["status"] = "error_invalid_duration"
                                task["error_message"] = error_msg
                                send_failure_email(task, error_msg)
                                tasks_modified = True
                                continue
                            
                            log_message(f"Calculated duration for task {task_id}: {duration_minutes} minutes.")
                            success, message = call_door_unlock_script("unlock", doors, str(duration_minutes))

                            if success:
                                log_message(f"SUCCESS: Unlock command for task {task_id} succeeded.")
                                send_success_email(event_name, doors, duration_minutes)
                                task["status"] = "completed"
                            else:
                                log_message(f"ERROR: Unlock command for task {task_id} failed.", "ERROR")
                                send_failure_email(task, message)
                                task["status"] = "unlock_error"
                                task["error_message"] = message
                            
                            task["last_action_timestamp_utc"] = now_utc.isoformat()
                            tasks_modified = True
                except Exception as e:
                    task_id = task.get("task_id", "Unknown")
                    full_error = f"Critical error processing task {task_id}:\n\n{traceback.format_exc()}"
                    log_message(full_error, "CRITICAL")
                    task["status"] = "processing_error"
                    task["error_message"] = str(e)
                    send_failure_email(task, full_error)
                    tasks_modified = True
            
            if tasks_modified:
                save_tasks(tasks)
                log_message("Task statuses updated.")
            
            tz_local = ZoneInfo(LOCAL_TIMEZONE_FOR_SHUTDOWN)
            if datetime.now(tz_local).hour >= SHUTDOWN_HOUR_LOCAL:
                log_message(f"Shutdown hour ({SHUTDOWN_HOUR_LOCAL}:00) reached. Exiting.")
                break 

        except Exception as e_loop:
            log_message(f"FATAL ERROR IN SCHEDULER'S MAIN LOOP: {e_loop}", "CRITICAL")
            traceback.print_exc()
        
        log_message(f"Scheduler cycle complete. Waiting {CHECK_INTERVAL_SCHEDULER_SECONDS}s.")
        time.sleep(CHECK_INTERVAL_SCHEDULER_SECONDS)
    
    log_message("DoorEventScheduler has shut down gracefully.")

if __name__ == "__main__":
    if not os.path.exists(TASKS_FILE_PATH):
        save_tasks([])
    run_scheduler()