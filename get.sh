#!/bin/sh
# Install ha-batocera on a Batocera box from a release on GitHub, without
# giving Home Assistant root on the box. Run it there as root; README.md has
# the command that fetches this file.
#
# It downloads the source archive of the release below, over HTTPS only, and
# runs the installer from it. The release is fixed: every release updates TAG.
set -eu

TAG=v0.3.0
URL=https://github.com/vjt/ha-batocera/archive/refs/tags/$TAG.tar.gz

dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
curl -fsSL --proto '=https' --tlsv1.2 -o "$dir/src.tar.gz" "$URL"
tar -xzf "$dir/src.tar.gz" -C "$dir"
set -- "$dir"/*/custom_components/batocera/daemon/install.sh
bash "$1"
