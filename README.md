# image-creator

RaspberryPi image creator to build OLIP or Kiwix Hotspot off [`base-image`](https://github.com/offspot/base-image).

[![CodeFactor](https://www.codefactor.io/repository/github/offspot/image-creator/badge)](https://www.codefactor.io/repository/github/offspot/image-creator)
[![Build Status](https://github.com/offspot/image-creator/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/offspot/image-creator/actions/workflows/build.yml?query=branch%3Amain)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)


## Usage

`image-creator` is to be **ran as `root`**.

```
❯ image-creator --help
usage: image-creator [-h] [--build-dir BUILD_DIR] [-C] [-K] [-X] [-T CONCURRENCY] [-D] [-V] CONFIG_SRC OUTPUT

create an Offspot Image from a config

positional arguments:
  CONFIG_SRC            Offspot Config YAML file path or URL
  OUTPUT                Where to write image to

options:
  -h, --help            show this help message and exit
  --build-dir BUILD_DIR
                        Directory to store temporary files in, like files that needs to be extracted. Defaults to some place within /tmp
  -C, --check           Only check inputs, URLs and sizes. Don't download/create image.
  -K, --keep            [DEBUG] Don't remove output image if creation failed
  -X, --overwrite       Don't fail on existing output image: remove instead
  -T CONCURRENCY, --concurrency CONCURRENCY
                        Nb. of threads to start for parallel downloads (at most one per file). `0` (default) for auto-selection based on CPUs.
                        `1` to disable concurrency.
  -D, --debug
  -V, --version         show program's version number and exit
```


## Configuration

Image configuration is done through a YAML file which must match the following format. Only `base` is required.



| Member           | Kind           | Function                                                                                                                                                           |
|------------------|----------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `base`           | `string`       | Version ([official releases](https://drive.offspot.it/base/)) or URL to a base-image file. Accepts `file://` URLs. Accepts lzma encoded images using `.xz` suffix  |
| `output.size`    | `string`/`int` | Requested size of output image. Accepts `auto` for an power-of-2 sized that can fit the content (⚠️ TBI)                                                           |
| `oci_images`     | `string[]`     | List of **specific** OCI Image names. Prefer ghcr.io if possible. [Format](https://github.com/opencontainers/.github/blob/master/docs/docs/introduction/digests.md)|
| `files`          | `file[]`       | List of files to include on the data partition. See below. One of `url` or `content` must be present                                                               |
| `files[].url`    | `string`       | URL to download file from                                                                                                                                          |
| `files[].to`     | `string`       | [required] Path to store file at. Must be a descendent of `/data`                                                                                                  |
| `files[].content`| `string`       | Text content of the file to write. Replaces `url` if present                                                                                                       |
| `files[].via`    | `string`       | For `url`-based files, transformation to apply on downloaded file: `direct` (default): simple download, `bztar`, `gztar`, `tar`, `xztar`, `zip` to expand archives |
| `files[].size`   | `string`/`int` | **Only for `untar`/`unzip`** should file be compressed. Specify expanded size. Assumes File-size (uncompressed) if not specified. ⚠️ Fails if lower than file size |
| `write_config`   | `bool`         | Whether to write this file to `/data/conf/image.yaml`                                                                                                              |
| `offspot`        | `dict`         | [runtime-config](https://github.com/offspot/runtime-config) configuration. Will be parsed and dumped to `/boot/offspot.yaml`                                       |

### Sample

```yaml
---
base: 1.0.0
output:
  size: 8G
oci_images:
- ghcr.io/offspot/kiwix-serve:dev
files:
- url: http://download.kiwix.org/zim/wikipedia_fr_test.zim
  to: /data/contents/zims/wikipedia_fr_test.zim
  via: direct
- to: /data/conf/message.txt
  content: |
    hello world
wite_config: true
offspot:
  timezone: Africa/Bamako
  ap:
    ssid: Kiwix Offspot
    as-gateway: true
    domain: demo
    tld: offspot
  containers:
    services:
      kiwix:
        container_name: kiwix
        image: ghcr.io/offspot/kiwix-serve:dev
        command: /bin/sh -c "kiwix-serve /data/*.zim"
        volumes:
          - "/data/contents/zims:/data:ro"
        ports:
          - "80:80"

```
