import pathlib
from typing import Any, Dict

try:
    from yaml import CDumper as Dumper
    from yaml import dump as yaml_dump
except ImportError:
    # we don't NEED cython ext but it's faster so use it if avail.
    from yaml import Dumper, dump as yaml_dump

from image_creator.constants import logger
from image_creator.inputs import Config
from image_creator.steps import Step
from image_creator.utils import requirements
from image_creator.utils.download import read_text_from
from image_creator.utils.misc import format_size
from image_creator.utils.oci_images import image_exists


class CheckRequirements(Step):
    name = "Checking system requirements…"

    def run(self, payload: Dict[str, Any]) -> int:
        """whether system requirements are satisfied"""

        all_good = True

        logger.start_task("Checking uid…")
        if not requirements.is_root():
            logger.fail_task("you must be root")
            all_good &= False
        else:
            logger.succeed_task()

        logger.start_task("Checking binary dependencies")
        has_all, missing_bins = requirements.has_all_binaries()
        if not has_all:
            all_good &= False
            logger.fail_task(f"Missing binaries: {', '.join(missing_bins)}")
        else:
            logger.succeed_task()

        logger.start_task("Checking loop-device capability")
        if not requirements.has_loop_device():
            all_good &= False
            logger.fail_task()
        else:
            logger.succeed_task()

        logger.start_task("Checking ext4 support")
        if not requirements.has_ext4_support():
            all_good &= False
            logger.fail_task()
        else:
            logger.succeed_task()

        if not all_good:
            logger.warning(requirements.help_text)

        return 0 if all_good else 2


class CheckInputs(Step):
    name = "Checking config inputs…"

    def run(self, payload: Dict[str, Any]) -> int:

        # checks split accross various methods to reduce complexity
        for method in (
            "check_parsing",
            "check_params",
            "check_different_output",
            "check_target_path",
            "check_target_location",
            "check_target_nondestructive",
        ):
            res = getattr(self, method).__call__(payload)
            if res != 0:
                return res

        return 0

    def check_parsing(self, payload: Dict[str, Any]):
        logger.start_task(f"Reading config from {payload['options'].config_src}")
        try:
            if isinstance(payload["options"].config_src, pathlib.Path):
                text = payload["options"].config_src.read_text()
            else:
                text = read_text_from(payload["options"].config_src)
        except Exception as exc:
            logger.fail_task(str(exc))
            raise exc
        else:
            logger.succeed_task()

        logger.start_task("Parsing config data…")
        try:
            payload["config"] = Config.read_from(text)
        except Exception as exc:
            logger.fail_task()
            logger.exception(exc)
            return 3
        else:
            logger.succeed_task()
        return 0

    def check_params(self, payload: Dict[str, Any]) -> int:
        logger.start_task("Checking parameters…")
        try:
            if not payload["config"].init():
                logger.fail_task("Config file is not valid")
                logger.warning(
                    "\n".join(
                        [
                            f"- [{key}] {error}"
                            for key, error in payload["config"].errors
                        ]
                    )
                )
                return 3
            else:
                logger.succeed_task()
        except Exception as exc:
            logger.fail_task(f"Config contains invalid values: {exc}")
            return 3
        return 0

    def check_different_output(self, payload: Dict[str, Any]) -> int:
        logger.start_task("Making sure base and output are different…")
        if (
            payload["config"].base.is_local
            and payload["config"].base.getpath() == payload["options"].output_path
        ):
            logger.fail_task("base and output image are the same")
            return 3
        else:
            logger.succeed_task()

        if payload["options"].check_only:
            return 0

        return 0

    def check_target_path(self, payload: Dict[str, Any]) -> int:
        # skip if --check-only
        if payload["options"].check_only:
            return 0

        if payload["options"].output_path.exists() and payload["options"].overwrite:
            logger.start_task("Removing target path…")
            try:
                payload["options"].output_path.unlink()
            except Exception as exc:
                logger.fail_task(str(exc))
            else:
                logger.succeed_task()
        else:
            logger.start_task("Checking target path…")
            if payload["options"].output_path.exists():
                logger.fail_task(f"{payload['options'].output_path} exists.")
                return 3
            else:
                logger.succeed_task()
        return 0

    def check_target_location(self, payload: Dict[str, Any]) -> int:
        # skip if --check-only
        if payload["options"].check_only:
            return 0
        logger.start_task("Testing target location…")
        try:
            payload["options"].output_path.touch()
            payload["options"].output_path.unlink()
        except Exception as exc:
            logger.fail_task(str(exc))
            return 3
        else:
            logger.succeed_task(str(payload["options"].output_path))
        return 0

    def check_target_nondestructive(self, payload: Dict[str, Any]) -> int:
        """--check-only friendly test for output

        does not remove output if already present"""

        # already tested
        if not payload["options"].check_only:
            return 0

        logger.start_task("Testing target location…")
        if not payload["options"].output_path.exists():
            try:
                payload["options"].output_path.touch()
                payload["options"].output_path.unlink()
            except Exception as exc:
                logger.fail_task(str(exc))
                return 3
            else:
                logger.succeed_task(str(payload["options"].output_path))
        else:
            try:
                payload["options"].output_path.touch()
            except Exception as exc:
                logger.fail_task(str(exc))
                return 3
            else:
                logger.succeed_task(str(payload["options"].output_path))
        return 0


class CheckURLs(Step):
    name = "Checking all Sources…"

    def run(self, payload: Dict[str, Any]) -> int:
        all_valid = True

        for file in [payload["config"].base] + payload["config"].all_files:
            if file.is_plain:
                continue
            logger.start_task(f"Checking {file.geturl()}…")
            size = file.fetch_size()
            if size >= 0:
                logger.succeed_task(format_size(size))
            elif size == -1:
                logger.succeed_task("size unknown")
            else:
                logger.fail_task()
                all_valid &= False

        for image in payload["config"].oci_images:
            logger.start_task(f"Checking OCI Image {image}…")
            if not image_exists(image):
                logger.fail_task()
                all_valid &= False
            else:
                logger.succeed_task()

        return 0 if all_valid else 4


class WritingOffspotConfig(Step):
    name = "Writing Offspot Config…"

    def run(self, payload: Dict[str, Any]) -> int:
        if not payload["config"].offspot_config:
            logger.add_task("No Offspot config passed")
            return 0

        offspot_fpath = payload["image"].p1_mounted_on.joinpath("offspot.yaml")
        logger.start_task(f"Saving Offspot config to {offspot_fpath}…")
        try:
            offspot_fpath.write_text(
                yaml_dump(payload["config"].offspot_config, Dumper=Dumper)
            )
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task()

        return 0
