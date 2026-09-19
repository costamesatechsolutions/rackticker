"""Source offer for this running core, excluding private config and runtime data."""
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def source_zip():
    installed_root = Path(__file__).resolve().parents[2]
    working_root = Path.cwd().resolve()
    # A deployed wheel runs from /opt/rackticker/current; prefer that matching
    # checkout so separately packaged in-tree plugins and deployment files are
    # included in the AGPL source offer. Fall back to installed package sources.
    root = (working_root if (working_root / "pyproject.toml").is_file()
            and (working_root / "app").is_dir() else installed_root)
    paths = []
    # Explicit source directories and file types. Never archive the workspace,
    # installed dependencies, .git, plugins' settings, exports or credentials.
    for directory in ("app", "rackticker", "plugins", "deploy", "tools", "tests"):
        for path in (root / directory).rglob("*"):
            if (path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
                    and (path.suffix in (".py", ".js", ".html", ".css", ".toml", ".md", ".sh", ".service") or path.name == "LICENSE")
                    and path.resolve().is_relative_to(root / directory)):
                paths.append(path)
    for name in ("LICENSE", "README.md", "pyproject.toml", "requirements.txt", "config/config.example.json",
                 "docs/plugins.md", "docs/architecture.md"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            paths.append(path)
    # Wheels retain Python source and the license in package data. Supply build
    # metadata when the source checkout's pyproject is not present.
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, "rackticker-source/" + path.relative_to(root).as_posix())
        license_path = root / "app/web/static/LICENSE"
        if not (root / "LICENSE").is_file():
            archive.write(license_path, "rackticker-source/LICENSE")
        if not (root / "pyproject.toml").is_file():
            archive.writestr("rackticker-source/pyproject.toml", '''[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"
[project]
name = "rackticker"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = ["Pillow>=9.4,<13", "aiohttp>=3.8,<4"]
license = "AGPL-3.0-only"
license-files = ["LICENSE"]
[project.scripts]
rackticker = "app.main:main"
[tool.setuptools.packages.find]
include = ["app*", "rackticker*"]
[tool.setuptools.package-data]
"app.web" = ["static/*"]
''')
        archive.writestr("rackticker-source/SOURCE-NOTES.txt",
                         "Run: python -m pip install .\nThen: python -m app\n"
                         "This archive contains the running RackTicker core. Private configuration is excluded.\n"
                         "Third-party plugins are separate packages; obtain their matching source from their authors.\n")
    return out.getvalue()
