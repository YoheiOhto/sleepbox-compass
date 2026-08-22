#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENDOR="$ROOT/engine/vendor/nerolis-lab"
COMMIT=a033942b699854a80507e48b5246199afec17e01
if [ ! -d "$VENDOR/.git" ]; then
  git clone https://github.com/nerolis-lab/nerolis-lab.git "$VENDOR"
fi
git -C "$VENDOR" fetch origin "$COMMIT"
git -C "$VENDOR" checkout --detach "$COMMIT"
cd "$VENDOR/common"
npm run build
echo "Neroli’s Lab engine is ready: $COMMIT"
