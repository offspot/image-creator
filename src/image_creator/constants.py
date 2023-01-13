import logging
import pathlib
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from typing import Union

from image_creator import __version__ as vers
from image_creator.logger import Logger
from image_creator.utils.misc import is_http

# where will data partition be monted on final device.
# used as reference for destinations in config file and in the UI
DATA_PART_PATH = pathlib.Path("/data")
# version of the python interpreter
pyvers = ".".join([str(p) for p in sys.version_info[:3]])
banner: str = rf"""
  _                                                      _
 (_)_ __ ___   __ _  __ _  ___        ___ _ __ ___  __ _| |_ ___  _ __
 | | '_ ` _ \ / _` |/ _` |/ _ \_____ / __| '__/ _ \/ _` | __/ _ \| '__|
 | | | | | | | (_| | (_| |  __/_____| (__| | |  __/ (_| | || (_) | |
 |_|_| |_| |_|\__,_|\__, |\___|      \___|_|  \___|\__,_|\__\___/|_|
                    |___/                                       v{vers}|py{pyvers}

"""


@dataclass(kw_only=True)
class Options:
    """Command-line options"""

    CONFIG_SRC: str
    OUTPUT: str
    BUILD_DIR: str
    CACHE_DIR: str

    show_cache: bool
    check_only: bool
    debug: bool

    config_path: pathlib.Path = None
    output_path: pathlib.Path = None
    build_dir: pathlib.Path = None
    cache_dir: pathlib.Path = None

    keep_failed: bool
    overwrite: bool
    concurrency: int

    config_url: urllib.parse.ParseResult = None
    logger: Logger = Logger()

    def __post_init__(self):
        if is_http(self.CONFIG_SRC):
            self.config_url = urllib.parse.urlparse(self.CONFIG_SRC)
        else:
            self.config_path = pathlib.Path(self.CONFIG_SRC).expanduser().resolve()

        if self.debug:
            self.logger.setLevel(logging.DEBUG)

        self.output_path = pathlib.Path(self.OUTPUT).expanduser().resolve()

        if not self.BUILD_DIR:
            # holds reference to tempdir until Options is released
            # and will thus automatically remove actual folder
            self.__build_dir = tempfile.TemporaryDirectory(
                prefix="image-creator_build-dir", ignore_cleanup_errors=True
            )
        self.build_dir = (
            pathlib.Path(self.BUILD_DIR or self.__build_dir.name).expanduser().resolve()
        )

        if self.CACHE_DIR:
            self.cache_dir = pathlib.Path(self.CACHE_DIR).expanduser().resolve()

    @property
    def version(self):
        return vers

    @property
    def config_src(self) -> Union[pathlib.Path, urllib.parse.ParseResult]:
        return self.config_url or self.config_path


class _Global:
    options = None

    @property
    def logger(self):
        return Global.options.logger if Global.options else Options.logger


Global = _Global()
logger = Global.logger
