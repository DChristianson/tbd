#!/usr/bin/env bash
# Builds batari Basic (bB), the BASIC-to-2600 compiler, from a pinned upstream
# source archive and installs it into the folder given as the only argument.
# src/tbd/tools/batari.py runs it from there, found through TBD_BATARI
# (tbd.paths.BATARI).
#
#   bash .devcontainer/build-batari.sh /opt/batari-basic    # dev container image
#   bash .devcontainer/build-batari.sh build/batari-basic   # a Mac, in the checkout
#
# Needs curl, a C compiler, make and flex (with m4). The Xcode command-line
# tools have all of them; the dev container image installs flex.
#
# The folder gets bB's four pipeline programs (preprocess, 2600basic,
# postprocess, optimize), its includes/ and its LICENSE.txt. bB is GPL-2.0;
# its includes/ assembly is CC0 (see LICENSE.txt). bbfilter (a DASM output
# filter) is not installed: batari.py filters DASM's output itself.
#
# The hash is of GitHub's source archive for the tag. To upgrade: change
# BB_VERSION, download
#   https://github.com/batari-Basic/batari-Basic/archive/refs/tags/v<ver>.tar.gz
# and paste in its sha256.
set -euo pipefail

BB_VERSION=1.9
BB_SHA256=93843bf8ad1f9dedaa8f7faf88c38219a4a80b1c55cdc10d6d483f869bd64147

if [ $# -ne 1 ]; then
  echo "usage: $0 <install folder>" >&2
  exit 2
fi
mkdir -p "$1"
prefix=$(cd "$1" && pwd)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"

curl -fsSL -o bb.tar.gz \
  "https://github.com/batari-Basic/batari-Basic/archive/refs/tags/v${BB_VERSION}.tar.gz"
if command -v sha256sum >/dev/null; then
  echo "$BB_SHA256  bb.tar.gz" | sha256sum -c -
else
  echo "$BB_SHA256  bb.tar.gz" | shasum -a 256 -c -
fi
tar -xzf bb.tar.gz
src="batari-Basic-${BB_VERSION}"

# Upstream's makefile calls `lex`; name flex directly, since some systems
# have flex without the lex alias.
make -C "$src" LEX=flex 2600basic preprocess postprocess optimize

for prog in preprocess 2600basic postprocess optimize; do
  install -m 0755 "$src/$prog" "$prefix/$prog"
done
rm -rf "$prefix/includes"
cp -R "$src/includes" "$prefix/includes"
install -m 0644 "$src/LICENSE.txt" "$prefix/LICENSE.txt"
echo "$BB_VERSION" > "$prefix/VERSION"

echo "batari Basic $BB_VERSION installed in $prefix"
