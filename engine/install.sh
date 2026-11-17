#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENDOR="$ROOT/engine/vendor/nerolis-lab"
# Prefer the project-local Node.js used by engine/bin/pokesleep-engine.
if [ -x "$ROOT/.cache/node/bin/npm" ]; then
  PATH="$ROOT/.cache/node/bin:$PATH"
fi
# Override with NEROLI_COMMIT=<sha> to try another upstream commit.
COMMIT=${NEROLI_COMMIT:-8a6566c5e824db51e9939e2929d3734c38de11f8}
if [ ! -d "$VENDOR/.git" ]; then
  git clone https://github.com/nerolis-lab/nerolis-lab.git "$VENDOR"
fi
git -C "$VENDOR" fetch origin "$COMMIT"
git -C "$VENDOR" checkout --detach "$COMMIT"
cd "$VENDOR/common"
npm run build
cd "$VENDOR/backend"
npm install
# Emit JavaScript only. Upstream `npm run build` ends with a Bun type check
# (`bun x tsc --noEmit`) that produces no files the engine needs.
npx tsc --project tsconfig.build.json
npx tsc-alias
echo "Neroli’s Lab engine is ready: $COMMIT"
