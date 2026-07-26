import os

from dotenv import load_dotenv

load_dotenv()

MIN_DELAY = float(os.getenv("SCRAPER_MIN_DELAY", "2.0"))
MAX_DELAY = float(os.getenv("SCRAPER_MAX_DELAY", "6.0"))

MAX_RETRIES = int(os.getenv("SCRAPER_MAX_RETRIES", "2"))
HEADLESS = os.getenv("SCRAPER_HEADLESS", "true").lower() == "true"

PAGE_TIMEOUT = int(os.getenv("SCRAPER_PAGE_TIMEOUT", "30000"))
NAV_TIMEOUT = int(os.getenv("SCRAPER_NAV_TIMEOUT", "30000"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

GOOGLE_SHEET_CREDENTIALS = os.getenv(
    "GOOGLE_SHEET_CREDENTIALS",
    "credentials.json",
)

DEFAULT_SHEET_NAME = "Sheet1"

RESULT_COLUMNS = [
    "Title",
    "Description",
    "Image URL",
    "Dimensions",
    "HSN",
    "Status",
    "Error",
]
