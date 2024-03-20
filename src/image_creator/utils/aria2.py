import datetime
import os
import random
import re
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
from requests.exceptions import ConnectionError, RequestException

from image_creator import __version__
from image_creator.constants import logger

# used in times ariap2 doesnt return error message (based on aria2c 1.37.0)
ARIA_EXIT_CODES = {
    0: "all downloads were successful.",
    1: "an unknown error occurred.",
    2: "time out occurred.",
    3: "a resource was not found.",
    4: 'aria2 saw the specified number of "resource not found" error. '
    "See --max-file-not-found option.",
    5: (
        "a download aborted because download speed was too slow. "
        "See --lowest-speed-limit option."
    ),
    6: "network problem occurred.",
    7: "there were unfinished downloads. This error is only reported if all finished "
    "downloads were successful and there were unfinished downloads in a queue when "
    "aria2 exited by pressing Ctrl-C by an user or sending TERM or INT signal.",
    8: "remote server did not support resume when resume was required "
    "to complete download.",
    9: "there was not enough disk space available.",
    10: "piece length was different from one in .aria2 control file. "
    "See --allow-piece-length-change option.",
    11: "aria2 was downloading same file at that moment.",
    12: "aria2 was downloading same info hash torrent at that moment.",
    13: "file already existed. See --allow-overwrite option.",
    14: "renaming file failed. See --auto-file-renaming option.",
    15: "aria2 could not open existing file.",
    16: "aria2 could not create new file or truncate existing file.",
    17: "file I/O error occurred.",
    18: "aria2 could not create directory.",
    19: "name resolution failed.",
    20: "aria2 could not parse Metalink document.",
    21: "FTP command failed.",
    22: "HTTP response header was bad or unexpected.",
    23: "too many redirects occurred.",
    24: "HTTP authorization failed.",
    25: 'aria2 could not parse bencoded file (usually ".torrent" file).',
    26: '".torrent" file was corrupted or missing information that aria2 needed.',
    27: "Magnet URI was bad.",
    28: "bad/unrecognized option was given or unexpected option argument was given.",
    29: "the remote server was unable to handle the request due "
    "to a temporary overloading or maintenance.",
    30: "aria2 could not parse JSON-RPC request.",
    31: "Reserved. Not used.",
    32: "checksum validation failed.",
}


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
    """aria2c process manager. Start and stop it at will

    aria2c is started for RPC communication on all interfaces.
    Port defaults to 6800 and is configurable. Can use random port.

    Default empty secret. Is configurable. Can use random string"""

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

    def __init__(
        self, aria2c_bin: Path | None = None, port: int = 6800, secret: str = ""
    ):
        """Initialize (but don't start) an instance

        Parameters:
            aria2c_bin: path to aria2c binary. Otherwise looked for into PATH
            port: port to set for RPC communication. Use 0 for random port
            secret: secret to share between aria2c and RPC client. Random if None

        Raises:
            OSError: if no aria2c_bin provided and not found in PATH
            OSError: if aria2c_bin does not exists"""
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
    """aria2p Download subclass with additional accessors

    You would mostly be receiving them from Downloader.add()"""

    def __init__(
        self,
        *args,
        folder: tempfile.TemporaryDirectory,
        final_path: Path,
        started_on: datetime.datetime,
        callback: Callable | None,
        **kargs,
    ):
        """Initialize Download (should not be used directly. See `cast()` method)

        Parameters:
            folder: Temp folder in which aria2 downloads the files
            final_path: where to move downloaded file upon completion
            started_on: when to consider it started (for duration and speed comp)
            callback: to call upon completion or error
        """
        super().__init__(*args, **kargs)
        self.folder = folder
        self.final_path = final_path
        self.started_on = started_on
        self.completed_on: datetime.datetime | None = None
        self.callback = callback
        self.done = False  # updated by downloader to inform API is gone

    @property
    def is_processing(self) -> bool:
        """Whether this Download is “active” and should be awaited

        Use it to watch a download you added as this only will reliably tell
        you whether a download is done or not.
        An errored download returns False (not processing anymore).
        Check post-processing status or use `is_processing_or_raises`"""

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

    @property
    def is_processing_or_raises(self) -> bool:
        """is_processing alternative, raising on download error

        Raises:
            DownloadError: should the download errored"""
        succeeded = self.succeeded
        if succeeded is None:
            return True
        if succeeded is False:
            raise DownloadError(*self.error)
        return False

    @property
    def succeeded(self) -> bool | None:
        if self.is_processing:
            return
        return self.error is None

    def block(self):
        """Sleep until this Download is completed (or errored)

        Raises:
            DownloadError: if the download errors"""
        while self.is_processing:
            time.sleep(0.5)
        if self.error:
            raise DownloadError(*self.error)
        return self

    @property
    def error(self) -> DownloadErrorInfo | None:
        if self.status == "error":
            return DownloadErrorInfo(
                code=self.error_code or "-1", message=self.error_message
            )

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
    def feedback(self) -> Feedback:
        """Unified feedback for this Downloads and its followers"""
        return Feedback(
            name=self.name,
            gid=self.gid,
            status=self.status,
            error=self.error,
            progress=self.get_progress(),
            overall_speed=self.overall_speed,
        )

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
        """Extend an aria2p.Download (returned by API) into a Download

        Parameters:
            update: whether to update its data using API once casted
            folder: Temp folder in which aria2 downloads the files
            final_path: where to move downloaded file upon completion
            started_on: when to consider it started (for duration and speed comp)
            callback: to call upon completion or error
        """
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
                "done": False,
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
        """Create a Download from an aria2p.Download (returned by API)

        Parameters:
            instance: the API-retrieved object
            folder: Temp folder in which aria2 downloads the files
            final_path: where to move downloaded file upon completion
            callback: to call upon completion or error
        """
        return cls.cast(
            instance,
            folder=folder,
            final_path=final_path,
            started_on=datetime.datetime.now(tz=datetime.UTC),
            callback=callback,
        )

    def update(self):
        try:
            super().update()
        except (RequestException, ConnectionError) as exc:
            if not self.done:
                raise exc

    def get_followers(self, *, updated: bool) -> list[Self]:
        """list of following Downloads (casted followed_by)

        Parameters:
            updated: whether to update them upon retrieval"""
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

    @property
    def error_code(self) -> str | None:
        code = super().error_code
        if exit_code := re.match(r"\[Errno (?P<num>\d+)\]", str(code)):
            return exit_code.groupdict()["num"]
        return code

    @property
    def error_message(self) -> str:
        msg = super().error_message
        if msg:
            return msg
        unknown = "Unknown Error"
        error_code = str(self.error_code)
        if error_code.isdigit():
            return ARIA_EXIT_CODES.get(int(error_code), unknown)
        return unknown

    def get_error(self) -> DownloadErrorInfo | None:
        for dl in self.all_downloads:
            if dl.status == "error":
                return dl.error

    @property
    def uris(self) -> list[str]:
        """URIs used for this Download"""
        return [
            uri["uri"]
            for file in self.files
            for uri in file.uris
            if uri["status"] == "used"
        ]

    @property
    def uri(self) -> str:
        """First (main?) URI used to download"""
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
        """Whether a .torrent file"""
        return not self.is_torrent and any(
            uri.endswith(".torrent") for uri in self.uris
        )

    @property
    def followers_active(self):
        """Whether this has still active followers"""
        return any(
            dl.status in ("active", "waiting", "paused") for dl in self.followed_by
        )

    @property
    def active(self):
        """Whether this very download is active"""
        return self.status in ("active", "waiting", "paused")

    @property
    def actual_files(self) -> list[aria2p.File]:
        """Downloaded Files that are not metadata

        Uses this after completion as metadata downloads don't know all files until
        metatada has been retrieved and parsed"""

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

    def post_process(self):
        """run post-processing tasks

        - should be run on completed download!!
        - copies downloaded file(s) into final destination
        - runs the user-requested callback"""

        # assume all followers completed
        if self.post_processed:
            return

        if not self.error:

            # we only post-process the metadata dl (once all followers have completed)
            if self.is_torrent and not self.is_metadata:
                return

            # we only post-process the metadata dl (once all followers have completed)
            if self.following_id:
                return

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
            self.callback.__call__(dl=self, succeeded=self.error is None)

    def cleanup(self):
        """Remove temporary files and resources created to download.

        Use once done/errored"""
        if getattr(self, "folder", None):
            self.folder.cleanup()

    def mark_done(self):
        """set done flag on download and all its followers, indicating API is gone"""
        for dl in self.all_downloads:
            dl.done = True


class Downloader:
    """aria2c based Downloader

    Downloader starts (or connects to) an aria2 process via its RPC interface
    using aria2p lib (wrapper around the RPC API)

    Limitations:
        - We don't support downloading .meta4, .metalink nor .torrent files.
            We always assume to want the actual file(s) behind.
        - In order to predict aria2 behavior, we set global options to the aria2
            instance. Connecting to an existing aria2 process that might require care.
        - There is no API-comminication optimization. We assume localhost access to
            the RPC and make tons of calls to it. No calls batching ATM for simplicity.
        - We use falloc option for aria2 which mandates a filesystem that supports it.
            Change `file_allocation` option is you dont
        - We dont support stopping a Download. Pausing and resuming should work as
            expected but we dont know what to do on stop()

    Usage:

    ```py
    with Downloader(manage_aria2c=True) as downloader:
        dl = downloader.add("https://xxx", to=Path("/tmp/toto.zip"))
        try:
            dl.block()
        except DownloadError as exc:
            logger.exception(exc)

        dl2 = downloader.add("magnet://xxx", to=Path("/tmp/tools"))
        while dl2.is_processing:
            pg = dl2.get_progress()
            print(
                f"\r[{pg.percent}%] {human(pg.downloaded)} of {human(pg.total)} "
                f"at {human(pg.speed)}/s"
            )
            time.sleep(.5)
        print(f"{dl2.succeeded=}")

        dl3 = downloader.add("https://xxx.torrent", to=Path("/tmp/others"))
        dl4 = downloader.add("https://xxx.meta4", to=Path("/tmp/myfile.zim"))
        dl5 = downloader.add("https://xxx.zip", to=Path("/tmp/myfile.zip"))

        while downloader.is_processing:
            fb = downloader.get_feedback()
            print(
                f"\r[{fb.weight.percent}%] "
                f"[{fb.count.downloaded}/{fb.count.total}] "
                f"[{human(fb.weight.downloaded)} / {human(fb.weight.total)}] "
                f"at {human(fb.weight.speed)}/s)")
            time.sleep(.5)

        if not downloader.all_succeeded:
            logger.exception(downloader.errored.block())

        print(f"Overall speed: {human(downloader.overall_speed)}/s")
    ```
    """

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
        ("disk_cache", 32 * 2**20),  # 32MiB
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
        """Initializes the instance

        Parameters:
            manage_aria2c: Whether to start a dedicated aria2c process for this instance
                If not, connection details will be mandatory.
                Mandates `aria2c` binary in PATH or path passed in `aria2c_bin_path`
            aria2c_bin_path: path to aria2c binary (for `manage_aria2c=True`)
            aria2c_rpc_host: URL (`http://127.0.0.1` for inst.) to an aria2c RPC server
                Only for (manage_aria2=False)
            aria2c_rpc_port: if `manage_aria2c=False`: Port to connect to RPC server
                If `manage_aria2c=True`: Port to set for aria2c process
            aria2c_rpc_secret: if `manage_aria2c=False`: Secret to connect to RPC server
                If `manage_aria2c=True`: Secret to set for aria2c process
            halt_on_error: Whether to halt() downloader when a single download errors.
        """

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
        self.is_halted = False

        # tracking this instance's downloads as aria2c process can be shared
        self.downloads: list[Download] = []

        self.api = aria2p.API(client=self._client)

        self.set_options(use_defaults=True)

        self._start_listening()

    def add(
        self,
        uri: str,
        to: Path,
        *,
        checksum: str = "",
        insecure: bool = False,
        callback: Callable | None = None,
        **indiv_options: str | int | float | bool | list,
    ) -> aria2p.Download:
        """Place a download request

        Parameters:
            uri: Source URI to download from.
                Can be any aria2 supported URL: HTTP(s)/magnet/torrent/metalink
            to: Final destination of downloaded file
                In case of multiple files download (torrent/metalink)
            insecure: whether to drop TLS verifications (known certificate issue)
            checksum: optional checksum to verify downloaded file against (HTTP/S)
                Must be in type=value format. Ex: md5=0192ba11326f...
                Torrent dont need it as pieces sum are included in metadata and checked
                directly. Metalink usually include it as well. (whole or pieces)
            callback: Your callback upon success/error. Signature:
                `def mycallback(dl: Download, succeeded: bool)`
            indiv_options: Pass any aria2 option that will be applied solely to this
                download.

        Raises:
            OSError: if downloaded is already halted
            aria2p.client.ClientException: if an option is incorrect.
                Ex: checksum type not supported or value not for right checksum type
        """
        if self.is_halted:
            raise OSError("Downloader is halted")

        options = aria2p.Options(self.api, {})
        for optname, optvalue in indiv_options.items():
            setattr(options, optname, optvalue)

        # download to the parent folder of requested target
        to.resolve().parent.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(
            prefix="dl-", dir=to.resolve().parent, ignore_cleanup_errors=True
        )
        options.dir = str(folder.name)

        if checksum:
            options.checksum = checksum  # fmt: type=value

        if insecure:
            options.check_certificate = False

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
        if self.is_halted:
            return False
        return any(dl.is_processing for dl in self.downloads)

    @property
    def all_succeeded(self) -> bool:
        return all(dl.succeeded for dl in self.downloads)

    @property
    def errored(self) -> Download | None:
        """First-found errored download"""
        for dl in self.downloads:
            if dl.error:
                return dl
        return

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

    def set_options(
        self,
        *,
        use_defaults: bool,
        **indiv_options: str | int | float | bool | list,
    ):
        """set aria2 global options

        Parameters:
            use_defaults: Whether to extend our defaults or start from scratch
            indiv_options: Use aria2 option names to set directly (- becomes _)
        """
        options = aria2p.Options(self.api, self.api.get_global_options()._struct)
        if use_defaults:
            for optname, optvalue in self.default_options:
                setattr(options, optname, optvalue)

        for optname, optvalue in indiv_options.items():
            setattr(options, optname, optvalue)

        self.api.set_global_options(options)

    def halt(self):
        """Halth the downloader (and aria2c if managed)

        Once halted, no download can continue or be added, but downloads still
        holds their data"""
        if self.is_halted:
            return
        for dl in self.downloads:
            dl.mark_done()
            if not dl.is_complete:
                dl.remove(force=True)  # we may be safer and also remove followers
        self._stop_listening()
        if self.aria2c:
            logger.debug(f"stopping {self.aria2c}")
            self.aria2c.stop()
        self.is_halted = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.halt()

    def get(self, gid: str) -> Download:
        """Download from its ID

        Parameters:
            gid: Aria2 GID

        Raises:
            KeyError: should the GID not be for this aria2 instance"""
        for dl in self.downloads:
            if dl.gid == gid:
                return dl.updated
            if gid in dl.followed_by_ids:
                for follower in dl.followers:
                    if follower.gid == gid:
                        return follower.updated
        raise KeyError(f"Not managing Download with GID={gid}")

    def is_ours(self, gid: str) -> bool:
        """Whether gid is a known download of us

        This includes both first-party (added with `add()` and listed
        in `self.downloads` and their followers

        Parameter:
            gid: Aria2 GID to check"""
        for dl in self.downloads:
            if dl.gid == gid:
                return True
            if gid in dl.followed_by_ids:
                return True
        return False

    def _cb_on_download_start(self, api: aria2p.API, gid: str):  # noqa: ARG002
        """aria2p.API callback for a started download"""
        if not self.is_ours(gid):
            return
        dl = self.get(gid)
        dl.started_on = datetime.datetime.now(tz=datetime.UTC)

    def _cb_on_download_pause(self, api: aria2p.API, gid: str):  # noqa: ARG002
        """aria2p.API callback for a paused download"""
        if not self.is_ours(gid):
            return

    def _cb_on_download_stop(self, api: aria2p.API, gid: str):  # noqa: ARG002
        """aria2p.API callback for a stopped download"""
        if not self.is_ours(gid):
            return
        logger.debug("Download #{gid} has been stopped")
        dl = self.get(gid)

        if dl.callback:
            dl.callback.__call__(dl=dl, succeeded=False)
            return

    def _cb_on_download_complete(self, api: aria2p.API, gid: str):  # noqa: ARG002
        """aria2p.API callback for a completed download"""
        logger.debug(f"on_download_complete({gid})")
        if not self.is_ours(gid):
            return
        dl = self.get(gid)
        dl.completed_on = datetime.datetime.now(tz=datetime.UTC)

        # don't process torrent initiator just now. last followers will
        if (dl.is_torrent and dl.is_metadata) or dl.is_dottorrent:
            return

        # metalink is not post-processed on completion (its just metadata)
        # last completing follower will trigger instead
        if dl.is_metalink:
            return

        # are we a regular download following a metalink?
        parent = self.get(dl.following_id) if dl.following_id else None
        if parent and parent.is_metalink:
            if all(dl.is_complete for dl in parent.updated_followers):
                parent.post_process()
                return

        dl.post_process()

    def _cb_on_bt_download_complete(self, api: aria2p.API, gid: str):  # noqa: ARG002
        """aria2p.API callback for a completed bitTorrent download"""
        if not self.is_ours(gid):
            return
        dl = self.get(gid)
        dl.completed_on = datetime.datetime.now(tz=datetime.UTC)
        dl.post_process()

        # request post-processing of main torrent
        # post-processing will be refused if there are followers awaiting
        if dl.following_id:
            # retrieve from self.downloads as we attached final destination to it
            self.get(dl.following_id).post_process()

    def _cb_on_download_error(self, api: aria2p.API, gid: str):  # noqa: ARG002
        """aria2p.API callback for an errored download"""
        if not self.is_ours(gid):
            return
        dl = self.get(gid)
        try:
            dl.post_process()
        finally:
            if self.halt_on_error:
                self.halt()

    def _start_listening(self):
        """start listenning to aria2p.API's notifications

        You shouldn't need to use it as we listen on init"""
        if self.is_listening is not False:
            return

        self.is_listening = None
        self.api.listen_to_notifications(
            threaded=True,
            on_download_start=self._cb_on_download_start,
            on_download_pause=self._cb_on_download_pause,
            on_download_stop=self._cb_on_download_stop,
            on_download_complete=self._cb_on_download_complete,
            on_download_error=self._cb_on_download_error,
            on_bt_download_complete=self._cb_on_bt_download_complete,
            timeout=1,
        )
        self.is_listening = True

    def _stop_listening(self):
        """stop listenning to aria2p.API notifications

        Use at own risk as downloader behavior depends on it"""
        if self.is_listening is not True:
            return
        try:
            self.api.stop_listening()
        except RuntimeError:
            ...
        self.is_listening = False
