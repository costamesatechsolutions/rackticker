#!/usr/bin/env python3
"""Build the source and printable-enclosure archives for a RackTicker release."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
HARDWARE_FILES = tuple(
    str(path.relative_to(ROOT)) for path in sorted((ROOT / "hardware/enclosure/v10").rglob("*"))
    if path.is_file() and path.suffix in (".3mf", ".stl", ".step", ".png", ".md")
) + ("hardware/enclosure/BEZEL_V10.md", "hardware/enclosure/README.md", "hardware/enclosure/MEASUREMENTS.md")


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_hardware_zip(path: Path, version: str) -> None:
    prefix = f"rackticker-{version}-enclosure/"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for relative in HARDWARE_FILES:
            source = ROOT / relative
            if not source.is_file():
                raise SystemExit(f"missing release file: {relative}")
            info = zipfile.ZipInfo(prefix + relative, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()

    match = re.search(
        r'^version\s*=\s*"([^"]+)"',
        (ROOT / "pyproject.toml").read_text(),
        re.MULTILINE,
    )
    if not match:
        raise SystemExit("project version missing from pyproject.toml")
    version = match.group(1)
    if run("git", "status", "--porcelain"):
        raise SystemExit("release builds require a clean working tree")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = output / f"rackticker-{version}-source.tar.gz"
    hardware = output / f"rackticker-{version}-enclosure.zip"

    subprocess.run(
        ["git", "archive", "--format=tar.gz",
         f"--prefix=rackticker-{version}/", f"--output={source}", "HEAD"],
        cwd=ROOT,
        check=True,
    )
    write_hardware_zip(hardware, version)

    sums = output / "SHA256SUMS"
    sums.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in (source, hardware))
    )
    print(source)
    print(hardware)
    print(sums)


if __name__ == "__main__":
    main()
