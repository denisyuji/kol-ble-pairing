#!/usr/bin/env bash
# fetch-artifacts.sh -- push the current branch, wait for its CI build
# (.github/workflows/build.yml) and download the executables for Linux,
# macOS and Windows into artifacts/.
#
#   ./fetch-artifacts.sh          -> artifacts/kol-ble-pairing-*.{tar.gz,exe}
#
# Needs the GitHub CLI (gh), logged in. Only committed work is built: the
# script refuses to run with uncommitted changes.

set -euo pipefail
cd "$(dirname "$0")"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "uncommitted changes: commit them first, CI builds only what is pushed" >&2
  exit 1
fi

branch=$(git branch --show-current)
sha=$(git rev-parse HEAD)
echo "pushing $branch ($(git log --oneline -1)) ..."
git push origin "$branch"

echo "waiting for the CI run of ${sha:0:7} to start ..."
run=
for _ in $(seq 60); do
  run=$(gh run list --workflow build.yml --commit "$sha" --event push \
          --limit 1 --json databaseId --jq '.[0].databaseId // empty')
  [ -n "$run" ] && break
  sleep 5
done
[ -n "$run" ] || { echo "no CI run started for ${sha:0:7} within 5 minutes" >&2; exit 1; }

gh run watch "$run" --exit-status --interval 15 \
  || { echo "CI run $run failed: gh run view $run --log-failed" >&2; exit 1; }

rm -rf artifacts
gh run download "$run" --dir artifacts.tmp
mkdir artifacts
mv artifacts.tmp/*/* artifacts/
rm -rf artifacts.tmp
echo "downloaded from CI run $run (${sha:0:7}):"
ls -l artifacts
