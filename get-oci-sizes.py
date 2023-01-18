#!/usr/bin/env python3

""" Outputs the sizes of an OCI Image for use in image.yaml

    Computes the file size of an exported image and its on-disk (extract) sizes
    either from an image name (as in docker/docker-export) or a tar file

    Dependencies:
        - docker-export must be present in PATH if not referencing a local file

    Usage:

        get-oci-sizes docker.io/library/caddy:2.6.1-alpine
        get-oci-sizes docker.io_library_caddy:2.6.1-alpine.tar


    Output:

        oci_images:
          - ident: docker.io/library/caddy:2.6.1-alpine
            # url: PREFIX/docker.io_library_caddy:2.6.1-alpine.tar
            filesize: 47206400  # 45.02MiB
            fullsize: 47156908  # 44.97MiB
"""

import pathlib
import subprocess
import sys
import tarfile
import tempfile
from typing import Tuple


def get_dirsize(fpath: pathlib.Path) -> int:
    """size in bytes of a local directory"""
    if not fpath.exists():
        raise FileNotFoundError(fpath)
    if fpath.is_file():
        raise IOError(f"{fpath} is a file")
    return sum(f.stat().st_size for f in fpath.rglob("**/*") if f.is_file())


def human(size: int) -> str:
    """Human-readable (MiB/GiB) size"""
    one_mib = 2**20
    one_gib = 2**30
    suffix = "GiB" if size >= one_gib else "MiB"
    denom = one_gib if size >= one_gib else one_mib
    value = size / denom
    return f"{value:.2f}{suffix}"


def sizes_from_path(fpath: pathlib.Path) -> Tuple[int, int]:
    """filesize and fullsize from a local tar file"""
    filesize = fpath.stat().st_size

    temp_dir = tempfile.TemporaryDirectory(
        prefix=fpath.name, ignore_cleanup_errors=True
    )
    temp_dir_path = pathlib.Path(temp_dir.name)
    with tarfile.open(fpath) as tar:
        tar.extractall(temp_dir_path)

    fullsize = get_dirsize(temp_dir_path)
    temp_dir.cleanup()
    return filesize, fullsize


def print_from_path(fpath: pathlib.Path, name: str = None):
    filesize, fullsize = sizes_from_path(fpath)
    fuzzy_text = "  # !fixup" if not name else ""
    name = name if name else fpath.stem
    print(
        f"oci_images:\n"
        f"  - ident: {name}{fuzzy_text}\n"
        f"    # url: PREFIX/{fpath.name}\n"
        f"    filesize: {filesize}  # {human(filesize)}\n"
        f"    fullsize: {fullsize}  # {human(fullsize)}\n"
    )


def download_image(name: str) -> pathlib.Path:
    """download image to a tar file using docker-export"""
    fs_name = pathlib.Path(f"{pathlib.Path(name.replace('/', '_'))}.tar")
    subprocess.run(
        ["/usr/bin/env", "docker-export", name, str(fs_name)],
        check=True,
        capture_output=True,
    )
    return fs_name


def main(name_or_path: str) -> int:
    fpath = pathlib.Path(name_or_path)

    if not fpath.exists():
        image_name = name_or_path
        print(f"Downloading {name_or_path} using docker-export", file=sys.stderr)
        fpath = download_image(name_or_path)
    else:
        image_name = None

    # not an existing path ; maybe it's an image name
    if not fpath.exists():
        print(f"Can't find image file at {fpath}", file=sys.stderr)
        return 1

    print_from_path(fpath, image_name)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} PATH")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
