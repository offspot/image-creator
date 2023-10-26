from __future__ import annotations

from typing import ClassVar

from image_creator.constants import logger
from image_creator.logger import Status
from image_creator.steps import GivingFeedback, VirtualInitStep
from image_creator.steps.base import DownloadImage
from image_creator.steps.cache import ApplyCachePolicy, CheckCache, PrintingCache
from image_creator.steps.check_inputs import (
    CheckInputs,
    CheckRequirements,
    CheckURLs,
    WritingOffspotConfig,
)
from image_creator.steps.contents import DownloadingContent, ProcessingLocalContent
from image_creator.steps.image import (
    DetachingImage,
    MountingBootPart,
    MountingDataPart,
    ResizingImage,
    ShrinkingImage,
    UnmountingBootPart,
    UnmountingDataPart,
)
from image_creator.steps.oci_images import DownloadingOCIImages
from image_creator.steps.sizes import ComputeSizes


class StepMachine:
    """Ordered list of Steps"""

    steps: ClassVar[list] = [
        VirtualInitStep,
        CheckRequirements,
        CheckInputs,
        CheckCache,
        PrintingCache,
        ApplyCachePolicy,
        CheckURLs,
        ComputeSizes,
        # check-only stops here
        DownloadImage,
        ResizingImage,
        MountingDataPart,
        DownloadingOCIImages,
        ProcessingLocalContent,
        DownloadingContent,
        UnmountingDataPart,
        MountingBootPart,
        WritingOffspotConfig,
        UnmountingBootPart,
        DetachingImage,
        ShrinkingImage,
        GivingFeedback,
    ]

    def __init__(self, **kwargs):
        self.payload = dict(**kwargs)
        self._current = 0
        self.step = self._get_step(self._current)

    @classmethod
    def halt_after(cls, step: str):
        """reduce StepMachine to end with that step"""
        index = [stepcls.__name__ for stepcls in cls.steps].index(step)
        cls.steps = cls.steps[: index + 1]

    @classmethod
    def remove_step(cls, step: str):
        """reduce StepMachine to end with that step"""
        stepcls = next(stepcls for stepcls in cls.steps if stepcls.__name__ == step)
        cls.steps.remove(stepcls)

    def _get_step(self, index: int):
        return self.steps[index].__call__()

    @property
    def step_num(self):
        """current step number (1-indexed)"""
        return self._current + 1

    def __iter__(self):
        return self

    def __next__(self):
        try:
            new_index = self._current + 1
            self.steps[new_index]
        except IndexError as exc:
            raise StopIteration() from exc

        try:
            self.step = self._get_step(new_index)
        except Exception as exc:
            logger.error(f"failed to init step {self.steps[new_index]}: {exc}")
            logger.exception(exc)
            raise StopIteration() from exc
        self._current = new_index
        return self.step

    def halt(self):
        """request cleanup of ran-steps

        Steps being dependent on the previous ones, some resources such as
        loop-device or mount-points are passed along several steps
        this calls individual cleanup() methods in reversed order"""

        # calling .cleanup() on each step from last called one, in reverse order
        for index in range(self._current, 0, -1):
            step = self._get_step(index)
            try:
                step.cleanup(payload=self.payload)
            except Exception:
                logger.add_dot(status=Status.NOK)
            else:
                logger.add_dot(status=Status.OK)

        # delete created image file on failure (unless requested otherwise)
        if (
            not self.payload["succeeded"]
            and not self.payload["options"].keep_failed
            and self.payload["options"].output_path
            and self.payload["options"].output_path.exists()
        ):
            try:
                self.payload["options"].output_path.unlink(missing_ok=True)
            except Exception:
                logger.add_dot(status=Status.NOK)
            else:
                logger.add_dot(status=Status.OK)
