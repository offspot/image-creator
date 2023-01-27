import pathlib
from typing import Any, Dict

from image_creator.cache.manager import CacheManager
from image_creator.cache.policy import MainPolicy
from image_creator.constants import logger
from image_creator.steps import Step
from image_creator.utils.misc import supports_xattr


class CheckCache(Step):
    name = "Checking Cache Policy…"

    def run(self, payload: Dict[str, Any]) -> int:
        if not payload["options"].cache_dir:
            logger.add_task("Not using cache")
            payload["cache"] = CacheManager(pathlib.Path(), MainPolicy.disabled())
            return 0

        # check cache folder
        payload["options"].cache_dir.mkdir(parents=True, exist_ok=True)

        logger.start_task(
            f"Checking user_xattr support at {payload['options'].cache_dir}"
        )
        if not supports_xattr(payload["options"].cache_dir):
            logger.fail_task("cache must be on ext4 with `user_xattr`")
            return 1
        else:
            logger.succeed_task()

        # load MainPolicy
        policy_fpath = payload["options"].cache_dir / "policy.yaml"
        logger.start_task(f"Reading cache policy at {policy_fpath}…")

        if not policy_fpath.exists():
            policy = MainPolicy.defaults()
            logger.fail_task("Not present ; using defaults")
        else:
            try:
                policy = MainPolicy.read_from(policy_fpath.read_text())
            except Exception as exc:
                logger.fail_task(f"Failed to parse cache policy: {exc}")
                return 1
            logger.succeed_task()

        logger.start_task(f"Preparing cache at {policy_fpath.parent}")
        try:
            payload["cache"] = CacheManager(policy_fpath.parent, policy)
            payload["cache"].walk()
        except Exception as exc:
            logger.fail_task(f"Failed to initialize cache: {exc}")
            raise exc
            return 1

        logger.succeed_task()
        return 0


class PrintingCache(Step):
    name = "Printing Cache Content…"

    def run(self, payload: Dict[str, Any]) -> int:
        payload["cache"].print(with_evictions=True)
        return 0


class ApplyCachePolicy(Step):
    name = "Enforcing Cache Policy…"

    def run(self, payload: Dict[str, Any]) -> int:

        entries = payload["cache"].apply() + payload["cache"].evict_outdated()
        for entry, reason, succeeded in entries:
            logger.start_task(f"Evicting {entry.source}")
            if succeeded:
                logger.succeed_task(f"({reason})")
            else:
                logger.fail_task(f"failed to evict {reason}")
        if not entries:
            logger.add_task("No entry to evict")

        return 0
