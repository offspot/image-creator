import pathlib
import re
import shutil
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from yaml import CLoader as Loader
    from yaml import load as yaml_load
except ImportError:
    # we don't NEED cython ext but it's faster so use it if avail.
    from yaml import Loader, load as yaml_load

from image_creator.constants import DATA_PART_PATH
from image_creator.utils.download import get_online_rsc_size
from image_creator.utils.misc import get_filesize
from image_creator.utils.oci_images import Image as OCIImage


def has_val(data, key: str) -> bool:
    value = data.get(key, "")
    if not value:
        return False
    if not isinstance(value, str):
        return False
    return bool(value)


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
        self.size = payload.get("size", -1)

    def fetch_size(self, force: Optional[bool] = False) -> int:
        """retrieve size of source, making sure it's reachable"""
        if not force and self.size >= 0:
            return self.size
        self.size = (
            get_filesize(self.getpath())
            if self.is_local
            else get_online_rsc_size(self.geturl())
        )
        return self.size

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


class Config(dict):
    """Parsed Image YAML Configuration"""

    @classmethod
    def read_from(cls, text: str):
        """Instanciate from yaml text"""
        return cls(**yaml_load(text, Loader=Loader))

    def init(self):
        """Prepare Config from yaml-parsed dict"""
        self.errors: List[Tuple[str, str]] = []

        self.base: File = self._get_base()
        self.all_files: List[File] = [
            File(payload) for payload in self.get("files", [])
        ]
        self.oci_images: List[OCIImage] = [
            OCIImage.parse(str(name)) for name in self.get("oci_images", [])
        ]
        return self.validate()

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def _get_base(self) -> File:
        """Infer url from flexible `base` and return a File"""
        url = self.get("base")
        match = re.match(r"^(?P<version>\d\.\d\.\d)(?P<extra>[a-z0-9\-\.\_]*)", url)
        if match:
            version = "".join(match.groups())
            url = f"https://drive.offspot.it/base/base-image-{version}.img.xz"
        return File({"url": url, "to": DATA_PART_PATH / "-"})

    def dig(self, path, default=None) -> Any:
        """get a value using it's dotted tree path"""
        data = self
        parts = path.split(".")
        for index in range(0, len(parts) - 1):
            data = data.get(parts[index], {})
        return data.get(parts[-1], default)

    @property
    def offspot_config(self) -> Dict:
        """parsed `offspot` subtree representing runtime-config file"""
        return self.get("offspot")

    @property
    def remote_files(self) -> List[File]:
        return [file for file in self.all_files if file.is_remote]

    @property
    def non_remote_files(self) -> List[File]:
        return [file for file in self.all_files if file.is_plain or file.is_local]

    def validate(self) -> bool:
        """whether Config can be run or not

        Feedback for user in self.errors"""

        # check for required props (only base ATM)
        for key in ("base",):
            if not self.get(key):
                self.errors.append(key, f"missing `{key}`")

        # check that files are OK
        files = self.get("files", [])
        if not isinstance(files, list):
            self.errors.append(("files", "not a list"))

        for file in files:
            if not isinstance(file, dict):
                self.errors.append("files", "not a dict")
            if not has_val(file, "to"):
                self.errors.append("files.to", "missing or invalid")
            if not has_val(file, "url") and not has_val(file, "content"):
                self.errors.append("files", "`url` or `content` must be set")

        # make sure no two-files have the same destination
        all_tos = [file.get("to") for file in files]
        if len(all_tos) != len(set(all_tos)):
            self.errors.append(("files", "using same `to:` target several times"))

        return self.is_valid
