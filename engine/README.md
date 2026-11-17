# Evaluation engine bridge

The Python layer deliberately does not reproduce game formulas. The production
engine is expected at `engine/vendor/nerolis-lab`, pinned by commit, and will
call Neroli's Lab's `RP`, `calculatePokemonProduction`, and `calculateIv` APIs.

The vendor directory is ignored so Apache-2.0 source is not silently copied.
Install and build the pinned version locally with:

```sh
./engine/install.sh
```

The script checks out the commit pinned in `engine/install.sh` (Neroli's Lab
2.74.1, 2026-11-16), builds `common`, and emits the `backend` JavaScript. It
uses `.cache/node` when present and skips upstream's Bun-only type check, which
produces no files the engine needs. The bridge's `metadata` mode reports each
species' berry, specialty, base main skill (variants such as
`Psystrike (Berry Zone)` are reduced to `Berry Zone`) and ingredient options.

`engine/bin/pokesleep-engine` then exposes batched `verify`, `evaluate`, and
`benchmark` modes. Evaluation uses Neroli’s Lab's simulation for berry,
ingredient/cooking energy, direct-strength energy, team skill interactions,
and RP components at Lv50/60/70/80. Cooking defaults to the average of all
three recipe categories at recipe level 1, and the bridge never
copies its formulas into Python. Until the bridge is built, `import-json`
accepts externally computed scores. Missing scores or non-strict SP matches are
fail-safe: the individual becomes `protected`, never `send`.
