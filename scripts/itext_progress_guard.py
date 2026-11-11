"""Bound only the two pinned isolated-entity loops; retain native graph semantics."""
import ast
import inspect
import textwrap


def bounded_graph_class(original, limit):
    if type(limit) is not int or limit < 1:
        raise ValueError("No-progress limit must be a positive integer")
    tree = ast.parse(textwrap.dedent(inspect.getsource(original.build_graph)))

    class BoundLoops(ast.NodeTransformer):
        count = 0

        def visit_While(self, node):
            self.generic_visit(node)
            if not isinstance(node.test, ast.Name) or node.test.id != "isolated_entities":
                return node
            self.count += 1
            section = "0" if self.count == 1 else "i"
            initial = ast.parse("_stall_passes = 0\n_best_isolated = len(isolated_entities)").body
            node.body.extend(ast.parse(f"""
if len(isolated_entities) < _best_isolated:
    _best_isolated = len(isolated_entities)
    _stall_passes = 0
else:
    _stall_passes += 1
if isolated_entities and _stall_passes >= {limit}:
    self._no_progress_events.append({{
        'section_index': {section},
        'consecutive_no_progress_passes': _stall_passes,
        'retained_isolated_names': [e['name'] for e in isolated_entities],
        'action': 'retain_entities_and_relations_then_continue_next_section'
    }})
    break
""").body)
            return initial + [node]

    transform = BoundLoops()
    transformed = transform.visit(tree)
    if transform.count != 2:
        raise ValueError("Expected exactly the two pinned isolated-entity loops")
    ast.fix_missing_locations(transformed)
    namespace = dict(original.build_graph.__globals__)
    exec(compile(transformed, "<itext2kg-explicit-progress-guard>", "exec"), namespace)
    return type("ProgressBoundedIText2KG", (original,), {"build_graph": namespace["build_graph"]})
