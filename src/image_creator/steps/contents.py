import pathlib
import shutil
import tempfile
from collections import OrderedDict as od
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import progressbar

from image_creator.constants import logger
from image_creator.inputs import File
from image_creator.steps import Step
from image_creator.utils.download import download_file
from image_creator.utils.misc import (
    ensure_dir,
    expand_file,
    format_size,
    get_filesize,
    get_size_of,
)


@dataclass
class MultiDownloadProgress:
    """Holds progress for multi-downloads"""

    nb_total: int = 0
    nb_completed: int = 0
    bytes_total: int = 0
    bytes_received: int = 0

    def on_data(self, bytes_received: int):
        self.bytes_received += bytes_received

    @property
    def nb_remaining(self) -> int:
        return self.nb_total - self.nb_completed

    @property
    def bytes_remaining(self) -> int:
        return self.bytes_total - self.bytes_received

    def __str__(self) -> str:
        if not self.nb_total:
            return repr(self)

        if not self.nb_remaining:
            return f"{self.nb_completed} items completed"

        return (
            f"{self.nb_remaining} items accounting "
            f"{format_size(self.bytes_remaining)} remaining"
        )


class MultiDownloadProgressBar:
    """Custom progress bar tailored for MultiDownloadProgress

    Displays as:

    [Elapsed Time: 0:02:45] 1 MiB of 20 MiB downloaded|#####   |1 KiB/s (Time:  0:00:45)
    """

    def __init__(self, dl_progress: MultiDownloadProgress):
        widgets = [
            "[",
            progressbar.Timer(),
            "] ",
            progressbar.DataSize(),
            f" of {format_size(dl_progress.bytes_total)} downloaded",
            progressbar.Bar(),
            progressbar.AdaptiveTransferSpeed(),
            " (",
            progressbar.ETA(),
            ")",
        ]
        self.bar = progressbar.ProgressBar(
            max_value=dl_progress.bytes_total, widgets=widgets
        )
        self.dl_progress = dl_progress

    def update(self):
        self.bar.update(self.dl_progress.bytes_received)

    def finish(self):
        self.bar.finish()


def download_file_worker(
    file: File,
    mount_point: pathlib.Path,
    temp_dir: pathlib.Path,
    on_data: Optional[Callable] = None,
):
    """Downloads a File into its destination, unpacking if required"""
    block_size = 2**20  # 1MiB

    dest_path = file.mounted_to(mount_point)
    ensure_dir(dest_path.parent)

    if file.is_direct:
        download_file(
            file.geturl(),
            dest_path,
            block_size=block_size,
            on_data=on_data,
        )
    else:
        temp_path = pathlib.Path(
            tempfile.NamedTemporaryFile(dir=temp_dir, suffix=dest_path.name).name
        )
        try:
            download_file(
                file.geturl(),
                temp_path,
                block_size=block_size,
                on_data=on_data,
            )
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            raise exc
        try:
            expand_file(temp_path, dest_path, file.via)
        except Exception as exc:
            raise exc
        finally:
            temp_path.unlink(missing_ok=True)


class FilesMultiDownloader:
    """Downloads the File objects supplied using a ThreadPoolExecutor"""

    def __init__(
        self,
        files,
        mount_point: pathlib.Path,
        temp_dir: Optional[pathlib.Path] = None,
        concurrency: Optional[int] = None,
        callback: Optional[Callable] = None,
        on_data: Optional[Callable] = None,
    ):
        self.files = files
        self.mount_point = mount_point
        self.temp_dir = temp_dir

        self.callback = callback
        self.on_data = on_data
        self.is_running = False
        self.futures, self.cancelled, self.succeeded, self.failed = (
            od(),
            od(),
            od(),
            od(),
        )
        self.executor = ThreadPoolExecutor(max_workers=concurrency or None)

    def notify_completion(self, future: Future):
        """called once Future id done. Moves it to approp. list then callback()"""
        if not future.done():
            return
        if future.cancelled():
            self.cancelled[future] = self.futures.pop(future)
            index = self.cancelled[future]
        elif future.exception(0.1) is not None:
            self.failed[future] = self.futures.pop(future)
            index = self.failed[future]
        else:
            self.succeeded[future] = self.futures.pop(future)
            index = self.succeeded[future]
        if self.callback:
            try:
                self.callback(
                    file=self.files[index],
                    result=future.result(0.1),
                    exc=future.exception(0.1),
                )
            except (CancelledError, TimeoutError):
                ...

        # break out downloads as we dont allow failures
        if not self.futures:
            self.is_running = False

    def start(self):
        """submit all files'workers to the executor"""
        self.is_running = True
        self.futures = {
            self.executor.submit(
                download_file_worker,
                file,
                self.mount_point,
                self.temp_dir,
                self.on_data,
            ): index
            for index, file in enumerate(self.files)
        }
        for future in list(self.futures.keys()):
            future.add_done_callback(self.notify_completion)

    def shutdown(self, now=True):
        if now or self.cancelled or self.failed:
            self.executor.shutdown(wait=False, cancel_futures=True)
        else:
            self.executor.shutdown(wait=True)
        self.is_running = False


class ProcessingLocalContent(Step):
    name = "Processing local contents"

    def run(self, payload: Dict[str, Any]) -> int:
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

    def process_file(self, file: File, mount_point: str):
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

        src_path = pathlib.Path(file.url)

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

        logger.start_task(f"Expanding {self.via} file to {file.to}…")
        try:
            expand_file(src_path, dest_path, file.via)
        except Exception as exc:
            logger.fail_task(str(exc))
            return 1
        else:
            logger.succeed_task(format_size(get_size_of(dest_path)))

        return 0


class DownloadingContent(Step):
    name = "Downloading content"

    def run(self, payload: Dict[str, Any]) -> int:
        mount_point = payload["image"].p3_mounted_on

        nb_remotes = len(payload["config"].remote_files)

        if not nb_remotes:
            logger.add_task("No content to download")
            return 0

        dl_progress = MultiDownloadProgress(
            nb_total=nb_remotes,
            nb_completed=0,
            bytes_total=sum(
                [
                    file.size if file.size else 0
                    for file in payload["config"].remote_files
                ]
            ),
            bytes_received=0,
        )
        dl_pb = MultiDownloadProgressBar(dl_progress)

        # dont use multi-downloader for single file or thread==1
        if nb_remotes == 1 or payload["options"].concurrency == 1:

            def on_data(bytes_received: int):
                """update both data holder and progress bar on data receival"""
                dl_progress.on_data(bytes_received)
                dl_pb.update()

            for file in payload["config"].remote_files:
                logger.add_task(f"Downloading {file.geturl()} into {file.to}…")
                dest_path = file.mounted_to(mount_point)
                try:
                    download_file_worker(
                        file,
                        mount_point,
                        payload["options"].build_dir,
                        on_data=on_data,
                    )
                except Exception as exc:
                    logger.complete_download(
                        dest_path.name, failed=True, extra=str(exc)
                    )
                    return 1
                else:
                    dl_progress.nb_completed += 1
                    logger.complete_download(
                        dest_path.name,
                        format_size(get_size_of(dest_path)),
                        extra=f"({str(dl_progress)})",
                    )
            return 0

        # multi-download with UI refresh on MainThread
        def on_completion(file, result: Any, exc: Exception = None):
            dl_progress.nb_completed += 1
            dest_path = file.mounted_to(mount_point)
            if exc is not None:
                logger.debug(f"Failed to download {file.url} into {dest_path}: {exc}")
                raise exc

            logger.complete_download(
                dest_path.name,
                format_size(get_size_of(dest_path)),
                extra=f"({str(dl_progress)})",
            )

        downloader = FilesMultiDownloader(
            files=payload["config"].remote_files,
            mount_point=mount_point,
            temp_dir=payload["options"].build_dir.joinpath("dl_remotes"),
            concurrency=payload["options"].concurrency,
            callback=on_completion,
            on_data=dl_progress.on_data,
        )

        logger.add_task(
            f"Downloading {nb_remotes} files "
            f"totaling {format_size(dl_progress.bytes_total)}…",
            f"using {min([nb_remotes, downloader.executor._max_workers])} workers",
        )

        try:
            downloader.start()
            while downloader.is_running:
                dl_pb.update()
        except KeyboardInterrupt:
            downloader.shutdown(now=True)
            # TODO: we should actually interrupt downloads here
            raise
        else:
            downloader.shutdown()
        finally:
            dl_pb.update()
            dl_pb.finish()
        return 0
