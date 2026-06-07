#!/bin/bash
# Post-build script to restructure Flutter web output for cache busting
#
# This script:
# 1. Uses the 'RELEASE_HASH' env var as the release hash
# 2. Moves all build artifacts (except index.html) into a hashed subdirectory
# 3. Updates index.html to load resources from the hashed directory
#
# Usage: ./post-build-cache-bust.sh [build_dir]
#   build_dir: Path to Flutter build/web directory (default: /app/build/web)
#
# Environment:
#   RELEASE_HASH: Required. The git tag used for cache busting.

set -e

BUILD_DIR="${1:-/app/build/web}"

# Helper function to run sed with before/after verification
# Usage: verify_sed <file> <pattern> <replacement> <verify_after>
verify_sed() {
    local file="$1"
    local pattern="$2"
    local replacement="$3"
    local verify_after="$4"

    if ! grep -q "$pattern" "$file"; then
        echo "Error: Pattern '$pattern' not found in $file"
        echo "This may indicate Flutter's output format has changed."
        exit 1
    fi

    sed -i "s|$pattern|$replacement|g" "$file"

    if ! grep -q "$verify_after" "$file"; then
        echo "Error: Expected result '$verify_after' not found in $file after sed"
        echo "The sed replacement may have failed."
        exit 1
    fi

    echo "  Verified: $file"
}

if [ -z "$RELEASE_HASH" ]; then
    echo "Error: RELEASE_HASH environment variable is not set"
    exit 1
fi

echo "Using release hash: $RELEASE_HASH"
echo "Build directory: $BUILD_DIR"

# Verify build directory exists
if [ ! -d "$BUILD_DIR" ]; then
    echo "Error: Build directory does not exist: $BUILD_DIR"
    exit 1
fi

# Verify index.html exists
if [ ! -f "$BUILD_DIR/index.html" ]; then
    echo "Error: index.html not found in $BUILD_DIR"
    exit 1
fi

# Create the hashed directory
HASH_DIR="$BUILD_DIR/$RELEASE_HASH"
mkdir -p "$HASH_DIR"

echo "Moving assets to $HASH_DIR..."

# Move all files except index.html, manifest.json, favicon.png, and icons to the hash directory
# Keep these at root for PWA/browser compatibility
for item in "$BUILD_DIR"/*; do
    basename=$(basename "$item")
    case "$basename" in
        index.html|manifest.json|favicon.png|icons|"$RELEASE_HASH")
            echo "  Keeping at root: $basename"
            ;;
        *)
            echo "  Moving to hash dir: $basename"
            mv "$item" "$HASH_DIR/"
            ;;
    esac
done

echo "Updating flutter_bootstrap.js..."

# Modify flutter_bootstrap.js to pass config to the loader
# This tells Flutter where to find all resources:
# - entrypointBaseUrl: where to find main.dart.js during load
# - assetBase: where to find assets at runtime (fonts, images, JSON manifests)
# - canvasKitBaseUrl: where to find canvaskit (needed because useLocalCanvasKit:true doesn't respect entrypointBaseUrl)
verify_sed "$HASH_DIR/flutter_bootstrap.js" \
    "_flutter.loader.load()" \
    "_flutter.loader.load({config: {entrypointBaseUrl: \"/$RELEASE_HASH/\", assetBase: \"/$RELEASE_HASH/\", canvasKitBaseUrl: \"/$RELEASE_HASH/canvaskit/\"}})" \
    "entrypointBaseUrl"

echo "Updating index.html..."

# Update index.html to use the hashed directory

# Ensure base href stays as /
verify_sed "$BUILD_DIR/index.html" \
    '<base href="[^"]*">' \
    '<base href="/">' \
    '<base href="/">'

# Replace flutter_bootstrap.js reference
verify_sed "$BUILD_DIR/index.html" \
    'src="flutter_bootstrap.js"' \
    "src=\"/$RELEASE_HASH/flutter_bootstrap.js\"" \
    "$RELEASE_HASH/flutter_bootstrap.js"

# Update splash image paths (they're now in the hash directory)
# Note: This one may not always be present, so we check if splash exists first
if grep -q "splash/img/" "$BUILD_DIR/index.html"; then
    verify_sed "$BUILD_DIR/index.html" \
        "splash/img/" \
        "/$RELEASE_HASH/splash/img/" \
        "$RELEASE_HASH/splash/img/"
else
    echo "  Skipped: No splash/img/ references found in index.html"
fi

# Write the release hash to a file for nginx to use (optional, for debugging)
echo "$RELEASE_HASH" > "$BUILD_DIR/.release-hash"

echo "Cache busting setup complete!"
echo "  - Release hash: $RELEASE_HASH"
echo "  - Assets moved to: $HASH_DIR"
echo "  - index.html updated to reference hashed assets"

# List the final structure
echo ""
echo "Final directory structure:"
ls -la "$BUILD_DIR"
echo ""
echo "Hash directory contents:"
ls -la "$HASH_DIR" | head -20
