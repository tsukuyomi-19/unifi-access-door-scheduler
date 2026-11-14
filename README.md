# UniFi Access Door Scheduler

Automates temporary door unlocks in **UniFi Access** from emailed event and unlock requests.

## How it works

1. **`DoorAutomation.py`** watches a Gmail inbox for form-notification emails (event set-up and unlock requests), parses the event name, doors and unlock window, queues an unlock task in `door_unlock_tasks.json`, and logs it to a Google Calendar. Each email is labeled once it's handled so nothing is processed twice.
2. **`DoorEventScheduler.py`** reads the task queue and, when an unlock window begins, calls the controller with the doors and duration.
3. **`UbiquitiDoorController.py`** signs in to the UniFi web console with Selenium, opens the Access doors page, and applies a **Custom Unlock Duration** temporary unlock to each requested door, retrying on failure.
4. **`notifications.py`** emails success and failure reports.

## Setup

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # then fill in your values
```

- Create a Google Cloud service account with Gmail/Calendar access (domain-wide delegation if impersonating a user) and point `SERVICE_ACCOUNT_FILE` at its key.
- Set `DOOR_NAMES` to the door names exactly as they appear in UniFi Access.
- Requires Google Chrome; `webdriver-manager` handles ChromeDriver.

The `run_*.bat` files launch each component on Windows (e.g. from Task Scheduler).

No credentials are stored in the code; everything sensitive comes from `.env`.
