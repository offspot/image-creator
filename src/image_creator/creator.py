from __future__ import annotations

import atexit

from offspot_config.utils.misc import rmtree

from image_creator.constants import Global, Options, banner, logger
from image_creator.logger import Status
from image_creator.steps.machine import StepMachine


class ImageCreator:
    def __init__(
        self,
        *,
        build_dir: str,
        cache_dir: str,
        show_cache: bool,
        check_only: bool,
        keep_failed: bool,
        overwrite: bool,
        max_size: str,
        debug: bool,
        config_src: str,
        output: str,
    ):
        Global.options = Options(
            BUILD_DIR=build_dir,
            CACHE_DIR=cache_dir,
            show_cache=show_cache,
            check_only=check_only,
            keep_failed=keep_failed,
            overwrite=overwrite,
            max_size=max_size,
            debug=debug,
            CONFIG_SRC=config_src,
            OUTPUT=output,
        )

        # cpyright: ignore [reportGeneralTypeIssues]

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
