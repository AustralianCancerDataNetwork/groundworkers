"""Abstract base for source system profiles.

A source system profile contains only platform-structural knowledge —
facts about how the source platform encodes data, regardless of which
institution or deployment is using it.

Content-level knowledge (instrument libraries, label skip patterns,
source vocab string mappings) belongs in the localisation namespace,
not here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SourceSystemProfile(ABC):
    """Platform-structural knowledge for a specific source system."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier, e.g. 'redcap'. Used as detected_source_system value."""

    @abstractmethod
    def detect(self, headers: frozenset[str]) -> bool:
        """Return True if these normalised column headers match this platform's fingerprint.

        Headers are passed as a frozenset of lower-cased, stripped strings.
        Detection must be conservative — false negatives are preferable to
        false positives, since a wrong profile is worse than no profile.
        """

    @abstractmethod
    def structural_skip_field_types(self) -> frozenset[str]:
        """Field type values this platform defines as never independently groundable.

        These are platform facts, not deployment choices. A REDCap 'calc' field
        is always a computed expression regardless of what any site puts in it.
        Values should match what appears in the source data's field_type column,
        lower-cased.
        """

    @abstractmethod
    def packed_value_column_hint(self) -> str | None:
        """The column that holds packed choice encodings, if the platform has one.

        Returns the original-case column name as it appears in the source data,
        or None if this platform does not use packed value columns.
        Used by the ingester router to signal that value expansion is needed.
        """
