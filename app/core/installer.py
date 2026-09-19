"""Install, update and remove plugins: from a GitHub link, a zip, or a folder.

Installed plugins live in DATA/plugins/<id>/ next to the configuration, never
inside the application, so updating RackTicker never touches them and removing
one is deleting a folder. Each install is checked (layout, size, id) and loaded
once in a sandbox process before it replaces anything that was there.
"""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile

from app.core.manifest import DESCRIPTION, MANIFEST, SOURCE, read_manifest
from app.core.sandbox import describe

GITHUB = re.compile(r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?"
                    r"(?:/tree/(?P<ref>[\w./-]+?))?/?$")
MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_FILES = 2000
USER_AGENT = {"User-Agent": "RackTicker plugin installer (+https://github.com/costamesatechsolutions/rackticker)"}


class InstallError(ValueError):
    pass


def parse_github(url):
    """(owner, repo, ref or None, folder inside the repository) from a GitHub link.

    /tree/<ref>/<path> is ambiguous when a branch name has slashes; the common
    case (a branch without slashes) is assumed and the rest is the folder."""
    match = GITHUB.match(str(url).strip())
    if not match:
        raise InstallError("Paste a GitHub link like https://github.com/owner/repo or …/tree/main/folder")
    ref, folder = match["ref"], ""
    if ref and "/" in ref:
        ref, folder = ref.split("/", 1)
    return match["owner"], match["repo"], ref, folder.strip("/")


def safe_extract(data, target):
    """Unzip into target, refusing links, absolute paths, escapes and zip bombs."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise InstallError("That file is not a zip archive") from exc
    members = archive.infolist()
    if len(members) > MAX_FILES:
        raise InstallError("The archive has too many files")
    if sum(member.file_size for member in members) > 4 * MAX_ZIP_BYTES:
        raise InstallError("The archive is too large once unpacked")
    root = Path(target).resolve()
    for member in members:
        name = member.filename.replace("\\", "/")
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode) or name.startswith("/") or ".." in Path(name).parts:
            raise InstallError(f"Refusing unsafe path in archive: {name}")
        destination = (root / name).resolve()
        if root not in destination.parents and destination != root:
            raise InstallError(f"Refusing unsafe path in archive: {name}")
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, open(destination, "wb") as out:
            shutil.copyfileobj(source, out)


def find_plugin(folder, wanted=""):
    """The plugin folder inside an unpacked archive."""
    folder = Path(folder)
    entries = [path for path in folder.iterdir() if not path.name.startswith(".")]
    # GitHub archives (and most zips) wrap everything in one top-level folder.
    if len(entries) == 1 and entries[0].is_dir() and not (folder / MANIFEST).exists():
        folder = entries[0]
    if wanted:
        folder = folder / wanted
        if not folder.is_dir():
            raise InstallError(f"No folder {wanted!r} in that repository")
    if (folder / MANIFEST).exists():
        return folder
    candidates = sorted(path.parent for path in folder.glob(f"*/{MANIFEST}"))
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        names = ", ".join(path.name for path in candidates[:8])
        raise InstallError(f"That repository has several plugins ({names}); link to one folder")
    raise InstallError(f"No {MANIFEST} found: is this a RackTicker plugin?")


class Installer:
    def __init__(self, directory, reserved=()):
        self.directory = Path(directory)
        self.reserved = set(reserved)  # built-in screens and bundled plugin ids

    async def from_github(self, url, session):
        owner, repo, ref, folder = parse_github(url)
        api = f"https://api.github.com/repos/{owner}/{repo}"
        async with session.get(api, headers=USER_AGENT) as response:
            if response.status == 404:
                raise InstallError("GitHub has no public repository at that link "
                                   "(private repositories cannot be installed)")
            response.raise_for_status()
            ref = ref or (await response.json())["default_branch"]
        commit = await self.latest_commit(session, owner, repo, ref)
        async with session.get(f"https://codeload.github.com/{owner}/{repo}/zip/{commit}",
                               headers=USER_AGENT) as response:
            response.raise_for_status()
            data = await response.content.read(MAX_ZIP_BYTES + 1)
        if len(data) > MAX_ZIP_BYTES:
            raise InstallError("That repository is too large to install as a plugin")
        link = f"https://github.com/{owner}/{repo}" + (f"/tree/{ref}/{folder}" if folder else f"/tree/{ref}")
        return await self.from_zip(data, {"kind": "github", "url": link, "owner": owner, "repo": repo,
                                          "ref": ref, "folder": folder, "commit": commit}, folder)

    @staticmethod
    async def latest_commit(session, owner, repo, ref):
        async with session.get(f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}",
                               headers={**USER_AGENT, "Accept": "application/vnd.github.sha"}) as response:
            if response.status in (404, 422):
                raise InstallError(f"No branch, tag or commit named {ref!r}")
            response.raise_for_status()
            commit = (await response.text()).strip()
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise InstallError("GitHub returned an unexpected commit id")
        return commit

    async def from_zip(self, data, source, folder="", expected=None):
        if len(data) > MAX_ZIP_BYTES:
            raise InstallError("The zip is larger than 20 MB")
        self.directory.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.directory))
        try:
            safe_extract(data, staging / "unpacked")
            return await self._place(find_plugin(staging / "unpacked", folder), source, expected)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    async def from_folder(self, folder, source=None):
        """Copy a local folder in (used by tests and by developers on the device)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.directory))
        try:
            shutil.copytree(folder, staging / "copy", ignore=shutil.ignore_patterns("__pycache__", ".git"))
            return await self._place(staging / "copy", source or {"kind": "folder", "path": str(folder)})
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    async def _place(self, folder, source, expected=None):
        try:
            manifest = read_manifest(folder)
        except ValueError as exc:
            raise InstallError(str(exc)) from exc
        if manifest.id in self.reserved:
            raise InstallError(f"{manifest.id!r} is the id of a built-in screen; choose another id")
        if expected and manifest.id != expected:
            raise InstallError(f"Expected plugin {expected!r} but the upload is {manifest.id!r}")
        for stale in (SOURCE, DESCRIPTION):
            (folder / stale).unlink(missing_ok=True)
        try:
            description = await describe(folder)
        except (ValueError, OSError) as exc:
            raise InstallError(f"The plugin failed to load: {exc}") from exc
        (folder / DESCRIPTION).write_text(json.dumps(description, indent=2))
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (folder / SOURCE).write_text(json.dumps({**source, "installed_at": stamp}, indent=2))
        target = self.directory / manifest.id
        backup = self.directory / f".previous-{manifest.id}"
        shutil.rmtree(backup, ignore_errors=True)
        if target.exists():
            target.rename(backup)
        shutil.move(str(folder), str(target))
        shutil.rmtree(backup, ignore_errors=True)
        return read_manifest(target)

    def remove(self, plugin_id):
        target = self.directory / plugin_id
        if not target.is_dir() or target.parent != self.directory:
            raise InstallError("That plugin is not installed")
        shutil.rmtree(target)
