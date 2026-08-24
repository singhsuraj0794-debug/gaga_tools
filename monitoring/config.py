import os
from pathlib import Path
from dotenv import load_dotenv

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

_MONITOR_DIR = os.path.dirname(os.path.abspath(__file__))
LIGHTHOUSE_CLI = os.getenv("LIGHTHOUSE_CLI", os.path.join(_MONITOR_DIR, "node_modules", ".bin", "lighthouse"))

PAGE_TIMEOUT = int(os.getenv("MONITOR_PAGE_TIMEOUT", "30000"))
NAV_TIMEOUT = int(os.getenv("MONITOR_NAV_TIMEOUT", "30000"))
OTP_TIMEOUT = int(os.getenv("MONITOR_OTP_TIMEOUT", "30"))
OTP_POLL_INTERVAL = int(os.getenv("MONITOR_OTP_POLL_INTERVAL", "3"))

VIEWPORT = {"width": 430, "height": 932}
VIEWPORT_DESKTOP = {"width": 1440, "height": 900}
DEVICE_SCALE_FACTOR = 2
GEOLOCATION = {"latitude": 19.4560, "longitude": 72.8054}

PLATFORMS = ["mweb", "web"]  # mweb = mobile, web = desktop

URLS = {
    "home": os.getenv("MONITOR_URL_HOME", "https://gajab.go.link/k9bGV"),
    "category": os.getenv("MONITOR_URL_CATEGORY", "https://gajab.com/product-list/all"),
    "product_detail": os.getenv(
        "MONITOR_URL_PRODUCT_DETAIL",
        "https://gajab.com/product-detail/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914",
    ),
}

CATEGORIES = [
    {"name": "all", "url": "https://gajab.com/product-list/all", "nav_text": "All Products", "direct_url": True},
    {"name": "home-kitchen", "url": "https://gajab.com/product-list/home-kitchen/1", "nav_text": "Home & Kitchen"},
    {"name": "toys-games", "url": "https://gajab.com/product-list/toys-games/17", "nav_text": "Toys & Games"},
    {"name": "fashion-accessories", "url": "https://gajab.com/product-list/fashion-accessories/44", "nav_text": "Fashion Accessories"},
    {"name": "electronics", "url": "https://gajab.com/product-list/electronics/49", "nav_text": "Electronics"},
]

THRESHOLDS = {
    "performance_score": float(os.getenv("THRESHOLD_PERFORMANCE_SCORE", "0.5")),
    "lcp_ms": int(os.getenv("THRESHOLD_LCP_MS", "2500")),
    "cls": float(os.getenv("THRESHOLD_CLS", "0.1")),
    "tbt_ms": int(os.getenv("THRESHOLD_TBT_MS", "300")),
    "si_ms": int(os.getenv("THRESHOLD_SI_MS", "4000")),
}

TIME_BUDGETS_SECONDS = {
    "home_page_load": int(os.getenv("BUDGET_HOME_PAGE_LOAD", "15")),
    "home_products_populate": int(os.getenv("BUDGET_HOME_PRODUCTS_POPULATE", "5")),
    "category_page_load": int(os.getenv("BUDGET_CATEGORY_PAGE_LOAD", "12")),
    "product_detail_load": int(os.getenv("BUDGET_PRODUCT_DETAIL_LOAD", "15")),
    "login_submit": int(os.getenv("BUDGET_LOGIN_SUBMIT", "3")),
    "otp_receive": int(os.getenv("BUDGET_OTP_RECEIVE", "30")),
    "bargain_flow": int(os.getenv("BUDGET_BARGAIN_FLOW", "20")),
    "add_to_cart": int(os.getenv("BUDGET_ADD_TO_CART", "5")),
    "checkout_nav": int(os.getenv("BUDGET_CHECKOUT_NAV", "20")),
}

MONITOR_PHONE = os.getenv("MONITOR_PHONE", "")

TWILIO_SID = os.getenv("TWILIO_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")
EMAIL_SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "587"))

PAGESPEED_API_KEY = os.getenv("PAGESPEED_API_KEY", "")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "groq/compound-mini")

PAGESPEED_URLS = list(URLS.values())
