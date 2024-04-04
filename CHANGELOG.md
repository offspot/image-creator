# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Third partition is checked once again after being unmounted to ensure clean state
- Use force (-f) on resize2fs for part3

## [1.0.0] - 2024-04-01

### Added

- `utils.aria2` module with an aria2c based downloader (communicates via RPC)

### Changed

- Base image download and Content download now use aria2 downloader

### Removed

- `--concurrency` CLI param

## [0.9.4] - 2024-01-30

### Fixed

- Multiple versions of an OCI Image can now be used together (#28)

## [0.9.3] - 2024-01-29

### Fixed

- [cache] `keep_identified_versions` evicting same-filename entries from different sources

## [0.9.2] - 2024-01-26

### Changed

- Using offspot-config 1.7.2

## [0.9.1] - 2024-01-22

### Fixed

- Filters matching not owning Cache entries (all matching filters were applied)

## [0.9] - 2024-01-22

### Added

- `check_after` Cache Policy property to mark a cache entry _not outdated_ for a period of time
- `keep_identified_versions` Cache Policy property to evict older version of identified ZIMs and Images

### Changed

- `pattern` matching in Cache Policy now uses `re.findall` for simpler regexp matching

## [0.8] - 2024-01-03

### Changed

- Use a larger buffer to download (4MiB per download)
- Update download progressbar at most once per second
- Updated dev/build dependencies (building with nuikta 1.9)

## [0.7] - 2023-12-23

### Changed

- third-party commands (image) output displayed on --debug
- Create special devices for partitions if not exists (not only if fdisk failed)
- Removed extra (ignored) command to fdisk on part3 recreate
- Always check third part before resize2fs (even if it appears clean)
- using parted instead of fdisk
- querying kernel for partition sizes (removed lsblk dependency)
- using offspot-config 1.5.0

## [0.6] - 2023-10-20

### Added

- detach/reattach tweak for in-docker special-blkdev not usable

## [0.5] - 2023-10-20

### Changed

- Fixed OCI Image download
- Prevented HTTP inconsistencies (HEAD vs GET size) to break due to progress error
- [TEMPORARILY] Adding a lot of debug to image resizing underlying calls

## [0.4] - 2023-10-19

### Changed

- `dmsetup` (`dmsetup`) added to requirements checks
- upgraded offspot-config to 1.4.3

## [0.3] - 2023-10-19

### Added

- Special Block devices manually created/removed when not done by system (in docker)

### Changed

- `partprobe` (`parted`) added to requirements checks

## [0.2] - 2023-10-18

### Changed

- allow disabled cache policy to not define sub policies
- upgrade offspot-config to 1.4.2


## [0.1] - 2023-10-10

- initial version
