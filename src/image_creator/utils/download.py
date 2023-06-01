import io
import pathlib
import re
from typing import Callable, Dict, Optional, Union

import requests

from image_creator.utils.misc import is_http

session = requests.Session()
# basic urllib retry mechanism.
# Sleep (seconds): {backoff factor} * (2 ** ({number of total retries} - 1))
# https://docs.descarteslabs.com/_modules/urllib3/util/retry.html
retries = requests.packages.urllib3.util.retry.Retry(
    total=10,  # Total number of retries to allow. Takes precedence over other counts.
    connect=5,  # How many connection-related errors to retry on
    read=5,  # How many times to retry on read errors
    redirect=20,  # How many redirects to perform. (to avoid infinite redirect loops)
    status=3,  # How many times to retry on bad status codes
    other=0,  # How many times to retry on other errors
    allowed_methods=False,  # Set of HTTP verbs that we should retry on (False is all)
    status_forcelist=[
        413,
        429,
        500,
        502,
        503,
        504,
    ],  # Set of integer HTTP status we should force a retry on
    backoff_factor=30,  # backoff factor to apply between attempts after the second try,
    raise_on_redirect=False,  # raise MaxRetryError instead of 3xx response
    raise_on_status=False,  # raise on Bad Status or response
    respect_retry_after_header=True,  # respect Retry-After header (status_forcelist)
)
retries.DEFAULT_BACKOFF_MAX = 30 * 60  # allow up-to 30mn backoff (default 2mn)
session.mount("http", requests.adapters.HTTPAdapter(max_retries=retries))


def get_online_rsc_size(url: str) -> int:
    """size (Content-Length) from url if specified, -1 otherwise (-2 on errors)"""
    try:
        resp = session.head(url, allow_redirects=True, timeout=60)
        # some servers dont offer HEAD
        if resp.status_code != 200:
            resp = requests.get(
                url,
                allow_redirects=True,
                timeout=60,
                stream=True,
                headers={"Accept-Encoding": "identity"},
            )
            resp.raise_for_status()
        return int(resp.headers.get("Content-Length") or -1)
    except Exception:
        return -2


def read_text_from(url: str) -> str:
    """Text content from an URL"""
    resp = session.get(url)
    resp.raise_for_status()
    return resp.text


def download_file(
    url: str,
    to: Union[pathlib.Path, io.BytesIO],
    block_size: Optional[int] = 1024,
    proxies: Optional[dict] = None,
    only_first_block: Optional[bool] = False,
    headers: Optional[Dict[str, str]] = None,
    on_data: Optional[Callable] = None,
) -> Union[int, requests.structures.CaseInsensitiveDict]:
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
    if isinstance(to, pathlib.Path):
        fp = open(to, "wb")

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


def get_digest(url: str, etag_only: Optional[bool] = False) -> str:
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
            return hist_resp.headers.get("Digest")

    etag = resp.headers.get("ETag")
    if etag:
        etag = re.sub(r'^"(.+)"$', r"\1", etag)

    if etag or etag_only:
        return etag

    length = resp.headers.get("Content-Length")
    modified = resp.headers.get("Last-Modified")
    if not length or not modified:
        return ""

    return f"{length}|{modified}"
