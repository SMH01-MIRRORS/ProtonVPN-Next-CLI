#!/bin/sh
# Recompute the buildGoModule vendorHash for pvpn-engine in flake.nix and,
# if it changed, commit the fix back to master.
# Runs in CI on tag pushes only (see .woodpecker.yml, step update-nix-hash).
#
# How it works:
#   1. Replace the current vendorHash with a fake one.
#   2. Run `nix build` and parse the real hash from the mismatch error
#      ("got: sha256-..."). This is the canonical way to obtain it.
#   3. If the real hash differs from the committed one, verify the build
#      with the new hash, then commit and push flake.nix to master.
set -eu

FLAKE="flake.nix"
FAKE_HASH="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
BUILD_TARGET="path:.#"

set_hash() {
    sed -i "s|vendorHash = \"sha256-[^\"]*\"|vendorHash = \"$1\"|" "$FLAKE"
}

current_hash=$(sed -n 's/.*vendorHash = "\(sha256-[^"]*\)".*/\1/p' "$FLAKE" | head -n1)
if [ -z "$current_hash" ]; then
    echo "ERROR: vendorHash not found in $FLAKE" >&2
    exit 1
fi
echo "Committed vendorHash: $current_hash"

# Ask Nix for the real hash: build with a fake hash and parse the mismatch error.
set_hash "$FAKE_HASH"
nix build "$BUILD_TARGET" --no-link 2> nix-build.log || true
new_hash=$(sed -n 's/.*got: *\(sha256-[A-Za-z0-9+/=]*\).*/\1/p' nix-build.log | head -n1)
set_hash "$current_hash"

if [ -z "$new_hash" ]; then
    echo "ERROR: could not extract the expected hash from nix output:" >&2
    cat nix-build.log >&2
    exit 1
fi
echo "Actual vendorHash:    $new_hash"

if [ "$new_hash" = "$current_hash" ]; then
    echo "vendorHash is up to date - nothing to do."
    exit 0
fi

# Safety: make sure the flake actually builds with the new hash before pushing.
set_hash "$new_hash"
nix build "$BUILD_TARGET" --no-link
echo "Build with the new hash succeeded."

# Commit the fix to master (the tag itself stays immutable).
git config user.name "woodpecker-ci"
git config user.email "woodpecker-ci@noreply.codeberg.org"
git fetch origin master
git checkout -B master origin/master
set_hash "$new_hash"
if git diff --quiet -- "$FLAKE"; then
    echo "master already contains the new vendorHash - nothing to push."
    exit 0
fi
git commit -m "fix(nix): update pvpn-engine vendorHash [skip ci]" \
    -m "Auto-generated on tag ${CI_COMMIT_TAG:-unknown}." -- "$FLAKE"
git push "https://oauth2:${CODEBERG_TOKEN}@codeberg.org/${CI_REPO}.git" master
echo "Pushed updated vendorHash to master."
