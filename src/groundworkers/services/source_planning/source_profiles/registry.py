"""Registry for source system profiles."""

from __future__ import annotations

from groundworkers.services.source_planning.source_profiles.base import (
    SourceSystemProfile,
)
from groundworkers.services.source_planning.source_profiles.redcap import (
    REDCapSourceProfile,
)


def _default_profiles() -> list[SourceSystemProfile]:
    return [REDCapSourceProfile()]


class SourceProfileRegistry:
    """Matches a set of column headers against registered source system profiles.

    Profiles are evaluated in registration order; the first match is returned.
    For the built-in registry this means more specific profiles should be
    registered before more general ones.
    """

    def __init__(self, profiles: list[SourceSystemProfile] | None = None) -> None:
        self._profiles: list[SourceSystemProfile] = (
            list(profiles) if profiles is not None else _default_profiles()
        )

    def match(self, headers: frozenset[str]) -> SourceSystemProfile | None:
        """Return the first profile whose fingerprint matches these headers, or None."""
        normalised = frozenset(h.lower().strip() for h in headers)
        for profile in self._profiles:
            if profile.detect(normalised):
                return profile
        return None

    def register(self, profile: SourceSystemProfile) -> None:
        """Add a profile to the end of the evaluation order."""
        self._profiles.append(profile)

    @property
    def registered(self) -> list[str]:
        return [p.name for p in self._profiles]
