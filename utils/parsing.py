import re

DIMENSION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:\b|^)(\d+(?:[.,]\d+)?)\s*[xX×*]\s*"
        r"(\d+(?:[.,]\d+)?)\s*[xX×*]\s*"
        r"(\d+(?:[.,]\d+)?)\s*(?:cm|mm|inches|in|\")?(?:\b|$)",
    ),
    re.compile(
        r"(?:dimension|dims?|size)[\s:]*"
        r"(\d+(?:[.,]\d+)?)\s*[xX×*]\s*"
        r"(\d+(?:[.,]\d+)?)\s*[xX×*]\s*"
        r"(\d+(?:[.,]\d+)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:dimension|dims?|size)[\s:]*"
        r"(\d+(?:[.,]\d+)?)\s*x\s*"
        r"(\d+(?:[.,]\d+)?)",
        re.IGNORECASE,
    ),
]

PRICE_CLEAN_RE = re.compile(r"[^\d.]")


def extract_dimensions(text: str | None) -> str | None:
    if not text:
        return None

    for pattern in DIMENSION_PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groups()
            if len(groups) == 3:
                w, h, d = groups
                w = w.replace(",", ".")
                h = h.replace(",", ".")
                d = d.replace(",", ".")
                return f"{w} x {h} x {d}"
            elif len(groups) == 2:
                w, h = groups
                w = w.replace(",", ".")
                h = h.replace(",", ".")
                return f"{w} x {h}"

    return None


def clean_price(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = PRICE_CLEAN_RE.sub("", text)
    return cleaned if cleaned else None


def extract_domain(url: str) -> str:
    match = re.search(r"https?://(?:www\.)?([^/]+)", url.lower())
    if match:
        return match.group(1)
    return ""


def platform_from_url(url: str) -> str | None:
    domain = extract_domain(url)
    if "flipkart.com" in domain:
        return "flipkart"
    if "meesho.com" in domain:
        return "meesho"
    if "amazon." in domain:
        return "amazon"
    return None
