import pathlib
import shutil
import urllib.parse
from typing import Dict, Optional, Union

from image_creator.constants import DATA_PART_PATH
from image_creator.utils.download import get_online_rsc_size
from image_creator.utils.misc import get_filesize


class File:
    """In-Config reference to a file to write to the data partition

    Created from files entries in config:
    - to: str
        mandatory destination to save file into. Must be inside /data
    - size: optional[int]
        size of (expanded) content. If specified, must be >= source file
    - via: optional[str]
        method to process source file (not for content). Values in File.unpack_formats
    - url: optional[str]
        URL to download file from
    - content: optional[str]
        plain text content to write to destination

    one of content or url must be supplied. content has priority"""

    kind: str = "file"  # Item interface

    unpack_formats = [f[0] for f in shutil.get_unpack_formats()]

    def __init__(self, payload: Dict[str, Union[str, int]]):
        self.url = None
        self.content = payload.get("content")

        if not self.content:
            try:
                self.url = urllib.parse.urlparse(payload.get("url"))
            except Exception:
                raise ValueError(f"URL “{payload.get('url')}” is incorrect")

        self.to = pathlib.Path(payload["to"]).resolve()
        if not self.to.is_relative_to(DATA_PART_PATH):
            raise ValueError(f"{self.to} not a descendent of {DATA_PART_PATH}")

        self.via = payload.get("via", "direct")
        if self.via not in ("direct", "unzip", "untar"):
            raise NotImplementedError(f"Unsupported handler `{self.via}`")

        # initialized has unknown
        self._size = payload.get("size", -1)

    @property
    def source(self) -> str:  # Item interface
        return self.geturl()

    @property
    def size(self):  # Item interface
        if self._size < 0:
            return self.fetch_size()
        return self._size

    def fetch_size(self, force: Optional[bool] = False) -> int:
        """retrieve size of source, making sure it's reachable"""
        if not force and self._size >= 0:
            return self._size
        self._size = (
            get_filesize(self.getpath())
            if self.is_local
            else get_online_rsc_size(self.geturl())
        )
        return self._size

    def geturl(self) -> str:
        """URL as string"""
        try:
            return self.url.geturl()
        except Exception:
            return None

    def getpath(self) -> pathlib.Path:
        """URL as a local path"""
        return pathlib.Path(self.url.path).expanduser().resolve()

    @property
    def is_direct(self):
        return self.via == "direct"

    @property
    def is_plain(self) -> bool:
        """whether a plain text content to be written"""
        return self.content is not None

    @property
    def is_local(self) -> bool:
        """whether referencing a local file"""
        return not self.is_plain and self.url and self.url.scheme == "file"

    @property
    def is_remote(self) -> bool:
        """whether referencing a remote file"""
        return self.content is None and self.url and self.url.scheme != "file"

    def mounted_to(self, mount_point: pathlib.Path):
        """destination (to) path inside mount-point"""
        return mount_point.joinpath(self.to.relative_to(DATA_PART_PATH))

    def __repr__(self) -> str:
        msg = f"File(to={self.to}, via={self.via}"
        if self.url:
            msg += f", url={self.geturl()}"
        if self.content:
            msg += f", content={self.content.splitlines()[0][:10]}"
        msg += f", size={self.size})"
        return msg

    def __str__(self) -> str:
        return repr(self)
