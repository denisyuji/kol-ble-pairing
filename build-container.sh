#!/usr/bin/env bash
# build-container.sh -- build the Linux executable inside a podman container
# with an old glibc (Containerfile), so it runs on older distros than the
# build machine's.
#
#   ./build-container.sh      -> dist/kol-ble-pairing
#
# The sources are mounted read-only and copied into the container; only dist/
# is bind-mounted writable, and the result is owned by the calling user.

set -euo pipefail
cd "$(dirname "$0")"

IMAGE=kol-ble-pairing-build
mkdir -p dist
podman build --tag "$IMAGE" --file Containerfile .
podman run --rm --userns=keep-id \
  --volume "$PWD:/src:ro,Z" \
  --volume "$PWD/dist:/out:Z" \
  "$IMAGE" bash -euc '
    work=$(mktemp -d)
    cp /src/kol_ble_pairing.py /src/build.sh /src/requirements.txt /src/requirements-build.txt "$work"
    cd "$work"
    ./build.sh
    cp dist/kol-ble-pairing /out/
    echo "glibc used: $(ldd --version | head -1)"
  '
echo "built: $(pwd)/dist/kol-ble-pairing"
