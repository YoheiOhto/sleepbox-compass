import fs from 'node:fs';
import {
  berryPowerForLevel, CarrySizeUtils, COMPLETE_POKEDEX, DEFAULT_ISLAND,
  emptyIngredientInventoryFloat, getBerry, getIngredient, getNature, getSubskill,
  MIN_POT_SIZE, OPTIMAL_POKEDEX, parseTime, RP
} from '../vendor/nerolis-lab/common/dist/index.mjs';
import { calculatePokemonProduction, calculateTeam } from '../vendor/nerolis-lab/backend/dist/services/api-service/production/production-service.js';
import { defaultUserRecipes } from '../vendor/nerolis-lab/backend/dist/services/simulation-service/team-simulator/cooking-state/cooking-utils.js';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const recipeLevel = Math.max(1, Math.min(60, input.recipeLevel ?? 1));
const userRecipes = Object.fromEntries(Object.entries(defaultUserRecipes()).map(([type, recipes]) =>
  [type, recipes.map(recipe => ({...recipe, level: recipeLevel}))]));
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
const projectedSkillLevel=(raw,pokemon,evolve)=>Math.min(pokemon.skill.maxLevel,
  raw.skillLevel+(evolve?(byName(COMPLETE_POKEDEX,raw.species).remainingEvolutions||0):0));
const skillLevelFor=(raw,pokemon,level,evolve)=>Math.min(pokemon.skill.maxLevel,
  projectedSkillLevel(raw,pokemon,evolve)+raw.subskills.filter(([,unlock])=>unlock>raw.level&&unlock<=level)
    .reduce((sum,[name])=>sum+(name==='Skill Level Up M'?2:name==='Skill Level Up S'?1:0),0));
const toInstance = (raw, level = raw.level, evolve = true) => {const pokemon=evolve?finalEvolution(byName(COMPLETE_POKEDEX,raw.species)):byName(COMPLETE_POKEDEX,raw.species);return ({
  pokemon,
  level,
  ribbon: raw.ribbon ?? 0,
  skillLevel: skillLevelFor(raw,pokemon,level,evolve),
  nature: getNature(raw.nature),
  subskills: raw.subskills.map(([name, unlock]) => ({level: unlock, subskill: getSubskill(name)})),
  ingredients: raw.ingredients.map(([name, amount], index) => ({
    level: [1, 30, 60][index], amount, ingredient: getIngredient(name)
  })),
  sneakySnacking: false,
  version: 1, externalId: '', saved: false, shiny: false, gender: 'N', name: ''
})};
const verify = () => ({results: input.instances.map(({uid, instance, displayedSp}) => {
  const computedSp = new RP(toInstance(instance, instance.level, false)).calc();
  const diff = computedSp - displayedSp;
  const strict = instance.level < input.strictBelowLevel;
  const match = strict ? diff === 0 : Math.abs(diff) <= input.tolerance;
  return {uid, computedSp, diff, match, mode: strict ? 'strict' : (match ? 'tolerant' : 'failed')};
})});
const evaluate = () => {
  const total = input.instances.length;
  const results = input.instances.map(({uid, instance}, index) => {
    const row = {uid, scores: Object.fromEntries(input.anchors.map(level => {
      const rp = new RP(toInstance(instance, level));
      return [level, {berry: rp.miscFactor * rp.berryFactor,
                      ingredient: rp.miscFactor * rp.ingredientFactor,
                      skill: rp.miscFactor * rp.skillFactor}];
    })), energyScores: input.islands ? Object.fromEntries(Object.entries(input.islands).map(([name,berries]) =>
      [name,Object.fromEntries([['current',instance.level,false],...input.anchors.map(x=>[String(x),x,true])]
        .map(([mode,level,evolve])=>[mode,simulateInstanceEnergy(instance,level,berries,evolve)]))])) : undefined};
    process.stderr.write(`PROGRESS evaluate ${index + 1} ${total}\n`);
    return row;
  });
  return {engineVersion: 'nerolis-lab@a033942b699854a80507e48b5246199afec17e01',
    valuationHash: 'rp-components-v1', results};
};

const idealSubskills = specialty => specialty === 'berry'
  ? ['Berry Finding S','Helping Speed M','Helping Speed S','Helping Bonus','Skill Trigger M']
  : specialty === 'ingredient'
    ? ['Ingredient Finder M','Helping Speed M','Ingredient Finder S','Inventory Up L','Helping Speed S']
    : ['Skill Trigger M','Helping Speed M','Skill Trigger S','Helping Speed S','Helping Bonus'];
const idealNature = specialty => specialty === 'berry' ? 'Adamant' : specialty === 'ingredient' ? 'Quiet' : 'Careful';
const simulateEnergy = (pokemon, level, islandBerries) => {
  const ingredientSet = [pokemon.ingredient0[0], pokemon.ingredient30[0], pokemon.ingredient60[0]]
    .map(x => x.ingredient.name);
  const stats = {level, ribbon:0, nature:getNature(idealNature(pokemon.specialty)),
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
const safeSimulateEnergy = (pokemon, level, islandBerries) => {
  try { return simulateEnergy(pokemon, level, islandBerries); }
  catch { return null; }
};
const simulateInstanceEnergy = (raw, level, islandBerries, evolve=true) => {
  const basePokemon=byName(COMPLETE_POKEDEX,raw.species);
  const pokemon=evolve?finalEvolution(basePokemon):basePokemon;
  const ingredientSet=raw.ingredients.map(x=>x[0]);
  const stats={level,ribbon:raw.ribbon??0,nature:getNature(raw.nature),
    subskills:new Set(raw.subskills.filter(x=>x[1]<=level).map(x=>x[0])),skillLevel:skillLevelFor(raw,pokemon,level,evolve),
    inventoryLimit:CarrySizeUtils.calculateCarrySize({baseWithEvolutions:CarrySizeUtils.baseCarrySize(pokemon),subskillsLevelLimited:new Set(raw.subskills.filter(x=>x[1]<=level).map(x=>x[0])),ribbon:raw.ribbon??0,camp:false}),e4eProcs:0,e4eLevel:1,cheer:0,extraHelpful:0,
    helperBoostProcs:0,helperBoostUnique:0,helperBoostLevel:1,helpingBonus:0,camp:false,
    erb:0,incense:false,mainBedtime:parseTime('22:00'),mainWakeup:parseTime('06:00'),maxPotSize:15};
  const result=calculatePokemonProduction(pokemon,stats,ingredientSet,false,input.iterations||500);
  const berry=result.summary.totalProduce.berries.reduce((sum,x)=>sum+x.amount*berryPowerForLevel(x.berry,x.level)*(islandBerries.includes(x.berry.name)?2:1),0);
  const ingredient=result.summary.totalProduce.ingredients.reduce((sum,x)=>sum+x.amount*x.ingredient.value,0);
  const skill=result.summary.skillStrengthValue||0,expected=berry+ingredient+skill,spread=skill*.25;
  return {berry:Math.round(berry),ingredient:Math.round(ingredient),direct_skill:Math.round(skill),expected:Math.round(expected),
          low:Math.round(expected-spread),high:Math.round(expected+spread)};
};

const activeSubskills = (raw, level) => new Set(raw.subskills.filter(x => x[1] <= level).map(x => x[0]));
const toTeamMember = (uid, raw, level, evolve=true) => {
  const basePokemon=byName(COMPLETE_POKEDEX,raw.species);
  const pokemon=evolve?finalEvolution(basePokemon):basePokemon;
  const subskills = activeSubskills(raw, level);
  return {
    pokemonWithIngredients: {
      pokemon,
      ingredientList: raw.ingredients.map(([name, amount]) => ({ingredient: getIngredient(name), amount}))
    },
    settings: {
      carrySize: CarrySizeUtils.calculateCarrySize({
        baseWithEvolutions: CarrySizeUtils.baseCarrySize(pokemon), subskillsLevelLimited: subskills,
        ribbon: raw.ribbon ?? 0, camp: false
      }),
      level, ribbon: raw.ribbon ?? 0, nature: getNature(raw.nature),
      skillLevel: skillLevelFor(raw,pokemon,level,evolve),
      subskills, externalId: uid, sneakySnacking: false
    }
  };
};
const teamSettings = (name, berries) => ({
  bedtime: parseTime('22:00'), wakeup: parseTime('06:00'), camp: false, includeCooking: true,
  stockpiledIngredients: emptyIngredientInventoryFloat(), potSize: MIN_POT_SIZE,
  island: {...DEFAULT_ISLAND, name, berries: berries.map(getBerry), areaBonus: 0}
});
const teamResult = (members, name, berries, iterations) => {
  const result = calculateTeam({settings: teamSettings(name, berries), members,
    userRecipes}, iterations);
  const rows = result.members.map(member => ({
    uid: member.externalId,
    berry: member.strength.berries.total,
    direct_skill: member.strength.skill.total,
    energy: member.strength.berries.total + member.strength.skill.total,
    skill_procs: member.skillProcs,
    recovery: member.advanced.teamSupport.energy,
    team_energy_support: member.advanced.teamSupport.energy,
    team_help_support: member.advanced.teamSupport.helps
  }));
  const cookingTypes=['curry','salad','dessert'];
  const cooking=result.cooking?cookingTypes.reduce((sum,type)=>sum+result.cooking[type].weeklyStrength/7,0)/cookingTypes.length:0;
  return {total: rows.reduce((sum, row) => sum + row.energy, 0)+cooking,
    cooking, recipe_level:recipeLevel, members: rows};
};
const optimizeTeam = (instances, name, berries, mode) => {
  const levelFor = raw => mode === 'current' ? raw.level : Number(mode);
  const evolve=mode!=='current';
  const candidates = instances.map(({uid, instance}) => {const production=simulateInstanceEnergy(instance,levelFor(instance),berries,evolve);return {uid,raw:instance,
    member:toTeamMember(uid,instance,levelFor(instance),evolve),ingredient:production.ingredient||0,
    additive:production.expected}});
  if (!candidates.length) return null;
  const size = Math.min(5, candidates.length), searchIterations = input.teamSearchIterations ?? 80;
  let selected = [...candidates].sort((a,b) => b.additive-a.additive).slice(0,size);
  const score = team => teamResult(team.map(x=>x.member), name, berries, searchIterations).total;
  let bestScore = score(selected), improved = true;
  while (improved) {
    improved = false;
    let bestTeam = selected;
    const outside = candidates.filter(x => !selected.includes(x));
    for (let i=0; i<selected.length; i++) for (const candidate of outside) {
      const trial = selected.map((x,j) => j===i ? candidate : x);
      const trialScore = score(trial);
      if (trialScore > bestScore * 1.002) { bestScore=trialScore; bestTeam=trial; improved=true; }
    }
    selected = bestTeam;
  }
  const iterations = input.teamIterations ?? 500;
  const rawFinal = teamResult(selected.map(x=>x.member), name, berries, iterations);
  const ingredientByUid=Object.fromEntries(selected.map(x=>[x.uid,x.ingredient]));
  const final={...rawFinal,members:rawFinal.members.map(x=>({...x,ingredient:ingredientByUid[x.uid]||0}))};
  const soloTotal = selected.reduce((sum,x) => sum + teamResult([x.member],name,berries,iterations).total,0);
  const marginal = Object.fromEntries(selected.map((x) => [x.uid,
    final.total-teamResult(selected.filter(y=>y!==x).map(y=>y.member),name,berries,iterations).total]));
  return {
    island:name, mode, total_energy:Math.round(final.total), synergy_gain:Math.round(final.total-soloTotal),
    provisional: selected.some(x=>!instances.find(y=>y.uid===x.uid)?.verified),
    cooking:Math.round(final.cooking),recipe_level:recipeLevel,
    optimizer:'team-swap-v3-cooking-and-skills', cooking_included:true, recipe_bonus_included:true,
    members: final.members.sort((a,b)=>marginal[b.uid]-marginal[a.uid]).map(row => ({
      ...Object.fromEntries(Object.entries(row).map(([k,v])=>[k, k==='uid'?v:Math.round(v)])),
      marginal:Math.round(marginal[row.uid]),
      subskills:[...activeSubskills(selected.find(x=>x.uid===row.uid).raw,
        levelFor(selected.find(x=>x.uid===row.uid).raw))],
      utility_subskills:[...activeSubskills(selected.find(x=>x.uid===row.uid).raw,
        levelFor(selected.find(x=>x.uid===row.uid).raw))]
        .filter(x=>['Research EXP Bonus','Sleep EXP Bonus','Dream Shard Bonus'].includes(x))
    }))
  };
};
const teamEvaluate = () => {
  const islandEntries = Object.entries(input.islands);
  const modes = ['current', ...(input.anchors || [50, 60, 70, 80]).map(String)];
  const total = islandEntries.length * modes.length;
  let done = 0;
  const plans = islandEntries.flatMap(([name, berries]) =>
    modes.map(mode => {
      const plan = optimizeTeam(input.instances, name, berries, mode);
      done += 1;
      process.stderr.write(`PROGRESS team-evaluate ${done} ${total}\n`);
      return plan;
    }).filter(Boolean));
  return {engineVersion: 'nerolis-lab@a033942b699854a80507e48b5246199afec17e01', plans};
};
const customTeam = () => {
  if (!input.island?.name || !Array.isArray(input.island.berries)) throw new Error('custom-team requires island');
  if (!Array.isArray(input.instances) || input.instances.length !== 5) throw new Error('custom-team requires exactly 5 instances');
  return {engineVersion:'nerolis-lab@a033942b699854a80507e48b5246199afec17e01',
    plan:optimizeTeam(input.instances,input.island.name,input.island.berries,String(input.teamMode||'current'))};
};
const benchmark = () => ({benchmarks: OPTIMAL_POKEDEX.filter(p=>!p.evolvesInto.length).map(p=>({
  species:p.name,species_ja:input.names?.[p.name],berry:p.berry.name,island_scores:Object.fromEntries(
    Object.entries(input.islands).map(([name,berries])=>[name,safeSimulateEnergy(p,60,berries)])
      .filter(([,score])=>score).map(([name,score])=>[name,{60:score}]))
})).filter(p=>Object.keys(p.island_scores).length)});
const metadata = () => ({pokemon: Object.fromEntries(COMPLETE_POKEDEX.map(p => [p.name, {
  berry: p.berry.name,
  main_skill: p.skill.name,
  pokedex_number: p.pokedexNumber,
  ingredients: [p.ingredient0, p.ingredient30, p.ingredient60].map((choices, index) => ({
    level: [1, 30, 60][index],
    choices: choices.filter(x => x.amount > 0).map(x => [x.ingredient.name, x.amount])
  }))
}]))});
const scoreReferences = () => {
  const anchors = input.anchors || [50, 60, 70, 80];
  const pool = OPTIMAL_POKEDEX.filter(p=>!p.evolvesInto.length);
  const idealRaw = (p, specialty) => ({species:p.name,level:60,nature:idealNature(specialty),
    subskills:idealSubskills(specialty).map((name,i)=>[name,[10,25,50,75,100][i]]),
    ingredients:[p.ingredient0[0],p.ingredient30[0],p.ingredient60[0]].map(x=>[x.ingredient.name,x.amount]),
    mainSkill:p.skill.name,skillLevel:p.skill.maxLevel});
  const bySpecies = pool.map((p, index) => {
    // Score each role from its OWN role-optimized ideal individual (best
    // nature/subskills for that role), instead of one individual built for
    // the species' single labeled specialty. A skill that pays off in a
    // different role (e.g. a berry-boosting main skill on a "skill"
    // specialist) would otherwise be scored on a nature/subskill spread that
    // was never meant to maximize that role, underrating it.
    const perRole = {berry: idealRaw(p, 'berry'), ingredient: idealRaw(p, 'ingredient'),
                     skill: idealRaw(p, 'skill')};
    const scores = Object.fromEntries(anchors.map(level => {
      const rpBerry = new RP(toInstance(perRole.berry, level, false));
      const rpIngredient = new RP(toInstance(perRole.ingredient, level, false));
      const rpSkill = new RP(toInstance(perRole.skill, level, false));
      return [level, {berry: rpBerry.miscFactor * rpBerry.berryFactor,
                      ingredient: rpIngredient.miscFactor * rpIngredient.ingredientFactor,
                      skill: rpSkill.miscFactor * rpSkill.skillFactor}];
    }));
    process.stderr.write(`PROGRESS score-references ${index + 1} ${pool.length}\n`);
    return {species: p.name, scores};
  });
  const percentile=(values,p=.9)=>{const xs=values.filter(Number.isFinite).sort((a,b)=>a-b);return xs[Math.min(xs.length-1,Math.floor(xs.length*p))]};
  return {percentile:90,
    references:Object.fromEntries(anchors.map(level=>[level,
      Object.fromEntries(['berry','ingredient','skill'].map(role=>[role,Math.round(percentile(bySpecies.map(x=>x.scores[level][role])))]))])),
    // Per-species ideal-individual RP components, so callers can score each
    // species on the same 0-100 scale used for owned individuals instead of
    // only the aggregate percentile above.
    species: Object.fromEntries(bySpecies.map(x=>[x.species, x.scores]))};
};

process.stdout.write(JSON.stringify(input.mode === 'verify' ? verify()
  : input.mode === 'benchmark' ? benchmark()
  : input.mode === 'team-evaluate' ? teamEvaluate()
  : input.mode === 'custom-team' ? customTeam()
  : input.mode === 'metadata' ? metadata()
  : input.mode === 'score-references' ? scoreReferences()
  : evaluate()));
