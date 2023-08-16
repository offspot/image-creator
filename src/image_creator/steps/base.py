from __future__ import annotations

import pathlib
from typing import Any

from offspot_config.inputs import File
from offspot_config.utils.misc import (
    copy_file,
    extract_xz_image,
    format_size,
    get_filesize,
)

from image_creator.constants import logger
from image_creator.steps import Step
from image_creator.utils.download import download_file


class DownloadImage(Step):
    name_ = "Fetching base image"

    def run(self, payload: dict[str, Any]) -> int:
        # we need to extract this into the actual target
        if payload["config"].base_file.getpath().suffix == ".xz":
            return self.run_compressed(payload["config"].base_file, payload)

        return self.run_uncompressed(payload["config"].base_file, payload)

    def run_uncompressed(self, base_file: File, payload: dict[str, Any]) -> int:
        target = payload["options"].output_path

        if base_file not in payload["cache"] and not base_file.is_local:
            logger.start_task(f"Downloading {base_file.geturl()} into {target}…")
            try:
                download_file(base_file.geturl(), target)
            except Exception as exc:
                logger.fail_task(str(exc))
                return 1
            else:
                logger.succeed_task(format_size(get_filesize(target)))
                if payload["cache"].should_cache(base_file):
                    logger.start_task("Adding Base Image to cache…")
                    if payload["cache"].introduce(base_file, target):
                        logger.succeed_task()
                    else:
                        logger.fail_task()

            return 0

        if base_file in payload["cache"]:
            src_path = payload["cache"][base_file].fpath
            message = f"Copying from cache… into {target}"
        else:
            src_path = base_file.getpath()
            message = f"Copying {src_path} into {target}…"

        logger.start_task(message)
        try:
            copy_file(src_path, target)
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task(format_size(get_filesize(target)))

        if base_file in payload["cache"]:
            payload["cache"][base_file] += 1

        return 0

    def run_compressed(self, base_file: File, payload: dict[str, Any]) -> int:
        target = payload["options"].output_path
        xz_fpath = payload["options"].build_dir.joinpath(base_file.getpath().name)
        remove_xz = False

        # we need to download it first
        if base_file not in payload["cache"] and not base_file.is_local:
            logger.start_task(f"Downloading {base_file.geturl()} into {xz_fpath}…")
            try:
                download_file(base_file.geturl(), xz_fpath)
            except Exception as exc:
                logger.fail_task(str(exc))
                return 1
            else:
                logger.succeed_task(format_size(get_filesize(xz_fpath)))
                remove_xz = True
                if payload["cache"].should_cache(base_file):
                    logger.start_task("Adding Base Image to cache…")
                    if payload["cache"].introduce(base_file, xz_fpath):
                        logger.succeed_task()
                    else:
                        logger.fail_task()
        else:
            xz_fpath = (
                payload["cache"][base_file].fpath
                if base_file in payload["cache"]
                else base_file.getpath()
            )

        return self.extract(base_file, payload, xz_fpath, target, remove_xz=remove_xz)

    def extract(
        self,
        base_file,
        payload: dict[str, Any],
        xz_fpath: pathlib.Path,
        target: pathlib.Path,
        *,
        remove_xz: bool,
    ) -> int:
        logger.start_task(f"Extracting {xz_fpath}…")
        try:
            extract_xz_image(xz_fpath, target)
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task(format_size(get_filesize(target)))

        if base_file in payload["cache"]:
            payload["cache"][base_file] += 1

        if remove_xz:
            logger.start_task(f"Removing {xz_fpath}…")
            try:
                xz_fpath.unlink(missing_ok=True)
            except Exception as exc:
                logger.fail_task(str(exc))
            else:
                logger.succeed_task()

        return 0
