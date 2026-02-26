import re
from typing import List, Optional, Set
from urllib.parse import urlparse

EMAIL_REGEX = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def normalize_domain(value: str) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None

    if "://" in value:
        parsed = urlparse(value)
        host = parsed.netloc
    else:
        host = value.split("/")[0]

    host = host.split(":")[0].strip().lower().rstrip(".")
    return host or None


def parse_domains_from_text(raw_text: str) -> List[str]:
    parts = re.split(r"[\n,;\t\s]+", raw_text or "")
    domains = [normalize_domain(part) for part in parts if part.strip()]
    return sorted({d for d in domains if d})


def extract_emails_from_text(text: str) -> Set[str]:
    return {m.group(0).lower() for m in EMAIL_REGEX.finditer(text or "")}
