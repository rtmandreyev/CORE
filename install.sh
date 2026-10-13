#!/bin/sh
# Make `core` available on PATH by linking ~/.local/bin/core to this checkout.
# Idempotent. Never edits shell startup files; it tells you if PATH needs a line.
set -e

repo="$(cd "$(dirname "$0")" && pwd)"
bin_dir="${CORE_BIN_DIR:-$HOME/.local/bin}"
target="$bin_dir/core"

python_ok=""
for candidate in "${CORE_PYTHON:-}" python3.14 python3.13 python3.12 python3.11 \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    [ -n "$candidate" ] || continue
    interpreter="$(command -v "$candidate" 2>/dev/null)" || continue
    if "$interpreter" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        python_ok="$interpreter"
        break
    fi
done

if [ -z "$python_ok" ]; then
    echo "CORE needs Python 3.11 or newer (macOS ships 3.9)." >&2
    echo "Install one, for example: brew install python@3.12 — then run ./install.sh again." >&2
    exit 1
fi

mkdir -p "$bin_dir"
if [ -e "$target" ] && [ ! -L "$target" ]; then
    echo "$target exists and is not a link; leaving it alone." >&2
    exit 1
fi
ln -sfn "$repo/bin/core" "$target"
echo "Linked $target -> $repo/bin/core (Python: $python_ok)"

case ":$PATH:" in
    *":$bin_dir:"*) echo "Ready. Try: core doctor" ;;
    *) echo "Add $bin_dir to PATH, e.g. in ~/.zshrc:  export PATH=\"$bin_dir:\$PATH\"" ;;
esac
