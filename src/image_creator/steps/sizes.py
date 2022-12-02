from typing import Any, Dict

from image_creator.constants import logger
from image_creator.steps import Step
from image_creator.utils.misc import format_size, parse_size


class ComputeSizes(Step):
    name = "Compute sizes…"

    def run(self, payload: Dict[str, Any]) -> int:

        payload["output_size"] = parse_size(payload["config"].dig("output.size"))
        logger.add_task("Image size:", f"{format_size(payload['output_size'])}")

        # TODO: we should do more
        # - compute size of all content
        # - display cumulative size of content
        # - display suggested minimum SD size
        # - use that size as image size if set to `auto`
        # - fail if `auto` but some files dont report size
        # - check disk space on output_path.parent
        # - failed it it wont allow the base + resize
        # - calc how much space will be needed in the build-dir (non-direct DL)
        # - fail if build-dir wont allow it
        return 0
