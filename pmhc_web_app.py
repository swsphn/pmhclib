# This class interacts with the PMHC website, tracking uploads and
# their corresponding error JSON files.
# The script uses Python Playwright to do this
# This script is useful when doing multiple error removal runs with
# remove_pmhc_mds_errors.py as it saves time and ensures you always get the correct
# corresponding error file. Tested under Ubuntu WSL and PowerShell.
# --no-headless runs best under PowerShell
#
# No login details are saved anywhere
# To speed up usage when doing repeated calls, create the following local env variables:
# PMHC_USERNAME
# PMHC_PASSWORD
#
# Good tute on persistent Playwright browsing
# https://www.youtube.com/watch?v=JMq8ImhDih0
import logging
import os
import platform
import random
import shutil
import sqlite3
import time
from datetime import datetime
from getpass import getpass
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from rich.progress import track


class FileNotFoundException(Exception):
    """Custom error handler for when no file is found"""


class IncorrectFileType(Exception):
    """Custom error handler for when an incorrect file is provided"""


class InvalidPmhcUploadId(Exception):
    """Custom error handler for when we cannot find a PMHC upload_id"""


class InvalidPmhcUser(Exception):
    """Custom error handler for when a PMHC login is unsuccessful"""


class MissingPmhcElement(Exception):
    """Custom error handler for when PMHC page returns incorrect Playwright element"""


class PmhcWebApp:
    # class properties
    headless = True  # default value, can be overridden
    user_info = None
    upload_filename = None
    upload_id = None
    upload_status = None
    STATE = Path("./DO_NOT_COMMIT/state.json")  # save browser session state
    downloads_folder = Path("downloads")  # default value, can be overridden
    uploads_folder = Path("uploads")  # default value, can be overridden
    start_time = None
    default_timeout = 60000
    db_conn = None  # sqlite database connection object
    db_file = "pmhc_web_app.db"

    def __init__(
        self,
        downloads_folder: Path,
        uploads_folder: Path,
        start_time: datetime,
        headless: bool,
    ):
        # save whether to use a headless browser instance or not
        self.headless = headless
        self.start_time = start_time

        # create required folders on disk
        state_folder = self.STATE.parent
        state_folder.mkdir(parents=True, exist_ok=True)
        self.downloads_folder.mkdir(parents=True, exist_ok=True)
        self.uploads_folder.mkdir(parents=True, exist_ok=True)

        # setup sqlite database for saving/resuming PMHC uploads
        self.db_setup()

        # login to PMHC and save browser session state for later use
        # self.login()

    def db_setup(self):
        # Setup sqlite database
        self.db_conn = sqlite3.connect(self.db_file)

        # startime, filename, rounds, savepoints (see savepoints_for_gui_testing.txt)
        sql = """
        CREATE TABLE IF NOT EXISTS save_points
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upload_id TEXT NOT NULL,
                input_file TEXT NOT NULL,
                round_count INTEGER NOT NULL,
                start_time DATETIME,
                processing_time INTEGER NOT NULL
            )
        """
        self.db_conn.execute(sql)

    def db_create_save_point(
        self,
        upload_id: str,
        input_file: Path,
        round_count: int,
        start_time: datetime,
        processing_time: int,
    ) -> int:
        """Creates a new save point which the user can resume from in future

        Args:
            upload_id (str): PMHC upload_id e.g. 09cbed58
            input_file (Path): PMHC input_file e.g. PMHC_MDS_20230101_20230512.xlsx
            round_count (int): which round the user is currently up to e.g. 2
            start_time (datetime): start_time when script started
            e.g. 2023-05-24 11:35:55.926231
            processing_time (int): number of seconds it took PMHC to process the file

        Returns:
            int: the id of the new save_point
        """
        # strip off everything but the filename from input_file, e.g. remove C:\test\
        input_file_stripped = input_file.name

        insert_sql = (
            "INSERT INTO save_points (upload_id, input_file, round_count, start_time, "
            "processing_time) "
            f"VALUES ('{upload_id}', '{input_file_stripped}', '{round_count}', "
            f"'{start_time}', '{processing_time}')"
        )

        try:
            cursor = self.db_conn.execute(insert_sql)
            self.db_conn.commit()
            save_point_id = cursor.lastrowid
        except sqlite3.Error as e:
            # Handle the SQLite error
            logging.exception("SQLite Error: %s", e)

        return save_point_id

    def db_get_save_point(self, save_point_id: int) -> dict:
        """Gets a particular save_point for a given save_point_id

        Args:
            id (int): save_point_id e.g. 2

        Returns:
            dict: returns a dictionary with fields from save_points table
        """

        select_sql = (
            "SELECT id, upload_id, input_file, round_count, "
            "strftime('%d/%m/%Y %H:%M', start_time) as start_time, processing_time "
            f"FROM save_points WHERE id = '{save_point_id}' LIMIT 1"
        )

        cursor = self.db_conn.execute(select_sql)
        row = cursor.fetchone()

        if row is None:
            return {}
        else:
            columns = [
                "id",
                "upload_id",
                "input_file",
                "round_count",
                "start_time",
                "processing_time",
            ]
            result_dict = dict(zip(columns, row))
            return result_dict

    def db_get_save_points(self, input_file: Path) -> dict:
        """gets all the savepoints for a given input_file

        Args:
            input_file (Path): PMHC input_file e.g. PMHC_MDS_20230101_20230512.xlsx

        Returns:
            dict: dictionary containing rows from save_points table
        """

        # strip off everything but the filename from input_file, e.g. remove C:\test\
        input_file_stripped = input_file.name

        select_sql = (
            "SELECT id, upload_id, input_file, round_count, "
            "strftime('%d/%m/%Y %H:%M', start_time) as start_time, processing_time "
            f"FROM save_points WHERE input_file = '{input_file_stripped}' "
            "ORDER BY id ASC"
        )

        cursor = self.db_conn.execute(select_sql)
        rows = cursor.fetchall()

        # Convert the fetched rows to a dictionary
        columns = [
            "id",
            "upload_id",
            "input_file",
            "round_count",
            "start_time",
            "processing_time",
        ]
        result_dict = [dict(zip(columns, row)) for row in rows]

        return result_dict

    def random_delay(self, min: int = 1, max: int = 3):
        """Delays the script for a random number of seconds
        Useful in slowing down playwright to make it look more human-like and not
        upset PMHC website which appears to dislike too much login page activity

        Args:
            min (int, optional): minimum delay in seconds. Defaults to 1.
            max (int, optional): maximum delay in seconds. Defaults to 3.

        """
        random_number = random.uniform(min, max)
        time.sleep(random_number)

    def is_logged_in(self) -> bool:
        """check if we already have an existing PMHC login session active we can use

        Returns:
            bool: True if logged in
        """
        # First check if state file exists. It won't exist if this is the first time
        # user has run script, as it only gets created when self.login() is called
        if not os.path.exists(self.STATE):
            return False

        # check against PMHC website if session saved in self.STATE is still valid
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(storage_state=self.STATE)
            context.set_default_timeout(self.default_timeout)
            page = context.new_page()
            page.goto("https://pmhc-mds.net")
            page.wait_for_load_state()
            self.random_delay()

            # Detect this element which should only exist for logged in users:
            # <span id="currentUserText" class="ng-binding">
            element_exists = page.locator("#currentUserText").is_visible()
            if element_exists:
                logged_in = True
            else:
                logged_in = False

            browser.close()
            return logged_in

    def login(self):
        """Logs in to PMHC website and saves the Playwright state
        This allows us to resume the session across class methods
        """

        # Prompt user for credentials if not set in env.
        username = os.getenv("PMHC_USERNAME")
        password = os.getenv("PMHC_PASSWORD")

        if not username or not password:
            if platform.system() == "Windows":
                logging.debug(
                    "In future, consider setting the following environment variables "
                    "when running this script:\n"
                    "PMHC_USERNAME and PMHC_PASSWORD\n"
                    "To do so, run the following commands in PowerShell:\n"
                    "$env:PMHC_USERNAME='your_username_here'\n"
                    "$env:PMHC_PASSWORD=python -c 'import getpass; print(getpass.getpass())'"
                )
            elif platform.system() == "Linux":
                logging.debug(
                    "In future, consider setting the following environment variables "
                    "when running this script:\n"
                    "PMHC_USERNAME and PMHC_PASSWORD\n"
                    "To do so, run the following commands in Linux:\n"
                    "read PMHC_USERNAME && export PMHC_USERNAME\n"
                    "read -rs PMHC_PASSWORD && export PMHC_PASSWORD"
                )
            username = input("Enter PMHC username: ")
            password = getpass("Enter PMHC password (keyboard input will be hidden): ")

        logging.info("Logging into PMHC website")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context()
            context.set_default_timeout(self.default_timeout)
            page = context.new_page()

            # login to PMHC website
            page.goto("https://pmhc-mds.net")
            self.random_delay()
            logging.info("Clicking 'Sign in' button")
            page.locator('[id="loginBtn"]').click()
            self.random_delay()
            logging.info("Filling in user credentials")
            page.type('input[id="username"]', username)
            page.type('input[id="password"]', password)

            # target the 'Continue' submit button. Note from 25/05/2023 there are now
            # two of them: the first hidden one (a decoy!), the second visible
            # one (real). We need to isolate the correct one based on its parent div
            buttons = page.locator(
                "button[type='submit'][name='action'][value='default']"
                "[data-action-button-primary='true']"
            ).all()

            # find the real 'Continue' button which isn't hidden, determined by the
            # parent div class="c79fd81e4"
            if buttons:
                for button in buttons:
                    # click the correct button
                    parent_div_class = button.evaluate(
                        '(element) => element.parentNode.getAttribute("class")'
                    )
                    if parent_div_class == "c79fd81e4":
                        button.click()
            else:
                logging.error("Could not find 'Continue' button on login page")
                raise MissingPmhcElement

            page.wait_for_load_state()
            self.random_delay()

            # confirm login was successful
            user_query = page.request.get("https://pmhc-mds.net/api/current-user")
            self.user_info = user_query.json()

            # error key will be present if login was unsuccessful
            if "error" in self.user_info:
                logging.error(
                    "PMHC login was unsuccessful. Are you sure you entered "
                    "correct credentials?"
                )
                raise InvalidPmhcUser

            # Save storage state into file
            context.storage_state(path=self.STATE)
            browser.close()

    def get_page_content(self, url: str) -> str:
        """Gets the page HTML for a given URL

        Args:
            url (str): e.g. https://pmhc-mds.net

        Returns:
            str: HTML content of the page
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(storage_state=self.STATE)
            context.set_default_timeout(self.default_timeout)
            page = context.new_page()
            page.goto(url)
            page.wait_for_load_state()
            page_content = page.content()

            return page_content

    def upload_file(
        self, user_file: Path, round_count: int, mode: str = "test"
    ) -> Path:
        """Uploads a user specified file to PMHC website

        Args:
            user_file (Path): path to the file e.g. 'PMHC_MDS_20230101_20230131.xlsx'
            round_count (int): What round of file this is e.g. 1, 2, 3 etc
            mode (str): Upload in 'test' or 'live' mode? Defaults to 'test'.
            Use 'live' with care!

        Raises:
            IncorrectFileType: If user uploads a bad filetype
            FileNotFoundException: If we cannot find user file

        Returns:
            Path: filename of the new file we generated for matching purposes
        """

        user_file = Path(user_file)

        # check file looks ok
        if user_file.suffix != ".xlsx" and user_file.suffix != ".zip":
            logging.error(
                "Only .xlsx or .zip (containing multiple csv's) are acceptable PMHC "
                "input files"
            )
            raise IncorrectFileType

        if not user_file.exists():
            logging.error(
                "Input file does not exist - please check the file path and try again"
            )
            raise FileNotFoundException

        # check no uploads are currently being processed
        # PMHC only allows one upload at a time per user account.
        # This usually only occurs if the user is also using their browser to upload
        # manually at the same time as running this script
        # skip this check for live files which just need to be uploaded, they don't
        # need to have matching done for JSON error file retrieval
        if mode == "test":
            self.wait_for_upload()

        # copy and rename the user file so we can find it again when it is uploaded
        # to PMHC
        # new filename should be in the format of:
        # YYYYMMDD_HHMMSS_round1.xlsx
        now = datetime.now()
        date_string = now.strftime("%Y%m%d_%H%M%S")

        # self.upload_filename will be used by other class methods
        # e.g. to retrieve upload_id
        self.upload_filename = (
            f"{user_file.stem}_{date_string}_round_{round_count}{user_file.suffix}"
        )
        logging.info(
            f"New dynamically generated round {round_count} filename is: "
            f"'{self.upload_filename}'"
        )
        upload_filepath = f"{self.uploads_folder}/{self.upload_filename}"
        shutil.copyfile(user_file, upload_filepath)

        logging.info(
            f"Uploading '{self.upload_filename}' to PMHC as a '{mode}' file\n"
            "It usually takes approx 3-10 minutes for PMHC to process xlsx files "
            "depending on the number of months included in the data, less for zipped "
            "csv files (e.g. round 2 onward)"
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(storage_state=self.STATE)
            context.set_default_timeout(self.default_timeout)
            page = context.new_page()
            page.goto("https://pmhc-mds.net/#/upload/add")
            page.wait_for_load_state()

            # upload in 'live' (e.g. completed file) or 'test' mode (error file)?
            if mode == "live":
                logging.info("Uploading in 'live' mode")
            else:
                logging.info("Uploading in 'test' mode, clicking checkbox")
                page.locator('[id="testUploadCheckbox"]').click()

            logging.info("Selecting Organisation: SWSPHN")
            # page.locator('"South Western Sydney ( PHN105 )"').click()
            page.select_option("#uploadOrgSelect", value="PHN105")

            # PMHC have hidden form fields which holds the filename etc
            # reveal these to make our life easier when debugging
            # <input type="file" id="fileUpload" style="display: none;">
            logging.info("Revealing the hidden form fields")
            page.eval_on_selector("#fileUpload", 'el => el.style.display = "block"')
            page.eval_on_selector("#uploadBtn", 'el => el.style.display = "block"')

            logging.info("Entering filename into dialog box")
            # Get the input element for the file selector
            file_input = page.query_selector("#fileUpload")
            file_input.set_input_files(upload_filepath)

            logging.info("Clicking 'Upload' button")
            page.locator('[id="uploadBtn"]').click()
            delay = 60
            logging.info(
                f"Uploading '{self.upload_filename}' to PMHC, waiting {delay} seconds..."
            )
            self.showLoadingBar(delay, description="Waiting for PMHC upload...")

            page.wait_for_load_state()
            browser.close()

            return self.upload_filename

    def start_timer(self):
        """Start a timer. Useful in recording how long a PMHC upload takes to process"""
        self.start_timestamp = datetime.now()

    def stop_timer(self) -> int:
        """Stops a timer started with start_timer() and returns the value in minutes

        Returns:
            int: number of minutes the timer ran for
        """
        end_timestamp = datetime.now()
        elapsed_time = end_timestamp - self.start_timestamp
        elapsed_minutes = elapsed_time.total_seconds() / 60
        return round(elapsed_minutes, 1)

    def wait_for_upload(self):
        """Waits for a PMHC upload to complete processing in 'test' mode"""

        # delay between each check of PMHC Uploads page
        delay = 30
        counter = 1

        while True:
            # check to see if the PMHC upload queue is free
            if self.is_upload_processing():
                logging.info(
                    f"An upload is currently processing for '{self.get_pmhc_username()}' "
                    f"account, waiting for {delay} seconds..."
                )
            else:
                logging.info(
                    f"No upload is processing for '{self.get_pmhc_username()}' account, "
                    "so we can stop waiting now"
                )
                break

            self.showLoadingBar(
                delay, description=f"{counter} - Waiting for PMHC processing..."
            )
            counter += 1

    def download_error_json(self, upload_id: str) -> Path:
        """Downloads a JSON error file from PMHC
        This is useful for matching against uploaded files and processing

        Args:
            upload_id (str): PMHC upload_id from View Uploads page e.g. 7f91a4f5

        Returns:
            Path: Path to JSON file saved to local disk
        """

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(storage_state=self.STATE)
            context.set_default_timeout(self.default_timeout)
            page = context.new_page()

            # first we need to get the uuid e.g. 94edf5e3-36b1-46d3-9178-bf3b142da6a1
            id_query = page.request.get(
                f"https://pmhc-mds.net/api/uploads?upload_uuid={upload_id}",
                headers={"Range": "0-49"},
            )

            id_json = id_query.json()

            time.sleep(0.5)
            for upload in id_json:
                uuid = upload["uuid"]
                url = f"https://pmhc-mds.net/api/organisations/PHN105/uploads/{uuid}"
                upload_errors_json = page.request.get(url)

                filename = f"{self.downloads_folder}/{upload_id}.json"
                with open(filename, "wb") as file:
                    file.write(upload_errors_json.body())

                logging.info(f"Saved JSON file to disk: '{filename}'")

            # return whatever json file it last found to calling script
            # We'll need to make this more robust in the near future if
            # we start getting multiple files coming back
            return Path(filename)

    def showLoadingBar(self, delay: int, description: str):
        """Shows a loading bar for a given amount of seconds
        This is useful for delaying a script e.g. whilst a PMHC
        upload processes

        Args:
            delay (int): number of seconds to show loading bar
            description (str): Descriptive text to show user
        """
        # simulate some work being done to progress our loading bar
        for _i in track(range(delay), description=description):
            time.sleep(1)

    def get_user_info(self, name: str) -> str:
        """Gets info about the logged in PMHC user
        eg:
        email:    jonathan.stucken@swsphn.com.au
        id:       3826
        username, roles, user_agent, uuid etc

        Args:
            name (str): specific field of the user info you want e.g. username

        Raises:
            InvalidPmhcUser: if error retrieving from PMHC

        Returns:
            str: the value of the user info field requested e.g. 'johnsmith1'
        """
        # only do this if this class hasn't already requested user info through use of
        # the login() method
        if not self.user_info:
            # get info about the logged in user from PMHC
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(storage_state=self.STATE)
                context.set_default_timeout(self.default_timeout)
                page = context.new_page()
                user_query = page.request.get("https://pmhc-mds.net/api/current-user")
                self.user_info = user_query.json()

            # error key will be present if login was unsuccessful
            if "error" in self.user_info:
                logging.error("Could not retrieve user details from PMHC")
                raise InvalidPmhcUser

        return self.user_info[name]

    def get_pmhc_username(self) -> str:
        """Gets the PMHC username of the current logged in user
        e.g. jonathans1
        Note that this may differ to the env var PMHC_USERNAME which could be an
        email address
        Returns:
            str: PMHC username
        """
        return self.get_user_info("username")

    def get_request(self, url: str) -> str:
        """gets the JSON response for a given request to PMHC website

        Args:
            url (str): the PMHC API url to query
            e.g. https://pmhc-mds.net/api/current-user

        Returns:
            str: A string containing the JSON reponse from the request
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(storage_state=self.STATE)
            context.set_default_timeout(self.default_timeout)
            page = context.new_page()
            page.goto(url)
            page.wait_for_load_state()
            user_query = page.request.get(url)

            return user_query.json()

    def is_upload_processing(self) -> bool:
        """Checks if the user has an upload currently 'processing' in either live or
        test mode. Useful for checking before we do certain actions e.g. try upload
        another file, because this script can only handle one 'processing' file at a time
        Returns:
            bool: True if an upload is currently processing
        """
        # Get a list of all this user's 'test' uploads ('processing', 'complete'
        # and 'error' status)
        pmhc_username = self.get_pmhc_username()
        json_list = self.get_request(
            f"https://pmhc-mds.net/api/uploads?username={pmhc_username}&sort=-date"
        )
        # see if any are in a 'processing' state
        for json in json_list:
            if "status" in json and json["status"] == "processing":
                return True

        # all ok if
        return False

    def get_last_upload_filename(self) -> str:
        """Gets the filename of the last file uploaded by the class

        Returns:
            str: filname e.g. 20230320_094815_round_1.xlsx
        """
        return self.upload_filename

    def find_upload_id(self, pmhc_filename: str) -> str:
        """Finds an upload_id for a given PMHC upload filename

        Args:
            pmhc_filename (str): The PMHC filename to search for
            e.g. 20230320_094815_round_1.xlsx

        Raises:
            InvalidPmhcUploadId: Raises error if upload_id cannot be found

        Returns:
            str: PMHC upload_id e.g. 94edf5e3
        """

        # open PMHC 'View Uploads' page
        logging.info("Opening PMHC 'View Uploads page'")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(storage_state=self.STATE)
            page = context.new_page()

            # Open the PMHC 'View Uploads' page
            page.goto("https://pmhc-mds.net/#/upload/list")
            page.wait_for_load_state()
            logging.info("Clicking 'Filters' button")
            page.locator('"Filters"').click()
            time.sleep(1)

            logging.info(f"Setting 'File Name': {pmhc_filename}")
            page.type('input[id="filename"]', pmhc_filename)

            pmhc_username = self.get_pmhc_username()
            logging.info(f"Setting 'Username': {pmhc_username}")
            page.type('input[id="user.username"]', pmhc_username)

            logging.info("Setting 'Test': Yes")
            page.select_option("#test", value="1")

            logging.info("Clicking 'Apply' button")
            page.locator('"Apply"').click()
            time.sleep(1)
            page.wait_for_load_state()

            logging.info("Scraping 'Upload ID'")
            parent_div_obj = page.query_selector(".ag-center-cols-container")
            parent_div = parent_div_obj.inner_html()

            # Use bs4 to isolate the columns we want
            # 'Upload ID' should be the first child <span> column
            # 'Status should' be the last child <span> column
            soup = BeautifulSoup(parent_div, "html.parser")
            spans = soup.find_all("span", {"class": "ag-cell-value"})
            upload_id = spans[0].text

            # A valid PMHC upload_id should be 8 characters long
            # this should catch any general errors with the bs4 scrapes to this point
            if len(upload_id) == 8:
                self.upload_id = upload_id

                # status could be 'error', 'processing', or 'complete'
                self.upload_status = spans[-1].text
            else:
                logging.error(
                    "Could not retrieve a valid PMHC upload_id for filename: "
                    f"'{pmhc_filename}'"
                )
                raise InvalidPmhcUploadId

        return self.upload_id

    def get_last_upload_status(self) -> str:
        """Gets the status of the last PMHC upload
        self.find_upload_id() must be called prior to calling this method
        status is typically 'error', 'processing', or 'complete'

        Returns:
            str: status of last upload e.g. 'error'
        """
        if self.upload_id and self.upload_status:
            return self.upload_status
        else:
            logging.error(
                "Could not retrieve PMHC upload_id and upload_status. Make sure\n"
                "self.find_upload_id() has been called first before calling this method"
            )
            raise InvalidPmhcUploadId
