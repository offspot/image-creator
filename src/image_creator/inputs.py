import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Union

try:
    from yaml import CLoader as Loader
    from yaml import load as yaml_load
except ImportError:
    # we don't NEED cython ext but it's faster so use it if avail.
    from yaml import Loader, load as yaml_load

from image_creator.constants import DATA_PART_PATH
from image_creator.utils.file import File
from image_creator.utils.misc import enforce_types, is_list_of_dict, parse_size
from image_creator.utils.oci_images import OCIImage

WAYS = ("direct", "bztar", "gztar", "tar", "xztar", "zip")


def get_base_from(url: str) -> File:
    """Infer url from flexible `base` and return a File"""
    match = re.match(r"^(?P<version>\d\.\d\.\d)(?P<extra>[a-z0-9\-\.\_]*)", url)
    if match:
        version = "".join(match.groups())
        url = f"https://drive.offspot.it/base/base-image-{version}.img.xz"
    return File({"url": url, "to": DATA_PART_PATH / "-"})


@enforce_types
@dataclass(kw_only=True)
class OCIImageConfig:
    ident: str
    url: Optional[str] = None
    filesize: int
    fullsize: int


@enforce_types
@dataclass(kw_only=True)
class FileConfig:
    to: str
    url: Optional[str] = None
    content: Optional[str] = None
    via: Optional[str] = "direct"

    def __post_init__(self, *args, **kwargs):
        if self.via not in WAYS:
            raise ValueError(
                f"Incorrect value `{self.via}` for {type(self).__name__}.via"
            )
        if not self.url and not self.content:
            raise ValueError(
                f"Either {type(self).__name__}.url "
                f"or {type(self).__name__}.content must be set"
            )


@enforce_types
@dataclass(kw_only=True)
class OutputConfig:
    size: Optional[Union[int, str]] = None
    shrink: Optional[bool] = False
    compress: Optional[bool] = False

    def __post_init__(self):
        self.parse_size()

    def parse_size(self):
        if isinstance(self.size, int):
            return

        if self.size == "auto":
            self.size = None
            return
        try:
            self.size = parse_size(self.size)
        except Exception as exc:
            raise ValueError(
                f"Unable to parse `{self.size}` into size "
                f"for {type(self).__name__}.size ({exc})"
            )


@enforce_types
@dataclass(kw_only=True)
class MainConfig:
    base: Union[str, File]
    output: Optional[Union[Dict, OutputConfig]] = field(default_factory=OutputConfig)
    oci_images: List[OCIImageConfig]
    files: List[FileConfig]
    write_config: Optional[bool] = False
    offspot: Optional[Dict] = field(default_factory=dict)

    all_files: Optional[List[File]] = field(default_factory=list)
    all_images: Optional[List[OCIImage]] = field(default_factory=list)

    def __post_init__(self, *args, **kwargs):
        if isinstance(self.base, str):
            self.base = get_base_from(self.base)

        if isinstance(self.output, dict):
            self.output = OutputConfig(**self.output)

        all_tos = [fileconf.to for fileconf in self.files]
        dup_tos = [to for to in all_tos if all_tos.count(to) > 1]
        if len(dup_tos):
            raise ValueError(
                f"{type(self).__name__}.files: duplicate to target(s): "
                f"{','.join(dup_tos)}"
            )

        for conf in self.files:
            self.all_files.append(File(asdict(conf)))
        for conf in self.oci_images:
            self.all_images.append(OCIImage(asdict(conf)))

    @classmethod
    def read_from(cls, text: str):
        """Config from a YAML string config"""

        # parse YAML (Dict) will be our input to MainConfig
        payload = yaml_load(text, Loader=Loader)

        # build SubPolicies first (args of the main Policy)
        for name, sub_config_cls in {
            "oci_images": OCIImageConfig,
            "files": FileConfig,
        }.items():
            # remove he key from payload ; we'll replace it with actual SubConfig
            subload = payload.pop(name, [])

            if not is_list_of_dict(subload):
                raise ValueError(f"Unexpected type for Config.{name}: {type(subload)}")

            # create SubConfig (will fail in case of errors)
            payload[name] = [sub_config_cls(**item) for item in subload]

        # ready to create the actual main Policy
        return cls(**payload)

    @property
    def remote_files(self) -> List[File]:
        return [file for file in self.all_files if file.is_remote]

    @property
    def non_remote_files(self) -> List[File]:
        return [file for file in self.all_files if file.is_plain or file.is_local]
