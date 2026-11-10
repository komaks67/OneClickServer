/* OneClickServer: ARK / Palworld / Terraria の画面(ゲームごとの違いは、サーバーから届く info で組み立てる) */
const $ = id => document.getElementById(id);
const KEY = location.pathname.split("/").filter(Boolean)[1];
const TOKEN = document.querySelector('meta[name="token"]').content;
let cfg = null, info = null, mods = [], EVENTS = {}, lastState = null;

async function post(url, body) {
  const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json", "X-Token": TOKEN},
    body: JSON.stringify(body || {})});
  let j = {};
  try { j = await r.json(); } catch (e) { /* 空の返事 */ }
  if (!r.ok) throw new Error(j.error || "エラーが起きました");
  return j;
}
const api = new Proxy({}, {get: (_, name) => (...args) => post(`/g/${KEY}/api/${name}`, {args})});
const home = (name, params) => post("/api/" + name, params);

function toast(t) {
  const e = $("toast"); e.textContent = t; e.classList.add("show");
  clearTimeout(toast.t); toast.t = setTimeout(() => e.classList.remove("show"), 3500);
}
async function doCall(p) {
  try { const r = await p; if (r && r.message) toast(r.message); return r; }
  catch (e) { toast(e.message); return {ok: false, message: e.message}; }
}
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

document.querySelectorAll("#tabs button.nav").forEach(b => b.onclick = () => {
  document.querySelectorAll("#tabs .nav").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".page").forEach(x => x.classList.remove("active"));
  b.classList.add("active"); $("p-" + b.dataset.p).classList.add("active"); closeHelp();
});

function applyTheme(t) { document.documentElement.dataset.theme = t || "auto"; }

/* ===== 設定の画面を、フィールドの定義から作る ===== */
const GROUPS = [["basic", "基本"], ["game", "ゲームの設定"], ["port", "ポート番号"], ["adv", "上級者向け"]];
const COMMON_IDS = ["auto_backup", "backup_minutes", "backup_keep", "auto_restart", "restart_times", "restart_on_crash", "discord_webhook"];

function buildSettings() {
  const box = $("settingsBox"); box.innerHTML = "";
  GROUPS.forEach(([g, title]) => {
    const fields = info.fields.filter(f => (f.group || "basic") === g);
    if (!fields.length) return;
    const card = el("div", "card"); card.append(el("h3", "", title));
    let row = null, n = 0;
    fields.forEach(f => {
      if (f.type === "bool") {
        const lab = el("label", "chk"); lab.style.marginTop = "6px";
        const inp = el("input"); inp.type = "checkbox"; inp.id = "f_" + f.key; inp.checked = !!cfg[f.key];
        lab.append(inp, document.createTextNode(f.label)); attachQm(lab, f.help, f.label);
        card.append(lab); return;
      }
      if (!row || n % 3 === 0) { row = el("div", "row"); row.style.marginTop = "10px"; card.append(row); }
      n++;
      const lab = el("label"); const span = el("span", "lbl"); span.append(document.createTextNode(f.label));
      attachQm(span, f.help, f.label); lab.append(span);
      let inp;
      if (f.type === "select") {
        inp = el("select");
        Object.entries(f.options).forEach(([k, v]) => { const o = el("option", "", v); o.value = k; inp.append(o); });
        inp.value = String(cfg[f.key]);
      } else {
        inp = el("input");
        inp.type = f.type === "password" ? "password" : (f.type === "number" || f.type === "float" ? "number" : "text");
        if (f.type === "float") inp.step = "any";
        if (f.min !== undefined) inp.min = f.min;
        if (f.max !== undefined) inp.max = f.max;
        inp.value = cfg[f.key] ?? "";
      }
      inp.id = "f_" + f.key; lab.append(inp); row.append(lab);
    });
    box.append(card);
  });
}

function collectSettings() {
  const d = {};
  info.fields.forEach(f => {
    const e = $("f_" + f.key); if (!e) return;
    d[f.key] = f.type === "bool" ? e.checked : e.value;
  });
  d.auto_backup = $("auto_backup").checked; d.auto_restart = $("auto_restart").checked;
  d.restart_on_crash = $("restart_on_crash").checked;
  d.backup_minutes = $("backup_minutes").value; d.backup_keep = $("backup_keep").value;
  d.restart_times = $("restart_times").value.split(",").map(s => s.trim()).filter(Boolean);
  d.discord_webhook = $("discord_webhook").value;
  d.discord_events = [...document.querySelectorAll("[data-ev]")].filter(x => x.checked).map(x => x.dataset.ev);
  if (info.mods) d.mods = mods.slice();
  return d;
}

async function saveAll() {
  const d = collectSettings();
  const r = await doCall(api.save_settings(d));
  if (r.ok) { Object.assign(cfg, d); $("gameBadge").textContent = info.name; }
  return r;
}

function fillCommon() {
  $("auto_backup").checked = !!cfg.auto_backup; $("backup_minutes").value = cfg.backup_minutes;
  $("backup_keep").value = cfg.backup_keep; $("auto_restart").checked = !!cfg.auto_restart;
  $("restart_times").value = (cfg.restart_times || []).join(",");
  $("restart_on_crash").checked = !!cfg.restart_on_crash; $("discord_webhook").value = cfg.discord_webhook || "";
  const eb = $("evBox"); eb.innerHTML = "";
  Object.entries(EVENTS).forEach(([k, v]) => {
    const l = el("label", "chk"); const i = el("input"); i.type = "checkbox"; i.dataset.ev = k;
    i.checked = (cfg.discord_events || []).includes(k);
    l.append(i, document.createTextNode(v + "とき、知らせる")); eb.append(l);
  });
}

/* ===== MOD ===== */
function renderMods() {
  const ul = $("modList"); ul.innerHTML = "";
  if (!mods.length) { const li = el("li"); li.append(el("span", "small", "まだ MOD はありません。")); ul.append(li); return; }
  mods.forEach((m, i) => {
    const li = el("li"); li.append(el("span", "", String(m)));
    const up = el("button", "btn", "↑"); up.disabled = i === 0;
    up.onclick = () => { [mods[i - 1], mods[i]] = [mods[i], mods[i - 1]]; renderMods(); };
    const del = el("button", "btn danger", "削除"); del.onclick = () => { mods.splice(i, 1); renderMods(); };
    li.append(up, del); ul.append(li);
  });
}
function addMod() {
  const v = parseInt($("modId").value, 10);
  if (!v || v <= 0) { toast("数字の ID を入力してください。"); return; }
  if (mods.includes(v)) { toast("すでに追加されています。"); return; }
  mods.push(v); $("modId").value = ""; renderMods(); toast("追加しました。「保存する」を押してください。");
}

/* ===== 管理コマンド ===== */
function buildAdmin() {
  const box = $("adminBtns"); box.innerHTML = "";
  info.admin_buttons.forEach(b => {
    const btn = el("button", "btn", b.label);
    btn.onclick = () => { if (b.confirm && !confirm(b.confirm)) return; cmd(b.cmd); };
    box.append(btn);
    if (b.help) box.append(mkQm(b.label, b.help));
  });
  $("chatBox").hidden = !info.chat;
}
async function cmd(c) {
  try {
    const r = await api.admin_command(c);
    $("adminOut").textContent = r.output || r.message;
    if (!r.ok) toast(r.message);
  } catch (e) { toast(e.message); }
}
function chat() {
  const t = $("chatText").value.trim();
  if (!t) { toast("メッセージを入力してください。"); return; }
  cmd((info.chat_prefix || "") + t); $("chatText").value = "";
}

async function diagnose() {
  $("bDiag").disabled = true; $("diagOut").textContent = "調べています…";
  try {
    const r = await api.diagnose();
    $("diagOut").innerHTML = "";
    const h = el("p"); const b = el("b", "", r.message); h.append(b); $("diagOut").append(h);
    r.items.forEach(i => $("diagOut").append(el("div", "dg " + i.level, i.text)));
  } catch (e) { toast(e.message); }
  $("bDiag").disabled = false;
}

function fmt(sec) { const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60); return `${h}時間${m}分`; }

function render(s) {
  lastState = s;
  const st = $("status");
  if (s.busy) { st.textContent = s.busy + "中…"; st.className = "status busy"; }
  else if (s.running) { st.textContent = "起動中"; st.className = "status on"; }
  else { st.textContent = "停止中"; st.className = "status"; }
  let t = s.installed ? "サーバーは導入済みです。" : "サーバーはまだ導入されていません。まず「サーバーを導入・更新」を押してください。";
  if (s.running && s.uptime) t += ` 稼働時間: ${fmt(s.uptime)}`;
  if (!s.is_windows) t += " (このパソコンは Windows ではないため、導入と起動はできません)";
  $("homeInfo").textContent = t;
  $("lanInfo").textContent = s.lan ? `同じ家のネットワーク(Wi-Fi)の友達は、ゲームから「${s.lan}」で入れます。` : "";
  $("progress").textContent = s.progress || "";
  renderUpd(s);
  $("updDot").hidden = !(s.update && s.update.newer);
  $("bInstall").disabled = !!s.busy || s.running;
  $("bStart").disabled = !!s.busy || s.running || !s.installed;
  $("bRestart").disabled = !!s.busy || !s.running;
  $("bStop").disabled = !!s.busy || !s.running;
  $("bBackup").disabled = !!s.busy;
  $("bPrune").disabled = !!s.busy || s.backups.length <= 1;
  $("bPlayit").textContent = s.playit ? "playit を止める" : "playit を起動";
  $("bPlayit").disabled = !!s.busy;
  const lg = $("log"); const atEnd = lg.scrollTop + lg.clientHeight >= lg.scrollHeight - 20;
  lg.textContent = s.logs.join("\n"); if (atEnd) lg.scrollTop = lg.scrollHeight;
  const bl = $("backupList"); const key = JSON.stringify(s.backups) + s.running + s.busy;
  if (bl.dataset.k !== key) {
    bl.dataset.k = key; bl.innerHTML = "";
    if (!s.backups.length) { const li = el("li"); li.append(el("span", "small", "まだバックアップはありません。")); bl.append(li); }
    s.backups.forEach(b => {
      const li = el("li"); li.append(el("span", "", `${b.name}(${b.size_mb} MB)`));
      const r = el("button", "btn", "復元"); r.disabled = s.running || !!s.busy;
      r.onclick = () => { if (confirm("今のセーブデータを、このバックアップで置き換えます。よろしいですか?")) doCall(api.restore(b.name)); };
      li.append(r); bl.append(li);
    });
  }
}
function renderUpd(s) {
  const u = s.upd || {}, box = $("updBox"); box.innerHTML = "";
  const add = (html) => { const d = el("div", "updnote"); d.innerHTML = html; box.append(d); };
  if (u.checking) add("更新を確認しています…");
  if (u.game) add("<b>ゲーム本体に新しい版があります。</b>停止してから「サーバーを導入・更新」を押すと、最新になります。(ワールドや設定は消えません)");
  (u.mods || []).forEach(m => add(`<b>MOD「${esc(m.title)}」が更新されました</b>(${esc(m.ago)})。サーバーを再起動すると、最新になります。`));
  if (u.error) add(esc(u.error));
  if (!u.checking && !u.game && !(u.mods || []).length && u.checked && manualCheck) add("新しい版はありませんでした。");
  if (info && info.mod_update_note && info.mods) {
    const n = el("div", "small", info.mod_update_note); n.style.marginTop = "6px"; box.append(n);
  }
}
let manualCheck = false;
const esc = (t) => String(t).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
function renderStat(d) {
  const b = $("statBox"); b.innerHTML = "";
  if (!d || !d.running) { b.hidden = true; return; }
  b.hidden = false;
  const add = (k, v) => { const x = el("div", "st"); x.append(el("span", "small", k)); x.append(el("b", "", v)); b.append(x); };
  add("稼働時間", fmt(d.uptime));
  add("参加人数", d.players === null || d.players === undefined ? "不明" : d.players + (d.max_players ? " / " + d.max_players : "") + " 人");
  add("メモリ", d.mem_mb ? (d.mem_mb >= 1024 ? (d.mem_mb / 1024).toFixed(1) + " GB" : Math.round(d.mem_mb) + " MB") : "不明");
}
async function statTick() { try { renderStat(await api.status_detail()); } catch (e) { /* 一時的な失敗は無視 */ } }
async function tick() { try { render(await api.get_state()); } catch (e) { /* 一時的な失敗は無視 */ } }

async function updCheck() {
  $("bUpdCheck").disabled = true; $("verInfo").textContent = "調べています…";
  try {
    const r = await home("update_check");
    let t = "いまの版: " + r.current;
    if (r.latest) t += " / 最新の版: " + r.latest;
    if (r.message) t += "\n" + r.message;
    if (r.newer) t += "\n新しい版があります。ホーム(サーバー一覧)を開き直すと、更新の画面が出ます。";
    $("verInfo").style.whiteSpace = "pre-wrap"; $("verInfo").textContent = t;
  } catch (e) { $("verInfo").textContent = e.message; }
  $("bUpdCheck").disabled = false;
}

/* ===== 「?」ヘルプ ===== */
const HELP = {
  bInstall: ["サーバーを導入・更新", "サーバー本体をダウンロードして入れます。すでに入っているときは、<b>最新版に更新</b>します。更新のときは、サーバーを止めてから押してください。"],
  bStart: ["起動", "サーバーを起動します。ワールドの読み込みに<b>少し時間</b>がかかります。パスワードなどが空だと、起動できないことがあります。"],
  bRestart: ["再起動", "ワールドをセーブして、止めて、もう一度起動します。設定を変えたあとに使います。"],
  bStop: ["停止", "ワールドを<b>セーブしてから</b>安全に止めます。最大で1分ほどかかります。"],
  bPlayit: ["playit を起動", "playit の黒い画面を開きます。最初は、画面に出る URL でアカウントを作る必要があります。サーバーを動かしている間は、<b>黒い画面を閉じないで</b>ください。"],
  bFirewall: ["ファイアウォールの許可", "Windows が外からの通信を止めないように、許可を追加します。このソフトを<b>「管理者として実行」</b>したときだけ成功します。"],
  bBackup: ["今すぐバックアップ", "セーブデータと設定の<b>コピー</b>を作ります。サーバーが動いているときは、保存してから作ります。古いものは、設定した数をこえると自動で消えます。"],
  bOpenBackups: ["保存先を開く", "バックアップのファイルが入っているフォルダを開きます。別の場所にもコピーしておくと、さらに安心です。"],
  auto_backup: ["自動バックアップ", "オンにすると、サーバーが動いている間、決めた間隔で自動でバックアップを作ります。"],
  backup_minutes: ["バックアップの間隔", "何分ごとに作るかです。短いほど安心ですが、ディスクをたくさん使います。<b>60分</b>くらいが目安です。"],
  backup_keep: ["残す数", "バックアップを何個まで残すかです。これをこえると、<b>古いものから</b>消えます。"],
  auto_restart: ["自動で再起動", "オンにすると、決めた時刻にサーバーを再起動します。再起動の<b>1分前</b>に、ゲームの中へ予告を出します(対応しているゲームのみ)。長く動かすと重くなるので、1日1回くらいがおすすめです。"],
  restart_times: ["再起動の時刻", "24時間の書き方で入れます。例: <b>05:00</b>。複数のときは、<b>カンマ</b>で区切ります。例: 05:00,17:00"],
  restart_on_crash: ["落ちたら起動し直す", "サーバーが急に止まったら、自動でもう一度起動します。<b>10分の間に3回</b>落ちたときは、くり返さずに止めて、お知らせします。"],
  bDiag: ["診断する", "前のサーバーの残り、ポート、メモリ、空き容量、ログを調べて、<b>原因を日本語で</b>表示します。赤い項目から、順に直してください。"],
  discord_webhook: ["Webhook の URL", "Discord のチャンネルの設定 →「連携サービス」→「ウェブフック」で作った URL です。<b>他の人に見せないで</b>ください。"],
  bTestDiscord: ["テスト通知", "Discord に、テストのメッセージを送ります。届けば設定は成功です。"],
  chatText: ["全員にメッセージ", "ゲームの中に、全員宛てのメッセージが出ます。再起動の予告などに使えます。"],
  mode: ["明るさ", "<b>自動</b>は、Windows の設定(ライト/ダーク)に合わせます。"],
  bUpdCheck: ["更新を確認", "インターネットで、新しい版があるか調べます。新しい版があるときは、ホームを開き直すと、更新の画面が出ます。"],
  modId: ["MOD の ID", "数字を入れて「追加」を押します。そのあと<b>「保存する」</b>を押し、サーバーを再起動してください。並びは<b>読み込みの順番</b>で、「↑」で上に動かせます。MODを外すと、そのMODのものが消えることがあるので、先にバックアップを取ってください。"],
};
let helpBtn = null;
function mkQm(title, body) {
  const b = el("button", "qm", "?"); b.type = "button"; b.title = "説明を見る"; b.setAttribute("aria-label", "説明を見る");
  b.onclick = e => { e.preventDefault(); e.stopPropagation(); toggleHelp(b, title, body); };
  return b;
}
function attachQm(parent, body, title) { if (body) parent.append(mkQm(title, body)); }
function toggleHelp(btn, title, body) {
  const pop = $("helpPop");
  if (helpBtn === btn && !pop.hidden) { closeHelp(); return; }
  closeHelp();
  pop.innerHTML = '<b class="t"></b><div></div>';
  pop.firstChild.textContent = title; pop.lastChild.innerHTML = body;   // body はこのソフトの中に書いた文章だけ
  pop.hidden = false; btn.classList.add("on"); helpBtn = btn;
  const r = btn.getBoundingClientRect(), w = Math.min(340, innerWidth - 24);
  pop.style.maxWidth = w + "px";
  pop.style.left = Math.min(Math.max(12, r.left), innerWidth - w - 12) + "px";
  pop.style.top = (r.bottom + 8) + "px";
  const ph = pop.offsetHeight;
  if (r.bottom + 8 + ph > innerHeight - 12) pop.style.top = Math.max(12, r.top - ph - 8) + "px";
}
function closeHelp() { const p = $("helpPop"); p.hidden = true; if (helpBtn) helpBtn.classList.remove("on"); helpBtn = null; }
document.addEventListener("click", e => { if (!e.target.closest("#helpPop") && !e.target.closest(".qm")) closeHelp(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeHelp(); });

function attachStaticHelp() {
  Object.entries(HELP).forEach(([id, [title, body]]) => {
    const e = $(id); if (!e) return;
    if (e.tagName === "BUTTON") { e.insertAdjacentElement("afterend", mkQm(title, body)); return; }
    const label = e.closest("label"); if (!label) return;
    if (label.classList.contains("chk")) { label.append(mkQm(title, body)); return; }
    const tn = [...label.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
    if (!tn) return;
    const span = el("span", "lbl"); label.insertBefore(span, tn); span.append(tn, mkQm(title, body));
  });
  const pages = {home: "サーバーの<b>導入・起動・停止</b>をする画面です。初めての人は、上から順に<b>「導入」→「起動」</b>を押してください。",
    settings: "サーバーの<b>名前・人数・ゲームの中身</b>などを決める画面です。変えたら<b>「保存する」</b>を押して、サーバーを再起動すると反映されます。",
    mods: "ゲームに<b>追加するMOD</b>を管理する画面です。追加・削除したあとは、保存して再起動してください。",
    friends: "友達が<b>インターネット越しに入れる</b>ようにする方法です。ルーターの設定が要らない<b>方法1(playit)</b>が簡単です。",
    admin: "動いているサーバーに<b>命令を送る</b>画面です。サーバーが動いているときだけ使えます。",
    backup: "セーブデータの<b>コピー</b>を取っておく画面です。データが壊れたときに、<b>「復元」</b>で戻せます。復元は、サーバーを止めてからしてください。",
    auto: "バックアップや再起動を<b>自動</b>でやる設定です。サーバーが動いている間だけ働きます。",
    diag: "うまく動かないときに、<b>原因を調べて</b>日本語で教えます。",
    discord: "サーバーの起動や停止などを、<b>Discord に知らせる</b>設定です。",
    app: "ソフトの更新を調べたり、見た目を変えたり、保存されているフォルダを開いたりできます。"};
  document.querySelectorAll(".page > h2").forEach(h => {
    const k = h.parentElement.id.slice(2); if (pages[k]) h.append(mkQm(h.textContent, pages[k]));
  });
}

async function init() {
  try {
    const all = await api.get_all();
    cfg = all.settings; info = all.info; EVENTS = all.events;
    info.chat_prefix = info.chat ? chatPrefix(info) : "";
    document.title = "OneClickServer — " + info.name;
    document.documentElement.dataset.game = info.key;
    $("gameBadge").textContent = info.name;
    $("tunnelText").innerHTML = info.tunnel; $("routerPorts").textContent = info.router_ports;
    $("installNote").textContent = info.install_note + " 画面を閉じても、動いているサーバーは止まりません。";
    $("navMods").hidden = !info.mods;
    if (info.mods) {
      $("modLabel").textContent = info.mods.label; $("modNote").textContent = info.mods.note;
      $("modId").placeholder = "例: " + (info.mods.example || "123456"); mods = [...(cfg.mods || [])]; renderMods();
    }
    buildSettings(); fillCommon(); buildAdmin(); render(all.state); attachStaticHelp();
  } catch (e) { toast(e.message); return; }
  try { const t = await home("get_theme"); applyTheme(t.theme); $("mode").value = t.theme; } catch (e) { /* そのまま */ }
  $("bInstall").onclick = () => doCall(api.install());
  $("bStart").onclick = () => doCall(api.start());
  $("bStop").onclick = () => { if (confirm("サーバーを停止します。よろしいですか?")) doCall(api.stop()); };
  $("bRestart").onclick = () => { if (confirm("サーバーを再起動します。よろしいですか?")) doCall(api.restart()); };
  $("bBackup").onclick = () => doCall(api.backup());
  $("bPrune").onclick = () => { if (confirm("いちばん新しい1つだけ残して、ほかのバックアップを全部消します。\n消したものは戻せません。よろしいですか?")) doCall(api.prune_backups()); };
  $("bOpenBackups").onclick = () => doCall(api.open_folder("backups"));
  $("bOpenData").onclick = () => doCall(api.open_folder("data"));
  $("bOpenServer").onclick = () => doCall(api.open_folder("server"));
  $("bPlayit").onclick = () => doCall(api.playit_toggle());
  $("bFirewall").onclick = () => doCall(api.add_firewall());
  $("bDiag").onclick = diagnose;
  $("bSaveSettings").onclick = saveAll; $("bSaveMods").onclick = saveAll;
  $("bSaveAuto").onclick = saveAll; $("bSaveDiscord").onclick = saveAll;
  $("bTestDiscord").onclick = async () => { const r = await saveAll(); if (r && r.ok) doCall(api.test_discord()); };
  $("bAddMod").onclick = addMod; $("bChat").onclick = chat;
  $("bUpdCheck").onclick = updCheck;
  $("mode").onchange = async () => { try { await home("set_theme", {theme: $("mode").value}); applyTheme($("mode").value); } catch (e) { toast(e.message); } };
  $("verInfo").textContent = "いまの版: " + (lastState ? lastState.version : "");
  setInterval(tick, 1000);
  statTick(); setInterval(statTick, 10000);
  $("bCheckUpd").onclick = () => { manualCheck = true; doCall(api.check_updates()); };
}
function chatPrefix(i) { return i.key === "palworld" ? "announce " : (i.key === "terraria" ? "say " : "ServerChat "); }
init();
