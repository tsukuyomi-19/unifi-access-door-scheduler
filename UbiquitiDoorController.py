"""
Ubiquiti Door Controller (Selenium-based)

This program logs into a Ubiquiti website and applies temporary door unlocks
based on command-line arguments. Expects duration in minutes.
It's intended to be called by a scheduler script.

All sensitive information (passwords, usernames, URLs) must be set in the .env file.
"""

import os
import sys
import time
import logging
import traceback
import argparse
from datetime import datetime
from dotenv import load_dotenv
# import math # Not needed here if scheduler sends whole minutes

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    NoSuchElementException,
    WebDriverException
)
from webdriver_manager.chrome import ChromeDriverManager

# Load environment variables from .env file
load_dotenv()

# Get website for login from .env
WEBSITE_URL = os.getenv("LOGIN_URL")
UID_USERNAME = os.getenv("UID_USERNAME")
UID_PASSWORD = os.getenv("PASSWORD")
ACCESS_DOORS_URL = os.getenv("UNIFI_ACCESS_DOORS_URL")  # e.g. https://unifi.ui.com/consoles/<console-id>/access/devices/hubs
DOOR_NAMES = [d.strip() for d in os.getenv("DOOR_NAMES", "Main Entrance,Side Door,Back Door").split(",") if d.strip()]

# Script configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE_CONTROLLER = os.path.join(LOG_DIR, f'ubiquiti_door_controller_{datetime.now().strftime("%Y%m%d")}.log')

MAX_RETRIES = 3

def setup_logging_controller():
    log_format = "%(asctime)s - %(levelname)s - %(module)s - %(funcName)s:%(lineno)d - %(message)s"
    logger_instance = logging.getLogger("UbiquitiDoorController")
    logger_instance.setLevel(logging.DEBUG)
    logger_instance.handlers = []
    file_handler = logging.FileHandler(LOG_FILE_CONTROLLER, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format))
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format))
    logger_instance.addHandler(file_handler)
    logger_instance.addHandler(console_handler)
    logger_instance.propagate = False
    logger_instance.info("Logging initialized for UbiquitiDoorController")
    return logger_instance

logger = setup_logging_controller()

# In UbiquitiDoorController.py, replace the entire "class DoorUnlockManager" block

class DoorUnlockManager:
    def __init__(self):
        logger.info("Initializing DoorUnlockManager")
        self.active_driver = None
        # Door names exactly as they appear in UniFi Access (set DOOR_NAMES in .env)
        self.all_doors_map = {name.lower(): f"//span[text()='{name}']" for name in DOOR_NAMES}

    def setup_chrome_driver(self):
        logger.info("Setting up Chrome WebDriver...")
        chrome_options = webdriver.ChromeOptions()
        #chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument('--log-level=3')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")

        # --- Attempt 1: Automatic setup with webdriver-manager ---
        try:
            logger.info("Attempt 1/2: Automatic setup with webdriver-manager.")
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            self.active_driver = driver
            logger.info("Automatic Chrome WebDriver setup successful.")
            return driver
        except Exception as e_auto:
            logger.warning(f"Automatic setup failed: {e_auto}")
            logger.warning("Falling back to manual chromedriver setup from local folder.")

        # --- Attempt 2: Manual setup (fallback) ---
        try:
            logger.info("Attempt 2/2: Manual setup from local 'chrome-win64' folder.")
            # SCRIPT_DIR is defined globally at the top of your file
            driver_path = os.path.join(SCRIPT_DIR, "chrome-win64", "chromedriver.exe")
            
            if not os.path.exists(driver_path):
                logger.critical(f"Manual driver not found at path: {driver_path}")
                raise FileNotFoundError(f"Manual chromedriver.exe not found. Please ensure the 'chrome-win64' folder and its contents are in the script directory.")

            service = Service(executable_path=driver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
            self.active_driver = driver
            logger.info("Manual Chrome WebDriver setup successful.")
            return driver
        except Exception as e_manual:
            logger.critical(f"FATAL: Both automatic and manual WebDriver setups failed.")
            logger.critical(f"Manual setup error: {e_manual}")
            # Re-raise the final exception to halt the script gracefully
            raise

    def safe_click_element(self, driver, wait, locator, element_name):
        last_exception = None
        for attempt in range(MAX_RETRIES):
            try:
                element = wait.until(EC.element_to_be_clickable(locator))
                element.click()
                logger.info(f"Successfully clicked {element_name} (Attempt {attempt + 1})")
                return True
            except Exception as e:
                last_exception = e
                logger.warning(f"Attempt {attempt + 1} to click {element_name} failed. Trying JS fallback. Error: {e}")
                try:
                    element_js = driver.find_element(*locator)
                    driver.execute_script("arguments[0].click();", element_js)
                    logger.info(f"Successfully clicked {element_name} using JavaScript.")
                    return True
                except Exception as e_js:
                    last_exception = e_js
                    logger.warning(f"JS click also failed for {element_name}: {e_js}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
        logger.error(f"Failed to click {element_name} after {MAX_RETRIES} attempts. Last error: {last_exception}")
        return False

    def take_screenshot(self, driver, name_prefix="error"):
        if not driver: return
        try:
            filename = os.path.join(LOG_DIR, f"{name_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
            driver.save_screenshot(filename)
            logger.info(f"Screenshot saved to {filename}")
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")

    def apply_door_temporary_unlock(self, driver, wait, unlock_duration_minutes):
        logger.info(f"Applying temporary unlock for {unlock_duration_minutes} minutes.")
        try:
            if not self.safe_click_element(driver, wait, (By.XPATH, "//label[.//input[@data-id='temporaryUnlock']]"), "Temporary unlock toggle"):
                raise Exception("Failed to click Temporary unlock toggle")
            time.sleep(1.5)

            if not self.safe_click_element(driver, wait, (By.XPATH, "//div[@data-testid='inputReadOnly']"), "Unlock mode dropdown"):
                raise Exception("Failed to click unlock mode dropdown")
            time.sleep(1.5)

            if not self.safe_click_element(driver, wait, (By.XPATH, "//div[contains(@class, 'style_option')]//button[.//span[text()='Custom Unlock Duration']]"), "Custom Unlock Duration option"):
                raise Exception("Failed to click Custom Unlock Duration option")
            time.sleep(1.5)

            duration_input_locator = (By.XPATH, "//input[@type='number']")
            unit_selector_locator = (By.XPATH, "//div[contains(@class, 'inputContent')]//div[@data-testid='inputReadOnly']")
            minutes_option_locator = (By.XPATH, "//li[@id='dropdownOptions_minutes']//div[text()='minutes']")
            
            if not self.safe_click_element(driver, wait, unit_selector_locator, "Time unit dropdown"):
                raise Exception("Could not click unit dropdown")
            time.sleep(0.7)
            if not self.safe_click_element(driver, wait, minutes_option_locator, "Minutes option"):
                raise Exception("Could not select 'minutes' option")
            logger.info("Successfully selected 'minutes' unit.")
            time.sleep(1.0)
            
            duration_input = wait.until(EC.element_to_be_clickable(duration_input_locator))
            logger.info("Aggressively clearing duration input field.")
            duration_input.send_keys(Keys.CONTROL, 'a')
            time.sleep(0.2)
            duration_input.send_keys(Keys.DELETE)
            
            logger.info(f"Sending keys for duration: {unlock_duration_minutes}")
            duration_input.send_keys(str(unlock_duration_minutes))

            if not self.safe_click_element(driver, wait, (By.XPATH, "//button[.//span[text()='Confirm']]"), "Confirm duration button"):
                raise Exception("Failed to click Confirm duration button")

            if not self.safe_click_element(driver, wait, (By.XPATH, "//button[.//span[text()='Apply Changes']]"), "Apply Changes button"):
                raise Exception("Failed to click Apply Changes button")
            
            logger.info("Clicked 'Apply Changes'. Waiting...")
            time.sleep(3.5)
            return self.verify_unlock_application(wait)
        except Exception as e:
            logger.error(f"Error in apply_door_temporary_unlock: {e}")
            self.take_screenshot(driver, "apply_door_temporary_unlock_error")
            return False

    def verify_unlock_application(self, wait):
        try:
            time.sleep(2.5) 
            status_indicator_xpath = "//div[contains(@class, 'OverflowText_overflowText') and (contains(text(), 'Unlocked Until') or contains(text(), 'Temporarily Unlocked'))]"
            status_element = wait.until(EC.visibility_of_element_located((By.XPATH, status_indicator_xpath)))
            logger.info(f"Post-unlock status indicator: \"{status_element.text}\"")
            return "unlocked" in status_element.text.lower()
        except Exception as e:
            logger.error(f"Exception during unlock verification: {e}")
            self.take_screenshot(self.active_driver, "verify_unlock_error")
            return False

    def perform_door_action(self, action, doors_to_process, duration_minutes=None):
        if not all([UID_USERNAME, UID_PASSWORD, WEBSITE_URL]):
            logger.critical("Missing website credentials or URL in .env file.")
            return False
        if action.lower() == "unlock" and (duration_minutes is None or duration_minutes <= 0):
            logger.error("Unlock action requires a positive duration in minutes.")
            return False
        
        logger.info(f"Performing action: '{action}' for doors: {doors_to_process} with duration: {duration_minutes} minutes")
        
        driver = None 

        try:
            driver = self.setup_chrome_driver()
            wait = WebDriverWait(driver, 30)
            
            # The login URL in your .env file is now the primary navigation point
            login_page_url = WEBSITE_URL 
            driver.get(login_page_url)
            logger.info(f"Navigated to login page: {login_page_url}")
            
            # --- STEP 1: CORRECTED One-Step Login Flow ---
            logger.info("Attempting one-step login...")

            # Find and fill the username/email field
            username_locator = (By.NAME, "user")
            wait.until(EC.element_to_be_clickable(username_locator)).send_keys(UID_USERNAME)
            logger.info("Entered username.")
            
            # Find and fill the password field immediately
            password_locator = (By.CSS_SELECTOR, "input[type='password']")
            wait.until(EC.element_to_be_clickable(password_locator)).send_keys(UID_PASSWORD)
            logger.info("Entered password.")
            time.sleep(1) # Short pause before screenshot
            self.take_screenshot(driver, "pre_signin_screenshot")

            # Find and click the final "Sign In" button
            signin_button_locator = (By.XPATH, "//button[@type='submit' and .//span[text()='Sign In']]")
            if not self.safe_click_element(driver, wait, signin_button_locator, "Sign In button"):
                raise Exception("Login failed: Could not click the final 'Sign In' button.")
            
            logger.info("Clicked 'Sign In' button. Pausing to observe result...")
            time.sleep(2)
            logger.info(f"URL immediately after clicking Sign In: {driver.current_url}")
            self.take_screenshot(driver, "post_signin_screenshot")

            # --- STEP 2: VERIFY LOGIN SUCCESS ---
            logger.info("Verifying login by waiting for the portal dashboard to load...")
            # After login, we expect to be at a URL that contains 'unifi.ui.com'
            # Waiting for the "Access" icon is a good way to confirm we're logged in.
            access_icon_locator = (By.XPATH, "//div[contains(@class, 'access_')]//a")
            try:
                wait.until(EC.visibility_of_element_located(access_icon_locator))
                logger.info("Login VERIFIED. Portal dashboard loaded successfully.")
            except TimeoutException:
                logger.error("LOGIN FAILED. The portal dashboard did not load after clicking Sign In.")
                self.take_screenshot(driver, "login_failed_dashboard_not_found")
                with open(os.path.join(LOG_DIR, 'login_failed_source.html'), 'w', encoding='utf-8') as f:
                    f.write(driver.page_source)
                raise Exception("Login verification failed; dashboard element was not found.")

            # --- STEP 3: DIRECT Navigation ---
            direct_access_url = ACCESS_DOORS_URL
            logger.info(f"Navigating directly to UniFi Access URL: {direct_access_url}")
            driver.get(direct_access_url)

            # --- STEP 4: Wait for the Doors Page to be Ready ---
            page_header_locator = (By.XPATH, "//h1[text()='Doors']")
            logger.info("Waiting for the main Doors page to render...")
            wait.until(EC.visibility_of_element_located(page_header_locator))
            logger.info("Doors page has rendered successfully.")
            
            # --- STEP 5: Process Each Door ---
            # (This section remains the same)
            first_door_settings_opened = False
            overall_success = True
            for i, door_name in enumerate(doors_to_process, 1):
                door_xpath = self.all_doors_map.get(door_name.lower())
                if not door_xpath:
                    logger.warning(f"Door name '{door_name}' not found in map. Skipping.")
                    overall_success = False
                    continue
                logger.info(f"--- Processing door {i}/{len(doors_to_process)}: '{door_name}' ---")
                if not self.safe_click_element(driver, wait, (By.XPATH, door_xpath), f"Door '{door_name}'"):
                    logger.error(f"Failed to click door '{door_name}'. Skipping.")
                    overall_success = False
                    continue
                time.sleep(1.5)
                if not first_door_settings_opened:
                    if not self.safe_click_element(driver, wait, (By.XPATH, '//button[@data-testid="settings"]'), "Settings tab"):
                        raise Exception("Could not open settings panel for the first door.")
                    first_door_settings_opened = True
                    time.sleep(1.5)
                action_successful_this_door = self.apply_door_temporary_unlock(driver, wait, duration_minutes)
                if not action_successful_this_door:
                    overall_success = False
                logger.info(f"--- Finished processing door '{door_name}' ---")
                time.sleep(1)
            return overall_success

        except Exception as e:
            logger.critical(f"Critical error in perform_door_action: {e}")
            traceback.print_exc()
            if driver: self.take_screenshot(driver, "perform_door_action_critical_error")
            return False
        finally:
            if self.active_driver:
                try:
                    self.active_driver.quit()
                    logger.info("Browser session closed.")
                except Exception as e_quit:
                    logger.warning(f"Exception during driver.quit(): {e_quit}")
                finally:
                    self.active_driver = None

def main():
    # Import notification function here to avoid circular dependencies if it grows
    from notifications import send_notification


    # --- TESTING BLOCK: Uncomment to run without needing to edit the JSON -------------
    # To use command-line arguments again, comment this block out and uncomment the one below.
    from argparse import Namespace
    args = Namespace(
        action="unlock",
        doors="Back Door",  # <-- Change door name(s) for your test here
        duration_minutes=1      # <-- Change duration for your test here
    )
    logger.info("--- RUNNING IN TEST MODE WITH HARDCODED VALUES ---")
    # --- END OF TESTING BLOCK ---------------------------------------------------------


#    parser = argparse.ArgumentParser(description="Control Ubiquiti door locks via Selenium.")
#    parser.add_argument("--action", required=True, choices=["unlock"],
#                        help="Action to perform.")
#    parser.add_argument("--doors", required=True, type=str,
#                        help="Comma-separated string of door names.")
#    parser.add_argument("--duration_minutes", type=int, required=True, 
#                        help="Number of minutes for the temporary unlock.")
#
#    args = parser.parse_args()
#
    logger.info(f"UbiquitiDoorController.py initiated with: Action='{args.action}', Doors='{args.doors}', Duration='{args.duration_minutes}' minutes")

    if not WEBSITE_URL or not UID_USERNAME or not UID_PASSWORD or not ACCESS_DOORS_URL:
        logger.critical("LOGIN_URL, UID_USERNAME, PASSWORD, or UNIFI_ACCESS_DOORS_URL not configured in .env file. Exiting.")
        sys.exit(1)

    # --- Retry Logic ---
    MAX_ATTEMPTS = 3
    for attempt in range(MAX_ATTEMPTS):
        logger.info(f"--- Starting door control attempt {attempt + 1}/{MAX_ATTEMPTS} ---")
        
        doors_list = [door.strip().lower() for door in args.doors.split(',') if door.strip()]
        if not doors_list:
            logger.error("No valid door names provided. Aborting.")
            sys.exit(1)

        door_manager = DoorUnlockManager()
        success = False
        try:
            success = door_manager.perform_door_action(
                action=args.action, 
                doors_to_process=doors_list,
                duration_minutes=args.duration_minutes
            )
        except Exception as e_main:
            logger.critical(f"Unhandled exception in main execution flow on attempt {attempt + 1}: {e_main}")
            traceback.print_exc()
            success = False

        if success:
            logger.info(f"Action '{args.action}' COMPLETED SUCCESSFULLY on attempt {attempt + 1} for doors: {args.doors}.")
            sys.exit(0) # Exit with success code
        
        # If not successful, log it and wait before retrying
        logger.warning(f"Attempt {attempt + 1} failed.")
        if attempt < MAX_ATTEMPTS - 1:
            wait_time = 60 # Wait 60 seconds between retries
            logger.info(f"Waiting for {wait_time} seconds before retrying...")
            time.sleep(wait_time)
    
    # This part is only reached if all attempts fail
    logger.critical(f"All {MAX_ATTEMPTS} attempts to perform action '{args.action}' on doors '{args.doors}' have failed.")
    
    # --- Send Failure Email ---
    subject = f"❌ CRITICAL FAILURE: Door Unlock Script Failed All Retries"
    body = (
        f"The Selenium script 'UbiquitiDoorController.py' failed to unlock doors after {MAX_ATTEMPTS} attempts.\n\n"
        f"  - Action: {args.action}\n"
        f"  - Doors: {args.doors}\n"
        f"  - Requested Duration: {args.duration_minutes} minutes\n\n"
        f"Please check the script logs in the 'logs' directory on the server for detailed errors and manually verify the door status."
    )
    send_notification(subject, body)
    
    sys.exit(1) # Exit with failure code

if __name__ == "__main__":
    main()
