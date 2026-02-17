import io
import re
from typing import Iterable, List, Set, Tuple
from urllib.parse import urljoin, urlparse

from core import EMAIL_REGEX, parse_domains_from_text, normalize_domain

COMMON_PATHS = ["", "/contact", "/about", "/support"]


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


def build_candidate_urls(domain: str) -> List[str]:
    urls = []
    for scheme in ("https", "http"):
        for path in COMMON_PATHS:
            urls.append(f"{scheme}://{domain}{path}")
    return urls


def extract_emails_from_html(html: str) -> Set[str]:
    from bs4 import BeautifulSoup

    emails = {m.group(0).lower() for m in EMAIL_REGEX.finditer(html or "")}
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.select("a[href^='mailto:']"):
        href = (link.get("href") or "").replace("mailto:", "")
        href = href.split("?")[0].strip()
        if EMAIL_REGEX.fullmatch(href):
            emails.add(href.lower())
    return emails


def crawl_domain_emails(domain: str) -> Set[str]:
    import requests
    from bs4 import BeautifulSoup

    found: Set[str] = set()
    visited = set()

    session = requests.Session()
    session.headers.update({"User-Agent": "EmailCollectorBot/1.0"})

    for url in build_candidate_urls(domain):
        if url in visited:
            continue
        visited.add(url)
        try:
            resp = session.get(url, timeout=8, allow_redirects=True)
            if resp.status_code >= 400:
                continue
            final_url = resp.url
            if urlparse(final_url).netloc and urlparse(final_url).netloc != domain:
                # Skip crawling redirected external domains
                continue
            found.update(extract_emails_from_html(resp.text))

            # Try to discover internal links to target pages from homepage variants
            if url.endswith(domain) or url.endswith(domain + "/"):
                soup = BeautifulSoup(resp.text, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    abs_url = urljoin(final_url, href)
                    parsed = urlparse(abs_url)
                    if parsed.netloc and parsed.netloc != domain:
                        continue
                    path = parsed.path.lower().rstrip("/")
                    if path in ("/contact", "/about", "/support") and abs_url not in visited:
                        visited.add(abs_url)
                        try:
                            sub_resp = session.get(abs_url, timeout=8)
                            if sub_resp.status_code < 400:
                                found.update(extract_emails_from_html(sub_resp.text))
                        except Exception:
                            continue
        except Exception:
            continue
    return found


def process_domains(domains: Iterable[str]):
    import pandas as pd

    rows = []
    csv_rows = []

    for domain in domains:
        has_dmarc, policy = get_dmarc_policy(domain)

        if policy == "reject":
            continue

        if not has_dmarc:
            display_policy = "no_dmarc"
        else:
            display_policy = policy

        if display_policy not in {"none", "quarantine", "no_dmarc"}:
            continue

        emails = sorted(crawl_domain_emails(domain))
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
            csv_rows.append(
                {"Domain": domain, "DMARC Policy": display_policy, "Email": ""}
            )

    return pd.DataFrame(rows), pd.DataFrame(csv_rows)


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="DMARC Email Collector", layout="wide")
    st.title("DMARC-aware Email Collector")
    st.write(
        "Upload domains or paste them manually. The app filters based on DMARC policy and extracts public emails from common pages."
    )

    uploaded_file = st.file_uploader("Upload .txt or .csv domain list", type=["txt", "csv"])
    text_input = st.text_area("Or paste domains (comma/newline separated)")

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
            result_df, csv_df = process_domains(domains)

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
