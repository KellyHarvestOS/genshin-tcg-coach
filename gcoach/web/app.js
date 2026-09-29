/* Card Coach UI — vanilla JS, no build step. The page only displays advice;
   nothing here can interact with the game. */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x) => `${Math.round((x ?? 0) * 100)}%`;

  const EL_RU = { pyro: "Пиро", hydro: "Гидро", electro: "Электро", cryo: "Крио", anemo: "Анемо", geo: "Гео", dendro: "Дендро", physical: "Физ.", omni: "Всеэлем.", piercing: "Пронз." };
  const ELEMENTS = ["pyro", "hydro", "electro", "cryo", "anemo", "geo", "dendro"];
  const EL_IMG = new Set(ELEMENTS);
  const elImg = (el) => `<img src="/static/img/elements/${el}.png" alt="${esc(EL_RU[el] || el)}" draggable="false">`;
  const badge = (el, extra = "") => EL_IMG.has(el)
    ? `<span class="el-badge img ${extra}" data-el="${esc(el)}">${elImg(el)}</span>`
    : `<span class="el-badge ${extra}" data-el="${esc(el)}">${icon("el-" + (el === "piercing" ? "physical" : el))}</span>`;
  const STAR_SVG = `<svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#fffaf0"/><circle cx="32" cy="32" r="29" fill="none" stroke="url(#gGoldRing)" stroke-width="2.4"/><circle cx="32" cy="32" r="24.5" fill="none" stroke="#d9bb7f" stroke-width=".8" stroke-dasharray="1.5 3"/><path d="M32 11c1.7 11.6 9.4 19.3 21 21-11.6 1.7-19.3 9.4-21 21-1.7-11.6-9.4-19.3-21-21 11.6-1.7 19.3-9.4 21-21z" fill="url(#gGold)" stroke="#a98548" stroke-width=".6"/><path d="M32 21c.9 6 4 9.1 10 10-6 .9-9.1 4-10 10-.9-6-4-9.1-10-10 6-.9 9.1-4 10-10z" fill="url(#gStarCore)"/><path d="M46 14.5c.4 2.3 1.4 3.3 3.7 3.7-2.3.4-3.3 1.4-3.7 3.7-.4-2.3-1.4-3.3-3.7-3.7 2.3-.4 3.3-1.4 3.7-3.7z" fill="#c9a86a"/></svg>`;

  let snap = null;
  let debugTab = "shot";
  let kbChars = [], kbCards = [];

  const icon = (id, cls = "") => `<svg class="${cls}"><use href="#${id}"/></svg>`;
  const elIcon = (el, extra = "") => `<span class="el-icon ${extra}" data-el="${esc(el)}">${icon("el-" + (el === "piercing" ? "physical" : el))}</span>`;

  // ------------------------------------------------------------------ API
  async function api(path, body) {
    try {
      const res = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
      const txt = await res.text();
      const data = txt ? JSON.parse(txt) : {};
      if (!res.ok) throw new Error(data.detail || res.statusText);
      return data;
    } catch (e) {
      toast(`Ошибка: ${e.message}`, "error");
      throw e;
    }
  }

  // ------------------------------------------------------------------ WebSocket
  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    let ping;
    ws.onopen = () => { ping = setInterval(() => ws.readyState === 1 && ws.send("ping"), 20000); };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "snapshot") render(msg.data);
    };
    ws.onclose = () => { clearInterval(ping); setTimeout(connect, 1500); };
  }

  // ------------------------------------------------------------------ render root
  function render(s) {
    const prevStatus = snap && snap.status && snap.status.message;
    snap = s;
    renderBanner(prevStatus);
    $("#btnWatch").classList.toggle("on", !!s.watching);
    renderMenuDemos();
    document.body.classList.toggle("welcome-mode", !s.state);
    $("#btnVoice").classList.toggle("on", !!(s.voice && s.voice.enabled));
    if (!s.state) { renderWelcome(); updateStart(); } else { renderArena(); }
    autoSpeak();
    if (!$("#debug").hidden) renderDebug();
  }

  function renderBanner(prevMessage) {
    const b = $("#banner"), st = snap.status || {};
    // on the start screen only warnings/errors are shown
    if (prevMessage && prevMessage !== st.message && (st.level === "warn" || st.level === "error") && !snap.state && !snap.watching) toast(st.message, st.level);
    if (!st.message || snap.state || snap.watching || (st.level !== "warn" && st.level !== "error")) { b.hidden = true; return; }
    b.hidden = false;
    b.className = `banner ${st.level || ""}`;
    b.innerHTML = `${icon(st.level === "ok" ? "ic-shield" : st.level === "warn" || st.level === "error" ? "ic-warn" : "ic-star4")}<span>${esc(st.message)}</span>`;
  }

  function renderMenuDemos() {
    const box = $("#menuDemos");
    const sc = snap.scenarios || {};
    const html = Object.entries(sc).map(([k, v]) => `<button data-demo="${esc(k)}">${esc(v)}</button>`).join("");
    if (box.innerHTML !== html) box.innerHTML = html;
  }

  // ------------------------------------------------------------------ welcome
  function updateStart() {
    const b = $(".start-btn");
    if (!b) return;
    b.classList.toggle("on", !!snap.watching);
    b.querySelector(".start-label").textContent = snap.watching ? "Слежу за игрой…" : "Начать";
    $(".start-hint").textContent = snap.watching ? (snap.status && snap.status.message) || "" : "";
  }
  function renderWelcome() {
    if ($("#layout .welcome")) return;  // already shown: don't replay the intro animation
    const kb = snap.knowledge || {};
    $("#layout").innerHTML = `
      <div class="welcome"><div class="welcome-card">
        <h1>Помощник <span>Священного призыва</span></h1>
        <svg class="orn"><use href="#ornament"/></svg>
        <p class="lead">Нажмите «Начать» и откройте партию в Genshin Impact — я слежу за экраном и подсказываю голосом лучший ход. Играете вы, я только советую.</p>
        <div class="start-row"><button class="start-btn" data-start="watch">
          <img class="start-art" src="/static/img/buttons/start.png" alt="" draggable="false" onload="this.parentNode.classList.add('has-art')" onerror="this.remove()">
          <span class="start-label"></span></button></div>
        <div class="start-hint"></div>
        <div class="dry-note">${icon("ic-shield")} Режим советника: приложение не нажимает ничего в игре · база: ${kb.characters || 0} персонажей, ${kb.action_cards || 0} карт с вики</div>
      </div></div>`;
  }

  // ------------------------------------------------------------------ left column
  function charTip(c) {
    const sk = (c.skills || []).map((s) => `<b>${esc(s.type_ru)}: ${esc(s.name)}</b> (${costText(s.cost)})\n${esc(s.text)}${s.modelled ? "" : "\n⚠ часть эффекта не смоделирована"}`).join("\n\n");
    return `<b>${esc(c.name)}</b> · ${EL_RU[c.element] || c.element}\nHP ${c.hp}/${c.max_hp} · Энергия ${c.energy}/${c.max_energy}\n\n${sk}`;
  }
  function costText(cost) {
    return Object.entries(cost || {}).map(([k, v]) => `${v} ${k === "unaligned" ? "любых" : k === "same" ? "одинак." : k === "energy" ? "энергии" : EL_RU[k] || k}`).join(" + ") || "0";
  }
  function statusChip(st) {
    const n = st.usages != null ? ` ×${st.usages}` : st.duration != null ? ` ${st.duration}р` : "";
    return `<span class="chip ${st.modelled ? "" : "unmodelled"}" data-tip="<b>${esc(st.name)}</b>\n${esc(st.text || "")}${st.modelled ? "" : "\n⚠ эффект смоделирован частично или не смоделирован"}">${esc(st.name)}${n}</span>`;
  }
  function hpClass(c) { const r = c.hp / Math.max(1, c.max_hp); return r <= 0.3 ? "low" : r <= 0.6 ? "mid" : ""; }

  // ------------------------------------------------------------------ match screen: only the cards
  const SKILL_ICON = { normal: "ic-sword", skill: "ic-skill", burst: "ic-burst" };
  function skillIcon(s, hl) {
    const dice = Object.entries(s.cost || {}).filter(([k]) => k !== "energy").reduce((n, [, v]) => n + v, 0);
    const el = Object.keys(s.cost || {}).find((k) => ELEMENTS.includes(k)) || "omni";
    return `<span class="skill ${s.type} ${hl ? "hl" : ""}" data-el="${el}" data-tip="<b>${esc(s.type_ru)}: ${esc(s.name)}</b> (${esc(costText(s.cost))})\n${esc(s.text)}">${icon(SKILL_ICON[s.type] || "ic-star4")}<i>${dice}</i></span>`;
  }
  function unit(c, target, hlSkill) {
    const frozen = c.statuses.some((s) => s.id === "frozen");
    const chips = [...c.statuses.map(statusChip), ...c.equipment.map((e) => `<span class="chip">${icon("ic-shield")}${esc(e.name)}</span>`)];
    return `<div class="unit ${c.active ? "active" : ""}">
      <div class="u-aura">${c.aura.map((a) => badge(a)).join("")}</div>
      <div class="ccard ${c.active ? "active" : ""} ${c.alive ? "" : "dead"} ${target ? "target" : ""} ${frozen ? "frozen" : ""}" data-el="${c.element}" data-tip="${esc(charTip(c))}">
        <div class="frame ${c.img ? "has-img" : ""}">${c.img ? `<img class="art" src="${esc(c.img)}" alt="" draggable="false">` : `<span class="art-glyph">${icon("el-" + c.element)}</span><span class="mono">${esc((c.name || "?")[0])}</span>`}<span class="cname">${esc(c.name)}</span></div>
        <div class="hp-drop ${hpClass(c)}"><svg viewBox="0 0 24 24"><path d="M12 1.5c3.3 4.3 7.2 7.8 7.2 12.4a7.2 7.2 0 0 1-14.4 0C4.8 9.3 8.7 5.8 12 1.5z" fill="currentColor" stroke="#fff" stroke-width="1.4"/></svg><span>${c.hp}</span></div>
        <span class="u-el">${badge(c.element)}</span>
        <div class="epips">${Array.from({ length: c.max_energy }, (_, i) => `<i class="${i < c.energy ? "on" : ""}">${icon("ic-energy")}</i>`).join("")}</div>
      </div>
      <div class="u-skills ${(c.skills || []).length > 3 ? "many" : ""}">${c.alive ?(c.skills || []).map((s) => skillIcon(s, hlSkill && s.id === hlSkill)).join("") : ""}</div>
      <div class="u-status">${chips.join("")}</div>
    </div>`;
  }
  function summonCard(s) {
    return `<div class="scard" data-el="${s.element || "physical"}" data-tip="<b>${esc(s.name)}</b>\n${esc(s.text)}">
      <div class="sframe">${s.img ? `<img src="${esc(s.img)}" alt="" draggable="false">` : `<span class="sglyph">${icon("ic-summon")}</span>`}</div>
      <span class="suses">${s.usages}</span>${s.damage ? `<span class="sdmg">${s.element ? badge(s.element) : ""}${s.damage}</span>` : ""}<span class="sname">${esc(s.name)}</span></div>`;
  }
  function handCard(c, hl) {
    return `<div class="hc ${hl ? "hl" : ""} ${c.modelled ? "" : "unmodelled"}" data-tip="<b>${esc(c.name)}</b>\n${esc(c.text)}${c.modelled ? "" : "\n⚠ эффект не смоделирован — советник не предлагает эту карту"}">
      <div class="hframe">${c.img ? `<img src="${esc(c.img)}" alt="" draggable="false">` : `<span class="hglyph">${icon("ic-cards")}</span>`}<span class="hname">${esc(c.name)}</span></div>
      <span class="hcost">${c.cost_total}</span></div>`;
  }
  function targets() {
    const r = snap.recommendation, t = { opp: -1, me: -1 };
    if (!r || r.status !== "ok" || !r.action) return t;
    const a = r.action;
    if (["normal_attack", "elemental_skill", "elemental_burst"].includes(a.type)) t.opp = snap.state.opponent.active_index;
    if (["switch_character", "choose_active"].includes(a.type)) t.me = a.target;
    return t;
  }
  function coachLine(r) {
    const text = r ? r.speech || r.message || "" : "Жду состояние партии…";
    const demo = snap.source === "demo" || snap.source === "manual";
    const canSim = demo && r && (r.status === "ok" || r.status === "waiting_opponent");
    return `<div class="coach-line ${r ? esc(r.status) : ""}">
      <button class="cl-btn" data-do="say" data-tip="Повторить вслух (F8)">${icon("ic-voice")}</button>
      <span class="cl-text">${esc(text)}</span>
      ${canSim ? `<button class="cl-btn" data-do="simulate" data-tip="Только в демо: сыграть этот ход в симуляции">${icon("ic-play")}</button>` : ""}
    </div>`;
  }
  function renderArena() {
    const st = snap.state, me = st.player, op = st.opponent, r = snap.recommendation;
    const a = r && r.status === "ok" && r.action ? r.action : {};
    const t = targets();
    const hlSkill = ["normal_attack", "elemental_skill", "elemental_burst"].includes(a.type) ? a.skill : null;
    const actor = a.actor ?? me.active_index;
    const hlCard = ["play_card", "elemental_tuning"].includes(a.type) ? a.card : null;
    const mine = (st.pending_choose || st.active_player) === "player";
    let marked = false;
    const hand = me.hand.map((c) => { const hl = !marked && c.id === hlCard; marked ||= hl; return handCard(c, hl); }).join("")
      + Array.from({ length: Math.min(10, me.hand_hidden || 0) }, () => `<div class="hc back"></div>`).join("");
    const row = (p, cls, tIndex, skillFor) => `
      <div class="arena-row ${cls}">
        <div class="a-side left">${p.combat_statuses.map(statusChip).join("")}</div>
        <div class="units">${p.characters.map((c, i) => unit(c, i === tIndex, i === skillFor ? hlSkill : null)).join("")}</div>
        <div class="a-side right">${p.summons.map(summonCard).join("")}</div>
      </div>`;
    $("#layout").innerHTML = `<div class="arena">
      ${row(op, "opp", t.opp, -1)}
      <div class="arena-mid"><svg><use href="#ornament"/></svg><span>Раунд ${st.round}</span>
        <button class="turn ${mine ? "me" : "opp"}" id="turnPill" data-tip="Нажмите, если чей ход определён неверно">${mine ? "Ваш ход" : "Ход противника"}</button><svg><use href="#ornament"/></svg></div>
      ${row(me, "me", t.me, actor)}
      <div class="arena-hand">${hand}</div>
      ${coachLine(r)}
    </div>`;
    $("#turnPill").onclick = () => api("/api/turn", { side: st.active_player === "player" ? "opponent" : "player" });
  }

  // ------------------------------------------------------------------ voice
  // New advice is said once after each analysis. Google Chirp 3 HD voice via the local server
  // when a key is set, otherwise the Windows voice (speechSynthesis).
  let spoken = { serial: null, text: "" }, sayToken = 0, audio = null, googleFailed = false;
  const voiceCfg = () => (snap && snap.voice) || { enabled: true };
  function stopVoice() {
    sayToken++;
    if (audio) { audio.pause(); audio = null; }
    if (window.speechSynthesis) speechSynthesis.cancel();
  }
  function systemVoice(text) {
    if (!window.speechSynthesis) return;
    const u = new SpeechSynthesisUtterance(text);
    const ru = speechSynthesis.getVoices().filter((v) => (v.lang || "").toLowerCase().startsWith("ru"));
    u.voice = ru.find((v) => /irina|svetlana|dariya|ekaterina/i.test(v.name)) || ru.find((v) => !/pavel|dmitry/i.test(v.name)) || ru[0] || null;
    u.lang = "ru-RU";
    u.rate = voiceCfg().rate || 1.05;
    speechSynthesis.speak(u);
  }
  async function say(text, force = false) {
    if (!text || (!voiceCfg().enabled && !force)) return;
    stopVoice();
    const token = sayToken;
    if (voiceCfg().provider !== "system") {
      try {
        const res = await fetch(`/api/tts?text=${encodeURIComponent(text)}`);
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
        const url = URL.createObjectURL(await res.blob());
        if (token !== sayToken) { URL.revokeObjectURL(url); return; }  // newer advice arrived meanwhile
        audio = new Audio(url);
        audio.onended = () => URL.revokeObjectURL(url);
        await audio.play();
        googleFailed = false;
        return;
      } catch (e) {
        if (token !== sayToken) return;
        if (!googleFailed) toast(`Нейроголос недоступен (${e.message}). Пока говорю голосом Windows.`, "warn");
        googleFailed = true;
      }
    }
    systemVoice(text);
  }
  function autoSpeak() {
    const r = snap.recommendation;
    if (!snap.state || !r || !r.speech || snap.rec_serial === spoken.serial) return;
    spoken.serial = snap.rec_serial;
    // advice for a new position is repeated even if the words match; status lines only once
    if (r.status !== "ok" && r.speech === spoken.text) return;
    spoken.text = r.speech;
    say(r.speech);
  }
  if (window.speechSynthesis) speechSynthesis.getVoices();  // voices load lazily

  function openVoice() {
    const v = voiceCfg();
    openModal("Голосовой помощник", `
      <label class="check-row"><input type="checkbox" id="vOn" ${v.enabled ? "checked" : ""}> Говорить советы вслух после анализа экрана</label>
      <h5 class="mh">ГОЛОС</h5>
      <select class="input" id="vVoice">${(v.voices || []).map((o) => `<option value="${esc(o.id)}" ${o.id === v.voice ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select>
      <h5 class="mh">СКОРОСТЬ РЕЧИ · <span id="vRateVal">${v.rate}</span></h5>
      <input type="range" id="vRate" min="0.8" max="1.4" step="0.05" value="${v.rate || 1.1}" style="width:100%">
      <h5 class="mh">КЛЮЧ GOOGLE CLOUD TEXT-TO-SPEECH · НЕОБЯЗАТЕЛЬНО</h5>
      <input class="input" id="vKey" type="password" autocomplete="off" placeholder="${v.has_key ? "Ключ сохранён — оставьте пустым, чтобы не менять" : "Нужен только для голосов Google"}">
      <div class="help">Голоса-нейросети Microsoft работают без ключа, в сервис уходит только текст совета.
        Ключ Google: console.cloud.google.com → «Cloud Text-to-Speech API» → «Учётные данные» → «Создать ключ API»; хранится только на этом компьютере (data/voice.json).
        ${v.has_key ? `<br><a href="#" id="vDelKey">Удалить ключ</a>` : ""}</div>`,
      `<button class="btn" id="vTest">${icon("ic-voice")}Проверить</button><button class="btn" data-close>Отмена</button><button class="btn primary" id="vSave">Сохранить</button>`);
    $("#vRate").oninput = () => ($("#vRateVal").textContent = $("#vRate").value);
    const save = async () => {
      const body = { enabled: $("#vOn").checked, voice: $("#vVoice").value, rate: parseFloat($("#vRate").value) };
      if ($("#vKey").value.trim()) body.api_key = $("#vKey").value.trim();
      snap.voice = await api("/api/voice", body);
      googleFailed = false;
    };
    $("#vSave").onclick = async () => { await save(); closeModal(); toast("Настройки голоса сохранены", "ok"); };
    $("#vTest").onclick = async () => { await save(); say("Привет! Я буду подсказывать, что делать в каждом ходе.", true); };
    const del = $("#vDelKey");
    if (del) del.onclick = async (e) => { e.preventDefault(); snap.voice = await api("/api/voice", { api_key: "" }); closeModal(); toast("Ключ удалён — буду говорить голосом Windows"); };
  }

  // ------------------------------------------------------------------ debug drawer
  function renderDebug() {
    $$("#debugTabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === debugTab));
    const d = snap.debug || {}, body = $("#debugBody");
    if (debugTab === "shot") {
      const t = Date.now();
      body.innerHTML = `<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start">
        <div class="shot-wrap"><img src="/debug/latest_annotated.jpg?t=${t}" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'empty',textContent:'Скриншотов пока нет — нажмите «Захват».'}))"></div>
        <div><table class="t"><tr><th>OCR</th><td>${esc(d.ocr)}</td></tr><tr><th>Разметка</th><td>${d.layout_calibrated ? "откалибрована" : "по умолчанию — нужна калибровка"}</td></tr>
        <tr><th>Шаблоны</th><td>${esc(Object.entries(d.templates || {}).map(([k, v]) => `${k}: ${v}`).join(", "))}</td></tr>
        <tr><th>Тайминги</th><td>${esc(JSON.stringify(d.timings || {}))}</td></tr></table></div></div>`;
    } else if (debugTab === "conf") {
      const cm = snap.confidence_map || {};
      const rows = Object.entries(cm).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(JSON.stringify(v.value))}</td><td>${pct(v.confidence)}</td><td><span class="kn ${v.knowledge}">${v.knowledge.toUpperCase()}</span></td><td>${esc(v.source)}</td></tr>`).join("");
      const regs = (d.regions || []).map((r) => `<tr><td>${esc(r.name)}</td><td>${esc(JSON.stringify(r.value))}</td><td>${pct(r.confidence)}</td><td>${esc(r.rect.join(","))}</td></tr>`).join("");
      body.innerHTML = rows ? `<table class="t"><tr><th>Поле</th><th>Значение</th><th>Уверенность</th><th>Статус</th><th>Источник</th></tr>${rows}</table>
        <h4 style="color:var(--ink-3)">Регионы экрана</h4><table class="t"><tr><th>Регион</th><th>Значение</th><th>Уверенность</th><th>x,y,w,h</th></tr>${regs}</table>` : `<div class="empty">Данные распознавания появятся после захвата экрана (в демо все значения KNOWN).</div>`;
    } else if (debugTab === "cand") {
      body.innerHTML = `<table class="t"><tr><th>#</th><th>Действие</th><th>Шанс</th><th>Оценка</th><th>Быстрое</th></tr>${(d.candidates || []).map((c, i) => `<tr><td>${i + 1}</td><td>${esc(c.label)}</td><td>${pct(c.win_probability)}</td><td>${c.value}</td><td>${c.fast ? "да" : ""}</td></tr>`).join("")}</table>`;
    } else if (debugTab === "tree") {
      body.innerHTML = (d.tree || []).map((t) => `<div style="margin-bottom:12px"><b style="color:var(--gold)">${esc(t.label)}</b> — ${pct(t.win_probability)}<br>
        ${t.line.map((s) => `<span style="color:${s.side === "opponent" ? "var(--enemy)" : "var(--ink)"}">${s.side === "opponent" ? "⟵ " : "⟶ "}${esc(s.label)}${s.side === "opponent" ? ` (p≈${s.p})` : ""}</span>`).join("<br>")}
        ${t.replies?.length ? `<div style="color:var(--ink-3);margin-top:4px">Ответы противника: ${t.replies.map((r) => `${esc(r.label)} (p=${r.probability}, урон ${r.damage_to_us})`).join("; ")}</div>` : ""}</div>`).join("") || `<div class="empty">Нет данных</div>`;
    } else if (debugTab === "log") {
      body.innerHTML = `<pre>${(d.logs || []).slice().reverse().map((l) => `${new Date(l.time * 1000).toLocaleTimeString()} ${l.level.padEnd(5)} ${l.name}: ${esc(l.message)}`).join("\n")}</pre>`;
    } else if (debugTab === "json") {
      body.innerHTML = `<pre>${esc(JSON.stringify(d.raw_state, null, 2))}</pre>`;
    }
  }

  // ------------------------------------------------------------------ modals
  function openModal(title, bodyHtml, footHtml) {
    $("#modalTitle").textContent = title;
    $("#modalBody").innerHTML = bodyHtml;
    $("#modalFoot").innerHTML = footHtml || "";
    $("#modal").hidden = false;
  }
  function closeModal() { $("#modal").hidden = true; }

  async function loadKb() {
    if (kbChars.length) return;
    [kbChars, kbCards] = await Promise.all([api("/api/kb/characters"), api("/api/kb/cards")]);
    $("#dlChars").innerHTML = kbChars.map((c) => `<option value="${esc(c.name)}">${EL_RU[c.element] || ""} · ${c.hp} HP${c.available ? "" : " · недоступна"}</option>`).join("");
    $("#dlCards").innerHTML = kbCards.map((c) => `<option value="${esc(c.name)}">${c.modelled ? "" : "(не смоделирована) "}${esc(c.type)}</option>`).join("");
  }
  async function openSetup() {
    await loadKb();
    const su = snap.setup || {};
    const cur = snap.state;
    const val = (side, i) => (su[side] && su[side][i]) || (cur && cur[side].characters[i] && cur[side].characters[i].known ? cur[side].characters[i].id : "");
    const slots = (side) => [0, 1, 2].map((i) => `<div class="field"><span class="slot">${i + 1}</span><input class="input" list="dlChars" data-side="${side}" data-i="${i}" value="${esc(val(side, i))}" placeholder="Персонаж…"></div>`).join("");
    const hand = (su.player_hand && su.player_hand.length ? su.player_hand : cur ? cur.player.hand.map((h) => h.id) : []).join("; ");
    const turn = su.turn || (cur ? cur.active_player : "");
    openModal("Настройка партии", `
      <div class="setup-grid"><div><h5>Ваша команда</h5>${slots("player")}</div><div><h5>Команда противника</h5>${slots("opponent")}</div></div>
      <h5 style="margin:16px 0 8px;font-size:12px;letter-spacing:.14em;color:var(--ink-3)">КАРТЫ В РУКЕ (через «;»)</h5>
      <input class="input" id="setupHand" list="dlCards" value="${esc(hand)}" placeholder="Стратег; Поручи это мне!">
      <div class="help">Данные персонажей и карт берутся из базы, импортированной с вики. Составы команд делают распознавание экрана намного надёжнее.</div>
      <h5 style="margin:16px 0 8px;font-size:12px;letter-spacing:.14em;color:var(--ink-3)">ЧЕЙ ХОД</h5>
      <div class="seg" id="setupTurn"><button data-v="" class="${!turn ? "on" : ""}">Авто</button><button data-v="player" class="${turn === "player" ? "on" : ""}">Мой</button><button data-v="opponent" class="${turn === "opponent" ? "on" : ""}">Противника</button></div>`,
      `<button class="btn" data-close>Отмена</button><button class="btn primary" id="setupSave">Сохранить</button>`);
    $$("#setupTurn button").forEach((b) => (b.onclick = () => { $$("#setupTurn button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); }));
    $("#setupSave").onclick = async () => {
      const byName = Object.fromEntries(kbChars.map((c) => [c.name, c.id]));
      const pick = (side) => $$(`input[data-side="${side}"]`).map((i) => byName[i.value.trim()] || i.value.trim()).filter(Boolean);
      const cardByName = Object.fromEntries(kbCards.map((c) => [c.name, c.id]));
      const handIds = $("#setupHand").value.split(";").map((x) => x.trim()).filter(Boolean).map((x) => cardByName[x] || x);
      const turnV = $("#setupTurn .on")?.dataset.v || null;
      await api("/api/setup", { player: pick("player"), opponent: pick("opponent"), player_hand: handIds, turn: turnV });
      closeModal();
      toast("Настройки партии сохранены", "ok");
    };
  }
  function openStateEditor() {
    const raw = snap.debug && snap.debug.raw_state;
    openModal("Состояние партии (JSON)", `<textarea class="input" id="stateJson">${esc(JSON.stringify(raw || {}, null, 2))}</textarea><div class="help">Можно исправить любое поле вручную — советник пересчитает план.</div>`,
      `<button class="btn" data-close>Отмена</button><button class="btn primary" id="stateApply">Применить</button>`);
    $("#stateApply").onclick = async () => {
      let data;
      try { data = JSON.parse($("#stateJson").value); } catch (e) { toast("Некорректный JSON", "error"); return; }
      await api("/api/state", { state: data });
      closeModal();
    };
  }
  function mdToHtml(md) {
    return md.split("\n").map((l) => l.startsWith("# ") ? `<h1>${esc(l.slice(2))}</h1>` : l.startsWith("## ") ? `<h2>${esc(l.slice(3))}</h2>` : l.startsWith("- ") ? `<li>${esc(l.slice(2))}</li>` : l.trim() ? `<p>${esc(l)}</p>` : "").join("").replace(/(<li>.*?<\/li>)+/g, (m) => `<ul>${m}</ul>`);
  }
  function openEndMatch() {
    const w = snap.state && snap.state.winner;
    openModal("Завершить партию", `<p style="color:var(--ink-2);margin-top:0">Результат партии:</p>
      <div class="seg" id="resSeg"><button data-v="player" class="${w === "player" ? "on" : ""}">Победа</button><button data-v="opponent" class="${w === "opponent" ? "on" : ""}">Поражение</button><button data-v="draw" class="${w === "draw" ? "on" : ""}">Ничья</button></div>
      <label style="display:flex;gap:8px;align-items:center;margin-top:14px;color:var(--ink-2)"><input type="checkbox" id="resAi"> Добавить комментарий AI (${esc(snap.provider)})</label>`,
      `<button class="btn" data-close>Отмена</button><button class="btn primary" id="resGo">Сделать разбор</button>`);
    $$("#resSeg button").forEach((b) => (b.onclick = () => { $$("#resSeg button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); }));
    $("#resGo").onclick = async () => {
      const result = $("#resSeg .on")?.dataset.v || null;
      const rep = await api("/api/match/end", { result, ai: $("#resAi").checked });
      openModal("Разбор партии", `<div class="report">${mdToHtml(rep.markdown || "")}</div><div class="help">Сохранено: ${esc(rep.path || "")}</div>`, `<button class="btn primary" data-close>Готово</button>`);
    };
  }

  // ------------------------------------------------------------------ toasts & tooltip
  function toast(msg, level = "") {
    const el = document.createElement("div");
    el.className = `toast ${level}`;
    el.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(() => { el.style.transition = "opacity .4s"; el.style.opacity = "0"; setTimeout(() => el.remove(), 400); }, 3800);
  }
  const tip = $("#tooltip");
  document.addEventListener("mouseover", (e) => {
    const t = e.target.closest("[data-tip]");
    if (!t) { tip.hidden = true; return; }
    tip.innerHTML = t.getAttribute("data-tip").replace(/&lt;b&gt;/g, "<b>").replace(/&lt;\/b&gt;/g, "</b>");
    tip.hidden = false;
  });
  document.addEventListener("mousemove", (e) => {
    if (tip.hidden) return;
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > innerWidth - 8) x = e.clientX - w - pad;
    if (y + h > innerHeight - 8) y = e.clientY - h - pad;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${Math.max(8, y)}px`;
  });
  document.addEventListener("mouseleave", () => (tip.hidden = true));

  // ------------------------------------------------------------------ actions
  async function doCapture() { toast("Анализирую экран…"); await api("/api/capture", {}); }
  async function doAction(act, el) {
    switch (act) {
      case "capture": return doCapture();
      case "simulate": return api("/api/simulate", {});
      case "explain": {
        toast("Запрашиваю объяснение…");
        const res = await api("/api/explain", {});
        return openModal("Почему этот ход", `<p style="white-space:pre-wrap;margin:0">${esc(res.text)}</p>`, `<button class="btn primary" data-close>Понятно</button>`);
      }
      case "say": return say(snap.recommendation && (snap.recommendation.speech || snap.recommendation.message), true);
      case "voice": return openVoice();
      case "setup": return openSetup();
      case "end": return openEndMatch();
      case "state": return openStateEditor();
      case "debug": $("#debug").hidden = false; return renderDebug();
    }
  }
  document.addEventListener("click", (e) => {
    const demo = e.target.closest("[data-demo]");
    if (demo) { $("#menu").hidden = true; api(`/api/demo/${demo.dataset.demo}`, {}); return; }
    const start = e.target.closest("[data-start]");
    if (start) { if (start.dataset.start === "watch") api("/api/watch", { on: !snap?.watching }); return; }
    const d = e.target.closest("[data-do]");
    if (d) { doAction(d.dataset.do, d); return; }
    const m = e.target.closest("#menu [data-act]");
    if (m) { $("#menu").hidden = true; doAction(m.dataset.act); return; }
    if (e.target.closest("[data-close]")) { closeModal(); return; }
    if (!e.target.closest(".menu-wrap")) $("#menu").hidden = true;
  });
  $("#btnMenu").onclick = (e) => { e.stopPropagation(); $("#menu").hidden = !$("#menu").hidden; };
  $("#btnWatch").onclick = () => api("/api/watch", { on: !snap?.watching });
  $(".brand").onclick = () => {  // logo = back to the start screen
    if (!snap || !snap.state) return;
    stopVoice();
    spoken = { serial: null, text: "" };
    api("/api/reset", {});
  };
  $("#btnVoice").onclick = async () => {
    const on = !voiceCfg().enabled;
    snap.voice = await api("/api/voice", { enabled: on });
    $("#btnVoice").classList.toggle("on", on);
    if (!on) stopVoice();
    toast(on ? "Голос включён" : "Голос выключен");
  };
  $("#modalClose").onclick = closeModal;
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  $("#btnDebugClose").onclick = () => ($("#debug").hidden = true);
  $("#debugTabs").onclick = (e) => { const b = e.target.closest("button"); if (b) { debugTab = b.dataset.tab; renderDebug(); } };

  document.addEventListener("keydown", (e) => {
    if (e.key === "F11") {  // full screen: the app window in the desktop build, the browser otherwise
      e.preventDefault();
      if (window.pywebview && window.pywebview.api && window.pywebview.api.toggle_fullscreen) window.pywebview.api.toggle_fullscreen();
      else if (document.fullscreenElement) document.exitFullscreen();
      else document.documentElement.requestFullscreen().catch(() => {});
      return;
    }
    if (e.key === "Escape") { closeModal(); $("#menu").hidden = true; }
    if (e.target.matches("input, textarea")) return;
    if (e.key === "F9") { e.preventDefault(); doCapture(); }
    if (e.key === "F8") { e.preventDefault(); doAction("say"); }
    if (e.key === "`") { $("#debug").hidden = !$("#debug").hidden; if (!$("#debug").hidden) renderDebug(); }
  });

  // ------------------------------------------------------------------ ambient effects
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // golden dust / sparkles drifting upwards (canvas, paused when the window is hidden)
  (function sparkles() {
    const cv = $("#sparkles");
    if (!cv || reduceMotion) return;
    const ctx = cv.getContext("2d");
    let W = 0, H = 0, dpr = 1, parts = [];
    const make = (initial) => ({
      x: Math.random() * W, y: initial ? Math.random() * H : H + 10,
      r: 0.6 + Math.random() * 1.8, vy: 0.15 + Math.random() * 0.45, vx: (Math.random() - 0.5) * 0.25,
      tw: Math.random() * Math.PI * 2, tws: 0.01 + Math.random() * 0.03, star: Math.random() < 0.18,
    });
    function resize() {
      dpr = Math.min(2, devicePixelRatio || 1);
      W = innerWidth; H = innerHeight;
      cv.width = W * dpr; cv.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.round(Math.min(90, (W * H) / 22000));
      parts = Array.from({ length: n }, () => make(true));
    }
    function star(x, y, s) {
      ctx.beginPath();
      ctx.moveTo(x, y - s); ctx.quadraticCurveTo(x, y, x + s, y); ctx.quadraticCurveTo(x, y, x, y + s);
      ctx.quadraticCurveTo(x, y, x - s, y); ctx.quadraticCurveTo(x, y, x, y - s); ctx.fill();
    }
    let cleared = false;
    function frame() {
      const active = !document.hidden && document.body.classList.contains("welcome-mode");
      if (!active && !cleared) { ctx.clearRect(0, 0, W, H); cleared = true; }
      if (active) {
        cleared = false;
        ctx.clearRect(0, 0, W, H);
        for (const p of parts) {
          p.y -= p.vy; p.x += p.vx + Math.sin(p.tw * 0.7) * 0.15; p.tw += p.tws;
          if (p.y < -10 || p.x < -10 || p.x > W + 10) Object.assign(p, make(false));
          const a = 0.25 + 0.55 * (0.5 + 0.5 * Math.sin(p.tw));
          ctx.fillStyle = `rgba(255, 236, 190, ${a})`;
          ctx.shadowColor = "rgba(247, 214, 140, .9)"; ctx.shadowBlur = p.star ? 10 : 6;
          if (p.star) star(p.x, p.y, p.r * 3.2);
          else { ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill(); }
        }
      }
      requestAnimationFrame(frame);
    }
    addEventListener("resize", resize);
    resize();
    requestAnimationFrame(frame);
  })();

  // gentle parallax of the background artwork following the mouse
  if (!reduceMotion) {
    let tx = 0, ty = 0, cx = 0, cy = 0;
    addEventListener("mousemove", (e) => { tx = e.clientX / innerWidth - 0.5; ty = e.clientY / innerHeight - 0.5; });
    (function drift() {
      cx += (tx - cx) * 0.04; cy += (ty - cy) * 0.04;
      document.body.style.setProperty("--px", cx.toFixed(4));
      document.body.style.setProperty("--py", cy.toFixed(4));
      requestAnimationFrame(drift);
    })();
  }

  api("/api/snapshot").then(render).catch(() => {});
  connect();
})();
