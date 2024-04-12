from __future__ import annotations

import re

from offspot_config.utils.download import session
from offspot_config.utils.misc import is_http


def read_text_from(url: str) -> str:
    """Text content from an URL"""
    resp = session.get(url)
    resp.raise_for_status()
    return resp.text


def get_digest(url: str, *, etag_only: bool | None = False) -> str:
    """Digest for an arbitrary URI -- or empty string

    Returns:
    - ETag (value) if found
    - A commbination of of Content-Length and Last-Modified headers if both are present
    - "" if etag not found and etag_only set
    - "" if etag not found and either Content-Length or Last-Modified if not found

    Should be enough to query whether an URL has been updated or not. If server
    doesn't specify those headers, resource should be considered updated"""

    # work only on http(s) URLs
    if not is_http(url):
        return ""

    resp = session.get(url, stream=True)
    resp.raise_for_status()

    # if there has been redirects, loop through each in order,
    # looking for MirrorBrain's Digest header and use it
    for hist_resp in resp.history:
        if hist_resp.headers.get("Digest"):
            return hist_resp.headers["Digest"]

    etag = resp.headers.get("ETag", "")
    if etag:
        etag = re.sub(r'^"(.+)"$', r"\1", etag)

    if etag or etag_only:
        return etag

    length = resp.headers.get("Content-Length", "")
    modified = resp.headers.get("Last-Modified", "")
    if not length or not modified:
        return ""

    return f"{length}|{modified}"
