#!/bin/sh
# Recalculate real data (verify/evaluate/decide/render) and start the local
# web server in one command. See README.md "起動（ワンコマンド）".
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

BOX="$ROOT/.venv/bin/pokesleep-box"
if [ ! -x "$BOX" ]; then
  echo "pokesleep-box が未インストールです。先に実行してください: python3 -m pip install -e ." >&2
  exit 1
fi

ENGINE="$ROOT/engine/bin/pokesleep-engine"
if [ ! -x "$ENGINE" ]; then
  echo "計算エンジンが未ビルドです。先に実行してください: ./engine/install.sh" >&2
  exit 1
fi

DB="${POKESLEEP_DB:-data/box.sqlite}"
SITE="${POKESLEEP_SITE:-site/private}"

if [ "${1:-}" = "--serve-only" ]; then
  echo "==> serve $SITE (再計算スキップ)"
  exec "$BOX" --db "$DB" serve --site "$SITE"
fi

echo "==> verify"
"$BOX" --db "$DB" verify
echo "==> evaluate"
"$BOX" --db "$DB" evaluate
echo "==> seed-evaluate"
"$BOX" --db "$DB" seed-evaluate
echo "==> main-seed-evaluate"
"$BOX" --db "$DB" main-seed-evaluate
echo "==> decide"
"$BOX" --db "$DB" decide --keep-top-n 2
echo "==> render"
"$BOX" --db "$DB" render --out "$SITE"
echo "==> serve $SITE"
exec "$BOX" --db "$DB" serve --site "$SITE"
