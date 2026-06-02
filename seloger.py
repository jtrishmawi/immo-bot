"""
immo-bot — Seloger.com URL utilities.

parse_url  : browser URL  → query dict
build_url  : query dict   → browser URL
"""
from urllib.parse import quote, urlparse, parse_qs

BASE_URL = "https://www.seloger.com/classified-search"


def parse_url(url: str) -> dict:
    """Parse a Seloger search URL into a query dict compatible with build_criteria()."""
    qs = parse_qs(urlparse(url).query, keep_blank_values=False)
    return {k: ",".join(v) for k, v in qs.items()}


def build_url(query: dict) -> str:
    """Reconstruct a Seloger search URL from a query dict (e.g. from parse_url)."""
    if not query:
        raise ValueError("query is required.")
    qs = "&".join(f"{k}={quote(v, safe=',')}" for k, v in query.items())
    return f"{BASE_URL}?{qs}"


if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) < 2:
        print("Usage: python seloger.py <seloger_url>")
        print("       Parses a Seloger search URL and prints the query fields as JSON.")
    else:
        print(json.dumps(parse_url(sys.argv[1]), indent=2, ensure_ascii=False))
