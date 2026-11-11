"""Rebuild pinned external source vendors in a new directory, without model IO."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import urllib.request
import zipfile


@dataclass(frozen=True)
class VendorSpec:
    name: str
    repository: str
    commit: str
    tree: str
    archive_sha256: str
    source_sha256: str

    @property
    def archive_root(self) -> str:
        return f"{self.repository.rsplit('/', 1)[1]}-{self.commit}"

    @property
    def archive_name(self) -> str:
        return self.archive_root + ".zip"

    @property
    def archive_url(self) -> str:
        return f"https://codeload.github.com/{self.repository}/zip/{self.commit}"


SPECS = (
    VendorSpec(
        "edc", "clear-nus/edc", "02aad17e2fc1e9845c84746b384e7fd3160edc1a",
        "25ea1bcdd6841713ba67db1c9942d2f98bd746dc",
        "0aa33b66c35338ac572bfa96238c39128b9b678e471de3a247cc5794075f6493",
        "e6365be857dd0e594c5d5d0a4e787e84afce23404798c8a6d118b7072723a380",
    ),
    VendorSpec(
        "itext2kg", "AuvaLab/itext2kg", "43bc581c99a3a35ace99dc4699acdabb0f1a98f7",
        "54b19da57c9968de892187f5decb20dc4c3ac577",
        "d36c5fe24ae1a466542b2a2876c6059d84affbcac1be6a48adbdca5aeb121a54",
        "c93a08d7aa80f52320ab65afa428cd3e5675ac4f3d9a1b0bfd5907bef520b499",
    ),
)
PATCH_PATH = Path(__file__).resolve().parent / "vendor_patches" / "edc_go_transport.patch"
PATCH_SHA256 = "382e423a09b043f8c204c7453f8b6e96376b0fac80efc93b58fad507b3ef4a8c"
PATCH_PATHS = {
    "edc/extract.py", "edc/schema_canonicalization.py",
    "edc/schema_definition.py", "edc/utils/llm_utils.py",
}
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 10000
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
}
EDC_TEXT_SUFFIXES = {".py", ".txt", ".csv", ".json", ".md", ".yml", ".sh"}


def source_tree_sha256(package: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py"), key=lambda p: p.relative_to(package).as_posix()):
        if "__pycache__" in path.parts:
            continue
        raw = path.read_bytes()
        raw.decode("utf-8")
        digest.update(path.relative_to(package).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw)
        digest.update(b"\0")
    return digest.hexdigest()


def _git_object(kind: str, data: bytes) -> bytes:
    return hashlib.sha1(kind.encode("ascii") + b" " + str(len(data)).encode("ascii") + b"\0" + data).digest()


def git_tree_sha1(entries: dict[str, tuple[str, bytes]]) -> str:
    """Reconstruct Git trees from ZIP paths, executable modes, and raw blob bytes."""
    root = {}
    for name, (mode, raw) in entries.items():
        branch = root
        parts = name.split("/")
        for component in parts[:-1]:
            branch = branch.setdefault(component, {})
        branch[parts[-1]] = (mode, _git_object("blob", raw))

    def encode(branch):
        data = bytearray()
        ordered = sorted(branch.items(), key=lambda kv: (kv[0] + ("/" if isinstance(kv[1], dict) else "")).encode("utf-8"))
        for name, value in ordered:
            mode, object_id = ("40000", encode(value)) if isinstance(value, dict) else value
            data.extend(mode.encode("ascii") + b" " + name.encode("utf-8") + b"\0" + object_id)
        return _git_object("tree", bytes(data))

    return encode(root).hex()


def _member_parts(info: zipfile.ZipInfo, archive_root: str) -> tuple[str, ...]:
    name = info.orig_filename
    if "\0" in name or "\\" in name or name.startswith("/"):
        raise ValueError(f"Unsafe ZIP path: {name!r}")
    parts = name.removesuffix("/").split("/")
    for part in parts:
        if (not part or part in {".", ".."} or part.endswith((".", " "))
                or any(ord(c) < 32 or c in ':<>\"|?*' for c in part)
                or part.split(".", 1)[0].upper() in WINDOWS_DEVICES
                or part.casefold() == ".git"):
            raise ValueError(f"Unsafe ZIP path: {name!r}")
    if parts[0] != archive_root or (len(parts) == 1 and not info.is_dir()):
        raise ValueError(f"Unexpected ZIP root: {name!r}")
    return tuple(parts[1:])


def extract_verified_archive(archive: Path, target: Path, spec: VendorSpec) -> None:
    if os.path.lexists(target):
        raise FileExistsError(f"Extraction target already exists: {target}")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Source archive exceeds size limit")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != spec.archive_sha256:
        raise ValueError(f"{spec.name} ZIP SHA-256 mismatch")

    entries = {}
    seen = set()
    file_keys = set()
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > MAX_MEMBERS or sum(i.file_size for i in members) > MAX_EXPANDED_BYTES:
            raise ValueError("Source archive exceeds extraction limits")
        for info in members:
            parts = _member_parts(info, spec.archive_root)
            key = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)
            if key in seen:
                raise ValueError(f"Duplicate or case-colliding ZIP path: {info.filename!r}")
            seen.add(key)
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if info.flag_bits & 1 or kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError("Encrypted or special ZIP entry is not supported")
            if info.is_dir():
                if kind == stat.S_IFREG:
                    raise ValueError("ZIP directory has regular-file mode")
                continue
            if kind == stat.S_IFDIR:
                raise ValueError("ZIP file has directory mode")
            file_keys.add(key)
            entries["/".join(parts)] = ("100755" if mode & 0o111 else "100644", source.read(info))
    if any(key[:i] in file_keys for key in seen for i in range(1, len(key))):
        raise ValueError("ZIP file/directory path collision")
    if git_tree_sha1(entries) != spec.tree:
        raise ValueError(f"{spec.name} upstream Git tree mismatch")

    target.mkdir(parents=True, exist_ok=False)
    resolved_target = target.resolve()
    for relative, (mode, raw) in entries.items():
        path = target.joinpath(*relative.split("/"))
        path.resolve().relative_to(resolved_target)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
        path.chmod(0o755 if mode == "100755" else 0o644)


def _verified_patch() -> bytes:
    raw = PATCH_PATH.read_bytes().replace(b"\r\n", b"\n")
    text = raw.decode("utf-8")
    if hashlib.sha256(raw).hexdigest() != PATCH_SHA256:
        raise ValueError("EDC source patch SHA-256 mismatch")
    old = re.findall(r"^--- a/(.+)$", text, flags=re.MULTILINE)
    new = re.findall(r"^\+\+\+ b/(.+)$", text, flags=re.MULTILINE)
    if not old or old != new or not set(old).issubset(PATCH_PATHS):
        raise ValueError("EDC patch has unexpected source paths")
    return raw


def _apply_edc_patch(root: Path, raw: bytes, git: str) -> None:
    patch = root.parent / "verified-edc.patch"
    patch.write_bytes(raw)
    # Prevent inherited Git settings or a parent checkout from redirecting apply.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CEILING_DIRECTORIES=str(root.parent.resolve()))
    command = [git, "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn"]
    for options in (["--check"], []):
        subprocess.run(command + options + [str(patch.resolve())], cwd=root, env=env,
                       check=True, capture_output=True, timeout=30)


def _restore_edc_snapshot_newlines(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and (path.suffix in EDC_TEXT_SUFFIXES or path.name in {"LICENSE", ".gitignore"}):
            raw = path.read_bytes()
            raw.decode("utf-8")
            path.write_bytes(raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))


def _download_archive(spec: VendorSpec, target: Path) -> None:
    request = urllib.request.Request(spec.archive_url, headers={"User-Agent": "MaF-KG-vendor-reproducer/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, target.open("xb") as stream:
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("Downloaded source archive exceeds size limit")
            stream.write(chunk)


def _write_metadata(root: Path, spec: VendorSpec, upstream_python_hash: str) -> dict:
    lock = {
        "repository": f"https://github.com/{spec.repository}.git",
        "locked_ref": spec.commit,
        "upstream_commit": spec.commit,
        "upstream_tree": spec.tree,
        "archive_url": spec.archive_url,
        "archive_sha256": spec.archive_sha256,
        "upstream_python_sha256": upstream_python_hash,
        "dependency_declaration": "requirements-adapter.txt",
    }
    if spec.name == "edc":
        lock.update(adapted_python_sha256=spec.source_sha256, patch_sha256=PATCH_SHA256,
                    patch_source="scripts/vendor_patches/edc_go_transport.patch",
                    text_line_endings="CRLF: frozen Windows checkout bytes",
                    adaptation_record="ADAPTATION.md")
        requirements = "numpy==1.26.4\ntqdm==4.65.0\n"
        note_name = "ADAPTATION.md"
        note = (
            "# Reconstructed EDC adapted source\n\n"
            "The locked upstream ZIP and Git tree are verified before applying the source patch.\n"
            "The patch contains injected, labelled, single-attempt chat transport and lazy imports\n"
            "in extract.py, schema_definition.py, schema_canonicalization.py and utils/llm_utils.py.\n"
            "Native prompts, parsers and stage behavior are retained. EDC text files are explicitly\n"
            "restored to CRLF to reproduce the frozen Windows snapshot and its raw-byte source hash.\n\n"
            "requirements-adapter.txt declares the Target Alignment import dependencies. Installation\n"
            "is caller-managed. Shared L2-normalized 384d multilingual MiniLM ONNX embeddings and\n"
            "the model transport are supplied externally: this remains an adapted baseline.\n"
            "This source setup performs no model IO, credential lookup, pip install or model download.\n"
        )
    else:
        lock.update(upstream_source_sha256=spec.source_sha256,
                    source_layout=f"snapshot/{spec.archive_root}/",
                    text_line_endings="Unmodified ZIP bytes", adaptation_record="UPSTREAM.md")
        requirements = "numpy\nscikit-learn\npydantic>=2\nlangchain-core\n"
        note_name = "UPSTREAM.md"
        note = (
            "# Reconstructed iText2KG v0.0.5 source\n\n"
            f"Repository: {spec.repository}\nCommit: {spec.commit}\n"
            f"ZIP SHA-256: {spec.archive_sha256}\nGit tree: {spec.tree}\n"
            f"Package SHA-256: {spec.source_sha256}\n\n"
            f"The unchanged ZIP is retained here; original sources are in snapshot/{spec.archive_root}/.\n"
            "requirements-adapter.txt declares the adapter's NumPy/scikit-learn matcher and\n"
            "Pydantic/langchain-core schema dependencies. Their versions belong to the separate\n"
            "experiment environment lock; this source installer does not resolve or install them.\n"
            "The upstream requirements.txt is preserved inside the source snapshot. The external\n"
            "adapter supplies IO and bypasses eager provider/Neo4j exports without editing source.\n"
        )
    (root / "UPSTREAM_LOCK.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8", newline="\n")
    (root / "requirements-adapter.txt").write_text(requirements, encoding="utf-8", newline="\n")
    (root / note_name).write_text(note, encoding="utf-8", newline="\n")
    return lock


def prepare(output_dir: str | Path, *, archives_dir: str | Path | None = None) -> dict:
    destination = Path(output_dir).expanduser().absolute()
    if os.path.lexists(destination):
        raise FileExistsError(f"Refusing to overwrite existing target: {destination}")
    patch = _verified_patch()
    git = shutil.which("git")
    if git is None:
        raise FileNotFoundError("Git is required to check and apply the pinned EDC patch")
    cache = Path(archives_dir) if archives_dir is not None else None
    locks = {}
    with tempfile.TemporaryDirectory(prefix="maf-vendor-setup-") as temporary:
        stage = Path(temporary)
        downloaded = {}
        for spec in SPECS:
            archive = stage / spec.archive_name
            if cache is None:
                _download_archive(spec, archive)
            else:
                shutil.copyfile(cache / spec.archive_name, archive)
            downloaded[spec.name] = archive

        bundle = stage / "vendors"
        for spec in SPECS:
            vendor = bundle / spec.name
            source = vendor if spec.name == "edc" else vendor / "snapshot" / spec.archive_root
            extract_verified_archive(downloaded[spec.name], source, spec)
            package = source / spec.name
            upstream_hash = source_tree_sha256(package)
            if spec.name == "edc":
                _apply_edc_patch(source, patch, git)
                _restore_edc_snapshot_newlines(source)
            actual_hash = source_tree_sha256(package)
            if actual_hash != spec.source_sha256:
                raise ValueError(f"{spec.name} final Python source SHA-256 mismatch: {actual_hash}")
            shutil.copyfile(downloaded[spec.name], vendor / spec.archive_name)
            locks[spec.name] = _write_metadata(vendor, spec, upstream_hash)

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.mkdir(exist_ok=False)
        for spec in SPECS:
            shutil.copytree(bundle / spec.name, destination / spec.name)
    return locks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="New, nonexistent vendor root")
    parser.add_argument("--archives-dir", type=Path, help="Read pinned ZIPs here instead of downloading")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.output_dir, archives_dir=args.archives_dir)
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(f"Vendor preparation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "vendors": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
