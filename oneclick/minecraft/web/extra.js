// OneClickServerMC — 追加機能の画面（コピー・一覧・コマンド補完・予約・招待カード など）
"use strict";

// ================================================================ コピー（名前・ID・アドレスなど）
function copyText(text, label) {
  const done = () => toast((label ? label + "を" : "") + "コピーしました");
  const fallback = () => {
    const t = document.createElement("textarea");
    t.value = text; t.style.position = "fixed"; t.style.opacity = "0";
    document.body.appendChild(t); t.select();
    try { document.execCommand("copy"); done(); } catch (e) { toast("コピーできませんでした", "error"); }
    t.remove();
  };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
  else fallback();
}
// コピーのボタン（名前・ID・アドレスなど）
const cpBtn = (text, label = "コピー", what = "") =>
  `<button type="button" class="btn sm ghost cp" data-cp="${esc(text)}" data-cplabel="${esc(what)}" title="${esc(what ? what + "をコピー" : "コピー")}">${esc(label)}</button>`;
// プレイヤーのID（UUID）をコピーするボタン（押したときに調べる）
const cpIdBtn = (name) => `<button type="button" class="btn sm ghost cp" data-cpid="${esc(name)}" title="${esc(name)} のID（UUID）をコピー">ID</button>`;

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-cp]");
  if (b) { e.preventDefault(); e.stopPropagation(); copyText(b.dataset.cp, b.dataset.cplabel); return; }
  const c = e.target.closest("[data-cpid]");
  if (c) {
    e.preventDefault(); e.stopPropagation();
    try {
      const r = await api("player_ids", { id: S.server });
      const p = r.players.find((x) => x.name.toLowerCase() === c.dataset.cpid.toLowerCase());
      if (p && p.uuid) copyText(p.uuid, c.dataset.cpid + " のID");
      else toast("IDが見つかりません（一度でもサーバーに入った人だけ、IDが分かります）", "warn", 5000);
    } catch (err) { fail(err); }
  }
}, true);

// ================================================================ 入退出の音
const soundOn = () => { try { return localStorage.getItem("mcmh_sound") !== "0"; } catch (e) { return true; } };
const setSound = (v) => { try { localStorage.setItem("mcmh_sound", v ? "1" : "0"); } catch (e) { /* 何もしない */ } };
let audioCtx = null;
const wakeAudio = () => {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch (e) { /* 音が出せない環境 */ }
};
document.addEventListener("pointerdown", wakeAudio, { once: false, passive: true });
function beep(kind) {
  if (!audioCtx) return;
  const seq = kind === "join" ? [[660, 0], [880, 0.12]] : [[660, 0], [440, 0.12]];
  const t0 = audioCtx.currentTime;
  for (const [f, dt] of seq) {
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.type = "triangle"; o.frequency.value = f;
    g.gain.setValueAtTime(0.0001, t0 + dt);
    g.gain.exponentialRampToValueAtTime(0.18, t0 + dt + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dt + 0.18);
    o.connect(g); g.connect(audioCtx.destination);
    o.start(t0 + dt); o.stop(t0 + dt + 0.2);
  }
}
// 状態を調べるたびに呼ぶ。動いているサーバーの人数の変化を見て、音を鳴らす
function checkPlayerSound() {
  const now = {};
  for (const s of S.servers) if (s.status === "running") now[s.id] = s.players || [];
  const prev = S.prevPlayers;
  S.prevPlayers = now;
  if (!prev || !soundOn()) return;
  for (const id of Object.keys(now)) {
    if (!(id in prev)) continue;
    const joined = now[id].filter((n) => !prev[id].includes(n));
    const left = prev[id].filter((n) => !now[id].includes(n));
    const name = (S.servers.find((x) => x.id === id) || {}).name || "";
    if (joined.length) { beep("join"); toast(`👋 ${joined.join("、")} が入りました（${name}）`, "ok", 4500); }
    if (left.length) { beep("leave"); toast(`🚪 ${left.join("、")} が出ました（${name}）`, "ok", 4500); }
  }
}

// ================================================================ 起動の進み具合・重さの注意
function paintProgress(root, p) {
  const box = $("[data-prog]", root);
  if (!box) return;
  if (!p) { box.hidden = true; return; }
  box.hidden = false;
  $("[data-progbar]", box).style.width = Math.min(100, p.pct) + "%";
  $("[data-progtxt]", box).textContent = `${p.pct}%　${p.stage || "起動しています"}`;
}
function paintLag(root, tps, status) {
  const el = $("[data-lag-banner]", root);
  if (!el) return;
  if (status === "running" && tps != null && tps < 12) {
    el.hidden = false;
    el.innerHTML = `<b>サーバーが重くなっています（TPS ${Number(tps).toFixed(1)}。20 が快適）</b><div class="small">「重さ・最適化」タブで、原因をさがせます。</div>
      <div class="row" style="margin-top:8px"><button class="btn sm" data-lagtab>重さの診断を見る</button></div>`;
    $("[data-lagtab]", el).onclick = () => { S.tab = "perf"; viewServer($("#main")); };
  } else el.hidden = true;
}

// ================================================================ 起動前チェック
async function renderPreflight(el, s) {
  if (s.status !== "stopped") { el.innerHTML = ""; return; }
  el.innerHTML = `<div class="panel"><div class="row"><h3 class="grow">起動前チェック</h3><button class="btn sm" data-pfgo>もう一度調べる</button></div><div data-pfbody class="small muted" style="margin-top:8px">調べています…</div></div>`;
  const run = async () => {
    const body = $("[data-pfbody]", el);
    if (!body) return;
    body.textContent = "調べています…";
    let r;
    try { r = await api("preflight", { id: s.id }); } catch (e) { body.textContent = e.message; return; }
    if (!el.isConnected) return;
    const ic = { ok: "✅", info: "ℹ️", warn: "⚠️", error: "⛔" };
    const bad = r.items.filter((i) => i.level === "error").length, warn = r.items.filter((i) => i.level === "warn").length;
    body.className = "";
    body.innerHTML = `<div class="${bad ? "note error" : warn ? "note warn" : "note ok"}" style="margin:0 0 8px"><b>${bad ? `直したほうがいいところが ${bad}つあります` : warn ? "起動できます（気をつけたいところが少しあります）" : "起動して大丈夫です"}</b></div>
      <div class="stack" style="gap:6px">${r.items.map((i) => `<div class="small"><span aria-hidden="true">${ic[i.level] || "・"}</span> <b>${esc(i.title)}</b>${i.detail ? `<div class="muted" style="margin-left:1.6em">${esc(i.detail)}</div>` : ""}${i.fix ? `<div style="margin-left:1.6em">→ ${esc(i.fix)}</div>` : ""}</div>`).join("")}</div>`;
  };
  $("[data-pfgo]", el).onclick = run;
  run();
}

// ================================================================ サーバー一覧
function listCardHtml(s) {
  const [cls, label] = statusInfo(s);
  const running = s.status === "running", starting = s.status === "starting";
  const tps = running && s.tps != null ? `<span class="badge ${s.tps < 12 ? "warn" : "ok"}">TPS ${Number(s.tps).toFixed(1)}</span>` : "";
  return `<div class="panel list-card" data-card="${esc(s.id)}">
    <div class="row" style="gap:10px"><span class="dot ${cls}"></span><h3 class="grow" style="margin:0">${esc(s.name)}</h3><span class="badge">${esc(label)}</span>${tps}</div>
    <div class="chips" style="margin:8px 0"><span class="badge">マイクラ ${esc(s.mc)}</span><span class="badge">${esc(s.loader_label)}</span><span class="badge">MOD ${s.mods}個</span><span class="badge">メモリ ${gb(s.memory_mb)}</span><span class="badge">ポート ${esc(s.port)}</span></div>
    ${starting && s.progress ? `<div class="meter" style="margin:6px 0"><i style="width:${Math.min(100, s.progress.pct)}%;background:#80ff20;transition:width .3s"></i></div><div class="small muted">${s.progress.pct}%　${esc(s.progress.stage || "")}</div>` : ""}
    <div class="small" style="margin:6px 0"><b>参加中: ${(s.players || []).length}人</b>${(s.players || []).length ? "　" + (s.players || []).map((n) => `<span class="nm">${esc(n)}</span>`).join("、") : ""}</div>
    <div class="small" style="margin:6px 0">${s.address ? `アドレス: <code>${esc(s.address)}</code> ${cpBtn(s.address, "コピー", "アドレス")}` : `<span class="muted">アドレスは、まだ決まっていません</span>`}</div>
    <div class="row" style="margin-top:10px;gap:6px"><button class="btn sm primary" data-lopen>開く</button>
      <button class="btn sm" data-lstart ${s.status === "stopped" ? "" : "disabled"}>▶ 起動</button>
      <button class="btn sm" data-lstop ${running || starting ? "" : "disabled"}>■ 停止</button>
      <span class="grow"></span>${cpBtn(s.id, "IDコピー", "サーバーのID")}</div></div>`;
}
function paintList() {
  const box = $("[data-listbox]");
  if (!box) return;
  if ($("#modal-root").children.length) return;
  const running = S.servers.filter((s) => s.status === "running").length;
  const players = S.servers.reduce((n, s) => n + (s.players || []).length, 0);
  $("[data-listsum]").textContent = `${S.servers.length}個のサーバー・動いている: ${running}個・参加中: ${players}人`;
  box.innerHTML = S.servers.length ? S.servers.map(listCardHtml).join("") : `<div class="empty">まだサーバーがありません。</div>`;
  $$("[data-card]", box).forEach((card) => {
    const id = card.dataset.card;
    $("[data-lopen]", card).onclick = () => go("server", { server: id, tab: "home", consoleNext: 0 });
    $("[data-lstart]", card).onclick = async () => { await startServer(id); await refreshState(); paintList(); };
    $("[data-lstop]", card).onclick = async () => {
      const sv = S.servers.find((x) => x.id === id);
      if (!(await ask("サーバーを止める", `<p>「${esc(sv.name)}」を止めますか？ワールドは保存されます。${(sv.players || []).length ? `<br><b>いま ${(sv.players || []).length}人 遊んでいます。</b>` : ""}</p>`, "止める", { danger: true }))) return;
      try { await api("server_stop", { id }); toast("止めています…"); } catch (e) { fail(e); }
      setTimeout(async () => { await refreshState().catch(() => {}); paintList(); }, 800);
    };
  });
}
function viewList(root) {
  root.innerHTML = `<div class="page"><div class="srv-head"><div class="grow"><h1>サーバー一覧</h1><div class="meta"><span data-listsum></span></div></div></div>
    <div class="grid2" data-listbox style="margin-top:14px"></div></div>`;
  paintList();
}

// ================================================================ コマンドの辞典・補完
const CMD_GROUPS = [["world", "ワールド（時間・天気・ルール）"], ["people", "プレイヤー"], ["manage", "管理（入れる人・追い出す・保存）"]];
const CMD_DICT = [
  // ワールド
  { c: "time set day", ja: "時間を朝にする", g: "world" }, { c: "time set noon", ja: "時間をお昼にする", g: "world" },
  { c: "time set night", ja: "時間を夜にする", g: "world" }, { c: "time set midnight", ja: "時間を真夜中にする", g: "world" },
  { c: "weather clear", ja: "天気を晴れにする", g: "world" }, { c: "weather rain", ja: "天気を雨にする", g: "world" },
  { c: "weather thunder", ja: "天気を雷雨にする", g: "world" },
  { c: "difficulty peaceful", ja: "難しさを「ピースフル」（敵が出ない）にする", g: "world" }, { c: "difficulty easy", ja: "難しさを「イージー」にする", g: "world" },
  { c: "difficulty normal", ja: "難しさを「ノーマル」にする", g: "world" }, { c: "difficulty hard", ja: "難しさを「ハード」にする", g: "world" },
  { c: "gamerule keepInventory true", ja: "死んでも持ち物を落とさない", g: "world" }, { c: "gamerule keepInventory false", ja: "死んだら持ち物を落とす（普通）", g: "world" },
  { c: "gamerule doDaylightCycle false", ja: "時間を止める（ずっと同じ明るさ）", g: "world" }, { c: "gamerule doDaylightCycle true", ja: "時間を動かす（普通）", g: "world" },
  { c: "gamerule mobGriefing false", ja: "クリーパーなどが地形をこわさないようにする", g: "world" }, { c: "gamerule doMobSpawning false", ja: "敵や動物が自然に出ないようにする", g: "world" },
  { c: "setworldspawn", ja: "いまの場所を、ワールドの出発地点にする（本人がコンソールでは使えません）", g: "world" },
  { c: "worldborder set <大きさ>", ja: "行ける範囲を決める（例: worldborder set 5000）", g: "world" },
  { c: "seed", ja: "ワールドの「シード値」を見る", g: "world" },
  { c: "kill @e[type=item]", ja: "落ちているアイテムを、全部消す（お掃除）", g: "world", danger: true },
  // プレイヤー
  { c: "list", ja: "いま遊んでいる人の一覧", g: "people" },
  { c: "gamemode survival <名前>", ja: "サバイバルにする", g: "people", p: true }, { c: "gamemode creative <名前>", ja: "クリエイティブにする（何でも置ける・飛べる）", g: "people", p: true },
  { c: "gamemode adventure <名前>", ja: "アドベンチャーにする（ブロックをこわせない）", g: "people", p: true }, { c: "gamemode spectator <名前>", ja: "スペクテイターにする（見るだけ）", g: "people", p: true },
  { c: "tp <名前> <行き先の名前>", ja: "1人目を、2人目のところへ移動させる", g: "people", p: true }, { c: "tp <名前> <x> <y> <z>", ja: "決めた座標へ移動させる", g: "people", p: true },
  { c: "give <名前> <アイテムID> 1", ja: "アイテムをあげる（IDは「コマンド」タブの一覧でコピーできます）", g: "people", p: true },
  { c: "clear <名前>", ja: "持ち物を、全部消す", g: "people", p: true, danger: true }, { c: "kill <名前>", ja: "その人を倒す（やり直し用）", g: "people", p: true, danger: true },
  { c: "effect give <名前> minecraft:speed 60 1", ja: "60秒間、足が速くなる", g: "people", p: true }, { c: "effect clear <名前>", ja: "かかっている効果を消す", g: "people", p: true },
  { c: "xp add <名前> 10 levels", ja: "レベルを10上げる", g: "people", p: true }, { c: "spawnpoint <名前>", ja: "その人の復活地点を、いまの場所にする", g: "people", p: true },
  { c: "msg <名前> <ことば>", ja: "その人にだけ、メッセージを送る", g: "people", p: true }, { c: "say <ことば>", ja: "みんなにメッセージを送る", g: "people" },
  // 管理
  { c: "op <名前>", ja: "OP（管理者）にする", g: "manage", p: true }, { c: "deop <名前>", ja: "OPをやめさせる", g: "manage", p: true },
  { c: "whitelist add <名前>", ja: "ホワイトリスト（入っていい人）に追加", g: "manage", p: true }, { c: "whitelist remove <名前>", ja: "ホワイトリストから外す", g: "manage", p: true },
  { c: "whitelist list", ja: "ホワイトリストの一覧", g: "manage" }, { c: "whitelist on", ja: "ホワイトリストを使う（名簿の人だけ入れる）", g: "manage" },
  { c: "whitelist off", ja: "ホワイトリストをやめる（だれでも入れる・注意）", g: "manage", danger: true },
  { c: "kick <名前>", ja: "サーバーから追い出す（また入れます）", g: "manage", p: true, danger: true },
  { c: "ban <名前>", ja: "二度と入れなくする", g: "manage", p: true, danger: true }, { c: "pardon <名前>", ja: "BANを解除する", g: "manage", p: true },
  { c: "ban-ip <IPアドレス>", ja: "IPアドレスで入れなくする", g: "manage", danger: true }, { c: "pardon-ip <IPアドレス>", ja: "IPアドレスのBANを解除する", g: "manage" },
  { c: "banlist", ja: "BANしている人の一覧", g: "manage" },
  { c: "save-all", ja: "ワールドを、いま保存する", g: "manage" }, { c: "stop", ja: "サーバーを止める（保存してから止まります）", g: "manage", danger: true },
  { c: "help", ja: "使えるコマンドの一覧を見る", g: "manage" },
];
CMD_DICT.forEach((x) => { x.tpl = x.c.split("<")[0]; x.key = (x.c + " " + x.ja).toLowerCase(); });

function cmdMatches(q, n = 8) {
  q = q.replace(/^\//, "").toLowerCase().trim();
  if (!q) return [];
  const words = q.split(/\s+/);
  const pre = CMD_DICT.filter((x) => x.c.toLowerCase().startsWith(q));
  const rest = CMD_DICT.filter((x) => !pre.includes(x) && words.every((w) => x.key.includes(w)));
  return pre.concat(rest).slice(0, n);
}
// 入力欄にコマンドの候補を出す
function bindCmdSuggest(input, getPlayers, onSend) {
  const wrap = input.parentElement;
  wrap.style.position = "relative";
  const box = document.createElement("div");
  box.className = "cmd-sug"; box.hidden = true;
  wrap.appendChild(box);
  let list = [], hi = -1;
  const paint = () => {
    box.innerHTML = list.map((x, i) => `<button type="button" class="cmd-row ${i === hi ? "on" : ""}" data-i="${i}"><code>${esc(x.c)}</code><span>${esc(x.ja)}</span></button>`).join("");
    box.hidden = !list.length;
  };
  const refresh = () => {
    const v = input.value, bare = v.replace(/^\//, "");
    list = cmdMatches(v).filter((x) => x.c.toLowerCase() !== bare.trim().toLowerCase());   // 入力が完成しているときは、候補を出さない
    // 名前を入れる場所なら、いま遊んでいる人も出す
    const need = CMD_DICT.find((x) => x.p && x.tpl.endsWith(" ") && bare.startsWith(x.tpl) && !bare.slice(x.tpl.length).includes(" "));
    if (need) {
      const part = bare.slice(need.tpl.length).toLowerCase();
      const pl = getPlayers().filter((n) => n.toLowerCase().startsWith(part) && n.toLowerCase() !== part).slice(0, 6);
      if (pl.length) list = pl.map((n) => ({ c: need.tpl + n, ja: "いま遊んでいる人", tpl: need.tpl + n + " " }));
    }
    hi = -1; paint();
  };
  const pick = (x) => { input.value = x.tpl || x.c; box.hidden = true; list = []; input.focus(); refresh(); };
  input.addEventListener("input", refresh);
  input.addEventListener("blur", () => setTimeout(() => (box.hidden = true), 150));
  input.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;   // 日本語の変換中は、じゃましない
    if (box.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); hi = (hi + 1) % list.length; paint(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); hi = (hi - 1 + list.length) % list.length; paint(); }
    else if (e.key === "Tab" && list.length) { e.preventDefault(); pick(list[hi < 0 ? 0 : hi]); }
    else if (e.key === "Enter" && hi >= 0) { e.preventDefault(); e.stopImmediatePropagation(); pick(list[hi]); }
    else if (e.key === "Escape") { box.hidden = true; }
  }, true);
  box.addEventListener("mousedown", (e) => {
    const b = e.target.closest("[data-i]");
    if (b) { e.preventDefault(); pick(list[+b.dataset.i]); }
  });
}

// よく使うコマンドのボタン（標準＋自分で足したもの）
const QUICK_DEFAULT = [["朝にする", "time set day"], ["晴れにする", "weather clear"], ["保存する", "save-all"], ["参加者の一覧", "list"]];
const QUICK_MORE = [["夜にする", "time set night"], ["雨にする", "weather rain"], ["持ち物を落とさない", "gamerule keepInventory true"],
  ["落ちたアイテムを消す", "kill @e[type=item]"], ["ホワイトリストON", "whitelist on"], ["みんなにお知らせ", null]];
async function renderQuick(el, s, send) {
  let custom = [];
  try { custom = (await api("quick_cmds_get", { id: s.id })).items; } catch (e) { /* なし */ }
  if (!el.isConnected) return;
  const btn = ([l, c], i, cust) => c === null
    ? `<button class="btn sm" data-qsay>${esc(l)}</button>`
    : `<span class="qb"><button class="btn sm" data-q="${esc(c)}" title="${esc(c)}">${esc(l)}</button>${cust ? `<button class="qx" data-qdel="${i}" title="このボタンを消す" aria-label="${esc(l)} を消す">×</button>` : ""}</span>`;
  el.innerHTML = `${QUICK_DEFAULT.map((x) => btn(x)).join("")}${custom.map((x, i) => btn([x.label, x.cmd], i, true)).join("")}
    <details class="qmore"><summary class="btn sm">もっと</summary><div class="qpop">${QUICK_MORE.map((x) => btn(x)).join("")}<button class="btn sm primary" data-qadd>＋ 自分のボタンを足す</button></div></details>`;
  $$("[data-q]", el).forEach((b) => (b.onclick = () => send(b.dataset.q)));
  const say = $("[data-qsay]", el);
  if (say) say.onclick = async () => {
    const m = modal(`<h2>みんなにお知らせ</h2><p class="muted small">ゲームの中に、メッセージが出ます。</p><input type="text" data-m maxlength="150" placeholder="例: 5分後に休憩します">
      <div class="modal-foot"><button class="btn ghost" data-no>やめる</button><button class="btn primary" data-ok>送る</button></div>`);
    $("[data-no]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = () => { const v = $("[data-m]", m.el).value.trim(); if (v) { send("say " + v); m.close(); } };
  };
  $$("[data-qdel]", el).forEach((b) => (b.onclick = async () => {
    custom.splice(+b.dataset.qdel, 1);
    try { await api("quick_cmds_set", { id: s.id, items: custom }); renderQuick(el, s, send); } catch (e) { fail(e); }
  }));
  $("[data-qadd]", el).onclick = () => {
    const m = modal(`<h2>自分のボタンを足す</h2><p class="muted small">よく使うコマンドを、ボタンにできます。コマンドは、辞典から選んでもOKです。</p>
      <div class="form-grid" style="grid-template-columns:90px 1fr"><div class="lbl">ボタンの名前</div><input type="text" data-l maxlength="24" placeholder="例: 夜にする">
      <div class="lbl">コマンド</div><div><input type="text" data-c maxlength="200" placeholder="例: time set night"></div></div>
      <div class="modal-foot"><button class="btn ghost" data-no>やめる</button><button class="btn primary" data-ok>足す</button></div>`);
    bindCmdSuggest($("[data-c]", m.el), () => (cur() ? cur().players || [] : []));
    $("[data-no]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = async () => {
      const label = $("[data-l]", m.el).value.trim(), cmd = $("[data-c]", m.el).value.trim();
      if (!label || !cmd) return toast("名前とコマンドの両方を入れてください", "warn");
      if (custom.length >= 24) return toast("ボタンは24個までです", "warn");
      try { await api("quick_cmds_set", { id: s.id, items: [...custom, { label, cmd }] }); m.close(); renderQuick(el, s, send); } catch (e) { fail(e); }
    };
  };
}

// コマンド辞典（検索つき）
function renderCmdDict(el, s, running) {
  const row = (x) => `<div class="dict-row"><div><code>${esc(x.c)}</code>${x.danger ? ' <span class="badge warn">注意</span>' : ""}<div class="small muted">${esc(x.ja)}</div></div>
    <div class="acts">${cpBtn(x.c, "コピー", "コマンド")}</div></div>`;
  el.innerHTML = `<div class="row"><h3 class="grow">コマンド辞典</h3></div>
    <p class="muted small" style="margin:6px 0">よく使うコマンドの意味です。<b>&lt;名前&gt;</b> のところは、自分で入れかえます。ホーム画面の入力欄に入れると、候補も出ます。</p>
    <input type="text" data-dq placeholder="さがす（例: 天気、creative、BAN）"><div data-dl style="margin-top:8px"></div>`;
  const paint = () => {
    const q = $("[data-dq]", el).value.trim().toLowerCase();
    const words = q.split(/\s+/).filter(Boolean);
    $("[data-dl]", el).innerHTML = CMD_GROUPS.map(([g, gl]) => {
      const rows = CMD_DICT.filter((x) => x.g === g && words.every((w) => x.key.includes(w)));
      return rows.length ? `<h4 style="margin:12px 0 4px">${gl}</h4>${rows.map(row).join("")}` : "";
    }).join("") || `<div class="faint small">見つかりません</div>`;
  };
  $("[data-dq]", el).addEventListener("input", paint);
  paint();
}

// ================================================================ 予約（曜日と時刻で、起動・停止）
async function renderSchedule(el, s) {
  let d;
  try { d = await api("schedule_get", { id: s.id }); } catch (e) { el.innerHTML = `<div class="note warn">${esc(e.message)}</div>`; return; }
  const DAYS = "月火水木金土日";
  el.innerHTML = `<h2>予約（決めた時刻に、起動・停止）</h2>
    <p class="muted" style="max-width:76ch">「毎週 金・土の19:00に起動、23:30に停止」のように決められます。停止の5分前と1分前には、ゲームの中にお知らせが出ます。<b>予約は、このソフトが起動しているあいだだけ動きます</b>（PCの電源が入っていて、ソフトを閉じていないこと。「PCを起動したら、サーバーも起動」と組み合わせると便利です）。</p>
    <div class="stack" style="margin:10px 0;gap:6px">${d.texts.map((t, i) => `<div class="row" style="gap:12px;padding:8px 12px;background:var(--panel-2, rgba(128,128,128,.12))"><span class="grow"><b>⏰ ${esc(t)}</b></span><button class="btn sm ghost" style="flex:none;width:auto" data-sdel="${i}">消す</button></div>`).join("") || `<p class="faint small">予約はまだありません</p>`}</div>
    <div class="panel"><h3>予約を足す</h3>
      <div class="row" style="margin:8px 0;gap:10px" data-days>${[...DAYS].map((n, i) => `<label class="check"><input type="checkbox" value="${i}"> ${n}</label>`).join("")}
        <button class="btn sm ghost" data-preset="0,1,2,3,4,5,6">毎日</button><button class="btn sm ghost" data-preset="0,1,2,3,4">平日</button><button class="btn sm ghost" data-preset="5,6">週末</button></div>
      <div class="row" style="gap:12px"><label class="small">起動する時刻<br><input type="time" data-st style="width:140px"></label><label class="small">停止する時刻<br><input type="time" data-sp style="width:140px"></label>
        <button class="btn primary" data-sadd style="align-self:flex-end">足す</button></div>
      <p class="hint">どちらか片方だけでもOKです。（例: 停止だけ決めて、夜中に自動で止める）</p></div>`;
  const save = async (rules) => { try { await api("schedule_set", { id: s.id, rules }); toast("保存しました"); renderSchedule(el, s); } catch (e) { fail(e); } };
  $$("[data-preset]", el).forEach((b) => (b.onclick = () => { const on = b.dataset.preset.split(","); $$("[data-days] input", el).forEach((c) => (c.checked = on.includes(c.value))); }));
  $$("[data-sdel]", el).forEach((b) => (b.onclick = () => save(d.rules.filter((_, i) => i !== +b.dataset.sdel))));
  $("[data-sadd]", el).onclick = () => {
    const days = $$("[data-days] input:checked", el).map((c) => +c.value);
    const start = $("[data-st]", el).value, stop = $("[data-sp]", el).value;
    if (!days.length) return toast("曜日を1つ以上えらんでください", "warn");
    if (!start && !stop) return toast("起動か停止の時刻を入れてください", "warn");
    save([...d.rules, { days, start, stop }]);
  };
}

// ================================================================ 招待カード（画像）
function wrapLines(ctx, text, maxW) {
  const out = [];
  for (const para of String(text).split("\n")) {
    let line = "";
    for (const ch of para) {
      if (ctx.measureText(line + ch).width > maxW && line) { out.push(line); line = ch; } else line += ch;
    }
    out.push(line);
  }
  return out;
}
async function drawInviteCard(d) {
  const W = 960, pad = 48, font = '"DotGothic16","Yu Gothic UI","Meiryo","Noto Sans CJK JP",sans-serif';
  const c = document.createElement("canvas"), ctx = c.getContext("2d");
  const steps = ["「OneClickServerMC」を開いて「友達のサーバーに参加」を押す", `アドレス ${d.address || "（ホストに聞いてね）"} を入れる（または、もらった .mrpack を選ぶ）`,
    "マイクラ公式ランチャーで「" + d.name + "（マルチ用）」を選んで「プレイ」", "「マルチプレイ」からサーバーに接続！"];
  ctx.font = `22px ${font}`;
  const stepLines = steps.map((t, i) => wrapLines(ctx, `${i + 1}. ${t}`, W - pad * 2 - 20));
  const mods = d.required.slice(0, 14);
  const stepH = stepLines.reduce((n, l) => n + l.length * 32 + 6, 0);
  const H = 120 + 150 + 60 + 40 + stepH + 50 + (mods.length ? 60 + Math.ceil(mods.length / 2) * 30 : 0) + (d.whitelist ? 50 : 0) + 60;
  c.width = W; c.height = H;
  ctx.fillStyle = "#1f2a1c"; ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = "#5fa83a"; ctx.fillRect(0, 0, W, 26);
  ctx.fillStyle = "#7a5a37"; ctx.fillRect(0, 26, W, 8);
  ctx.fillStyle = "#fff"; ctx.textBaseline = "top";
  ctx.font = `20px ${font}`; ctx.fillStyle = "#cfe8b8"; ctx.fillText("マイクラ MODサーバーへの招待", pad, 56);
  ctx.font = `bold 44px ${font}`; ctx.fillStyle = "#fff";
  const nameLines = wrapLines(ctx, d.name, W - pad * 2 - 230);
  nameLines.slice(0, 2).forEach((l, i) => ctx.fillText(l, pad, 86 + i * 52));
  let y = 86 + Math.min(2, nameLines.length) * 52 + 10;
  ctx.font = `22px ${font}`; ctx.fillStyle = "#e8e8e8";
  ctx.fillText(`Java版 ${d.mc}　/　${d.loader_label}　/　最大 ${d.max_players}人`, pad, y);
  y += 46;
  // アドレスの枠
  ctx.fillStyle = "#101810"; ctx.fillRect(pad, y, W - pad * 2 - 200, 74);
  ctx.strokeStyle = "#5fa83a"; ctx.lineWidth = 3; ctx.strokeRect(pad, y, W - pad * 2 - 200, 74);
  ctx.fillStyle = "#9fd37a"; ctx.font = `16px ${font}`; ctx.fillText("サーバーのアドレス", pad + 16, y + 8);
  ctx.fillStyle = "#fff"; ctx.font = `bold 30px ${font}`;
  let addr = d.address || "（あとで伝えます）";
  while (ctx.measureText(addr).width > W - pad * 2 - 232 && addr.length > 6) addr = addr.slice(0, -2);
  ctx.fillText(addr, pad + 16, y + 32);
  if (d.address) {
    try {
      const img = new Image();
      await new Promise((ok, ng) => { img.onload = ok; img.onerror = ng; img.src = "data:image/svg+xml;base64," + btoa(qrSvg(d.address, 6)); });
      ctx.fillStyle = "#fff"; ctx.fillRect(W - pad - 180, y - 44, 180, 180);
      ctx.drawImage(img, W - pad - 172, y - 36, 164, 164);
    } catch (e) { /* QRなし */ }
  }
  y += 120;
  ctx.fillStyle = "#cfe8b8"; ctx.font = `bold 24px ${font}`; ctx.fillText("入り方", pad, y); y += 40;
  ctx.font = `22px ${font}`; ctx.fillStyle = "#fff";
  for (const ls of stepLines) { ls.forEach((l, i) => ctx.fillText(l, pad + (i ? 30 : 0), y + i * 32)); y += ls.length * 32 + 6; }
  y += 14;
  if (mods.length) {
    ctx.fillStyle = "#cfe8b8"; ctx.font = `bold 24px ${font}`; ctx.fillText(`入るMOD（${d.required.length}個・自動で入ります）`, pad, y); y += 40;
    ctx.font = `19px ${font}`; ctx.fillStyle = "#e8e8e8";
    mods.forEach((m, i) => { let t = "・" + m; while (ctx.measureText(t).width > (W - pad * 2) / 2 - 10 && t.length > 4) t = t.slice(0, -2) + "…"; ctx.fillText(t, pad + (i % 2) * ((W - pad * 2) / 2), y + Math.floor(i / 2) * 30); });
    y += Math.ceil(mods.length / 2) * 30 + 10;
  }
  if (d.whitelist) { ctx.fillStyle = "#f2c94c"; ctx.font = `20px ${font}`; ctx.fillText("※ 入れないときは、マイクラの名前をホストに教えてね（ホワイトリストに追加します）", pad, y); y += 34; }
  // 余白を切りつめる
  const H2 = Math.min(H, y + 36), o = document.createElement("canvas");
  o.width = W; o.height = H2;
  const oc = o.getContext("2d");
  oc.drawImage(c, 0, 0);
  oc.fillStyle = "#7a5a37"; oc.fillRect(0, H2 - 8, W, 8);
  return o;
}
function inviteCardText(d) {
  return `マイクラのMODサーバー「${d.name}」に遊びに来てね！\n\n■ Java版 ${d.mc}（${d.loader_label}）\n■ アドレス: ${d.address || "（あとで伝えるね）"}\n${d.link ? `■ 招待リンク: ${d.link}\n` : ""}\n【入り方】\n1. 「OneClickServerMC」で「友達のサーバーに参加」→ アドレスを入れる（または、もらった .mrpack を選ぶ）\n2. 公式ランチャーで「${d.name}（マルチ用）」を選んで「プレイ」\n3. 「マルチプレイ」から接続！\n${d.required.length ? `\n入るMOD（${d.required.length}個）: ${d.required.slice(0, 20).join("、")}${d.required.length > 20 ? " ほか" : ""}\n` : ""}${d.whitelist ? "\n入れないときは、マイクラの名前を教えてね（ホワイトリストに追加します）。" : ""}`;
}
async function inviteCardFlow(s) {
  let d;
  try { d = await api("invite_card", { id: s.id }); } catch (e) { return fail(e); }
  const canvas = await drawInviteCard(d);
  const url = canvas.toDataURL("image/png");
  const m = modal(`<h2>招待カード</h2><p class="muted small">画像を保存して、Discord や LINE に貼れます。${d.address ? "" : "<b>アドレスがまだ決まっていないので、先に「友達を呼ぶ」で決めると、QRコードも入ります。</b>"}</p>
    <div style="max-height:56vh;overflow:auto;background:#000"><img src="${url}" alt="招待カード" style="width:100%;display:block"></div>
    <div class="modal-foot"><button class="btn ghost" data-no>閉じる</button><button class="btn" data-text>文をコピー</button><button class="btn primary" data-save>画像を保存</button></div>`, { wide: true });
  $("[data-no]", m.el).onclick = m.close;
  $("[data-text]", m.el).onclick = () => copyText(inviteCardText(d), "文");
  $("[data-save]", m.el).onclick = () => {
    const a = document.createElement("a");
    a.href = url; a.download = `${d.name}-招待カード.png`.replace(/[\\/:*?"<>|]/g, "_"); a.click();
  };
}

// ================================================================ アイテムの画像・名前の言語
const itemLang = () => { try { return localStorage.getItem("mcmh_itemlang") === "en" ? "en" : "ja"; } catch (e) { return "ja"; } };
const setItemLang = (v) => { try { localStorage.setItem("mcmh_itemlang", v); } catch (e) { /* 何もしない */ } };
// [大きく出す名前, 小さく出す名前]
function itemNames(x) {
  const ja = x.ja || "", en = x.name || "";
  if (itemLang() === "en") return [en || ja, ja && ja !== en ? ja : ""];
  return [ja || en, ja && ja !== en ? en : ""];
}
const itemLangToggle = () => `<span class="seg" role="group" aria-label="名前の言語">${[["ja", "日本語"], ["en", "English"]]
  .map(([k, l]) => `<button type="button" class="btn sm ${itemLang() === k ? "primary" : ""}" data-ilang="${k}">${l}</button>`).join("")}</span>`;
function itemIcon(id, s, size = 24) {
  const q = `id=${encodeURIComponent(id)}&mc=${encodeURIComponent((s && s.mc) || "")}&s=${encodeURIComponent((s && s.id) || "")}`;
  return `<img class="iic" width="${size}" height="${size}" loading="lazy" alt="" src="/icon?${q}">`;
}
// 画像がないアイテムは、「？」の枠にする
document.addEventListener("error", (e) => {
  const t = e.target;
  if (t && t.classList && t.classList.contains("iic") && t.tagName === "IMG") {
    if (t.hasAttribute("data-pv")) { t.remove(); return; }     // 入力欄の横の見本は、なければ出さない
    const sp = document.createElement("span");
    sp.className = "iic ph"; sp.style.width = t.width + "px"; sp.style.height = t.height + "px"; sp.textContent = "?";
    t.replaceWith(sp);
  }
}, true);


// ================ 友達側：パックの変更点（＋追加 ↑更新 －削除） ================
function changesBrief(c) {
  if (!c || !c.known) return "";
  const bits = [...(c.added || []).map((x) => "＋" + x), ...(c.updated || []).map((u) => "↑" + u.title), ...(c.removed || []).map((x) => "－" + x)];
  return bits.length ? "（" + bits.slice(0, 3).join("、") + (bits.length > 3 ? ` ほか${bits.length - 3}件` : "") + "）" : "";
}
function changesHtml(c) {
  if (!c || !c.known) return `<div class="chg"><div class="chg-h">新しいパックがあります</div><div class="small muted">変更点の詳しい一覧は取れませんでした。「今すぐ更新」で、最新のMODに入れ替わります。</div></div>`;
  const rows = [
    ...(c.added || []).map((x) => `<li class="add"><b>＋ 追加</b> ${esc(x)}</li>`),
    ...(c.updated || []).map((u) => `<li class="upd"><b>↑ 更新</b> ${esc(u.title)}${u.from || u.to ? ` <span class="muted">${esc(u.from || "?")} → ${esc(u.to || "?")}</span>` : ""}</li>`),
    ...(c.removed || []).map((x) => `<li class="del"><b>－ 削除</b> ${esc(x)}</li>`),
  ];
  if (!rows.length) rows.push(`<li class="upd"><b>設定ファイルなど</b> が変わりました（MODの数は同じです）</li>`);
  return `<div class="chg"><div class="chg-h">🔔 サーバーのMODが変わりました</div><ul>${rows.join("")}</ul></div>`;
}
