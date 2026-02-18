import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from core import EMAIL_REGEX, parse_domains_from_text, normalize_domain

COMMON_PATHS = [
    "",
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/support",
    "/help",
    "/team",
]
CONTACT_KEYWORDS = (
    "contact",
    "support",
    "help",
    "about",
    "team",
    "sales",
)


@lru_cache(maxsize=2048)
def get_dmarc_policy(domain: str) -> Tuple[bool, str]:
    import dns.resolver

    query_name = f"_dmarc.{domain}"
    try:
        answers = dns.resolver.resolve(query_name, "TXT")
        txt_records = [b"".join(r.strings).decode("utf-8", errors="ignore") for r in answers]
    except Exception:
        return False, "no_dmarc"

    joined = " ".join(txt_records)
    match = re.search(r"\bp\s*=\s*([a-zA-Z]+)", joined)
    if not match:
        return True, "unknown"
    return True, match.group(1).lower()


def parse_domains_from_upload(uploaded_file) -> List[str]:
    import pandas as pd

    name = uploaded_file.name.lower()
    content = uploaded_file.getvalue()

    if name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(content), header=None)
        domains: List[str] = []
        for col in df.columns:
            domains.extend(df[col].dropna().astype(str).tolist())
        return sorted({d for d in (normalize_domain(x) for x in domains) if d})

    text = content.decode("utf-8", errors="ignore")
    return parse_domains_from_text(text)


def _canonical_host(host: str) -> str:
    return host.lower().replace("www.", "", 1)


def _is_same_domain(candidate_host: str, domain: str) -> bool:
    if not candidate_host:
        return True
    return _canonical_host(candidate_host) == _canonical_host(domain)


def _deobfuscate_email_text(text: str) -> str:
    normalized = text
    replacements = [
        (r"\s*\[\s*at\s*\]\s*", "@"),
        (r"\s*\(\s*at\s*\)\s*", "@"),
        (r"\s+at\s+", "@"),
        (r"\s*\[\s*dot\s*\]\s*", "."),
        (r"\s*\(\s*dot\s*\)\s*", "."),
        (r"\s+dot\s+", "."),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return normalized


def extract_emails_from_html(html: str) -> Set[str]:
    if not html:
        return set()

    normalized_html = _deobfuscate_email_text(html)
    emails = {m.group(0).lower() for m in EMAIL_REGEX.finditer(normalized_html)}

    # Fast fallback parser for mailto links without requiring HTML parser.
    for href in re.findall(r'mailto:([^"\'\s>]+)', html, flags=re.IGNORECASE):
        for candidate in href.split(","):
            item = candidate.split("?")[0].strip().lower()
            if EMAIL_REGEX.fullmatch(item):
                emails.add(item)

    return emails


def _build_candidate_urls(domain: str, base_url: str) -> List[str]:
    urls = [urljoin(base_url, path) for path in COMMON_PATHS]
    # keep order while deduping
    return list(dict.fromkeys(urls))


def _fetch_html(session, url: str, timeout: Tuple[int, int]) -> Tuple[str, Optional[str]]:
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        if resp.status_code >= 400:
            return url, None
        return resp.url, resp.text
    except Exception:
        return url, None


def _discover_priority_links(html: str, source_url: str, domain: str, limit: int = 12) -> List[str]:
    from bs4 import BeautifulSoup

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    discovered: List[str] = []

    for link in soup.find_all("a", href=True):
        text = (link.get_text(" ", strip=True) or "").lower()
        href = (link.get("href") or "").strip()
        if not href:
            continue

        abs_url = urljoin(source_url, href)
        parsed = urlparse(abs_url)
        if not _is_same_domain(parsed.netloc, domain):
            continue

        path = (parsed.path or "").lower().rstrip("/")
        if path in ("/contact", "/contact-us", "/about", "/about-us", "/support", "/help", "/team"):
            discovered.append(abs_url)
            continue

        if any(k in path for k in CONTACT_KEYWORDS) or any(k in text for k in CONTACT_KEYWORDS):
            discovered.append(abs_url)

    # keep order + cap
    deduped = list(dict.fromkeys(discovered))
    return deduped[:limit]


def _resolve_base_url(session, domain: str, timeout: Tuple[int, int]) -> Optional[str]:
    for candidate in (f"https://{domain}", f"http://{domain}"):
        final_url, html = _fetch_html(session, candidate, timeout)
        if not html:
            continue
        host = urlparse(final_url).netloc
        if not _is_same_domain(host, domain):
            continue
        return final_url
    return None


def crawl_domain_emails(domain: str) -> Set[str]:
    import requests

    timeout = (3, 6)
    found: Set[str] = set()

    session = requests.Session()
    session.headers.update({"User-Agent": "EmailCollectorBot/1.1"})

    base_url = _resolve_base_url(session, domain, timeout)
    if not base_url:
        return found

    candidate_urls = _build_candidate_urls(domain, base_url)
    page_html: Dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=min(8, len(candidate_urls) or 1)) as pool:
        futures = {pool.submit(_fetch_html, session, u, timeout): u for u in candidate_urls}
        for future in as_completed(futures):
            final_url, html = future.result()
            if not html:
                continue
            host = urlparse(final_url).netloc
            if not _is_same_domain(host, domain):
                continue
            page_html[final_url] = html
            found.update(extract_emails_from_html(html))

    # discover more contact-like pages from fetched pages and crawl those in parallel
    discovered_urls: List[str] = []
    for source_url, html in page_html.items():
        discovered_urls.extend(_discover_priority_links(html, source_url, domain))

    extra_urls = [u for u in dict.fromkeys(discovered_urls) if u not in page_html]
    if extra_urls:
        with ThreadPoolExecutor(max_workers=min(8, len(extra_urls))) as pool:
            futures = {pool.submit(_fetch_html, session, u, timeout): u for u in extra_urls}
            for future in as_completed(futures):
                final_url, html = future.result()
                if not html:
                    continue
                host = urlparse(final_url).netloc
                if not _is_same_domain(host, domain):
                    continue
                found.update(extract_emails_from_html(html))

    return found


def process_domains(domains: Iterable[str], workers: int = 6):
    import pandas as pd

    domains = list(domains)
    rows = []
    csv_rows = []

    def process_one(domain: str):
        has_dmarc, policy = get_dmarc_policy(domain)

        if policy == "reject":
            return None

        display_policy = "no_dmarc" if not has_dmarc else policy
        if display_policy not in {"none", "quarantine", "no_dmarc"}:
            return None

        emails = sorted(crawl_domain_emails(domain))
        return domain, display_policy, emails

    with ThreadPoolExecutor(max_workers=min(workers, len(domains) or 1)) as pool:
        futures = {pool.submit(process_one, d): d for d in domains}
        for future in as_completed(futures):
            result = future.result()
            if not result:
                continue
            domain, display_policy, emails = result
            rows.append(
                {
                    "Domain": domain,
                    "DMARC Policy": display_policy,
                    "Extracted Emails": "; ".join(emails),
                }
            )
            if emails:
                for email in emails:
                    csv_rows.append(
                        {"Domain": domain, "DMARC Policy": display_policy, "Email": email}
                    )
            else:
                csv_rows.append({"Domain": domain, "DMARC Policy": display_policy, "Email": ""})

    result_df = pd.DataFrame(rows)
    if not result_df.empty:
        result_df = result_df.sort_values("Domain").reset_index(drop=True)

    csv_df = pd.DataFrame(csv_rows)
    if not csv_df.empty:
        csv_df = csv_df.sort_values(["Domain", "Email"]).reset_index(drop=True)

    return result_df, csv_df


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="DMARC Email Collector", layout="wide")
    st.title("DMARC-aware Email Collector")
    st.write(
        "Upload domains or paste them manually. Domains with DMARC p=reject are skipped."
    )

    uploaded_file = st.file_uploader("Upload .txt or .csv domain list", type=["txt", "csv"])
    text_input = st.text_area("Or paste domains (comma/newline separated)")
    workers = st.slider("Parallel workers", min_value=1, max_value=16, value=6)

    domains: List[str] = []
    if uploaded_file is not None:
        domains.extend(parse_domains_from_upload(uploaded_file))
    if text_input.strip():
        domains.extend(parse_domains_from_text(text_input))
    domains = sorted(set(domains))

    st.caption(f"Detected domains: {len(domains)}")

    if st.button("Run collection", type="primary"):
        if not domains:
            st.warning("Please provide at least one domain.")
            return

        with st.spinner("Checking DMARC and crawling pages..."):
            result_df, csv_df = process_domains(domains, workers=workers)

        st.subheader("Results")
        st.dataframe(result_df, use_container_width=True)

        csv_bytes = csv_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download extracted emails CSV",
            data=csv_bytes,
            file_name="extracted_emails.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
