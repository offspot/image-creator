from typing import Any, Dict

from image_creator.constants import logger
from image_creator.steps import Step
from image_creator.utils.misc import format_size, get_filesize, rmtree
from image_creator.utils.oci_images import download_image


class DownloadingOCIImages(Step):
    name = "Downloading OCI Images"

    def run(self, payload: Dict[str, Any]) -> int:
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

        for image in payload["config"].oci_images:
            target = images_dir.joinpath(f"{image.fs_name}.tar")
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
                logger.complete_download(target.name, format_size(get_filesize(target)))
        return 0
