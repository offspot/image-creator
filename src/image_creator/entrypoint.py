from __future__ import annotations

import argparse
import sys
import tempfile

from image_creator import __version__
from image_creator.constants import Global, logger


def main():
    parser = argparse.ArgumentParser(
        prog="image-creator",
        description="Create an Offspot Image from a config file",
        epilog="See https://github.com/offspot/image-creator "
        "for config and cache-policy format",
    )

    parser.add_argument(
        "--build-dir",
        dest="BUILD_DIR",
        help="Directory to store temporary files in, "
        "like files that needs to be extracted. "
        f"Defaults to some place within {tempfile.gettempdir()}",
    )
    parser.add_argument(
        "--cache-dir",
        dest="CACHE_DIR",
        help="Directory to use as a download cache. "
        "Should a remote file be present in the cache, "
        "it is fetched from there instead of being downloaded. "
        "Files matching the cache policy are stored to the cache once downloaded. "
        "Cache Policy can be configured in CACHE_DIR/policy.yaml",
    )
    parser.add_argument(
        "--show-cache",
        action="store_true",
        dest="show_cache",
        help="Print a summary of the Cache's content. "
        "Use with --check to query a Cache's status.",
    )
    parser.add_argument(
        "-C",
        "--check",
        action="store_true",
        dest="check_only",
        help="Only check inputs, URLs and sizes. Don't download/create image.",
    )

    parser.add_argument(
        "-K",
        "--keep",
        action="store_true",
        dest="keep_failed",
        default=False,
        help="[DEBUG] Don't remove output image if creation failed",
    )
    parser.add_argument(
        "-X",
        "--overwrite",
        action="store_true",
        default=False,
        dest="overwrite",
        help="Don't fail on existing output image: remove instead",
    )
    parser.add_argument(
        "--max-size",
        dest="max_size",
        help="Maximum image size allowed. Ex: 512GB",
    )
    parser.add_argument("-D", "--debug", action="store_true", dest="debug")
    parser.add_argument("-V", "--version", action="version", version=__version__)

    parser.add_argument(help="Offspot Config YAML file path or URL", dest="CONFIG_SRC")
    parser.add_argument(
        dest="OUTPUT",
        help="Where to write image to",
    )

    kwargs = dict(parser.parse_args()._get_kwargs())
    Global._debug = kwargs.get("debug", False)

    # purposedly import after setting debug
    from image_creator.creator import ImageCreator

    try:
        app = ImageCreator(**kwargs)
        sys.exit(app.run())
    except Exception as exc:
        if kwargs.get("debug"):
            logger.exception(exc)
        logger.critical(str(exc))
        sys.exit(1)
    finally:
        try:
            app.halt()  # pyright: ignore [reportUnboundVariable]
        except Exception as exc:
            logger.debug(f"Errors cleaning-up: {exc}")
        logger.terminate()


if __name__ == "__main__":
    sys.exit(main())
