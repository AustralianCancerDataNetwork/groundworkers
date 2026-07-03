"""REDCap v1 source system profile.

Detection fingerprint: the three column names below are present in every
REDCap data dictionary export regardless of institution or version.
All three are required — any two could appear in a non-REDCap CSV by chance.

Structural skip types: 'calc' and 'descriptive' are REDCap platform values
that are never independently groundable. 'calc' fields hold computed
expressions; 'descriptive' fields are static display text only.

Packed value column: choices/calculations share one column in REDCap exports.
Only radio, checkbox, and dropdown rows have meaningful content there.
"""

from __future__ import annotations

from groundworkers.services.source_planning.source_profiles.base import SourceSystemProfile

_FINGERPRINT: frozenset[str] = frozenset({
    "field type",
    "choices, calculations, or slider labels",
    "branching logic (show field only if...)",
})

_PACKED_VALUE_COLUMN = "Choices, Calculations, OR Slider Labels"


class REDCapSourceProfile(SourceSystemProfile):

    @property
    def name(self) -> str:
        return "redcap"

    def detect(self, headers: frozenset[str]) -> bool:
        return _FINGERPRINT <= headers

    def structural_skip_field_types(self) -> frozenset[str]:
        return frozenset({"calc", "descriptive"})

    def packed_value_column_hint(self) -> str | None:
        return _PACKED_VALUE_COLUMN
