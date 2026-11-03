#!/usr/bin/env bash
# Download the chosen model archive into ./artifacts and the web build into ./web-build, unless a
# cache restored them. Nothing here may fail the pipeline: without models it publishes status
# without forecasts, and without a web build it publishes the API alone.
set -uo pipefail

fetch() {  # $1 = release, $2 = asset name, $3 = target directory
  [ -n "$2" ] || return 0
  [ -z "$(ls -A "$3" 2>/dev/null)" ] || return 0
  local tmp
  tmp="$(mktemp -d)"
  # Only archives this repository's own workflows built and signed are used: the attestation ties
  # the file to a workflow run here, so a file swapped in by anything else is refused.
  if gh release download "$1" --pattern "$2" --dir "$tmp" --clobber \
    && gh attestation verify "$tmp/$2" --repo "$GITHUB_REPOSITORY" >/dev/null \
    && tar -xzf "$tmp/$2" -C "$3"; then
    echo "downloaded and verified $2"
  else
    echo "could not fetch or verify $2 from $1; continuing without it"
    rm -rf "${3:?}"/*
  fi
  rm -rf "$tmp"
}

mkdir -p artifacts web-build site
fetch artifacts "${ARTIFACTS_ASSET:-}" artifacts
fetch site "${WEB_ASSET:-}" web-build
echo "artifacts: $(cat artifacts/VERSION 2>/dev/null || echo none); web: $(cat web-build/BUILD 2>/dev/null || echo none)"

# The site is the web build with the freshly published API on top.
cp -R web-build/. site/ 2>/dev/null || true
