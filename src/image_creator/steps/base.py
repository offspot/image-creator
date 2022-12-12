import shutil
from typing import Any, Dict

from image_creator.constants import logger
from image_creator.inputs import File
from image_creator.steps import Step
from image_creator.utils.download import download_file
from image_creator.utils.misc import extract_xz_image, format_size, get_filesize


class DownloadImage(Step):
    name = "Fetching base image"

    def run(self, payload: Dict[str, Any]) -> int:
        """whether system requirements are satisfied"""
        base_file = payload["config"].base

        # no need to download
        if base_file.is_local:
            return self.run_local(base_file, payload)

        # we'll have to download it
        return self.run_remote(base_file, payload)

    def run_remote(self, base_file: File, payload: Dict[str, Any]) -> int:
        rem_path = base_file.getpath()

        # we'll have to download it to build-dir then extract
        if rem_path.suffix == ".xz":
            xz_fpath = payload["options"].build_dir.joinpath(rem_path.name)
            logger.start_task(f"Downloading {base_file.geturl()} into {xz_fpath}…")
            try:
                download_file(base_file.geturl(), xz_fpath)
            except Exception as exc:
                logger.fail_task(str(exc))
                return 1
            else:
                logger.succeed_task(format_size(get_filesize(xz_fpath)))

            logger.start_task(
                f"Extracting {xz_fpath} into {payload['options'].output_path}…"
            )
            try:
                extract_xz_image(xz_fpath, payload["options"].output_path)
            except Exception as exc:
                logger.fail_task(str(exc))
                return 1
            else:
                logger.succeed_task(
                    format_size(get_filesize(payload["options"].output_path))
                )

            logger.start_task(f"Removing {xz_fpath}…")
            try:
                xz_fpath.unlink(missing_ok=True)
            except Exception as exc:
                logger.fail_task(str(exc))
            else:
                logger.succeed_task()

        # download straight to final destination
        else:
            logger.start_task(
                f"Downloading {base_file} into {payload['options'].output_path}…"
            )
            try:
                download_file(base_file.geturl(), payload["options"].output_path)
            except Exception as exc:
                logger.fail_task(str(exc))
                return 1
            else:
                logger.succeed_task(
                    format_size(get_filesize(payload["options"].output_path))
                )
        return 0

    def run_local(self, base_file: File, payload: Dict[str, Any]) -> int:
        # we'll extract it to destination directly
        if base_file.getpath().suffix == ".xz":
            logger.start_task(
                f"Extracting {base_file.getpath()} "
                f"into {payload['options'].output_path}…"
            )
            try:
                extract_xz_image(base_file.getpath(), payload["options"].output_path)
            except Exception as exc:
                logger.fail_task(str(exc))
                return 1
            else:
                logger.succeed_task(
                    format_size(get_filesize(payload["options"].output_path))
                )
        # we'll simply copy it to destination
        else:
            logger.start_task(
                f"Copying {base_file.getpath()} "
                f"into {payload['options'].output_path}…"
            )
            try:
                shutil.copy2(base_file.getpath(), payload["options"].output_path)
            except Exception as exc:
                logger.fail_task(str(exc))
                return 1
            else:
                logger.succeed_task(
                    format_size(get_filesize(payload["options"].output_path))
                )
        return 0
