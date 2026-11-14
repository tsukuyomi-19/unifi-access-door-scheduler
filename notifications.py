# notifications.py
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

# --- Email Configuration (from .env file) ---
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD") # IMPORTANT: Must be a Google App Password
SUCCESS_RECIPIENTS_STR = os.getenv("SUCCESS_RECIPIENTS")
FAILURE_RECIPIENTS_STR = os.getenv("FAILURE_RECIPIENTS")

def send_notification(subject, body, recipients_str):
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        print("ERROR (Notification): EMAIL_SENDER or EMAIL_PASSWORD not set in .env file. Cannot send email.")
        return False
    if not recipients_str:
        print(f"INFO (Notification): No recipients specified for subject '{subject}'. Skipping email.")
        return False

    recipients = [email.strip() for email in recipients_str.split(',') if email.strip()]
    if not recipients:
        print(f"INFO (Notification): No valid recipients after parsing '{recipients_str}'. Skipping email.")
        return False

    msg = MIMEMultipart()
    msg['From'] = EMAIL_SENDER
    msg['To'] = ", ".join(recipients)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        print(f"Attempting to send email notification to: {recipients_str} with subject: {subject}")
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        print("SUCCESS (Notification): Email sent successfully.")
        return True
    except Exception as e:
        print(f"CRITICAL ERROR (Notification): Failed to send email. Error: {e}")
        return False

def send_success_email(event_name, doors, duration_minutes):
    subject = f"✅ Success: Doors Unlocked for Event '{event_name}'"
    body = (
        f"This is an automated notification from the Door Event Automation system.\n\n"
        f"The following doors were successfully unlocked for the event: '{event_name}'\n\n"
        f"  - Doors: {doors}\n"
        f"  - Unlocked for a duration of: {duration_minutes} minutes\n\n"
        f"The unlock is temporary and will expire automatically."
    )
    send_notification(subject, body, SUCCESS_RECIPIENTS_STR)

def send_failure_email(task, error_message):
    event_name = task.get("event_name", "Unknown Event")
    doors = task.get("doors_to_unlock", "N/A")
    task_id = task.get("task_id", "N/A")
    subject = f"❌ FAILURE: Door Unlock Failed for Event '{event_name}'"
    body = (
        f"This is an automated alert from the Door Event Automation system.\n\n"
        f"The system FAILED to unlock doors for the event: '{event_name}'.\n\n"
        f"  - Doors: {doors}\n"
        f"  - Scheduled Start (UTC): {task.get('unlock_start_time_utc', 'N/A')}\n"
        f"  - Task ID: {task_id}\n\n"
        f"--- Error Details ---\n"
        f"{error_message}\n\n"
        f"Please check the system logs for more information and manually verify the door status."
    )
    send_notification(subject, body, FAILURE_RECIPIENTS_STR)