import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from itext_progress_guard import bounded_graph_class


class FixtureGraph:
    def build_graph(self, sections):
        entities = [{"name": "A"}, {"name": "B"}]
        isolated_entities = list(entities)
        relationships = []
        while isolated_entities:
            self.calls += 1
            if self.progress and self.calls == 2:
                isolated_entities = isolated_entities[:-1]
        for i in range(1, len(sections)):
            self.visited.append(i)
            isolated_entities = [{"name": "C"}]
            while isolated_entities:
                self.calls += 1
        return entities, relationships


def test_stall_preserves_nodes_does_not_invent_edges_and_continues():
    graph = bounded_graph_class(FixtureGraph, 3)()
    graph.calls, graph.progress, graph.visited, graph._no_progress_events = 0, False, [], []
    entities, edges = graph.build_graph(["first", "second"])
    assert entities == [{"name": "A"}, {"name": "B"}] and edges == []
    assert graph.calls == 6
    assert graph.visited == [1]
    assert [e["section_index"] for e in graph._no_progress_events] == [0, 1]
    assert graph._no_progress_events[0]["retained_isolated_names"] == ["A", "B"]


def test_progress_resets_stall_counter():
    graph = bounded_graph_class(FixtureGraph, 3)()
    graph.calls, graph.progress, graph.visited, graph._no_progress_events = 0, True, [], []
    graph.build_graph(["first"])
    assert graph.calls == 5


def test_invalid_limit_rejected():
    with pytest.raises(ValueError):
        bounded_graph_class(FixtureGraph, 0)
