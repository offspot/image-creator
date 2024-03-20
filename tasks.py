# pyright: strict, reportUntypedFunctionDecorator=false
import base64
import hashlib
import os
import pathlib
import shlex
import shutil
import sys
import tempfile
import zipfile

import requests
from invoke.context import Context
from invoke.tasks import task  # pyright: ignore [reportUnknownVariableType]

from image_creator import __version__

ARIA2_RELEASE = (
    "https://github.com/abcfy2/aria2-static-build/releases/download/1.37.0/"
    "aria2-x86_64-linux-musl_libressl_static.zip"
)
ARIA2_BIN = pathlib.Path("aria2c")
use_pty = not os.getenv("CI", "")


@task(optional=["args"], help={"args": "pytest additional arguments"})
def test(ctx: Context, args: str = ""):
    """run tests (without coverage)"""
    ctx.run(f"pytest {args}", pty=use_pty)


@task(optional=["args"], help={"args": "pytest additional arguments"})
def test_cov(ctx: Context, args: str = ""):
    """run test vith coverage"""
    ctx.run(f"coverage run -m pytest {args}", pty=use_pty)


@task(optional=["html"], help={"html": "flag to export html report"})
def report_cov(ctx: Context, *, html: bool = False):
    """report coverage"""
    ctx.run("coverage combine", warn=True, pty=use_pty)
    ctx.run("coverage report --show-missing", pty=use_pty)
    if html:
        ctx.run("coverage html", pty=use_pty)


@task(
    optional=["args", "html"],
    help={
        "args": "pytest additional arguments",
        "html": "flag to export html report",
    },
)
def coverage(ctx: Context, args: str = "", *, html: bool = False):
    """run tests and report coverage"""
    test_cov(ctx, args=args)
    report_cov(ctx, html=html)


@task(optional=["args"], help={"args": "black additional arguments"})
def lint_black(ctx: Context, args: str = "."):
    args = args or "."  # needed for hatch script
    ctx.run("black --version", pty=use_pty)
    ctx.run(f"black --check --diff {args}", pty=use_pty)


@task(optional=["args"], help={"args": "ruff additional arguments"})
def lint_ruff(ctx: Context, args: str = "."):
    args = args or "."  # needed for hatch script
    ctx.run("ruff --version", pty=use_pty)
    ctx.run(f"ruff check {args}", pty=use_pty)


@task(
    optional=["args"],
    help={
        "args": "linting tools (black, ruff) additional arguments, typically a path",
    },
)
def lintall(ctx: Context, args: str = "."):
    """Check linting"""
    args = args or "."  # needed for hatch script
    lint_black(ctx, args)
    lint_ruff(ctx, args)


@task(optional=["args"], help={"args": "check tools (pyright) additional arguments"})
def check_pyright(ctx: Context, args: str = ""):
    """check static types with pyright"""
    ctx.run("pyright --version")
    ctx.run(f"pyright {args}", pty=use_pty)


@task(optional=["args"], help={"args": "check tools (pyright) additional arguments"})
def checkall(ctx: Context, args: str = ""):
    """check static types"""
    check_pyright(ctx, args)


@task(optional=["args"], help={"args": "black additional arguments"})
def fix_black(ctx: Context, args: str = "."):
    """fix black formatting"""
    args = args or "."  # needed for hatch script
    ctx.run(f"black {args}", pty=use_pty)


@task(optional=["args"], help={"args": "ruff additional arguments"})
def fix_ruff(ctx: Context, args: str = "."):
    """fix all ruff rules"""
    args = args or "."  # needed for hatch script
    ctx.run(f"ruff --fix {args}", pty=use_pty)


@task(
    optional=["args"],
    help={
        "args": "linting tools (black, ruff) additional arguments, typically a path",
    },
)
def fixall(ctx: Context, args: str = "."):
    """Fix everything automatically"""
    args = args or "."  # needed for hatch script
    fix_black(ctx, args)
    fix_ruff(ctx, args)
    lintall(ctx, args)


@task(optional=["force"], help={"force": "Download even if aria2c bin is present"})
def download_aria2c(ctx: Context, *, force: bool = False):  # noqa: ARG001
    aria2c_bin = pathlib.Path.cwd() / ARIA2_BIN
    if aria2c_bin.exists() and not force:
        print(f"{aria2c_bin.resolve()} already exixts.")
        return

    resp = requests.get(ARIA2_RELEASE, stream=True, timeout=5)
    resp.raise_for_status()
    total = int(resp.headers.get("Content-Length", "1"))
    received_sum = resp.headers.get("content-md5", "").strip()
    downloaded = 0
    aria2c_bin.parent.mkdir(parents=True, exist_ok=True)
    aria2c_bin_zip = aria2c_bin.with_name(f"{aria2c_bin.name}.zip")
    md5sum = hashlib.md5()  # noqa: S324
    print(f"Downloading {aria2c_bin_zip.resolve()} from {ARIA2_RELEASE}…")
    with open(aria2c_bin_zip, "wb") as fh:
        for data in resp.iter_content(1048576):  # 1MiB
            nb_received = len(data)
            downloaded += nb_received
            fh.write(data)
            percent = downloaded / total * 100
            print(f"\r*** {downloaded}b of {total}b ({percent:.2f}%)", end="")
            md5sum.update(data)
    print("")
    computed_sum = base64.standard_b64encode(md5sum.digest()).decode("UTF-8").strip()
    if received_sum and received_sum != computed_sum:
        print("Checksum mismatch! {received_sum=} - {computed_sum=}")
        print("Removing.")
        aria2c_bin.unlink()
        aria2c_bin_zip.unlink()
        return 1
    print(f"Checksum matches! {md5sum.hexdigest()}")

    with tempfile.TemporaryDirectory() as dirname:
        folder = pathlib.Path(dirname)
        with zipfile.ZipFile(aria2c_bin_zip) as zh:
            zh.extract("aria2c", path=folder)
        shutil.move(folder.joinpath("aria2c"), aria2c_bin)
    aria2c_bin_zip.unlink()
    print(f"Downloaded aria2c at {aria2c_bin.resolve()}: {aria2c_bin.stat().st_size}b")


@task(
    optional=["filename", "compress"],
    help={
        "filename": "output filename or fullname for the output binary",
        "no-compress": "dont zstd-compress binary (faster startup on macOS)",
    },
)
def binary(ctx: Context, filename: str = "", *, no_compress: bool = False):
    """build a standalone binary executable with nuitka"""
    fpath = (
        pathlib.Path(
            filename or f"image-creator_{__version__}{'-nc' if no_compress else ''}"
        )
        .expanduser()
        .resolve()
    )
    fpath.parent.mkdir(parents=True, exist_ok=True)
    pyexe = shlex.quote(sys.executable)

    command = [
        str(pyexe),
        "-m",
        "nuitka",
        "--onefile",
        "--python-flag=no_site,no_warnings,no_asserts,no_docstrings",
        "--include-package=image_creator",
        "--include-data-files=aria2c=aria2c",
        "--show-modules",
        "--warn-implicit-exceptions",
        "--warn-unusual-code",
        "--assume-yes-for-downloads",
        f'--output-dir="{fpath.parent!s}"',
        f'--output-filename="{fpath.name}"',
        "--remove-output",
        "--no-progressbar",
    ]
    if no_compress:
        command.append("--onefile-no-compression")
    command.append("src/image_creator/")
    ctx.run(" ".join(command))
