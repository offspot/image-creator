from __future__ import annotations

import pathlib
import shutil
from typing import Any

from docker_export import export
from offspot_config.oci_images import OCIImage
from offspot_config.utils.misc import copy_file, format_size, get_filesize, rmtree

from image_creator.constants import Global, logger
from image_creator.steps import Step


def download_image(image: OCIImage, dest: pathlib.Path, build_dir: pathlib.Path):
    """download image into a tar file at dest"""
    export(image=image.oci, platform=Global.platform, to=dest, build_dir=build_dir)


class DownloadingOCIImages(Step):
    _name = "Downloading OCI Images"

    def run(self, payload: dict[str, Any]) -> int:
        logger.start_task("Creating OCI Images placeholder…")
        mount_point = payload["image"].p3_mounted_on
        images_dir = mount_point.joinpath("images")
        build_dir = payload["options"].build_dir.joinpath("oci_export")

        try:
            images_dir.mkdir(exist_ok=True, parents=True)
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task(images_dir)

        for image in payload["config"].all_images:
            target = images_dir.joinpath(f"{image.oci.fs_name}.tar")
            if image in payload["cache"]:
                logger.start_task(f"Copying OCI Image {image} from cache…")
                try:
                    copy_file(payload["cache"][image].fpath, target)
                    payload["cache"][image] += 1
                except Exception as exc:
                    logger.fail_task(str(exc))
                    return 1
                else:
                    logger.succeed_task(format_size(get_filesize(target)))
                    continue

            logger.add_task(
                f"Downloading OCI Image to {target.relative_to(mount_point)}…"
            )
            try:
                download_image(image=image, dest=target, build_dir=build_dir)
            except Exception as exc:
                logger.fail_task(str(exc))
                rmtree(build_dir)
                return 1
            else:
                logger.complete_download(
                    target.name, size=format_size(get_filesize(target))
                )
                if payload["cache"].should_cache(image):
                    logger.start_task(f"Adding OCI Image {image} to cache…")
                    if payload["cache"].introduce(image, target):
                        logger.succeed_task()
                    else:
                        logger.fail_task()

        return 0

    def copy_from_cache(
        self,
        payload: dict[str, Any],
        image: OCIImage,
        mount_point: pathlib.Path,
        target: pathlib.Path,
    ):
        logger.add_task(f"Copying cached image to {target.relative_to(mount_point)}…")
        try:
            shutil.copy2(payload["cache"][image].fpath, target)
        except Exception as exc:
            logger.fail_task(str(exc))
            return False
        else:
            logger.complete_download(
                target.name, size=format_size(get_filesize(target))
            )
        return True
