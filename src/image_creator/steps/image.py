from __future__ import annotations

from typing import Any

from offspot_config.utils.misc import format_size

from image_creator.constants import logger
from image_creator.steps import Step
from image_creator.utils.image import Image


class ResizingImage(Step):
    _name = "Resizing image"

    def run(self, payload: dict[str, Any]) -> int:
        logger.start_task("Checking image size…")
        payload["image"] = Image(payload["options"].output_path)
        try:
            size = payload["image"].get_size()
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task(format_size(size))

        logger.start_task(f"Resizing image to {format_size(payload['output_size'])}…")
        try:
            payload["image"].resize(payload["output_size"])
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task(format_size(payload["image"].get_size()))

        logger.start_task("Getting a loop device…")
        try:
            loop_dev = payload["image"].assign_loopdev()
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task(loop_dev)

        logger.start_task(f"Attaching image to {loop_dev}…")
        try:
            payload["image"].attach()
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task()

        logger.start_task(f"Resizing third partition of {loop_dev}…")
        try:
            payload["image"].resize_last_part()
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task()

        return 0

    def cleanup(self, payload):
        if payload.get("image"):
            payload["image"].detach()


class MountingDataPart(Step):
    _name = "Mounting data partition"

    def run(self, payload: dict[str, Any]) -> int:
        logger.start_task(f"Mouting {payload['image'].loop_dev}p3…")
        try:
            mounted_on = payload["image"].mount_p3()
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task(mounted_on)
        return 0

    def cleanup(self, payload):
        if payload.get("image"):
            payload["image"].unmount_p3()


class UnmountingDataPart(Step):
    _name = "Unmounting data partition"

    def run(self, payload: dict[str, Any]) -> int:
        logger.start_task(f"Unmouting {payload['image'].p3_mounted_on}…")
        try:
            payload["image"].unmount_p3()
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task()

        try:
            logger.start_task(f"Checking {payload['image'].p3_mounted_on} fs…")
            payload["image"].fsck_last_part()
        except Exception as exc:
            # OK to fail (will be caught on first boot)
            logger.fail_task(str(exc))
        else:
            logger.succeed_task()
        return 0


class UnmountingBootPart(Step):
    _name = "Unmounting boot partition"

    def run(self, payload: dict[str, Any]) -> int:
        logger.start_task(f"Unmouting {payload['image'].p1_mounted_on}…")
        try:
            payload["image"].unmount_p1()
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task()
        return 0


class MountingBootPart(Step):
    _name = "Mounting boot partition"

    def run(self, payload: dict[str, Any]) -> int:
        logger.start_task(f"Mouting {payload['image'].loop_dev}p1…")
        try:
            mounted_on = payload["image"].mount_p1()
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task(mounted_on)
        return 0

    def cleanup(self, payload):
        if payload.get("image"):
            payload["image"].unmount_p1()


class DetachingImage(Step):
    _name = "Detaching Image"

    def run(self, payload: dict[str, Any]) -> int:
        logger.start_task(f"Detach image from {payload['image'].loop_dev}")
        if not payload["image"].detach():
            logger.fail_task(f"{payload['image']} not detached!")
            return 1
        else:
            logger.succeed_task()

        return 0
