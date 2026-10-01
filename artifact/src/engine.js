/* Hobby Hero engine (v0.2, 2026-09-24): Poisson goals, no market input.
 * Mirrors src/export/artifact_snapshot.py `predict` exactly; parity-tested in
 * artifact/src/parity_test.cjs. Pure functions over the snapshot JSON.
 * v0.2: several models (one per month, walk-forward, plus the off-season model) and
 * team form on the morning of every game day. */
const HHEngine = (() => {
  function pmf(lam, n) {
    const out = new Array(n);
    out[0] = Math.exp(-lam);
    for (let k = 1; k < n; k++) out[k] = out[k - 1] * lam / k;
    return out;
  }

  // Raw feature vector x (home-minus-away deltas) -> win %, goal rates, score grid.
  function predictX(snap, model, x) {
    const { mean, scale } = model.scaler;
    let eh = model.home.intercept, ea = model.away.intercept;
    for (let i = 0; i < x.length; i++) {
      const z = (x[i] - mean[i]) / scale[i];
      eh += z * model.home.coef[i];
      ea += z * model.away.coef[i];
    }
    const lh = Math.exp(eh), la = Math.exp(ea), n = snap.max_goals;
    const ph = pmf(lh, n), pa = pmf(la, n);
    const grid = [];
    let win = 0, tie = 0;
    for (let i = 0; i < n; i++) {
      grid.push([]);
      for (let j = 0; j < n; j++) {
        const p = ph[i] * pa[j];
        grid[i].push(p);
        if (i > j) win += p; else if (i === j) tie += p;
      }
    }
    return { pHome: win + tie * model.tie_home_share, lamHome: lh, lamAway: la, grid, pTie: tie };
  }

  function features(snap, homeVals, awayVals) {
    return snap.features.map(f => homeVals[f.team_key] - awayVals[f.team_key]);
  }

  // ---- team form by date ------------------------------------------------------------
  // Packed team row: state_keys values..., goalie index, goalie GSAx, starts in last 20.
  function unpack(snap, row) {
    const k = snap.state_keys.length, values = {};
    snap.state_keys.forEach((key, i) => { values[key] = row[i]; });
    values.g_gsax = row[k + 1];
    const gi = row[k];
    return { values, goalie: { name: gi == null ? "" : snap.goalies[gi], starts: row[k + 2] } };
  }

  // Build the full state for every game day once (states are stored as changes only).
  function timeline(snap) {
    const days = [];
    let cur = null, season = null;
    for (const d of snap.dates) {
      if (d.season !== season) { cur = {}; season = d.season; }
      cur = { ...cur, ...snap.states[d.d] };
      days.push({ key: d.d, season: d.season, model: d.model, packed: cur });
    }
    const end = { key: "end", season: days.length ? days[days.length - 1].season : null,
                  model: snap.end.model, packed: snap.end.teams };
    return { days, end };
  }

  // Form on the morning of `ymd` (YYYYMMDD): the first game day on or after it, or the
  // off-season state after the last game.
  function stateOn(snap, tl, ymd) {
    let day = tl.end;
    if (ymd !== "end") {
      let lo = 0, hi = tl.days.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (tl.days[mid].key < ymd) lo = mid + 1; else hi = mid; }
      if (lo < tl.days.length) day = tl.days[lo];
    }
    const teams = {};
    for (const [t, row] of Object.entries(day.packed)) teams[t] = unpack(snap, row);
    return { key: day.key, season: day.season, model: snap.models[day.model], teams };
  }

  // Apply lever positions {leverId: newValue} to a team's values. A lever moves
  // both half-lives of its stat by the same amount; xG shares follow xGF/xGA.
  function adjust(snap, base, moves) {
    const v = { ...base };
    for (const lever of snap.levers) {
      if (!(lever.id in moves)) continue;
      const shift = moves[lever.id] - base[lever.keys[lever.keys.length - 1]];
      for (const k of lever.keys) v[k] = base[k] + shift;
    }
    for (const h of [10, 40]) {
      const f0 = base[`xg_xgf_h${h}`], a0 = base[`xg_xga_h${h}`];
      const f1 = v[`xg_xgf_h${h}`], a1 = v[`xg_xga_h${h}`];
      if (f0 + a0 > 0 && f1 + a1 > 0) {
        const d = f1 / (f1 + a1) - f0 / (f0 + a0);
        v[`xg_xg_share_h${h}`] = base[`xg_xg_share_h${h}`] + d;
        v[`xg_xg5_share_h${h}`] = base[`xg_xg5_share_h${h}`] + d;
      }
    }
    return v;
  }

  // Home / neutral matchup. Neutral averages both orientations, reported for A vs B.
  function matchup(snap, model, aVals, bVals, venue) {
    const ab = predictX(snap, model, features(snap, aVals, bVals)); // A at home
    if (venue === "home") return { pA: ab.pHome, lamA: ab.lamHome, lamB: ab.lamAway,
                                   grid: ab.grid, pTie: ab.pTie };
    const ba = predictX(snap, model, features(snap, bVals, aVals)); // B at home
    const n = snap.max_goals, grid = [];
    for (let i = 0; i < n; i++) {
      grid.push([]);
      for (let j = 0; j < n; j++) grid[i].push((ab.grid[i][j] + ba.grid[j][i]) / 2);
    }
    return { pA: (ab.pHome + 1 - ba.pHome) / 2, lamA: (ab.lamHome + ba.lamAway) / 2,
             lamB: (ab.lamAway + ba.lamHome) / 2, grid, pTie: (ab.pTie + ba.pTie) / 2 };
  }

  function parity(snap) {
    let worst = 0;
    const models = new Set();
    for (const row of snap.parity) {
      const r = predictX(snap, snap.models[row.model], row.x);
      models.add(row.model);
      worst = Math.max(worst, Math.abs(r.pHome - row.p_home),
                       Math.abs(r.lamHome - row.lam_home), Math.abs(r.lamAway - row.lam_away));
    }
    return { rows: snap.parity.length, models: models.size, maxAbsDiff: worst, ok: worst <= 1e-6 };
  }

  // Log loss, Brier and accuracy of a probability list against outcomes (1 = home won).
  function score(ps, ys) {
    let ll = 0, br = 0, hit = 0;
    for (let i = 0; i < ps.length; i++) {
      const p = Math.min(1 - 1e-6, Math.max(1e-6, ps[i])), y = ys[i];
      ll -= y ? Math.log(p) : Math.log(1 - p);
      br += (p - y) ** 2;
      hit += (p >= 0.5) === (y === 1) ? 1 : 0;
    }
    const n = ps.length || 1;
    return { logLoss: ll / n, brier: br / n, accuracy: hit / n, games: ps.length };
  }

  return { predictX, features, timeline, stateOn, adjust, matchup, parity, score };
})();
if (typeof module !== "undefined") module.exports = HHEngine;
