"""Per-project configuration: `cartograph.toml` at the repo root.

Nothing about a mapped repository is hardcoded into cartograph. The excluded
directories, the language, the granularity, and above all the ROSTER are
properties of the project being looked at, not of the tool.

That matters more than it sounds. The first version of this tool had a trading
codebase's path baked into the UI and its defect history baked into the wiring
analyser's defaults. It worked beautifully on one repository, which is the
failure mode of every internal tool ever written: it encodes the first example
as though it were the general case.

An absent `cartograph.toml` is fine and means "use the defaults" — a tool that
requires configuration before it shows you anything does not get used.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from .roles import RoleRegistry

CONFIG_NAME = "cartograph.toml"

# Directories that are code but are rarely the architecture you want to see.
# Overridable, because "tests are not the architecture" is true of a service and
# false of a test framework.
DEFAULT_EXCLUDE: tuple[str, ...] = ("tests", "test", "spec", "__tests__")


@dataclass
class Channel:
    """A named deliverable path through the system.

    Declared here and NOT yet rendered. Flow views are deliberately unbuilt
    until the general abstraction settles — building them against one codebase's
    three channels is how a general tool quietly becomes a bespoke one.
    """

    name: str
    description: str = ""
    entry_points: tuple[str, ...] = ()
    sinks: tuple[str, ...] = ()


@dataclass
class ProjectConfig:
    root: Path
    language: str = "auto"
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE
    resolution: float = 0.6
    min_members: int = 3
    roles: RoleRegistry = field(default_factory=RoleRegistry.default)
    channels: tuple[Channel, ...] = ()
    source: Optional[Path] = None          # which file this came from, if any

    @property
    def configured(self) -> bool:
        return self.source is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "language": self.language,
            "exclude": list(self.exclude),
            "resolution": self.resolution,
            "min_members": self.min_members,
            "channels": [asdict(c) for c in self.channels],
            "roles": self.roles.to_dict(),
            "source": str(self.source) if self.source else None,
            "configured": self.configured,
        }


def load(root: str | Path) -> ProjectConfig:
    """Read `cartograph.toml` from `root`, or return defaults.

    A malformed config RAISES rather than falling back to defaults. Silently
    ignoring a config someone wrote is how a revoked capability comes back: they
    edit the roster, the file fails to parse, the default roster loads, and the
    tool behaves exactly as if their edit said nothing.
    """
    root = Path(root).expanduser().resolve()
    path = root / CONFIG_NAME
    if not path.is_file():
        return ProjectConfig(root=root)

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"{path} is not valid TOML: {exc}. Refusing to fall back to defaults — "
            f"a config that fails to parse would silently restore anything you removed."
        ) from None

    proj = data.get("project", {})
    view = data.get("view", {})

    channels = tuple(
        Channel(
            name=name,
            description=spec.get("description", ""),
            entry_points=tuple(spec.get("entry_points", ())),
            sinks=tuple(spec.get("sinks", ())),
        )
        for name, spec in sorted(data.get("channels", {}).items())
    )

    return ProjectConfig(
        root=root,
        language=proj.get("language", "auto"),
        exclude=tuple(proj.get("exclude", DEFAULT_EXCLUDE)),
        resolution=float(view.get("resolution", 0.6)),
        min_members=int(view.get("min_members", 3)),
        roles=RoleRegistry.from_config(data.get("roles")),
        channels=channels,
        source=path,
    )


EXAMPLE = '''\
# cartograph.toml — drop this at the root of any repository you map.
# Every field is optional; an absent file means defaults.

[project]
language = "auto"                    # or "python"
exclude  = ["tests", "fixtures"]     # not the architecture you want to see

[view]
resolution  = 0.6                    # higher = more, smaller blocks
min_members = 3                      # blocks smaller than this fold together

# The roster. Declaring ANY role replaces the default roster entirely —
# a partial merge would let you believe you removed a capability that a
# default quietly restored.
[roles.socrates]
purpose      = "Ask what the thing leaves behind, before it is built."
may          = ["question"]
must_not     = ["build", "review", "merge", "deploy", "decide"]
escalates_to = "foreman"
answers_with = "questions naming a mechanism that would answer them"

[roles.engineer]
purpose      = "Turn a spec into tested code."
may          = ["investigate", "build"]
must_not     = ["review", "merge", "decide"]
escalates_to = "reviewer"

[roles.reviewer]
purpose      = "Judge work this seat did not produce."
may          = ["investigate", "review"]
must_not     = ["build", "merge", "decide"]
escalates_to = "foreman"

[roles.foreman]
purpose      = "Decide and merge; keep the human oriented."
may          = ["investigate", "review", "merge", "decide"]
must_not     = ["build", "deploy"]
escalates_to = "operator"

[roles.operator]
purpose = "The human. The only seat that gives orders."
may     = ["question", "investigate", "review", "merge", "deploy", "decide"]

# Named deliverable paths. Declared now, rendered later — flow views are
# deliberately unbuilt until the general abstraction settles.
[channels.production]
description  = "what actually runs in production"
entry_points = ["cron/"]
sinks        = ["data/"]
'''
