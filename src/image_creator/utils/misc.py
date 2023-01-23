import datetime
import inspect
import lzma
import os
import pathlib
import re
import shutil
import tarfile
import tempfile
import zipfile
from contextlib import suppress
from functools import wraps
from typing import Any, Dict, List, Optional, _SpecialForm, get_origin

import humanfriendly
import xattr


def format_size(size: int) -> str:
    """human-readable representation of a size in bytes"""
    return humanfriendly.format_size(size, binary=True)


def parse_size(size: str) -> int:
    """size in bytes of a human-readable size representation"""
    return humanfriendly.parse_size(size)


def format_dt(dt: datetime.datetime) -> str:
    """std formatted datetime"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_duration(duration: str) -> int:
    """duration in seconds of a human-readable duration representation"""
    return int(humanfriendly.parse_timespan(duration))


def format_duration(duration: int) -> str:
    """human-readble duration from seconds (1 week, 2 days)"""
    return humanfriendly.format_timespan(duration)


def get_filesize(fpath: pathlib.Path) -> int:
    """size in bytes of a local file path"""
    return fpath.stat().st_size


def get_dirsize(fpath: pathlib.Path) -> int:
    """size in bytes of a local directory"""
    if not fpath.exists():
        raise FileNotFoundError(fpath)
    if fpath.is_file():
        raise IOError(f"{fpath} is a file")
    return sum(f.stat().st_size for f in fpath.rglob("**/*") if f.is_file())


def get_size_of(fpath: pathlib.Path) -> int:
    """size in bytes of a local file or directory"""
    if not fpath.exists():
        raise FileNotFoundError(fpath)
    if fpath.is_file():
        return get_filesize(fpath)
    return get_dirsize(fpath)


def get_freespace(fpath: pathlib.Path) -> int:
    """free-space in bytes for the volume at fpath"""
    stat = os.statvfs(fpath)
    return stat.f_bavail * stat.f_frsize


def rmtree(fpath: pathlib.Path):
    """recursively remove an entire folder (rm -rf)"""
    shutil.rmtree(fpath, ignore_errors=True)


def ensure_dir(fpath: pathlib.Path):
    """recursively creating a folder (mkdir -p)"""
    fpath.mkdir(parents=True, exist_ok=True)


def get_environ() -> Dict[str, str]:
    """current environment variable with langs set to C to control cli output"""
    environ = os.environ.copy()
    environ.update({"LANG": "C", "LC_ALL": "C"})
    return environ


def extract_xz_image(src: pathlib.Path, dest: pathlib.Path):
    """Extract compressed (lzma via xz compress) image file"""
    buff_size = parse_size("16MiB")
    buffer = b""
    with lzma.open(src, "rb") as reader, open(dest, "wb") as writer:
        buffer = reader.read(buff_size)
        while buffer:
            writer.write(buffer)
            buffer = reader.read(buff_size)


def expand_file(src: pathlib.Path, method: str, dest: pathlib.Path):
    """Expand into dest failing should any member to-be written outside dest"""
    if method not in shutil._UNPACK_FORMATS.keys():
        raise NotImplementedError(f"Cannot expand `{method}`")

    # raise on unauthorized filenames instead of ignoring (zip) or accepting (tar)
    names = []
    if method == "zip":
        with zipfile.ZipFile(src, "r") as zh:
            names = zh.namelist()
    elif method == "tar" or method.endswith("tar"):
        with tarfile.Tarfile(src, "r") as th:
            names = th.getnames()
    for name in names:
        path = pathlib.Path(name)
        if path.root == "/":
            raise IOError(f"{method} file contains member with absolute path: {name}")
        path = dest.joinpath(name).resolve()
        if not path.is_relative_to(dest):
            raise IOError(f"{method} file contains out-of-bound member path: {name}")

    return shutil.unpack_archive(src, dest, method)


class SimpleAttrs(xattr.xattr):
    """Simple xattr wrapper to save specifying user. prefix"""

    def __init__(self, path: pathlib.Path):
        super().__init__(str(path))

    @classmethod
    def usered(cls, name: str) -> str:
        return f"user.{cls.unusered(name)}"

    @classmethod
    def unusered(cls, name: str) -> str:
        return re.sub(r"^user.", "", name)

    def get(self, name: str) -> str:
        return super().get(self.usered(name)).decode("UTF-8")

    def set(self, name: str, value: str):
        return super().set(self.usered(name), value.encode("UTF-8"))

    def list(self) -> List[str]:
        return [self.unusered(name) for name in super().list()]


def device_supports(dev_path: str, fs: str, option: str) -> bool:
    """whether device, mounted as `fs` has fs-option `option` enabled"""
    try:
        options = pathlib.Path(
            f"/proc/fs/{fs}/{pathlib.Path(dev_path).resolve().name}/options"
        ).read_text()
    except Exception:
        return False
    return bool(re.search(rf"^{option}", options, re.MULTILINE))


def supports_xattr(path: pathlib.Path) -> bool:
    """whether path's filesystem supports user_xattr"""
    path = path.resolve()
    for line in pathlib.Path("/proc/mounts").read_text().splitlines():
        device, mount, fs, _ = line.split(" ", 3)
        mp = pathlib.Path(mount)
        if path.is_relative_to(mp) and mp.stat().st_dev == path.stat().st_dev:
            return device_supports(device, fs, "user_xattr")

    def test_xattr(test_path: pathlib.Path) -> bool:
        test_path.touch()
        try:
            attrs = SimpleAttrs(test_path)
            attrs.list()
            attrs["flag"] = "value"
            if attrs["flag"] != "value":
                return False
        except Exception:
            return False
        return True

    if path.is_file():
        return test_xattr(path)

    with tempfile.NamedTemporaryFile(dir=path) as tmp:
        return test_xattr(pathlib.Path(tmp.name))


def is_dict(value: Any, accepts_none: Optional[bool] = False) -> bool:
    """whether value is a Dict (shortcut)"""
    return isinstance(value, dict)


def is_list(value: Any, accepts_none: Optional[bool] = False) -> bool:
    """whether value is a List (shortcut)"""
    return isinstance(value, list)


def is_list_of_dict(value: Any, accepts_none: Optional[bool] = False) -> bool:
    """whether value is a list for which each element is a Dict (shortcut)"""
    if not is_list(value, accepts_none):
        return False

    if accepts_none and value is None:
        return True

    return all([is_dict(item, False) for item in value])


def copy_file(src_path: pathlib.Path, dest_path: pathlib.Path):
    """Copy src into dest"""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dest_path)


def is_http(url: str) -> bool:
    """whether this URL is using HTTP(s) protocol"""
    return re.match(r"https?://", url)


class IncorectType(TypeError):
    def __init__(self, where: str, field: str, expected: str, found: type):
        self.where = where
        self.field = field
        self.expected = expected
        self.found = found
        super().__init__(f"{where}.{field} expects {expected}, not {found.__name__}")


def enforce_types(callable):
    spec = inspect.getfullargspec(callable)

    def check_types(*args, **kwargs):
        parameters = dict(zip(spec.args, args))
        parameters.update(kwargs)
        for name, value in parameters.items():
            with suppress(KeyError):  # Assume un-annotated parameters can be any type
                type_hint = spec.annotations[name]
                if isinstance(type_hint, _SpecialForm):
                    # No check for Any, Union, ClassVar
                    # without parameters
                    continue
                actual_type = get_origin(type_hint) or type_hint
                if isinstance(actual_type, _SpecialForm):
                    # case of typing.Union[…] or typing.ClassVar[…]
                    actual_type = type_hint.__args__

                if not isinstance(value, actual_type):
                    raise IncorectType(callable.__name__, name, type_hint, type(value))

    def decorate(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            check_types(*args, **kwargs)
            return func(*args, **kwargs)

        return wrapper

    if inspect.isclass(callable):
        callable.__init__ = decorate(callable.__init__)
        return callable

    return decorate(callable)
