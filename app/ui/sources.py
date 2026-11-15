"""Prepare safe, compact source links without losing citation metadata."""
from urllib.parse import urlsplit


def prepare_sources(citations):
    sources = []
    for index, citation in enumerate(citations, 1):
        page = citation.get("printed_catalog_page")
        section = citation.get("section") or "Reference"
        label = f"[{index}] Catalog p. {page}" if page else f"[{index}] Source"
        label += " · " + section[:72]
        url = citation.get("source_url") or ""
        try:
            parsed = urlsplit(url)
            safe = parsed.scheme.lower() in ("https", "http") and bool(parsed.netloc)
        except ValueError:
            safe = False
        # Keep distinct sections/chunks even if their PDF URL is identical.
        sources.append({"label":label,"url":url if safe else None,"metadata":dict(citation)})
    return sources
