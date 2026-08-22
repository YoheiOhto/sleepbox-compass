import fs from 'node:fs';
import {
  COMPLETE_POKEDEX, INGREDIENTS, NATURES, RP, SUBSKILLS
} from '../vendor/nerolis-lab/common/dist/index.mjs';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const byName = (xs, name) => {
  const found = xs.find(x => x.name === name);
  if (!found) throw new Error(`Unknown engine value: ${name}`);
  return found;
};
const finalEvolution = pokemon => {
  let result = pokemon;
  while (result.evolvesInto?.length === 1) result = byName(COMPLETE_POKEDEX, result.evolvesInto[0]);
  return result;
};
const toInstance = (raw, level = raw.level) => ({
  pokemon: finalEvolution(byName(COMPLETE_POKEDEX, raw.species)),
  level,
  ribbon: raw.ribbon ?? 0,
  skillLevel: raw.skillLevel,
  nature: byName(NATURES, raw.nature),
  subskills: raw.subskills.map(([name, unlock]) => ({level: unlock, subskill: byName(SUBSKILLS, name)})),
  ingredients: raw.ingredients.map(([name, amount], index) => ({
    level: [1, 30, 60][index], amount, ingredient: byName(INGREDIENTS, name)
  })),
  sneakySnacking: false,
  version: 1, externalId: '', saved: false, shiny: false, gender: 'N', name: ''
});
const verify = () => ({results: input.instances.map(({uid, instance, displayedSp}) => {
  const computedSp = new RP(toInstance(instance)).calc();
  const diff = computedSp - displayedSp;
  const strict = instance.level < input.strictBelowLevel;
  const match = strict ? diff === 0 : Math.abs(diff) <= input.tolerance;
  return {uid, computedSp, diff, match, mode: strict ? 'strict' : (match ? 'tolerant' : 'failed')};
})});
const evaluate = () => ({
  engineVersion: 'nerolis-lab@a033942b699854a80507e48b5246199afec17e01',
  valuationHash: 'rp-components-v1',
  results: input.instances.map(({uid, instance}) => ({uid, scores: Object.fromEntries(input.anchors.map(level => {
    const rp = new RP(toInstance(instance, level));
    return [level, {berry: rp.miscFactor * rp.berryFactor,
                    ingredient: rp.miscFactor * rp.ingredientFactor,
                    skill: rp.miscFactor * rp.skillFactor}];
  }))}))
});

process.stdout.write(JSON.stringify(input.mode === 'verify' ? verify() : evaluate()));
