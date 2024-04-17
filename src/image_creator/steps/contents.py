from __future__ import annotations

import functools
import pathlib
import shutil
import tempfile
import time
from collections.abc import Callable
from typing import Any

import progressbar  # type: ignore
from offspot_config.file import File
from offspot_config.utils.misc import (
    copy_file,
    ensure_dir,
    expand_file,
    format_duration,
    format_size,
    get_filesize,
    get_size_of,
)

from image_creator.cache.manager import CacheManager
from image_creator.constants import logger
from image_creator.steps import Step
from image_creator.utils.aria2 import Download, Downloader, DownloadError, Feedback


class ProcessingLocalContent(Step):
    _name = "Processing local contents"

    def run(self, payload: dict[str, Any]) -> int:
        mount_point = payload["image"].p3_mounted_on

        if not payload["config"].non_remote_files:
            logger.add_task("No local content to process")
            return 0

        # only non-remote Files (plain and local)
        for file in payload["config"].non_remote_files:
            res = self.process_file(file, mount_point)
            if res != 0:
                return res
        return 0

    def process_file(self, file: File, mount_point: pathlib.Path):
        dest_path = file.mounted_to(mount_point)
        try:
            ensure_dir(dest_path.parent)
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1

        if file.is_plain:
            logger.start_task(f"Writing plain text to {file.to}…")
            try:
                size = dest_path.write_text(file.content)
            except Exception as exc:
                logger.fail_task(str(exc))
                return 1
            else:
                logger.succeed_task(format_size(size))
            return 0

        src_path = pathlib.Path(file.geturl())

        if file.is_direct:
            logger.start_task(f"Copying file to {file.to}…")
            try:
                shutil.copy2(src_path, dest_path)
            except Exception as exc:
                logger.fail_task(str(exc))
                return 1
            else:
                logger.succeed_task(format_size(get_filesize(dest_path)))
            return 0

        logger.start_task(f"Expanding {file.via} file to {file.to}…")
        try:
            expand_file(src=src_path, dest=dest_path, method=file.via)
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task(format_size(get_size_of(dest_path)))

        return 0


class InitDownloader(Step):
    _name = "Initializing Downloader"

    def run(self, payload: dict[str, Any]) -> int:
        bin_path = (
            payload["options"].root_dir.parent.joinpath("aria2c")
            if payload["options"].is_onebinary
            else None
        )
        payload["downloader"] = Downloader(
            manage_aria2c=True,
            # only specify aria2c path on built nuitka binary
            aria2c_bin_path=bin_path,
        )
        logger.add_task("Downloader started")
        return 0

    def cleanup(self, payload: dict[str, Any]):
        payload["downloader"].halt()
        del payload["downloader"]


class Aria2DownloadProgressBar:
    """Custom progress bar tailored for aria2 Downloader

    Displays as:

    [Elapsed Time: 0:02:45] 1 MiB of 20 MiB downloaded|#####   |1 KiB/s (Time:  0:00:45)
    """

    def __init__(self, downloader: Downloader, total_bytes: int):
        self.aria_downloader = downloader
        self.bar = progressbar.ProgressBar()
        self.rebuild_for(total_bytes=total_bytes)

    def rebuild_for(self, total_bytes: int):
        self.total_bytes = total_bytes
        # if self.bar:
        #     self.bar.finish()

        widgets = [
            "[",
            progressbar.Timer(),
            "] ",
            progressbar.DataSize(),
            f" of {format_size(total_bytes)} downloaded",
            progressbar.Bar(),
            progressbar.AdaptiveTransferSpeed(),
            " (",
            progressbar.ETA(),
            ")",
        ]
        self.bar = progressbar.ProgressBar(max_value=total_bytes, widgets=widgets)

    def update(self):
        feedback = self.aria_downloader.get_feedback()
        if feedback.weight.total != self.total_bytes:
            self.rebuild_for(total_bytes=feedback.weight.total)
        self.bar.update(
            # make sure we don't update bar above 100% (will not work)
            min([feedback.weight.downloaded, feedback.weight.total])
        )

    def finish(self):
        self.bar.finish()


class FilesProcessor:
    """Downloads the File objects supplied using a ThreadPoolExecutor"""

    def __init__(
        self,
        files,
        cache: CacheManager,
        mount_point: pathlib.Path,
        temp_dir: pathlib.Path,
        aria_downloader: Downloader,
        callback: Callable,
    ):
        self.files = files
        self.cache = cache
        self.mount_point = mount_point
        self.temp_dir = temp_dir

        self.files = files
        self.remaining = len(files)
        self.aria_downloader = aria_downloader
        self.callback = callback

    def process_file(
        self,
        file: File,
    ):
        dest_path = file.mounted_to(self.mount_point)
        ensure_dir(dest_path.parent)
        ensure_dir(self.temp_dir)

        if file.is_direct:
            if file in self.cache:
                copy_file(self.cache[file].fpath, dest_path)
                self.cache[file] += 1
                self.direct_callback(
                    file=file,
                    dest_path=dest_path,
                    dl=None,
                    succeeded=True,
                )
            else:
                callback = functools.partial(
                    self.direct_callback, file=file, dest_path=dest_path
                )
                self.aria_downloader.add(
                    uri=file.geturl(),
                    to=dest_path,
                    checksum=file.checksum.as_aria if file.checksum else "",
                    callback=callback,
                )
        else:
            temp_path = pathlib.Path(
                tempfile.NamedTemporaryFile(
                    dir=self.temp_dir, suffix=f"_{dest_path.name}"
                ).name
            )
            if file in self.cache:
                copy_file(self.cache[file].fpath, temp_path)
                self.cache[file] += 1
                self.nondirect_callback(
                    file=file,
                    temp_path=temp_path,
                    dest_path=dest_path,
                    dl=None,
                    succeeded=True,
                )
            else:
                callback = functools.partial(
                    self.nondirect_callback,
                    file=file,
                    temp_path=temp_path,
                    dest_path=dest_path,
                )
                self.aria_downloader.add(
                    uri=file.geturl(), to=temp_path, callback=callback
                )

    def direct_callback(
        self,
        *,
        file: File,
        dest_path: pathlib.Path,
        dl: Download | None,
        succeeded: bool,
    ):
        if succeeded:
            if self.cache.should_cache(file):
                self.cache.introduce(file, dest_path)

        try:
            self.callback.__call__(
                file=file,
                succeeded=succeeded,
                feedback=None if dl is None else dl.feedback,
            )
        finally:
            self.remaining -= 1

    def nondirect_callback(
        self,
        *,
        file: File,
        temp_path: pathlib.Path,
        dest_path: pathlib.Path,
        dl: Download | None,
        succeeded: bool,
    ):
        if succeeded:
            if self.cache.should_cache(file):
                self.cache.introduce(file, temp_path)

            try:
                expand_file(src=temp_path, method=file.via, dest=dest_path)
            except Exception as exc:
                raise exc
            finally:
                temp_path.unlink(missing_ok=True)

        try:
            self.callback.__call__(
                file=file,
                succeeded=succeeded,
                feedback=None if dl is None else dl.feedback,
            )
        finally:
            self.remaining -= 1

    @property
    def is_running(self) -> bool:
        return bool(self.remaining)

    def start(self):
        for file in self.files:
            self.process_file(file)

    def shutdown(self):
        self.aria_downloader.halt()


class DownloadingContent(Step):
    _name = "Downloading content"

    def run(self, payload: dict[str, Any]) -> int:
        mount_point = payload["image"].p3_mounted_on

        nb_remotes = len(payload["config"].remote_files)

        if not nb_remotes:
            logger.add_task("No content to download")
            return 0

        # multi-download with UI refresh on MainThread
        def on_completion(*, file: File, succeeded: bool, feedback: Feedback | None):
            dest_path = file.mounted_to(mount_point)
            logger.message()
            if not succeeded:
                logger.complete_download(
                    dest_path.name,
                    extra=str(feedback.error) if feedback else "n/a",
                    failed=True,
                )
                logger.debug(
                    f"Failed to download {file.url} into {dest_path}: "
                    f"{feedback.error if feedback else '?'}"
                )
                if feedback and feedback.error:
                    raise DownloadError(*feedback.error)
                raise DownloadError("Unknown Error")

            cache_suffix = " (cached)" if file in payload["cache"] else ""
            logger.complete_download(
                dest_path.name,
                size=format_size(get_size_of(dest_path)) + cache_suffix,
                extra=(
                    f"at {format_size(feedback.overall_speed)}/s"
                    if feedback
                    else "(copied)"
                ),
            )

        bytes_total = sum(
            [file.size if file.size else 0 for file in payload["config"].remote_files]
        )

        manager = FilesProcessor(
            files=payload["config"].remote_files,
            cache=payload["cache"],
            mount_point=mount_point,
            temp_dir=payload["options"].build_dir.joinpath("dl_remotes"),
            callback=on_completion,
            aria_downloader=payload["downloader"],
        )

        dl_pb = Aria2DownloadProgressBar(
            downloader=manager.aria_downloader, total_bytes=bytes_total
        )

        logger.add_task(
            f"Retrieving {nb_remotes} files totaling {format_size(bytes_total)}…",
        )

        try:
            manager.start()
            while manager.is_running:
                dl_pb.update()
                time.sleep(0.5)
        except KeyboardInterrupt:
            manager.shutdown()  # we should actually interrupt downloads here
            raise
        else:
            manager.shutdown()
        finally:
            dl_pb.update()
            dl_pb.finish()
        fb = payload["downloader"].get_feedback()
        logger.add_task(
            f"Downloaded "
            f"{fb.count.downloaded}/{fb.count.total} files ({fb.weight.percent}% – "  # noqa: RUF001
            f"{format_size(fb.weight.downloaded)} / {format_size(fb.weight.total)}) "
            f"in {format_duration(fb.duration)} "
            f"at {format_size(fb.speed)}/s"
        )
        return 0
