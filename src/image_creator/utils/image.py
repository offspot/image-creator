from __future__ import annotations

import json
import pathlib
import re
import subprocess
import tempfile

from offspot_config.utils.misc import get_environ, rmtree


def get_image_size(fpath: pathlib.Path) -> int:
    """Size in bytes of the virtual device in image"""
    virtsize_re = re.compile(
        r"^virtual size: ([0-9\.\sa-zA-Z]+) \((?P<size>\d+) bytes\)"  # ya
    )
    for line in subprocess.run(
        ["/usr/bin/env", "qemu-img", "info", "-f", "raw", str(fpath)],
        check=True,
        capture_output=True,
        text=True,
        env=get_environ(),
    ).stdout.splitlines():
        match = virtsize_re.match(line)
        if match:
            return int(match.groupdict()["size"])
    return -1


def resize_image(fpath: pathlib.Path, size: int):
    """Resize virtual device in image (bytes)"""
    subprocess.run(
        ["/usr/bin/env", "qemu-img", "resize", "-f", "raw", fpath, str(size)],
        check=True,
        capture_output=True,
        text=True,
        env=get_environ(),
    )


def get_loopdev() -> str:
    """free loop-device path ready to ease"""
    return subprocess.run(
        ["/usr/bin/env", "losetup", "-f"],
        check=True,
        capture_output=True,
        text=True,
        env=get_environ(),
    ).stdout.strip()


def is_loopdev_free(loop_dev: str):
    """whether a loop-device (/dev/loopX) is not already attached"""
    devices = json.loads(
        subprocess.run(
            ["/usr/bin/env", "losetup", "--json"],
            check=True,
            capture_output=True,
            text=True,
            env=get_environ(),
        ).stdout.strip()
    )["loopdevices"]
    return loop_dev not in [device["name"] for device in devices]


def attach_to_device(img_fpath: pathlib.Path, loop_dev: str):
    """attach a device image to a loop-device"""
    subprocess.run(
        ["/usr/bin/env", "losetup", "--partscan", loop_dev, str(img_fpath)],
        check=True,
        capture_output=True,
        text=True,
        env=get_environ(),
    )


def detach_device(loop_dev: str, *, failsafe: bool = False) -> bool:
    """whether detaching this loop-device succeeded"""
    return (
        subprocess.run(
            ["/usr/bin/env", "losetup", "--detach", loop_dev],
            check=not failsafe,
            capture_output=True,
            text=True,
            env=get_environ(),
        ).returncode
        == 0
    )


def get_device_sectors(dev_path: str) -> int:
    """number of sectors composing this device"""
    summary_re = re.compile(
        rf"^Disk {dev_path}: (?P<size>[\d\.\s]+ [KMGP]iB+), "
        r"(?P<bytes>\d+) bytes, (?P<sectors>\d+) sectors$"
    )
    line = subprocess.run(
        ["/usr/bin/env", "fdisk", "--list", dev_path],
        check=True,
        capture_output=True,
        text=True,
        env=get_environ(),
    ).stdout.splitlines()[0]
    match = summary_re.match(line)
    if match:
        return int(match.groupdict()["sectors"])
    raise ValueError(f"Unable to get nb of sectors for {dev_path}")


def get_thirdpart_start_sector(dev_path) -> int:
    """Start sector number of third partition of device"""
    part_re = re.compile(
        rf"{dev_path}p3      (?P<start>\d+) (?P<end>\d+)  (?P<nb>\d+) .+$"
    )
    line = subprocess.run(
        ["/usr/bin/env", "fdisk", "--list", dev_path],
        check=True,
        capture_output=True,
        text=True,
        env=get_environ(),
    ).stdout.splitlines()[-1]
    match = part_re.match(line)
    if match:
        return int(match.groupdict()["start"])
    raise ValueError(f"Unable to get start sector for {dev_path}p3")


def resize_third_partition(dev_path: str):
    """recreate third partition of a device and its (ext4!) filesystem"""
    nb_sectors = get_device_sectors(dev_path)
    start_sector = get_thirdpart_start_sector(dev_path)
    end_sector = nb_sectors - 1

    # delete 3rd part and recreate from same sector until end of device
    commands = ["d", "3", "n", "p", "3", str(start_sector), str(end_sector), "N", "w"]
    subprocess.run(
        ["/usr/bin/env", "fdisk", dev_path],
        # fdisk might return ioctl failed to apply.
        # not much of an issue. in this case partprobe should help
        check=False,
        input="\n".join(commands),
        capture_output=True,
        text=True,
        env=get_environ(),
    )

    subprocess.run(
        ["/usr/bin/env", "partprobe", "--summary", dev_path],
        check=True,
        input="\n".join(commands),
        capture_output=True,
        text=True,
        env=get_environ(),
    )

    # check fs on 3rd part
    subprocess.run(
        ["/usr/bin/env", "e2fsck", "-p", f"{dev_path}p3"],
        check=True,
        capture_output=True,
        text=True,
        env=get_environ(),
    )

    # resize fs on 3rd part
    subprocess.run(
        ["/usr/bin/env", "resize2fs", f"{dev_path}p3"],
        check=True,
        capture_output=True,
        text=True,
        env=get_environ(),
    )


def mount_on(dev_path: str, mount_point: pathlib.Path, filesystem: str | None) -> bool:
    """whether mounting device onto mount point succeeded"""
    commands = ["/usr/bin/env", "mount"]
    if filesystem:
        commands += ["-t", filesystem]
    commands += [dev_path, str(mount_point)]
    return (
        subprocess.run(
            commands,
            capture_output=True,
            text=True,
            env=get_environ(),
        ).returncode
        == 0
    )


def unmount(mount_point: pathlib.Path) -> bool:
    """whether unmounting mount-point succeeded"""
    return (
        subprocess.run(
            ["/usr/bin/env", "umount", str(mount_point)],
            capture_output=True,
            text=True,
            env=get_environ(),
        ).returncode
        == 0
    )


class Image:
    """File-backed Image that can be attached/resized/mounted"""

    def __init__(self, fpath: pathlib.Path, mount_in: pathlib.Path | None = None):
        # ensure image is readable
        with open(fpath, "rb") as fh:
            fh.read(1024)
        self.fpath: pathlib.Path = fpath
        self.loop_dev: str = ""
        self.p1_mounted_on: pathlib.Path | None = None
        self.p3_mounted_on: pathlib.Path | None = None
        self.mount_in: pathlib.Path | None = mount_in

    @property
    def is_mounted(self) -> bool:
        return bool(self.p3_mounted_on) or bool(self.p1_mounted_on)

    @property
    def is_attached(self) -> bool:
        return bool(self.loop_dev)

    def get_size(self) -> int:
        """virtual device size"""
        return get_image_size(self.fpath)

    def assign_loopdev(self) -> str:
        """find a free loop device we'll use"""
        self.loop_dev = get_loopdev()
        return self.loop_dev

    def attach(self):
        """attach image to loop device"""
        if not self.loop_dev or is_loopdev_free(self.loop_dev):
            detach_device(self.loop_dev, failsafe=True)
            self.loop_dev = ""
            self.assign_loopdev()
        attach_to_device(self.fpath, self.loop_dev)

    def detach(self):
        """detach loop-device"""
        if self.is_mounted:
            self.unmount_all()
        if self.loop_dev and detach_device(self.loop_dev):
            self.loop_dev = ""
            return True
        return False

    def resize(self, to: int):
        """resize virtual device inside image (expand only)"""
        resize_image(self.fpath, size=to)

    def resize_last_part(self):
        """resize 3rd partition and filesystem to use all remaining space"""
        resize_third_partition(self.loop_dev)

    def mount_p1(self) -> pathlib.Path:
        """mount first (boot) partition"""
        return self.mount_part(1)

    def mount_p3(self) -> pathlib.Path:
        """mount third (data) partition"""
        return self.mount_part(3)

    def unmount_p1(self) -> pathlib.Path:
        """unmount first (boot) partition"""
        return self.unmount_part(1)

    def unmount_p3(self) -> pathlib.Path:
        """unmount third (data) partition"""
        return self.unmount_part(3)

    def mount_part(self, part_num: int) -> pathlib.Path:
        """path to mounted specific partition"""
        mount_point = pathlib.Path(
            tempfile.mkdtemp(dir=self.mount_in, prefix=f"part{part_num}_")
        )
        fs = "vfat" if part_num == 1 else "ext4"
        if mount_on(f"{self.loop_dev}p{part_num}", mount_point, fs):
            setattr(self, f"p{part_num}_mounted_on", mount_point)
        else:
            raise OSError(
                f"Unable to mount {self.loop_dev}p{part_num} on {mount_point}"
            )
        return mount_point

    def unmount_part(self, part_num: int) -> pathlib.Path:
        """unmount specific partition"""
        mount_point = getattr(self, f"p{part_num}_mounted_on")
        if not mount_point:
            return mount_point
        if unmount(mount_point):
            setattr(self, f"p{part_num}_mounted_on", None)
            rmtree(mount_point)
        else:
            raise OSError(f"Unable to unmount p{part_num} at {mount_point}")
        return mount_point

    def unmount_all(self):
        """failsafely unmount all partitions we would have mounted"""
        try:
            self.unmount_p1()
        except Exception:
            ...
        try:
            self.unmount_p3()
        except Exception:
            ...
