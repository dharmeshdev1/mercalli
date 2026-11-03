#!/usr/bin/env bash
# Choose the model archive and web build to use, and write cache keys for them to $GITHUB_OUTPUT, so
# the live pipeline only downloads them after a newer one is published. The model archive falls
# back to the newest dated copy when the current one is missing, for example mid-upload.
set -euo pipefail

assets() {
  gh api "repos/${GITHUB_REPOSITORY}/releases/tags/$1" \
    --jq '.assets[] | "\(.name) \(.id)-\(.updated_at)"' 2>/dev/null || true
}

pick() {  # $1 = release listing, $2 = preferred name, $3 = fallback pattern
  local exact
  exact="$(grep -E "^$2 " <<<"$1" | head -n1 || true)"
  if [ -n "$exact" ]; then echo "$exact"; return; fi
  grep -E "^$3 " <<<"$1" | sort -r | head -n1 || true
}

art="$(pick "$(assets artifacts)" 'artifacts\.tar\.gz' 'artifacts-[0-9]{8}\.tar\.gz')"
web="$(pick "$(assets site)" 'web\.tar\.gz' 'web-[0-9]{8}\.tar\.gz')"
{
  echo "artifacts_asset=${art%% *}"
  echo "artifacts=${art#* }"
  echo "web_asset=${web%% *}"
  echo "web=${web#* }"
} >> "$GITHUB_OUTPUT"
echo "artifacts: ${art:-none}; web: ${web:-none}"
