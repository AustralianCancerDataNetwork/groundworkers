"""SNOMED defining attributes in the bundled predicate mapping are ATTRIBUTE.

Procedure and finding sites were classed Association/Diagnostic while their
sibling attributes (Has method, Has dir device, Has asso morph) were
Attribute, so concept_neighbors(predicate_kinds=["ATTRIBUTE"]) missed them.
"""

from __future__ import annotations

import csv
from importlib.resources import files

DEFINING_ATTRIBUTES = {
    "Has dir proc site": ("Attribute", "Anatomical"),
    "Dir proc site of": ("Attribute", "Anatomical"),
    "Has indir proc site": ("Attribute", "Anatomical"),
    "Indir proc site of": ("Attribute", "Anatomical"),
    "Has proc site": ("Attribute", "Anatomical"),
    "Proc site of": ("Attribute", "Anatomical"),
    "Has finding site": ("Attribute", "Anatomical"),
    "Finding site of": ("Attribute", "Anatomical"),
    "Has specimen topo": ("Attribute", "Anatomical"),
    "Specimen topo of": ("Attribute", "Anatomical"),
    "Has proc context": ("Attribute", "Context"),
    "Proc context of": ("Attribute", "Context"),
    "Has occurrence": ("Attribute", "Temporal"),
    "Occurrence of": ("Attribute", "Temporal"),
    "Using subst": ("Attribute", "Procedural"),
    "Subst used by": ("Attribute", "Procedural"),
    "Has dir subst": ("Attribute", "Procedural"),
    "Dir subst of": ("Attribute", "Procedural"),
    "Has method": ("Attribute", "Procedural"),
    "Has dir device": ("Attribute", "Procedural"),
    "Has access": ("Attribute", "Procedural"),
    "Using acc device": ("Attribute", "Procedural"),
}


def test_bundled_mapping_classes_snomed_defining_attributes_as_attributes() -> None:
    mapping_file = files("groundworkers") / "config" / "predicate_mapping.csv"
    with mapping_file.open(newline="") as handle:
        classes = {row["r_id"]: (row["class"], row["subclass"]) for row in csv.DictReader(handle)}

    assert {r_id: classes.get(r_id) for r_id in DEFINING_ATTRIBUTES} == DEFINING_ATTRIBUTES
