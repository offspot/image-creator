from __future__ import annotations

import pathlib
from typing import Any

from offspot_config.inputs import MainConfig
from offspot_config.utils.misc import format_size

try:
    from yaml import CDumper as Dumper
    from yaml import dump as yaml_dump
except ImportError:
    # we don't NEED cython ext but it's faster so use it if avail.
    from yaml import Dumper
    from yaml import dump as yaml_dump


from image_creator.constants import Global, logger
from image_creator.steps import Step
from image_creator.utils import requirements
from image_creator.utils.download import read_text_from


class CheckRequirements(Step):
    _name: str = "Checking system requirements…"

    def run(self, payload: dict[str, Any]) -> int:  # noqa: ARG002
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
    _name = "Checking config inputs…"

    def run(self, payload: dict[str, Any]) -> int:
        # checks split accross various methods to reduce complexity
        for method in (
            "check_parsing",
            # "check_params",
            "check_different_output",
            "check_target_path",
            "check_target_location",
            "check_target_nondestructive",
        ):
            res = getattr(self, method).__call__(payload)
            if res != 0:
                return res

        return 0

    def check_parsing(self, payload: dict[str, Any]):
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
            payload["config"] = MainConfig.read_from(text)
        except Exception as exc:
            logger.fail_task()
            logger.exception(exc)
            return 3
        else:
            logger.succeed_task()
        return 0

    def check_different_output(self, payload: dict[str, Any]) -> int:
        logger.start_task("Making sure base and output are different…")

        if (
            payload["config"].base_file.is_local
            and payload["config"].base_file.getpath() == payload["options"].output_path
        ):
            logger.fail_task("base and output image are the same")
            return 3
        else:
            logger.succeed_task()

        if payload["options"].check_only:
            return 0

        return 0

    def check_target_path(self, payload: dict[str, Any]) -> int:
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

    def check_target_location(self, payload: dict[str, Any]) -> int:
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

    def check_target_nondestructive(self, payload: dict[str, Any]) -> int:
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
    _name = "Checking all Sources…"

    def run(self, payload: dict[str, Any]) -> int:
        all_valid = True

        for file in [payload["config"].base_file, *payload["config"].all_files]:
            if file.is_plain:
                continue

            logger.start_task(f"Checking {file.geturl()}…")

            if payload["cache"].in_cache(file, check_outdacy=True):
                logger.succeed_task(
                    f"{format_size(payload['cache'][file].size)} (cached)"
                )
                continue

            # TODO: account for user-defined size
            # TODO: fail on missing size
            size = file.fetch_size()
            if size >= 0:
                logger.succeed_task(format_size(size))
            elif size == -1:
                logger.succeed_task("size unknown")
            else:
                logger.fail_task()
                all_valid &= False
            payload["cache"].add_candidate(file)

        for image in payload["config"].all_images:
            logger.start_task(f"Checking OCI Image {image}…")

            if payload["cache"].in_cache(image, check_outdacy=True):
                logger.succeed_task(
                    f"{format_size(payload['cache'][image].size)} (cached)"
                )
                continue

            if not image.oci.exists(Global.platform):
                logger.fail_task()
                all_valid &= False
            else:
                logger.succeed_task()
            payload["cache"].add_candidate(image)

        if payload["cache"].candidates:
            logger.start_task("Computing cache updates…")
            nb_to_evict = len(payload["cache"])
            payload["cache"].apply_candidates()
            nb_to_evict -= len(payload["cache"])

            msgs = []
            if nb_to_evict:
                msgs.append(f"{nb_to_evict} removed")

            if payload["cache"].candidates:
                nb_candidates = len(payload["cache"].candidates)
                size_candidates = sum(
                    [entry.size for entry in payload["cache"].candidates.values()]
                )
                msgs.append(f"{nb_candidates} to add ({format_size(size_candidates)})")
            logger.end_task(message=". ".join(msgs))

        return 0 if all_valid else 4


class WritingOffspotConfig(Step):
    _name = "Writing Offspot Config…"

    def run(self, payload: dict[str, Any]) -> int:
        if not payload["config"].offspot:
            logger.add_task("No Offspot config passed")
            return 0

        offspot_fpath = payload["image"].p1_mounted_on.joinpath("offspot.yaml")
        logger.start_task(f"Saving Offspot config to {offspot_fpath}…")
        try:
            offspot_fpath.write_text(
                yaml_dump(payload["config"].offspot, Dumper=Dumper)
            )
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task()

        return 0
