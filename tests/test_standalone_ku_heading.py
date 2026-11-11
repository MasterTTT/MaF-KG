import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_knowledge_units import heading_section


def test_standalone_section_is_not_mistaken_for_subheading():
    n = {"evidence": [{"heading_path": ["第三节 函数的极限", "一、函数极限的定义", "1. 有限值"]}]}
    assert heading_section(n, "第一章") == "第三节 函数的极限"


def test_chapter_section_and_subheading_have_same_result():
    n = {"evidence": [{"heading_path": ["第一章 函数与极限", "第三节 函数的极限", "一、定义"]}]}
    assert heading_section(n, "第一章") == "第三节 函数的极限"


def test_only_subheadings_do_not_create_phantom_sections():
    assert heading_section({"evidence": [{"heading_path": ["一、定义", "1. 小标题"]}]}, "第一章") is None
