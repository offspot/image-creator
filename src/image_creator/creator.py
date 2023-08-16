from __future__ import annotations

import atexit
from typing import Any

from offspot_config.utils.misc import rmtree

from image_creator.constants import Global, Options, banner, logger
from image_creator.logger import Status
from image_creator.steps.machine import StepMachine


class ImageCreator:
    def __init__(self, **kwargs: dict[str, Any]):
        Global.options = Options(**kwargs)
        Global._ready = True
        # make sure we clean things up before exiting
        atexit.register(self.halt)

    def run(self):
        if not Global.options.cache_dir:
            StepMachine.remove_step("ApplyCachePolicy")
        if not Global.options.show_cache:
            StepMachine.remove_step("PrintingCache")
        if Global.options.check_only:
            StepMachine.halt_after("ComputeSizes")

        logger.message(banner)

        self.machine = StepMachine(options=Global.options, succeeded=False)
        for step in self.machine:
            logger.start_step(step.name)
            res = step.run(self.machine.payload)
            logger.end_step()
            if res != 0:
                logger.error(f"Step “{step!r}” returned {res}")
                return res

    def halt(self):
        logger.message("Cleaning-up…", end=" ", timed=True)
        self.machine.halt()
        if Global.options.build_dir:
            try:
                rmtree(Global.options.build_dir)
            except Exception:
                logger.add_dot(Status.NOK)
            else:
                logger.add_dot(Status.OK)
        logger.message()
