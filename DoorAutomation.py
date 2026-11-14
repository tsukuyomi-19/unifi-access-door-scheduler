import os
import base64
import time
import re
import json
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dateutil import parser as dateutil_parser

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/gmail.modify']
CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar.events']
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE')
CALENDAR_ID_FROM_ENV = os.getenv('CALENDAR_ID')
IMPERSONATED_USER_EMAIL = os.getenv('IMPERSONATED_USER_EMAIL')

SUBJECT_PHRASES_TO_FIND = [
    "(Event Set Up Request) You've Been CC'd on",
    "(Unlock Door Request) You've Been CC'd on"
]

PRIMARY_FIELD_TO_FIND = "Door(s) Unlocked?:"
PRIMARY_VALUE_TO_FIND = "Yes, unlock schedule must be adjusted"

EVENT_SETUP_FIELDS = {
    "event_name": "Event Name:", "doors_to_unlock": "Doors to be Unlocked:",
    "door_unlock_start_time_str": "Door Unlock Start Time:", "door_unlock_end_time_str": "Door Unlock End Time:",
}
UNLOCK_REQUEST_FIELDS = {
    "event_name": "Event Name:", "doors_to_unlock": "Doors Unlocked:",
    "door_unlock_start_time_str": "Door Unlock Start Time:", "door_unlock_end_time_str": "Door Unlock End Time:",
}

ESSENTIAL_PARSED_DETAILS_KEYS = ["doors_to_unlock", "unlock_start_time_utc", "lock_end_time_utc"]
PROCESSED_ACTIONABLE_LABEL_NAME = "PythonProcessedEventMatch"
PROCESSED_NOT_ACTIONABLE_LABEL_NAME = "PythonProcessedEventNotActionable"
LOCAL_TIMEZONE = "America/New_York"
TASKS_FILE_PATH = "door_unlock_tasks.json"

def get_gmail_service():
    creds = None
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', GMAIL_SCOPES)
        except ValueError:
            if os.path.exists('token.json'): os.remove('token.json')
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                if os.path.exists('token.json'): os.remove('token.json')
                creds = None
        if not creds:
            if not os.path.exists('client_secret.json'): return None
            flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    try:
        service = build('gmail', 'v1', credentials=creds)
        print("Gmail service initialized successfully.")
        return service
    except Exception as e:
        print(f'An error occurred building the Gmail service: {e}')
    return None

def get_calendar_service():
    if not SERVICE_ACCOUNT_FILE:
        print("INFO: SERVICE_ACCOUNT_FILE not set. Calendar features disabled.")
        return None
    resolved_path = os.path.abspath(os.path.expanduser(SERVICE_ACCOUNT_FILE))
    if not os.path.exists(resolved_path):
        print(f"ERROR: Service account key file not found: '{resolved_path}'.")
        return None
    try:
        creds = service_account.Credentials.from_service_account_file(
            resolved_path, scopes=CALENDAR_SCOPES, subject=IMPERSONATED_USER_EMAIL
        ) if IMPERSONATED_USER_EMAIL else service_account.Credentials.from_service_account_file(
            resolved_path, scopes=CALENDAR_SCOPES
        )
        service = build('calendar', 'v3', credentials=creds)
        print("Google Calendar service initialized successfully.")
        return service
    except Exception as e:
        print(f"ERROR authenticating Calendar service: {e}")
    return None

def get_label_id_by_name(gmail_service, label_name):
    try:
        results = gmail_service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])
        for label in labels:
            if label['name'] == label_name: return label['id']
        label_body = {'name': label_name, 'labelListVisibility': 'labelShow', 'messageListVisibility': 'show'}
        created_label = gmail_service.users().labels().create(userId='me', body=label_body).execute()
        return created_label['id']
    except HttpError as e:
        print(f'Error with label {label_name}: {e}')
    return None

def search_emails(gmail_service, actionable_label_name, not_actionable_label_name):
    subjects_or_query = " OR ".join([f'"{phrase}"' for phrase in SUBJECT_PHRASES_TO_FIND])
    query = f'subject:({subjects_or_query}) -label:"{actionable_label_name}" -label:"{not_actionable_label_name}"'
    print(f"DEBUG: Gmail search query: \"{query}\"")
    try:
        result = gmail_service.users().messages().list(userId='me', q=query, maxResults=50).execute()
        return result.get('messages', [])
    except HttpError as e:
        print(f'Error during email search: {e}')
    return []

def get_email_body(msg_parts):
    plain_text_content = ""
    html_derived_text_content = ""
    if not msg_parts: return ""
    for part in msg_parts:
        mime_type = part.get('mimeType', '')
        if mime_type == 'text/plain' and not part.get('filename'):
            if 'data' in part.get('body', {}):
                plain_text_content = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='replace')
        elif mime_type == 'text/html' and not part.get('filename'):
            if 'data' in part.get('body', {}):
                soup = BeautifulSoup(base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='replace'), "html.parser")
                html_derived_text_content = soup.get_text(separator='\n', strip=True)
        elif mime_type.startswith('multipart/'):
            return get_email_body(part.get('parts', []))
    return html_derived_text_content or plain_text_content

def mark_email_processed(gmail_service, message_id, label_id, label_name):
    try:
        gmail_service.users().messages().modify(userId='me', id=message_id, body={'addLabelIds': [label_id]}).execute()
        print(f"Email {message_id} marked with label: '{label_name}'.")
    except HttpError as e:
        print(f'Error marking email {message_id} with label {label_name}: {e}')

def load_tasks():
    if not os.path.exists(TASKS_FILE_PATH): return []
    try:
        with open(TASKS_FILE_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            return json.loads(content) if content.strip() else []
    except Exception: return []

def save_tasks(tasks):
    try:
        with open(TASKS_FILE_PATH, 'w', encoding='utf-8') as f: json.dump(tasks, f, indent=4)
    except Exception as e: print(f"Error saving tasks: {e}")

# In DoorAutomation.py, replace this entire function

def generate_event_signature(details):
    """
    Generates a simple signature for an event to help with de-duplication.
    This version safely handles cases where details might be None.
    """
    # Safely get event_name, default to "N/A" if it's missing or None
    event_name = details.get("event_name")
    if not event_name:
        event_name = "N/A"
    
    # Safely get doors_to_unlock, default to "N/A" if it's missing or None
    doors = details.get("doors_to_unlock")
    if not doors:
        doors = "N/A"

    # Use UTC start time for signature as it's standardized
    start_utc = details.get("unlock_start_time_utc", "N/A")
    
    # Now that we're sure we have strings, we can safely call .lower() and .strip()
    return f"{event_name.lower().strip()}|{doors.lower().strip()}|{start_utc}"

def add_task_if_new(new_task_details):
    tasks = load_tasks()
    new_task_signature = generate_event_signature(new_task_details)
    for task in tasks:
        if generate_event_signature(task) == new_task_signature and task.get("status") == "pending_unlock":
            print(f"  INFO: Active task with signature '{new_task_signature}' already exists. Skipping duplicate.")
            return False
    task_id = uuid.uuid4().hex
    task_entry = {
        "task_id": task_id, "source_email_id": new_task_details.get("source_email_id"),
        "subject": new_task_details.get("subject"), "event_name": new_task_details.get("event_name"),
        "doors_to_unlock": new_task_details.get("doors_to_unlock"),
        "unlock_start_time_utc": new_task_details.get("unlock_start_time_utc"),
        "lock_end_time_utc": new_task_details.get("lock_end_time_utc"),
        "unlock_start_time_local_iso": new_task_details.get("unlock_start_time_local_iso"),
        "lock_end_time_local_iso": new_task_details.get("lock_end_time_local_iso"),
        "status": "pending_unlock", "added_timestamp_utc": datetime.now(timezone.utc).isoformat()
    }
    tasks.append(task_entry)
    save_tasks(tasks)
    print(f"  SUCCESS: New task '{task_id}' for event '{task_entry.get('event_name', 'N/A')}' added to schedule.")
    return True

def create_google_calendar_event(calendar_service, details):
    if not calendar_service or not CALENDAR_ID_FROM_ENV: return False
    start_time = details.get("unlock_start_time_local_iso")
    end_time = details.get("lock_end_time_local_iso")
    if not start_time or not end_time: return False
    event_body = {
        'summary': f"DOOR UNLOCK: {details.get('event_name', 'Scheduled Event')}",
        'description': f"Automated door unlock schedule.\nDoors: {details.get('doors_to_unlock', 'N/A')}\nSource Email Subject: {details.get('subject', 'N/A')}",
        'start': {'dateTime': start_time, 'timeZone': LOCAL_TIMEZONE},
        'end': {'dateTime': end_time, 'timeZone': LOCAL_TIMEZONE},
    }
    try:
        calendar_service.events().insert(calendarId=CALENDAR_ID_FROM_ENV, body=event_body).execute()
        print(f"  SUCCESS: Calendar event created for '{event_body['summary']}'.")
        return True
    except HttpError as e:
        print(f"  ERROR creating calendar event: {e}")
        if "forbidden" in str(e).lower() or "notfound" in str(e).lower():
            print(f"  HINT: Check that CALENDAR_ID is correct and that the service account has 'Make changes to events' permission on it.")
    return False

def process_email(message_id, gmail_service):
    try:
        msg = gmail_service.users().messages().get(userId='me', id=message_id, format='full').execute()
        payload = msg.get('payload', {})
        subject_header = next((h['value'] for h in payload.get('headers', []) if h['name'].lower() == 'subject'), "No Subject")
        
        print(f"\n--- Processing Message ID: {message_id} ---")
        
        body_content = get_email_body(payload.get('parts', []))
        if not body_content: return None

        normalized_body_lines = [line.strip() for line in body_content.splitlines() if line.strip()]
        primary_condition_met = False
        fields_to_use = None
        
        is_unlock_door_request = "Unlock Door Request".lower() in subject_header.lower()

        if is_unlock_door_request:
            print("  INFO: Detected 'Unlock Door Request' form type.")
            fields_to_use = UNLOCK_REQUEST_FIELDS
            primary_condition_met = True
        else:
            print("  INFO: Detected 'Event Set Up Request' form type.")
            fields_to_use = EVENT_SETUP_FIELDS
            for i, line in enumerate(normalized_body_lines):
                if line.lower().startswith(PRIMARY_FIELD_TO_FIND.lower()):
                    if i + 1 < len(normalized_body_lines) and ' '.join(normalized_body_lines[i + 1].lower().split()) == ' '.join(PRIMARY_VALUE_TO_FIND.lower().split()):
                        primary_condition_met = True
                        print(f"  SUCCESS: Door unlock condition MET.")
                        break
        
        if not primary_condition_met:
            print(f"  INFO: Email did not meet door unlock criteria for its form type.")
            return None

        extracted_details = {"source_email_id": message_id, "subject": subject_header}
        for i, line in enumerate(normalized_body_lines):
            for key, field_name in fields_to_use.items():
                if line.lower().startswith(field_name.lower()):
                    if i + 1 < len(normalized_body_lines):
                        extracted_details[key] = normalized_body_lines[i + 1].strip()
                        break
        
        try:
            start_str = extracted_details.get("door_unlock_start_time_str")
            end_str = extracted_details.get("door_unlock_end_time_str")
            if start_str and end_str:
                local_tz = ZoneInfo(LOCAL_TIMEZONE)
                start_dt = dateutil_parser.parse(start_str).astimezone(local_tz)
                end_dt = dateutil_parser.parse(end_str).astimezone(local_tz)
                extracted_details["unlock_start_time_utc"] = start_dt.astimezone(timezone.utc).isoformat()
                extracted_details["lock_end_time_utc"] = end_dt.astimezone(timezone.utc).isoformat()
                extracted_details["unlock_start_time_local_iso"] = start_dt.isoformat()
                extracted_details["lock_end_time_local_iso"] = end_dt.isoformat()
        except Exception as e:
            print(f"    ERROR: Could not parse date/time strings: {e}")

        missing_keys = [k for k in ESSENTIAL_PARSED_DETAILS_KEYS if not extracted_details.get(k)]
        if not missing_keys:
            print(f"  SUCCESS: All essential details extracted and parsed.")
            return extracted_details
        else:
            print(f"  WARNING: Email was missing essential details after parsing: {missing_keys}.")
            return None

    except Exception as e:
        print(f'An unexpected error during email processing: {e}')
        return None

def print_startup_messages(actionable_label_id, not_actionable_label_id, calendar_service_available):
    print(f"\n--- Script Configuration ---")
    print(f"Searching for emails with subject phrases:")
    for phrase in SUBJECT_PHRASES_TO_FIND:
        print(f"  - \"{phrase}\"")
    print(f"Door unlock check (for Event Set Up Requests): '{PRIMARY_FIELD_TO_FIND}' -> '{PRIMARY_VALUE_TO_FIND}'")
    if calendar_service_available and CALENDAR_ID_FROM_ENV: 
        print(f"New events will be added to Calendar ID: '{CALENDAR_ID_FROM_ENV}'")
    else: 
        print(f"WARNING: Calendar event creation is disabled.")
    print(f"Actionable emails marked with: '{PROCESSED_ACTIONABLE_LABEL_NAME}' (ID: {actionable_label_id})")
    print(f"Non-actionable emails marked with: '{PROCESSED_NOT_ACTIONABLE_LABEL_NAME}' (ID: {not_actionable_label_id})")
    print(f"----------------------------")

def main():
    print(f"[{datetime.now(ZoneInfo(LOCAL_TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S %Z')}] Starting scheduled run...")
    gmail_service = get_gmail_service()
    if not gmail_service: return
    
    calendar_service = get_calendar_service()
    
    actionable_label_id = get_label_id_by_name(gmail_service, PROCESSED_ACTIONABLE_LABEL_NAME)
    not_actionable_label_id = get_label_id_by_name(gmail_service, PROCESSED_NOT_ACTIONABLE_LABEL_NAME)
    
    print_startup_messages(actionable_label_id, not_actionable_label_id, calendar_service)
    
    run_actionable_email_count = 0
    run_new_tasks_scheduled_count = 0
    run_processed_not_actionable_count = 0

    try:
        messages_to_check = search_emails(gmail_service, PROCESSED_ACTIONABLE_LABEL_NAME, PROCESSED_NOT_ACTIONABLE_LABEL_NAME)
        if not messages_to_check:
            print("No new, unprocessed emails found.")
        else:
            print(f"Found {len(messages_to_check)} new email(s) to process...")
            for item in messages_to_check:
                details = process_email(item['id'], gmail_service)
                if details:
                    run_actionable_email_count += 1
                    if add_task_if_new(details):
                        run_new_tasks_scheduled_count += 1
                        create_google_calendar_event(calendar_service, details)
                    mark_email_processed(gmail_service, item['id'], actionable_label_id, PROCESSED_ACTIONABLE_LABEL_NAME)
                else:
                    run_processed_not_actionable_count += 1
                    mark_email_processed(gmail_service, item['id'], not_actionable_label_id, PROCESSED_NOT_ACTIONABLE_LABEL_NAME)
                time.sleep(1)
    except Exception as e:
        print(f"CRITICAL ERROR during main execution: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print(f"\n--- Run Summary ---")
        print(f"  Actionable Emails Found: {run_actionable_email_count}")
        print(f"  New Tasks Scheduled: {run_new_tasks_scheduled_count}")
        print(f"  Processed (Not Actionable): {run_processed_not_actionable_count}")
        print(f"[{datetime.now(ZoneInfo(LOCAL_TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S %Z')}] Run finished.")

if __name__ == '__main__':
    main()