(() => {
  const snap = JSON.parse(document.getElementById("snapshot").textContent);
  const E = HHEngine;
  const $ = id => document.getElementById(id);
  const tl = E.timeline(snap);
  const ix = Object.fromEntries(snap.game_cols.map((c, i) => [c, i]));
  const gamesByDate = {};
  for (const g of snap.games) (gamesByDate[g[ix.date]] ||= []).push(g);
  const dayKeys = tl.days.map(d => d.key);
  // This season's schedule (2026-09-29): pre-season estimates from the off-season model on
  // end-of-season form, with last season's main starters. Days after the off-season state.
  const sched = snap.schedule;
  const sx = sched ? Object.fromEntries(sched.cols.map((c, i) => [c, i])) : {};
  const schedByDate = {};
  if (sched) for (const g of sched.games) (schedByDate[g[sx.date]] ||= []).push(g);
  const schedKeys = Object.keys(schedByDate).sort();
  const allKeys = [...dayKeys, "end", ...schedKeys];
  function stateAt(ymd) {
    if (ymd !== "end" && schedKeys.length && ymd > snap.end.after) {
      const k = schedKeys.find(d => d >= ymd);
      if (k) return { ...E.stateOn(snap, tl, "end"), key: k, season: sched.season, scheduled: true };
    }
    return E.stateOn(snap, tl, ymd);
  }
  const today = (() => { const d = new Date();
    return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`; })();
  const defaultDate = schedKeys.find(d => d >= today) || dayKeys[dayKeys.length - 1] || "end";
  const kickoff = iso => iso ? new Date(iso).toLocaleTimeString("en-CA",
    { hour: "numeric", minute: "2-digit", timeZoneName: "short" }) : "Time to be set";
  const seasonLabel = s => `${String(s).slice(0, 4)}-${String(s).slice(6)}`;
  const ymdToIso = k => `${k.slice(0, 4)}-${k.slice(4, 6)}-${k.slice(6)}`;
  const isoToYmd = s => s.replaceAll("-", "");
  const nice = k => new Date(`${ymdToIso(k)}T12:00:00`).toLocaleDateString("en-CA",
    { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  const pct = (p, d = 0) => `${(100 * p).toFixed(d)}%`;
  const ml = v => v == null ? "" : (v > 0 ? `+${v}` : `−${Math.abs(v)}`);
  const signed = (v, d = 4) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}`;

  // Default view (Dan, 2026-09-24): the last game in the data, loaded with its real starters.
  // From 2026-09-29: today's (or the next) scheduled game day, loading its first game.
  const state = { date: defaultDate, A: "FLA", B: "CAR", venue: "home", moves: { A: {}, B: {} },
                  override: { A: null, B: null }, game: null, range: "todate" };
  let restored = false, savedGame = null;
  try {
    const saved = JSON.parse(localStorage.getItem("hh-v4") || "null");
    if (saved) { restored = true; savedGame = saved.game || null; }
    if (saved) Object.assign(state, { date: saved.date || "end", A: saved.A || "FLA", B: saved.B || "CAR",
                                      venue: saved.venue || "home", range: saved.range || "todate" });
  } catch (e) { /* storage unavailable: keep defaults */ }
  let cur = stateAt(state.date);

  const tInfo = t => snap.teams[t];
  const fullName = t => {
    const n = tInfo(t).names, nm = n[String(cur.season)] || n[Object.keys(n).sort().pop()];
    return `${nm.place} ${nm.name}`.trim();
  };
  const seasonTeams = () => Object.keys(cur.teams).filter(t => snap.teams[t])
    .sort((a, b) => fullName(a).localeCompare(fullName(b)));
  const leverFmt = (lever, v) => lever.format === "pct" ? `${(100 * v).toFixed(1)}%`
    : lever.format.replace(/\{:(\+?)\.(\d)f\}/, (_, plus, d) => (plus && v >= 0 ? "+" : "") + v.toFixed(+d));
  const baseValues = side => {
    const v = { ...cur.teams[state[side]].values };
    if (state.override[side]) v.g_gsax = state.override[side].gsax;
    return v;
  };
  const leverBase = (side, lever) => baseValues(side)[lever.keys[lever.keys.length - 1]];

  function styleChip(el, t) {
    const c = tInfo(t).colors;
    el.textContent = t;
    el.style.background = c.bg; el.style.color = c.fg; el.style.setProperty("--chip-accent", c.accent);
  }
  // Team colour for the win bar; very dark team colours are lifted so they read on navy.
  function barColor(t) {
    const hex = tInfo(t).colors.bg.replace("#", "");
    const [r, g, b] = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map(c => c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    return lum < 0.05 ? `color-mix(in srgb, #${hex} 50%, var(--hh-ice))` : `#${hex}`;
  }
  function chipHtml(t) {
    const c = tInfo(t).colors;
    return `<span class="chip" style="background:${c.bg};color:${c.fg};--chip-accent:${c.accent}">${t}</span>`;
  }

  function fillSelects() {
    const list = seasonTeams();
    if (!cur.teams[state.A]) state.A = list.includes("FLA") ? "FLA" : list[0];
    if (!cur.teams[state.B] || state.B === state.A) state.B = list.find(t => t !== state.A && t === "CAR") || list.find(t => t !== state.A);
    for (const side of ["A", "B"]) {
      const sel = $("team" + side);
      sel.innerHTML = "";
      for (const t of list) sel.add(new Option(`${t} · ${fullName(t)}`, t));
      sel.value = state[side];
    }
  }

  function buildLevers(side) {
    const t = state[side], box = $("levers" + side);
    const g = state.override[side] || cur.teams[t].goalie;
    box.innerHTML = "";
    const head = document.createElement("div");
    head.className = "lever-head";
    const who = document.createElement("div");
    who.style.display = "grid"; who.style.gap = "4px";
    const chip = document.createElement("span"); chip.className = "chip"; styleChip(chip, t);
    const goalie = document.createElement("span"); goalie.className = "muted";
    goalie.style.fontSize = "var(--hh-size-small)";
    goalie.textContent = state.override[side]?.kind === "main"
      ? `Starter: ${g.name || "unknown"} (${seasonLabel(sched.starters_from)} main starter, ${sched.starter_starts[t] ?? "?"} starts)`
      : state.override[side] ? `Starter: ${g.name || "unknown"} (started that game)`
      : g.name ? `Starter: ${g.name} (${g.starts} of the last 20 starts)` : "Starter: unknown";
    who.append(chip, goalie);
    const reset = document.createElement("button");
    reset.type = "button"; reset.className = "ghost"; reset.textContent = `Reset ${t}`;
    reset.addEventListener("click", () => { state.moves[side] = {}; buildLevers(side); render(); });
    head.append(who, reset);
    box.append(head);
    for (const lever of snap.levers) {
      const base = leverBase(side, lever);
      const val = state.moves[side][lever.id] ?? base;
      const id = `lever-${side}-${lever.id}`;
      const row = document.createElement("div"); row.className = "lever";
      const top = document.createElement("div"); top.className = "lever-row";
      const lab = document.createElement("label"); lab.htmlFor = id; lab.textContent = lever.label;
      const out = document.createElement("output"); out.htmlFor = id;
      const input = document.createElement("input");
      Object.assign(input, { type: "range", id, min: Math.min(lever.min, base), max: Math.max(lever.max, base),
                             step: lever.step, value: val });
      const show = v => {
        out.textContent = leverFmt(lever, v);
        if (Math.abs(v - base) > lever.step / 2) {
          const s = document.createElement("span"); s.className = "delta";
          s.textContent = ` (was ${leverFmt(lever, base)})`;
          out.append(s);
        }
      };
      show(val);
      input.addEventListener("input", () => {
        const v = parseFloat(input.value);
        if (Math.abs(v - base) <= lever.step / 2) delete state.moves[side][lever.id];
        else state.moves[side][lever.id] = v;
        show(v); render();
      });
      top.append(lab, out); row.append(top, input); box.append(row);
    }
  }

  function save() {
    try { localStorage.setItem("hh-v4", JSON.stringify({ date: state.date, A: state.A, B: state.B, venue: state.venue, range: state.range, game: state.game })); } catch (e) { /* ignore */ }
  }

  let firstRender = true;
  function render() {
    const home = state.venue === "home";
    const aVals = E.adjust(snap, baseValues("A"), state.moves.A);
    const bVals = E.adjust(snap, baseValues("B"), state.moves.B);
    const r = E.matchup(snap, cur.model, aVals, bVals, state.venue);
    const pA = r.pA, pB = 1 - pA;
    $("labelA").textContent = home ? "Home" : "Team 2";
    $("labelB").textContent = home ? "Away" : "Team 1";
    $("atWord").textContent = home ? "at" : "vs";
    $("vsWord").textContent = home ? "AT" : "VS";
    for (const [side, p] of [["A", pA], ["B", pB]]) {
      const t = state[side];
      styleChip($("chip" + side), t);
      $("pct" + side).innerHTML = `${(100 * p).toFixed(0)}<small>%</small>`;
      $("name" + side).textContent = fullName(t) + (home && side === "A" ? " · home ice" : "");
      $("bar" + side).style.flexGrow = p;
      $("bar" + side).style.background = barColor(t);
    }
    $("puck").style.left = `${(100 * pB).toFixed(2)}%`;
    if (firstRender) { $("puck").classList.add("drop"); firstRender = false; }
    $("xgLine").textContent = `${state.B} ${r.lamB.toFixed(2)} – ${r.lamA.toFixed(2)} ${state.A}`;
    $("soLine").textContent = pct(r.pTie);
    const cells = [];
    r.grid.forEach((row, i) => row.forEach((p, j) => cells.push({ i, j, p })));
    cells.sort((x, y) => y.p - x.p);
    const top = cells.slice(0, 5);
    $("topLine").textContent = `${state.B} ${top[0].j}–${top[0].i} ${state.A}`;
    const list = $("toplist"); list.innerHTML = "";
    for (const c of top) {
      const li = document.createElement("li");
      li.innerHTML = `<span class="num"></span><span class="meter"><i></i></span><span class="num" style="text-align:right"></span>`;
      li.children[0].textContent = c.i === c.j ? `${c.j}–${c.i}*` : `${c.j}–${c.i}`;
      li.children[1].firstChild.style.width = `${(100 * c.p / top[0].p).toFixed(1)}%`;
      li.children[2].textContent = pct(c.p, 1);
      li.title = c.i === c.j ? `${c.j}–${c.i}, decided in a shootout` : `${state.B} ${c.j}–${c.i} ${state.A}`;
      list.append(li);
    }
    const N = 7, gmax = top[0].p;
    let html = `<caption>Chance of each score after regulation and overtime, ${state.B} across, ${state.A} down. * = level, decided in a shootout.</caption><tr><th></th>`;
    for (let j = 0; j < N; j++) html += `<th scope="col">${j}</th>`;
    html += `<th scope="col" class="micro" style="text-align:left">${state.B}</th></tr>`;
    for (let i = 0; i < N; i++) {
      html += `<tr><th scope="row">${i}</th>`;
      for (let j = 0; j < N; j++) {
        const p = r.grid[i][j], a = Math.round(6 + 72 * p / gmax);
        const txt = p >= 0.01 ? (100 * p).toFixed(0) : "";
        html += `<td style="background: color-mix(in srgb, var(--hh-ice-line) ${a}%, var(--hh-sunken))" title="${state.B} ${j}, ${state.A} ${i}: ${pct(p, 1)}">${txt}</td>`;
      }
      html += "</tr>";
    }
    html += `<tr><th class="micro">${state.A}</th></tr>`;
    $("grid").innerHTML = html;
    const ov = state.override;
    if (ov.A?.kind === "main" || ov.B?.kind === "main") {
      $("overrideNote").hidden = false;
      $("overrideNote").innerHTML = `Pre-season estimate for the game on ${nice(cur.key)}${home ? "" : " (neutral site)"}, with each team's main starter from ${seasonLabel(sched.starters_from)}: <b>${ov.B?.name || "?"}</b> for ${state.B}, <b>${ov.A?.name || "?"}</b> for ${state.A}. Nothing since the end of last season is in it.`;
    } else if (ov.A || ov.B) {
      $("overrideNote").hidden = false;
      $("overrideNote").innerHTML = `Loaded from the game on ${nice(cur.key)}, with the goalies who actually started: <b>${ov.B?.name || "?"}</b> for ${state.B}, <b>${ov.A?.name || "?"}</b> for ${state.A}.`;
    } else $("overrideNote").hidden = true;
    renderGames();
    save();
  }

  function dateNote() {
    const m = cur.model;
    const trained = `trained on ${m.train_games.toLocaleString("en-CA")} games through ${nice(m.trained_through)}`;
    if (cur.scheduled) {
      const sel = state.date, same = sel === cur.key;
      $("dateNote").innerHTML = `<b>${nice(cur.key)}, scheduled</b>${same ? "" : ` (the next game day after ${nice(sel)})`} · ${seasonLabel(cur.season)} · <b>pre-season estimates</b>: form after the last game of ${seasonLabel(tl.end.season)} (${nice(snap.end.after)}) and the off-season model, ${trained}. They know nothing about trades, signings or injuries since.`;
      $("date").value = ymdToIso(cur.key);
    } else if (cur.key === "end") {
      $("dateNote").innerHTML = `<b>Off-season.</b> Form after the last game of ${seasonLabel(tl.end.season)} (${nice(snap.end.after)}), with the model ${trained}.`;
      $("date").value = ymdToIso(snap.end.after);
    } else {
      const sel = state.date;
      const same = sel === cur.key;
      $("dateNote").innerHTML = `<b>Morning of ${nice(cur.key)}</b>${same ? "" : ` (the next game day after ${nice(sel)})`} · ${seasonLabel(cur.season)} · the ${m.label} model, ${trained}.`;
      $("date").value = ymdToIso(cur.key);
    }
    $("leverNote").textContent = `Each lever starts at the team's form on this date, weighted toward recent games. The model's response is smooth, and every lever stays within roughly the league's range, because the model hasn't seen values far outside it.`;
  }

  function loadGame(g, scroll = true) {
    state.B = g[ix.away]; state.A = g[ix.home]; state.venue = "home";
    state.moves = { A: {}, B: {} };
    const known = g[ix.gsax_home] != null && g[ix.gsax_away] != null;
    state.override = known ? { A: { name: snap.goalies[g[ix.g_home]] ?? "", gsax: g[ix.gsax_home] },
                               B: { name: snap.goalies[g[ix.g_away]] ?? "", gsax: g[ix.gsax_away] } } : { A: null, B: null };
    state.game = `${g[ix.date]}-${g[ix.home]}`;
    $("teamA").value = state.A; $("teamB").value = state.B;
    setVenueButtons();
    buildLevers("A"); buildLevers("B"); render();
    if (scroll) document.querySelector(".result").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // A scheduled game: last season's main starters, and a neutral venue for neutral-site games.
  function loadScheduled(g, scroll = true) {
    state.B = g[sx.away]; state.A = g[sx.home]; state.venue = g[sx.neutral] ? "neutral" : "home";
    state.moves = { A: {}, B: {} };
    const known = g[sx.gsax_home] != null && g[sx.gsax_away] != null;
    state.override = known ? { A: { name: snap.goalies[g[sx.g_home]] ?? "", gsax: g[sx.gsax_home], kind: "main" },
                               B: { name: snap.goalies[g[sx.g_away]] ?? "", gsax: g[sx.gsax_away], kind: "main" } } : { A: null, B: null };
    state.game = `${g[sx.date]}-${g[sx.home]}`;
    $("teamA").value = state.A; $("teamB").value = state.B;
    setVenueButtons();
    buildLevers("A"); buildLevers("B"); render();
    if (scroll) document.querySelector(".result").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderScheduled() {
    const box = $("games"), list = schedByDate[cur.key] || [];
    box.innerHTML = list.map(g => {
      const h = g[sx.home], a = g[sx.away], p = g[sx.p_home];
      const model = p == null ? `<span class="s">No estimate</span>`
        : `<span class="v">${p >= 0.5 ? h : a} ${pct(p >= 0.5 ? p : 1 - p)}</span><span class="s">xG ${g[sx.lam_away].toFixed(1)}–${g[sx.lam_home].toFixed(1)}</span>`;
      const ns = g[sx.neutral] ? ` <span class="micro">Neutral site</span>` : "";
      const isCur = state.game === `${g[sx.date]}-${h}`;
      return `<div class="game${isCur ? " current" : ""}">
        <div class="who">${chipHtml(a)}<span class="at2">at</span>${chipHtml(h)}${ns}</div>
        <div class="cell"><span class="micro">Model, pre-season</span>${model}</div>
        <div class="cell"><span class="micro">Closing market</span><span class="s">No closing line yet</span></div>
        <div class="cell"><span class="micro">Puck drop</span><span class="v">${kickoff(g[sx.start_utc])}</span><span class="s">No result yet</span></div>
        <button type="button" class="ghost load" data-s="${sched.games.indexOf(g)}">Load</button>
      </div>`;
    }).join("");
    $("dayNote").textContent = `${list.length} game${list.length > 1 ? "s" : ""} scheduled. Pre-season estimates; results come in when the page is rebuilt with played games.`;
    box.querySelectorAll("button.load").forEach(b => b.addEventListener("click", () => loadScheduled(sched.games[+b.dataset.s])));
  }

  function renderGames() {
    if (cur.scheduled) return renderScheduled();
    const box = $("games");
    const list = cur.key === "end" ? [] : (gamesByDate[cur.key] || []);
    if (!list.length) {
      $("dayNote").textContent = "";
      box.innerHTML = `<div class="empty">${cur.key === "end" ? `Off-season: no games. Pick a date from ${seasonLabel(tl.days[0].season)} to ${seasonLabel(tl.end.season)}, or step with the game-day buttons, to see what the model said before each game${schedKeys.length ? `. Step forward for the ${seasonLabel(sched.season)} schedule` : ""}.` : "No games on this date."}</div>`;
      return;
    }
    let hits = 0, mhits = 0, withM = 0;
    const rows = list.map(g => {
      const h = g[ix.home], a = g[ix.away], p = g[ix.p_home], mk = g[ix.market_home];
      const hf = g[ix.home_final], af = g[ix.away_final], homeWon = hf > af;
      const fav = p >= 0.5 ? h : a, favP = p >= 0.5 ? p : 1 - p;
      const hit = (p >= 0.5) === homeWon; hits += hit ? 1 : 0;
      let mCell = `<span class="s">No closing line in the data</span>`;
      if (mk != null) {
        withM++; mhits += ((mk >= 0.5) === homeWon) ? 1 : 0;
        const mf = mk >= 0.5 ? h : a, mp = mk >= 0.5 ? mk : 1 - mk;
        mCell = `<span class="v">${mf} ${pct(mp)}</span><span class="s">${a} ${ml(g[ix.ml_away])} · ${h} ${ml(g[ix.ml_home])}</span>`;
      }
      const ot = g[ix.outcome] === "REG" ? "" : ` (${g[ix.outcome]})`;
      const po = g[ix.type] === 3 ? ` <span class="micro" style="color:var(--hh-red-bright)">Playoffs</span>` : "";
      const isCur = state.game === `${g[ix.date]}-${h}` || (!state.game && state.A === h && state.B === a);
      return `<div class="game${isCur ? " current" : ""}">
        <div class="who">${chipHtml(a)}<span class="at2">at</span>${chipHtml(h)}${po}</div>
        <div class="cell"><span class="micro">Model</span><span class="v">${fav} ${pct(favP)}</span><span class="s">xG ${g[ix.lam_away].toFixed(1)}–${g[ix.lam_home].toFixed(1)}</span></div>
        <div class="cell"><span class="micro">Closing market</span>${mCell}</div>
        <div class="cell"><span class="micro">Final</span><span class="v">${a} ${af}–${hf} ${h}${ot}</span><span class="hit ${hit ? "yes" : "no"}">${hit ? "Model favourite won" : "Model favourite lost"}</span></div>
        <button type="button" class="ghost load" data-g="${snap.games.indexOf(g)}">Load</button>
      </div>`;
    });
    box.innerHTML = rows.join("");
    $("dayNote").textContent = `${list.length} game${list.length > 1 ? "s" : ""}. Model favourite won ${hits} of ${list.length}` +
      (withM ? `; market favourite won ${mhits} of ${withM}.` : ".");
    box.querySelectorAll("button.load").forEach(b => b.addEventListener("click", () => loadGame(snap.games[+b.dataset.g])));
  }

  function renderBoard() {
    const season = cur.season, key = cur.key;
    let rows = snap.games.filter(g => g[ix.market_home] != null);
    let desc;
    if (state.range === "both") { desc = `every game from ${seasonLabel(tl.days[0].season)} to ${seasonLabel(tl.end.season)}`; }
    else if (state.range === "season" || key === "end") {
      rows = rows.filter(g => g[ix.season] === season);
      desc = `every game of ${seasonLabel(season)}`;
    } else {
      rows = rows.filter(g => g[ix.season] === season && g[ix.date] < key);
      desc = `every ${seasonLabel(season)} game before ${nice(key)}`;
    }
    const missing = snap.games.filter(g => g[ix.market_home] == null);
    const bySeason = {};
    for (const g of missing) bySeason[g[ix.season]] = (bySeason[g[ix.season]] || 0) + 1;
    const missTxt = Object.entries(bySeason).map(([s, n]) => `${n.toLocaleString("en-CA")} in ${seasonLabel(+s)}`).join(", ");
    $("boardNote").textContent = `Games: ${desc} with a closing line (${rows.length.toLocaleString("en-CA")}). Each game uses the model that was current that month.` +
      (missing.length ? ` Games with no closing line in the data are left out: ${missTxt}.` : "");
    if (!rows.length) {
      $("board").innerHTML = "";
      $("chart").innerHTML = `<text x="320" y="95" text-anchor="middle">${cur.scheduled
        ? `No ${seasonLabel(cur.season)} results in the data yet. “All seasons” shows the record so far.` : "No games before this date yet."}</text>`;
      $("boardVerdict").textContent = "";
      return;
    }
    const y = rows.map(g => g[ix.home_final] > g[ix.away_final] ? 1 : 0);
    const m = E.score(rows.map(g => g[ix.p_home]), y), k = E.score(rows.map(g => g[ix.market_home]), y);
    const row = (name, s) => `<tr><td>${name}</td><td>${s.logLoss.toFixed(4)}</td><td>${s.brier.toFixed(4)}</td><td>${pct(s.accuracy, 1)}</td></tr>`;
    $("board").innerHTML = `<thead><tr><th>${rows.length.toLocaleString("en-CA")} games</th><th>Log loss</th><th>Brier</th><th>Favourite won</th></tr></thead>
      <tbody>${row("<b>Hobby Hero model</b>", m)}${row("Closing market", k)}
      <tr><td>Gap (model minus market)</td><td>${signed(m.logLoss - k.logLoss)}</td><td>${signed(m.brier - k.brier)}</td><td>${signed(100 * (m.accuracy - k.accuracy), 1)} pts</td></tr></tbody>`;
    // running gap
    const W = 640, H = 190, L = 44, R = 10, T = 12, B = 26;
    let acc = 0; const pts = [];
    rows.forEach((g, i) => {
      const p = Math.min(1 - 1e-6, Math.max(1e-6, g[ix.p_home])), q = Math.min(1 - 1e-6, Math.max(1e-6, g[ix.market_home]));
      acc += y[i] ? Math.log(q) - Math.log(p) : Math.log(1 - q) - Math.log(1 - p);
      pts.push(acc / (i + 1));
    });
    const skip = Math.min(25, Math.floor(pts.length / 4));
    const shown = pts.slice(skip);
    const lim = Math.max(0.01, ...shown.map(Math.abs));
    const step = lim > 0.04 ? 0.02 : lim > 0.02 ? 0.01 : 0.005;
    const top = Math.ceil(lim / step) * step;
    const X = i => L + (W - L - R) * (pts.length > 1 ? i / (pts.length - 1) : 0);
    const Y = v => T + (H - T - B) * (top - v) / (2 * top);
    let svg = "";
    for (let v = -top; v <= top + 1e-9; v += step) {
      svg += `<line class="${Math.abs(v) < 1e-9 ? "zero" : "gridl"}" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/>`;
      svg += `<text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${Math.abs(v) < 1e-9 ? "0" : signed(v, step < 0.01 ? 3 : 2)}</text>`;
    }
    let lastMonth = "";
    rows.forEach((g, i) => {
      const mo = g[ix.date].slice(0, 6);
      if (mo !== lastMonth) {
        lastMonth = mo;
        const lbl = new Date(`${ymdToIso(g[ix.date]).slice(0, 8)}15T12:00:00`).toLocaleDateString("en-CA", { month: "short" });
        if (X(i) < W - R - 20) svg += `<text x="${X(i)}" y="${H - 8}" text-anchor="start">${lbl}</text>`;
      }
    });
    const d = shown.map((v, i) => `${i ? "L" : "M"}${X(i + skip).toFixed(1)},${Y(v).toFixed(1)}`).join("");
    svg += `<path class="area" d="${d}L${X(pts.length - 1).toFixed(1)},${Y(0)}L${X(skip).toFixed(1)},${Y(0)}Z"/>`;
    svg += `<path class="gap" d="${d}"/>`;
    $("chart").innerHTML = svg;
    const gap = m.logLoss - k.logLoss;
    $("boardVerdict").innerHTML = gap > 0
      ? `Over these games the closing market was better calibrated by <span class="num">${gap.toFixed(4)}</span> in log loss. <b>No edge over the market here.</b>`
      : `Over these games the model was better calibrated by <span class="num">${(-gap).toFixed(4)}</span> in log loss. Over short stretches either side can lead by chance; the pre-registered test below is the one to trust.`;
    $("chart").setAttribute("aria-label", `Running log-loss gap over ${rows.length} games, ending at ${signed(gap)}.`);
  }

  function setDate(ymd, keepMatchup = true) {
    state.date = ymd;
    cur = stateAt(ymd);
    state.moves = { A: {}, B: {} }; state.override = { A: null, B: null }; state.game = null;
    fillSelects(); dateNote();
    buildLevers("A"); buildLevers("B"); render(); renderBoard();
  }
  // Past game days, then the off-season state, then this season's scheduled days.
  function stepDay(dir) {
    const i = allKeys.indexOf(cur.key);
    setDate(allKeys[Math.max(0, Math.min(allKeys.length - 1, i + dir))]);
  }
  function setVenueButtons() {
    $("venueHome").setAttribute("aria-pressed", String(state.venue === "home"));
    $("venueNeutral").setAttribute("aria-pressed", String(state.venue === "neutral"));
  }
  function setTeam(side, t) {
    state[side] = t; state.moves[side] = {}; state.override = { A: null, B: null }; state.game = null;
    buildLevers("A"); buildLevers("B"); render();
  }

  $("date").min = ymdToIso(dayKeys[0]);
  $("date").max = ymdToIso(schedKeys.length ? schedKeys[schedKeys.length - 1] : snap.end.after);
  $("date").addEventListener("change", e => { if (e.target.value) setDate(isoToYmd(e.target.value)); });
  $("prevDay").addEventListener("click", () => stepDay(-1));
  $("nextDay").addEventListener("click", () => stepDay(1));
  $("offSeason").addEventListener("click", () => setDate("end"));
  $("teamA").addEventListener("change", e => setTeam("A", e.target.value));
  $("teamB").addEventListener("change", e => setTeam("B", e.target.value));
  $("swap").addEventListener("click", () => {
    [state.A, state.B] = [state.B, state.A];
    [state.moves.A, state.moves.B] = [state.moves.B, state.moves.A];
    [state.override.A, state.override.B] = [state.override.B, state.override.A];
    state.game = null;
    $("teamA").value = state.A; $("teamB").value = state.B;
    buildLevers("A"); buildLevers("B"); render();
  });
  for (const [id, v] of [["venueHome", "home"], ["venueNeutral", "neutral"]]) {
    $(id).addEventListener("click", () => { state.venue = v; setVenueButtons(); render(); });
  }
  $("resetAll").addEventListener("click", () => { state.moves = { A: {}, B: {} }; buildLevers("A"); buildLevers("B"); render(); });
  $("rangeSeg").querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    state.range = b.dataset.range;
    $("rangeSeg").querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
    renderBoard(); save();
  }));
  $("rangeSeg").querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x.dataset.range === state.range)));

  // static facts
  const mt = snap.metrics, meta = snap.meta, endModel = snap.models[snap.end.model];
  $("trainGames").textContent = endModel.train_games.toLocaleString("en-CA");
  $("lastGame").textContent = nice(snap.end.after);
  const rowsM = Object.entries(mt.windows).map(([name, w]) => `<tr><td>${name.replace(/^W\d /, "")}</td><td>${w.games.toLocaleString("en-CA")}</td>
      <td>${w.model.log_loss.toFixed(4)}</td><td>${pct(w.model.accuracy, 1)}</td>
      <td>${w.market.log_loss.toFixed(4)}</td><td>${pct(w.market.accuracy, 1)}</td>
      <td>${signed(w.gap_vs_market[0])}</td></tr>`).join("");
  $("metrics").innerHTML = `<thead><tr><th>Test window</th><th>Games</th><th>Model log loss</th><th>Model accuracy</th>
    <th>Market log loss</th><th>Market accuracy</th><th>Gap</th></tr></thead><tbody>${rowsM}</tbody>`;
  const pg = mt.pooled_gap_vs_market;
  $("verdict").innerHTML = `Across both windows (${mt.pooled_games.toLocaleString("en-CA")} games) the model trails the closing market by <span class="num">${pg[0].toFixed(4)}</span> in log loss (90% interval <span class="num">${pg[1].toFixed(4)}</span> to <span class="num">${pg[2].toFixed(4)}</span>). <b>No edge over the market.</b> Over the full 2025-26 season it comes within <span class="num">${mt.windows[Object.keys(mt.windows)[1]].gap_vs_market[0].toFixed(4)}</span>. Those windows refit the model four times each; the scoreboard above refits monthly, so its numbers differ slightly.`;
  $("modelHash").textContent = meta.model_hash;
  $("builtAt").textContent = meta.built_at.slice(0, 10);
  const check = E.parity(snap);
  $("engineCheck").innerHTML = check.ok
    ? `<span class="check-ok">Engine check passed:</span> this page reproduces the Python models (${check.models} of them) on ${check.rows} test games (largest difference ${check.maxAbsDiff.toExponential(0)}).`
    : `Engine check failed: this page's numbers differ from the Python model by up to ${check.maxAbsDiff.toExponential(1)}. Treat them as unverified.`;

  fillSelects(); dateNote(); setVenueButtons();
  buildLevers("A"); buildLevers("B"); render(); renderBoard();
  // First visit: load the last game. Returning visitor: reload the game they last loaded, if any.
  if (cur.scheduled) {
    const day = schedByDate[cur.key] || [];
    const g = restored ? day.find(x => `${x[sx.date]}-${x[sx.home]}` === savedGame) : day[0];
    if (g) loadScheduled(g, false);
  } else if (cur.key !== "end") {
    const day = gamesByDate[cur.key] || [];
    const g = restored ? day.find(x => `${x[ix.date]}-${x[ix.home]}` === savedGame)
                       : day[day.length - 1];
    if (g) loadGame(g, false);
  }
})();
