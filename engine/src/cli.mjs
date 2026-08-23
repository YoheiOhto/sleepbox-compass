import fs from 'node:fs';
import {
  berryPowerForLevel, CarrySizeUtils, COMPLETE_POKEDEX, DEFAULT_ISLAND,
  emptyIngredientInventoryFloat, getBerry, getIngredient, getNature, getSubskill,
  MIN_POT_SIZE, OPTIMAL_POKEDEX, parseTime, RP
} from '../vendor/nerolis-lab/common/dist/index.mjs';
import { calculatePokemonProduction, calculateTeam } from '../vendor/nerolis-lab/backend/dist/services/api-service/production/production-service.js';
import { defaultUserRecipes } from '../vendor/nerolis-lab/backend/dist/services/simulation-service/team-simulator/cooking-state/cooking-utils.js';

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
const toInstance = (raw, level = raw.level, evolve = true) => ({
  pokemon: evolve ? finalEvolution(byName(COMPLETE_POKEDEX, raw.species))
                  : byName(COMPLETE_POKEDEX, raw.species),
  level,
  ribbon: raw.ribbon ?? 0,
  skillLevel: raw.skillLevel,
  nature: getNature(raw.nature),
  subskills: raw.subskills.map(([name, unlock]) => ({level: unlock, subskill: getSubskill(name)})),
  ingredients: raw.ingredients.map(([name, amount], index) => ({
    level: [1, 30, 60][index], amount, ingredient: getIngredient(name)
  })),
  sneakySnacking: false,
  version: 1, externalId: '', saved: false, shiny: false, gender: 'N', name: ''
});
const verify = () => ({results: input.instances.map(({uid, instance, displayedSp}) => {
  const computedSp = new RP(toInstance(instance, instance.level, false)).calc();
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
const simulateInstanceEnergy = (raw, level, islandBerries) => {
  const pokemon=finalEvolution(byName(COMPLETE_POKEDEX,raw.species));
  const ingredientSet=raw.ingredients.map(x=>x[0]);
  const stats={level,ribbon:raw.ribbon??0,nature:getNature(raw.nature),
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

const activeSubskills = (raw, level) => new Set(raw.subskills.filter(x => x[1] <= level).map(x => x[0]));
const skillLevelAt = (raw, pokemon, level) => Math.min(pokemon.skill.maxLevel,
  raw.skillLevel + raw.subskills.filter(([,unlock])=>unlock>raw.level && unlock<=level)
    .reduce((sum,[name])=>sum+(name==='Skill Level Up M'?2:name==='Skill Level Up S'?1:0),0));
const toTeamMember = (uid, raw, level) => {
  const pokemon = finalEvolution(byName(COMPLETE_POKEDEX, raw.species));
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
      skillLevel: skillLevelAt(raw,pokemon,level),
      subskills, externalId: uid, sneakySnacking: false
    }
  };
};
const teamSettings = (name, berries) => ({
  bedtime: parseTime('22:00'), wakeup: parseTime('06:00'), camp: false, includeCooking: false,
  stockpiledIngredients: emptyIngredientInventoryFloat(), potSize: MIN_POT_SIZE,
  island: {...DEFAULT_ISLAND, name, berries: berries.map(getBerry), areaBonus: 0}
});
const teamResult = (members, name, berries, iterations) => {
  const result = calculateTeam({settings: teamSettings(name, berries), members,
    userRecipes: defaultUserRecipes()}, iterations);
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
  return {total: rows.reduce((sum, row) => sum + row.energy, 0), members: rows};
};
const optimizeTeam = (instances, name, berries, mode) => {
  const levelFor = raw => mode === 'current' ? raw.level : Number(mode);
  const candidates = instances.map(({uid, instance}) => ({uid, raw: instance,
    member: toTeamMember(uid, instance, levelFor(instance)),
    additive: simulateInstanceEnergy(instance, levelFor(instance), berries).expected}));
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
  const final = teamResult(selected.map(x=>x.member), name, berries, iterations);
  const soloTotal = selected.reduce((sum,x) => sum + teamResult([x.member],name,berries,iterations).total,0);
  const marginal = Object.fromEntries(selected.map((x) => [x.uid,
    final.total-teamResult(selected.filter(y=>y!==x).map(y=>y.member),name,berries,iterations).total]));
  return {
    island:name, mode, total_energy:Math.round(final.total), synergy_gain:Math.round(final.total-soloTotal),
    provisional: selected.some(x=>!instances.find(y=>y.uid===x.uid)?.verified),
    optimizer:'team-swap-v1', cooking_included:false,
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
const teamEvaluate = () => ({
  engineVersion:'nerolis-lab@a033942b699854a80507e48b5246199afec17e01',
  plans:Object.entries(input.islands).flatMap(([name,berries]) =>
    ['current',...(input.anchors||[50,60,70,80]).map(String)].map(mode =>
      optimizeTeam(input.instances,name,berries,mode)).filter(Boolean))
});
const benchmark = () => ({benchmarks: OPTIMAL_POKEDEX.filter(p=>!p.evolvesInto.length).map(p=>({
  species:p.name,species_ja:input.names?.[p.name],island_scores:Object.fromEntries(
    Object.entries(input.islands).map(([name,berries])=>[name,safeSimulateEnergy(p,60,berries)])
      .filter(([,score])=>score).map(([name,score])=>[name,{60:score}]))
})).filter(p=>Object.keys(p.island_scores).length)});
const metadata = () => ({pokemon: Object.fromEntries(COMPLETE_POKEDEX.map(p => [p.name, {
  berry: p.berry.name,
  ingredients: [p.ingredient0, p.ingredient30, p.ingredient60].map((choices, index) => ({
    level: [1, 30, 60][index],
    choices: choices.filter(x => x.amount > 0).map(x => [x.ingredient.name, x.amount])
  }))
}]))});
const scoreReferences = () => {
  const anchors = input.anchors || [50, 60, 70, 80];
  const rows = OPTIMAL_POKEDEX.filter(p=>!p.evolvesInto.length).map(p=>{
    const raw={species:p.name,level:60,nature:idealNature(p.specialty),
      subskills:idealSubskills(p.specialty).map((name,i)=>[name,[10,25,50,75,100][i]]),
      ingredients:[p.ingredient0[0],p.ingredient30[0],p.ingredient60[0]].map(x=>[x.ingredient.name,x.amount]),
      mainSkill:p.skill.name,skillLevel:p.skill.maxLevel};
    return Object.fromEntries(anchors.map(level=>{const rp=new RP(toInstance(raw,level,false));return [level,
      {berry:rp.miscFactor*rp.berryFactor,ingredient:rp.miscFactor*rp.ingredientFactor,
       skill:rp.miscFactor*rp.skillFactor}]}));
  });
  const percentile=(values,p=.9)=>{const xs=values.filter(Number.isFinite).sort((a,b)=>a-b);return xs[Math.min(xs.length-1,Math.floor(xs.length*p))]};
  return {percentile:90,references:Object.fromEntries(anchors.map(level=>[level,
    Object.fromEntries(['berry','ingredient','skill'].map(role=>[role,Math.round(percentile(rows.map(x=>x[level][role])))]))]))};
};

process.stdout.write(JSON.stringify(input.mode === 'verify' ? verify()
  : input.mode === 'benchmark' ? benchmark()
  : input.mode === 'team-evaluate' ? teamEvaluate()
  : input.mode === 'metadata' ? metadata()
  : input.mode === 'score-references' ? scoreReferences()
  : evaluate()));
