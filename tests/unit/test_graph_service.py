from __future__ import annotations

from groundworkers.services.graph import GraphService


class HierarchyAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | tuple[int, ...]]] = []
        self._parents = {
            10: (20, 30),
            20: (40,),
            30: (40, 50),
            40: (),
            50: (),
        }
        self._views = {
            concept_id: {
                "concept_id": concept_id,
                "concept_name": f"Concept {concept_id}",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "standard_concept": True,
            }
            for concept_id in (10, 20, 30, 40, 50)
        }

    def get_concept(self, concept_id: int):
        self.calls.append(("get_concept", concept_id))
        return self._views.get(concept_id)

    def parents(self, concept_id: int) -> tuple[int, ...]:
        self.calls.append(("parents", concept_id))
        return self._parents.get(concept_id, ())

    def concept_views(self, concept_ids: tuple[int, ...]):
        self.calls.append(("concept_views", concept_ids))
        return {concept_id: self._views[concept_id] for concept_id in concept_ids}


def test_get_ancestors_returns_shallowest_depths_in_monotonic_order() -> None:
    adapter = HierarchyAdapter()
    service = GraphService(adapter)

    ancestors = service.get_ancestors(10, max_depth=5)

    assert [item["concept_id"] for item in ancestors] == [20, 30, 40, 50]
    assert [item["depth"] for item in ancestors] == [1, 1, 2, 2]
    assert [item["depth"] for item in ancestors] == sorted(item["depth"] for item in ancestors)
    assert ("concept_views", (20, 30, 40, 50)) in adapter.calls


def test_get_ancestors_respects_max_depth() -> None:
    adapter = HierarchyAdapter()
    service = GraphService(adapter)

    ancestors = service.get_ancestors(10, max_depth=1)

    assert [item["concept_id"] for item in ancestors] == [20, 30]
    assert [item["depth"] for item in ancestors] == [1, 1]
