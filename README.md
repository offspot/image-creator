# image-creator

RaspberryPi image creator to build OLIP or Kiwix Hotspot off [`base-image`](https://github.com/offspot/base-image).

[![CodeFactor](https://www.codefactor.io/repository/github/offspot/image-creator/badge)](https://www.codefactor.io/repository/github/offspot/image-creator)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![PyPI - Python Version](https://img.shields.io/badge/python-3.11-blue)]([https://pypi.org/project/great_project](https://drive.offspot.it/image-creator/))

## Usage

`image-creator` is to be **ran as `root`**.

```
❯ image-creator --help
usage: image-creator [-h] [--build-dir BUILD_DIR] [--cache-dir CACHE_DIR] [-C] [-K] [-X] [-T CONCURRENCY] [-D] [-V] CONFIG_SRC OUTPUT

Create an Offspot Image from a config file

positional arguments:
  CONFIG_SRC            Offspot Config YAML file path or URL
  OUTPUT                Where to write image to

options:
  -h, --help            show this help message and exit
  --build-dir BUILD_DIR
                        Directory to store temporary files in, like files that needs to be extracted. Defaults to some place within
                        /var/folders/p3/58pln35d7y15wpvvl49q3xpm0000gn/T
  --cache-dir CACHE_DIR
                        Directory to use as a download cache. Should a remote file be present in the cache, it is fetched from there instead
                        of being downloaded. Files matching the cache policy are stored to the cache once downloaded. Cache Policy can be
                        configured in CACHE_DIR/policy.yaml
  -C, --check           Only check inputs, URLs and sizes. Don't download/create image.
  -K, --keep            [DEBUG] Don't remove output image if creation failed
  -X, --overwrite       Don't fail on existing output image: remove instead
  -T CONCURRENCY, --concurrency CONCURRENCY
                        Nb. of threads to start for parallel downloads (at most one per file). `0` (default) for auto-selection based on CPUs.
                        `1` to disable concurrency.
  -D, --debug
  -V, --version         show program's version number and exit

See https://github.com/offspot/image-creator for config and cache-policy format
```


## Configuration

Image configuration is done through a YAML file which must match the following format. Only `base` is required.

| Member             | Kind           | Function                                                                                                                                                           |
|--------------------|----------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **`base`**         |                | Reference to the Offspot Base Image to use                                                                                                                         |
| `base.source`      | `string`       | Version ([official releases](https://drive.offspot.it/base/)) or URL to a base-image file. Accepts `file://` URLs. Accepts lzma encoded images using `.xz` suffix  |
| `base.root_size`   | `string`/`int` | Size of the root (system) partition in the referenced base image (used to calculate free space)                                                                    |
| `output.size`      | `string`/`int` | Requested size of output image. Accepts `auto` for a cluster-aligned size that can fit the content                                                                 |
| `output.shrink`    | `bool`         | Whether to shrink output image file to actual content's size (a 128GB image with 10GB content will thus be shrunk to 10GB)                                         |
| `oci_images`       | `image[]`      | List of  OCI Image                                                                                                                                                 |
| **`image[].ident`**| `string`       | **specific** OCI Image name. Prefer ghcr.io if possible. [Format](https://github.com/opencontainers/.github/blob/master/docs/docs/introduction/digests.md)         |
| `image[].url`      | `string`       | Optional URL to the exported tar file of the image. Downloaded from registry if not present                                                                        |
| `image[].filesize` | `int`          | Size in bytes of the exported tar file of the image                                                                                                                |
| `image[].fullsize` | `int`          | Size in bytes of the extracted tar file of the image. See Get OCI Image Sizes below                                                                                |
| `files`            | `file[]`       | List of files to include on the data partition. See below. One of `url` or `content` must be present                                                               |
| `files[].url`      | `string`       | URL to download file from                                                                                                                                          |
| `files[].to`       | `string`       | [required] Path to store file at. Must be a descendent of `/data`                                                                                                  |
| `files[].content`  | `string`       | Text content of the file to write. Replaces `url` if present                                                                                                       |
| `files[].via`      | `string`       | For `url`-based files, transformation to apply on downloaded file: `direct` (default): simple download, `bztar`, `gztar`, `tar`, `xztar`, `zip` to expand archives |
| `files[].size`     | `string`/`int` | **Only for `*tar`/`zip`** should file be compressed. Specify expanded size. Assumes File-size (uncompressed) if not specified. ⚠️ Fails if lower than file size |
| `write_config`     | `bool`         | Whether to write this file to `/data/conf/image.yaml`                                                                                                              |
| `offspot`          | `dict`         | [runtime-config](https://github.com/offspot/runtime-config) configuration. Will be parsed and dumped to `/boot/offspot.yaml`                                       |

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

### Get OCI Images Sizes

`image-creator` needs to know about all content's sizes in order to create the image. This includes the OCI Image filesize and fullsize (once extracted on disk). To simplify getting those values, you can use a script that will download the image and extract it to compute the necessary sizes and print the required information in the appropriate format for copy-pasting.

```sh
curl -o ~/bin/get-oci-sizes https://raw.githubusercontent.com/offspot/image-creator/main/get-oci-sizes.py && chmod +x ~/bin/get-oci-sizes
```

**Usage:**

```sh
❯ ./get-oci-sizes.py ghcr.io/offspot/base-httpd:dev
Downloading ghcr.io/offspot/base-httpd:dev using docker-export
oci_images:
  - id: ghcr.io/offspot/base-httpd:dev
    # url: PREFIX/ghcr.io_offspot_base-httpd:dev.tar
    filesize: 5980160  # 5.70MiB
    fullsize: 5944584  # 5.67MiB
❯ ./get-oci-sizes.py caddy-2.6.1-alpine_armv6.tar
oci_images:
  - id: caddy-2.6.1-alpine_armv6  # !fixup
    # url: PREFIX/caddy-2.6.1-alpine_armv6.tar
    filesize: 43847680  # 41.82MiB
    fullsize: 43803832  # 41.77MiB
```

## Cache Policy

As `image-creator` mostly moves content from remote locations into an image file, it features a configurable *Cache* so that users creating multiple images have an option to download-once and reuse.

The cache is optional and set to a particular directory using `--cache-dir` option. The cache is flexible and configurable via a `policy.yaml` file inside of it. A default cache-policy is created if not present.

_Only define the properties you want_. Everything's optionnal. Sub-levels are bound by upper level. If you define a filter, a **`pattern`** is mandatory.

| Key                      | Kind       | Default   | Function                                                                                      |
|--------------------------|------------|-----------|-----------------------------------------------------------------------------------------------|
| `enabled`                | `bool`     | `true`    | Use to disable the cache completely.                                                          |
| `max_size`               | `size`     | `10GiB`   | Overalll maximum size for cache. `0` disables                                                 |
| `max_age`                | `duration` |           | Duration after which an entry should be evicted (from added-date)                             |
| `max_num`                | `int`      |           | Max number of items to keep in cache. `0` disables                                            |
| `eviction`               | `string`   | `lru`     | Main eviction Strategy. One of `oldest`, `newest`, `largest`, `smallest`, `lru`               |
| `oci_images`             | `dict`     |           | OCI-Images specific cache configuration                                                       |
| `oci_images.enabled`     | `bool`     | `true`    | If `false`, no OCI Image is cached.                                                           |
| `oci_images.max_size`    | `size`     |           | Size of the OCI-Images cache. Must fit witin main cache size                                  |
| `oci_images.max_age`     | `duration` |           | Duration after which an OCI Image should be evicted                                           |
| `oci_images.max_num`     | `int`      |           | Max number of OCI-Images to keep in cache.                                                    |
| `oci_images.eviction`    | `string`   | `lru`     | OCI Images Eviction Strategy                                                                  |
| `oci_images.filters`     | `list`     |           | Patterns to override config for. First matched is applied. Options applies to all matched     |
| **`.filters[].pattern`** | `string`   |           | Regexp to match OCI Image identifier. ex: `\/kiwix\/`                                         |
| `.filters[].max_size`    | `size`     |           | Max total size of cache for entries of this pattern                                           |
| `.filters[].max_age`     | `duration` |           | Duration after which entries of this pattern should be evicted                                |
| `.filters[].max_num`     | `int`      |           | Max number of cache entries for this pattern                                                  |
| `.filters[].eviction`    | `string`   | `lru`     | Eviction strategy for cache entries of this pattern                                           |
| `.filters[].ignore`      | `bool`     | `false`   | Don't cache entries of this pattern                                                           |
| `files`                  | `dict`     |           | Files (content and base image) specific cache configuration                                   |
| `files.enabled`          | `bool`     | `true`    | If `false`, no file/base is cached.                                                           |
| `files.max_size`         | `size`     |           | Size of the Files/base cache. Must fit witin main cache size                                  |
| `files.max_age`          | `duration` |           | Duration after which Files/base should be evicted                                             |
| `files.max_num`          | `int`      |           | Max number of files/base to keep in cache.                                                    |
| `files.eviction`         | `string`   | `lru`     | Files Eviction Strategy                                                                       |
| `files.filters`          | `list`     |           | Patterns to override config for. First matched is applied. Options applies to all matched     |
| **`.filters[].pattern`** | `string`   |           | Regexp to match Files URLs. ex: `https?\/\/download\.kiwix\.org\/zim\/`                       |
| `.filters[].max_size`    | `size`     |           | Max total size of cache for entries of this pattern                                           |
| `.filters[].max_age`     | `duration` |           | Duration after which entries of this pattern should be evicted                                |
| `.filters[].max_num`     | `int`      |           | Max number of cache entries for this pattern                                                  |
| `.filters[].eviction`    | `string`   |           | Eviction strategy for cache entries of this pattern                                           |
| `.filters[].ignore`      | `bool`     | `false`   | Don't cache entries of this pattern                                                           |

- `size` type is a parse-able file size string (`1G`, `2.4GiB`) or `0` string.
- `duration` type is a parse-able timespan string (`30d` `4w` `1y`) or `0` string.

### Default Policy

```yaml
---
enabled: true
max_size: 10GiB
eviction: lru
oci_images:
  enabled: true
  eviction: lru
files:
  enabled: true
  eviction: lru
```

### Sample Policy

```yaml
---
enabled: true
max_size: 0
oci_images:
  eviction: oldest
files:
  enabled: false
```
