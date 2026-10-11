#!/usr/bin/env bash
# Build the macOS helper. Run this ON the Mac — PaperDrop cannot cross-compile it.
#
#   bash build.sh              build into ./paperdrop-set
#   bash build.sh --install    also copy it to ~/Library/Application Support/PaperDrop/
#
# ("bash build.sh" works even if the executable bit was lost, e.g. after
# unpacking the source tarball; "./build.sh" is the same when the bit is set.)
#
# Requires the Swift toolchain and the macOS SDK (Command Line Tools are enough:
#   xcode-select --install).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$HERE/Sources/paperdrop-set/main.swift"
OUTPUT="$HERE/paperdrop-set"
TARGET_DIR="${HOME}/Library/Application Support/PaperDrop"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "build.sh: this must run on macOS (uname -s = $(uname -s))." >&2
  exit 1
fi

if ! command -v swiftc >/dev/null 2>&1; then
  echo "build.sh: swiftc not found. Install the Command Line Tools: xcode-select --install" >&2
  exit 1
fi

echo "swiftc --version:"
swiftc --version

swiftc -O -framework AppKit -framework Foundation -o "$OUTPUT" "$SOURCE"
chmod +x "$OUTPUT"
echo "built: $OUTPUT"

if [[ "${1:-}" == "--install" ]]; then
  mkdir -p "$TARGET_DIR"
  cp "$OUTPUT" "$TARGET_DIR/paperdrop-set"
  echo "installed: $TARGET_DIR/paperdrop-set"
  echo "PaperDrop finds it there automatically, or point apply.helper_path at it."
fi

cat <<'EOF'

Smoke test (safe: it applies the current cached image if one exists):
  ./paperdrop-set screens
  ./paperdrop-set apply "$HOME/.cache/paperdrop/current.jpg" --fill
  ./paperdrop-set current

If AppKit reports no screens, you are not in a graphical (Aqua) session.
EOF
