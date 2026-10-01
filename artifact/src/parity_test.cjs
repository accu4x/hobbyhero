// Parity (SPEC-artifact-v0 Phase 2; v0.2 2026-09-24):
//  1. JS engine vs Python on sampled games from every monthly model and the off-season
//     model (max difference <= 1e-6).
//  2. Consistency: every look-back game, recomputed from the stored morning-of team form
//     plus the actual starters' GSAx, matches the stored Python win % (rounding only).
const fs = require("fs"), path = require("path");
const E = require("./engine.js");
const snap = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "snapshot.json"), "utf8"));
const r = E.parity(snap);
console.log("parity", JSON.stringify(r));

const tl = E.timeline(snap);
const ix = Object.fromEntries(snap.game_cols.map((c, i) => [c, i]));
let worst = 0, n = 0, worstGame = null;
for (const g of snap.games) {
  const st = E.stateOn(snap, tl, g[ix.date]);
  const h = st.teams[g[ix.home]], a = st.teams[g[ix.away]];
  if (!h || !a) continue;
  // Python fills a missing delta with 0, so an unknown starter zeroes the GSAx delta.
  const known = g[ix.gsax_home] != null && g[ix.gsax_away] != null;
  const hv = { ...h.values, g_gsax: known ? g[ix.gsax_home] : 0 };
  const av = { ...a.values, g_gsax: known ? g[ix.gsax_away] : 0 };
  const p = E.predictX(snap, snap.models[g[ix.model]], E.features(snap, hv, av)).pHome;
  const d = Math.abs(p - g[ix.p_home]);
  n++;
  if (d > worst) { worst = d; worstGame = g.slice(0, 5); }
}
console.log("consistency", JSON.stringify({ games: n, of: snap.games.length, maxAbsDiff: worst, worstGame }));

const t = E.stateOn(snap, tl, "end").teams.MTL.values;
console.log("MTL vs MTL neutral:", E.matchup(snap, snap.models[snap.end.model], t, t, "neutral").pA.toFixed(6));
process.exit(r.ok && worst < 1e-3 ? 0 : 1);
