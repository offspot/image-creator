from __future__ import annotations

import io
import pathlib
import re
from collections.abc import Callable

import requests
from offspot_config.utils.download import session
from offspot_config.utils.misc import is_http


def read_text_from(url: str) -> str:
    """Text content from an URL"""
    resp = session.get(url)
    resp.raise_for_status()
    return resp.text


def download_file(
    url: str,
    to: pathlib.Path | io.BytesIO,
    *,
    block_size: int | None = 4194304,  # 4MiB
    proxies: dict | None = None,
    only_first_block: bool = False,
    headers: dict[str, str] | None = None,
    on_data: Callable | None = None,
) -> int | requests.structures.CaseInsensitiveDict:  # type: ignore
    """Stream data from a URL to either a BytesIO object or a file
    Arguments -
        fpath - Path or BytesIO to write data into
        block_size - Size of each chunk of data read in one iteration
        proxies - A dict of proxies to be used
        https://requests.readthedocs.io/en/master/user/advanced/#proxies
        only_first_block - Whether to download only one (first) block

    Returns the total number of bytes downloaded and the response headers"""

    resp = session.get(
        url,
        stream=True,
        proxies=proxies,
        headers=headers,
    )
    resp.raise_for_status()

    total_downloaded = 0

    fp = open(to, "wb") if isinstance(to, pathlib.Path) else to

    for data in resp.iter_content(block_size):
        nb_received = len(data)
        total_downloaded += nb_received
        fp.write(data)

        if on_data:
            on_data(nb_received)

        # stop downloading/reading if we're just testing first block
        if only_first_block:
            break

    if isinstance(to, pathlib.Path):
        fp.close()
    else:
        fp.seek(0)
    return total_downloaded, resp.headers


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
