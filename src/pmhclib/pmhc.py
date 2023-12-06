# This class interacts with the PMHC website, tracking uploads and
# their corresponding error JSON files.
# The script uses Python Playwright to do this
# This script is useful when doing multiple error removal runs with
# Tested under Ubuntu WSL and PowerShell.
# --no-headless runs best under PowerShell (it's slower under Ubuntu WSL)
#
# No login details are saved anywhere
# To speed up usage when doing repeated calls, create the following local env variables:
# PMHC_USERNAME
# PMHC_PASSWORD
#
import logging
import mimetypes
import os
import platform
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from enum import Enum, unique
from getpass import getpass
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright
from rich.progress import Progress, TimeElapsedColumn


class FileNotFoundException(Exception):
    """Custom error handler for when no file is found"""


class IncorrectFileType(Exception):
    """Custom error handler for when an incorrect file is provided"""


class InvalidPmhcUser(Exception):
    """Custom error handler for when a PMHC login is unsuccessful"""


class CouldNotFindPmhcUpload(Exception):
    """Custom error handler for when an upload cannot be found on PMHC"""


class SecureString(str):
    """Show `'***'` instead of string value in tracebacks"""

    def __repr__(self):
        return "***"


@dataclass
class PMHCSpecificationRepresentation:
    """Dataclass which provides structure for PMHCSpecification Enum."""

    term: str
    filter_term: str


@unique
class PMHCSpecification(PMHCSpecificationRepresentation, Enum):
    """Enum of valid PMHC specifications"""

    ALL = "meta", "META 4.0"
    PMHC = "pmhc", "PMHC 4.0"
    HEADSPACE = "headspace", "headspace 2.0"
    WAYBACK = "wayback", "WAYBACK 3.0"


class PMHC:
    """This class wraps the unofficial PMHC API.

    Use it to automate tasks such as uploading to the PMHC website,
    downloading error reports, downloading PMHC extracts, etc.

    Usage:

    This class is intended to be used with a context manager. This ensures
    that the Playwright browser context is correctly closed. For
    example:

    >>> with PMHC() as pmhc:
    ...     pmhc.login()
    ...     pmhc.download_error_json('94edf5e3-36b1-46d3-9178-bf3b142da6a1')

    :param headless: Use headless browser
    """

    def __enter__(self):
        """Initialise playwright. (Called automatically by context
        manager.)
        """
        self.p = sync_playwright().start()
        self.browser = self.p.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context()
        self.context.set_default_timeout(self.default_timeout)
        self.page = self.context.new_page()
        return self  # Return the instance of this class

    def __exit__(self, exc_type, exc_value, traceback):
        """Shutdown playwright. (Called automatically when context
        manager exits.)
        """
        # exc_type, exc_value, and traceback are required parameters in __exit__()
        self.browser.close()
        self.p.stop()

    def __init__(self, headless: bool = True):
        # user_info is set by login()
        self.user_info = None
        self.default_timeout = 60000
        self.phn_identifier = "PHN105"

        # save whether to use a headless browser instance or not
        self.headless = headless

    def login(self):
        """Logs in to PMHC website. This allows us to reuse the login the session
        across other class methods.

        Set the following environment variables to skip interactive login prompt:

        - `PMHC_USERNAME`
        - `PMHC_PASSWORD`
        """

        # Prompt user for credentials if not set in env.
        username = os.getenv("PMHC_USERNAME")
        password = SecureString(os.getenv("PMHC_PASSWORD") or "")

        while not username:
            username = input("Enter PMHC username: ")

        while not password:
            password = SecureString(
                getpass("Enter PMHC password (keyboard input will be hidden): ")
            )

        print("Logging into PMHC website")
        self.page.goto("https://pmhc-mds.net")
        self.page.wait_for_load_state()
        self.page.locator('[id="loginBtn"]').click()
        self.page.wait_for_load_state()

        logging.info("Entering username")
        username_field = self.page.locator('input[id="username"]')
        username_field.fill(username)
        username_field.press("Enter")
        self.page.wait_for_load_state()

        logging.info("Entering password")
        password_field = self.page.locator('input[id="password"]')
        password_field.fill(password)
        password_field.press("Enter")

        self.page.wait_for_load_state()

        # confirm login was successful
        user_query = self.page.request.get("https://pmhc-mds.net/api/current-user")
        self.user_info = user_query.json()

        # error key will be present if login was unsuccessful
        if "error" in self.user_info:
            raise InvalidPmhcUser(
                "PMHC login was unsuccessful. Are you sure you entered "
                "correct credentials?"
            )

    def upload_file(
        self,
        input_file: Path,
        test: bool = True,
    ) -> Path:
        """Uploads a user specified file to PMHC website.

        :param input_file: Path to the file e.g. `'cc9dd7b5.csv'`
            e.g. `'PMHC_MDS_20230101_20230131.xlsx'`
        :param test: Upload in 'test' or 'live' mode? Defaults to `True`
            ('test'). Use `False` ('live') with care!

        :raise IncorrectFileType: If user uploads a bad filetype
        :raise FileNotFoundException: If we cannot find user file
        :return: Filename of the new file we generated for matching purposes
        """

        # check file looks ok
        if input_file.suffix not in (".xlsx", ".zip"):
            raise IncorrectFileType(
                "Only .xlsx or .zip (containing multiple csv's) are acceptable PMHC "
                "input files"
            )

        if not input_file.exists():
            raise FileNotFoundException(
                "Input file does not exist - please check the file path and try again"
            )

        # check no uploads are currently being processed
        # PMHC only allows one upload at a time per user account.
        # This usually only occurs if the user is also using their browser to upload
        # manually at the same time as running this script
        self.wait_for_upload()

        mode = "test" if test else "live"
        print(
            f"Uploading '{input_file}' to PMHC as a '{mode}' file\n"
            "It usually takes approx 3-10 minutes for PMHC to process xlsx files "
            "depending on the number of months included in the data, less for zipped "
            "csv files (e.g. round 2 onward)"
        )

        # First PUT the file and receive a uuid
        with open(input_file, "rb") as file:
            upload_response = self.page.request.put(
                "https://uploader.strategicdata.com.au/upload",
                multipart={
                    "file": {
                        "name": input_file.name,
                        "mimeType": mimetypes.guess_type(input_file)[0],
                        "buffer": file.read(),
                    }
                },
            )

        upload_status = upload_response.json()
        logging.debug(f"Upload status:")
        logging.debug(upload_status)

        uuid = upload_status["id"]

        # Second POST the upload details
        # This is required to register the upload with the PMHC portal
        post_response = self.page.request.post(
            f"https://pmhc-mds.net/api/organisations/{self.phn_identifier}/uploads",
            data={
                "uuid": uuid,
                "filename": input_file.name,
                "test": test,
                "encoded_organisation_path": self.phn_identifier,
            },
        )
        logging.info("Upload details POST response:")
        logging.info(post_response)
        logging.info(post_response.text())

        return uuid

    def wait_for_upload(self):
        """Waits for a PMHC upload to complete processing in 'test' mode"""

        # check to see if the PMHC upload queue is free
        delay = 10
        with Progress(*Progress.get_default_columns(), TimeElapsedColumn()) as progress:
            processing_task = progress.add_task(
                "Checking PMHC upload queue...", total=None
            )
            while self.is_upload_processing():
                progress.update(
                    processing_task, description="Waiting for PMHC processing..."
                )
                time.sleep(delay)

    def download_error_json(self, uuid: str, download_folder: Path = Path(".")) -> Path:
        """Downloads a JSON error file from PMHC
        This is useful for matching against uploaded files and processing

        :param uuid: PMHC upload uuid from View Uploads page. For
            example: `'94edf5e3-36b1-46d3-9178-bf3b142da6a1'`.
            The uuid is found in the URL to the upload summary page.
        :param download_folder: Location to save the downloaded error
            JSON.

        :return: Path to JSON file saved to local disk
        """

        url = f"https://pmhc-mds.net/api/organisations/{self.phn_identifier}/uploads/{uuid}"
        upload_errors_json = self.page.request.get(url)

        download_folder.mkdir(parents=True, exist_ok=True)
        filename = download_folder / f"{uuid}.json"
        with open(filename, "wb") as file:
            file.write(upload_errors_json.body())

        logging.info(f"Saved JSON file to disk: '{filename}'")

        # Remove download body from memory. Otherwise it will stay in
        # memory so long as the PMHC class is in use.
        upload_errors_json.dispose()

        return filename

    def is_upload_processing(self) -> bool:
        """Checks if the user has an upload currently processing in either live or
        test mode. Useful for checking before we do certain actions e.g. try upload
        another file, because this script can only handle one 'processing' file at a time

        :return: `True` if an upload is currently processing, otherwise `False`.
        """
        # Get a list of all this user's 'test' uploads ('processing', 'complete'
        # and 'error' status)
        pmhc_username = self.user_info["username"]
        json_list = self.page.request.get(
            f"https://pmhc-mds.net/api/uploads?username={pmhc_username}&sort=-date"
        ).json()
        # see if any are in a 'processing' state
        for json in json_list:
            if "status" in json and json["status"] == "processing":
                return True

        # all ok, none are processing, we are free to now upload a new file
        return False

    def download_pmhc_mds(
        self,
        output_directory: Path = Path("."),
        start_date: date = date.today() - timedelta(days=30),
        end_date: date = date.today(),
        organisation_path: Optional[str] = None,
        specification: PMHCSpecification = PMHCSpecification.ALL,
        without_associated_dates: bool = True,
        matched_episodes: bool = True,
    ) -> Path:
        """Extract PMHC MDS Data within the date range. If no date range
        is given, `start_date` defaults to 30 days before the current
        date and `end_date` defaults to the current date.

        :param output_directory: directory to save download
        :param start_date: start date for extract
        :param end_date: end date for extract (default: today)
        :param organisation_path: PHN identifier (default: inherited
            from parent class.)
        :param specification: Specification for extract. (default:
            `PMHCSpecification.ALL`, which returns data from all
            specifications
        :param without_associated_dates: Enable extract option
            "Include data without associated dates"
        :param matched_episodes: Enable extract option
            "Include all data associated with matched episodes"

        :return: Path to downloaded extract.
        """

        if organisation_path is None:
            organisation_path = self.phn_identifier

        # Wait for queued download to be processed
        with Progress(*Progress.get_default_columns(), TimeElapsedColumn()) as progress:
            extract_task = progress.add_task("Checking for PMHC extract...", total=None)

            # Queue download from PMHC
            progress.update(extract_task, description="Queuing extract...")
            params = {
                "organisation_path": f"{organisation_path}",
                "encoded_organisation_path": f"{organisation_path}",
                "file_type": "csv",
                "start_date": f"{start_date:%Y-%m-%d}",
                "end_date": f"{end_date:%Y-%m-%d}",
                # These need to be interpreted as a JS boolean
                # (true or 1, rather than True).
                "childless": int(without_associated_dates),
                "all_episode_children": int(matched_episodes),
                "spec_type": specification.term,
            }
            download_request = self.page.request.get(
                "https://pmhc-mds.net/api/extract/csv",
                params=params,
            )
            download_response = download_request.json()
            download_uuid = download_response["uuid"]

            progress.update(extract_task, description="Waiting for extract...")
            # We know the URL which will give us the final download URL,
            # as we have the uuid. However, the URL doesn't exist
            # immediately. We loop until we get a success code.
            request_ok = False
            while not request_ok:
                time.sleep(30)
                download_url_request = self.page.request.get(
                    f"https://pmhc-mds.net/api/extract/{download_uuid}/fetch"
                )
                request_ok = download_url_request.ok

            download_url_json = download_url_request.json()
            download_url = download_url_json["location"]

            progress.update(extract_task, description="Downloading extract...")
            download = self.page.request.get(download_url)
            output_file = output_directory / f"pmhc_extract_{start_date}_{end_date}.zip"
            logging.info(f"Saving output to {output_file}")
            with open(output_file, "wb") as fp:
                fp.write(download.body())

            # Remove download body from memory. Otherwise it will stay
            # in memory so long as the PMHC class is in use.
            download.dispose()

            return output_file
