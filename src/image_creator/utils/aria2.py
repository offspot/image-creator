import datetime
import os
import random
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, Self

import aria2p

from image_creator import __version__
from image_creator.constants import logger

ONE_MiB = 2**20


class DownloadError(OSError): ...


class DownloadErrorInfo(NamedTuple):
    code: str
    message: str

    def __str__(self) -> str:
        return f"Error #{self.code}: {self.message}"


class Progress(NamedTuple):
    downloaded: int
    total: int
    speed: int

    @property
    def ratio(self) -> float:
        try:
            return self.downloaded / self.total
        except ZeroDivisionError:
            return 0

    @property
    def percent(self) -> float:
        return float(f"{(self.ratio * 100): .2f}")


class Feedback(NamedTuple):
    name: str
    gid: str
    status: str
    error: DownloadErrorInfo | None
    progress: Progress
    overall_speed: int


class GeneralFeedback(NamedTuple):
    count: Progress
    weight: Progress
    downloads: list[Feedback]


class Aria2Process:

    @staticmethod
    def is_port_used(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("localhost", port)) == 0

    @classmethod
    def find_port(cls) -> int:
        remaining_attempts = 1000
        while remaining_attempts:
            remaining_attempts -= 1
            while port := random.randint(a=6800, b=32000):  # noqa: S311
                if not cls.is_port_used(port):
                    return port
        raise OSError("Unable to find an unused port for aria2c RPC")

    def __init__(self, aria2c_bin: Path | None = None, port: int = 0, secret: str = ""):
        if aria2c_bin is None and shutil.which("aria2c") is None:
            raise OSError("Missing aria2c binary in PATH")
        self.aria2c_bin = (aria2c_bin or Path(str(shutil.which("aria2c")))).resolve()
        if not self.aria2c_bin.exists():
            raise OSError(f"Missing aria2c binary at {self.aria2c_bin}")
        self.started = False
        self.host = "http://127.0.0.1"
        self.port = port or self.find_port()
        self.secret = secret or uuid.uuid4().hex

    def __str__(self):
        return (
            f"{self.__class__.__name__}(running={self.is_running}, "
            f"returncode={self.returncode}, pid={self.pid})"
        )

    @property
    def is_running(self) -> bool:
        return self.started and self.ps.returncode is None

    @property
    def returncode(self) -> int:
        return self.ps.returncode if self.ps else -1

    @property
    def pid(self) -> int:
        return self.ps.pid if self.ps else -1

    # def run(self):
    def start(self):
        self.ps = subprocess.Popen(
            args=[
                str(self.aria2c_bin),
                "--enable-rpc",
                "--rpc-listen-all",
                "--rpc-listen-port",
                str(self.port),
                "--rpc-secret",
                self.secret,
                "--stop-with-process",
                str(os.getpid()),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(2)  # allow aria2c to start rpc before assuming it's there
        self.started = True

    def stop(self):
        if self.is_running:
            self.ps.send_signal(signal.SIGINT)
            self.ps.send_signal(signal.SIGTERM)
            self.started = False
            self.ps.terminate()
            # make sure process is dead and release before releasing ourselves
            time.sleep(1)


class Download(aria2p.Download):
    """aria2p Download subclass with additional accessors"""

    def __init__(
        self,
        *args,
        folder: tempfile.TemporaryDirectory,
        final_path: Path,
        started_on: datetime.datetime,
        callback: Callable | None,
        **kargs,
    ):
        super().__init__(*args, **kargs)
        self.folder = folder
        self.final_path = final_path
        self.started_on = started_on
        self.completed_on: datetime.datetime | None = None
        self.callback = callback

    @classmethod
    def cast(
        cls,
        obj,
        *,
        update: bool = False,
        folder: tempfile.TemporaryDirectory | None,
        final_path: Path | None,
        started_on: datetime.datetime | None,
        callback: Callable | None,
    ) -> Self:
        base_cls_name = obj.__class__.__name__
        obj.__class__ = type(
            base_cls_name,
            (Download,),
            {
                "folder": folder,
                "final_path": final_path,
                "started_on": started_on,
                "completed_on": None,
                "callback": callback,
                "post_processed": False,
            },
        )

        if update:
            obj.update()
        return obj

    @classmethod
    def create_from(
        cls,
        instance: aria2p.Download,
        folder: tempfile.TemporaryDirectory[str],
        final_path: Path,
        callback: Callable | None = None,
    ) -> Self:
        dl = cls.cast(
            instance,
            folder=folder,
            final_path=final_path,
            started_on=datetime.datetime.now(tz=datetime.UTC),
            callback=callback,
        )
        return dl

    def get_followers(self, *, updated: bool) -> list[Self]:
        """list of followers, optionnaly updated (casted followed_by)"""
        return [
            type(self).cast(
                follower,
                update=updated,
                folder=None,
                final_path=None,
                started_on=None,
                callback=None,
            )
            for follower in self.followed_by
        ]

    @property
    def updated(self) -> Self:
        """Similar to .live, but keeping cast"""
        self.update()
        return self

    @property
    def followers(self) -> list[Self]:
        """Similar to followed_by, but casted"""
        return self.get_followers(updated=False)

    @property
    def updated_followers(self) -> list[Self]:
        """Followers with updated data"""
        return self.get_followers(updated=True)

    @property
    def all_downloads(self) -> list[Self]:
        """Self and all followers"""
        return [self, *self.followers]

    def update_all(self):
        """update self and followers"""
        _ = [dl.update() for dl in self.all_downloads]

    @property
    def overall_length(self) -> int:
        return sum(dl.total_length for dl in self.all_downloads)

    @property
    def overall_duration(self) -> datetime.timedelta:
        if not self.completed_on:
            return datetime.timedelta(seconds=0)
        return self.completed_on - self.started_on

    @property
    def overall_speed(self) -> int:
        if not self.completed_on:
            return 0
        return int(self.total_length / self.overall_duration.total_seconds())

    def get_progress(self) -> Progress:
        """Unified progress data for self and followers"""
        completed_length = 0
        total_length = 0
        download_speed = 0
        self.update_all()
        for dl in self.all_downloads:
            completed_length += dl.completed_length
            total_length += dl.total_length
            download_speed += dl.download_speed

        return Progress(completed_length, total_length, download_speed)

    @property
    def error(self) -> DownloadErrorInfo | None:
        if self.status == "error":
            return DownloadErrorInfo(
                code=self.error_code or "-1",
                message=self.error_message or "Unknown Error",
            )

    def get_error(self) -> DownloadErrorInfo | None:
        for dl in self.all_downloads:
            if dl.status == "error":
                return dl.error

    @property
    def feedback(self) -> Feedback:
        return Feedback(
            name=self.name,
            gid=self.gid,
            status=self.status,
            error=self.error,
            progress=self.get_progress(),
            overall_speed=self.overall_speed,
        )

    @property
    def uris(self) -> list[str]:
        return [
            uri["uri"]
            for file in self.files
            for uri in file.uris
            if uri["status"] == "used"
        ]

    @property
    def uri(self) -> str:
        return self.uris[0]

    @property
    def is_metalink(self) -> bool:
        """whether this is a metalink download (followed)

        ie. a download that has follwers, not following and not a BT"""
        return bool(
            not self.following_id and self.followed_by_ids and not self.bittorrent
        )

    @property
    def is_dottorrent(self) -> bool:
        return not self.is_torrent and any(
            uri.endswith(".torrent") for uri in self.uris
        )

    @property
    def followers_active(self):
        return any(
            dl.status in ("active", "waiting", "paused") for dl in self.followed_by
        )

    @property
    def active(self):
        return self.status in ("active", "waiting", "paused")

    @property
    def actual_files(self) -> list[aria2p.File]:
        def is_real_file(file: aria2p.File, from_dl: Download) -> bool:
            if file.is_metadata:
                return False
            if from_dl.is_dottorrent:
                return False
            if (
                file.path.is_dir()
                or file.path.resolve() == self.dir
                or not file.path.is_relative_to(self.dir)
            ):
                return False

            # WARN: this is our Kiwix convention. Could be anything.
            # maybe retrieve Content Type initially
            if self.is_metalink and file.path.suffix in (".meta4", ".metalink"):
                return False
            if self.is_torrent and file.path.suffix == ".torrent":
                return False
            return True

        return [
            file
            for dl in self.all_downloads
            for file in dl.files
            if is_real_file(file=file, from_dl=dl)
        ]

    @property
    def is_processing(self) -> bool:
        """Download is active and should be awaited

        Will raise on error"""

        if self.post_processed:
            return False

        # make sure we have fresh data
        self.update_all()

        dls = self.all_downloads

        # a download can be the source of multiple downloads
        # as is the case for a metalink download or bittorrent
        # if any of our followers [ONLY] is active, we have to wait
        # if self.active or self.followers_active:
        if self.followers_active:
            return True

        # keep the loop on error as exception will be triggered in
        # event handler, on the Downloader
        if any(dl.status == "error" for dl in dls):
            return True

        # now everything should be OK ; awaiting post-processing to happen on event
        return not self.post_processed

    def post_process(self):
        logger.debug(f"post_process({self.gid})")

        # assume all followers completed
        if self.post_processed:
            logger.debug("> already processed")
            return

        # we only post-process the metadata dl (once all followers have completed)
        if self.is_torrent and not self.is_metadata:
            logger.debug("> this is torrent metadata")
            return

        # we only post-process the metadata dl (once all followers have completed)
        if self.following_id:
            return

        logger.debug(">> PROCESSING!")

        # now handle files (should all be unique)
        files = self.actual_files

        # expected use case: single file download (direct or ML or BT)
        if len(files) == 1:
            files[0].path.rename(self.final_path)

        # download is multiple file (BT)
        elif len(files) > 1:
            self.final_path.mkdir(parents=True, exist_ok=True)
            for file in files:
                # /!\ assuming all followers share same dir
                # (because those are auto created)
                relative_path = file.path.relative_to(self.dir)
                target = self.final_path / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                file.path.rename(target)

        self.cleanup()

        # done processing now
        self.post_processed = True

        if self.callback:
            self.callback.__call__(dl=self, succeeded=True)

    def block(self):
        while self.is_processing:
            time.sleep(0.5)
        return self

    def cleanup(self):
        if getattr(self, "folder", None):
            self.folder.cleanup()


class Downloader:

    default_options = (
        # Rename file name if the same file already exists.
        # we don't want to. we'll overwrite
        ("allow_overwrite", True),
        ("auto_file_renaming", False),
        ("remove_control_file", True),
        ("auto-save-interval", 0),
        # check integrity of BT and ML or HTTP/FTP if --checksum passed
        ("check_integrity", True),
        # disk cache to store details about downloads
        ("disk_cache", 32 * ONE_MiB),
        # /!\ falloc not avail on all filesystem but is on ext4
        ("file_allocation", "falloc"),
        ("min_split_size", "20M"),
        ("split", 5),
        # not for BT, not for ML if pieces hashes
        ("piece_length", "1M"),
        ("optimize_concurrent_downloads", "false"),
        #
        ("rpc_save_upload_metadata", True),
        ("user_agent", f"offspot image-creator/{__version__}"),
        ("force_sequential", False),
        ("max_download_result", 1000),
        ("keep_unfinished_download_result", False),
        # disable config file (so we dont conflict)
        ("no_conf", True),
        #
        # HTTP/FTP
        #
        ("enable_http_keep_alive", True),
        #
        #
        # Metadata
        ("bt_max_open_files", 1000),
        #
        # MetaLink
        #
        ("follow_metalink", "true"),
        ("metalink_enable_unique_protocol", True),
        ("metalink_preferred_protocol", "https"),
        #
        # bitTorrent
        #
        # whether to continue seeding after check is OK
        ("bt_hash_check_seed", False),
        # stop BitTorrent download if download speed is 0 in consecutive SEC seconds.
        ("bt_stop_timeout", 600),  # 10mn
        ("dht_message_timeout", 10),
        ("follow_torrent", "true"),
        # BT/MG and ML are metadata only
        ("pause_metadata", False),
        ("realtime_chunk_checksum", True),
        ("bt_detach_seed_only", True),
        ("bt_metadata_only", False),
        # ("bt_remove_unselected_file", False),
        #
        # Failsafe
        #
        ("connect_timeout", 60),
        ("max_concurrent_downloads", 5),
        ("max_connection_per_server", 1),
        ("max_file_not_found", 2),
        ("max_tries", 5),
        ("max_resume_failure_tries", 0),
        ("retry_wait", 300),  # for 503 responses
        ("timeout", 60),
    )

    def __init__(
        self,
        *,
        manage_aria2c: bool = False,
        aria2c_bin_path: Path | None = None,
        aria2c_rpc_host: str | None = None,
        aria2c_rpc_port: int | None = None,
        aria2c_rpc_secret: str | None = None,
        halt_on_error: bool = True,
    ):
        if manage_aria2c:
            self.aria2c = Aria2Process(
                aria2c_bin=aria2c_bin_path,
                port=int(os.getenv("IC_ARIA2_PORT", "0")),
                secret=os.getenv("IC_ARIA2_SECRET", ""),
            )
            self.aria2c.start()
            self._client = aria2p.Client(
                host=self.aria2c.host, port=self.aria2c.port, secret=self.aria2c.secret
            )
        else:
            self.aria2c = None
            kwargs = {}
            if aria2c_rpc_host:
                kwargs["host"] = aria2c_rpc_host
            if aria2c_rpc_port:
                kwargs["port"] = aria2c_rpc_port
            if aria2c_rpc_secret:
                kwargs["secret"] = aria2c_rpc_secret
            self._client = aria2p.Client(**kwargs)

        self.halt_on_error = halt_on_error
        self.is_listening = False

        # tracking this instance's downloads as aria2c process can be shared
        self.downloads: list[Download] = []

        self.api = aria2p.API(client=self._client)

        self.set_options(use_defaults=True)

        self.start_listening()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.halt()

    def halt(self):
        for dl in self.downloads:
            if not dl.is_complete:
                dl.remove(force=True)  # we may be safer and also remove followers
        self.stop_listening()
        if self.aria2c:
            logger.debug(f"stopping {self.aria2c}")
            self.aria2c.stop()

    def is_ours(self, gid: str) -> bool:
        for dl in self.downloads:
            if dl.gid == gid:
                return True
            if gid in dl.followed_by_ids:
                return True
        return False

    def get_dl(self, gid: str) -> Download:
        for dl in self.downloads:
            if dl.gid == gid:
                return dl.updated
            if gid in dl.followed_by_ids:
                for follower in dl.followers:
                    if follower.gid == gid:
                        return follower.updated
        raise KeyError(f"Not managing Download with GID={gid}")

    def on_download_start(self, api: aria2p.API, gid: str):  # noqa: ARG002
        if not self.is_ours(gid):
            return
        dl = self.get_dl(gid)
        dl.started_on = datetime.datetime.now(tz=datetime.UTC)
        logger.debug(f"download started GID#{gid}")

    def on_download_pause(self, api: aria2p.API, gid: str):  # noqa: ARG002
        if not self.is_ours(gid):
            return
        logger.debug("Download #{gid} has been paused")

    def on_download_stop(self, api: aria2p.API, gid: str):  # noqa: ARG002
        if not self.is_ours(gid):
            return
        logger.debug("Download #{gid} has been stopped")
        # should we raise?
        dl = self.get_dl(gid)
        if dl.callback:
            dl.callback.__call__(dl=dl, succeeded=False)
            return

    def on_download_complete(self, api: aria2p.API, gid: str):  # noqa: ARG002
        logger.debug(f"on_download_complete({gid})")
        if not self.is_ours(gid):
            return
        dl = self.get_dl(gid)
        dl.completed_on = datetime.datetime.now(tz=datetime.UTC)

        logger.debug(
            f"{dl.is_torrent=}, {dl.is_metadata=}, {dl.is_metalink=}, "
            f"{dl.is_dottorrent=}, {dl.following_id=}, {dl.actual_files=} "
            f"{dl.followers=} "
            f"{[(f.is_metadata, f.path, f.uris) for f in dl.files]}"
        )

        # don't process torrent initiator just now. last followers will
        if (dl.is_torrent and dl.is_metadata) or dl.is_dottorrent:
            logger.debug("> is torrent meta")
            return

        # metalink is not post-processed on completion (its just metadata)
        # last completing follower will trigger instead
        if dl.is_metalink:
            logger.debug("> is metalink")
            return

        # are we a regular download following a metalink?
        parent = self.get_dl(dl.following_id) if dl.following_id else None
        if parent and parent.is_metalink:
            if all(dl.is_complete for dl in parent.updated_followers):
                parent.post_process()
                return

        dl.post_process()

    def on_bt_download_complete(self, api: aria2p.API, gid: str):  # noqa: ARG002
        logger.debug(f"on_bt_download_complete({gid})")
        if not self.is_ours(gid):
            return
        dl = self.get_dl(gid)
        dl.completed_on = datetime.datetime.now(tz=datetime.UTC)
        dl.post_process()

        # request post-processing of main torrent
        # post-processing will be refused if there are followers awaiting
        if dl.following_id:
            # retrieve from self.downloads as we attached final destination to it
            self.get_dl(dl.following_id).post_process()

    def on_download_error(self, api: aria2p.API, gid: str):  # noqa: ARG002
        if not self.is_ours(gid):
            return
        dl = self.get_dl(gid)
        logger.debug(f"on_download_error({gid})")
        logger.error(str(dl.error))
        dl.cleanup()
        self.stop_listening()
        if self.halt_on_error:
            self.halt()
        if dl.callback:
            dl.callback.__call__(dl=dl, succeeded=False)
            return
        raise DownloadError(
            getattr(dl.error, "code", None), getattr(dl.error, "message", None)
        )

    def start_listening(self):
        if self.is_listening is not False:
            return

        self.is_listening = None
        self.api.listen_to_notifications(
            threaded=True,
            on_download_start=self.on_download_start,
            on_download_pause=self.on_download_pause,
            on_download_stop=self.on_download_stop,
            on_download_complete=self.on_download_complete,
            on_download_error=self.on_download_error,
            on_bt_download_complete=self.on_bt_download_complete,
            timeout=1,
        )
        self.is_listening = True

    def stop_listening(self):
        if self.is_listening is not True:
            return
        try:
            self.api.stop_listening()
        except RuntimeError:
            ...
        self.is_listening = False

    def set_options(
        self,
        *,
        use_defaults: bool,
        **indiv_options: str | int | float | bool | list,
    ):
        options = aria2p.Options(self.api, self.api.get_global_options()._struct)
        if use_defaults:
            for optname, optvalue in self.default_options:
                setattr(options, optname, optvalue)

        for optname, optvalue in indiv_options.items():
            setattr(options, optname, optvalue)

        self.api.set_global_options(options)

    def download_to(
        self,
        uri: str,
        to: Path,
        *,
        checksum: str = "",
        insecure: bool = False,
        callback: Callable | None = None,
        **indiv_options: str | int | float | bool | list,
    ) -> aria2p.Download:
        """downloads uri"""

        options = aria2p.Options(self.api, {})

        # download to the parent folder of requested target
        to.resolve().parent.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(
            prefix="dl-", dir=to.resolve().parent, ignore_cleanup_errors=True
        )
        options.dir = str(folder.name)

        if checksum:
            options.checksum = checksum  # fmt: digest=value

        if insecure:
            options.check_certificate = False

        for optname, optvalue in indiv_options.items():
            setattr(options, optname, optvalue)

        dl = Download.create_from(
            self.api.add(uri=uri, options=options)[-1],
            folder=folder,
            final_path=to,
            callback=callback,
        )
        self.downloads.append(dl)
        return dl

    @property
    def is_processing(self) -> bool:
        return any(dl.is_processing for dl in self.downloads)

    def get_feedback(self, only_for: list[str] | None = None):
        """unified progress feedback for all downloads (or gid-list)"""

        dls = self.downloads
        if only_for:
            feedbacks = [dl for dl in dls if dl.gid in only_for]

        downloaded_nb = downloaded_bytes = total_nb = total_bytes = speed = 0
        feedbacks = []
        for dl in self.downloads:
            if only_for and dl.gid not in only_for:
                continue
            total_nb += 1
            feedback = dl.feedback
            if dl.is_complete:
                downloaded_nb += 1
            downloaded_bytes += feedback.progress.downloaded
            total_bytes += feedback.progress.total
            speed += feedback.progress.speed
            feedbacks.append(feedback)

        return GeneralFeedback(
            count=Progress(downloaded=downloaded_nb, total=total_nb, speed=0),
            weight=Progress(
                downloaded=downloaded_bytes, total=total_bytes, speed=speed
            ),
            downloads=feedbacks,
        )
