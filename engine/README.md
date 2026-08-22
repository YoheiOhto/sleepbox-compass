# Evaluation engine bridge

The Python layer deliberately does not reproduce game formulas. The production
engine is expected at `engine/vendor/nerolis-lab`, pinned by commit, and will
call Neroli's Lab's `RP`, `calculatePokemonProduction`, and `calculateIv` APIs.

The vendor directory is ignored so Apache-2.0 source is not silently copied.
Install and build the pinned version locally with:

```sh
git clone https://github.com/nerolis-lab/nerolis-lab engine/vendor/nerolis-lab
git -C engine/vendor/nerolis-lab checkout a033942b699854a80507e48b5246199afec17e01
cd engine/vendor/nerolis-lab/common
npm run build
```

`engine/bin/pokesleep-engine` then exposes batched `verify` and `evaluate`
modes. Evaluation uses Neroli’s Lab's RP components at Lv50/60/70/80 and never
copies its formulas into Python. Until the bridge is built, `import-json`
accepts externally computed scores. Missing scores or non-strict SP matches are
fail-safe: the individual becomes `protected`, never `send`.
