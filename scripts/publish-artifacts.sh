#!/usr/bin/env bash
# Publish packaged artifacts to the `artifacts` release, which doubles as the model registry:
# `artifacts.tar.gz` is always the current set, and each training run also keeps a dated copy and
# its model card. Only the twelve most recent dated copies are kept.
#   scripts/publish-artifacts.sh STAMP DIR   (DIR holds artifacts.tar.gz and model-card.json)
set -euo pipefail
stamp="$1"
dir="$2"

cp "$dir/artifacts.tar.gz" "/tmp/artifacts-${stamp}.tar.gz"
cp "$dir/model-card.json" "/tmp/model-card-${stamp}.json"

if ! gh release view artifacts >/dev/null 2>&1; then
  gh release create artifacts --prerelease --title "Model artifacts" \
    --notes "Trained models, the dependency graph, census results and the public incident dataset. Updated weekly by the Models workflow."
fi
# The dated copy goes up first: if replacing the current archive fails halfway, the live pipeline
# falls back to the newest dated copy instead of running without models.
gh release upload artifacts "/tmp/artifacts-${stamp}.tar.gz" "/tmp/model-card-${stamp}.json" --clobber
gh release upload artifacts "$dir/artifacts.tar.gz" --clobber

# Keep the registry bounded.
gh release view artifacts --json assets --jq '.assets[].name' | grep -E '^artifacts-[0-9]{8}\.tar\.gz$' | sort -r \
  | tail -n +13 | while read -r old; do
    day="${old#artifacts-}"
    day="${day%.tar.gz}"
    gh release delete-asset artifacts "$old" --yes
    gh release delete-asset artifacts "model-card-${day}.json" --yes 2>/dev/null || true
  done
