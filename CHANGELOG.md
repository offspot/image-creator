# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Support for `shrink` option in `output` (#21)

### Changed

- third-party commands (image) output displayed on --debug
- Create special devices for partitions if not exists (not only if fdisk failed)

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
