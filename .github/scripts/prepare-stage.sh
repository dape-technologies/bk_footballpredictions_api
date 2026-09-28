#!/usr/bin/env bash
set -Eeuo pipefail

app_root=${1:?Application root is required}
stage_name=${2:?Stage name is required}

if [[ "$app_root" != /home/*/* || "$stage_name" != .deploy-api-* ]]; then
  echo "Refusing unsafe deployment paths." >&2
  exit 1
fi

stage="$app_root/$stage_name"
shopt -s nullglob
for old_stage in "$app_root"/.deploy-api-*; do
  rm -rf -- "$old_stage"
done
mkdir -p -- "$stage"
