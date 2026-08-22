import fs from 'node:fs';
import {
  berryPowerForLevel, CarrySizeUtils, COMPLETE_POKEDEX, INGREDIENTS, NATURES, OPTIMAL_POKEDEX,
  parseTime, RP, SUBSKILLS
} from '../vendor/nerolis-lab/common/dist/index.mjs';
import { calculatePokemonProduction } from '../vendor/nerolis-lab/backend/dist/services/api-service/production/production-service.js';

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
  })), energyScores: input.islands ? Object.fromEntries(Object.entries(input.islands).map(([name,berries]) =>
    [name,Object.fromEntries([['current',instance.level],...input.anchors.map(x=>[String(x),x])]
      .map(([mode,level])=>[mode,simulateInstanceEnergy(instance,level,berries)]))])) : undefined}))
});

const idealSubskills = specialty => specialty === 'berry'
  ? ['Berry Finding S','Helping Speed M','Helping Speed S','Helping Bonus','Skill Trigger M']
  : specialty === 'ingredient'
    ? ['Ingredient Finder M','Helping Speed M','Ingredient Finder S','Inventory Up L','Helping Speed S']
    : ['Skill Trigger M','Helping Speed M','Skill Trigger S','Helping Speed S','Helping Bonus'];
const idealNature = specialty => specialty === 'berry' ? 'Adamant' : specialty === 'ingredient' ? 'Quiet' : 'Careful';
const simulateEnergy = (pokemon, level, islandBerries) => {
  const ingredientSet = [pokemon.ingredient0[0], pokemon.ingredient30[0], pokemon.ingredient60[0]]
    .map(x => x.ingredient.name);
  const stats = {level, ribbon:0, nature:byName(NATURES,idealNature(pokemon.specialty)),
    subskills:new Set(idealSubskills(pokemon.specialty)), skillLevel:pokemon.skill.maxLevel,
    inventoryLimit:CarrySizeUtils.calculateCarrySize({baseWithEvolutions:CarrySizeUtils.baseCarrySize(pokemon),subskillsLevelLimited:new Set(idealSubskills(pokemon.specialty)),ribbon:0,camp:false}), e4eProcs:0,e4eLevel:1,cheer:0,extraHelpful:0,
    helperBoostProcs:0,helperBoostUnique:0,helperBoostLevel:1,helpingBonus:0,camp:false,
    erb:0,incense:false,mainBedtime:parseTime('22:00'),mainWakeup:parseTime('06:00'),maxPotSize:15};
  const result = calculatePokemonProduction(pokemon,stats,ingredientSet,false,input.iterations||500);
  const berry = result.summary.totalProduce.berries.reduce((sum,x) => sum + x.amount *
    berryPowerForLevel(x.berry,x.level) * (islandBerries.includes(x.berry.name)?2:1),0);
  const skill = result.summary.skillStrengthValue || 0, expected=berry+skill, spread=skill*.25;
  return {berry:Math.round(berry),direct_skill:Math.round(skill),expected:Math.round(expected),
          low:Math.round(expected-spread),high:Math.round(expected+spread)};
};
const simulateInstanceEnergy = (raw, level, islandBerries) => {
  const pokemon=finalEvolution(byName(COMPLETE_POKEDEX,raw.species));
  const ingredientSet=raw.ingredients.map(x=>x[0]);
  const stats={level,ribbon:raw.ribbon??0,nature:byName(NATURES,raw.nature),
    subskills:new Set(raw.subskills.filter(x=>x[1]<=level).map(x=>x[0])),skillLevel:raw.skillLevel,
    inventoryLimit:CarrySizeUtils.calculateCarrySize({baseWithEvolutions:CarrySizeUtils.baseCarrySize(pokemon),subskillsLevelLimited:new Set(raw.subskills.filter(x=>x[1]<=level).map(x=>x[0])),ribbon:raw.ribbon??0,camp:false}),e4eProcs:0,e4eLevel:1,cheer:0,extraHelpful:0,
    helperBoostProcs:0,helperBoostUnique:0,helperBoostLevel:1,helpingBonus:0,camp:false,
    erb:0,incense:false,mainBedtime:parseTime('22:00'),mainWakeup:parseTime('06:00'),maxPotSize:15};
  const result=calculatePokemonProduction(pokemon,stats,ingredientSet,false,input.iterations||500);
  const berry=result.summary.totalProduce.berries.reduce((sum,x)=>sum+x.amount*berryPowerForLevel(x.berry,x.level)*(islandBerries.includes(x.berry.name)?2:1),0);
  const skill=result.summary.skillStrengthValue||0,expected=berry+skill,spread=skill*.25;
  return {berry:Math.round(berry),direct_skill:Math.round(skill),expected:Math.round(expected),
          low:Math.round(expected-spread),high:Math.round(expected+spread)};
};
const benchmark = () => ({benchmarks: OPTIMAL_POKEDEX.filter(p=>!p.evolvesInto.length).map(p=>({
  species:p.name,species_ja:input.names?.[p.name],island_scores:Object.fromEntries(
    Object.entries(input.islands).map(([name,berries])=>[name,{60:simulateEnergy(p,60,berries)}]))
}))});
const metadata = () => ({pokemon: Object.fromEntries(COMPLETE_POKEDEX.map(p => [p.name, {
  berry: p.berry.name,
  ingredients: [p.ingredient0, p.ingredient30, p.ingredient60].map((choices, index) => ({
    level: [1, 30, 60][index],
    choices: choices.filter(x => x.amount > 0).map(x => [x.ingredient.name, x.amount])
  }))
}]))});

process.stdout.write(JSON.stringify(input.mode === 'verify' ? verify()
  : input.mode === 'benchmark' ? benchmark()
  : input.mode === 'metadata' ? metadata()
  : evaluate()));
