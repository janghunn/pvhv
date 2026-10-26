#!/usr/bin/env bash
# Install the pinned Blender 5.2.2 LTS (official portable Linux x64 tarball) user-locally.
#
#   blender/install_blender.sh        installs into ${BLENDER_PREFIX:-$HOME/.local/opt}
#
# The tarball's sha256 is verified before extraction; the extracted binary must report
# "Blender 5.2.2" before it is moved into place.  Prints the binary path on success.
set -euo pipefail

VERSION=5.2.2
URL=https://download.blender.org/release/Blender5.2/blender-5.2.2-linux-x64.tar.xz
SHA256=84098912789dc450e95697c4184fb8a90acbe5111c2ba4aede3fecb57806a168
NAME="blender-$VERSION-linux-x64"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
PREFIX="${BLENDER_PREFIX:-$HOME/.local/opt}"
mkdir -p "$PREFIX"
PREFIX="$(cd "$PREFIX" && pwd -P)"
case "$PREFIX/" in
    "$(cd "$REPO" && pwd -P)/"*) echo "error: BLENDER_PREFIX ($PREFIX) is inside the repository" >&2; exit 1 ;;
esac
DEST="$PREFIX/$NAME"
BIN="$DEST/blender"

if [ -x "$BIN" ] && "$BIN" --version 2>/dev/null | head -n 1 | grep -q "^Blender $VERSION"; then
    echo "$BIN"
    exit 0
fi
if [ -e "$DEST" ]; then
    echo "error: $DEST exists but is not a working Blender $VERSION - remove it and re-run" >&2
    exit 1
fi

STAGE="$(mktemp -d "$PREFIX/.$NAME.install.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
echo "downloading $URL" >&2
if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$STAGE/$NAME.tar.xz" "$URL"
else
    wget -O "$STAGE/$NAME.tar.xz" "$URL"
fi
echo "$SHA256  $STAGE/$NAME.tar.xz" | sha256sum -c - >&2
tar -xJf "$STAGE/$NAME.tar.xz" -C "$STAGE"
rm -f "$STAGE/$NAME.tar.xz"
"$STAGE/$NAME/blender" --version 2>/dev/null | head -n 1 | grep -q "^Blender $VERSION" || {
    echo "error: extracted binary does not report Blender $VERSION" >&2
    exit 1
}
mv "$STAGE/$NAME" "$DEST"
echo "$BIN"
