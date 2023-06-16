# This class interacts with the PMHC website, tracking uploads and
# their corresponding error JSON files.
# The script uses Python Playwright to do this
# This script is useful when doing multiple error removal runs with
# remove_pmhc_mds_errors.py as it saves time and ensures you always get the correct
# corresponding error file. Tested under Ubuntu WSL and PowerShell.
# --no-headless runs best under PowerShell (it's slower under Ubuntu WSL)
#
# No login details are saved anywhere
# To speed up usage when doing repeated calls, create the following local env variables:
# PMHC_USERNAME
# PMHC_PASSWORD
#
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

import pytz
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


class CouldNotFindPmhcUpload(Exception):
    """Custom error handler for when an upload cannot be found on PMHC"""


class PmhcWebApp:
    # class properties
    user_info = None
    upload_filename = None
    upload_id = None
    upload_status = None
    upload_link = None
    upload_date = None
    default_timeout = 60000
    phn_identifier = "PHN105"
    db_conn = None  # sqlite database connection object
    db_file = "pmhc_web_app.db"

    def __enter__(self):
        # Initialise playwright without a context manager
        self.p = sync_playwright().start()
        self.browser = self.p.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context()
        self.context.set_default_timeout(self.default_timeout)
        self.page = self.context.new_page()
        return self  # Return the instance of this class

    def __exit__(self, exc_type, exc_value, traceback):
        # exc_type, exc_value, and traceback are required parameters in __exit__()
        self.browser.close()
        self.p.stop()

    def __init__(
        self,
        start_time: datetime,
        headless: True,
    ):
        # save whether to use a headless browser instance or not
        self.headless = headless
        self.start_time = start_time

        self.downloads_folder = Path("downloads")
        self.downloads_folder.mkdir(parents=True, exist_ok=True)

        self.uploads_folder = Path("uploads")
        self.uploads_folder.mkdir(parents=True, exist_ok=True)

        # setup sqlite database for saving/resuming PMHC uploads
        self.db_setup()

    def db_setup(self):
        """Sets up sqlite database"""
        #
        self.db_conn = sqlite3.connect(self.db_file)

        sql = """
        CREATE TABLE IF NOT EXISTS save_points
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upload_id TEXT NOT NULL,
                original_input_file TEXT NOT NULL,
                original_input_file_size INTEGER NOT NULL,
                pmhc_filename TEXT NOT NULL,
                errors_removed_file TEXT,
                round_count INTEGER NOT NULL,
                num_pmhc_errors INTEGER NOT NULL,
                upload_date DATETIME,
                upload_link TEXT NOT NULL,
                processing_time INTEGER NOT NULL,
                complete BOOLEAN NOT NULL DEFAULT 0
            )
        """
        self.db_conn.execute(sql)

    def db_create_save_point(
        self,
        upload_id: str,
        original_input_file: Path,
        pmhc_filename: str,
        errors_removed_file: Path,
        round_count: int,
        num_pmhc_errors: int,
        processing_time: int,
    ) -> int:
        """Creates a new save point which the user can resume from in future

        Args:
            upload_id (str): PMHC upload_id e.g. 09cbed58
            original_input_file (Path): PMHC input_file
            e.g. PMHC_MDS_20230101_20230512.xlsx
            pmhc_filename (str): the dynmically generated filename which was uploaded to
            PMHC e.g. a7ca62e9_20230526_091643_round_3.zip
            errors_removed_file (Path): File with errors removed uploaded to PMHC
            e.g. errors_removed\9d9b43d9.zip
            round_count (int): which round the user is currently up to e.g. 2
            num_pmhc_errors (int): number of errors returned by PMHC
            processing_time (int): number of seconds it took PMHC to process the file

        Returns:
            int: the id of the new save_point
        """

        # get the filesize of input_file for matching purposes
        original_input_file_size = original_input_file.stat().st_size

        # strip off everything but the filename from input_file, e.g. remove C:\test\
        original_input_file_stripped = original_input_file.name

        insert_sql = f"""
            INSERT INTO save_points (
                upload_id,
                original_input_file,
                original_input_file_size,
                pmhc_filename,
                errors_removed_file,
                round_count,
                num_pmhc_errors,
                upload_date,
                upload_link,
                processing_time,
                complete
            )
            VALUES (
                '{upload_id}',
                '{original_input_file_stripped}',
                '{original_input_file_size}',
                '{pmhc_filename}',
                '{errors_removed_file}',
                '{round_count}',
                '{num_pmhc_errors}',
                '{self.upload_date}',
                '{self.upload_link}',
                '{processing_time}',
                {self.complete}
            )
        """
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

        select_sql = f"""
        SELECT * FROM save_points
        WHERE id = '{save_point_id}' LIMIT 1
        """

        cursor = self.db_conn.execute(select_sql)
        row = cursor.fetchone()

        if row is None:
            return {}
        else:
            columns = [column[0] for column in cursor.description]
            result_dict = dict(zip(columns, row))
            return result_dict

    def db_does_error_count_match_last_round(
        self, original_input_file: Path, id: int
    ) -> bool:
        """Checks if same number of errors in current round compared to prior round.
        Useful in warning the user if they are entering an endless loop where they get
        the same errors each round due to the script not being able to remove them.

        Args:
            original_input_file (Path): e.g. PMHC_MDS_20200708_20200731.xlsx
            id (int): save_point_id of current round e.g. 8

        Returns:
            bool: True if error counts match
        """

        # get input_file_size
        original_input_file_size = original_input_file.stat().st_size

        # strip off everything but the filename from input_file, e.g. remove C:\test\
        original_input_file_stripped = original_input_file.name

        # get current save_point
        current_sql = f"""
        SELECT id, num_pmhc_errors FROM save_points
        WHERE id = '{id}'
        LIMIT 1
        """
        current_cursor = self.db_conn.execute(current_sql)
        current_row = current_cursor.fetchone()
        current_columns = [column[0] for column in current_cursor.description]
        current_result_dict = dict(zip(current_columns, current_row))
        current_num_pmhc_errors = current_result_dict["num_pmhc_errors"]

        # get prior save_point matching same file/filesize
        old_sql = f"""
        SELECT id, num_pmhc_errors FROM save_points
        WHERE original_input_file = '{original_input_file_stripped}'
        AND original_input_file_size = '{original_input_file_size}'
        AND id < '{id}'
        ORDER BY id DESC
        LIMIT 1
        """
        old_cursor = self.db_conn.execute(old_sql)
        old_row = old_cursor.fetchone()
        old_columns = [column[0] for column in old_cursor.description]
        old_result_dict = dict(zip(old_columns, old_row))
        old_num_pmhc_errors = old_result_dict["num_pmhc_errors"]

        if old_row is None:
            return False
        elif old_num_pmhc_errors == current_num_pmhc_errors:
            return True
        else:
            return False

    def db_get_save_points(self, original_input_file: Path) -> dict:
        """gets all the savepoints for a given input_file

        Args:
            original_input_file (Path): PMHC input_file
            e.g. PMHC_MDS_20230101_20230512.xlsx

        Returns:
            dict: dictionary containing rows from save_points table
        """

        # get input_file_size
        original_input_file_size = original_input_file.stat().st_size

        # strip off everything but the filename from input_file, e.g. remove C:\test\
        original_input_file_stripped = original_input_file.name

        select_sql = f"""
        SELECT * FROM save_points
        WHERE original_input_file = '{original_input_file_stripped}'
        AND original_input_file_size = '{original_input_file_size}'
        ORDER BY id ASC
        """

        cursor = self.db_conn.execute(select_sql)
        rows = cursor.fetchall()

        # Convert the fetched rows to a dictionary
        columns = [column[0] for column in cursor.description]
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

    def login(self):
        """Logs in to PMHC website. This allows us to reuse the login the session
        across other class methods
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
        self.page.goto("https://pmhc-mds.net")
        self.random_delay()
        self.page.locator('[id="loginBtn"]').click()
        self.random_delay()
        self.page.type('input[id="username"]', username)
        self.page.type('input[id="password"]', password)

        # target the 'Continue' submit button. Note from 25/05/2023 there are now
        # two of them: the first hidden one (a decoy!), the second visible
        # one (real). We need to isolate the correct one based on its attributes
        buttons = self.page.locator("button:text('Continue')").all()

        if buttons:
            for button in buttons:
                # the real button contains 'data-action-button-primary' attribute
                if button.get_attribute("data-action-button-primary"):
                    button.click()
        else:
            logging.error("Could not find 'Continue' button on login page")
            raise MissingPmhcElement

        self.page.wait_for_load_state()
        self.random_delay()

        # confirm login was successful
        user_query = self.page.request.get("https://pmhc-mds.net/api/current-user")
        self.user_info = user_query.json()

        # error key will be present if login was unsuccessful
        if "error" in self.user_info:
            logging.error(
                "PMHC login was unsuccessful. Are you sure you entered "
                "correct credentials?"
            )
            raise InvalidPmhcUser

    def upload_file(
        self,
        input_file: Path,
        original_input_file: Path,
        round_count: int,
        mode: str = "test",
    ) -> Path:
        """Uploads a user specified file to PMHC website

        Args:
            input_file (Path): path to the file e.g. 'cc9dd7b5.csv'
            original_input_file (Path): path to the original file
            e.g. 'PMHC_MDS_20230101_20230131.xlsx'
            round_count (int): What round of file this is e.g. 1, 2, 3 etc
            mode (str): Upload in 'test' or 'live' mode? Defaults to 'test'.
            Use 'live' with care!

        Raises:
            IncorrectFileType: If user uploads a bad filetype
            FileNotFoundException: If we cannot find user file

        Returns:
            Path: filename of the new file we generated for matching purposes
        """

        # check file looks ok
        if input_file.suffix != ".xlsx" and input_file.suffix != ".zip":
            logging.error(
                "Only .xlsx or .zip (containing multiple csv's) are acceptable PMHC "
                "input files"
            )
            raise IncorrectFileType

        if not input_file.exists():
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
        # to PMHC. New filename should be in the format of:
        # round_4_PMHC_MDS_20200708_20200731_1686875652.zip
        current_timestamp = round(time.time())

        # self.upload_filename will be used by other class methods
        # e.g. to retrieve upload_id
        self.upload_filename = (
            f"round_{round_count}_{original_input_file.stem}_{current_timestamp}"
            f"{input_file.suffix}"
        )

        logging.info(
            f"New dynamically generated round {round_count} filename is: "
            f"'{self.upload_filename}'"
        )
        upload_filepath = f"{self.uploads_folder}/{self.upload_filename}"
        shutil.copyfile(input_file, upload_filepath)

        logging.info(
            f"Uploading '{self.upload_filename}' to PMHC as a '{mode}' file\n"
            "It usually takes approx 3-10 minutes for PMHC to process xlsx files "
            "depending on the number of months included in the data, less for zipped "
            "csv files (e.g. round 2 onward)"
        )

        self.page.goto("https://pmhc-mds.net/#/upload/add")
        self.page.wait_for_load_state()

        # upload in 'live' (e.g. completed file) or 'test' mode (error file)?
        logging.debug("Clicking 'Upload as test data' checkbox")
        if mode == "test":
            self.page.locator('[id="testUploadCheckbox"]').click()

        # This select field appears to be hard set from 13/06/2023 onward, so
        # this code has been disabled for now. We may need it again in the future.
        # logging.info("Clicking 'South Western Sydney ( PHN105 )'")
        # self.page.locator('#uploadOrgSelect').click()
        # self.page.select_option("#uploadOrgSelect", value="PHN105")

        # PMHC have hidden form fields which hold the filename etc
        # reveal these to make our life easier when debugging
        logging.debug("Unhiding #fileUpload field")
        self.page.eval_on_selector(
            "#fileUpload", "element => element.style.display = 'block'"
        )

        logging.debug("Adding upload file details")
        file_input = self.page.locator("#fileUpload")
        file_input.set_input_files(upload_filepath)

        logging.debug("Unhiding #uploadBtn")
        upload_button = self.page.locator("#uploadBtn")
        self.page.eval_on_selector("#uploadBtn", 'el => el.style.display = "block"')

        logging.debug("Clicking #uploadBtn")
        upload_button.click()
        delay = 60
        logging.info(
            f"Uploading '{self.upload_filename}' to PMHC in '{mode}' mode, "
            f"waiting {delay} seconds..."
        )
        self.show_loading_bar(delay, description="Waiting for PMHC upload...")
        self.page.wait_for_load_state()

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
            pmhc_username = self.user_info["username"]
            if self.is_upload_processing():
                logging.info(
                    f"An upload is currently processing for '{pmhc_username}' "
                    f"account, waiting for {delay} seconds..."
                )
            else:
                logging.info(
                    f"No upload is processing for '{pmhc_username}' account, "
                    "so we can stop waiting now"
                )
                break

            self.show_loading_bar(
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

        # first we need to get the uuid e.g. 94edf5e3-36b1-46d3-9178-bf3b142da6a1
        id_query = self.page.request.get(
            f"https://pmhc-mds.net/api/uploads?upload_uuid={upload_id}",
            headers={"Range": "0-49"},
        )

        id_json = id_query.json()

        time.sleep(0.5)
        for upload in id_json:
            uuid = upload["uuid"]
            url = f"https://pmhc-mds.net/api/organisations/{self.phn_identifier}/uploads/{uuid}"
            upload_errors_json = self.page.request.get(url)

            filename = f"{self.downloads_folder}/{upload_id}.json"
            with open(filename, "wb") as file:
                file.write(upload_errors_json.body())

            logging.info(f"Saved JSON file to disk: '{filename}'")

        # return whatever json file it last found to calling script
        # We'll need to make this more robust in the near future if
        # we start getting multiple files coming back
        return Path(filename)

    def show_loading_bar(self, delay: int, description: str):
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

    def get_json_request(self, url: str) -> str:
        """gets the JSON response for a given request to PMHC website

        Args:
            url (str): the PMHC API url to query
            e.g. https://pmhc-mds.net/api/current-user

        Returns:
            str: A string containing the JSON reponse from the request
        """
        self.page.goto(url)
        self.page.wait_for_load_state()
        user_query = self.page.request.get(url)

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
        pmhc_username = self.user_info["username"]
        json_list = self.get_json_request(
            f"https://pmhc-mds.net/api/uploads?username={pmhc_username}&sort=-date"
        )
        # see if any are in a 'processing' state
        for json in json_list:
            if "status" in json and json["status"] == "processing":
                return True

        # all ok, none are processing, we are free to now upload a new file
        return False

    def find_upload_id(self, pmhc_filename: str) -> str:
        """Uses the PMHC backend to get upload_id from filter results

        Args:
            pmhc_filename (str): PMHC filename to search for
            e.g. round_5_PMHC_MDS_20200708_20200731_1686871871.zip

        Returns:
            str: upload id e.g. 11ef7dcf
        """
        pmhc_username = self.user_info["username"]

        # this search should return exactly one result
        filter_page = f"https://pmhc-mds.net/api/uploads?filename={pmhc_filename}&username={pmhc_username}&test=1&sort=-date"
        filter_json = self.get_json_request(filter_page)

        num_filter_json_results = len(filter_json)
        if num_filter_json_results != 1:
            raise CouldNotFindPmhcUpload(
                f"Expected 1 filter search result - received {num_filter_json_results}"
            )

        uuid = filter_json[0]["uuid"]

        # the first 8 chars of the uuid is the upload_id
        self.upload_id = uuid[:8]

        # set other properties the class will use later
        # Convert the PMHC UTC date string into a formatted AEST datetime
        pmhc_utc_date = filter_json[0]["date"]

        # Convert the date string to a datetime object in UTC
        datetime_obj_utc = datetime.strptime(
            pmhc_utc_date, "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=pytz.UTC)

        # Convert the datetime object to AEST timezone
        aest_timezone = pytz.timezone("Australia/Sydney")
        datetime_obj_aest = datetime_obj_utc.astimezone(aest_timezone)

        # Format the datetime object as per the desired format
        self.upload_date = datetime_obj_aest.strftime("%d/%m/%Y %I:%M:%S %p")

        self.upload_link = f"https://pmhc-mds.net/#/upload/details/{uuid}/{self.phn_identifier}"

        self.upload_status = filter_json[0]["status"]
        if self.upload_status == "complete":
            self.complete = True
        else:
            self.complete = False

        return self.upload_id
