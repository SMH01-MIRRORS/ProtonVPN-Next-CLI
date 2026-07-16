#!/bin/sh
# Sync flake.nix with reality on tag pushes:
#   - recompute the buildGoModule vendorHash for pvpn-engine,
#   - set every `version = "...";` field to the pushed tag (CI_COMMIT_TAG).
# If anything changed, commit flake.nix back to master.
# Runs in CI on tag pushes only (see .woodpecker.yml, step update-nix-hash).
#
# How the hash is obtained: replace the committed vendorHash with a fake one,
# run `nix build` and parse the real hash from the mismatch error
# ("got: sha256-..."). This is the canonical way to obtain it.
set -eu

FLAKE="flake.nix"
FAKE_HASH="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
BUILD_TARGET="path:.#"
TAG="${CI_COMMIT_TAG:-}"

set_hash() {
    sed -i "s|vendorHash = \"sha256-[^\"]*\"|vendorHash = \"$1\"|" "$FLAKE"
}

set_version() {
    sed -i "s|version = \"[^\"]*\";|version = \"$1\";|" "$FLAKE"
}

apply_desired_state() {
    set_hash "$new_hash"
    if [ -n "$TAG" ]; then
        set_version "$TAG"
    fi
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
if [ -n "$TAG" ]; then
    echo "Target version:       $TAG"
fi

if [ "$new_hash" != "$current_hash" ]; then
    # Safety: make sure the flake actually builds with the new hash before pushing.
    set_hash "$new_hash"
    nix build "$BUILD_TARGET" --no-link
    set_hash "$current_hash"
    echo "Build with the new hash succeeded."
fi

# Check whether anything needs to change at all.
apply_desired_state
if git diff --quiet -- "$FLAKE"; then
    echo "flake.nix is up to date - nothing to do."
    exit 0
fi
git --no-pager diff -- "$FLAKE"

if [ -z "${CI:-}" ]; then
    echo "Not running in CI - changes left uncommitted in the worktree."
    exit 0
fi

# Commit to master (the tag itself stays immutable). CI checks out the tag
# detached, so reset the file first and re-apply the changes on master.
git checkout -- "$FLAKE"
git config user.name "woodpecker-ci"
git config user.email "woodpecker-ci@noreply.codeberg.org"
git fetch origin master
git checkout -B master origin/master
apply_desired_state
if git diff --quiet -- "$FLAKE"; then
    echo "master already up to date - nothing to push."
    exit 0
fi
git commit -m "chore(nix): sync flake vendorHash and version [skip ci]" \
    -m "Auto-generated on tag ${TAG:-unknown}." -- "$FLAKE"
git push "https://oauth2:${CODEBERG_TOKEN}@codeberg.org/${CI_REPO}.git" master
echo "Pushed updated flake.nix to master."
