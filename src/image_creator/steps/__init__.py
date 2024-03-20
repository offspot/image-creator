from __future__ import annotations

from typing import Any

from image_creator.constants import logger


class Step:
    """StepInterface"""

    # name of step to be overriden
    @property
    def name(self) -> str:
        return getattr(self, "_name", repr(self))
        return repr(self)

    def __repr__(self):
        return self.__class__.__name__

    def __str__(self):
        return self.name

    def run(self, payload: dict[str, Any]) -> int:
        """actual step implementation. 0 on success"""
        raise NotImplementedError()

    def cleanup(self, payload: dict[str, Any]):
        """clean resources reserved in run()"""
        ...


class VirtualInitStep(Step): ...


class GivingFeedback(Step):
    _name: str = "Giving creation feedback"

    def run(self, payload: dict[str, Any]) -> int:
        payload["succeeded"] = True
        logger.start_task("Image created successfuly")
        logger.succeed_task(str(payload["options"].output_path))
        return 0
