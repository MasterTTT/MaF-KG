# -*- coding: utf-8 -*-
"""Frozen-config v2: tolerate blank separator lines as evidence endpoints.

The merged plan's core-evidence union drops blank separator lines, so a cited span
can end (or start) on a blank line inside the chunk span. v1 rejected that as
"not visible", failing runs for a formatting reason rather than a method reason.
v2 accepts an endpoint that is either visible in the chunk text/context, or blank
and inside the chunk's own span; an unseen *content* line is still rejected, and
the source-structure metadata requirement is satisfied via the nearest content
line inside the span when the start endpoint is blank.
"""
import py_compile, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
P = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09\snapshot\agents\Extractor.py')
src = P.read_text(encoding='utf-8')
if 'FROZEN-CONFIG-v2' in src:
    print('already patched (v2 marker present)')
    raise SystemExit(0)

old_check = '''        if start not in visible_line_numbers or end not in visible_line_numbers or end < start:
            raise ValueError(
                f"Evidence endpoints are not visible source lines in {chunk.id}: L{start}-L{end}"
            )
'''
new_check = '''        # FROZEN-CONFIG-v2 (merged three-section experiment, user-approved 2026-10-09):
        # the core-evidence union drops blank separator lines, so a cited span may
        # legitimately start or end on a blank line inside the chunk span. Accept
        # such endpoints; an unseen content line is still rejected.
        def _endpoint_ok(line_no: int) -> bool:
            if line_no in visible_line_numbers:
                return True
            if chunk.start_line <= line_no <= chunk.end_line and 0 <= line_no - 1 < len(raw_lines):
                return not raw_lines[line_no - 1].strip()
            return False

        if not _endpoint_ok(start) or not _endpoint_ok(end) or end < start:
            raise ValueError(
                f"Evidence endpoints are not visible source lines in {chunk.id}: L{start}-L{end}"
            )
'''
if old_check not in src:
    raise SystemExit('endpoint-check block not found; aborting')
src = src.replace(old_check, new_check, 1)

old_ctx = '''        source_context = self.source_context_by_line.get(start)
        if source_context is None:
            raise ValueError(f"Evidence start line has no source structure metadata in {chunk.id}: L{start}")
'''
new_ctx = '''        source_context = self.source_context_by_line.get(start)
        if source_context is None:
            # FROZEN-CONFIG-v2: a blank start endpoint carries no structure metadata;
            # borrow it from the nearest content line inside the cited span.
            for probe in range(start + 1, end + 1):
                candidate = self.source_context_by_line.get(probe)
                if candidate is not None:
                    source_context = candidate
                    self._log(
                        f"[Extractor] evidence start L{start} is blank in {chunk.id}; "
                        f"source structure borrowed from L{probe}"
                    )
                    break
        if source_context is None:
            raise ValueError(f"Evidence start line has no source structure metadata in {chunk.id}: L{start}")
'''
if old_ctx not in src:
    raise SystemExit('source-context block not found; aborting')
src = src.replace(old_ctx, new_ctx, 1)

P.write_text(src, encoding='utf-8')
py_compile.compile(str(P), doraise=True)
print('patched Extractor.py (FROZEN-CONFIG-v2) and compiled OK')
print('marker count:', src.count('FROZEN-CONFIG-v2'))
