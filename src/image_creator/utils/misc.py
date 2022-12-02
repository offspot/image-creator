import lzma
import os
import pathlib
import shutil
import tarfile
import zipfile
from typing import Dict

import humanfriendly


def format_size(size: int) -> str:
    """human-readable representation of a size in bytes"""
    return humanfriendly.format_size(size, binary=True)


def parse_size(size: str) -> int:
    """size in bytes of a human-readable size representation"""
    return humanfriendly.parse_size(size)


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
