from __future__ import annotations

import datetime
import re
from collections.abc import Iterable
from dataclasses import MISSING, dataclass, field

from typeguard import typechecked

try:
    from yaml import CSafeLoader as SafeLoader
    from yaml import load as yaml_load
except ImportError:
    # we don't NEED cython ext but it's faster so use it if avail.
    from yaml import SafeLoader
    from yaml import load as yaml_load

from offspot_config.utils.misc import (
    is_dict,
    is_list_of_dict,
    parse_duration,
    parse_size,
)


@dataclass(init=False)
class Eviction:
    oldest: str = "oldest"
    newest: str = "newest"
    largest: str = "largest"
    smallest: str = "smallest"
    lru: str = "lru"

    @classmethod
    def default(cls) -> str:
        return cls.lru

    @classmethod
    def get_all(cls) -> Iterable:
        return tuple(cls.__dataclass_fields__.keys())


class Policy:
    ...


@dataclass(kw_only=True)
class CommonParamsMixin(Policy):
    max_size: int | str | None = None
    max_age: int | str | None = None
    max_num: int | None = None
    eviction: str

    @property
    def checkable_fields(self):
        return ("max_size", "max_age", "max_num")

    @property
    def max_age_dt(self) -> datetime.datetime | None:
        """datetime that is max_age in the past"""
        if not self.max_age:
            return None
        return datetime.datetime.utcnow() - datetime.timedelta(seconds=self.max_age)

    def parse_max_size(self):
        if self.max_size is None or (
            isinstance(self.max_size, int) and self.max_size > 0
        ):
            return
        elif self.max_size == 0:
            self.max_size = None
            return
        elif isinstance(self.max_size, int):
            raise ValueError(
                f"Invalid negative value `{self.max_size}` "
                f"for {type(self).__name__}.max_size"
            )
        try:
            self.max_size = parse_size(self.max_size)
        except Exception as exc:
            raise ValueError(
                f"Unable to parse `{self.max_size}` into size "
                f"for {type(self).__name__}.max_size ({exc})"
            ) from exc

    def parse_max_age(self):
        if self.max_age is None or (isinstance(self.max_age, int) and self.max_age > 0):
            return
        elif self.max_age == 0:
            self.max_age = None
            return
        elif isinstance(self.max_age, int):
            raise ValueError(
                f"Invalid negative value `{self.max_age}` "
                f"for {type(self).__name__}.max_age"
            )
        try:
            self.max_age = parse_duration(self.max_age)
        except Exception as exc:
            raise ValueError(
                f"Unable to parse `{self.max_age}` into duration "
                f"for {type(self).__name__}.max_age ({exc})"
            ) from exc

    def enforce_eviction(self):
        if self.eviction not in Eviction.get_all():
            raise ValueError(
                f"Unexpected value `{self.eviction}` "
                f"for {type(self).__name__}.eviction."
                f"Accepts: {', '.join(Eviction.get_all())}"
            )

    def __post_init__(self):
        if not self.eviction:
            self.eviction = Eviction.default()
        self.enforce_eviction()
        self.parse_max_size()
        self.parse_max_age()


@typechecked
@dataclass
class SubPolicyFilter(CommonParamsMixin):
    pattern: str
    ignore: bool | None = False

    def match(self, value: str):
        return re.match(self.pattern, value, re.IGNORECASE)


@dataclass()
class SubPolicy(CommonParamsMixin):
    enabled: bool | None = True
    filters: list[SubPolicyFilter] = field(default_factory=list)

    def __post_init__(self, *args, **kwargs):
        super().__post_init__(*args, **kwargs)
        self.check()

    def check(self):
        for idx, filter_ in enumerate(self.filters):
            for value in self.checkable_fields:
                if (
                    getattr(filter_, value)
                    and getattr(self, value)
                    and getattr(filter_, value) > getattr(self, value)
                ):
                    raise ValueError(
                        f"{type(filter_).__name__}[{idx}].{value} "
                        f"({getattr(filter_, value)}) "
                        f"exceeds {type(self).__name__}.{value} "
                        f"({getattr(self, value)})"
                    )


@typechecked
class OCIImagePolicy(SubPolicy):
    ...


@typechecked
class FilesPolicy(SubPolicy):
    ...


@typechecked
@dataclass
class MainPolicy(CommonParamsMixin):
    oci_images: OCIImagePolicy | None = field(default_factory=OCIImagePolicy)
    files: FilesPolicy | None = field(default_factory=FilesPolicy)
    enabled: bool | None = True

    def __post_init__(self, *args, **kwargs):
        super().__post_init__(*args, **kwargs)
        self.check()

    @classmethod
    def defaults(cls):
        """A Policy with default values"""
        return cls(enabled=True, max_size="10GiB", eviction=Eviction.lru)

    @classmethod
    def disabled(cls):
        """A cache-less Policy"""
        return cls(enabled=False)

    @classmethod
    def sub_names(cls):
        """names of fields referencing SubPolicies"""
        return [
            name
            for name, field in cls.__dataclass_fields__.items()
            if field.default_factory != MISSING
            and SubPolicy in field.default_factory.mro()
        ]

    @classmethod
    def read_from(cls, text: str):
        """Policy from a YAML string config"""

        # parse YAML (Dict) will be our input to Policy()
        payload = yaml_load(text, Loader=SafeLoader)

        # Subpolicies have subclasses for human-friendlyness
        _sub_policy_map = {"oci_images": OCIImagePolicy, "files": FilesPolicy}

        # build SubPolicies first (args of the main Policy)
        for name in cls.sub_names():
            sub_policy_cls = _sub_policy_map.get(name, SubPolicy)

            # remove he key from payload ; we'll replace it with actual SubPolocy
            subload = payload.pop(name, None)

            # fail early if SubPolicy is not well formatted
            if not is_dict(subload, accepts_none=True):
                raise ValueError(f"Unexpected type for Policy.{name}: {type(subload)}")

            # remove filters from (sub)payload ; we'll replace with actual FilterPolicy
            filters = subload.pop("filters", [])
            if not is_list_of_dict(filters):
                raise ValueError(
                    f"Unexpected type for Policy.{name}.filters: {type(filters)}"
                )

            # create FilterPolicies (will fail in case of errors)
            subload["filters"] = [SubPolicyFilter(**subfilter) for subfilter in filters]

            # now create the SubPolicy with our filters, storing on main payload
            payload[name] = sub_policy_cls(**subload)

        # ready to create the actual main Policy
        return cls(**payload)

    def check(self):
        for subname in self.sub_names():
            sub_policy = getattr(self, subname)
            for value in self.checkable_fields:
                if (
                    getattr(sub_policy, value)
                    and getattr(self, value)
                    and getattr(sub_policy, value) > getattr(self, value)
                ):
                    raise ValueError(
                        f"{type(sub_policy).__name__}.{value} "
                        f"({getattr(sub_policy, value)}) "
                        f"exceeds {type(self).__name__}.{value} "
                        f"({getattr(self, value)})"
                    )
