#!/usr/bin/env bash
# Publish the version in app/__init__.py as a GitHub Release.
#
# Devices update to the newest *release*, not to every push to main, so a version
# bumped in the code but never published here never reaches anyone.
#
#   tools/publish_release.sh            release HEAD of main as v<__version__>
#   tools/publish_release.sh <commit>   release an older commit (its own __version__)
set -euo pipefail
cd "$(dirname "$0")/.."

commit=$(git rev-parse "${1:-HEAD}")
version=$(git show "$commit:app/__init__.py" | sed -n 's/^__version__ = "\(.*\)"/\1/p')
tag="v$version"
[ -n "$version" ] || { echo "no __version__ at $commit" >&2; exit 1; }

git fetch -q origin main
git merge-base --is-ancestor "$commit" origin/main \
  || { echo "$commit is not on origin/main: push it first" >&2; exit 1; }
if [ -z "${1:-}" ] && [ -n "$(git status --porcelain)" ]; then
  echo "uncommitted changes: commit and push them first" >&2; exit 1
fi

# The release notes are this version's section of the changelog.
notes=$(git show "$commit:CHANGELOG.md" | awk -v v="## $version" '
  index($0, v) == 1 { on = 1; next }  on && /^## / { exit }  on { print }')
[ -n "$notes" ] || { echo "CHANGELOG.md has no '## $version' section" >&2; exit 1; }

if gh release view "$tag" >/dev/null 2>&1; then
  echo "$tag is already published"; exit 0
fi
git rev-parse -q --verify "refs/tags/$tag" >/dev/null || git tag -a "$tag" "$commit" -m "RackTicker $version"
git push -q origin "$tag"
title=${RELEASE_TITLE:-"RackTicker $version"}
gh release create "$tag" --verify-tag --title "$title" --notes "$notes" ${NOT_LATEST:+--latest=false}
echo "published $tag ($commit); devices will offer it within ten minutes"
