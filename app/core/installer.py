"""Install, update and remove plugins: from a GitHub link, a zip, or a folder.

Installed plugins live in DATA/plugins/<id>/ next to the configuration, never
inside the application, so updating RackTicker never touches them and removing
one is deleting a folder. Each install is checked (layout, size, id) and loaded
once in a sandbox process before it replaces anything that was there.
"""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import zipfile

from app.core.manifest import DESCRIPTION, MANIFEST, SOURCE, read_manifest
from app.core.sandbox import describe

GITHUB = re.compile(r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?"
                    r"(?:/tree/(?P<ref>[\w./-]+?))?/?$")
MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_FILES = 2000
USER_AGENT = {"User-Agent": "RackTicker plugin installer (+https://github.com/costamesatechsolutions/rackticker)"}
# GitHub's API allows 60 requests an hour from an address without an account, and a
# house is one address. So a repository's file list is asked for once and kept a
# while (every installed community plugin shares one), and files come from
# raw.githubusercontent.com, which is not counted, one plugin folder at a time:
# the community repository as one archive is past 18 MB.
TREE_SECONDS = 600
DOWNLOADS_AT_ONCE = 6
_trees = {}


class InstallError(ValueError):
    pass


async def read_limited(response, limit):
    """The whole body, up to `limit` bytes. `content.read(n)` returns only what has
    arrived so far, which on a slow link was a truncated zip."""
    chunks, size = [], 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        chunks.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    return b"".join(chunks)[:limit]


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
        if not ref:
            async with session.get(f"https://api.github.com/repos/{owner}/{repo}", headers=USER_AGENT) as response:
                if response.status == 404:
                    raise InstallError("GitHub has no public repository at that link "
                                       "(private repositories cannot be installed)")
                await check_limits(response)
                ref = (await response.json())["default_branch"]
        listing = await repo_tree(session, owner, repo, ref)
        folder = plugin_folder(listing, folder)
        prefix = f"{folder}/" if folder else ""
        files = [(path[len(prefix):], entry) for path, entry in listing["entries"].items()
                 if entry["type"] == "blob" and path.startswith(prefix)]
        if len(files) > MAX_FILES:
            raise InstallError("That plugin has too many files")
        if sum(entry.get("size", 0) for _, entry in files) > MAX_ZIP_BYTES:
            raise InstallError("That plugin is larger than 20 MB")
        self.directory.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.directory))
        try:
            target = staging / "plugin"
            await download(session, owner, repo, ref, listing, prefix, files, target)
            link = f"https://github.com/{owner}/{repo}/tree/{ref}" + (f"/{folder}" if folder else "")
            # What an update check compares: this folder's own fingerprint, so in a shared
            # repository a change to another plugin is not an update to this one.
            return await self._place(target, {"kind": "github", "url": link, "owner": owner, "repo": repo, "ref": ref,
                                              "folder": folder, "commit": listing["sha"],
                                              "tree": folder_id(listing, folder)})
        finally:
            await asyncio.to_thread(shutil.rmtree, staging, ignore_errors=True)

    async def from_zip(self, data, source, folder="", expected=None):
        if len(data) > MAX_ZIP_BYTES:
            raise InstallError("The zip is larger than 20 MB")
        self.directory.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.directory))
        try:
            # On a worker thread: a GitHub archive is the whole repository, and unpacking
            # it on the display's own loop froze the panel for a second on a Pi.
            await asyncio.to_thread(safe_extract, data, staging / "unpacked")
            return await self._place(find_plugin(staging / "unpacked", folder), source, expected)
        finally:
            await asyncio.to_thread(shutil.rmtree, staging, ignore_errors=True)

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


# --- GitHub, with as few API requests as possible --------------------------------------


async def check_limits(response):
    """Turn GitHub's rate limit into words, and anything else unexpected into an error."""
    if response.status in (403, 429) and (response.headers.get("X-RateLimit-Remaining") == "0"
                                          or response.status == 429):
        reset = response.headers.get("X-RateLimit-Reset")
        when = ""
        if reset and reset.isdigit():
            when = " Try again after " + datetime.fromtimestamp(int(reset)).strftime("%-I:%M %p").lower() + "."
        raise InstallError("GitHub is limiting how often this network can ask it (60 times an hour)." + when)
    if response.status == 404:
        raise InstallError("GitHub has no such repository, branch or folder")
    response.raise_for_status()


async def repo_tree(session, owner, repo, ref, fresh=False):
    """Every file and folder in a repository at ref, with git's fingerprint of each:
    one API request, kept TREE_SECONDS and shared by every plugin from it."""
    key = (owner, repo, ref)
    kept = _trees.get(key)
    if kept and not fresh and time.monotonic() - kept[0] < TREE_SECONDS:
        return kept[1]
    async with session.get(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}",
                           params={"recursive": "1"}, headers=USER_AGENT) as response:
        if response.status in (404, 422):
            raise InstallError(f"No branch, tag or commit named {ref!r} in {owner}/{repo}")
        await check_limits(response)
        body = await response.json()
    if body.get("truncated"):
        raise InstallError("That repository is too large to list; link to a smaller one")
    listing = {"sha": str(body.get("sha", "")),
               "entries": {item["path"]: {"type": item["type"], "sha": item["sha"], "size": item.get("size", 0),
                                          "mode": item.get("mode", "")}
                           for item in body.get("tree", []) if item.get("type") in ("blob", "tree")}}
    _trees[key] = (time.monotonic(), listing)
    return listing


def forget_tree(owner, repo, ref):
    _trees.pop((owner, repo, ref), None)


def folder_id(listing, folder):
    """git's fingerprint of a folder (the whole repository's commit for a plugin at its root)."""
    if not folder:
        return listing["sha"]
    entry = listing["entries"].get(folder)
    return entry["sha"] if entry and entry["type"] == "tree" else None


def plugin_folder(listing, folder):
    """The folder holding plugin.json, the way find_plugin does it for an archive."""
    entries = listing["entries"]
    if folder:
        if entries.get(folder, {}).get("type") != "tree":
            raise InstallError(f"No folder {folder!r} in that repository")
        if f"{folder}/{MANIFEST}" in entries:
            return folder
    elif MANIFEST in entries:
        return ""
    base = f"{folder}/" if folder else ""
    candidates = sorted(path[:-len(MANIFEST) - 1] for path in entries
                        if path.startswith(base) and path.endswith(f"/{MANIFEST}")
                        and path[len(base):].count("/") == 1)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        names = ", ".join(path.rsplit("/", 1)[-1] for path in candidates[:8])
        raise InstallError(f"That repository has several plugins ({names}); link to one folder")
    raise InstallError(f"No {MANIFEST} found: is this a RackTicker plugin?")


def blob_id(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


async def download(session, owner, repo, ref, listing, prefix, files, target):
    """Each file of one plugin folder from raw.githubusercontent.com, checked against git's
    fingerprint from the listing, so what lands is exactly what the listing describes."""
    gate = asyncio.Semaphore(DOWNLOADS_AT_ONCE)
    root = Path(target).resolve()

    async def fetch(relative, entry):
        destination = (root / relative).resolve()
        if root not in destination.parents or ".." in Path(relative).parts:
            raise InstallError(f"Refusing unsafe path: {relative}")
        data = None
        async with gate:
            # By the listed commit, so a push halfway through cannot mix two versions; by
            # the branch if that commit is not served (the check below still applies).
            for revision in (listing["sha"], ref):
                url = f"https://raw.githubusercontent.com/{owner}/{repo}/{revision}/{prefix}{relative}"
                async with session.get(url, headers=USER_AGENT) as response:
                    if response.status == 404 and revision != ref:
                        continue
                    await check_limits(response)
                    data = await read_limited(response, MAX_ZIP_BYTES + 1)
                    break
        if blob_id(data) != entry["sha"]:
            raise InstallError(f"{prefix}{relative} did not download completely; try again")
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(destination.write_bytes, data)

    root.mkdir(parents=True, exist_ok=True)
    await asyncio.gather(*(fetch(relative, entry) for relative, entry in files))


def local_tree_id(folder, skip=(".source.json", ".describe.json", "__pycache__")):
    """git's fingerprint of a folder on disk, for plugins installed before the
    fingerprint was recorded: it tells an untouched install from an out-of-date one."""
    body = b""
    items = []
    for path in Path(folder).iterdir():
        if path.name in skip or path.is_symlink():
            continue
        if path.is_dir():
            items.append((path.name + "/", b"40000", path.name, bytes.fromhex(local_tree_id(path, skip))))
        else:
            mode = b"100755" if os.access(path, os.X_OK) else b"100644"
            items.append((path.name, mode, path.name, bytes.fromhex(blob_id(path.read_bytes()))))
    for _, mode, name, sha in sorted(items, key=lambda item: item[0].encode()):
        body += mode + b" " + name.encode() + b"\0" + sha
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()
