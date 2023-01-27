import logging
import pathlib
from typing import Any, Dict

from docker_export import (
    Image,
    Platform,
    RegistryAuth,
    export,
    get_layers_from_v1_manifest,
    get_layers_manifest,
    get_manifests,
)
from docker_export import logger as de_logger

from image_creator.constants import logger

de_logger.setLevel(logging.WARNING)  # reduce docker-export logging
platform = Platform.parse("linux/arm64/v8")  # our only target arch


class OCIImage:
    kind: str = "image"  # Item interface

    def __init__(self, payload: Dict[str, Any]):
        self.oci: Image = Image.parse(payload["ident"])
        self.url = payload.get("url")
        self.filesize = int(payload["filesize"])
        self.fullsize = int(payload["fullsize"])
        self.is_in_cache = False

    @property
    def size(self) -> int:  # Item interface
        return self.filesize

    @property
    def source(self) -> str:  # Item interface
        return str(self.oci)

    def __repr__(self):
        return f"{self.__class__.__name__}<{repr(self.oci)}>"

    def __str__(self):
        return str(self.oci)


def image_exists(image: Image) -> bool:
    """whether image exists on the registry"""
    auth = RegistryAuth.init(image)
    auth.authenticate()
    try:
        get_layers_manifest(image=image, platform=platform, auth=auth)
    except Exception as exc:
        logger.exception(exc)
        return False
    return True


def download_image(image: Image, dest: pathlib.Path, build_dir: pathlib.Path):
    """download image into a tar file at dest"""
    export(image=image, platform=platform, to=dest, build_dir=build_dir)


def get_image_digest(image: Image) -> str:
    """Current digest for an Image

    Value of the current in-registry image for our platform.

    For v1 manifests and single-arch images, this is not the same value
    as in the registry's UI.
    Not much of a problem for us as images to be used here should be v2/multi
    and what's return is consistent and will be used only for comparison
    to check if a tag has been updated or not"""

    auth = RegistryAuth.init(image)
    auth.authenticate()
    fat_manifests = get_manifests(image, auth)

    if fat_manifests["schemaVersion"] == 1:
        return get_layers_from_v1_manifest(
            image=image, platform=platform, manifest=fat_manifests
        )["config"]["digest"]

    # image is single-platform, thus considered linux/amd64
    if "layers" in fat_manifests:
        if platform != platform.default():
            raise ValueError("Image not found (single)")
        return fat_manifests["config"]["digest"]
    else:
        # multi-platform image
        for arch_manifest in fat_manifests.get("manifests", []):
            if platform.match(arch_manifest.get("platform", {})):
                return arch_manifest["digest"]

    raise ValueError("Image not found (multi)")
