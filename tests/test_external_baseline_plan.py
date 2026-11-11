import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_external_baselines as runner


def test_frozen_provider_and_no_optional_enrichment():
    assert runner.CONFIG["provider"] == "opencode-go"
    assert runner.CONFIG["model"] == "deepseek-v4-flash"
    assert runner.CONFIG["reasoning_effort"] == "low"
    assert runner.CONFIG["max_retries"] == 0
    assert runner.CONFIG["chunk_strategy"] == "teaching-units"
    assert "involves" not in runner.TARGET_SCHEMA
    assert "A-N" not in str(runner.TARGET_SCHEMA)


def test_domain_scope_not_test_answers():
    assert "example-local" in runner.DOMAIN
    assert "conditions" in runner.DOMAIN
    assert "improper" not in runner.DOMAIN.lower()


def test_native_packing_keeps_every_original_line():
    text = "1: title\n2: aa\n3: bbbb\n4: ccccc\n5: tail"
    sections = runner.pack_native_sections(text, target_chars=12)
    assert runner.baseline.numbered_lines("\n".join(sections)) == runner.baseline.numbered_lines(text)
    assert len(sections) >= 2
