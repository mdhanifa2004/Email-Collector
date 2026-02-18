from app import extract_emails_from_html
from core import extract_emails_from_text, parse_domains_from_text


def test_parse_domains_from_text_deduplicates_and_normalizes():
    raw = "Example.com\nhttps://Sub.Example.com/path, EXAMPLE.com"
    assert parse_domains_from_text(raw) == ["example.com", "sub.example.com"]


def test_extract_emails_from_text():
    text = "Contact team@example.com and Sales@example.com"
    assert extract_emails_from_text(text) == {"team@example.com", "sales@example.com"}


def test_extract_emails_from_html_handles_obfuscation_and_multi_mailto():
    html = """
    Contact: info [at] example [dot] com
    <a href='mailto:sales@example.com,support@example.com?subject=Hello'>Mail us</a>
    """
    assert extract_emails_from_html(html) == {
        "info@example.com",
        "sales@example.com",
        "support@example.com",
    }
