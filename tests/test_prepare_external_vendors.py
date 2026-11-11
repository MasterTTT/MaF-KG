import difflib
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_external_vendors as vendors  # noqa: E402


def write_zip(path, root, entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, (mode, raw) in entries.items():
            name = f"{root}/{relative}"
            info = zipfile.ZipInfo(name)
            # Preserve hostile raw names instead of Windows ZipInfo normalization.
            info.filename = info.orig_filename = name
            info.create_system = 3
            info.external_attr = (int(mode, 8) << 16)
            archive.writestr(info, raw)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_hash(entries, package, *, crlf=False):
    digest = hashlib.sha256()
    for name in sorted(entries):
        if not name.startswith(package + "/") or not name.endswith(".py"):
            continue
        raw = entries[name][1]
        if crlf:
            raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        digest.update(name[len(package) + 1:].encode("utf-8") + b"\0" + raw + b"\0")
    return digest.hexdigest()


@pytest.fixture
def offline_setup(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("Git is required for the source-patch integration fixture")
    cache = tmp_path / "archives"
    cache.mkdir()
    before = b"TITLE = 'raw'\nENABLED = True\n"
    after = b"TITLE = 'adapted'\nENABLED = True\n"
    edc = {
        "edc/__init__.py": ("100644", b""),
        "edc/extract.py": ("100644", before),
        "prompt_templates/oie_template.txt": ("100644", b"\xef\xbb\xbf" + "反常积分。\n".encode("utf-8")),
        "run.sh": ("100755", b"#!/bin/sh\n"),
    }
    itext = {
        "itext2kg/__init__.py": ("100644", b""),
        "itext2kg/graph_integration/itext2kg.py": ("100644", "TITLE = '原始源码'\n".encode("utf-8")),
        "requirements.txt": ("100644", b"numpy\n"),
    }
    patch_text = "".join(difflib.unified_diff(before.decode("utf-8").splitlines(keepends=True),
                                           after.decode("utf-8").splitlines(keepends=True),
                                           fromfile="a/edc/extract.py", tofile="b/edc/extract.py"))
    patch = tmp_path / "fixture.patch"
    patch.write_text(patch_text, encoding="utf-8", newline="\n")
    adapted = dict(edc, **{"edc/extract.py": ("100644", after)})
    specs = []
    for pinned, entries in zip(vendors.SPECS, (edc, itext)):
        archive_hash = write_zip(cache / pinned.archive_name, pinned.archive_root, entries)
        specs.append(replace(pinned, archive_sha256=archive_hash, tree=vendors.git_tree_sha1(entries),
                             source_sha256=package_hash(adapted if pinned.name == "edc" else entries,
                                                        pinned.name, crlf=pinned.name == "edc")))
    monkeypatch.setattr(vendors, "SPECS", tuple(specs))
    monkeypatch.setattr(vendors, "PATCH_PATH", patch)
    monkeypatch.setattr(vendors, "PATCH_SHA256", hashlib.sha256(patch.read_bytes()).hexdigest())

    def forbidden_download(*args, **kwargs):
        pytest.fail("Offline setup attempted a network download")

    monkeypatch.setattr(vendors, "_download_archive", forbidden_download)
    return cache, specs, edc, itext


def test_offline_setup_rebuilds_both_layouts_and_isolates_git(tmp_path, monkeypatch, offline_setup):
    cache, specs, edc, itext = offline_setup
    protected = tmp_path / "protected"
    protected.mkdir()
    sentinel = protected / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setenv("GIT_WORK_TREE", str(protected))
    monkeypatch.setenv("GIT_DIR", str(protected / "missing.git"))
    target = tmp_path / "new-vendors"

    locks = vendors.prepare(target, archives_dir=cache)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (protected / "missing.git").exists()
    assert (target / "edc/edc/extract.py").read_bytes() == b"TITLE = 'adapted'\r\nENABLED = True\r\n"
    assert (target / "edc/prompt_templates/oie_template.txt").read_bytes() == edc["prompt_templates/oie_template.txt"][1].replace(b"\n", b"\r\n")
    for spec in specs:
        root = target / spec.name
        package = root / "edc" if spec.name == "edc" else root / "snapshot" / spec.archive_root / "itext2kg"
        assert vendors.source_tree_sha256(package) == spec.source_sha256
        assert (root / spec.archive_name).read_bytes() == (cache / spec.archive_name).read_bytes()
        assert json.loads((root / "UPSTREAM_LOCK.json").read_text(encoding="utf-8")) == locks[spec.name]
        assert (root / "requirements-adapter.txt").is_file()
    itext_source = target / "itext2kg/snapshot" / specs[1].archive_root
    assert (itext_source / "itext2kg/graph_integration/itext2kg.py").read_bytes() == itext["itext2kg/graph_integration/itext2kg.py"][1]


@pytest.mark.parametrize("existing", ["file", "empty-directory", "populated-directory"])
def test_existing_target_refused_before_any_source_access(tmp_path, monkeypatch, existing):
    target = tmp_path / "existing"
    if existing == "file":
        target.write_bytes(b"keep")
    else:
        target.mkdir()
        if existing == "populated-directory":
            (target / "sentinel").write_bytes(b"keep")
    monkeypatch.setattr(vendors, "_verified_patch", lambda: pytest.fail("Existing target must fail first"))
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        vendors.prepare(target)
    assert target.exists()
    if existing == "file":
        assert target.read_bytes() == b"keep"
    elif existing == "populated-directory":
        assert (target / "sentinel").read_bytes() == b"keep"


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("failure", ["zip", "tree", "source"])
def test_verification_failure_does_not_publish_target(tmp_path, monkeypatch, offline_setup, index, failure):
    cache, specs, *_ = offline_setup
    if failure == "zip":
        archive = cache / specs[index].archive_name
        archive.write_bytes(archive.read_bytes() + b"corruption")
        message = "ZIP SHA-256 mismatch"
    else:
        specs[index] = replace(specs[index], **{("tree" if failure == "tree" else "source_sha256"): "0" * (40 if failure == "tree" else 64)})
        monkeypatch.setattr(vendors, "SPECS", tuple(specs))
        message = "Git tree mismatch" if failure == "tree" else "Python source SHA-256 mismatch"
    target = tmp_path / "failed-output"
    with pytest.raises(ValueError, match=message):
        vendors.prepare(target, archives_dir=cache)
    assert not target.exists()


def test_patch_integrity_survives_checkout_crlf_and_rejects_edits(tmp_path, monkeypatch):
    raw = vendors.PATCH_PATH.read_bytes().replace(b"\r\n", b"\n")
    checkout = tmp_path / "checkout.patch"
    checkout.write_bytes(raw.replace(b"\n", b"\r\n"))
    monkeypatch.setattr(vendors, "PATCH_PATH", checkout)
    assert vendors._verified_patch() == raw
    checkout.write_bytes(raw + b"unexpected\n")
    with pytest.raises(ValueError, match="patch SHA-256 mismatch"):
        vendors._verified_patch()


@pytest.mark.parametrize("relative", [
    "../outside.py", "/absolute.py", "C:/outside.py", "folder\\outside.py",
    "folder/../../outside.py", "file.py:stream", "NUL.py", "trailing./file.py", ".git/config",
])
def test_unsafe_zip_paths_rejected_before_extraction(tmp_path, relative):
    spec = vendors.SPECS[0]
    archive = tmp_path / "unsafe.zip"
    archive_hash = write_zip(archive, spec.archive_root, {relative: ("100644", b"bad")})
    spec = replace(spec, archive_sha256=archive_hash)
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"protected")
    target = tmp_path / "extracted"
    with pytest.raises(ValueError, match="ZIP path"):
        vendors.extract_verified_archive(archive, target, spec)
    assert not target.exists()
    assert outside.read_bytes() == b"protected"


@pytest.mark.parametrize("entries", [
    {"link": ("120777", b"../../outside")},
    {"same.py": ("100644", b"one"), "SAME.py": ("100644", b"two")},
    {"file": ("100644", b"one"), "file/child": ("100644", b"two")},
])
def test_zip_symlinks_and_path_collisions_rejected(tmp_path, entries):
    spec = vendors.SPECS[0]
    archive = tmp_path / "unsafe.zip"
    spec = replace(spec, archive_sha256=write_zip(archive, spec.archive_root, entries))
    target = tmp_path / "extracted"
    with pytest.raises(ValueError, match="ZIP"):
        vendors.extract_verified_archive(archive, target, spec)
    assert not target.exists()


@pytest.mark.parametrize("limit", ["MAX_ARCHIVE_BYTES", "MAX_EXPANDED_BYTES", "MAX_MEMBERS"])
def test_zip_resource_limits_rejected(tmp_path, monkeypatch, limit):
    spec = vendors.SPECS[0]
    archive = tmp_path / "oversized.zip"
    spec = replace(spec, archive_sha256=write_zip(archive, spec.archive_root, {"a.py": ("100644", b"large")}))
    monkeypatch.setattr(vendors, limit, 0)
    with pytest.raises(ValueError, match="limit"):
        vendors.extract_verified_archive(archive, tmp_path / "extracted", spec)


def test_git_tree_includes_file_modes_and_git_directory_order():
    assert vendors.git_tree_sha1({}) == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    entries = {"a/z.py": ("100644", b"z\n"), "a.c": ("100644", b"c\n"), "run.sh": ("100755", b"run\n")}
    assert vendors.git_tree_sha1(entries) == vendors.git_tree_sha1(dict(reversed(list(entries.items()))))
    changed = dict(entries, **{"run.sh": ("100644", b"run\n")})
    assert vendors.git_tree_sha1(entries) != vendors.git_tree_sha1(changed)
