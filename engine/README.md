# Evaluation engine bridge

The Python layer deliberately does not reproduce game formulas. The production
engine is expected at `engine/vendor/nerolis-lab`, pinned by commit, and will
call Neroli's Lab's `RP`, `calculatePokemonProduction`, and `calculateIv` APIs.

The vendor directory is ignored so Apache-2.0 source is not silently copied.
Install it locally with:

```sh
git clone https://github.com/nerolis-lab/nerolis-lab engine/vendor/nerolis-lab
git -C engine/vendor/nerolis-lab checkout <reviewed-commit>
```

Until this bridge is installed, `import-json` accepts externally computed
Lv60/Lv80 scores. Missing scores are fail-safe: the individual becomes
`protected`, never `send`.
