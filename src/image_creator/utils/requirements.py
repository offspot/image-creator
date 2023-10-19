from __future__ import annotations

import os
import re
import subprocess

help_text = """
Requirements
------------

kernel features:
    - `loop` must be enabled in your kernel or as a module
       if running inside a docker-container:
        - same loop feature applies to host's kernel
        - container must be run with --privileged
    - `ext4` filesystem (most likely enabled in-kernel)

tools:
    - losetup (mount)
    - fdisk (fdisk)
    - resize2fs (e2fsprogs)
    - mount (mount)
    - umount (mount)
    - qemu-img (qemu-utils)
    - partprobe (parted)

Sample setup (debian)
sudo modprobe --first-time loop
sudo modprobe --first-time ext4
sudo apt-get install --no-install-recommends mount fdisk e2fsprogs qemu-utils parted
"""


def is_root() -> bool:
    """whether running as root"""
    return os.getuid() == 0


def has_ext4_support() -> bool:
    """whether ext4 filesystem is enabled"""
    with open("/proc/filesystems") as fh:
        for line in fh.readlines():
            if re.match(r"\s*ext4\s?$", line.strip()):
                return True
    return False


def has_all_binaries() -> tuple[bool, list[str]]:
    """whether all required binaries are present, with list of missing ones"""
    missing_bins: list[str] = []
    missing_bin_retcode = 127
    for binary in (
        "e2fsck",
        "fdisk",
        "losetup",
        "mount",
        "partprobe",
        "qemu-img",
        "resize2fs",
        "umount",
    ):
        try:
            if (
                subprocess.run(
                    ["/usr/bin/env", binary], check=False, capture_output=True
                ).returncode
                == missing_bin_retcode
            ):
                missing_bins.append(binary)
        except Exception:
            missing_bins.append(binary)
    return not missing_bins, missing_bins


def has_loop_device() -> bool:
    """whether requesting a loop-device is possible"""
    return (
        subprocess.run(
            ["/usr/bin/env", "losetup", "-f"], check=False, capture_output=True
        ).returncode
        == 0
    )
