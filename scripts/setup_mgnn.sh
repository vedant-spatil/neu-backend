#!/usr/bin/env bash
# Fetch the IBM Multi-GNN clone at a pinned commit (plan.md section 22).
#
# Usage (from anywhere):  backend/scripts/setup_mgnn.sh
# Target folder:          $MGNN_DIR if set, else backend/third_party/Multi-GNN (gitignored).
# Idempotent: an existing clone is checked out at the pinned commit, not re-cloned.
set -euo pipefail

MGNN_REPO="https://github.com/IBM/Multi-GNN.git"
MGNN_COMMIT="252b0252afca109d1d216c411c59ff70753b25fc"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${MGNN_DIR:-$BACKEND_ROOT/third_party/Multi-GNN}"

if [ ! -d "$TARGET/.git" ]; then
  mkdir -p "$(dirname "$TARGET")"
  git clone "$MGNN_REPO" "$TARGET"
fi

git -C "$TARGET" fetch --quiet origin
git -C "$TARGET" checkout --quiet "$MGNN_COMMIT"

# Local edits in the clone (for example a changed model_settings.json) are not part of the pin.
if [ -n "$(git -C "$TARGET" status --porcelain --untracked-files=no)" ]; then
  echo "warning: $TARGET has local changes to tracked files; they are not part of the pinned commit" >&2
fi

echo "Multi-GNN at $(git -C "$TARGET" rev-parse --short HEAD) in $TARGET"
