import logging
import pathlib

from docker_export import Image, Platform, RegistryAuth, export, get_layers_manifest
from docker_export import logger as de_logger

from image_creator.constants import logger

de_logger.setLevel(logging.WARNING)  # reduce docker-export logging
platform = Platform.parse("linux/arm64/v8")  # our only target arch


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
