import pathlib
from typing import Any, Dict

from image_creator.constants import logger
from image_creator.steps import Step
from image_creator.utils.misc import format_size, get_freespace


def get_margin_for(content_size: int) -> int:
    """margin in bytes for such a content_size"""

    return int(0.1 * content_size)  # static 10% for now


def get_target_path(output_path: pathlib.Path) -> pathlib.Path:
    """usable output path given the requested one might not exist yet"""

    # at this stage, the target image file probably doesnt exists (but can)
    # and its parent folder might not exist as well
    if not output_path.exists():
        for parent in output_path.parents:
            if parent.exists():
                return parent
    return output_path


class ComputeSizes(Step):
    name = "Compute sizes…"

    def run(self, payload: Dict[str, Any]) -> int:

        tar_images_size = sum(
            [image.filesize for image in payload["config"].all_images]
        )
        expanded_images_size = sum(
            [image.fullsize for image in payload["config"].all_images]
        )
        expanded_files_size = sum(
            [file.fullsize for file in payload["config"].all_files]
        )

        raw_content_size = sum(
            [tar_images_size, expanded_images_size, expanded_files_size]
        )
        margin = get_margin_for(raw_content_size)
        min_image_size = sum(
            [payload["config"].base.rootfs_size, raw_content_size, margin]
        )

        logger.add_task("Computed Minimum Image Size", format_size(min_image_size))

        # user might have requested a specific output size ; must comply
        if payload["config"].output.size and isinstance(
            payload["config"].output.size, int
        ):
            image_size = payload["config"].output.size
            logger.start_task("Computed size fits within requested size")
            if payload["config"].output.size < min_image_size:
                logger.fail_task(
                    f"{format_size(min_image_size)} > "
                    f"{format_size(payload['config'].output.size)}"
                )
                return 1
            logger.succeed_task(
                f"{format_size(min_image_size)} <= "
                f"{format_size(payload['config'].output.size)}"
            )
        else:
            image_size = min_image_size

        # user might have requested a maximum image size to produce; must comply
        if payload["options"].max_size:
            logger.start_task("Computed size fits within max_size")
            if payload["options"].max_size < image_size:
                logger.fail_task(format_size(payload["options"].max_size))
                return 1
            logger.succeed_task()

        payload["output_size"] = image_size

        return self.check_physical_space(payload, image_size)

    def get_needs(self, payload: Dict[str, Any], image_size: int) -> Dict[str, int]:
        needs = {}
        # target volume needs:
        # - uncompressed image file is written to it
        # - it is later expanded
        needs["target"] = max([payload["config"].base_file.fullsize, image_size])

        # build-dir needs:
        # files that need to be uncompressed are downloaded to first (incl. base)
        remote_compressed_files = [
            file for file in payload["config"].remote_files if file.via != "direct"
        ]
        needs["build_dir"] = sum([file.filesize for file in remote_compressed_files])

        # cache needs:
        # - what will be introduced to cache
        if payload["options"].cache_dir:
            needs["cache_dir"] = sum(
                [entry.size for entry in payload["cache"].candidates.values()]
            )
            # exclude from build-dir the size of those we'll have in cache
            needs["build_dir"] -= sum(
                [
                    file.size
                    for file in remote_compressed_files
                    if file in payload["cache"] or payload["cache"].has_candidate(file)
                ]
            )
        return needs

    def check_physical_space(self, payload: Dict[str, Any], image_size: int) -> int:
        logger.start_task("Checking free-space availability…")
        target_path = get_target_path(payload["options"].output_path)
        needs = self.get_needs(payload, image_size)

        # mapping of volumes/mount point with their cumulative needs
        volumes_map = {}

        def update_map(volume, needs: int, path: pathlib.Path):
            if volume not in volumes_map:
                volumes_map[volume] = {"needs": needs, "paths": [path]}
                return
            volumes_map[volume]["needs"] + needs
            volumes_map[volume]["paths"].append(path)

        update_map(target_path.stat().st_dev, needs["target"], target_path)
        update_map(
            payload["options"].build_dir.stat().st_dev,
            needs["build_dir"],
            payload["options"].build_dir,
        )
        if payload["options"].cache_dir:
            update_map(
                payload["options"].cache_dir.stat().st_dev,
                needs["cache_dir"],
                payload["options"].cache_dir,
            )

        total_needs = total_free_space = 0
        for data in volumes_map.values():
            free_space = get_freespace(data["paths"][0])
            if data["needs"] > free_space:
                missing = data["needs"] - free_space
                paths = ", ".join([str(p) for p in data["paths"]])
                logger.fail_task(
                    f"missing {format_size(missing)} on disk {paths}. "
                    f"{format_size(data['needs'])} required. "
                    f"{format_size(free_space)} free."
                )
                return 1
            total_free_space += free_space
            total_needs += data["needs"]

        logger.succeed_task(
            f"{format_size(total_needs)} required, "
            f"{format_size(total_free_space)} free."
        )
        return 0
