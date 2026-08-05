from __future__ import annotations

import os

ACADEMAX_DOI_BASE = os.getenv(
    "ACADEMAX_DOI_BASE", "https://www.academax.com/doi"
).rstrip("/")


def doi_url(doi: str | None) -> str | None:
    """Build Academax article URL from DOI."""
    if not doi:
        return None
    doi = str(doi).strip().lstrip("/")
    if not doi:
        return None
    return f"{ACADEMAX_DOI_BASE}/{doi}"
