from __future__ import annotations

import collections
import datetime
import pathlib
import re
from collections.abc import Iterable

from natsort import natsorted
from offspot_config.inputs.file import File
from offspot_config.oci_images import OCIImage
from offspot_config.utils.misc import (
    SimpleAttrs,
    copy_file,
    format_dt,
    format_duration,
    format_size,
    get_filesize,
    is_http,
)

from image_creator.cache.policy import (
    Eviction,
    FilesPolicy,
    MainPolicy,
    OCIImagePolicy,
)
from image_creator.constants import Global, logger
from image_creator.utils.download import get_digest

Item = File | OCIImage

# version regexp from filename's stem for images and files
RE_OCI_VERSIONED = re.compile(r"^(?P<ident>.+):(?P<version>.+)$")
RE_FILES_VERSIONED = re.compile(r"^(?P<ident>.+)_(?P<version>\d{4}-\d{2}).zim$")


def path_for_item(item: Item) -> pathlib.Path:
    return path_for_image(item) if isinstance(item, OCIImage) else path_for_file(item)


def path_for_image(image: OCIImage) -> pathlib.Path:
    """cache-relative path for an image archive"""
    fname = image.oci.name
    if image.oci.tag:
        fname += f":{image.oci.tag}"
    if image.oci.digest:
        fname += f"@{image.oci.digest}"
    return (
        pathlib.Path("images")
        .joinpath(image.oci.registry)
        .joinpath(image.oci.repository)
        .joinpath(fname)
    )


def path_for_file(file: File) -> pathlib.Path:
    """cache-relative path for a File"""
    if not file.url:
        raise OSError("Cannot infer path for non-url File")
    # fake a filepath should there be none
    if not file.url.path or file.url.path == "/":
        path = pathlib.Path("/__ROOT__")
    else:
        path = pathlib.Path(file.url.path).resolve()

    # add params/query/fragment to basename to ensure uniqueness
    fname = path.parts[-1]
    if file.url.params:
        fname += f";{file.url.params}"
    if file.url.query:
        fname += f"?{file.url.query}"
    if file.url.fragment:
        fname += f"#{file.url.fragment}"

    return (
        pathlib.Path("files")
        .joinpath(file.url.scheme)
        .joinpath(file.url.netloc)
        # excluding last part (basename) and first (leading slash)
        .joinpath(*path.parts[1:-1])
        .joinpath(fname)
    )


def file_is_entry(fpath: pathlib.Path) -> bool:
    """whether this file looks like a CacheEntry (has source metatada)"""
    return "digest" in SimpleAttrs(fpath)


def digest_for_item(item: Item) -> str:
    return (
        item.oci.get_digest(Global.platform)
        if isinstance(item, OCIImage)
        else get_digest(item.source)
    )


def sort_for(eviction: str, iterable: Iterable):
    key, reverse = {
        Eviction.oldest: ("added_on", False),
        Eviction.newest: ("added_on", True),
        Eviction.largest: ("size", False),
        Eviction.smallest: ("size", True),
        Eviction.lru: ("last_used_on", True),
    }[eviction]
    return sorted(iterable, key=lambda item: getattr(item, key), reverse=reverse)


class CacheEntry:
    def __init__(self, fpath):
        self.fpath = fpath
        self.size = get_filesize(self.fpath)
        metadata = SimpleAttrs(self.fpath)
        self.added_on = datetime.datetime.fromisoformat(metadata["added_on"])
        self.last_checked_on = (
            datetime.datetime.fromisoformat(metadata["last_checked_on"])
            if "last_checked_on" in metadata
            else self.added_on
        )
        self.last_used_on = datetime.datetime.fromisoformat(metadata["last_used_on"])
        self.nb_used = int(metadata["nb_used"])
        self.kind, self.source = metadata["source"].split(":", 1)
        self.digest = metadata["digest"]
        self.check_after: int | None = None

    def __repr__(self):
        return f"{type(self).__name__}(fpath={self.fpath}, self.source={self.source})"

    def get_remote_digest(self) -> str:
        """retrieve remote source digest"""
        if self.kind == "image":
            return OCIImage(self.source, filesize=-1, fullsize=-1).oci.get_digest(
                Global.platform
            )
        return get_digest(self.source)

    @property
    def is_outdated(self):
        # force checking outdacy
        return self.is_outdated_if(checked_before=0)

    def is_outdated_if(self, checked_before: int | None = None):
        r"""whether remote resource got updated (has different digest)

        Accounts for `checked_before`: number of seconds to consider a checked
        file not needing another check.

        /!\ digest-less URLs are always considered outdated, when checked"""
        if checked_before is None:
            checked_before = self.check_after

        # consider not outdated if within policy's check bound
        if (
            checked_before
            and self.last_checked_on + datetime.timedelta(seconds=checked_before)
            >= datetime.datetime.utcnow()
        ):
            return False

        if not self.digest:
            return True
        try:
            remote = self.get_remote_digest()
        except Exception as exc:
            logger.exception(OSError(f"failed to retrieve remote digest: {exc}"))
            return False

        outdated = not remote or remote != self.digest
        # outdated are not marked as checked to prevent caching a result
        # that should already be evicted but might not have been
        if not outdated:
            self.mark_checked()
        return outdated

    def mark_checked(self):
        self.last_checked_on = datetime.datetime.utcnow()
        metadata = SimpleAttrs(self.fpath)
        metadata["last_checked_on"] = self.last_checked_on.isoformat()

    def mark_usage(self, num: int = 1):
        self.nb_used += num
        self.last_used_on = datetime.datetime.utcnow()
        metadata = SimpleAttrs(self.fpath)
        metadata["nb_used"] = str(self.nb_used)
        metadata["last_used_on"] = self.last_used_on.isoformat()

    __iadd__ = mark_usage

    def __int__(self) -> int:
        return self.nb_used


class CacheCandidate(CacheEntry):
    def __init__(self, item: Item, added_on: datetime.datetime | None = None):
        self.kind = item.kind
        self.source = item.source
        self.size = item.size
        self.fpath = path_for_item(item)
        self.digest = digest_for_item(item)
        self.nb_used = 0
        self.added_on = self.last_checked_on = self.last_used_on = (
            added_on or datetime.datetime.utcnow()
        )


def get_eviction_for(
    entries: list[CacheEntry], policy: MainPolicy | OCIImagePolicy | FilesPolicy
) -> list[tuple[CacheEntry, str]]:
    """list of (entry, reason) from entries that are expired or outdated"""
    if (
        hasattr(policy, "enabled")
        and not policy.enabled  # pyright: ignore[reportGeneralTypeIssues]
    ):
        return []

    evictions = []

    total_num = 0
    total_size = 0

    idv = collections.namedtuple("Identified", ["ident", "version"])

    def matched_ident_version(entry: CacheEntry) -> idv | None:
        """ident and version from entry if it was identified as versioned (or None)"""
        if isinstance(policy, OCIImagePolicy):
            re_version = RE_OCI_VERSIONED
        if isinstance(policy, FilesPolicy):
            re_version = RE_FILES_VERSIONED
        if isinstance(policy, OCIImagePolicy | FilesPolicy):
            version_match = re_version.match(  # pyright: ignore [reportUnboundVariable]
                str(entry.fpath)
            )
            if version_match:
                return idv(
                    version_match.groupdict()["ident"],
                    version_match.groupdict()["version"],
                )
            return
        # we're at main policy
        oci_version_match = RE_OCI_VERSIONED.match(str(entry.fpath))
        if oci_version_match:
            return idv(
                oci_version_match.groupdict()["ident"],
                oci_version_match.groupdict()["version"],
            )
        files_version_match = RE_FILES_VERSIONED.match(str(entry.fpath))
        if files_version_match:
            return idv(
                files_version_match.groupdict()["ident"],
                files_version_match.groupdict()["version"],
            )
        return

    if hasattr(policy, "filters"):
        for (
            filter_
        ) in (
            policy.filters  # pyright: ignore [reportAttributeAccessIssue, reportGeneralTypeIssues]
        ):
            filter_num = 0
            filter_size = 0
            filter_versioned_entries = {}

            for entry in sort_for(filter_.eviction, entries):
                # dont look at this filter policy if entry doesnt match
                if not filter_.match(entry.source) or getattr(entry, "__seen", False):
                    continue

                # mark seen so first matching filter only applies
                setattr(entry, "__seen", True)

                # apply filter check_after to entry
                if filter_.check_after is not None:
                    entry.check_after = filter_.check_after

                # if ignore is set, we should not cache at all
                if filter_.ignore:
                    evictions.append((entry, f"ignored pattern {filter_.pattern}"))
                    continue

                if filter_.max_age_dt and entry.added_on < filter_.max_age_dt:
                    evictions.append(
                        (
                            entry,
                            "Too old for filter max_age "
                            f"({format_duration(filter_.max_age_seconds)})",
                        )
                    )
                    continue

                if filter_.max_size and filter_size + entry.size > filter_.max_size:
                    evictions.append(
                        (
                            entry,
                            "Would exceed filter max_size "
                            f"({format_size(filter_.max_size_bytes)})",
                        )
                    )
                    continue

                if filter_.max_num and filter_num + 1 > filter_.max_num:
                    evictions.append(
                        (
                            entry,
                            f"Would exceed filter max_num ({filter_.max_num}",
                        )
                    )
                    continue

                # if filter requested version numbers, keep a list of candidates
                # for each individual item with said number
                if filter_.keep_identified_versions:
                    found = matched_ident_version(entry)
                    if found and found.ident not in filter_versioned_entries:
                        filter_versioned_entries[found.ident] = {
                            "num": filter_.keep_identified_versions,
                            "entries": [],
                        }
                    if found:
                        filter_versioned_entries[found.ident]["entries"].append(entry)

                filter_size += entry.size
                filter_num += 1

            # for each identified version, keep only requested number
            if filter_.keep_identified_versions:
                for item in filter_versioned_entries.values():
                    # dont bother if we dont have enough entries to remove any
                    if not item["num"] or len(item["entries"]) <= item["num"]:
                        continue
                    # sort (naturally) by version (from filename)
                    for obsolete in natsorted(
                        item["entries"],
                        key=lambda item: matched_ident_version(
                            item
                        ).version,  # pyright: ignore [reportOptionalMemberAccess]
                    )[: -filter_.keep_identified_versions]:
                        evictions.append(
                            (
                                obsolete,
                                "Version now obsolete (keeping "
                                f"only {filter_.keep_identified_versions})",
                            )
                        )

    total_versioned_entries = {}

    # applying policy-level boundaries
    for entry in sort_for(policy.eviction, entries):
        if policy.check_after is not None:
            entry.check_after = policy.check_after

        if entry.kind == "file" and not is_http(entry.source):
            evictions.append((entry, "Source protocol not cacheable"))
            continue

        if policy.max_age_dt and entry.added_on < policy.max_age_dt:
            evictions.append(
                (
                    entry,
                    f"Too old for {type(policy).__name__} max_age "
                    f"({format_duration(policy.max_age_seconds)})",
                )
            )
            continue

        if policy.max_size and total_size + entry.size > policy.max_size:
            evictions.append(
                (
                    entry,
                    f"Would exceed {type(policy).__name__} max_size "
                    f"({format_size(policy.max_size_bytes)})",
                )
            )
            continue

        if policy.max_num and total_num + 1 > policy.max_num:
            evictions.append(
                (
                    entry,
                    f"Would exceed {type(policy).__name__} max_num "
                    f"({policy.max_num})",
                )
            )
            continue

        # if filter requested version numbers, keep a list of candidates
        # for each individual item with said number
        if policy.keep_identified_versions:
            found = matched_ident_version(entry)
            if found and found.ident not in total_versioned_entries:
                total_versioned_entries[found.ident] = {
                    "num": policy.keep_identified_versions,
                    "entries": [],
                }
            if found:
                total_versioned_entries[found.ident]["entries"].append(entry)

        total_size += entry.size
        total_num += 1

    # for each identified version, keep only requested number
    if policy.keep_identified_versions:
        for item in total_versioned_entries.values():
            # dont bother if we dont have enough entries to remove any
            if not item["num"] or len(item["entries"]) <= item["num"]:
                continue
            # sort (naturally) by version (from filename)
            for obsolete in natsorted(
                item["entries"],
                key=lambda item: matched_ident_version(
                    item
                ).version,  # pyright: ignore [reportOptionalMemberAccess]
            )[: -policy.keep_identified_versions]:
                evictions.append(
                    (
                        obsolete,
                        "Version now obsolete (keeping "
                        f"only {policy.keep_identified_versions})",
                    )
                )

    return evictions


class CacheManager(dict):
    def __init__(self, root: pathlib.Path, policy: MainPolicy):
        # cache_dir
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.ref_date = datetime.datetime.utcnow()

        # policy reference
        self.policy: MainPolicy = policy

        # cached (ahah) list of entries seen in the cache
        self.entries = {}
        # whether cache_dir has been walked-through or not
        self.discovered = False
        # whether cache eviction have been applied or not
        self.applied = False

        # list of not-in-cache yet candidates
        self.candidates = {}
        # whether candidates have been considered (applied) or not
        self.considered = False

    def walk(self):
        """walk through filesystem to discover cache content"""
        if not self.policy.enabled:
            return

        entries = {}
        for fpath in self.root.rglob("*"):
            if not fpath.is_file() or fpath == self.root.joinpath("policy.yaml"):
                continue
            if not file_is_entry(fpath):
                continue
            entries[fpath.relative_to(self.root)] = CacheEntry(fpath)
        self.entries = entries
        self.discovered = True

    @property
    def size(self) -> int:
        """total size of cache"""
        if not self.discovered:
            self.walk()
        return sum([entry.size for entry in self.entries.values()])

    def get(self, item: Item) -> CacheEntry:
        """CacheEntry for Item"""
        if not self.discovered:
            self.walk()
        return self.entries[path_for_item(item)]

    __getitem__ = get

    def in_cache(self, item: Item, *, check_outdacy: bool = False) -> bool:
        """whether there is a CacheEntry for this item in cache"""
        if not self.discovered:
            self.walk()
        present = path_for_item(item) in self.entries.keys()
        if not present:
            return False

        if check_outdacy:
            if self[item].is_outdated:
                self.evict(self[item], "Found outdated")
                return False
        return present

    __contains__ = in_cache  # type: ignore

    def has_candidate(self, item: Item) -> bool:
        return path_for_item(item) in self.candidates.keys()

    def __len__(self):
        if not self.discovered:
            self.walk()
        return len(self.entries)

    def __iter__(self):
        return iter(list(self.entries.values()))

    def introduce(self, item: Item, src_path: pathlib.Path) -> bool:
        """whether item (File or OCI Image) was successfuly introduced to cache"""

        if not self.should_cache(item):
            return False

        relpath = path_for_item(item)
        entry = self.candidates[relpath]
        entry.fpath = self.root.joinpath(entry.fpath)

        metadata = {}
        metadata["added_on"] = entry.added_on.isoformat()
        metadata["last_checked_on"] = entry.last_checked_on.isoformat()
        metadata["last_used_on"] = entry.last_used_on.isoformat()
        metadata["nb_used"] = "1"
        metadata["source"] = f"{entry.kind}:{entry.source}"
        metadata["digest"] = digest_for_item(item)

        try:
            copy_file(src_path, entry.fpath)
        except Exception as exc:
            logger.exception(exc)
            return False
        try:
            attrs = SimpleAttrs(entry.fpath)
            attrs.clear()
            for attr, value in metadata.items():
                attrs[attr] = value
        except Exception as exc:
            logger.exception(exc)
            try:
                entry.fpath.unlink()
            except Exception as exc2:
                logger.exception(exc2)
            return False

        # move from candidates to entries
        self.entries[relpath] = entry
        del self.candidates[relpath]

        return True

    def evict(self, entry: CacheEntry, reason: str) -> bool:
        """whether entry was successfuly evicted from cache"""
        if not self.policy.enabled:
            return False

        logger.debug(f"Evicting {entry}: {reason}")

        try:
            entry.fpath.unlink()
        except Exception as exc:
            logger.exception(exc)
            return False

        del self.entries[entry.fpath.relative_to(self.root)]

        return True

    def add_candidate(self, item: Item):
        if not self.policy.enabled:
            return

        if not self.applied:
            self.apply()

        self.candidates[path_for_item(item)] = CacheCandidate(
            item, added_on=self.ref_date
        )

    def should_cache(self, item: Item) -> bool:
        """whether cache application for entry was accepted"""
        if not self.policy.enabled:
            return False

        # wouldn't make sense to check this without actualy go through screening
        if not self.considered:
            self.apply_candidates()

        return path_for_item(item) in self.candidates.keys()

    def get_eviction_for(
        self, entries: list[CacheEntry]
    ) -> list[tuple[CacheEntry, str]]:
        if not self.policy.enabled:
            return []

        if self.policy.oci_images:
            evictions = get_eviction_for(
                [entry for entry in entries if entry.kind == "image"],
                self.policy.oci_images,
            )
        else:
            evictions = []

        entries = [entry for entry in entries if entry not in evictions]

        if self.policy.files:
            evictions += get_eviction_for(
                [entry for entry in entries if entry.kind == "file"], self.policy.files
            )
        entries = [entry for entry in entries if entry not in evictions]
        evictions += get_eviction_for(entries, self.policy)

        return list(set(evictions))

    def dry_apply(self) -> list[tuple[CacheEntry, str]]:
        """list of (entry, reason) entries from cache that needs eviction"""
        return self.get_eviction_for(list(self.entries.values()))

    def apply(self) -> list[tuple[CacheEntry, str, bool]]:
        """(entry, reason, success) list of evictions for applying policy"""
        if not self.policy.enabled:
            return []

        evicted = []
        for entry, reason in self.dry_apply():
            evicted.append((entry, reason, self.evict(entry, reason)))

        self.applied = True

        return evicted

    def evict_outdated(self) -> list[tuple[CacheEntry, str, bool]]:
        """Check all Cache entries for remote updates"""
        evicted = []
        reason = "outdated"
        for entry in list(self.entries.values()):
            if entry.is_outdated_if():
                evicted.append((entry, reason, self.evict(entry, reason)))
        return evicted

    def apply_candidates(self):
        """run algo through entries + candidates, removing candidates that wont stay"""

        for entry, reason in self.get_eviction_for(
            list(self.entries.values()) + list(self.candidates.values())
        ):
            if entry in self.entries.values():
                self.evict(entry, f"{reason} [apply-candidates]")
            else:
                try:
                    del self.candidates[entry.fpath]
                # can be listed twice for eviction (matching several reasons)
                except KeyError:
                    pass

        self.considered = True

    def print(self, *, with_evictions: bool = False):
        """print content of cache"""
        if not self.discovered:
            self.walk()

        logger.message("")

        if not self.entries:
            logger.message("Cache is empty.")
        else:
            sorted_e = sorted(self.entries.values(), key=lambda e: e.added_on)
            oldest, newest = sorted_e[0], sorted_e[-1]
            # oldest, newest = datetime.datetime.utcnow(), datetime.datetime.utcnow()

            logger.table(
                headers=["Size", "Entries", "Oldest", "Newest"],
                data=[
                    [
                        (format_size(self.size),),
                        (str(len(self)),),
                        (format_dt(oldest.added_on),),
                        (format_dt(newest.added_on),),
                    ],
                ],
            )
            logger.message("")

            evictions = [e for e, _ in self.dry_apply()] if with_evictions else []

            def style_for(entry: CacheEntry):
                if not with_evictions or entry not in evictions:
                    return logger.ui.reset
                return logger.ui.red

            logger.table(
                headers=["Size", "Added On", "Nb. Used", "Last Used", "Source", "Path"],
                data=[
                    [
                        (
                            style_for(entry),
                            format_size(entry.size),
                        ),
                        (
                            style_for(entry),
                            format_dt(entry.added_on),
                        ),
                        (
                            style_for(entry),
                            str(entry.nb_used),
                        ),
                        (
                            style_for(entry),
                            format_dt(entry.last_used_on),
                        ),
                        (
                            style_for(entry),
                            entry.source,
                        ),
                        (
                            style_for(entry),
                            entry.fpath.relative_to(self.root),
                        ),
                    ]
                    for entry in sorted(self, key=lambda e: e.added_on)
                ],
            )
        logger.message("")
