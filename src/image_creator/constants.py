from __future__ import annotations

import logging
import pathlib
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass, field

from docker_export import Platform
from offspot_config.utils.misc import is_http, parse_size

from image_creator import __version__ as vers
from image_creator.logger import Logger

# where will data partition be monted on final device.
# used as reference for destinations in config file and in the UI
# TODO: moved to offspot_config
# DATA_PART_PATH = pathlib.Path("/data")
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

    config_path: pathlib.Path | None = None
    output_path: pathlib.Path | None = None
    build_dir: pathlib.Path | None = None
    cache_dir: pathlib.Path | None = None

    keep_failed: bool
    overwrite: bool
    concurrency: int
    max_size: int | None = None

    config_url: urllib.parse.ParseResult | None = None
    logger: Logger = field(init=False)

    def __post_init__(self):
        self.logger = self.get_logger()
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
                prefix="image-creator_build-dir_", ignore_cleanup_errors=True
            )
        self.build_dir = (
            pathlib.Path(self.BUILD_DIR or self.__build_dir.name).expanduser().resolve()
        )

        if self.CACHE_DIR:
            self.cache_dir = pathlib.Path(self.CACHE_DIR).expanduser().resolve()

        if isinstance(self.max_size, str):
            self.max_size = parse_size(self.max_size)

    @property
    def version(self):
        return vers

    @property
    def config_src(self) -> pathlib.Path | urllib.parse.ParseResult:
        if self.config_url is not None:
            return self.config_url
        if self.config_path is not None:
            return self.config_path
        raise OSError("Neither config_url nor config_path")

    @classmethod
    def get_logger(cls) -> Logger:
        return Logger()


class _Global:
    _ready: bool = False
    options: Options
    platform = Platform.parse("linux/arm64/v8")  # our only target arch
    default_eviction: str = "lru"

    @property
    def logger(self):
        return Global.options.logger if Global._ready else Options.get_logger()


Global = _Global()
logger = Global.logger
