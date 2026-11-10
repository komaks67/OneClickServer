// OneClickServerMC — 画面の動き
"use strict";
window.MCMH_TOKEN = (document.querySelector('meta[name="mcmh-token"]') || {}).content || "";

// ================================================================ 小さな道具
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const gb = (mb) => (Math.round((mb / 1024) * 10) / 10).toFixed(1).replace(/\.0$/, "") + "GB";

function fmtNum(n) {
  n = Number(n || 0);
  if (n >= 1e8) return (n / 1e8).toFixed(1).replace(/\.0$/, "") + "億";
  if (n >= 1e4) return Math.round(n / 1e4) + "万";
  return n.toLocaleString("ja-JP");
}

async function api(name, params = {}) {
  let r;
  try {
    r = await fetch("/api/" + name, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Token": window.MCMH_TOKEN },
      body: JSON.stringify(params),
    });
  } catch (e) {
    throw new Error("ソフト本体と通信できません。ソフトが終了していないか確認してください。");
  }
  let data = null;
  try { data = await r.json(); } catch (e) { /* 何もしない */ }
  if (!r.ok) throw new Error((data && data.error) || "エラーが起きました");
  return data;
}

async function upload(file) {
  const r = await fetch("/api/upload", {
    method: "POST",
    headers: { "X-Token": window.MCMH_TOKEN, "X-Filename": encodeURIComponent(file.name) },
    body: file,
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "アップロードできませんでした");
  return data;
}

function help(key) {
  return HELP[key] ? `<button type="button" class="help" data-help="${key}" aria-label="${esc(HELP[key][0])}の説明">?</button>` : "";
}

function slotImg(url, title, cls = "") {
  const letter = esc((Array.from((title || "?").trim())[0] || "?").toUpperCase());
  return `<div class="slot ${cls}">${url ? `<img src="${esc(url)}" alt="" loading="lazy" data-letter="${letter}">` : `<span class="letter">${letter}</span>`}</div>`;
}

// 画像が読めなかったら、頭文字にする
document.addEventListener("error", (e) => {
  const t = e.target;
  if (t.tagName === "IMG" && t.dataset.letter !== undefined) {
    const s = document.createElement("span");
    s.className = "letter";
    s.textContent = t.dataset.letter;
    t.replaceWith(s);
  }
}, true);

// ---------------- お知らせ・確認の窓 ----------------
function toast(msg, kind = "ok", ms = 3500) {
  const box = $("#toasts");
  while (box.children.length >= 3) box.firstChild.remove();
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

function modal(html, { wide = false, onClose = null, closable = true } = {}) {
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">${html}</div>`;
  $("#modal-root").appendChild(back);
  const close = () => { back.remove(); if (onClose) onClose(); };
  if (closable) {
    back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
    back.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  }
  const first = back.querySelector("button, input, select, textarea");
  if (first) setTimeout(() => first.focus(), 30);
  return { el: back.firstElementChild, close };
}

function ask(title, bodyHtml, okText = "OK", { danger = false, cancelText = "やめる" } = {}) {
  return new Promise((resolve) => {
    const m = modal(`<h2>${esc(title)}</h2><div class="plain">${bodyHtml}</div>
      <div class="modal-foot"><button class="btn ghost" data-no>${esc(cancelText)}</button>
      <button class="btn ${danger ? "danger" : "primary"}" data-yes>${esc(okText)}</button></div>`, { onClose: () => resolve(false) });
    $("[data-no]", m.el).onclick = () => { m.close(); };
    $("[data-yes]", m.el).onclick = () => {
      // 窓の中にチェック欄（data-cfg）があれば、選んだものも返す
      const boxes = $$("[data-cfg]", m.el);
      resolve(boxes.length ? { picked: boxes.filter((b) => b.checked).map((b) => b.value) } : true);
      m.close();
    };
  });
}

function info(title, bodyHtml, okText = "OK") {
  return new Promise((resolve) => {
    const m = modal(`<h2>${esc(title)}</h2><div class="plain">${bodyHtml}</div>
      <div class="modal-foot"><button class="btn primary" data-ok>${esc(okText)}</button></div>`, { onClose: () => resolve() });
    $("[data-ok]", m.el).onclick = () => m.close();
  });
}

function fail(e) {
  console.error(e);
  toast(e.message || String(e), "error", 7000);
}

// 時間のかかる作業：進み具合を見せながら待つ
async function runJob(startPromise, title, { cancellable = false, note = "" } = {}) {
  const m = modal(`<h2>${esc(title)}</h2><p class="muted" data-msg>${note ? esc(note) : "少し時間がかかります。この画面のまま待ってください。"}</p>
    <div class="bar"><i></i></div><div class="joblog" data-log></div>
    <div class="modal-foot">${cancellable ? '<button class="btn danger" data-cancel>途中で止める</button>' : ""}<button class="btn" data-close disabled>閉じる</button></div>`, { closable: false });
  const logEl = $("[data-log]", m.el);
  const closeBtn = $("[data-close]", m.el);
  try {
    const { job } = await startPromise;
    const cb = $("[data-cancel]", m.el);
    if (cb) cb.onclick = () => { cb.disabled = true; cb.textContent = "止めています…"; api("job_cancel", { id: job }).catch(() => {}); };
    let since = 0;
    for (;;) {
      const st = await api("job_status", { id: job, since });
      if (st.log.length) {
        logEl.textContent += st.log.join("\n") + "\n";
        logEl.scrollTop = logEl.scrollHeight;
      }
      since = st.next;
      if (st.status === "done") { m.close(); return st.result; }
      if (st.status === "error") {
        $(".bar", m.el).remove();
        $("[data-msg]", m.el).innerHTML = `<span style="color:var(--ng-text)">うまくいきませんでした: ${esc(st.error)}</span>`;
        closeBtn.disabled = false;
        await new Promise((res) => { closeBtn.onclick = () => { m.close(); res(); }; });
        throw Object.assign(new Error(st.error), { shown: true });
      }
      await sleep(700);
    }
  } catch (e) {
    if (!e.shown) { m.close(); }
    throw e;
  }
}

// ---------------- 「？」の説明 ----------------
document.addEventListener("click", (e) => {
  const b = e.target.closest(".help");
  const pop = $("#pop");
  if (!b) { if (!e.target.closest("#pop")) pop.hidden = true; return; }
  e.preventDefault();
  e.stopPropagation();
  const [t, body] = b.dataset.tip !== undefined ? [b.dataset.title || "", esc(b.dataset.tip)] : HELP[b.dataset.help] || ["", ""];
  pop.innerHTML = `<b>${esc(t)}</b>${body}`;
  pop.hidden = false;
  const r = b.getBoundingClientRect();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  let x = Math.min(window.innerWidth - w - 12, Math.max(12, r.left - 20));
  let y = r.bottom + 8;
  if (y + h > window.innerHeight - 10) y = Math.max(10, r.top - h - 8);
  pop.style.left = x + "px";
  pop.style.top = y + "px";
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#pop").hidden = true; closeDrawer(); } });

// 外部サイトはふだんのブラウザで開く
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-url]");
  if (!a) return;
  e.preventDefault();
  api("open_url", { url: a.dataset.url }).catch(fail);
});

// ================================================================ 状態と画面切り替え
const S = {
  servers: [], ram: 8192, settings: {}, windows: true,
  view: "welcome", server: null, tab: "home", sub: "installed",
  consoleNext: 0, tick: 0,
};

// ---------------- 明るい・暗いテーマ ----------------
const THEMES = [["auto", "自動"], ["light", "明るい"], ["dark", "暗い"]];
function applyTheme(t) {
  if (!THEMES.some(([k]) => k === t)) t = "auto";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("mcmh-theme", t); } catch (e) { /* 保存できなくても動く */ }
}
async function setTheme(t) {
  applyTheme(t);
  S.settings.theme = t;
  renderSide();
  if (S.view === "appsettings") renderMain();
  try { await api("set_settings", { theme: t }); } catch (e) { fail(e); }
}
function themePicker(cls = "theme-pick") {
  const now = S.settings.theme || document.documentElement.dataset.theme || "auto";
  return `<div class="${cls}" role="group" aria-label="画面の明るさ">${THEMES.map(([k, l]) =>
    `<button type="button" data-theme-set="${k}" aria-pressed="${now === k}">${l}</button>`).join("")}</div>`;
}
const STYLES = [["mc", "マイクラ風"], ["simple", "シンプル"]];
function applyStyle(v) {
  if (!STYLES.some(([k]) => k === v)) v = "simple";
  document.documentElement.dataset.style = v;
  try { localStorage.setItem("mcmh-style", v); } catch (e) { /* 保存できなくても動く */ }
}
async function setStyle(v) {
  applyStyle(v);
  S.settings.style = v;
  renderSide();
  if (S.view === "appsettings") renderMain();
  try { await api("set_settings", { style: v }); } catch (e) { fail(e); }
}
function stylePicker(cls = "theme-pick style-pick") {
  const now = S.settings.style || document.documentElement.dataset.style || "simple";
  return `<div class="${cls}" role="group" aria-label="見た目" style="grid-template-columns:repeat(2,1fr)">${STYLES.map(([k, l]) =>
    `<button type="button" data-style-set="${k}" aria-pressed="${now === k}">${l}</button>`).join("")}</div>`;
}
function bindThemePicker(root) {
  $$("[data-theme-set]", root).forEach((b) => (b.onclick = () => setTheme(b.dataset.themeSet)));
  $$("[data-style-set]", root).forEach((b) => (b.onclick = () => setStyle(b.dataset.styleSet)));
}

async function refreshState() {
  const st = await api("state");
  S.servers = st.servers;
  S.ram = st.ram_mb;
  S.settings = st.settings;
  S.windows = st.windows;
  S.foreign = !!st.foreign_data;
  S.version = st.version;
  S.update = st.update;
  S.appExe = !!st.app_exe;
  if (st.settings.theme && st.settings.theme !== document.documentElement.dataset.theme) applyTheme(st.settings.theme);
  if (st.settings.style && st.settings.style !== document.documentElement.dataset.style) applyStyle(st.settings.style);
  renderSide();
  try { checkPlayerSound(); } catch (e) { /* 音は、なくても動く */ }
  return st;
}

function cur() { return S.servers.find((s) => s.id === S.server); }

function go(view, opts = {}) {
  S.view = view;
  Object.assign(S, opts);
  closeDrawer();
  renderSide();
  renderMain();
  $("#main").scrollTop = 0;
}

function statusInfo(s) {
  if (s.crash && s.status === "stopped") return ["crash", "止まりました（トラブル）"];
  return {
    running: ["running", "動いています（遊べます）"], starting: ["starting", "起動中…"],
    stopping: ["stopping", "止めています…"], stopped: ["", "止まっています"],
  }[s.status] || ["", s.status];
}

function renderSide() {
  const side = $("#side");
  const nav = (view, ic, label) =>
    `<button class="side-btn" data-go="${view}" ${S.view === view ? 'aria-current="true"' : ""}><span class="ic">${ic}</span>${label}</button>`;
  side.innerHTML = `
    <div class="logo"><div class="logo-block" aria-hidden="true"></div><div><b>OneClickServerMC</b><span>マイクラ Java版 MODマルチ</span></div></div>
    <div class="side-label">あなたのサーバー</div>
    <div class="srv-list">
      ${S.servers.map((s) => {
        const [cls] = statusInfo(s);
        return `<button class="srv" data-srv="${esc(s.id)}" ${S.view === "server" && S.server === s.id ? 'aria-current="true"' : ""}>
          <span class="dot ${cls}"></span><span class="n">${esc(s.name)}</span><span class="m">${esc(s.mc)} ${esc(s.loader_label.split(" ")[0])}・MOD ${s.mods}個</span></button>`;
      }).join("") || `<p class="small faint" style="margin:0 8px 8px">まだありません</p>`}
    </div>
    ${S.servers.length ? nav("list", "📋", "サーバー一覧") : ""}
    ${nav("create", "＋", "新しいサーバーを作る")}
    ${nav("modpack", "📦", "MODパックから作る")}
    ${nav("import", "🚚", "引っ越しファイルから作る")}
    <div class="side-sep"></div>
    ${nav("join", "🎮", "友達のサーバーに参加" + (S.joinUpdates ? ` <span class="badge warn">更新${S.joinUpdates}</span>` : ""))}
    ${nav("howto", "📘", "使い方（画面つき）")}
    ${nav("guide", "📖", "入り方ガイド")}
    ${nav("glossary", "💬", "ことばの説明")}
    <div class="side-foot">
      ${nav("appsettings", "⚙", "ソフトの設定" + (S.update && S.update.newer ? ' <span class="badge warn">新しい版</span>' : ""))}
      <button class="side-btn" data-exit><span class="ic">⏻</span>ソフトを終了</button>
    </div>`;
  $$("[data-go]", side).forEach((b) => (b.onclick = () => {
    if (b.dataset.go === "create") S.wiz = null;
    go(b.dataset.go);
  }));
  $$("[data-srv]", side).forEach((b) => (b.onclick = () => go("server", { server: b.dataset.srv, tab: "home", consoleNext: 0 })));
  $("[data-exit]", side).onclick = exitApp;
  bindThemePicker(side);
}

async function exitApp() {
  const running = S.servers.filter((s) => s.status !== "stopped");
  const msg = running.length
    ? `<p>動いているサーバーがあります（${running.map((s) => esc(s.name)).join("、")}）。<br>ワールドを保存してからサーバーを止め、ソフトを終了します。</p>`
    : "<p>ソフトを終了しますか？</p>";
  if (!(await ask("ソフトを終了", msg + `<p class="muted small">画面を閉じる（×）だけなら、サーバーは動いたままになります。</p>`, "終了する"))) return;
  await api("shutdown", { stop_servers: true }).catch(() => {});
  document.body.innerHTML = `<div style="display:grid;place-items:center;height:100vh;text-align:center"><div><h1>終了しました</h1><p class="muted">このウィンドウを閉じてください。</p></div></div>`;
}

function renderMain() {
  const views = { welcome: viewWelcome, server: viewServer, create: viewCreate, modpack: viewModpack, import: viewImport, join: viewJoin,
    guide: viewGuide, glossary: viewGlossary, appsettings: viewAppSettings, list: viewList, howto: viewHowto };
  (views[S.view] || viewWelcome)($("#main"));
  showForeignBanner();
}

// 友達に渡された data フォルダ（ほかのPCのもの）を使っているときの警告
function showForeignBanner() {
  const main = $("#main");
  if (!S.foreign || !main || main.querySelector("[data-foreign]")) return;
  const d = document.createElement("div");
  d.setAttribute("data-foreign", "");
  d.className = "note error";
  d.style.cssText = "margin:12px 16px";
  d.innerHTML = `<b>⚠ この data フォルダは、ほかのPC（ほかの人）のものです</b>
    ホストの <b>data フォルダ</b>をコピーして使っていませんか？ 中には鍵（Webhook・APIキー）が入っています。安全のため、<b>ほかの人の鍵は使わず、読み込まないようにしてあります</b>。
    <ul class="small" style="margin:6px 0 8px 1.3em"><li>友達としてだけ使うなら、<b>data フォルダを消して</b>から、ソフトを開き直してください。</li><li>自分のPCの data として使うなら、下のボタンを押します。</li></ul>
    <button class="btn sm" data-adopt>この data を、このPC用にする</button>`;
  main.prepend(d);
  $("[data-adopt]", d).onclick = async () => {
    if (!(await ask("この data を、このPC用にします", "<p>ほかの人の鍵（Webhook など）は空のままです。必要なら、あとで入れ直してください。</p>", "このPC用にする"))) return;
    try { await api("adopt_data"); S.foreign = false; d.remove(); toast("このPC用にしました"); } catch (e) { fail(e); }
  };
}

// ================================================================ ようこそ
function viewWelcome(root) {
  root.innerHTML = `<div class="page">
    <div class="hero">
      <div>
        <h1 class="big-title">MODを入れて、<br>友達とマイクラを楽しみましょう。</h1>
        <p class="lead">サーバー作り、MODの導入、友達の呼び方まで、このソフトが順番に案内します。難しい設定は自動でやります。</p>
      </div>
    </div>
    <div class="welcome">
      <div class="stack">
        <button class="choice" data-go="create">${slotImg(null, "＋")}<div><b>サーバーを作る</b><span>自分のPCでサーバーを立てて、友達を呼ぶ人</span></div></button>
        <button class="choice" data-go="modpack">${slotImg(null, "📦")}<div><b>MODパックから作る</b><span>MODがたくさん入ったセットを選ぶだけです。一番かんたん</span></div></button>
        <button class="choice" data-go="join">${slotImg(null, "🎮")}<div><b>友達のサーバーに参加する</b><span>友達からファイル（.mrpack）をもらった人</span></div></button>
        <button class="choice" data-go="guide">${slotImg(null, "📖")}<div><b>入り方ガイドを読む</b><span>はじめての人向けに、全部の流れを説明</span></div></button>
      </div>
    </div></div>`;
  $$(".choice", root).forEach((b) => (b.onclick = () => go(b.dataset.go)));
  $$(".choice .slot .letter", root).forEach((l) => (l.style.fontSize = "26px"));
}

// ================================================================ MODの一覧（カード）
let translateBusy = false;
let translateStopped = false;
let translatePausedUntil = 0;   // 失敗したら、この時刻までお休み（ずっと止めない）
let translateToasted = 0;

async function autoTranslate(root) {
  if (!S.settings.translate_auto || translateStopped || translateBusy || Date.now() < translatePausedUntil) return;
  translateBusy = true;
  try {
    const queue = $$(".d.en[data-text]", root);
    let failed = false;
    const worker = async () => {
      while (queue.length && !failed) {
        const d = queue.shift();
        if (!d.isConnected || !d.dataset.text) continue;
        try {
          const r = await api("translate_text", { text: d.dataset.text });
          d.textContent = r.ja;
          d.classList.remove("en");
          d.removeAttribute("data-text");
          d.title = "自動翻訳";
        } catch (e) {
          failed = true;
          translatePausedUntil = Date.now() + 5 * 60 * 1000;  // 5分あとにまた試す
          if (Date.now() - translateToasted > 5 * 60 * 1000) {
            translateToasted = Date.now();
            toast(e.message + "（5分あとに、もう一度試します）", "warn", 8000);
          }
        }
      }
    };
    await Promise.all([worker(), worker(), worker(), worker(), worker()]);   // 5つずつ同時に訳す
  } finally {
    translateBusy = false;
  }
  // 5分あとに、残りをもう一度（画面が開いたままなら）
  if (Date.now() < translatePausedUntil && !translateStopped) {
    setTimeout(() => { if (root.isConnected) autoTranslate(root); }, translatePausedUntil - Date.now() + 500);
  }
}

function modCard(c, { pickable = false, picked = false, fav = false } = {}) {
  const desc = c.ja || c.desc || "";
  const compat = c.compat ? `<div class="compat ${c.compat.ok ? "ok" : "ng"}">${c.compat.ok ? "✔" : "✖"} ${esc(c.compat.text)}</div>` : "";
  return `<div class="mod ${picked ? "picked" : ""}" data-id="${esc(c.id)}" tabindex="0" role="button" aria-label="${esc(c.title)} の詳しい説明">
    ${slotImg(c.icon, c.title)}
    <div>
      <div class="t">${esc(c.title)}</div>
      <div class="by">${c.author ? "作者 " + esc(c.author) + "　" : ""}ダウンロード ${fmtNum(c.downloads)}回</div>
      <div class="d ${c.ja ? "" : "en"}" ${c.ja ? "" : `data-text="${esc(c.desc)}"`} ${c.ja_source === "auto" ? 'title="自動翻訳"' : ""}>${esc(desc)}</div>
      <div class="foot">
        ${c.installed ? '<span class="badge solid-ok">入っています</span>' : ""}
        ${c.source === "curseforge" ? '<span class="badge" style="color:#f2a65a">CurseForge</span>' : ""}
        ${c.can_download === false ? '<span class="badge warn" title="作者の設定で、自動では入れられません">手で入れる</span>' : ""}
        ${c.library ? '<span class="badge lib">前提MOD</span>' : ""}
        ${(c.genres || []).filter((g) => g !== "前提MOD").slice(0, 3).map((g) => `<span class="badge">${esc(g)}</span>`).join("")}
      </div>
      ${compat}
    </div>
    <button class="favbtn" type="button" data-fav aria-pressed="${fav}" title="${fav ? "お気に入りからはずす" : "お気に入りに入れる"}" aria-label="${esc(c.title)} をお気に入りに入れる">${fav ? "★" : "☆"}</button>
    ${pickable ? `<button class="pick" type="button" aria-pressed="${picked}" aria-label="${esc(c.title)} を選ぶ">✓</button>` : ""}
  </div>`;
}

/**
 * MODを探す部品。
 * opts: { server, picked: Map(id→title), onChange, type: "mod", installMode }
 */
function modBrowser(root, opts) {
  const st = { q: "", category: "", sort: "downloads", offset: 0, onlyOk: true, hits: new Map(), total: 0, source: "modrinth", mc: (opts.version && opts.version.mc) || "", loader: (opts.version && opts.version.loader) || "", fav: false };
  const favs = S.favs || (S.favs = new Map());
  const verBar = !opts.server && !opts.type;
  const cfOk = S.settings.cf_enabled && (opts.type || "mod") === "mod";
  const cats = [["", "ジャンル: すべて"], ["optimization", "軽量化"], ["utility", "便利"], ["technology", "工業・技術"],
    ["magic", "魔法"], ["adventure", "冒険"], ["worldgen", "地形・世界"], ["mobs", "生き物"], ["storage", "収納"],
    ["equipment", "装備"], ["decoration", "建築・飾り"], ["food", "料理"], ["transportation", "移動・乗り物"],
    ["game-mechanics", "ゲームの仕組み"], ["social", "交流"], ["library", "前提MOD"]];
  root.innerHTML = `
    <div class="mod-tools">
      ${cfOk ? `<select data-src aria-label="探すサイト" style="width:auto"><option value="modrinth">Modrinth</option><option value="curseforge">CurseForge</option></select>` : ""}
      <input type="search" placeholder="名前で検索（英語）　例: create, sodium, jei" aria-label="MODを検索">
      <select data-cat aria-label="ジャンル">${cats.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <select data-sort aria-label="並べ方"><option value="downloads">人気順</option><option value="relevance">関係が深い順</option><option value="follows">お気に入りが多い順</option><option value="updated">最近更新された順</option><option value="newest">新しい順</option></select>
      <button class="btn" data-search>検索</button>
      <button class="btn" data-favview aria-pressed="false" title="お気に入りに入れたMODだけを表示します">★ お気に入り</button>
    </div>
    ${verBar ? `<div class="mod-tools" style="margin-top:-4px"><label class="small">マイクラのバージョンを指定
        <select data-mcver style="width:auto"><option value="">指定しない（すべて）</option></select></label>
      <label class="small">MODの種類を指定
        <select data-ldr style="width:auto"><option value="">指定しない（すべて）</option><option value="fabric">Fabric</option><option value="neoforge">NeoForge</option><option value="forge">Forge</option></select></label>
      <span class="small muted" data-vernote>指定すると、そのバージョンで使えるMODだけが出ます。</span></div>` : ""}
    ${opts.server ? `<label class="check small" style="margin-bottom:10px"><input type="checkbox" data-only checked> このサーバーで使える${modWordFor(opts.server)}だけ表示する</label>` : ""}
    <div class="mod-grid" data-grid></div>
    <div class="more"><button class="btn" data-more hidden>もっと見る</button></div>`;
  const grid = $("[data-grid]", root);
  const input = $("input[type=search]", root);
  const load = async (reset) => {
    if (reset) { st.offset = 0; st.hits.clear(); grid.innerHTML = `<div class="empty">探しています…</div>`; }
    if (st.fav) {
      const q = st.q.toLowerCase();
      const list = [...favs.values()].filter((h) => !q || (h.title + " " + (h.ja || "") + " " + (h.desc || "")).toLowerCase().includes(q));
      grid.innerHTML = list.map((h) => { st.hits.set(h.id, h); return modCard(h, { pickable: !!opts.picked, picked: opts.picked && opts.picked.has(h.id), fav: true }); }).join("")
        || `<div class="empty">お気に入りのMODは、まだありません。<br>カードの ☆ を押すと、ここに入ります。</div>`;
      $("[data-more]", root).hidden = true;
      autoTranslate(grid);
      return;
    }
    try {
      const r = await api("mods_search", {
        q: st.q, category: st.category, sort: st.q && st.sort === "downloads" ? "relevance" : st.sort,
        offset: st.offset, server: opts.server || null, only_ok: st.onlyOk, project_type: opts.type || "mod", source: st.source,
        mc: st.mc || null, loader: st.loader || null,
      });
      if (reset) grid.innerHTML = "";
      st.total = r.total;
      const html = r.hits.filter((h) => !st.hits.has(h.id)).map((h) => {
        st.hits.set(h.id, h);
        return modCard(h, { pickable: !!opts.picked, picked: opts.picked && opts.picked.has(h.id), fav: favs.has(h.id) });
      }).join("");
      grid.insertAdjacentHTML("beforeend", html);
      if (!st.hits.size) grid.innerHTML = `<div class="empty">見つかりませんでした。<br>英語の名前で検索してみてください（例: 「工業」なら technology のジャンルを選ぶ）。</div>`;
      $("[data-more]", root).hidden = st.hits.size >= st.total;
      autoTranslate(grid);
    } catch (e) {
      grid.innerHTML = `<div class="empty">読み込めませんでした: ${esc(e.message)}</div>`;
    }
  };
  const doSearch = () => { st.q = input.value.trim(); load(true); };
  const favBtn = $("[data-favview]", root);
  const paintFavBtn = () => { favBtn.textContent = `★ お気に入り（${favs.size}）`; favBtn.setAttribute("aria-pressed", st.fav); favBtn.classList.toggle("primary", st.fav); };
  favBtn.onclick = () => { st.fav = !st.fav; paintFavBtn(); load(true); };
  api("favorites_get").then((r) => { favs.clear(); r.items.forEach((x) => favs.set(x.id, x)); paintFavBtn(); }).catch(() => paintFavBtn());
  paintFavBtn();
  const mcSel = $("[data-mcver]", root), ldSel = $("[data-ldr]", root);
  if (mcSel) {
    api("mc_versions").then((r) => {
      mcSel.insertAdjacentHTML("beforeend", r.versions.map((v) => `<option ${v === st.mc ? "selected" : ""}>${esc(v)}</option>`).join(""));
    }).catch(() => {});
    if (ldSel) ldSel.value = st.loader;
    const verChanged = () => {
      st.mc = mcSel.value; st.loader = ldSel.value;
      $("[data-vernote]", root).textContent = st.mc ? `マイクラ ${st.mc}${st.loader ? "・" + ldSel.selectedOptions[0].text : ""} で使えるMODだけを表示しています。` : "指定すると、そのバージョンで使えるMODだけが出ます。";
      if (opts.onVersion) opts.onVersion(st.mc, st.loader);
      load(true);
    };
    mcSel.onchange = verChanged; ldSel.onchange = verChanged;
  }
  $("[data-search]", root).onclick = doSearch;
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  $("[data-cat]", root).onchange = (e) => { st.category = e.target.value; load(true); };
  $("[data-sort]", root).onchange = (e) => { st.sort = e.target.value; load(true); };
  const src = $("[data-src]", root);
  if (src) src.onchange = () => { st.source = src.value; $("[data-cat]", root).disabled = st.source === "curseforge"; if (st.source === "curseforge") st.category = ""; load(true); };
  const only = $("[data-only]", root);
  if (only) only.onchange = () => { st.onlyOk = only.checked; load(true); };
  $("[data-more]", root).onclick = () => { st.offset += 24; load(false); };
  const toggleFav = async (id, card) => {
    const h = st.hits.get(id);
    if (!h) return;
    try {
      const r = await api("favorite_toggle", { card: h });
      favs.clear(); r.items.forEach((x) => favs.set(x.id, x));
      paintFavBtn();
      toast(r.on ? "お気に入りに入れました" : "お気に入りからはずしました");
      if (st.fav) load(true);
      else { const b = $("[data-fav]", card); b.textContent = r.on ? "★" : "☆"; b.setAttribute("aria-pressed", r.on); }
    } catch (err) { fail(err); }
  };
  const toggle = (id) => {
    const h = st.hits.get(id);
    if (!opts.picked || !h) return;
    if (opts.picked.has(id)) opts.picked.delete(id); else opts.picked.set(id, h.title);
    refreshCard(id);
    if (opts.onChange) opts.onChange();
  };
  const refreshCard = (id) => {
    const card = grid.querySelector(`.mod[data-id="${CSS.escape(id)}"]`);
    if (!card) return;
    const on = opts.picked.has(id);
    card.classList.toggle("picked", on);
    const b = $(".pick", card);
    if (b) b.setAttribute("aria-pressed", on);
  };
  grid.addEventListener("click", (e) => {
    const card = e.target.closest(".mod");
    if (!card) return;
    if (e.target.closest("[data-fav]")) { toggleFav(card.dataset.id, card); return; }
    if (e.target.closest(".pick")) { toggle(card.dataset.id); return; }
    openDetail(card.dataset.id, { server: opts.server, picked: opts.picked, onPick: () => { refreshCard(card.dataset.id); if (opts.onChange) opts.onChange(); }, installMode: opts.installMode });
  });
  grid.addEventListener("keydown", (e) => {
    const card = e.target.closest(".mod");
    if (card && (e.key === "Enter" || e.key === " ") && e.target === card) { e.preventDefault(); card.click(); }
  });
  load(true);
  return { reload: () => load(true), refreshCard, hits: st.hits };
}

// ---------------- MODの詳しい説明（右から出る） ----------------
function closeDrawer() {
  const d = $("#drawer");
  if (d && !d.hidden) { d.hidden = true; d.innerHTML = ""; }
}

async function openDetail(id, ctx = {}) {
  const W = ctx.server ? modWordFor(ctx.server) : "MOD";
  const d = $("#drawer");
  d.hidden = false;
  d.innerHTML = `<div class="drawer-close"><button class="btn" data-x>閉じる</button></div><div class="drawer-in"><div class="empty">読み込んでいます…</div></div>`;
  $("[data-x]", d).onclick = closeDrawer;
  let m;
  try {
    m = await api("mod_detail", { id, server: ctx.server || null });
  } catch (e) {
    $(".drawer-in", d).innerHTML = `<div class="empty">読み込めませんでした: ${esc(e.message)}</div>`;
    return;
  }
  if (d.hidden) return;
  const picked = ctx.picked && ctx.picked.has(m.id);
  const action = ctx.picked
    ? `<button class="btn ${picked ? "" : "primary"} big" data-pick>${picked ? "選ぶのをやめる" : `✓ この${W}を選ぶ`}</button>`
    : ctx.server
      ? (m.installed ? `<span class="badge solid-ok" style="font-size:14px;padding:6px 12px">このサーバーに入っています</span>`
        : `<button class="btn primary big" data-install ${m.compat && m.compat.ok === false ? "disabled" : ""}>⬇ この${W}を入れる</button>`)
      : "";
  const compat = m.compat ? `<div class="note ${m.compat.ok ? "ok" : "error"}"><b>${m.compat.ok ? "✔ 使えます" : "✖ 使えません"}</b>${esc(m.compat.text)}</div>` : "";
  const links = Object.entries({ source_url: "ソースコード", issues_url: "不具合の報告", wiki_url: "説明書（Wiki）", discord_url: "Discord" })
    .filter(([k]) => m.links[k]).map(([k, l]) => `<button class="linkbtn" data-url="${esc(m.links[k])}">${l}</button>`).join("　");
  d.querySelector(".drawer-in").innerHTML = `
    <div class="detail-head">${slotImg(m.icon, m.title)}<div>
      <h2>${esc(m.title)}</h2>
      <div class="muted small">${m.author ? "作者 " + esc(m.author) + "　" : ""}ダウンロード ${fmtNum(m.downloads)}回${m.updated ? "　最終更新 " + esc(m.updated) : ""}</div>
      <div class="chips" style="margin-top:6px">${m.library ? '<span class="badge lib">前提MOD</span>' : ""}${(m.genres || []).map((g) => `<span class="badge">${esc(g)}</span>`).join("")}</div>
    </div></div>
    <div class="row" style="margin:18px 0">${action}<button class="btn" data-url="${esc(m.url)}">🌐 サイトで見る（Modrinth）</button></div>
    <div class="stack">
      <div class="well"><div data-ja style="font-size:16px">${esc(m.ja || m.desc)}</div>
        ${m.ja ? (m.ja_source === "auto" ? '<div class="hint">※ 自動翻訳です</div>' : "") : `<div class="hint">英語の説明です。<button class="linkbtn" data-tr>日本語に翻訳する</button></div>`}
      </div>
      ${compat}
      ${(m.conflicts || []).map((c) => `<div class="note ${c.level === "info" ? "" : c.level}"><b>⚠ 相性の注意</b>${esc(c.text)}</div>`).join("")}
      <div class="panel"><h3>どこに入れる${W}か ${help(W === "MOD" ? "side" : "plugin")}</h3><p style="margin:6px 0 0">${esc(m.side)}</p></div>
      ${m.deps && m.deps.length ? `<div class="panel"><h3>一緒に必要な前提${W} ${help("library")}</h3><p class="small muted" style="margin:4px 0">入れると自動で一緒に入ります。</p>
        <div class="chips">${m.deps.map((x) => `<span class="badge ${x.installed ? "ok" : ""}">${x.installed ? "✔ " : ""}${esc(x.title)}</span>`).join("")}</div></div>` : ""}
      <div class="panel"><h3>使えるバージョン ${help("version")}</h3>
        ${m.matrix && m.matrix.length ? `<table class="matrix">${m.matrix.map((r) => `<tr><th>${esc(r.label)}</th><td>${esc(r.text)}${r.newest ? `<div class="small muted">一番新しい対応: ${esc(r.newest)}</div>` : ""}</td></tr>`).join("")}</table>` : `<p class="muted">情報がありません</p>`}
      </div>
      ${m.gallery && m.gallery.length ? `<div><h3 style="margin-bottom:8px">画像</h3><div class="gallery">${m.gallery.map((g, i) => `<button data-img="${i}" aria-label="画像を大きく見る"><img src="${esc(g.url)}" alt="${esc(g.title || "")}" loading="lazy"></button>`).join("")}</div></div>` : ""}
      ${m.body ? `<div class="panel"><div class="row"><h3 class="grow">詳しい説明（作者が書いた文）</h3><button class="btn sm" data-trbody>日本語に翻訳</button></div>
        <div class="body-md clamp" data-body>${renderMarkdown(m.body)}</div><button class="linkbtn" data-expand style="margin-top:8px">続きを読む</button></div>` : ""}
      ${links ? `<p class="small">${links}</p>` : ""}
      ${m.license ? `<p class="small faint">ライセンス: ${esc(m.license)}</p>` : ""}
    </div>`;
  const inner = d.querySelector(".drawer-in");
  const tr = $("[data-tr]", inner);
  if (tr) tr.onclick = async () => {
    tr.disabled = true; tr.textContent = "翻訳中…";
    try {
      const r = await api("translate_text", { text: m.desc });
      $("[data-ja]", inner).textContent = r.ja;
      tr.parentElement.textContent = "※ 自動翻訳です";
    } catch (e) { fail(e); tr.disabled = false; tr.textContent = "日本語に翻訳する"; }
  };
  const trb = $("[data-trbody]", inner);
  if (trb) trb.onclick = async () => {
    trb.disabled = true; trb.textContent = "翻訳中…";
    const plain = markdownToText(m.body).slice(0, 2400);
    try {
      const r = await api("translate_text", { text: plain });
      const body = $("[data-body]", inner);
      body.classList.remove("clamp");
      body.innerHTML = `<p class="hint">※ 自動翻訳です（画像や表は省いています）</p>` + r.ja.split(/\n+/).map((p) => `<p>${esc(p)}</p>`).join("");
      trb.remove();
      const ex = $("[data-expand]", inner); if (ex) ex.remove();
    } catch (e) { fail(e); trb.disabled = false; trb.textContent = "日本語に翻訳"; }
  };
  const ex = $("[data-expand]", inner);
  if (ex) ex.onclick = () => { $("[data-body]", inner).classList.remove("clamp"); ex.remove(); };
  $$("[data-img]", inner).forEach((b) => (b.onclick = () => {
    const g = m.gallery[+b.dataset.img];
    const v = modal(`<img class="viewer" src="${esc(g.url)}" alt=""><p style="text-align:center">${esc(g.title || "")}</p><div class="modal-foot"><button class="btn" data-c>閉じる</button></div>`, { wide: true });
    $("[data-c]", v.el).onclick = v.close;
  }));
  const pk = $("[data-pick]", inner);
  if (pk) pk.onclick = () => {
    if (ctx.picked.has(m.id)) ctx.picked.delete(m.id); else ctx.picked.set(m.id, m.title);
    if (ctx.onPick) ctx.onPick();
    closeDrawer();
  };
  const ins = $("[data-install]", inner);
  if (ins) ins.onclick = async () => {
    closeDrawer();
    await installMods(ctx.server, [m.id]);
    if (ctx.installMode && ctx.installMode.after) ctx.installMode.after();
  };
}

// 足りない前提MODがあれば「入れますか？」と聞いて、はいなら入れる。入れたら true、入れなかったら false
async function ensureDeps(serverId, W, { startLabel } = {}) {
  let missing = [];
  try { missing = (await api("dep_status", { server: serverId })).missing || []; } catch (e) { return null; }
  if (!missing.length) return null;
  const list = missing.map((m) => `<li><b>${esc(m.title)}</b><br><span class="small muted">${esc(m.detail)}</span></li>`).join("");
  const ok = await ask(`前提${W}が足りません`,
    `<p>このまま使うと、サーバーや、ゲームが<b>止まる原因</b>になります。次の前提${W}が必要です。${help("library")}</p><ul>${list}</ul><p class="small muted">「入れる」を押すと、自動で探して入れます。「あとで」を選ぶと、そのままにします。</p>`,
    `入れる`, { cancelText: "あとで" });
  if (!ok) return false;
  const names = [];
  for (const m of missing) {
    const id = m.id;
    try { await runJob(api("install_dep", { server: serverId, id }), `前提${W}「${id}」を入れています`); names.push(id); } catch (e) { if (!e.shown) fail(e); }
  }
  await refreshState();
  return names;
}

async function installMods(serverId, ids, { confirmDeps = true } = {}) {
  const W = modWordFor(serverId);
  try {
    if (confirmDeps) {
      let pv = { deps: [] };
      try { pv = await api("install_preview", { server: serverId, ids }); } catch (e) { /* 調べられなくても入れられる */ }
      if (pv.deps.length && !(await ask(`前提${W}が必要です`,
        `<p>この${W}を動かすには、次の<b>前提${W}</b>（${help("library")}）が必要です。一緒にインストールしますか？</p><div class="chips" style="margin:8px 0">${pv.deps.map((d) => `<span class="badge lib">${esc(d.title)}</span>`).join("")}</div><p class="small muted">前提${W}がないと、サーバーが起動しない原因になります。「やめる」を選ぶと、何も入れません。</p>`,
        `${ids.length + pv.deps.length}個 入れる`))) return null;
    }
    const r = await runJob(api("mods_install", { server: serverId, ids }), `${W}を入れています（${ids.length}個）`);
    const extra = await ensureDeps(serverId, W);
    if (extra && extra.length) r.installed.push(...extra.map((x) => x + "（前提）"));
    let html = "";
    if (r.installed.length) html += `<p><b>入れた${W}</b><br>${r.installed.map(esc).join("、")}</p>`;
    if (r.client_only.length) html += `<p><b>遊ぶ人のPCだけに入るMOD</b>（友達用パックに入ります）<br>${r.client_only.map(esc).join("、")}</p>`;
    if (r.skipped.length) html += `<p class="muted">すでに入っていた${W}: ${r.skipped.map(esc).join("、")}</p>`;
    if (r.errors.length) html += `<div class="note error"><b>入れられなかった${W}</b><ul>${r.errors.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
    if (r.warnings.length) html += `<div class="note warn"><b>相性の注意</b><ul>${r.warnings.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
    if (r.running) html += `<div class="note"><b>サーバーが動いています</b>「再起動」すると、入れた${W}が使えるようになります。</div>`;
    if (W === "MOD" && (r.installed.length || r.client_only.length)) html += `<p class="muted small">友達にも同じMODが必要です。友達用パックは、次にサーバーを起動したとき自動で新しくなります。</p>`;
    if (W !== "MOD" && r.installed.length) html += `<p class="muted small">プラグインはサーバーだけに入れればOKです。友達は何も入れなくて大丈夫です。</p>`;
    await info(`${W}を入れました`, html || "<p>変更はありませんでした。</p>");
    await refreshState();
    const sv = S.servers.find((x) => x.id === serverId);
    if (W === "MOD" && sv && sv.status === "stopped" && (r.installed.length || r.client_only.length)
      && await ask("起動テストをしますか？", `<p>MODを足したので、<b>ちゃんと起動するか</b>を試せます（1〜5分）。テスト用のワールドを使うので、本物のワールドには触りません。</p><p class="small muted">友達を呼ぶ前に、やっておくと安心です。</p>`, "起動テストをする", { cancelText: "あとで" })) {
      S.autoBoot = true; S.tab = "check"; go("server", { server: serverId, tab: "check" });
    }
    return r;
  } catch (e) {
    if (!e.shown) fail(e);
  }
}

// ---------------- かんたんなマークダウン表示（危ないものは消す） ----------------
function markdownToText(md) {
  return md.replace(/<[^>]+>/g, " ").replace(/!\[[^\]]*\]\([^)]*\)/g, "").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#*_`>|]+/g, " ").replace(/-{3,}/g, " ").replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n").trim();
}

function renderMarkdown(md) {
  const lines = md.replace(/\r/g, "").split("\n");
  const out = [];
  let inCode = false, list = null, para = [];
  const flush = () => {
    if (para.length) { out.push("<p>" + para.join(" ") + "</p>"); para = []; }
    if (list) { out.push(`</${list}>`); list = null; }
  };
  const inline = (s) => s
    .replace(/!\[([^\]]*)\]\(([^)\s]+)[^)]*\)/g, '<img alt="$1" src="$2">')
    .replace(/\[([^\]]+)\]\(([^)\s]+)[^)]*\)/g, '<a href="$2">$1</a>')
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|\s)\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^```/.test(line)) { flush(); out.push(inCode ? "</pre>" : "<pre>"); inCode = !inCode; continue; }
    if (inCode) { out.push(esc(line) + "\n"); continue; }
    let m;
    if (!line.trim()) { flush(); continue; }
    if ((m = line.match(/^(#{1,6})\s+(.*)/))) { flush(); out.push(`<h4>${inline(m[2])}</h4>`); continue; }
    if ((m = line.match(/^\s*[-*+]\s+(.*)/))) { if (para.length) flush(); if (list !== "ul") { flush(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = line.match(/^\s*\d+\.\s+(.*)/))) { if (para.length) flush(); if (list !== "ol") { flush(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if (/^\s*-{3,}\s*$/.test(line)) { flush(); out.push("<hr>"); continue; }
    if (/^\s*</.test(line)) { flush(); out.push(line); continue; }
    para.push(inline(line.replace(/^>\s?/, "")));
  }
  flush();
  return sanitize(out.join("\n"));
}

function sanitize(html) {
  const ok = new Set(["P", "BR", "B", "STRONG", "I", "EM", "U", "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL", "LI", "A", "IMG", "CODE", "PRE",
    "BLOCKQUOTE", "HR", "TABLE", "THEAD", "TBODY", "TR", "TD", "TH", "DETAILS", "SUMMARY", "CENTER", "DIV", "SPAN", "SUP", "SUB", "SMALL", "DEL", "S"]);
  const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
  const walk = (node) => {
    for (const ch of Array.from(node.childNodes)) {
      if (ch.nodeType === 8) { ch.remove(); continue; }
      if (ch.nodeType !== 1) continue;
      if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "FORM", "INPUT", "BUTTON", "VIDEO", "AUDIO", "SVG", "MATH", "LINK", "META"].includes(ch.tagName)) { ch.remove(); continue; }
      walk(ch);
      if (!ok.has(ch.tagName)) { ch.replaceWith(...ch.childNodes); continue; }
      const href = ch.getAttribute("href"), src = ch.getAttribute("src"), alt = ch.getAttribute("alt");
      for (const a of Array.from(ch.attributes)) ch.removeAttribute(a.name);
      if (ch.tagName === "A") {
        if (href && /^https?:\/\//i.test(href)) { ch.setAttribute("href", "#"); ch.dataset.url = href; }
      } else if (ch.tagName === "IMG") {
        if (src && /^https:\/\//i.test(src)) { ch.setAttribute("src", src); ch.setAttribute("loading", "lazy"); ch.setAttribute("alt", alt || ""); }
        else ch.remove();
      }
    }
  };
  walk(doc.body.firstChild);
  return doc.body.firstChild.innerHTML;
}

// ================================================================ メモリの表示
function memoryBlock(adv, value, { name = "mem", selfPlay = true } = {}) {
  const max = Math.max(2048, Math.min(adv.total - 1024, 32768));
  return `<div class="stack" data-memblock>
    <div class="row"><b style="font-size:22px" data-memval>${gb(value)}</b><span class="muted">をサーバーに使わせる</span><span class="grow"></span>
      <span class="badge ok">おすすめ ${gb(adv.recommended)}</span></div>
    <input type="range" name="${name}" min="1024" max="${max}" step="512" value="${value}" aria-label="サーバーのメモリ">
    <div class="membar" data-membar aria-hidden="true"></div>
    <div class="legend"><span><i class="mem-sys"></i>Windows・ほかのソフト</span>${selfPlay ? '<span><i class="mem-self"></i>自分が遊ぶマイクラ</span>' : ""}<span><i class="mem-srv"></i>サーバー</span><span>PCのメモリ合計 ${gb(adv.total)}</span></div>
    <div data-memnote></div>
    <details><summary class="small muted" style="cursor:pointer">メモリの決め方（詳しく）</summary>
      <ul class="small" style="margin:8px 0 0;padding-left:1.3em">
        <li>メモリは「作業机の広さ」。サーバーに多く割り当てるほど、MODが多くても重くなりにくくなります。</li>
        <li>目安: バニラ 2GB、MOD少なめ 3〜4GB、MOD 100個前後 5〜6GB、大型MODパック 6〜8GBです。</li>
        <li>多すぎても速くはなりません。PCのメモリが足りなくなると、PC全体が重くなります。</li>
        <li>同じPCで自分も遊ぶなら、マイクラ本体の分（4GBくらい）も残しておきましょう。</li>
        <li>サーバーが「メモリが足りません」で止まったら、1〜2GB増やします。</li>
      </ul></details>
  </div>`;
}

function bindMemory(root, adv, selfPlay, onChange) {
  const range = $("input[type=range]", root);
  const paint = () => {
    const v = +range.value;
    $("[data-memval]", root).textContent = gb(v);
    const sys = 3072, self = selfPlay ? adv.reserve - 3072 : 0;
    const t = adv.total;
    const pct = (x) => Math.max(0, (x / t) * 100);
    const free = t - sys - self - v;
    $("[data-membar]", root).innerHTML = `<div class="mem-sys" style="width:${pct(sys)}%">Windows</div>${self ? `<div class="mem-self" style="width:${pct(self)}%">自分のマイクラ</div>` : ""}<div class="mem-srv" style="width:${pct(v)}%">サーバー ${gb(v)}</div>${free > 0 ? `<div class="mem-free" style="width:${pct(free)}%">空き</div>` : ""}`;
    let note = "";
    if (v > adv.max_safe) note = `<div class="note error"><b>多すぎます</b>PCのメモリが足りなくなり、PC全体やゲームが重くなるかもしれません。${gb(adv.max_safe)} 以下がおすすめです。</div>`;
    else if (v < adv.need && adv.need <= adv.max_safe) note = `<div class="note warn"><b>少ないかもしれません</b>MOD ${adv.mods}個・最大${adv.players}人なら ${gb(adv.need)} くらいあると安心です。</div>`;
    else if (adv.level === "short" && v >= adv.max_safe - 512) note = `<div class="note warn"><b>PCのメモリがぎりぎりです</b>MODの数に対して、PCのメモリが少なめです。重いときは、MODを減らすか、自分が遊ぶPCとサーバーのPCを分けるのがおすすめです。</div>`;
    else note = `<div class="note ok"><b>ちょうどいい量です</b>MOD ${adv.mods}個・最大${adv.players}人を目安に計算しています。</div>`;
    $("[data-memnote]", root).innerHTML = note;
  };
  range.oninput = paint;
  range.onchange = () => onChange && onChange(+range.value);
  paint();
}

// ================================================================ サーバーの画面
const SIMPLE_TABS = new Set(["home", "mods", "public", "pack", "world", "easy", "members", "backup"]);
const isDetail = () => (S.settings && S.settings.ui_mode) === "detail";
const visibleTabs = () => TABS.filter(([k]) => isDetail() || SIMPLE_TABS.has(k));
async function setUiMode(mode) {
  try { await api("set_settings", { ui_mode: mode }); await refreshState(); } catch (e) { fail(e); }
}
const TABS = [["home", "ホーム"], ["mods", "MOD"], ["check", "起動テスト・相性"], ["perf", "重さ・最適化"], ["public", "友達を呼ぶ"],
  ["pack", "友達用パック"], ["world", "ルール・地図"], ["easy", "かんたん設定"], ["members", "メンバー・BAN"], ["commands", "管理コマンド"], ["backup", "バックアップ"], ["settings", "設定"]];

// 5つの大きなタブ（中に、いままでのタブが小見出しとして入る）
const TAB_GROUPS = [
  ["home", "🏠 ホーム", ["home"]],
  ["friends", "👥 友達", ["public", "pack", "members"]],
  ["mods", "🧩 MOD", ["mods", "check"]],
  ["world", "🌍 ワールド", ["world", "commands", "backup"]],
  ["config", "⚙ 設定", ["easy", "perf", "settings"]],
];
const TAB_ICONS = { public: "📨", pack: "📦", members: "👤", mods: "🧩", check: "🧪", world: "🗺", commands: "⌨", backup: "💾", easy: "🎛", perf: "🚀", settings: "🔧" };
function navHtml(s) {
  const vis = visibleTabs();
  const label = (k) => { const t = vis.find(([x]) => x === k); return t ? (k === "mods" ? modWord(s) : t[1]) : ""; };
  const groups = TAB_GROUPS.filter(([, , ks]) => ks.some((k) => vis.some(([x]) => x === k)));
  const cur = TAB_GROUPS.find(([, , ks]) => ks.includes(S.tab)) || TAB_GROUPS[0];
  const top = groups.map(([g, l]) => `<button class="tab" role="tab" data-grp="${g}" aria-selected="${g === cur[0]}">${g === "mods" ? l.replace("MOD", modWord(s)) : l}</button>`).join("");
  const subs = cur[2].filter((k) => vis.some(([x]) => x === k));
  const sub = subs.length > 1 ? `<div class="subtabs" role="tablist">${subs.map((k) => `<button class="subtab" role="tab" data-tab="${k}" aria-selected="${S.tab === k}"><span aria-hidden="true">${TAB_ICONS[k] || ""}</span> ${label(k)}</button>`).join("")}</div>` : "";
  return `<div class="tabs-row"><div class="tabs grp" role="tablist">${top}</div>
    <div class="feat"><input type="search" data-feat placeholder="🔍 機能を探す（例: バックアップ）" aria-label="機能を探す" autocomplete="off"><div class="feat-list" data-featlist hidden></div></div></div>${sub}`;
}

// 機能の検索（名前を入れると、その機能がある場所へ飛ぶ）
const FEATURES = [
  ["サーバーを起動・停止する", "起動 停止 再起動 スタート", "home"], ["コマンドを送る", "コマンド コンソール ログ 入力", "home"],
  ["いま遊んでいる人を見る", "プレイヤー 参加者 人数 オンライン", "home"], ["起動前チェック", "チェック 確認 メモリ ディスク", "home"],
  ["入退出の音", "音 サウンド 通知", "home"], ["招待カードを作る", "招待 カード QR 友達 呼ぶ", "public"],
  ["ネットワーク診断", "ネットワーク 診断 IPv6 UPnP 回線 つながらない", "public"], ["playit を使う", "playit 公開 アドレス トンネル", "public"],
  ["自動ポート開放（UPnP）", "ポート 開放 UPnP ルーター", "public"], ["友達に渡すアドレスを決める", "アドレス IP 保存", "public"],
  ["外から入れるかテストする", "テスト 届く 接続 確認", "public"], ["友達用パック（.mrpack）を作る", "パック mrpack 配布 友達", "pack"],
  ["パックを自動で作り直す", "自動 更新 パック", "pack"], ["自分のマイクラにMODを入れる", "自分 遊ぶ 入れる クライアント", "pack"],
  ["ホワイトリスト（入っていい人）", "ホワイトリスト 名前 登録 メンバー 追加", "members"], ["入りたい人の申請を承認する", "申請 承認 許可", "members"],
  ["BAN・キックする", "BAN キック 追い出す 荒らし", "members"], ["OP（管理者）にする", "OP 管理者 権限", "members"],
  ["あやしいアクセスを見る", "あやしい 侵入 不正 アクセス IP", "members"], ["入れない人の原因を調べる", "入れない 原因 診断 切れる", "members"],
  ["遊んだ時間を見る", "時間 記録 プレイ時間", "members"],
  ["入っているMODを見る・外す", "MOD 一覧 削除 OFF 無効", "mods", "installed"], ["MODを探して入れる", "MOD 検索 追加 インストール Modrinth CurseForge", "mods", "search"],
  ["MODをまとめて更新する", "更新 アップデート 新しい 元に戻す", "mods", "updates"], ["MODの「必須／あると便利」を決める", "必須 便利 任意 印", "mods", "installed"],
  ["起動テスト", "テスト 起動 確認", "check"], ["相性チェック", "相性 競合 エラー 前提", "check"],
  ["みんなのルールを書く", "ルール お知らせ 表示", "world"], ["場所の目印（座標）を登録する", "座標 場所 目印 地図 ランドマーク", "world"],
  ["コマンド辞典", "コマンド 辞典 一覧 意味 help", "commands"], ["アイテムのIDを探す", "アイテム ID give 画像", "commands"],
  ["ボタンでコマンドを実行する", "時間 天気 ゲームモード テレポート give", "commands"],
  ["バックアップを作る・戻す", "バックアップ 復元 戻す 保存", "backup"], ["荒らしの巻き戻し", "荒らし 巻き戻し 壊された", "backup"],
  ["難しさ・ゲームモード・人数を変える", "難易度 ゲームモード 最大人数 PvP かんたん設定", "easy"], ["ゲームルールを変える", "ゲームルール keepInventory", "easy"],
  ["重さ（TPS）を見る", "重い TPS ラグ カクカク グラフ", "perf"], ["軽くする（最適化）", "最適化 軽く 描画距離", "perf"],
  ["ワールドを先に作っておく（事前生成）", "事前生成 プリジェネ チャンク", "perf"],
  ["メモリを変える", "メモリ RAM 割り当て", "settings"], ["自動再起動・自動メモリ調整", "自動 再起動 落ちた クラッシュ", "settings"],
  ["予約（決めた時刻に起動・停止）", "予約 スケジュール 時刻 タイマー", "settings"], ["Discord に通知する", "Discord 通知 Webhook", "settings"], ["Discord から操作する（ボット）", "Discord ボット bot ボタン 起動 停止 遠隔", "settings"],
  ["サーバーの細かい設定（server.properties）", "設定 properties ポート MOTD", "settings"], ["引っ越しファイルを作る", "引っ越し 書き出し エクスポート 移動", "settings"],
];
function bindFeatureSearch(root) {
  const inp = $("[data-feat]", root), list = $("[data-featlist]", root);
  if (!inp) return;
  const vis = () => visibleTabs().map(([k]) => k);
  let items = [], idx = 0;
  const jump = (f) => {
    if (!vis().includes(f[2])) { setUiMode("detail").then(() => { S.tab = f[2]; S.sub = f[3] || "installed"; renderMain(); }); return; }
    S.tab = f[2]; S.sub = f[3] || "installed"; viewServer(root);
  };
  const paint = () => {
    const q = inp.value.trim().toLowerCase();
    items = q ? FEATURES.filter((f) => q.split(/\s+/).every((w) => (f[0] + " " + f[1]).toLowerCase().includes(w)))
      .sort((a, b) => (b[0].toLowerCase().includes(q) ? 1 : 0) - (a[0].toLowerCase().includes(q) ? 1 : 0)).slice(0, 8) : [];
    idx = Math.min(idx, Math.max(0, items.length - 1));
    list.hidden = !q;
    const tabName = (k) => { const g = TAB_GROUPS.find((x) => x[2].includes(k)); const t = TABS.find((x) => x[0] === k); return (g ? g[1].replace(/^\S+\s/, "") : "") + (g && g[2].length > 1 && t ? " › " + t[1] : ""); };
    list.innerHTML = items.length ? items.map((f, i) => `<button class="feat-item ${i === idx ? "on" : ""}" data-fi="${i}"><b>${esc(f[0])}</b><span>${esc(tabName(f[2]))}${vis().includes(f[2]) ? "" : "（詳しい表示）"}</span></button>`).join("")
      : `<div class="feat-none">見つかりません。別のことばで試してください。</div>`;
    $$("[data-fi]", list).forEach((b) => (b.onmousedown = (e) => { e.preventDefault(); jump(items[+b.dataset.fi]); }));
  };
  inp.addEventListener("input", () => { idx = 0; paint(); });
  inp.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "ArrowDown") { idx = Math.min(idx + 1, items.length - 1); paint(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { idx = Math.max(idx - 1, 0); paint(); e.preventDefault(); }
    else if (e.key === "Enter" && items[idx]) { jump(items[idx]); }
    else if (e.key === "Escape") { inp.value = ""; paint(); }
  });
  inp.addEventListener("blur", () => setTimeout(() => { list.hidden = true; }, 150));
  inp.addEventListener("focus", paint);
}

function viewServer(root) {
  const s = cur();
  if (!s) { go("welcome"); return; }
  const [cls, label] = statusInfo(s);
  const running = s.status !== "stopped";
  S.lastSig = { id: s.id, sig: srvSig(s) };
  if (!visibleTabs().some(([k]) => k === S.tab)) S.tab = "home";
  root.innerHTML = `<div class="page">
    <div class="srv-head">
      <div class="grow">
        <h1>${esc(s.name)}</h1>
        <div class="meta"><span>マイクラ ${esc(s.mc)}</span><span>${esc(s.loader_label)}</span><span>サーバーの${modWord(s)} ${s.mods}個</span><span>ポート ${esc(s.port)}</span></div>
        <div class="status-line"><span class="dot ${cls}"></span><span data-status>${label}</span></div>
      </div>
      <div class="row">
        <button class="btn go big" data-start ${running ? "disabled" : ""}>▶ 起動</button>
        <button class="btn big" data-stop ${s.status === "running" || s.status === "starting" ? "" : "disabled"}>■ 停止</button>
        <button class="btn" data-restart ${s.status === "running" ? "" : "disabled"}>再起動</button>
      </div>
    </div>
    <div data-lights>${lightsHtml(s)}</div><div data-pcwarn></div>
    <div class="row" style="justify-content:flex-end;margin:4px 0"><button class="linkbtn" data-diag title="起動できない原因になりやすいことを、まとめて調べます">困ったとき診断</button><button class="linkbtn" data-delsrv title="このサーバーをフォルダごと消します">このサーバーを消す</button><button class="linkbtn" data-modebtn title="タブの数を切り替えます">${isDetail() ? "かんたん表示にする" : "詳しい機能も表示する"}</button></div>
    ${navHtml(s)}
    <div class="tab-body" data-body></div>
  </div>`;
  $$("[data-tab]", root).forEach((b) => (b.onclick = () => { S.tab = b.dataset.tab; S.sub = "installed"; viewServer(root); }));
  bindFeatureSearch(root);
  $$("[data-grp]", root).forEach((b) => (b.onclick = () => {
    const g = TAB_GROUPS.find((x) => x[0] === b.dataset.grp);
    const vis = visibleTabs().map(([k]) => k);
    const first = g[2].find((k) => vis.includes(k));
    if (first) { S.tab = first; S.sub = "installed"; viewServer(root); }
  }));
  $("[data-diag]", root).onclick = () => openDiagnose(cur() || s);
  $("[data-delsrv]", root).onclick = () => deleteServerFlow(cur() || s);
  $("[data-modebtn]", root).onclick = async () => { await setUiMode(isDetail() ? "simple" : "detail"); viewServer(root); };
  bindLights(root, s);
  showForeignBanner();
  $("[data-start]", root).onclick = () => startServer(s.id);
  $("[data-stop]", root).onclick = async () => { await api("server_stop", { id: s.id }).catch(fail); toast("サーバーを止めています（ワールドを保存中）"); later(refreshAll, 600); };
  $("[data-restart]", root).onclick = async () => { await api("server_restart", { id: s.id }).catch(fail); toast("再起動しています"); later(refreshAll, 600); };
  const body = $("[data-body]", root);
  const tabs = { home: tabHome, mods: tabMods, check: tabCheck, perf: tabPerf, public: tabPublic, pack: tabPack, members: tabMembers, world: tabWorld, easy: tabEasy, commands: tabCommands, backup: tabBackup, settings: tabSettings };
  (tabs[S.tab] || tabHome)(body, s);
}

const later = (f, ms) => setTimeout(f, ms);
// Paper / Purpur は MOD ではなく「プラグイン」
const PLUGIN_LOADERS = ["paper", "purpur"];
const modWord = (s) => (s && PLUGIN_LOADERS.includes(s.loader) ? "プラグイン" : "MOD");
const modWordFor = (id) => modWord(S.servers.find((x) => x.id === id));

const srvSig = (s) => JSON.stringify([s.id, s.status, s.mods, s.playit, s.pack_exported, s.whitelist_on, s.members, s.address, s.port, !!s.crash]);
const RELOAD_TABS = ["home", "check", "public", "pack", "members", "backup", "mods"];
function safeToRerender() {
  if ($("#modal-root") && $("#modal-root").children.length) return false;
  const ae = document.activeElement;
  if (ae && $("#main").contains(ae) && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName)) return false;
  if (S.tab === "mods" && S.sub !== "installed") return false;
  return RELOAD_TABS.includes(S.tab);
}
async function refreshAll() {
  await refreshState();
  if (S.view === "server") {
    const s = cur();
    if (!s) return go("welcome");
    const root = $("#main");
    // 見出しのボタンと状態
    const [cls, label] = statusInfo(s);
    const st = $("[data-status]", root);
    if (st) {
      st.textContent = label;
      $(".status-line .dot", root).className = "dot " + cls;
      $("[data-start]", root).disabled = s.status !== "stopped";
      $("[data-stop]", root).disabled = !(s.status === "running" || s.status === "starting");
      $("[data-restart]", root).disabled = s.status !== "running";
    }
    const meta = $(".srv-head .meta", root);
    if (meta) meta.innerHTML = `<span>マイクラ ${esc(s.mc)}</span><span>${esc(s.loader_label)}</span><span>サーバーの${modWord(s)} ${s.mods}個</span><span>ポート ${esc(s.port)}</span>`;
    // 信号機
    const lg = $("[data-lights]", root);
    if (lg) { lg.innerHTML = lightsHtml(s); bindLights(root, s); }
    // 中身が変わったときは、いま開いているタブも書きかえる（入力中・ダイアログ表示中は、じゃましない）
    const sig = srvSig(s);
    if (S.lastSig && S.lastSig.id === s.id && S.lastSig.sig !== sig && safeToRerender()) viewServer(root);
    S.lastSig = { id: s.id, sig };
  }
}

async function startServer(id) {
  try {
    let r = await api("server_start", { id });
    if (r.need_eula) {
      const ok = await ask("利用規約（EULA）への同意",
        `<p>サーバーを動かすには、マイクラの利用規約（EULA）への同意が必要です。${help("eula")}</p>
         <p>おもな内容: サーバーでお金をとって、ゲームが有利になるものを売ってはいけない、など。</p>
         <p><button class="linkbtn" data-url="https://aka.ms/MinecraftEULA">利用規約を読む（ブラウザで開きます）</button></p>`, "同意して起動");
      if (!ok) return;
      r = await api("server_start", { id, accept_eula: true });
    }
    if (r.need_deps) {
      const W = modWordFor(id);
      const list = r.need_deps.map((m) => `<li><b>${esc(m.title)}</b><br><span class="small muted">${esc(m.detail)}</span></li>`).join("");
      const ok = await ask(`前提${W}が足りません`, `<p>このまま起動すると、<b>止まる可能性が高い</b>です。${help("library")}</p><ul>${list}</ul><p class="small muted">間違った警告で、毎回出るときは、「起動テスト・相性」タブの警告にある「この警告は出さない」を押してください。</p>`, "入れてから起動", { cancelText: "このまま起動" });
      if (ok) {
        for (const m of r.need_deps) {
          try { await runJob(api("install_dep", { server: id, id: m.id }), `前提${W}「${m.id}」を入れています`); } catch (e) { if (!e.shown) fail(e); }
        }
        await refreshState();
      }
      r = await api("server_start", { id, accept_eula: true, skip_deps: true });
    }
    if (r.stale) {
      const killed = await killJavaFlow(id, r.stale, { locked: r.locked, portBusy: r.port_busy, startAfter: true });
      if (killed === null) return;
      r = await api("server_start", { id, accept_eula: true, skip_deps: true, skip_stale: true });
    }
    toast("起動しています。はじめての起動は数分かかることがあります。");
    S.consoleNext = 0;
    later(refreshAll, 500);
  } catch (e) { fail(e); }
}

// 前のサーバー（java）が裏で残っているとき：一覧を見せて、終了するか聞く。
// 終了したら true、「そのまま起動する」なら false、やめたら null を返す。
function killJavaFlow(sid, list, { locked = false, portBusy = false, startAfter = false } = {}) {
  return new Promise(async (resolve) => {
    if (!list) { try { list = (await api("stale_java_list", { id: sid })).stale; } catch (e) { fail(e); return resolve(null); } }
    if (!list.length) { await info("残っているjavaは見つかりませんでした", `<p>裏で動いているサーバー（java）はありません。</p><p class="small muted">ワールドの鍵が使われているときは、数十秒待つか、PCを再起動してください。バックアップ・ウイルス対策ソフト・OneDriveなどが見ている可能性もあります。</p>`); return resolve(false); }
    const why = locked ? "ワールドの鍵（session.lock）を持っていて、新しいサーバーを起動できません。" : portBusy ? "サーバーが使うポート番号を持っていて、新しいサーバーを起動できません。" : "裏で動いたままになっています。";
    const rows = list.map((j) => `<label class="row" style="gap:10px;align-items:flex-start;margin:8px 0"><input type="checkbox" data-kj value="${j.pid}" ${j.certain || list.length === 1 ? "checked" : ""}>
      <span><b>java（番号 ${j.pid}）</b>${j.certain ? ' <span class="small" style="color:var(--ok-text)">このサーバーのものです</span>' : ' <span class="small muted">ほかのサーバーのものかもしれません</span>'}<br><span class="small muted">${j.started ? esc(j.started) + " に起動 / " : ""}${esc(j.cmd)}</span></span></label>`).join("");
    const m = modal(`<h2>前のサーバーが、裏で動いたままです</h2><div class="plain"><p>次の java が、${why}</p>${rows}
      <p class="small muted">終了すると、<b>最後の数分の変更が保存されない</b>ことがあります。普通は、画面の「■ 停止」で止めてから閉じてください。</p></div>
      <div class="modal-foot"><button class="btn ghost" data-no>やめる</button>${startAfter ? '<button class="btn" data-skip>そのまま起動する</button>' : ""}<button class="btn danger" data-kill>チェックしたjavaを終了${startAfter ? "して起動" : ""}</button></div>`,
      { onClose: () => resolve(null) });
    $("[data-no]", m.el).onclick = () => { m.close(); };
    const sk = $("[data-skip]", m.el);
    if (sk) sk.onclick = () => { m.el.parentElement.remove(); resolve(false); };
    $("[data-kill]", m.el).onclick = async (e) => {
      const pids = $$("[data-kj]", m.el).filter((b) => b.checked).map((b) => +b.value);
      if (!pids.length) { toast("終了するjavaを選んでください", "error"); return; }
      e.target.disabled = true; e.target.textContent = "終了しています…";
      try {
        const r = await api("stale_java_kill", { id: sid, pids });
        m.el.parentElement.remove();
        toast(r.killed.length ? `javaを ${r.killed.length}個 終了しました` : "終了できたjavaはありません");
        resolve(true);
      } catch (err) { fail(err); m.el.parentElement.remove(); resolve(null); }
    };
  });
}

// 「困ったとき診断」：起動できない原因になりやすいことを、まとめて調べる
async function openDiagnose(s) {
  const m = modal(`<h2>困ったとき診断</h2><div data-body><div class="empty">調べています…</div></div>
    <div class="modal-foot"><button class="btn" data-bundle title="ログや設定を、秘密の情報を消してzipにまとめます。作者に送るときに使います">診断情報をまとめる</button><button class="btn primary" data-close>閉じる</button></div>`, { wide: true });
  $("[data-close]", m.el).onclick = () => m.close();
  $("[data-bundle]", m.el).onclick = (e) => supportBundle(s, e.target);
  const body = $("[data-body]", m.el);
  try {
    const r = await api("diagnose_run", { id: s.id });
    const head = r.running ? `<p class="muted">サーバーは動いています。止まっているときに、もう一度調べると、より正確です。</p>` : "";
    body.innerHTML = head + (r.issues.length
      ? `<p class="muted">見つかったこと:</p><div class="stack">${r.issues.map((i) => issueHtml(i, s.id)).join("")}</div>`
      : `<div class="note ok"><b>問題は見つかりませんでした</b>起動できない原因になりやすいことは、見当足りません。</div>`);
    bindIssueActions(body, s, () => setTimeout(() => { m.close(); openDiagnose(s); }, 400));
  } catch (e) { fail(e); body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

// ---------------- 診断情報をまとめる ----------------
async function supportBundle(s, btn) {
  const ok = await ask("診断情報をまとめます",
    `<p>困ったときに、原因を調べてもらうための <b>zip</b> を、デスクトップに作ります。</p>
     <p><b>入るもの:</b> ソフトの版・PCの情報・${s ? "MODの一覧・診断の結果・ログ（記録）・クラッシュレポート" : "ソフトのエラーの記録"}</p>
     <p><b>消すもの:</b> 鍵（Webhook・APIキー・パスワード）・PCのユーザー名・IPアドレス・公開用のアドレス</p>
     <p class="small muted">ワールドのデータは入りません。ログには、遊んだ人のゲーム内の名前が入ることがあります。送る前に、中身を見て確認してください。</p>`, "まとめる");
  if (!ok) return;
  if (btn) btn.disabled = true;
  try {
    const r = await api("support_bundle", s ? { id: s.id } : {});
    await info("できました", `<p>デスクトップに、診断情報のzip（約${r.kb}KB）を作りました。フォルダを開きました。</p><p class="small muted">${esc(r.path)}</p><p>困っていることと一緒に、このzipを、詳しい人（作者）に送ってください。</p>`);
  } catch (e) { fail(e); } finally { if (btn) btn.disabled = false; }
}

// ---------------- ソフトの更新 ----------------
function updateNoticeHtml(i) {
  if (!i) return "";
  if (!i.latest) return `<div class="note"><b>${esc(i.message || "確認できませんでした。")}</b></div>`;
  if (!i.newer) return `<div class="note ok"><b>一番新しい版です（${esc(i.current)}）</b>${i.message ? esc(i.message) : ""}</div>`;
  const notes = i.notes ? `<p class="small" style="white-space:pre-wrap;max-height:160px;overflow:auto;margin:8px 0">${esc(i.notes)}</p>` : "";
  return `<div class="note warn"><b>新しい版があります: ${esc(i.latest)}</b>（いまは ${esc(i.current)}）${notes}
    ${i.message ? `<p class="small">${esc(i.message)}</p>` : ""}
    <div class="row" style="margin-top:8px">${i.can_apply && i.safe ? '<button class="btn primary" data-doupdate>更新する</button>' : ""}<button class="btn ghost" data-relpage>リリースのページを開く</button></div></div>`;
}

async function applyUpdateFlow(i) {
  const running = S.servers.filter((s) => s.status !== "stopped");
  if (running.length) { await info("先にサーバーを止めてください", `<p>動いているサーバー（${running.map((s) => esc(s.name)).join("、")}）があります。「■ 停止」で止めてから、もう一度押してください。</p>`); return; }
  if (!(await ask(`版 ${i.latest} に更新します`,
    `<p>新しい版をダウンロードして、入れ替えます。終わると、<b>ソフトが自動で開き直します</b>。</p>
     <p class="small muted">サーバー・ワールド・設定（dataフォルダ）は、そのまま残ります。ダウンロードしたファイルは、指紋（SHA-256）が合っているときだけ使います。失敗したら、元の版に戻します。</p>`, "更新する"))) return;
  S.updating = true;
  try {
    await runJob(api("update_apply"), "ソフトを更新しています", { note: "ダウンロードのあと、ソフトが自動で終了して、開き直します。" });
  } catch (e) {
    if (e.shown) { S.updating = false; return; }
    if (S.updating) {   // ソフトが終了して、通信が切れた＝入れ替えが始まった
      document.body.innerHTML = `<div style="display:grid;place-items:center;height:100vh;text-align:center;padding:0 24px"><div><h1>更新しています…</h1><p class="muted">数秒たつと、ソフトが自動で開き直します。<br>このウィンドウは、閉じても大丈夫です。</p></div></div>`;
      return;
    }
    fail(e);
  }
}

function bindUpdateButtons(root, afterCheck) {
  const d = $("[data-doupdate]", root);
  if (d) d.onclick = () => applyUpdateFlow(S._updInfo);
  const r = $("[data-relpage]", root);
  if (r) r.onclick = () => api("open_releases_page").catch(fail);
}

async function checkUpdateNotice() {
  try {
    const r = await api("update_status");
    if (r.failed) toast(r.failed, "warn", 12000);
  } catch (e) { /* 何もしない */ }
  setTimeout(async () => {
    try {
      await refreshState();
      if (S.update && S.update.newer && !S.updToast) { S.updToast = true; toast(`新しい版があります（${S.update.latest}）。「ソフトの設定」から更新できます`, "warn", 9000); }
    } catch (e) { /* 何もしない */ }
  }, 15000);
}

// ---------------- ホーム ----------------
function crashCard(s, crash) {
  if (!crash || s.status !== "stopped") return "";
  return `<div class="note error" style="margin-bottom:16px"><b>サーバーが${crash.during_start ? "起動の途中で" : "急に"}止まりました</b>
    ${crash.description ? `<p class="small">マイクラの記録: <b>${esc(crash.description)}</b>${crash.crash_report ? `（${esc(crash.crash_report)}）` : ""}</p>` : ""}
    <p>ログ（記録）から考えられる原因です:</p>
    <div class="stack">${(crash.issues || []).map((i) => issueHtml(i, s.id)).join("")}</div>
    ${crash.gave_up ? `<div class="note warn" style="margin:8px 0"><b>自動の再起動を、やめました</b>30分のうちに何度も落ちたので、くり返さないようにしました。原因を直してから、手動で起動してください。</div>` : ""}
    ${crash.memory_raised ? `<div class="note ok" style="margin:8px 0"><b>メモリを ${gb(crash.memory_raised)} に増やしました</b>メモリ不足で落ちたので、自動で増やしました（次の起動から）。</div>` : ""}
    ${crash.memory_suggest ? (crash.memory_suggest.suggested ? `<div class="note warn" style="margin:8px 0"><b>メモリ不足のようです</b>${gb(crash.memory_suggest.current)} → ${gb(crash.memory_suggest.suggested)} に増やしますか？<div style="margin-top:8px"><button class="btn sm primary" data-memup="${crash.memory_suggest.suggested}">${gb(crash.memory_suggest.suggested)} に増やす</button></div></div>` : `<div class="note warn" style="margin:8px 0"><b>メモリ不足のようです</b>PCのメモリの安全な上限（${gb(crash.memory_suggest.max_safe)}）まで使っています。MODを減らすか、PCのメモリを増やしてください。</div>`) : ""}
    <div class="row" style="margin-top:12px"><button class="btn sm" data-gocheck>相性チェックをする</button><button class="btn sm" data-logs>ログのフォルダを開く</button></div></div>`;
}

function bindCrash(root, s) {
  const g = $("[data-gocheck]", root);
  if (g) g.onclick = () => { S.tab = "check"; viewServer($("#main")); };
  const l = $("[data-logs]", root);
  if (l) l.onclick = () => api("open_folder", { id: s.id, which: "logs" }).catch(fail);
  const mu = $("[data-memup]", root);
  if (mu) mu.onclick = async () => { try { await api("server_set", { id: s.id, profile: { memory_mb: +mu.dataset.memup } }); toast("メモリを増やしました（次の起動から）"); await refreshState(); mu.disabled = true; } catch (e) { fail(e); } };
  bindIssueActions(root, s);
}

// ================================================================ 状態の信号機・「次にやること」
function lightsHtml(s) {
  const run = s.status === "running", starting = s.status === "starting";
  const L = [
    { k: "srv", t: "サーバー", c: run ? "ok" : starting ? "warn" : "ng", m: run ? "動いています" : starting ? "起動中…" : s.crash ? "止まりました（トラブル）" : "止まっています",
      b: !run && !starting ? (s.crash ? ["原因を調べる", "check"] : ["▶ 起動", "start"]) : null },
    { k: "pl", t: "playit（外とつなぐ道）", c: s.playit ? "ok" : s.address ? "warn" : "idle", m: s.playit ? "動いています" : "使っていません",
      b: s.playit ? null : ["▶ 起動", "playit"] },
    { k: "in", t: "友達が入れる状態", c: run && s.address ? "ok" : "ng", m: run && s.address ? "入れます" : !s.address ? "アドレスがありません" : "サーバーが止まっています",
      b: !s.address ? ["アドレスを作る", "public"] : null },
  ];
  return `<div class="lights">${L.map((x) => `<div class="light ${x.c}"><span class="dot ${x.c === "ok" ? "running" : x.c === "warn" ? "starting" : x.c === "ng" ? "crash" : ""}"></span>
    <div class="grow"><b>${x.t}</b><span class="small">${x.m}</span></div>${x.b ? `<button class="btn sm" data-light="${x.b[1]}">${x.b[0]}</button>` : ""}</div>`).join("")}</div>`;
}

function bindLights(root, s) {
  $$("[data-light]", root).forEach((b) => (b.onclick = async () => {
    const a = b.dataset.light;
    if (a === "start") return startServer(s.id);
    if (a === "playit") {
      b.disabled = true;
      try { await runJob(api("playit_toggle"), "playit を準備しています"); await refreshState(); viewServer($("#main")); } catch (e) { if (!e.shown) fail(e); }
      return;
    }
    if (a === "check") { S.tab = "check"; viewServer($("#main")); return; }
    if (a === "public") { S.tab = "public"; viewServer($("#main")); }
  }));
}

function nextSteps(s) {
  const run = s.status === "running";
  const crashed = s.crash && s.status === "stopped";
  const steps = [
    { k: "start", t: "サーバーを起動する", d: "右上の「▶ 起動」を押して、「起動完了」と出るまで待ちます（MODが多いと数分かかります）。", done: run || s.status === "starting", b: crashed ? ["止まった原因を調べる", "check"] : ["▶ 起動する", "start"] },
    { k: "addr", t: "友達が使うアドレスを作る", d: "「ネットワーク診断」を押すと、あなたの回線に合う方法がわかります。できたアドレスを保存します（初回だけ）。", done: !!s.address, b: ["アドレスを作る", "public"] },
    { k: "mem", t: "友達の「マイクラの名前」を登録する", d: "友達に「マイクラの名前」を聞いて、登録します。登録していない人は、サーバーに入れません。", done: !s.whitelist_on || s.members > 0, b: ["名前を登録する", "members"] },
    { k: "pack", t: "友達用パックを作って送る", d: "友達があなたと同じMODで遊べるように、パックを作ります。できたら、招待カードと一緒に友達へ送ります。", done: s.pack_exported, b: ["パックを作る", "pack"] },
  ];
  if (crashed) steps.unshift({ k: "crash", t: "サーバーが止まりました。原因を調べる", d: "記録から原因を探して、直し方を出します。", done: false, b: ["原因を調べる", "check"] });
  return steps;
}

function nextCardHtml(s) {
  const steps = nextSteps(s);
  const total = steps.filter((x) => x.k !== "crash").length, doneN = steps.filter((x) => x.k !== "crash" && x.done).length;
  const cur = steps.find((x) => !x.done);
  if (S.settings && S.settings.hide_next_step) {
    return `<div class="row" style="margin-bottom:10px"><button class="linkbtn" data-nexthide="show">次にやることを表示する</button></div>`;
  }
  const list = steps.map((x) => `<li class="${x.done ? "done" : x === cur ? "now" : ""}"><span class="ck">${x.done ? "✔" : x === cur ? "▶" : "・"}</span>${esc(x.t)}</li>`).join("");
  return `<div class="next-card ${cur ? "" : "alldone"}" data-next>
    <div class="row top"><div class="grow">
      <div class="small muted">進み具合: ${total}つ中 <b>${doneN}つ</b> 終わりました</div>
      <div class="meter"><i style="width:${Math.round(doneN / total * 100)}%"></i></div></div>
      <button class="linkbtn" data-nexthide="hide" title="この案内を消します（あとで、また表示できます）">× 非表示</button></div>
    ${cur ? `<div class="now-step"><div class="small muted">手順 ${steps.filter((x) => x.k !== "crash").indexOf(cur) + 1} / ${total}　今やること</div><h3>${esc(cur.t)}</h3><p class="muted">${esc(cur.d)}</p>
      <div class="row"><button class="btn go big glow" data-nextgo="${cur.b[1]}">${cur.b[0]}</button><button class="btn" data-quick title="サーバー起動・playit 起動・パック作成を、順番に自動でやります">全部自動でやる</button></div></div>`
    : `<div class="now-step"><h3>✔ 全部終わりました。遊べます！</h3><p class="muted">友達に、アドレスと入り方カードを送ってください。</p></div>`}
    <details class="small" style="margin-top:8px"><summary style="cursor:pointer">全部の手順を見る</summary><ul class="nextlist">${list}</ul></details></div>`;
}

function bindNextCard(body, s) {
  $$("[data-nexthide]", body).forEach((b) => (b.onclick = async () => {
    try { await api("set_settings", { hide_next_step: b.dataset.nexthide === "hide" }); await refreshState(); tabHome(body, cur()); } catch (e) { fail(e); }
  }));
  $$("[data-nextgo]", body).forEach((b) => (b.onclick = () => {
    const a = b.dataset.nextgo;
    if (a === "start") return startServer(s.id);
    S.tab = a; viewServer($("#main"));
  }));
  const q = $("[data-quick]", body);
  if (q) q.onclick = async () => {
    try {
      const r = await runJob(api("quick_invite", { id: s.id }), "友達を呼ぶ準備をしています", { note: "サーバーとplayitの起動、パックの作成を順番にやります。" });
      await refreshState();
      await info("やったこと", `<ul>${r.done.map((x) => `<li>✔ ${esc(x)}</li>`).join("")}</ul>${r.todo.length ? `<div class="note warn"><b>あなたがやること</b><ul>${r.todo.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}`);
      if (!r.address) { S.tab = "public"; }
      viewServer($("#main"));
    } catch (e) { if (!e.shown) fail(e); }
  };
}

// ---------------- はじめてガイド（最初の1回だけ） ----------------
async function startTour(force = false) {
  if (S.touring || (!force && S.settings && S.settings.tour_done)) return;
  S.touring = true;
  try { await runTour(); } finally { S.touring = false; }
}

async function runTour() {
  const steps = [
    ["ここが「次にやること」です", "いま、やることを1つだけ大きく出します。ボタンを押していけば、友達を呼べます。いらなければ「× 非表示」で消せます。", "[data-next]"],
    ["上の3つの信号で、状態がわかります", "緑は「だいじょうぶ」、赤は「直すところあり」です。赤のときは、その横のボタンで直せます。", "[data-lights]"],
    ["ふだんは、少ないタブで使えます", "難しい機能は、ふだんは隠れています。必要になったら、ここで「詳しい機能も表示する」を押します。", "[data-modebtn]"],
    ["友達に渡すものは、ここで作れます", "「友達」タブの「友達用パック」で、パックと「入り方カード」が作れます。「全部自動でやる」でも作れます。", "[data-grp=friends]"],
  ];
  for (let i = 0; i < steps.length; i++) {
    const [t, d, sel] = steps[i];
    const el = $(sel);
    if (el) { el.classList.add("tour-hl"); el.scrollIntoView({ block: "center", behavior: "smooth" }); }
    const ok = await ask(`はじめてガイド ${i + 1}/${steps.length}　${t}`, `<p>${d}</p>`, i === steps.length - 1 ? "わかった" : "次へ", { cancelText: "ガイドを閉じる" });
    if (el) el.classList.remove("tour-hl");
    if (!ok) break;
  }
  try { await api("set_settings", { tour_done: true }); await refreshState(); } catch (e) { /* 次回また出るだけ */ }
}

function tabHome(body, s) {
  body.innerHTML = `${nextCardHtml(s)}<div data-crash data-key="${s.crash && s.status === "stopped" ? esc(String(s.crash.time)) : ""}">${crashCard(s, s.crash)}</div>
    <div class="home-grid">
      <div>
        <div class="row" style="margin-bottom:8px"><h3 class="grow">サーバーの画面（ログ）</h3><span class="small muted">黄色は注意、赤はエラー</span></div>
        <div class="prog" data-prog hidden><div class="meter"><i data-progbar style="width:0"></i></div><div class="small muted" data-progtxt style="margin-top:4px"></div></div>
        <div class="note warn" data-lag-banner hidden style="margin-bottom:10px"></div>
        <div class="console" data-console aria-live="off"></div>
        <div class="inline-field" style="margin-top:10px">
          <input type="text" data-cmd placeholder="コマンドを入れて Enter（日本語で探せます。例: 天気）" aria-label="コマンド" autocomplete="off">
          <button class="btn" data-send>送る</button>
          ${cpBtn("", "ログをコピー", "ログ").replace('data-cp=""', "data-copylog")}
        </div>
        <div class="row" style="margin-top:8px;gap:6px;flex-wrap:wrap" data-quickrow></div>
      </div>
      <div class="stack">
        <div data-preflight></div>
        <div class="panel"><div class="row"><h3 class="grow" style="white-space:nowrap">今いる人</h3><label class="check small" title="誰かが入ったとき・出たときに、音とお知らせを出します"><input type="checkbox" data-snd ${soundOn() ? "checked" : ""}> 入退出の音</label></div><div class="players" data-players style="margin-top:8px"></div></div>
        <div class="panel"><h3>つながらなかった人 ${help("joinfail")}</h3><p class="muted small" style="margin:6px 0">入れなかった・切れた人と、その理由を、サーバーの記録から探します。</p>
          <button class="btn sm" data-joinfail>調べる</button><div data-joinfailout style="margin-top:8px"></div></div>
        <div class="panel"><h3>友達に伝えるアドレス</h3>
          ${s.address ? `<div class="addr" style="margin:8px 0">${esc(s.address)}</div><button class="btn sm" data-copy>コピー</button> <button class="btn sm" data-card>招待カード</button>`
            : `<p class="muted small" style="margin:6px 0">まだ決まっていません。</p><button class="btn sm primary" data-topublic>友達を呼ぶ準備をする</button>`}
        </div>
        <div class="panel"><h3>メモリ ${help("memory")}</h3><p style="margin:6px 0">${gb(s.memory_mb)} を使っています</p><button class="linkbtn" data-tomem>変える</button></div>
        <div class="panel"><h3>サーバーの情報</h3><div class="small" style="margin-top:6px;display:grid;gap:6px">
          <div class="row" style="gap:6px"><span class="muted">ID</span><code class="grow" style="overflow:hidden;text-overflow:ellipsis">${esc(s.id)}</code>${cpBtn(s.id, "コピー", "サーバーのID")}</div>
          <div class="row" style="gap:6px"><span class="muted">名前</span><span class="grow">${esc(s.name)}</span>${cpBtn(s.name, "コピー", "サーバーの名前")}</div>
          <div class="row" style="gap:6px"><span class="muted">ポート</span><span class="grow">${esc(s.port)}</span>${cpBtn(String(s.port), "コピー", "ポート番号")}</div></div></div>
      </div>
    </div>`;
  bindCrash(body, s);
  bindNextCard(body, s);
  setTimeout(() => startTour(), 600);
  $("[data-joinfail]", body).onclick = async (e) => {
    const out = $("[data-joinfailout]", body);
    e.target.disabled = true;
    try {
      const r = await api("join_problems", { id: s.id });
      out.innerHTML = r.items.length ? `<div class="stack">${r.items.map((i) => `<div class="note warn" style="margin:0"><b>${esc(i.who)}</b> ${i.who && !/\s/.test(i.who) ? cpBtn(i.who, "名前", "名前") : ""}　<span class="small muted">${esc(i.time)}</span><br>${esc(i.title)}<div class="small">どうする？ ${esc(i.fix)}</div><div class="small muted">記録: ${esc(i.raw)}</div></div>`).join("")}</div>`
        : `<div class="note ok" style="margin:0"><b>問題は見つかりませんでした</b>最近、入れなかった人はいません。</div>`;
    } catch (err) { out.textContent = err.message; } finally { e.target.disabled = false; }
  };
  const con = $("[data-console]", body);
  const cmd = $("[data-cmd]", body);
  const send = async (c) => {
    c = (c || cmd.value).trim();
    if (!c) return;
    try { await api("server_command", { id: s.id, command: c }); cmd.value = ""; pollConsole(true); } catch (e) { fail(e); }
  };
  $("[data-send]", body).onclick = () => send();
  cmd.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  const cp = $("[data-copy]", body);
  if (cp) cp.onclick = () => copyText(s.address, "アドレス");
  const cd = $("[data-card]", body);
  if (cd) cd.onclick = () => inviteCardFlow(s);
  $("[data-snd]", body).onchange = (e) => { setSound(e.target.checked); wakeAudio(); if (e.target.checked) beep("join"); };
  $("[data-copylog]", body).onclick = () => copyText($$("div", con).map((d) => d.textContent).join("\n"), "ログ");
  bindCmdSuggest(cmd, () => (cur() ? cur().players || [] : []));
  api("commands_get", { id: s.id }).then((r) => setModCmds(r.mod)).catch(() => {});   // MODのコマンドも候補に出す
  renderQuick($("[data-quickrow]", body), s, send);
  renderPreflight($("[data-preflight]", body), s);
  const tp = $("[data-topublic]", body);
  if (tp) tp.onclick = () => { S.tab = "public"; viewServer($("#main")); };
  $("[data-tomem]", body).onclick = () => { S.tab = "settings"; viewServer($("#main")); };
  con.textContent = "";
  S.consoleNext = 0;
  renderPlayers(s.players, s);
  pollConsole(true);
}

function renderPlayers(players, s) {
  const el = $("[data-players]");
  if (!el) return;
  if (!players.length) { el.innerHTML = `<p class="muted small" style="margin:0">${s.status === "running" ? "まだ誰もいません" : "サーバーが止まっています"}</p>`; return; }
  el.innerHTML = players.map((p) => `<div class="player"><span class="face" aria-hidden="true"></span><span class="nm">${esc(p)}</span>
    ${cpBtn(p, "名前", "名前")}${cpIdBtn(p)}
    <select data-pact="${esc(p)}" aria-label="${esc(p)} への操作" style="width:auto;min-height:32px;padding:2px 8px">
      <option value="">操作…</option><option value="op">OP（管理者）にする</option><option value="deop">OPをやめさせる</option>
      <option value="wl">ホワイトリストに入れる</option><option value="gamemode creative">クリエイティブにする</option>
      <option value="gamemode survival">サバイバルにする</option><option value="kick">キックする</option>
      <option value="ban">BAN する（二度と入れない）</option><option value="banip">IPアドレスで BAN する</option></select></div>`).join("");
  $$("[data-pact]", el).forEach((sel) => (sel.onchange = async () => {
    const v = sel.value; const name = sel.dataset.pact; sel.value = "";
    if (!v) return;
    try {
      if (v === "kick") {
        if (!(await ask("キックする", `<p>${esc(name)} をサーバーから追い出しますか？（また入ることはできます）</p>`, "キックする", { danger: true }))) return;
        await api("server_command", { id: s.id, command: `kick ${name}` });
      } else if (v === "ban" || v === "banip") {
        if (!(await ask(v === "ban" ? "BAN する" : "IPアドレスで BAN する", `<p>${esc(name)} を${v === "ban" ? "" : "IPアドレスごと"} BAN しますか？二度とサーバーに入れなくなります（あとで「メンバー・BAN」タブで解除できます）。</p>`, "BAN する", { danger: true }))) return;
        await api("ban_add", { id: s.id, kind: v === "ban" ? "player" : "ip", target: name, reason: "" });
      } else if (v === "wl") {
        await api("member_add", { id: s.id, kind: "whitelist", name });
      } else {
        await api("server_command", { id: s.id, command: `${v} ${name}` });
      }
      toast("送りました");
    } catch (e) { fail(e); }
  }));
}

let lastPlayers = "";
async function pollConsole(reset) {
  const con = $("[data-console]");
  if (!con || S.view !== "server" || S.tab !== "home") return;
  try {
    const r = await api("console", { id: S.server, since: reset ? 0 : S.consoleNext });
    if (reset) con.textContent = "";
    const ph = con.querySelector(".faint");
    if (ph && r.lines.length) ph.remove();
    const atBottom = con.scrollTop + con.clientHeight >= con.scrollHeight - 30;
    if (r.lines.length) {
      const frag = document.createDocumentFragment();
      for (const line of r.lines) {
        const div = document.createElement("div");
        if (/ERROR|Exception|エラー|失敗/.test(line)) div.className = "e";
        else if (/WARN|⚠/.test(line)) div.className = "w";
        else if (/^(> |▶|■)/.test(line)) div.className = "me";
        else if (/^✅|Done \(/.test(line)) div.className = "ok";
        div.textContent = line;
        frag.appendChild(div);
      }
      con.appendChild(frag);
      while (con.childNodes.length > 2500) con.removeChild(con.firstChild);
      if (atBottom || reset) con.scrollTop = con.scrollHeight;
    } else if (reset) {
      con.innerHTML = `<div class="faint">（サーバーを起動すると、ここに記録が出ます）</div>`;
    }
    S.consoleNext = r.next;
    paintProgress(document, r.progress);
    paintLag(document, r.tps, r.status);
    const key = r.players.join(",") + r.status;
    if (key !== lastPlayers) { lastPlayers = key; renderPlayers(r.players, { ...cur(), status: r.status }); }
    const s = cur();
    if (s && s.status !== r.status) { s.status = r.status; s.players = r.players; refreshAll(); }
    const c = $("[data-crash]");
    const crashKey = r.crash && r.status === "stopped" ? String(r.crash.time) : "";
    if (s && c && c.dataset.key !== crashKey) {
      s.crash = r.crash;
      c.dataset.key = crashKey;
      c.innerHTML = crashCard({ ...s, status: r.status }, r.crash);
      bindCrash(c, s);
    }
  } catch (e) { /* 次の回に */ }
}

// ---------------- MOD ----------------
function tabMods(body, s) {
  const W = modWord(s);
  const subs = [["installed", `入っている${W}`], ["search", `${W}を探して入れる`], ["updates", "更新"]];
  body.innerHTML = `<div class="row" style="margin-bottom:16px;gap:6px">${subs.map(([k, l]) => `<button class="btn ${S.sub === k ? "primary" : ""}" data-sub="${k}">${l}</button>`).join("")}
    <button class="btn" data-presets title="目的別に、おすすめを まとめて入れられます">おすすめセット</button><span class="grow"></span><span class="small muted">マイクラ ${esc(s.mc)}・${esc(s.loader_label)} 用の${W}が使えます ${help(W === "MOD" ? "loader" : "plugin")}</span></div><div data-subbody></div>`;
  $$("[data-sub]", body).forEach((b) => (b.onclick = () => { S.sub = b.dataset.sub; tabMods(body, s); }));
  const pb = $("[data-presets]", body); if (pb) { if (s.loader === "vanilla") pb.style.display = "none"; else pb.onclick = () => openPresets(s.id); }
  const sb = $("[data-subbody]", body);
  if (s.loader === "vanilla") {
    sb.innerHTML = `<div class="note warn"><b>このサーバーは「バニラ（MODなし）」です</b>MODを使うには、Fabric などで新しいサーバーを作ってください。</div>`;
    return;
  }
  ({ installed: subInstalled, search: subSearch, updates: subUpdates }[S.sub] || subInstalled)(sb, s);
}

async function subInstalled(root, s) {
  root.innerHTML = `<div class="empty">読み込んでいます…</div>`;
  let r;
  try { r = await api("mods_list", { server: s.id }); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const sel = new Set();
  root.innerHTML = `
    <div class="row" style="margin-bottom:12px">
      <label class="check"><input type="checkbox" data-all> すべて選ぶ</label>
      <button class="btn sm" data-bulk="on" disabled>ON にする</button><button class="btn sm" data-bulk="off" disabled>OFF にする</button>
      <button class="btn sm danger" data-bulk="rm" disabled>外す</button>
      <span class="grow"></span>
      <label class="btn sm">ファイルから追加（.jar）<input type="file" accept=".jar" multiple hidden data-jar></label>
      <button class="btn sm" data-folder>mods フォルダを開く</button>
    </div>
    ${r.mods.length ? "" : `<div class="empty">まだMODが入っていません。<br><button class="btn primary" data-tosearch style="margin-top:10px">MODを探して入れる</button></div>`}
    <div class="list">${r.mods.map((m) => `<div class="item ${m.enabled ? "" : "off"}" data-path="${esc(m.path)}">
      <input type="checkbox" data-sel aria-label="${esc(m.title)} を選ぶ" style="width:20px;height:20px;accent-color:var(--grass)">
      ${slotImg(m.icon, m.title, "sm")}
      <div style="min-width:0"><div class="t">${esc(m.title)} <span class="small muted">${esc(m.version)}</span></div>
        <div class="s d ${m.ja || !m.desc ? "" : "en"}" ${m.ja || !m.desc ? "" : `data-text="${esc(m.desc)}"`}>${esc(m.ja || m.desc || (m.manual ? "手で追加したMOD（Modrinth の情報なし）" : ""))}</div>
        <div class="chips" style="margin-top:3px"><span class="badge">${esc(m.where)}</span>${m.manual ? '<span class="badge warn">手動</span>' : ""}${m.mark === "optional" ? '<span class="badge ok">あると便利</span>' : (m.where === "サーバー ＋ 遊ぶ人" ? '<span class="badge lib">友達も必須</span>' : "")}</div></div>
      <div class="acts">
        ${m.can_optional && m.where !== "遊ぶ人だけ" ? `<select data-mark title="友達用パックでの扱い" style="width:auto;min-height:30px;padding:2px 6px"><option value="">友達も必須</option><option value="optional" ${m.mark === "optional" ? "selected" : ""}>あると便利（なくてもOK）</option></select>` : ""}
        ${m.id ? `<button class="btn sm ghost" data-detail="${esc(m.id)}">詳しく</button>` : ""}
        ${m.url ? `<button class="btn sm ghost" data-url="${esc(m.url)}">サイト</button>` : ""}
        <label class="switch" title="ON / OFF"><input type="checkbox" data-en ${m.enabled ? "checked" : ""} aria-label="${esc(m.title)} を使う"><span></span></label>
      </div></div>`).join("")}</div>
    <p class="small muted" style="margin-top:12px">スイッチを OFF にすると、外さずに一時的に使わないようにできます。MODを変えたら、サーバーの再起動と友達用パックの作り直しが必要です。</p>`;
  autoTranslate(root);
  const upd = () => $$("[data-bulk]", root).forEach((b) => (b.disabled = !sel.size));
  $$(".item", root).forEach((it) => {
    const p = it.dataset.path;
    $("[data-sel]", it).onchange = (e) => { e.target.checked ? sel.add(p) : sel.delete(p); upd(); };
    $("[data-en]", it).onchange = async (e) => {
      try { await api("mods_enable", { server: s.id, paths: [p], enabled: e.target.checked }); it.classList.toggle("off", !e.target.checked); } catch (err) { fail(err); }
    };
    const dt = $("[data-detail]", it);
    if (dt) dt.onclick = () => openDetail(dt.dataset.detail, { server: s.id });
    const mk = $("[data-mark]", it);
    if (mk) mk.onchange = async () => {
      try { await api("mod_mark", { server: s.id, path: p, mark: mk.value }); toast(mk.value ? "「あると便利」にしました（友達用パックを作り直すと反映されます）" : "「友達も必須」にしました（友達用パックを作り直すと反映されます）"); subInstalled(root, s); } catch (err) { fail(err); mk.value = mk.value ? "" : "optional"; }
    };
  });
  $("[data-all]", root).onchange = (e) => { $$("[data-sel]", root).forEach((c) => { c.checked = e.target.checked; c.dispatchEvent(new Event("change")); }); };
  $$("[data-bulk]", root).forEach((b) => (b.onclick = async () => {
    const paths = [...sel];
    try {
      if (b.dataset.bulk === "rm") {
        let pv = { items: [] };
        try { pv = await api("mods_remove_preview", { server: s.id, paths }); } catch (e) { /* 候補なしで続ける */ }
        const cfg = pv.items.flatMap((x) => x.configs.map((c) => ({ ...c, title: x.title })));
        const W = modWord(s);
        const ok = await ask(`${W}を外す`, `<p>${paths.length}個の${W}を外して、ファイルを消しますか？</p>
          ${cfg.length ? `<div class="panel" style="margin-top:10px"><b>いっしょに消せる設定ファイル</b><p class="small muted" style="margin:2px 0 8px">チェックしたものも消します（また入れたときは、最初の設定に戻ります）。</p>
            ${cfg.map((c) => `<label class="check small" style="display:flex;margin:4px 0"><input type="checkbox" data-cfg value="${esc(c.path)}" checked> ${c.dir ? "📁" : "📄"} ${esc(c.path)} <span class="faint">（${esc(c.title)}・${c.kb}KB）</span></label>`).join("")}</div>`
            : `<p class="small muted">この${W}の設定ファイルは見つかりませんでした。</p>`}`, "外す", { danger: true });
        if (!ok) return;
        const configs = ok.picked || [];
        const r = await api("mods_remove", { server: s.id, paths, configs });
        if (r.configs_removed && r.configs_removed.length) toast(`設定ファイルも ${r.configs_removed.length}個 消しました`);
      } else await api("mods_enable", { server: s.id, paths, enabled: b.dataset.bulk === "on" });
      toast("変更しました");
      await refreshState();
      subInstalled(root, cur());
    } catch (e) { fail(e); }
  }));
  $("[data-folder]", root).onclick = () => api("open_folder", { id: s.id, which: "mods" }).catch(fail);
  $("[data-jar]", root).onchange = async (e) => {
    for (const f of e.target.files) {
      try { const u = await upload(f); await api("mods_upload_jar", { server: s.id, path: u.path, filename: f.name }); } catch (err) { fail(err); }
    }
    toast("追加しました");
    subInstalled(root, s);
  };
  const ts = $("[data-tosearch]", root);
  if (ts) ts.onclick = () => { S.sub = "search"; tabMods($("[data-body]"), s); };
}

function subSearch(root, s) {
  const picked = new Map();
  root.innerHTML = `<p class="muted" style="margin-top:0">カードをクリックすると、日本語の説明・画像・使えるバージョンが見られます。右上の □ で選んで、まとめて入れられます。前提${modWord(s)}は自動で入ります。</p>
    <div data-browser></div>
    <div class="tray"><div class="names" data-names>${modWord(s)}を選んでください</div><button class="btn" data-clear>選択をやめる</button><button class="btn primary big" data-inst disabled>選んだ${modWord(s)}を入れる</button></div>`;
  const upd = () => {
    const n = picked.size;
    $("[data-names]", root).innerHTML = n ? `<b>${n}個</b> 選んでいます: ${[...picked.values()].slice(0, 8).map(esc).join("、")}${n > 8 ? " …" : ""}` : `${modWord(s)}を選んでください`;
    $("[data-inst]", root).disabled = !n;
    $("[data-inst]", root).textContent = n ? `選んだ ${n}個の${modWord(s)}を入れる` : `選んだ${modWord(s)}を入れる`;
  };
  const br = modBrowser($("[data-browser]", root), { server: s.id, picked, onChange: upd, installMode: { after: () => br.reload() } });
  $("[data-clear]", root).onclick = () => { const ids = [...picked.keys()]; picked.clear(); ids.forEach((id) => br.refreshCard(id)); upd(); };
  $("[data-inst]", root).onclick = async () => {
    const ids = [...picked.keys()];
    const r = await installMods(s.id, ids);
    if (r) { picked.clear(); upd(); br.reload(); }
  };
}

async function subUpdates(root, s) {
  root.innerHTML = `<p class="muted" style="margin-top:0;max-width:76ch">入っているMODの新しいバージョンをまとめて調べて、まとめて更新します。更新する前の状態は自動で取っておくので、調子が悪くなったら<b>「元に戻す」</b>で戻せます。</p>
    <div class="row"><button class="btn primary" data-chk>更新を探す</button><span data-snap></span></div><div data-res style="margin-top:16px"></div>`;
  const paintSnap = async () => {
    const el = $("[data-snap]", root);
    if (!el) return;
    try {
      const si = await api("mods_snapshot_info", { server: s.id });
      el.innerHTML = si.exists ? `<button class="btn" data-rb title="${esc(si.titles.join("、"))}">↩ 前の更新を元に戻す（${new Date(si.time * 1000).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} の ${si.titles.length}個）</button>` : "";
      const rb = $("[data-rb]", el);
      if (rb) rb.onclick = async () => {
        if (!(await ask("更新を元に戻す", `<p>前にまとめて更新した ${si.titles.length}個のMODを、更新する前のバージョンに戻しますか？</p><p class="small muted">${esc(si.titles.slice(0, 12).join("、"))}${si.titles.length > 12 ? " ほか" : ""}</p>`, "元に戻す"))) return;
        try { await api("mods_rollback", { server: s.id }); toast("元に戻しました"); subUpdates(root, s); } catch (e) { fail(e); }
      };
    } catch (e) { el.innerHTML = ""; }
  };
  paintSnap();
  $("[data-chk]", root).onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "探しています…";
    try {
      const r = await api("mods_updates", { server: s.id });
      const res = $("[data-res]", root);
      if (!r.updates.length) { res.innerHTML = `<div class="note ok"><b>すべて最新です</b></div>`; return; }
      res.innerHTML = `<div class="row" style="margin-bottom:8px"><b class="grow">新しいバージョンがあるMOD: ${r.updates.length}個</b>
          <button class="btn sm ghost" data-all>すべて選ぶ</button><button class="btn sm ghost" data-none>すべて外す</button></div>
        <div class="list">${r.updates.map((u) => `<label class="item"><input type="checkbox" checked value="${esc(u.sha1)}" style="width:20px;height:20px;accent-color:var(--grass)">${slotImg(u.icon, u.title, "sm")}
        <div><div class="t">${esc(u.title)}</div><div class="s">${esc(u.old || "?")} → <b style="color:var(--bone)">${esc(u.new || "?")}</b></div></div><span></span></label>`).join("")}</div>
        <div class="row" style="margin-top:14px"><button class="btn primary big" data-apply>選んだMODをまとめて更新する</button><span class="small muted" data-cnt></span></div>`;
      const cnt = () => { $("[data-cnt]", res).textContent = `${$$("input:checked", res).length}個 選んでいます`; };
      $$("input[type=checkbox]", res).forEach((c) => (c.onchange = cnt));
      $("[data-all]", res).onclick = () => { $$("input[type=checkbox]", res).forEach((c) => (c.checked = true)); cnt(); };
      $("[data-none]", res).onclick = () => { $$("input[type=checkbox]", res).forEach((c) => (c.checked = false)); cnt(); };
      cnt();
      $("[data-apply]", res).onclick = async () => {
        const sha1s = $$("input:checked", res).map((c) => c.value);
        if (!sha1s.length) { toast("更新するMODを選んでください", "warn"); return; }
        try {
          const out = await runJob(api("mods_apply_updates", { server: s.id, sha1s }), "MODをまとめて更新しています", { note: "更新する前の状態は、自動で取っておきます。" });
          const fails = (out.failed || []).map((f) => `<li>${esc(f.title)}<span class="muted small">（${esc(f.error)}）</span></li>`).join("");
          const iss = (out.issues || []).map((i) => `<li>${esc(i.title)}</li>`).join("");
          const go = await ask("更新しました", `<p><b>${out.updated}個</b>のMODを更新しました。</p>
            ${fails ? `<div class="note warn"><b>更新できなかったMOD</b><ul>${fails}</ul></div>` : ""}
            ${iss ? `<div class="note error"><b>相性の問題が見つかりました</b><ul>${iss}</ul>「相性チェック」で確認してください。</div>` : ""}
            <p>このあと「起動テスト」をすると、ちゃんと動くか確かめられます。動かなかったら、この画面の「前の更新を元に戻す」で戻せます。</p>
            <p class="small muted">友達用パックは、約30秒後に自動で作り直されます（友達のソフトに「更新があります」と出ます）。</p>`, "起動テストへ進む", { cancelText: "あとで" });
          if (go) { S.tab = "check"; renderMain(); return; }
          subUpdates(root, s);
        } catch (err) { if (!err.shown) fail(err); }
      };
    } catch (err) { fail(err); } finally { e.target.disabled = false; e.target.textContent = "もう一度探す"; }
  };
}

// ---------------- 相性チェック ----------------
function issueHtml(i, sid) {
  const ico = { error: "⛔", warn: "⚠️", info: "💡" }[i.level] || "💡";
  let act = "";
  if (i.action && i.action.type === "disable") act = `<button class="btn sm" data-act="disable" data-path="${esc(i.action.path)}">OFFにする</button>`;
  if (i.action && i.action.type === "install_dep") act = `<button class="btn sm primary" data-act="dep" data-dep="${esc(i.action.id)}">前提MODを入れる</button> <button class="btn sm ghost" data-act="ignore" data-dep="${esc(i.action.id)}" title="間違いの警告のとき、今後は出さないようにします">この警告は出さない</button>`;
  if (i.action && i.action.type === "kill_java") act = `<button class="btn sm danger" data-act="killjava">残っているjavaを確認する</button>`;
  return `<div class="issue ${i.level}"><div class="ico" aria-hidden="true">${ico}</div><div>
    <div class="tt">${esc(i.title)}</div><div class="small">${esc(i.detail || "")}</div>${i.fix ? `<div class="fx">どうする？ ${esc(i.fix)}</div>` : ""}</div>
    <div>${act}</div></div>`;
}

function bindIssueActions(root, s, after) {
  $$("[data-act]", root).forEach((b) => (b.onclick = async () => {
    try {
      if (b.dataset.act === "killjava") {
        const ok = await killJavaFlow(s.id, null, {});
        if (ok) { toast("終了しました。もう一度、起動してみてください。"); if (after) after(); }
        return;
      }
      if (b.dataset.act === "disable") {
        await api("mods_enable", { server: s.id, paths: [b.dataset.path], enabled: false });
        toast("OFFにしました。もう一度起動してみてください。");
        b.disabled = true; b.textContent = "OFFにしました";
      } else if (b.dataset.act === "ignore") {
        if (!(await ask("この警告を出さなくします", `<p>「${esc(b.dataset.dep)}」が足りない、という警告を、このサーバーでは<b>出さない</b>ようにします。</p><p class="small muted">間違った警告のときだけにしてください。本当に足りないと、サーバーが止まります。</p>`, "出さない"))) return;
        await api("ignore_dep", { server: s.id, id: b.dataset.dep });
        toast("この警告は出さなくしました");
        b.disabled = true; b.textContent = "出さなくしました";
      } else {
        await runJob(api("install_dep", { server: s.id, id: b.dataset.dep }), "前提MODを入れています");
        toast("前提MODを入れました");
        b.disabled = true; b.textContent = "入れました";
      }
      refreshState();
      if (after) after();
    } catch (e) { if (!e.shown) fail(e); }
  }));
}

async function bootTest(s, out) {
  try {
    let st = await api("boot_test", { id: s.id });
    if (st.need_eula) {
      const ok = await ask("利用規約（EULA）への同意", `<p>テストでもサーバーを起動するので、マイクラの利用規約（EULA）への同意が必要です。${help("eula")}</p>
        <p><button class="linkbtn" data-url="https://aka.ms/MinecraftEULA">利用規約を読む</button></p>`, "同意してテスト");
      if (!ok) return;
      st = await api("boot_test", { id: s.id, accept_eula: true });
    }
    const r = await runJob(Promise.resolve(st), "起動テストをしています", { cancellable: true, note: "テスト用のワールドで起動しています。本物のワールドには触りません。" });
    if (r.cancelled) { out.innerHTML = `<div class="note" style="margin-top:14px"><b>テストを止めました</b></div>`; return; }
    if (r.ok) {
      out.innerHTML = `<div class="note ok" style="margin-top:14px"><b>✔ 起動できました（${r.seconds}秒）</b>今のMODで問題なく起動します。友達を呼んでも大丈夫です。</div>`;
      return;
    }
    const issues = [...(r.issues || []), ...(r.check || []).filter((i) => i.level === "error")];
    out.innerHTML = `<div class="note error" style="margin-top:14px"><b>✖ 起動できませんでした${r.reason ? "（" + esc(r.reason) + "）" : ""}</b>考えられる原因です。直してから、もう一度テストしてください。</div>
      <div class="stack" style="margin-top:12px">${issues.length ? issues.map((i) => issueHtml(i, s.id)).join("") : `<p class="muted">ログからは、はっきりした原因が見つかりませんでした。</p>`}</div>
      <div class="panel" style="margin-top:14px"><h3>原因のMODを自動で探す</h3>
        <p class="small muted">MODを半分ずつ一時的にOFFにして起動を試し、原因を1つにしぼります。何回か起動するので時間がかかります（目安 ${Math.max(3, Math.round((r.seconds || 60) * 8 / 60))}分くらい）。終わったら（途中で止めても）MODは全部元に戻します。</p>
        <button class="btn primary" data-culprit>原因のMODを探す</button><div data-culres></div></div>`;
    bindIssueActions(out, s);
    $("[data-culprit]", out).onclick = () => findCulprit(s, $("[data-culres]", out));
  } catch (e) { if (!e.shown) fail(e); }
}

async function findCulprit(s, out) {
  try {
    const r = await runJob(api("find_culprit", { id: s.id }), "原因のMODを探しています", { cancellable: true, note: "何回か起動を試します。終わるまで待ってください。" });
    if (r.found) {
      out.innerHTML = `<div class="note error" style="margin-top:12px"><b>原因は「${esc(r.name)}」です</b>
        ${r.also && r.also.length ? `<p>このMODを必要としている ${r.also.map(esc).join("、")} も、一緒にOFFにする必要があります。</p>` : ""}
        <p>OFFにすると起動できることを確かめました。新しいバージョンがあれば「更新」で直ることもあります。</p>
        <button class="btn" data-off>OFFにする${r.also && r.also.length ? `（${r.paths.length}個）` : ""}</button></div>`;
      $("[data-off]", out).onclick = async (e) => {
        try { await api("mods_enable", { server: s.id, paths: r.paths, enabled: false }); e.target.disabled = true; e.target.textContent = "OFFにしました"; toast("OFFにしました"); refreshState(); } catch (err) { fail(err); }
      };
    } else {
      out.innerHTML = `<div class="note warn" style="margin-top:12px"><b>${esc(r.message)}</b></div>`;
    }
  } catch (e) { if (!e.shown) fail(e); }
}

function tabCheck(body, s) {
  body.innerHTML = `<div class="panel" style="margin-bottom:22px;border-top:3px solid var(--grass)">
      <div class="row top"><div class="grow"><h2>起動テスト</h2>
        <p class="muted" style="max-width:70ch">友達を呼ぶ前に、今のMODでサーバーがちゃんと起動するかを試します。テスト用のワールドを使うので、<b style="color:var(--bone)">本物のワールドには触りません</b>。起動できたらすぐ止めて、テスト用のワールドは消します。</p>
        <p class="small muted">MODが多いと 1〜5分かかります。${s.status !== "stopped" ? '<b style="color:var(--gold)">サーバーが動いている間はテストできません。先に止めてください。</b>' : ""}</p></div>
        <button class="btn go big" data-boot ${s.status !== "stopped" ? "disabled" : ""}>起動テストをする</button></div>
      <div data-bootres></div></div>
    <div class="grid2">
    <div><h3>相性チェック ${help("conflict")}</h3>
      <p class="muted">入っているMODの中身を読んで、いっしょに使うと問題が起きる組み合わせを探します。サーバーを起動する前にやると安心です。</p>
      <ul class="small muted" style="padding-left:1.3em;margin-top:0"><li>種類（Fabric / Forge / NeoForge）がサーバーと合っているか</li><li>同じMODが2つ入っていないか</li><li>前提MODが足りているか</li><li>「一緒に使えない」組み合わせ・同じ役割のMODがないか</li><li>遊ぶ人専用のMODがサーバーに入っていないか</li><li>マイクラのバージョンが合っているか</li></ul>
      <button class="btn primary big" data-run>相性チェックをする</button></div>
    <div><h3>止まった原因を調べる</h3>
      <p class="muted">サーバーが起動しない・急に止まったときに、ログ（記録）から原因を探して日本語で説明します。</p>
      <button class="btn big" data-log>原因を調べる</button>
      <div class="row" style="margin-top:10px"><button class="btn sm ghost" data-folder="logs">ログのフォルダ</button><button class="btn sm ghost" data-folder="crash">クラッシュレポートのフォルダ</button></div></div>
  </div><div data-res style="margin-top:22px"></div>`;
  const bootres = $("[data-bootres]", body);
  $("[data-boot]", body).onclick = () => bootTest(s, bootres);
  if (S.autoBoot) { S.autoBoot = false; if (s.status === "stopped") setTimeout(() => bootTest(s, bootres), 300); }
  const res = $("[data-res]", body);
  const show = (issues, okMsg) => {
    res.innerHTML = issues.length ? `<div class="stack">${issues.map((i) => issueHtml(i, s.id)).join("")}</div>`
      : `<div class="note ok"><b>問題は見つかりませんでした</b>${esc(okMsg)}</div>`;
    bindIssueActions(res, s);
  };
  $("[data-run]", body).onclick = async (e) => {
    e.target.disabled = true;
    res.innerHTML = `<div class="empty">調べています…</div>`;
    try {
      const r = await api("check_conflicts", { server: s.id });
      const errs = r.issues.filter((i) => i.level === "error").length;
      res.innerHTML = "";
      show(r.issues, `${r.checked}個のMODを調べました。`);
      if (r.issues.length) res.insertAdjacentHTML("afterbegin", `<p class="muted">${r.checked}個のMODを調べました。${errs ? `<b style="color:var(--ng-text)">直したほうがいいもの ${errs}件</b>` : "大きな問題はありません。"}</p>`);
    } catch (err) { fail(err); res.innerHTML = ""; } finally { e.target.disabled = false; }
  };
  $("[data-log]", body).onclick = async (e) => {
    e.target.disabled = true;
    try { const r = await api("analyze_logs", { server: s.id }); show(r.issues, "ログに分かりやすいエラーはありませんでした。"); } catch (err) { fail(err); } finally { e.target.disabled = false; }
  };
  $$("[data-folder]", body).forEach((b) => (b.onclick = () => api("open_folder", { id: s.id, which: b.dataset.folder }).catch(fail)));
}

// ---------------- 重さ・事前生成 ----------------
const PERF_CHARTS = [
  { key: "tps", title: "TPS（1秒の処理回数）", min: 0, max: 20, unit: "", fmt: (v) => v.toFixed(1), ref: 20, refLabel: "目標 20" },
  { key: "mem", title: "メモリ", min: 0, unit: "GB", fmt: (v) => (v >= 1024 ? (v / 1024).toFixed(1) + "GB" : Math.round(v) + "MB") },
  { key: "cpu", title: "CPU", min: 0, max: 100, unit: "%", fmt: (v) => Math.round(v) + "%" },
  { key: "players", title: "参加人数", min: 0, unit: "人", fmt: (v) => v + "人", step: true },
];

function tpsLevel(t) {
  if (t == null) return ["", "—", ""];
  if (t >= 19) return ["ok", "快適", "ほぼ遅れなく動いています"];
  if (t >= 15) return ["warn", "少し重い", "動きが少しおそくなっています"];
  return ["ng", "重い", "動きがカクカクしています"];
}

function lineChart(el, samples, cfg, alloc) {
  const W = Math.max(260, el.clientWidth || 300), H = 150, L = 46, R = 10, T = 12, B = 24;
  const pts = samples.map((x) => ({ t: x.t, v: x[cfg.key] }));
  const now = samples.length ? samples[samples.length - 1].t : Date.now() / 1000;
  // 表示する長さ：データがある分だけ（2分〜30分）
  const span = Math.min(30 * 60, Math.max(120, samples.length ? now - samples[0].t : 120));
  const t0 = now - span;
  let max = cfg.max;
  if (cfg.key === "mem") max = Math.max(alloc * 1.25, ...pts.map((p) => p.v || 0)) ;
  if (cfg.key === "players") max = Math.max(4, ...pts.map((p) => p.v || 0));
  const x = (t) => L + ((t - t0) / (now - t0 || 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - cfg.min) / (max - cfg.min || 1)) * (H - T - B);
  const vis = pts.filter((p) => p.t >= t0);
  let path = "", pen = false;
  for (const p of vis) {
    if (p.v == null) { pen = false; continue; }
    path += (pen ? "L" : "M") + x(p.t).toFixed(1) + "," + y(p.v).toFixed(1);
    pen = true;
  }
  const ticks = [cfg.min, (cfg.min + max) / 2, max];
  const grid = ticks.map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="g"/><text x="${L - 6}" y="${y(v) + 4}" class="ax" text-anchor="end">${esc(cfg.key === "mem" ? (v / 1024).toFixed(0) : Math.round(v))}</text>`).join("");
  const agoLabel = (sec) => (sec >= 60 ? Math.round(sec / 60) + "分前" : Math.round(sec) + "秒前");
  const xt = [1, 2 / 3, 1 / 3, 0].map((f) => `<text x="${x(now - f * span)}" y="${H - 6}" class="ax" text-anchor="${f === 0 ? "end" : f === 1 ? "start" : "middle"}">${f ? agoLabel(f * span) : "今"}</text>`).join("");
  const ref = cfg.ref != null ? `<line x1="${L}" x2="${W - R}" y1="${y(cfg.ref)}" y2="${y(cfg.ref)}" class="ref"/>` : "";
  const memRef = cfg.key === "mem" && alloc ? `<line x1="${L}" x2="${W - R}" y1="${y(alloc)}" y2="${y(alloc)}" class="ref"/><text x="${W - R}" y="${y(alloc) - 4}" class="ax" text-anchor="end">割り当て ${(alloc / 1024).toFixed(1)}GB</text>` : "";
  const last = [...vis].reverse().find((p) => p.v != null);
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(cfg.title)}のグラフ（${esc(agoLabel(span))}から今まで）">${grid}${ref}${memRef}${xt}
    ${path ? `<path d="${path}" class="ln"/>` : `<text x="${(L + W) / 2}" y="${H / 2}" class="ax" text-anchor="middle">${cfg.key === "tps" ? "はかっています…" : "データがまだありません"}</text>`}
    ${last ? `<circle cx="${x(last.t)}" cy="${y(last.v)}" r="4" class="pt"/>` : ""}
    <line class="cross" y1="${T}" y2="${H - B}" x1="0" x2="0" visibility="hidden"/><circle class="hov" r="5" visibility="hidden"/>
    <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" class="hit"/></svg><div class="tip" hidden></div>`;
  const svg = $("svg", el), tip = $(".tip", el), cross = $(".cross", svg), hov = $(".hov", svg);
  $(".hit", svg).addEventListener("mousemove", (e) => {
    const r = svg.getBoundingClientRect();
    const mx = ((e.clientX - r.left) / r.width) * W;
    const t = t0 + ((mx - L) / (W - L - R)) * (now - t0);
    let best = null;
    for (const p of vis) if (p.v != null && (!best || Math.abs(p.t - t) < Math.abs(best.t - t))) best = p;
    if (!best) return;
    cross.setAttribute("x1", x(best.t)); cross.setAttribute("x2", x(best.t)); cross.setAttribute("visibility", "visible");
    hov.setAttribute("cx", x(best.t)); hov.setAttribute("cy", y(best.v)); hov.setAttribute("visibility", "visible");
    const ago = now - best.t;
    tip.innerHTML = `<b>${esc(cfg.fmt(best.v))}</b><span>${ago >= 5 ? agoLabel(ago) : "今"}</span>`;
    tip.hidden = false;
    tip.style.left = Math.min(r.width - 110, Math.max(0, (x(best.t) / W) * r.width - 50)) + "px";
  });
  $(".hit", svg).addEventListener("mouseleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); hov.setAttribute("visibility", "hidden"); });
}

async function tabPerf(body, s) {
  body.innerHTML = `<div data-perf><div class="empty">読み込んでいます…</div></div><hr class="sep"><div data-opt></div><hr class="sep"><div data-lag></div><hr class="sep"><div data-pregen></div>`;
  await renderPerf($("[data-perf]", body), s);
  renderOptimize($("[data-opt]", body), s);
  renderLag($("[data-lag]", body), s);
  renderPregen($("[data-pregen]", body), s);
}

async function renderPerf(el, s) {
  let d;
  try { d = await api("perf", { id: s.id }); } catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (!el.isConnected) return;
  const [cls, lab, desc] = tpsLevel(d.tps);
  const tile = (title, value, sub, h = "") => `<div class="stat"><div class="small muted">${title} ${h}</div><div class="v">${value}</div><div class="small">${sub}</div></div>`;
  el.innerHTML = `<div class="row" style="margin-bottom:6px"><h2 class="grow">重さ</h2><span class="small muted">5秒ごとに記録・最大30分ぶん表示</span></div>
    ${!d.running ? `<div class="note" style="margin-bottom:14px"><b>サーバーが止まっています</b>起動すると、重さをはかり始めます。</div>` : ""}
    <div class="stats">
      ${tile("TPS（処理の速さ）", d.tps == null ? "—" : `<span class="lv ${cls}">${d.tps}</span>`, d.tps == null ? (d.probe_supported || !d.running ? "はかっています" : "このバージョンでははかれません") : `<b class="lv ${cls}">${lab}</b>　${desc}`, help("tps"))}
      ${tile("メモリ", d.mem ? PERF_CHARTS[1].fmt(d.mem) : "—", `割り当て ${(d.alloc / 1024).toFixed(1)}GB`, help("memory"))}
      ${tile("CPU", d.cpu == null ? "—" : d.cpu + "%", "PCの頭脳がどれだけ忙しいか")}
      ${tile("処理の遅れ", d.lags10 + "回", "この10分で「追いつかない」と出た回数")}
    </div>
    <div class="charts">${PERF_CHARTS.map((c) => `<div class="chart"><div class="ct">${c.title}</div><div class="cbox" data-chart="${c.key}"></div></div>`).join("")}</div>
    <h3 style="margin-top:18px">アドバイス</h3>
    <div class="stack" style="margin-top:8px">${d.advice.length ? d.advice.map((a, i) => `<div class="issue ${a.level === "error" ? "error" : a.level === "warn" ? "warn" : ""}">
        <div class="ico" aria-hidden="true">${{ error: "⛔", warn: "⚠️", ok: "✅" }[a.level] || "💡"}</div><div><div class="tt">${esc(a.title)}</div><div class="small">${esc(a.text)}</div></div>
        <div>${a.action ? `<button class="btn sm ${a.action.type === "install" ? "primary" : ""}" data-adv="${i}">${a.action.type === "install" ? "まとめて入れる" : "設定を開く"}</button>` : ""}</div></div>`).join("")
      : `<p class="muted">${d.running ? "データを集めています…" : "サーバーを起動すると表示されます。"}</p>`}</div>
    <details style="margin-top:14px"><summary class="small muted" style="cursor:pointer">TPS とは？（詳しく）</summary>
      <p class="small" style="margin-top:8px">マイクラのサーバーは、1秒に20回「世界を1コマ進める」計算をしています。この回数が TPS です。20 なら快適、15 を下回ると、ブロックや生き物の動きがおそく・カクカクになります。重くなる主な原因は、MODの多さ、見える距離・動く範囲の広さ、たくさんの機械や生き物、新しい地形の生成です。</p></details>`;
  for (const c of PERF_CHARTS) lineChart($(`[data-chart="${c.key}"]`, el), d.samples, c, d.alloc);
  $$("[data-adv]", el).forEach((b) => (b.onclick = async () => {
    const a = d.advice[+b.dataset.adv].action;
    if (a.type === "goto") { S.tab = a.tab; viewServer($("#main")); }
    else if (a.type === "install") { const r = await installMods(s.id, a.ids); if (r) renderPerf(el, s); }
  }));
}

// ---------------- ワンクリック最適化 ----------------
async function renderOptimize(el, s) {
  let d;
  try { d = await api("optimize_plan", { id: s.id }); } catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (!el.isConnected) return;
  const real = d.items.filter((it) => it.kind !== "info");
  el.innerHTML = `<div class="row"><h2 class="grow">ワンクリック最適化</h2>${d.undo_at ? `<button class="btn sm ghost" data-undo>前回（${esc(d.undo_at)}）の変更を元に戻す</button>` : ""}</div>
    <p class="muted" style="max-width:76ch">あなたのPC（メモリ ${gb(d.ram_mb)}・CPU ${d.cpus}コア）と、MOD ${d.mods}個に合わせて、重くなりやすい設定をちょうどいい値にします。チェックを外したものは変えません。</p>
    ${real.length ? `<div class="list" data-opts>${d.items.map((it, i) => it.kind === "info"
      ? `<div class="item noselect"><span class="slot sm"><span class="letter">i</span></span><div><div class="t">${esc(it.label)}</div><div class="s">${esc(it.why)}</div></div><span></span></div>`
      : `<label class="item"><input type="checkbox" value="${esc(it.id)}" ${it.risky ? "" : "checked"} style="width:20px;height:20px;accent-color:var(--grass)">
        <span class="slot sm"><span class="letter">${it.kind === "memory" ? "M" : it.kind === "yaml" ? "P" : "S"}</span></span>
        <div><div class="t">${esc(it.label)}　<span class="small muted">${esc(it.current)} → </span><b style="color:var(--ok-text)">${esc(it.new)}</b>${it.risky ? ' <span class="badge warn">注意</span>' : ""}</div>
          <div class="s" style="-webkit-line-clamp:4">${esc(it.why)}</div><div class="small faint">${esc(it.file)}</div></div><span></span></label>`).join("")}</div>
      <div class="row" style="margin-top:12px"><button class="btn primary" data-apply>チェックしたものを変える</button><span class="small muted">変える前の値は記録しておくので、あとで元に戻せます。</span></div>`
      : `<div class="note ok"><b>もう最適な設定です</b>今のPCとMODの数なら、変えたほうがいい設定は見つかりませんでした。</div>
        ${d.items.map((it) => `<div class="note" style="margin-top:8px"><b>${esc(it.label)}</b>${esc(it.why)}</div>`).join("")}`}
    ${d.perf_mods.length ? `<div class="issue" style="margin-top:14px"><div class="ico" aria-hidden="true">🚀</div><div><div class="tt">軽量化${modWord(s)}も入れる</div><div class="small">${d.perf_mods.map((m) => `${esc(m.title)}（${esc(m.desc)}）`).join("、")}</div></div>
      <div><button class="btn sm primary" data-perfmods>まとめて入れる</button></div></div>` : ""}
    <p class="small faint" style="margin-top:10px">Java の設定: ${esc(d.jvm)}。メモリの量に合わせて自動で切り替えます。</p>`;
  const ap = $("[data-apply]", el);
  if (ap) ap.onclick = async () => {
    const ids = $$("[data-opts] input:checked", el).map((x) => x.value);
    if (!ids.length) { toast("変える項目を選んでください", "warn"); return; }
    try {
      const r = await api("optimize_apply", { id: s.id, ids });
      toast(`${r.done.length}個の設定を変えました`);
      if (r.restart) info("最適化しました", "<p>いくつかの設定は、サーバーを<b>再起動</b>すると効きます。友達がいない時間に再起動してください。</p>");
      renderOptimize(el, s);
    } catch (e) { fail(e); }
  };
  const un = $("[data-undo]", el);
  if (un) un.onclick = async () => {
    if (!(await ask("元に戻す", "<p>前回の最適化で変えた設定を、変える前の値に戻しますか？</p>", "元に戻す"))) return;
    try { const r = await api("optimize_undo", { id: s.id }); toast(`${r.restored}個の設定を元に戻しました`); renderOptimize(el, s); } catch (e) { fail(e); }
  };
  const pm = $("[data-perfmods]", el);
  if (pm) pm.onclick = async () => { const r = await installMods(s.id, d.perf_mods.map((m) => m.id)); if (r) renderOptimize(el, s); };
}

// ---------------- 重さの原因探し・お掃除 ----------------
async function renderLag(el, s, result) {
  let info_;
  try { info_ = await api("server_info", { id: s.id }); } catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (!el.isConnected) return;
  const running = info_.status === "running";
  const rows = result ? result.rows : [];
  const clearable = rows.filter((r) => r.clearable);
  el.innerHTML = `<h2>重さの原因探し・お掃除</h2>
    <p class="muted" style="max-width:76ch">落ちているアイテム・経験値・生き物がどこに、どれだけたまっているかを数えます。たまりすぎは、重さ（TPSが下がる）の一番多い原因です。</p>
    <div class="row"><button class="btn primary" data-scan ${running ? "" : "disabled"}>今のようすを数える</button>${running ? "" : '<span class="small muted">サーバーを起動すると使えます</span>'}</div>
    ${result ? `<div style="margin-top:14px">
      ${result.advice.length ? result.advice.map((a) => `<div class="note warn" style="margin-bottom:8px">${esc(a)}</div>`).join("") : `<div class="note ok"><b>たまりすぎは見つかりませんでした</b>全部で ${fmtNum(result.total)}個（${esc(result.at)} 時点）。</div>`}
      <div class="grid2" style="margin-top:12px">
        <div class="panel"><h3>多いもの</h3>${rows.length ? `<table class="tbl"><thead><tr><th>場所</th><th>種類</th><th>数</th></tr></thead><tbody>${rows.slice(0, 15).map((r) => `<tr><td>${esc(r.dim_label)}</td><td>${esc(r.label)}</td><td><b>${fmtNum(r.count)}</b></td></tr>`).join("")}</tbody></table>` : '<p class="muted">ほとんどありません</p>'}</div>
        <div class="panel"><h3>プレイヤーのまわり（64ブロック以内）</h3>${result.near.length ? `<table class="tbl"><thead><tr><th>プレイヤー</th><th>生き物・物</th><th>アイテム</th></tr></thead><tbody>${result.near.map((n) => `<tr><td>${esc(n.player)}</td><td><b>${fmtNum(n.count)}</b></td><td>${fmtNum(n.items)}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">誰もいません</p>'}</div>
      </div></div>` : ""}
    <div class="panel" style="margin-top:14px"><h3>お掃除</h3>
      <p class="small muted">消すのは「落ちているアイテム・経験値・矢」だけです。生き物や、チェストの中身は消えません。</p>
      <div class="row" style="margin-top:6px">${Object.entries({ item: "落ちているアイテム", experience_orb: "経験値", arrow: "矢" }).map(([k, l]) => `<label class="check"><input type="checkbox" data-ct value="${k}" ${k === "experience_orb" ? "" : "checked"}> ${l}${clearable.find((r) => r.type === k) ? `（${fmtNum(clearable.filter((r) => r.type === k).reduce((a, r) => a + r.count, 0))}個）` : ""}</label>`).join("")}</div>
      <div class="row" style="margin-top:10px"><button class="btn danger" data-clear ${running ? "" : "disabled"}>30秒後に消す（チャットで知らせる）</button><button class="btn sm ghost" data-clearnow ${running ? "" : "disabled"}>今すぐ消す</button></div>
      <div class="form-grid" style="margin-top:14px"><div class="lbl">自動でお掃除</div>
        <div><select data-auto style="max-width:260px">${[[0, "しない"], [15, "15分ごと"], [30, "30分ごと"], [60, "1時間ごと"]].map(([v, l]) => `<option value="${v}" ${+info_.auto_clear_min === v ? "selected" : ""}>${l}</option>`).join("")}</select>
          <div class="hint">消す30秒前にチャットで知らせます（落ちているアイテムだけ消します）。</div></div></div>
    </div>`;
  const sc = $("[data-scan]", el);
  sc.onclick = async () => {
    try { const r = await runJob(api("lag_scan", { id: s.id }), "重さの原因を探しています", { cancellable: true }); if (r && !r.cancelled) renderLag(el, s, r); } catch (e) { if (!e.shown) fail(e); }
  };
  const types = () => $$("[data-ct]:checked", el).map((x) => x.value);
  $("[data-clear]", el).onclick = async () => {
    if (!types().length) return toast("消すものを選んでください", "warn");
    try { await api("lag_clear", { id: s.id, types: types(), warn_seconds: 30 }); toast("30秒後に消します。チャットで知らせました"); } catch (e) { fail(e); }
  };
  $("[data-clearnow]", el).onclick = async () => {
    if (!types().length) return toast("消すものを選んでください", "warn");
    if (!(await ask("今すぐ消す", "<p>知らせずに、今すぐ消しますか？ 友達が拾おうとしていたアイテムも消えます。</p>", "消す", { danger: true }))) return;
    try { await api("lag_clear", { id: s.id, types: types() }); toast("消しました"); } catch (e) { fail(e); }
  };
  $("[data-auto]", el).onchange = async (e) => {
    try { await api("server_set", { id: s.id, profile: { auto_clear_min: +e.target.value } }); toast("保存しました"); } catch (er) { fail(er); }
  };
}

async function renderPregen(el, s) {
  let d;
  try { d = await api("pregen_status", { id: s.id }); } catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (!el.isConnected) return;
  const head = `<h2>ワールドの事前生成</h2><p class="muted" style="max-width:72ch">遊ぶ前に、まわりの地形を先に作っておきます。新しい場所を歩いたときに地形を作る重さがなくなるので、カクつきが減ります。定番のMOD「Chunky」を使います（サーバーだけに入れればよく、友達はいりません）。</p>`;
  if (!d.supported) { el.innerHTML = head + `<div class="note warn"><b>バニラ（MODなし）のサーバーではできません</b>Chunky は Fabric / NeoForge / Forge / Paper / Purpur で使えます。</div>`; return; }
  if (!d.installed) {
    el.innerHTML = head + `<button class="btn primary" data-inst>Chunky を入れる</button><p class="hint">入れたあと、サーバーを起動（動いているなら再起動）すると使えるようになります。</p>`;
    $("[data-inst]", el).onclick = async () => {
      try { await runJob(api("pregen_install", { id: s.id }), "Chunky を入れています"); toast("Chunky を入れました。サーバーを（再）起動してください。"); refreshState(); renderPregen(el, s); } catch (e) { if (!e.shown) fail(e); }
    };
    return;
  }
  const states = Object.entries(d.state || {});
  const active = states.find(([, v]) => ["running", "started", "paused"].includes(v.status));
  const est = d.estimates;
  el.innerHTML = head + `
    ${!d.running ? `<div class="note" style="margin-bottom:12px"><b>サーバーを起動すると始められます</b>事前生成はサーバーが動いている間に進みます。友達が遊んでいる間は重くなるので、誰もいない時間がおすすめです。</div>` : ""}
    ${d.needs_restart ? `<div class="note warn" style="margin-bottom:12px"><b>サーバーの再起動が必要です</b>Chunky を入れたあとに再起動していません。</div>` : ""}
    ${states.map(([dim, v]) => {
      const name = (d.dims.find(([k]) => k === dim) || [dim, dim])[1];
      const pct = v.percent || 0;
      const label = { running: "作っています", started: "始めています…", paused: "一時停止中", finished: "完成", cancelled: "中止しました", stopped: "止まりました" }[v.status] || v.status;
      return `<div class="panel" style="margin-bottom:12px"><div class="row"><b class="grow">${esc(name)}</b><span class="badge ${v.status === "finished" ? "ok" : v.status === "running" ? "warn" : ""}">${label}</span></div>
        <div class="meter" style="height:18px;margin:10px 0 6px"><i style="width:${pct}%;background:${v.status === "finished" ? "var(--grass)" : "var(--oak)"}"></i></div>
        <div class="small muted">${pct.toFixed(1)}%${v.chunks ? `　${fmtNum(v.chunks)}チャンク` : ""}${v.eta && v.status === "running" ? `　残り約 ${esc(v.eta)}` : ""}${v.rate ? `　${v.rate}チャンク/秒` : ""}${v.total_time ? `　かかった時間 ${esc(v.total_time)}` : ""}</div></div>`;
    }).join("")}
    ${active ? `<div class="row"><button class="btn" data-pg="${active[1].status === "paused" ? "continue" : "pause"}">${active[1].status === "paused" ? "再開する" : "一時停止"}</button><button class="btn danger" data-pg="cancel">中止する</button></div>`
    : `<div class="panel"><div class="form-grid">
        <div class="lbl">どの世界</div><div><select data-dim>${d.dims.map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select></div>
        <div class="lbl">範囲<div class="hint">スポーン地点から、東西南北それぞれ何ブロックか</div></div>
        <div><select data-rad>${Object.entries(est).map(([r, e]) => `<option value="${r}" ${r === "2000" ? "selected" : ""}>${r}ブロック（${fmtNum(e.chunks)}チャンク・約${e.mb_min}〜${e.mb_max}MB・${e.min_fast}〜${e.min_slow}分）</option>`).join("")}</select>
          <div class="hint">時間と大きさは目安です（PCの速さで変わります）。はじめては 1000〜2000 がおすすめです。</div></div>
      </div><div class="row" style="margin-top:14px"><button class="btn primary" data-start ${d.running ? "" : "disabled"}>事前生成を始める</button></div></div>`}`;
  const st = $("[data-start]", el);
  if (st) st.onclick = async () => {
    try { await api("pregen_start", { id: s.id, radius: +$("[data-rad]", el).value, dim: $("[data-dim]", el).value }); toast("始めました。進み具合はここに出ます"); setTimeout(() => renderPregen(el, s), 1500); } catch (e) { fail(e); }
  };
  $$("[data-pg]", el).forEach((b) => (b.onclick = async () => {
    if (b.dataset.pg === "cancel" && !(await ask("事前生成を中止", "<p>中止しますか？ ここまで作った地形は残ります。</p>", "中止する", { danger: true }))) return;
    try { await api("pregen_cmd", { id: s.id, action: b.dataset.pg }); toast("送りました"); setTimeout(() => renderPregen(el, s), 1200); } catch (e) { fail(e); }
  }));
}

// ---------------- 友達を呼ぶ（公開・ポート） ----------------
async function tabPublic(body, s) {
  body.innerHTML = `<div class="empty">読み込んでいます…</div>`;
  let p;
  try { p = await api("public_info", { id: s.id }); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  body.innerHTML = `
    <h2>友達がインターネットから入れるようにする</h2>
    <p class="lead">友達のマイクラから、あなたのPCのサーバーまで「道」を作ります。下の方法から1つ選んで、できたアドレスを一番下の欄に保存してください。</p>

    <div class="sec panel" data-netdiag></div>

    <div class="sec panel">
      <div class="row"><h3 class="grow">一番おすすめ: playit.gg を使う ${help("playit")}</h3><span class="badge ok">ルーターの設定なし・無料</span></div>
      <ol class="steps" style="margin-top:14px">
        <li><b>「playit を起動」を押す</b><span class="muted">黒い画面が開きます。遊んでいる間は閉じないでください。</span></li>
        <li><b>アカウントを作ってつなぐ</b><span class="muted">黒い画面に出る <span class="kbd">https://playit.gg/claim/…</span> をブラウザで開き、アカウントを作って「Claim」を押します（初回だけ）。</span></li>
        <li><b>トンネルを作る</b><span class="muted">playit.gg のサイトで「Add Tunnel」→「Minecraft Java」を選び、Local Port を <b style="color:var(--bone)">${p.port}</b> にします（初回だけ）。</span></li>
        <li><b>アドレスを貼る</b><span class="muted">サイトに出たアドレス（例: <span class="kbd">xxxx.joinmc.link</span>）を、一番下の欄に貼って保存します。</span></li>
      </ol>
      <div class="row"><button class="btn primary" data-playit>${p.playit ? "playit を止める" : "▶ playit を起動"}</button>
        <button class="btn" data-url="https://playit.gg/account/tunnels">playit.gg のサイトを開く</button>
        ${p.playit ? '<span class="badge ok">playit 動作中</span>' : ""}</div>
    </div>

    <div class="sec panel">
      <div class="row"><h3 class="grow">自動ポート開放 ${help("portforward")}</h3><span class="badge warn">回線によっては使えません</span></div>
      <p class="muted">ルーターが「UPnP」に対応していれば、ボタン1つでポート開放できます。光回線の IPv6 方式（v6プラスなど）やマンションの回線では使えないことが多いので、だめなら playit.gg を使ってください。</p>
      <div class="row"><button class="btn" data-upnp>ポートを開ける</button><button class="btn ghost" data-upnpclose>閉じる</button></div>
    </div>

    <div class="sec panel">
      <h3>同じ家（同じ Wi-Fi）の人だけ</h3>
      <p class="muted">同じルーターにつながっている人は、このアドレスで入れます。設定はいりません。</p>
      <div class="row"><span class="addr">${esc(p.lan_address)}</span><button class="btn sm" data-uselan>これを使う</button></div>
    </div>

    <div class="sec panel">
      <h3>ポート番号のこと ${help("port")}</h3>
      <div class="grid2" style="margin-top:10px">
        <div>
          <p>このサーバーのポート番号: <b style="font-size:20px">${p.port}</b></p>
          <p>${p.running ? '<span class="badge ok">サーバーが使っています</span>' : p.port_free ? '<span class="badge ok">空いています（使えます）</span>' : '<span class="badge ng">ほかのソフトが使っています</span>'}</p>
          ${!p.running && !p.port_free ? `<div class="note error"><b>このままでは起動できません</b>ほかのマイクラサーバーが動いていないか確認するか、「設定」タブでポート番号を変えてください（25566 など）。</div>` : ""}
          <ul class="small muted" style="padding-left:1.3em"><li>普通は 25565 のままでOKです。</li><li>サーバーを2つ同時に動かすなら、番号を分けます（25565 と 25566 など）。</li><li>25565 以外にしたら、アドレスの後ろに「:番号」をつけて伝えます（例: <span class="kbd">1.2.3.4:25566</span>）。</li></ul>
        </div>
        <div>
          <p><b>Windows のファイアウォール</b></p>
          <p class="small muted">はじめて起動したときに「アクセスを許可しますか？」と出たら「許可」を押します。出なかった・押し忘れたときは、ここから登録できます（管理者の確認が出ます）。</p>
          <button class="btn sm" data-fw ${S.windows ? "" : "disabled"}>ファイアウォールに登録する</button>
        </div>
      </div>
      <div class="note warn" style="margin-top:14px"><b>ポート番号を変えたら、playit の設定も直します</b>
        playit は「<b>どの番号の部屋に案内するか</b>」を覚えています。サーバーの番号を変えると、案内先がずれて友達が入れなくなります。
        <ol class="small" style="margin:8px 0 0 1.3em"><li>このサーバーの今のポート番号は <b style="font-size:16px">${p.port}</b> です。</li>
          <li>playit の黒い画面で、<span class="kbd">アドレス =&gt; 127.0.0.1:番号</span> の行を見ます。<b>この番号が ${p.port} と同じ</b>なら、直す必要はありません。</li>
          <li>違うときは、「playit.gg のサイトを開く」→「Tunnels」→ 使っているトンネルを開いて、<b>Local Port を ${p.port}</b> に変えて保存します。（「Edit」などの変更ボタンがない場合は、トンネルを作り直してください。アドレスが変わるので、保存し直しも必要です）</li>
          <li>playit の黒い画面を、いったん閉じて「▶ playit を起動」で開き直すと、確実です。</li></ol>
        <div class="small muted" style="margin-top:6px">一番かんたんなのは、<b>すべてのサーバーのポートを同じ番号（25565）にする</b>ことです。「ソフトの設定」で選べます。同時に2つ動かさないなら、これで設定を変える必要がなくなります。</div></div>
      <details style="margin-top:16px"><summary style="cursor:pointer;font-weight:700">自分でルーターのポート開放をするには（詳しい人向け）</summary>
        <ol class="steps" style="margin-top:14px">
          <li><b>ルーターの設定画面を開く</b><span class="muted">ブラウザで <button class="linkbtn" data-url="http://${esc(p.gateway || "192.168.1.1")}/">http://${esc(p.gateway || "192.168.1.1")}/</button> を開きます。ログインのIDとパスワードは、ルーター本体のシールか説明書に書いてあります。</span></li>
          <li><b>ポート開放の画面を探す</b><span class="muted">メーカーによって名前が違います。<br>NEC（Aterm）: 「詳細設定」→「ポートマッピング設定」<br>バッファロー: 「詳細設定」→「セキュリティー」→「ポート変換」<br>NTT のホームゲートウェイ: 「詳細設定」→「静的IPマスカレード設定」<br>そのほか: 「ポートフォワーディング」「仮想サーバー」など</span></li>
          <li><b>設定を追加する</b><span class="muted">プロトコル: <b style="color:var(--bone)">TCP</b>、ポート: <b style="color:var(--bone)">${p.port}</b>、転送先（LAN側のIPアドレス）: <b style="color:var(--bone)">${esc(p.local_ip)}</b> を入れて保存します。</span></li>
          <li><b>このPCのアドレスを固定する（おすすめ）</b><span class="muted">PCのアドレス（${esc(p.local_ip)}）は、ルーターの再起動で変わることがあります。変わるとつながらなくなるので、ルーターの「DHCP固定割当」などで固定しておくと安心です。</span></li>
          <li><b>インターネット側のアドレスを調べる</b><span class="muted"><button class="linkbtn" data-gip>あなたの家のアドレスを調べる</button> <span data-gipout></span><br>これ（ポートが 25565 以外なら「:番号」をつける）を友達に伝えます。</span></li>
        </ol>
        <div class="note warn"><b>ポート開放できない回線もあります</b>IPv6 IPoE（v6プラス・transix・OCNバーチャルコネクトなど）や、マンションの共用回線、ホームルーター・スマホのテザリングでは、自分で開けても外から入れないことがあります。そのときは playit.gg を使ってください。</div>
      </details>
    </div>

    <div class="sec panel" style="border-top:3px solid var(--oak)">
      <h3>友達に伝えるアドレス</h3>
      ${p.port_mismatch ? `<div class="note warn" style="margin-top:8px"><b>アドレスのポート番号が、サーバーのポート番号（${p.port}）と違います</b>ポート番号を変えたときは、アドレスの後ろの「:番号」も変えてください（25565 なら書かなくてOK）。</div>` : ""}
      <div class="inline-field" style="margin-top:10px"><input type="text" data-addr value="${esc(p.address)}" placeholder="例: xxxx.joinmc.link　または　1.2.3.4:25565" style="font:700 18px var(--mono)">
        <button class="btn primary" data-save>保存</button><button class="btn" data-copy>コピー</button></div>
      <p class="hint">保存したアドレスは友達用パックにも入り、友達のマイクラのサーバー一覧に最初から表示されます。</p>
      <div class="row" style="margin-top:10px"><button class="btn primary" data-reach>外から届くかテストする</button><button class="btn" data-ping>このPCからテスト</button><span data-pingout class="small"></span></div>
      <div data-reachout style="margin-top:10px"></div>
      <p class="hint">「外から届くかテスト」は、外のサービス（mcsrvstat.us）に、インターネット側からあなたのサーバーにつながるか試してもらいます。送るのはアドレスだけです。</p>
    </div>

    <div class="sec panel" data-invite></div>

    <div class="sec panel" data-ddns></div>`;
  const addr = $("[data-addr]", body);
  const save = async (v) => { addr.value = v; await api("set_address", { id: s.id, address: v }); toast("アドレスを保存しました"); refreshState(); renderInvite($("[data-invite]", body), s); };
  $("[data-save]", body).onclick = () => save(addr.value.trim()).catch(fail);
  renderNetDiag($("[data-netdiag]", body), s, save);
  $("[data-copy]", body).onclick = () => { navigator.clipboard.writeText(addr.value); toast("コピーしました。LINE や Discord に貼って送りましょう"); };
  $("[data-uselan]", body).onclick = () => save(p.lan_address).catch(fail);
  $("[data-playit]", body).onclick = async () => {
    try {
      const r = await api("playit_toggle");
      if (r.job) await runJob(Promise.resolve(r), "playit を準備しています");
      tabPublic(body, s);
    } catch (e) { if (!e.shown) fail(e); }
  };
  $("[data-upnp]", body).onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "ルーターを探しています…";
    try { const r = await api("upnp_open", { id: s.id }); addr.value = r.address; toast("ポートを開けました: " + r.address); refreshState(); }
    catch (err) { await info("自動ポート開放ができませんでした", `<p>${esc(err.message)}</p><p>playit.gg を使う方法がおすすめです。</p>`); }
    finally { e.target.disabled = false; e.target.textContent = "ポートを開ける"; }
  };
  $("[data-upnpclose]", body).onclick = () => api("upnp_close", { id: s.id }).then(() => toast("閉じました")).catch(fail);
  $("[data-fw]", body).onclick = () => api("firewall", { id: s.id }).then(() => toast("登録しました")).catch(fail);
  $("[data-gip]", body).onclick = async () => {
    const out = $("[data-gipout]", body); out.textContent = "調べています…";
    try { const r = await api("global_ip"); out.innerHTML = r.ip ? `<b class="kbd">${esc(r.ip)}${p.port !== 25565 ? ":" + p.port : ""}</b>` : "調べられませんでした"; } catch (e) { out.textContent = e.message; }
  };
  $("[data-reach]", body).onclick = async (e) => {
    const out = $("[data-reachout]", body);
    e.target.disabled = true; out.innerHTML = `<div class="empty" style="padding:10px">インターネット側からつないでもらっています…（10秒くらい）</div>`;
    try {
      const r = await api("reach_test", { id: s.id, address: addr.value.trim() || null });
      out.innerHTML = `<div class="note ${r.online ? "ok" : "error"}"><b>${r.online ? "✔ " : "✖ "}${esc(r.message)}</b>
        <span class="small">${esc(r.address)}${r.online ? `　${esc(r.version || "")}・${r.players ?? "?"}/${r.max ?? "?"}人` : ""}</span>
        ${r.hints.length ? `<ul class="small">${r.hints.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : ""}</div>`;
    } catch (err) { out.innerHTML = `<div class="note warn">${esc(err.message)}</div>`; } finally { e.target.disabled = false; }
  };
  renderInvite($("[data-invite]", body), s);
  renderDdns($("[data-ddns]", body), s, (v) => save(v).catch(fail));
  $("[data-ping]", body).onclick = async (e) => {
    const out = $("[data-pingout]", body);
    const a = addr.value.trim();
    if (!a) { out.textContent = "先にアドレスを入れてください"; return; }
    e.target.disabled = true; out.textContent = "テストしています…";
    try {
      const r = await api("server_ping", { address: a });
      out.innerHTML = r.ok ? `<span class="badge solid-ok">つながりました</span> ${esc(r.version)}・${r.online}/${r.max}人・${r.ms}ミリ秒`
        : `<span class="badge solid-ng">つながりません</span> ${esc(r.error)}（サーバーが「動いています」になっているか確認してください）`;
    } catch (err) { out.textContent = err.message; } finally { e.target.disabled = false; }
  };
}

// ---------------- 招待リンク・QRコード ----------------
function qrSvg(text, cell = 6) {
  if (typeof qrcode !== "function" || !text) return "";
  const q = qrcode(0, "M");
  q.addData(text);
  q.make();
  const n = q.getModuleCount(), m = 4, size = (n + m * 2) * cell;
  let rects = "";
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c)) rects += `M${(c + m) * cell} ${(r + m) * cell}h${cell}v${cell}h-${cell}z`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/><path d="${rects}" fill="#000"/></svg>`;
}

async function renderInvite(el, s) {
  let d;
  try { d = await api("invite", { id: s.id }); } catch (e) { el.innerHTML = ""; return; }
  if (!el.isConnected) return;
  if (!d.address) { el.innerHTML = `<h3>招待リンク・QRコード</h3><p class="muted">上で「友達に伝えるアドレス」を保存すると、招待リンクとQRコードが作れます。</p>`; return; }
  const text = `「${d.name}」に遊びに来てね！\nアドレス: ${d.address}\n\nOneClickServerMCを使っている人は、このリンクを開くとMODも自動で入ります:\n${d.link}`;
  el.innerHTML = `<h3>招待リンク・QRコード</h3>
    <div class="grid2" style="margin-top:10px;align-items:start">
      <div>
        <p class="small muted">友達もこのソフトを使っていれば、<b>招待リンク</b>を開くだけで「参加」の画面が開き、MODも自動で入ります。使っていない友達は、アドレスと友達用パックで入れます。</p>
        <div class="form-grid" style="grid-template-columns:110px 1fr;margin-top:8px">
          <div class="lbl">招待リンク</div><div class="inline-field"><input type="text" readonly value="${esc(d.link)}" data-ilink style="font:13px var(--mono)"><button class="btn sm" data-ic="link">コピー</button></div>
          <div class="lbl">送る文</div><div><textarea readonly rows="5" data-itext style="font-size:13px">${esc(text)}</textarea><button class="btn sm" data-ic="text" style="margin-top:6px">文をまるごとコピー</button></div>
        </div>
        <p class="hint">Discord や LINE では「mcmh://」のリンクは押せないことがあります。そのときは、友達にリンクをコピーして、このソフトの「友達のサーバーに参加」→「アドレスで参加」に貼ってもらいます。</p>
      </div>
      <div style="text-align:center">
        <div data-qr style="display:inline-block;background:#fff;padding:6px;box-shadow:0 0 0 2px var(--edge)">${qrSvg(d.address)}</div>
        <div class="small muted" style="margin-top:6px">アドレスのQRコード（スマホで読んで送る用）</div>
        <button class="btn sm" data-qrsave style="margin-top:6px">画像として保存</button>
        <button class="btn sm primary" data-card2 style="margin-top:6px">招待カードを作る</button>
      </div>
    </div>`;
  $$("[data-ic]", el).forEach((b) => (b.onclick = () => { navigator.clipboard.writeText(b.dataset.ic === "link" ? d.link : text); toast("コピーしました"); }));
  $("[data-card2]", el).onclick = () => inviteCardFlow(s);
  $("[data-qrsave]", el).onclick = () => {
    const svg = qrSvg(d.address, 10);
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = img.width; c.height = img.height;
      c.getContext("2d").drawImage(img, 0, 0);
      const a = document.createElement("a");
      a.href = c.toDataURL("image/png");
      a.download = `${d.name}-QR.png`.replace(/[\\/:*?"<>|]/g, "_");
      a.click();
    };
    img.src = "data:image/svg+xml;base64," + btoa(svg);
  };
}

// ---------------- DuckDNS（変わらないアドレス） ----------------
async function renderDdns(el, s, useAddr) {
  let d;
  try { d = await api("ddns_get", { id: s.id }); } catch (e) { el.innerHTML = ""; return; }
  if (!el.isConnected) return;
  el.innerHTML = `<h3>変わらないアドレスを作る（DuckDNS・ポート開放をする人向け）</h3>
    <p class="small muted" style="max-width:78ch">家のインターネットの住所（数字）は、ときどき変わります。変わると友達が入れなくなるので、無料の <b>DuckDNS</b> で「◯◯.duckdns.org」という名前を作り、このソフトが10分ごとに今の住所を知らせます。<b>playit.gg を使う人はいりません</b>（ポート開放は別に必要です）。</p>
    <ol class="small" style="padding-left:1.4em"><li><button class="linkbtn" data-url="https://www.duckdns.org/">www.duckdns.org</button> を開いて、Google などでログイン</li>
      <li>「sub domain」に好きな名前（例: mymcserver）を入れて「add domain」</li><li>ページの上に出る <b>token</b>（36文字）と、作った名前を下に入れて「保存」</li></ol>
    <div class="row" style="margin-top:8px"><input type="text" data-dd value="${esc(d.domain)}" placeholder="名前（例: mymcserver）" style="max-width:220px"><span>.duckdns.org</span>
      <input type="password" data-dt placeholder="${d.set ? "設定ずみ（変えるときだけ）" : "token"}" style="max-width:320px" autocomplete="off"><button class="btn" data-dsave>保存</button>${d.set ? '<button class="btn ghost" data-ddel>やめる</button>' : ""}</div>
    ${d.set ? `<div class="row" style="margin-top:10px"><span class="badge ${d.ok === false ? "ng" : "ok"}">${esc(d.message || "設定ずみ")}${d.last ? `（${esc(d.last)}）` : ""}</span>${d.address ? `<span class="addr">${esc(d.address)}</span><button class="btn sm" data-duse>これを友達に伝えるアドレスにする</button>` : ""}</div>` : ""}`;
  $$("[data-url]", el).forEach((b) => (b.onclick = () => api("open_url", { url: b.dataset.url }).catch(fail)));
  $("[data-dsave]", el).onclick = async () => {
    try { await api("ddns_set", { id: s.id, domain: $("[data-dd]", el).value.trim(), token: $("[data-dt]", el).value.trim() }); toast("DuckDNS につながりました"); renderDdns(el, s, useAddr); } catch (e) { fail(e); }
  };
  const del = $("[data-ddel]", el);
  if (del) del.onclick = async () => { await api("ddns_set", {}).catch(fail); renderDdns(el, s, useAddr); };
  const use = $("[data-duse]", el);
  if (use) use.onclick = () => useAddr(d.address);
}

// ---------------- 友達用パック ----------------
async function tabPack(body, s) {
  body.innerHTML = `<div class="empty">読み込んでいます…</div>`;
  let p;
  try { p = await api("pack_tab", { server: s.id }); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const req = p.items.filter((i) => i.need === "required"), opt = p.items.filter((i) => i.need !== "required");
  let ps = null;
  try { ps = await api("pack_status", { server: s.id }); } catch (e) { /* なくても続ける */ }
  body.innerHTML = `${ps ? `<div class="panel" style="margin-bottom:20px;border-top:3px solid var(--grass)">
      <div class="row top"><label class="switch" style="margin-top:4px"><input type="checkbox" data-auto ${ps.enabled ? "checked" : ""}><span></span></label>
      <div class="grow"><h3>友達用パックを自動で配る（おすすめ）</h3>
        <p class="small muted" style="margin:4px 0 0;max-width:76ch">MODを変えてサーバーを起動すると、パックが自動で新しくなります。友達は「遊ぶ」ボタンを押すだけで、新しいパックに自動で入れ替わります。マイクラと同じアドレスを使うので、ポート開放や playit の設定を増やす必要はありません。</p>
        <div class="chips" style="margin-top:10px"><span class="badge">今のパック ${esc(ps.version || "まだありません")}</span>
          ${ps.outdated ? (ps.auto && ps.enabled ? '<span class="badge warn">MODが変わりました（まもなく自動で作り直します）</span>' : '<span class="badge warn">MODが変わりました（次の起動で新しくなります）</span>') : ps.exported ? '<span class="badge ok">最新</span>' : ""}
          ${ps.gateway ? `<span class="badge ok">配っています</span><span class="badge">ダウンロード ${ps.downloads}回</span>` : ps.enabled ? '<span class="badge">サーバーを起動すると配り始めます</span>' : ""}</div>
        ${ps.enabled ? `<div class="row" style="margin-top:12px;gap:10px;flex-wrap:nowrap;align-items:flex-start"><label class="switch" style="flex:none"><input type="checkbox" data-packauto ${ps.auto ? "checked" : ""}><span></span></label>
          <div><b>MODを変えたら、すぐに自動で作り直す</b><div class="small muted">サーバーが動いていても、止まっていても、MODの追加・更新・削除のあと（約30秒後）に新しいパックを作ります。友達のソフトに「更新があります」と出て、何が変わったかも見えます。</div></div></div>
          ${ps.auto_text ? `<p class="hint" style="margin-top:8px">📦 ${esc(ps.auto_text)}${ps.auto_time ? "（" + new Date(ps.auto_time * 1000).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) + "）" : ""}</p>` : ""}` : ""}
        ${ps.enabled ? `<p class="hint">※ この機能がONのとき、サーバーの記録（ログ）では、参加者のIPアドレスが 127.0.0.1 と表示されます（playit を使うときも同じです）。うまく入れないときは、OFFにすると前と同じつなぎ方に戻ります（次の起動から）。</p>` : ""}
      </div></div></div>` : ""}
    <div class="grid2">
    <div>
      <h2>友達用パックを作る ${help("pack")}</h2>
      <p class="muted">サーバーと同じMODが入ったファイル（.mrpack）を作ります。友達はこれを入れるだけで、同じMODがそろい、サーバーのアドレスも登録されます。<b>これは友達に送る用です。</b>自分が遊ぶ手順は、下の「ホスト用」の枠を見てください。</p>
      ${p.address ? "" : `<div class="note warn"><b>アドレスがまだありません</b>「友達を呼ぶ」タブでアドレスを決めてから作ると、友達のサーバー一覧に自動で入ります。</div>`}
      <div class="row" style="margin:14px 0"><label class="nowrap">パックのバージョン</label><input type="text" data-ver value="${esc(p.version)}" style="width:110px">
        <label class="check small"><input type="checkbox" data-cfg checked> MODの設定も入れる</label></div>
      <div class="row"><button class="btn primary big" data-export>パックを作る</button>
        <button class="btn" data-card title="友達に見せる「入り方」を、1枚のページにします（印刷・スクショ・PDF化できます）">友達用の入り方カードを作る</button></div>
      <div data-cardout></div>
      <div class="panel" style="margin-top:22px;border-top:3px solid var(--grass)">
        <h3>🎮 サーバーを立てた自分が遊ぶには（ホスト用）</h3>
        <p class="small muted" style="margin:4px 0 10px">「パックを作る」は友達に送る用です。<b>自分が遊ぶには、別の手順が必要です。</b>上から順番にやってください。</p>
        <ol class="steps">
          <li><b>サーバーを起動する</b><span class="muted">「ホーム」タブの「▶ 起動」を押して、「起動完了」と出るまで待ちます。</span></li>
          <li><b>下のボタンを押す</b><span class="muted">自分のマイクラにMODを入れる準備の画面に移ります。<b>ここではまだ入っていません。</b></span></li>
          <li><b>移った画面で、青い「入れる」ボタンを押す</b><span class="muted">「準備できました」と出たら、自分のマイクラに入ったことになります。</span></li>
          <li><b>公式ランチャーで「${esc(s.name)}（マルチ用）」を選んで「プレイ」</b><span class="muted">ランチャーが開いたままなら、一度閉じて開き直してください。</span></li>
          <li><b>「マルチプレイ」→ サーバーを選んで「サーバーに接続」</b><span class="muted">一覧に出ない・つながらないときは「ダイレクト接続」で <b>localhost</b> と入れます。</span></li>
        </ol>
        <div class="row" style="margin-top:12px"><button class="btn primary big" data-self>② 自分のマイクラに入れる準備をする</button></div>
        <p class="hint">自分の名前でホワイトリストを使っているなら、「メンバー・BAN」タブに自分のマイクラの名前を追加しておいてください。</p>
      </div>
      <p class="hint">${ps && ps.enabled ? "このファイルを送るのは最初の1回だけです。MODを変えても、サーバーを起動すれば自動で新しくなり、友達は「▶ 遊ぶ」で受け取れます。" : "MODを変えたら、バージョンを上げて（1.0.1 など）作り直し、友達にもう一度送ってください。"}</p>
      <div data-done></div>
    </div>
    <div>
      <h3>パックに入るMOD</h3>
      <p class="small muted">🔒 は全員に必要なので外せません。「なくてもOK」のMOD（軽量化・ミニマップなど）は、入れるか選べます。友達も自分で選べます。</p>
      <div class="list" style="max-height:440px;overflow-y:auto">
        ${opt.map((i) => `<div class="item noselect">${slotImg(i.icon, i.title, "sm")}<div style="min-width:0"><div class="t">${esc(i.title)}</div><div class="s">なくてもOK${i.ja ? "・" + esc(i.ja) : ""}</div></div>
          <label class="switch"><input type="checkbox" data-opt="${esc(i.path)}" ${i.included ? "checked" : ""} aria-label="${esc(i.title)} をパックに入れる"><span></span></label></div>`).join("")}
        ${req.map((i) => `<div class="item noselect">${slotImg(i.icon, i.title, "sm")}<div style="min-width:0"><div class="t">${esc(i.title)}</div><div class="s">全員に必要</div></div><span aria-label="外せません">🔒</span></div>`).join("")}
        ${p.items.length ? "" : `<div class="empty">まだMODがありません</div>`}
      </div>
    </div></div>`;
  const au = $("[data-auto]", body);
  if (au) au.onchange = async () => { try { await api("server_set", { id: s.id, profile: { pack_server: au.checked } }); toast(au.checked ? "ONにしました（次の起動から）" : "OFFにしました（次の起動から）"); tabPack(body, s); } catch (e) { fail(e); } };
  const pa = $("[data-packauto]", body);
  if (pa) pa.onchange = async () => { try { await api("server_set", { id: s.id, profile: { pack_auto: pa.checked } }); toast(pa.checked ? "MODを変えたら自動で作り直します" : "自動で作り直すのをやめました（起動のときだけ新しくなります）"); } catch (e) { fail(e); pa.checked = !pa.checked; } };
  const syncExclude = () => api("pack_set_exclude", { server: s.id, exclude: $$("[data-opt]", body).filter((c) => !c.checked).map((c) => c.dataset.opt) }).catch(fail);
  $$("[data-opt]", body).forEach((c) => (c.onchange = syncExclude));
  $("[data-card]", body).onclick = async (e) => {
    e.target.disabled = true;
    try {
      const r = await api("guide_card", { id: s.id });
      $("[data-cardout]", body).innerHTML = `<div class="note ok" style="margin-top:10px"><b>できました</b>デスクトップ（なければユーザーのフォルダ）に保存しました。ブラウザで開いて、印刷・スクショ・「PDFで保存」ができます。そのまま友達に送ってもOKです。<br><span class="small muted">${esc(r.path)}</span></div>`;
    } catch (err) { fail(err); } finally { e.target.disabled = false; }
  };
  $("[data-export]", body).onclick = async (e) => {
    e.target.disabled = true;
    try {
      const r = await api("pack_export", { server: s.id, version: $("[data-ver]", body).value || "1.0.0", configs: $("[data-cfg]", body).checked });
      const inv = inviteText(s, p, r.file);
      $("[data-ver]", body).value = r.version;
      $("[data-done]", body).innerHTML = `<div class="note ok" style="margin-top:16px"><b>できました: ${esc(r.file)}</b>MOD ${r.files + r.manual}個入りです。このファイルを友達に送ってください（LINE・Discord・Google ドライブなど）。最初の1回だけ送れば、あとは自動で更新されます。</div>
        <div class="row" style="margin-top:10px"><button class="btn" data-open>ファイルの場所を開く</button></div>
        <h3 style="margin-top:18px">友達に送るメッセージ</h3><textarea rows="12" data-inv style="margin-top:8px">${esc(inv)}</textarea>
        <button class="btn sm" data-cinv style="margin-top:6px">メッセージをコピー</button>`;
      $("[data-open]", body).onclick = () => api("open_folder", { path: r.path }).catch(fail);
      $("[data-cinv]", body).onclick = () => { navigator.clipboard.writeText($("[data-inv]", body).value); toast("コピーしました"); };
    } catch (err) { fail(err); } finally { e.target.disabled = false; }
  };
  $("[data-self]", body).onclick = async () => {
    try { const info_ = await api("pack_install_self", { server: s.id }); go("join", { joinInfo: info_ }); } catch (e) { fail(e); }
  };
}

function inviteText(s, p, file) {
  return `マイクラのMODサーバー「${s.name}」を立てたよ！

■ マイクラ Java版 ${s.mc}（${s.loader_label}）
■ アドレス: ${p.address || "（あとで送るね）"}

【入り方】
1. 添付の「${file}」をダウンロード
2. 「OneClickServerMC」を開いて「友達のサーバーに参加」→ ファイルを選んで「入れる」
   （Prism Launcher や Modrinth App を使っている人は、ファイルを開くだけでOK）
3. 公式ランチャーで「${s.name}（マルチ用）」を選んで「プレイ」
4. 「マルチプレイ」にサーバーが最初から入っているので、選んで「サーバーに接続」

次からは「OneClickServerMC」の「遊ぶ」ボタンを押すだけで、MODが変わっても自動で新しくなるよ。
入れないときは、マイクラの名前（Java版のプレイヤー名）を教えて！ホワイトリストに追加するよ。`;
}

// ---------------- メンバー ----------------
async function tabMembers(body, s) {
  let m;
  try { m = await api("members", { id: s.id }); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const col = (kind, title, hk, desc) => `<div class="panel"><h3>${title} ${help(hk)}</h3><p class="small muted">${desc}</p>
    <div class="inline-field"><input type="text" data-in="${kind}" placeholder="マイクラの名前（Java版のプレイヤー名）"><button class="btn primary" data-add="${kind}">追加</button></div>
    <div class="list" style="margin-top:12px">${(m[kind === "whitelist" ? "whitelist" : "ops"] || []).map((n) => `<div class="item noselect"><span class="face player" style="padding:0;box-shadow:none;background:none"><span class="face"></span></span>
      <div class="t">${esc(n)}</div>${cpBtn(n, "名前", "名前")}${cpIdBtn(n)}<button class="btn sm ghost" data-rm="${kind}" data-name="${esc(n)}">外す</button></div>`).join("") || `<p class="faint small">まだいません</p>`}</div></div>`;
  body.innerHTML = `<div class="row" style="margin-bottom:16px"><label class="switch"><input type="checkbox" data-wl ${m.enabled ? "checked" : ""}><span></span></label>
      <div><b>ホワイトリストを使う</b>（おすすめ） ${help("whitelist")}<div class="small muted">ON だと、下の名簿にいる人しか入れません。知らない人が入ってこないので安全です。</div></div></div>
    ${m.enabled ? "" : `<div class="note warn" style="margin-bottom:16px"><b>今は誰でも入れます</b>アドレスを知っている人なら、知らない人でも入れる状態です。</div>`}
    <div class="grid2">${col("whitelist", "入っていい人", "whitelist", "友達のマイクラの名前を、大文字・小文字も正確に入れてください。")}
    ${col("ops", "OP（管理者）", "op", "コマンドが使える人です。信頼できる人だけにしましょう。")}</div>
    <div data-appl></div><div data-intr></div><div data-bans></div><div data-play></div>`;
  renderApplicants($("[data-appl]", body), s, () => tabMembers(body, s));
  renderBans($("[data-bans]", body), s);
  renderIntrusions($("[data-intr]", body), s);
  renderPlaytime($("[data-play]", body), s);
  $("[data-wl]", body).onchange = async (e) => { try { await api("whitelist_toggle", { id: s.id, on: e.target.checked }); tabMembers(body, s); } catch (err) { fail(err); } };
  $$("[data-add]", body).forEach((b) => {
    const kind = b.dataset.add;
    const input = $(`[data-in="${kind}"]`, body);
    const add = async () => {
      if (!input.value.trim()) return;
      b.disabled = true;
      try { await api("member_add", { id: s.id, kind, name: input.value.trim() }); toast("追加しました"); tabMembers(body, s); } catch (e) { fail(e); b.disabled = false; }
    };
    b.onclick = add;
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
  });
  $$("[data-rm]", body).forEach((b) => (b.onclick = async () => {
    try { await api("member_remove", { id: s.id, kind: b.dataset.rm, name: b.dataset.name }); tabMembers(body, s); } catch (e) { fail(e); }
  }));
}

// ---------------- あやしいアクセス ----------------
async function renderIntrusions(el, s) {
  let d;
  try { d = await api("intrusions_get", { id: s.id }); } catch (e) { el.innerHTML = ""; return; }
  if (!el.isConnected) return;
  if (!d.items.length) { el.innerHTML = `<div class="note ok" style="margin-top:18px"><b>あやしいアクセスはありません</b>ホワイトリストにない人・BAN した人が入ろうとしたら、ここに出ます（Discord やデスクトップにも知らせられます：「設定」→「通知」）。</div>`; return; }
  el.innerHTML = `<div class="panel" style="margin-top:18px;box-shadow:inset 6px 0 0 #e0b23a,var(--block)"><div class="row"><h3 class="grow">🚨 あやしいアクセス（${d.items.length}件）</h3><button class="btn sm ghost" data-iclr>記録を消す</button></div>
    <table class="tbl" style="margin-top:8px"><thead><tr><th>だれ</th><th>IPアドレス</th><th>理由</th><th>回数</th><th>最後</th><th></th></tr></thead><tbody>
    ${d.items.map((x, i) => `<tr><td><b>${esc(x.name || "（名前なし）")}</b> ${x.name ? cpBtn(x.name, "名前", "名前") : ""}</td><td class="small">${esc(x.ip || "?")} ${x.ip ? cpBtn(x.ip, "コピー", "IPアドレス") : ""}${d.banned_ips.includes(x.ip) ? ' <span class="badge ng">BAN中</span>' : ""}</td><td class="small">${esc(x.reason)}</td><td>${x.count}</td><td class="small">${esc(x.last_text)}</td>
      <td style="text-align:right">${x.ip && !d.banned_ips.includes(x.ip) && !/^127\./.test(x.ip) ? `<button class="btn sm danger" data-iban="${esc(x.ip)}">IP BAN</button>` : ""}${x.name && x.kind === "rejected" ? `<button class="btn sm" data-iwl="${esc(x.name)}" title="知り合いだったら">名簿に入れる</button>` : ""}</td></tr>`).join("")}
    </tbody></table><p class="hint">IP BAN すると、その場所からはサーバーにつなげなくなります（このソフトの門番が断ります）。友達だったときは「名簿に入れる」を押します。</p></div>`;
  $("[data-iclr]", el).onclick = async () => { await api("intrusions_clear", { id: s.id }).catch(fail); renderIntrusions(el, s); };
  $$("[data-iban]", el).forEach((b) => (b.onclick = async () => {
    if (!(await ask("IP BAN", `<p>${esc(b.dataset.iban)} からのアクセスを断りますか？</p>`, "BAN する", { danger: true }))) return;
    try { await api("ban_add", { id: s.id, kind: "ip", target: b.dataset.iban, reason: "あやしいアクセス" }); toast("BAN しました"); renderIntrusions(el, s); } catch (e) { fail(e); }
  }));
  $$("[data-iwl]", el).forEach((b) => (b.onclick = async () => {
    try { await api("member_add", { id: s.id, kind: "whitelist", name: b.dataset.iwl }); toast("名簿に入れました"); tabMembers(el.closest("[data-body]"), s); } catch (e) { fail(e); }
  }));
}

// ---------------- プレイ時間 ----------------
async function renderPlaytime(el, s, days = 30) {
  let d;
  try { d = await api("playtime", { id: s.id, days }); } catch (e) { el.innerHTML = ""; return; }
  if (!el.isConnected) return;
  const max = Math.max(1, ...d.daily.map((x) => x.minutes));
  const hmax = Math.max(1, ...d.hours);
  const medals = ["🥇", "🥈", "🥉"];
  el.innerHTML = `<div class="panel" style="margin-top:18px"><div class="row"><h3 class="grow">プレイ時間ランキング</h3>
      <select data-days style="width:auto">${[[7, "この1週間"], [30, "この30日"], [365, "この1年"]].map(([v, l]) => `<option value="${v}" ${v === d.days ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    ${d.ranking.length ? `<div class="grid2" style="margin-top:10px;align-items:start">
      <table class="tbl"><thead><tr><th></th><th>プレイヤー</th><th>遊んだ時間</th><th>日数</th><th>最後</th></tr></thead><tbody>
        ${d.ranking.map((p, i) => `<tr><td>${medals[i] || i + 1}</td><td><b>${esc(p.name)}</b>${p.online ? ' <span class="badge ok">いま遊んでいる</span>' : ""} ${cpBtn(p.name, "名前", "名前")}${cpIdBtn(p.name)}</td><td><b>${esc(p.text)}</b></td><td>${p.days}日</td><td class="small muted">${esc(p.last_text)}</td></tr>`).join("")}
      </tbody></table>
      <div><div class="small muted">日ごと（全員の合計・分）</div>
        <div style="display:flex;align-items:flex-end;gap:3px;height:110px;margin-top:6px">${d.daily.map((x) => `<div title="${esc(x.day)}: ${x.minutes}分" style="flex:1;background:var(--grass);height:${Math.max(2, (x.minutes / max) * 100)}%;box-shadow:inset 0 2px 0 rgba(255,255,255,.3),0 0 0 1px var(--edge)"></div>`).join("")}</div>
        <div class="row small faint" style="justify-content:space-between"><span>${esc(d.daily[0].day)}</span><span>${esc(d.daily[d.daily.length - 1].day)}</span></div>
        <div class="small muted" style="margin-top:10px">よく遊ぶ時間帯</div>
        <div style="display:flex;align-items:flex-end;gap:2px;height:60px;margin-top:6px">${d.hours.map((h, i) => `<div title="${i}時: ${h}分" style="flex:1;background:var(--diamond);height:${Math.max(2, (h / hmax) * 100)}%;box-shadow:0 0 0 1px var(--edge)"></div>`).join("")}</div>
        <div class="row small faint" style="justify-content:space-between"><span>0時</span><span>12時</span><span>23時</span></div>
        <p class="small" style="margin-top:8px">全員の合計: <b>${esc(d.total_text)}</b></p></div></div>`
      : `<p class="muted" style="margin-top:8px">まだ記録がありません。誰かが遊ぶと、ここに時間が記録されます。</p>`}</div>`;
  $("[data-days]", el).onchange = (e) => renderPlaytime(el, s, +e.target.value);
}

// ---------------- バックアップ ----------------
async function tabBackup(body, s) {
  let b;
  try { b = await api("backups", { id: s.id }); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  body.innerHTML = `<h2>ワンクリック巻き戻し ${help("backup")}</h2>
    <p class="muted" style="max-width:76ch">荒らし・TNTの大爆発・火事などがあったとき、ワールドを前の状態に戻します。サーバーが動いていても、自動で止めて → 戻して → 起動し直します（今のワールドも念のため保存します）。</p>
    <div class="grid3" style="margin-top:10px">${b.rollback.map((t) => `<button class="choice" data-rb="${esc(t.file || "")}" ${t.file ? "" : "disabled"} style="${t.file ? "" : "opacity:.55;cursor:not-allowed"}">
        <span class="slot"><span class="letter">⟲</span></span><div><b>${esc(t.label)}</b><span>${t.file ? `${esc(t.when)} のバックアップ（${esc(t.ago)}）` : "まだその時間のバックアップがありません"}</span></div></button>`).join("")}</div>
    <hr class="sep">
    <div class="grid2"><div><h2>バックアップ</h2>
      <p class="muted">ワールドのコピーです。MODを入れる前や更新の前にとっておくと、何かあっても戻せます。</p>
      <button class="btn primary big" data-now>今すぐバックアップ</button></div>
    <div class="panel"><h3>自動バックアップ</h3>
      <div class="row" style="margin-top:10px"><label>間かく</label><select data-int style="width:auto">${[[0, "しない"], [15, "15分ごと"], [30, "30分ごと"], [60, "1時間ごと"], [120, "2時間ごと"]].map(([v, l]) => `<option value="${v}" ${+b.interval === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      <label>残す数</label><input type="number" data-keep min="1" max="100" value="${b.keep}" style="width:90px"><button class="btn sm" data-savebk>保存</button></div>
      <label class="check small" style="margin-top:10px"><input type="checkbox" data-bos ${b.backup_on_start ? "checked" : ""}> サーバーを起動する前にもバックアップする</label>
      <p class="hint">新しいものを「残す数」だけ残し、さらに「1時間ごとに1つ（1日ぶん）」「1日ごとに1つ（1週間ぶん）」を残すので、「1時間前」「昨日」に戻せます。手動のものは消えません。</p></div></div>
    <h3 style="margin-top:22px">とったバックアップ</h3>
    <table class="tbl" style="margin-top:8px"><thead><tr><th>日時</th><th>種類</th><th>大きさ</th><th></th></tr></thead><tbody>
      ${b.backups.map((x) => `<tr><td>${esc(x.when)}</td><td>${esc(x.kind)}</td><td>${x.size_mb}MB</td><td style="text-align:right"><button class="btn sm" data-restore="${esc(x.file)}">この時点に戻す</button></td></tr>`).join("") || `<tr><td colspan="4" class="faint">まだありません</td></tr>`}
    </tbody></table>
    <button class="btn sm ghost" data-open style="margin-top:10px">バックアップのフォルダを開く</button>`;
  $("[data-now]", body).onclick = async () => {
    try { const r = await runJob(api("backup_now", { id: s.id }), "バックアップしています"); toast("保存しました: " + r.file); tabBackup(body, s); } catch (e) { if (!e.shown) fail(e); }
  };
  $("[data-savebk]", body).onclick = () => api("server_set", { id: s.id, profile: { backup_interval_min: +$("[data-int]", body).value, backup_keep: +$("[data-keep]", body).value || 10 } }).then(() => toast("保存しました")).catch(fail);
  $("[data-bos]", body).onchange = (e) => api("server_set", { id: s.id, profile: { backup_on_start: e.target.checked } }).then(() => toast("保存しました")).catch(fail);
  const doRollback = async (file, label) => {
    const running = s.status !== "stopped";
    if (!(await ask("ワールドを巻き戻す", `<p>ワールドを <b>${esc(label)}</b> に戻しますか？</p><ul class="small"><li>それより後に作ったもの・集めたものは消えます</li><li>今のワールドも「巻き戻し前」として保存するので、間違えても戻せます</li>${running ? "<li>動いているサーバーは、10秒前にチャットで知らせてから止め、戻したあと起動し直します</li>" : ""}</ul>`, "巻き戻す", { danger: true }))) return;
    try { const r = await runJob(api("rollback", { id: s.id, file }), "ワールドを巻き戻しています", { cancellable: true }); if (r && !r.cancelled) { toast("巻き戻しました"); refreshAll(); tabBackup(body, cur() || s); } } catch (e) { if (!e.shown) fail(e); }
  };
  $$("[data-rb]", body).forEach((btn) => (btn.onclick = () => { if (btn.dataset.rb) doRollback(btn.dataset.rb, $("b", btn).textContent); }));
  $$("[data-restore]", body).forEach((btn) => (btn.onclick = () => doRollback(btn.dataset.restore, btn.dataset.restore + " の時点")));
  $("[data-open]", body).onclick = () => api("open_folder", { id: s.id, which: "backups" }).catch(fail);
  const gz = document.createElement("div"); body.appendChild(gz); renderGrief(gz, s);
}

// ---------------- 設定 ----------------
// ================================================================ 新しいサーバーを作る（順番に案内）
const WSTEPS = ["名前", "MODを選ぶ", "バージョンと種類", "メモリとポート", "確認して作る"];

function viewCreate(root) {
  if (!S.wiz) S.wiz = { step: 0, name: "みんなのMODサーバー", picked: new Map(), mc: null, loader: "auto", memory: null, port: null, plan: null, defaults: null };
  const w = S.wiz;
  root.innerHTML = `<div class="page"><h1>新しいサーバーを作る</h1>
    <div class="wizard-steps">${WSTEPS.map((t, i) => `<button class="wstep ${i < w.step ? "done" : i === w.step ? "now" : ""}" data-ws="${i}" ${i < w.step ? "" : "disabled"}><i>${i < w.step ? "✓" : i + 1}</i>${t}</button>`).join("")}</div>
    <div data-wbody></div></div>`;
  $$("[data-ws]", root).forEach((b) => (b.onclick = () => { w.step = +b.dataset.ws; viewCreate(root); }));
  [wizName, wizMods, wizVersion, wizMemory, wizConfirm][w.step]($("[data-wbody]", root), root);
}

function wizNext(root, step) { S.wiz.step = step; viewCreate(root); $("#main").scrollTop = 0; }

function wizName(body, root) {
  body.innerHTML = `<div class="grid2"><div class="panel">
      <label class="f" for="wname">サーバーの名前</label>
      <input type="text" id="wname" value="${esc(S.wiz.name)}" maxlength="40">
      <p class="hint">あとから変えられます。友達のサーバー一覧にもこの名前が出ます。</p>
      <div class="wizard-foot"><span></span><button class="btn primary big" data-next>次へ: MODを選ぶ</button></div>
    </div>
    <div class="stack"><div class="note"><b>サーバーとは ${help("server")}</b>みんなが同じワールドで遊ぶための「会場」です。あなたのPCで動かし、友達はインターネット経由で入ります。</div>
      <div class="note"><b>このあとの流れ</b><ul><li>入れたいMODを選ぶ（あとからでもOK）</li><li>選んだMODが一番そろうバージョンを、自動でおすすめ</li><li>メモリとポート番号を決める（おすすめが入っています）</li></ul></div></div></div>`;
  const next = () => { S.wiz.name = $("#wname").value.trim() || "マイクラサーバー"; wizNext(root, 1); };
  $("[data-next]", body).onclick = next;
  $("#wname").addEventListener("keydown", (e) => { if (e.key === "Enter") next(); });
}

function wizMods(body, root) {
  const w = S.wiz;
  body.innerHTML = `<p class="muted" style="margin-top:0">入れたいMODの □ を押して選びます。カードをクリックすると、日本語の説明や画像、使えるバージョンが見られます。バージョンを決めている場合は、下の「マイクラのバージョンを指定」で、そのバージョンで使えるMODだけを表示できます。決めていない場合は、そのまま選んでください（次の画面で、一番そろうバージョンを探します）。</p>
    <div data-browser></div>
    <div class="tray"><div class="names" data-names></div>
      <button class="btn ghost" data-back>戻る</button>
      <button class="btn" data-skip>MODなしで進む</button>
      <button class="btn primary big" data-next>次へ</button></div>`;
  const upd = () => {
    const n = w.picked.size;
    $("[data-names]", body).innerHTML = n ? `<b>${n}個</b> 選んでいます: ${[...w.picked.values()].slice(0, 10).map(esc).join("、")}${n > 10 ? " …" : ""}` : "まだ選んでいません（あとからでも入れられます）";
    $("[data-next]", body).disabled = !n;
    $("[data-next]", body).textContent = n ? `次へ（${n}個）` : "次へ";
  };
  modBrowser($("[data-browser]", body), { picked: w.picked, onChange: upd, version: { mc: w.mc || "", loader: w.loader && w.loader !== "auto" ? w.loader : "" }, onVersion: (mc, ld) => { w.mc = mc || null; w.loader = ld || "auto"; w.plan = null; } });
  upd();
  $("[data-back]", body).onclick = () => wizNext(root, 0);
  $("[data-skip]", body).onclick = () => { w.picked.clear(); w.plan = null; wizNext(root, 2); };
  $("[data-next]", body).onclick = () => { w.plan = null; wizNext(root, 2); };
}

async function wizVersion(body, root) {
  const w = S.wiz;
  const tok = (w.tok = (w.tok || 0) + 1);   // 「待たずに選ぶ」で作り直したとき、古い待ちが画面を上書きしないようにする
  body.innerHTML = `<div class="empty">選んだMODが使えるバージョンを調べています…</div>`;
  let versions = [];
  try {
    versions = (await api("mc_versions")).versions;
    if (w.picked.size && !w.plan) {
      const slow = setTimeout(() => { const el = $(".empty", body); if (el) el.innerHTML = `選んだMODが使えるバージョンを調べています…<br><span class="small muted">MODが多いと1分ほどかかります。</span> <button class="linkbtn" data-skipplan>待たずに自分で選ぶ</button>`; const b = $("[data-skipplan]", body); if (b) b.onclick = () => { w.plan = { rows: [], total: 0, errors: ["（調べるのをやめました）"], per_mod: {}, support: {} }; wizVersion(body, root); }; }, 6000);
      try { const r = await api("plan", { ids: [...w.picked.keys()], titles: Object.fromEntries(w.picked) }); if (w.tok !== tok) return; w.plan = r; } finally { clearTimeout(slow); }
    }
  } catch (e) {
    if (w.tok !== tok) return; body.innerHTML = `<div class="empty">調べられませんでした: ${esc(e.message)}</div>`; return; }
  const plan = w.plan;
  if (!w.mc) {
    const top = plan && plan.rows[0];
    w.mc = top ? top.mc : versions[0];
    w.loader = top ? top.loader : "auto";
  }
  const n = w.picked.size;
  const row = (r) => `<tr class="clickable ${r.mc === w.mc && r.loader === w.loader ? "sel" : ""}" data-pick="${esc(r.mc)}|${r.loader}">
      <td><b>${esc(r.mc)}</b></td><td>${esc(r.label)}</td>
      <td style="width:38%"><div class="row" style="gap:8px"><div class="meter grow"><i style="width:${(r.count / plan.total) * 100}%;background:${r.count === plan.total ? "var(--grass)" : "var(--gold)"}"></i></div><b>${r.count}/${plan.total}</b></div></td>
      <td class="small muted">${r.ng_titles.length ? "使えない: " + r.ng_titles.slice(0, 4).map(esc).join("、") + (r.ng_titles.length > 4 ? " …" : "") : '<span style="color:var(--ok-text)">全部使えます</span>'}</td></tr>`;
  body.innerHTML = `<div class="stack">
    ${n ? `<div class="panel"><h3>おすすめの組み合わせ ${help("version")}</h3><p class="small muted">選んだ ${n}個のMODが、いくつ使えるかの一覧です。行をクリックすると選べます。</p>
      ${plan.rows.length ? `<table class="tbl" style="margin-top:8px"><thead><tr><th>マイクラ</th><th>MODの種類</th><th>使えるMOD</th><th></th></tr></thead><tbody>${plan.rows.map(row).join("")}</tbody></table>`
        : `<div class="note warn"><b>全部がそろう組み合わせが見つかりませんでした</b></div>`}
      ${plan.errors.length ? `<p class="small muted">調べられなかったMOD: ${plan.errors.map(esc).join("、")}</p>` : ""}</div>` : `<div class="note">MODを選んでいないので、新しいバージョンをおすすめしています。</div>`}
    <div class="panel"><h3>自分で選ぶ</h3>
      <div class="form-grid" style="margin-top:12px">
        <div class="lbl">マイクラのバージョン ${help("version")}</div>
        <div><select data-mc>${versions.map((v) => `<option ${v === w.mc ? "selected" : ""}>${esc(v)}</option>`).join("")}</select><div class="hint">友達も同じバージョンで遊びます（友達用パックで自動でそろいます）。</div></div>
        <div class="lbl">サーバーの種類 ${help("loader")}</div>
        <div><select data-loader></select><div class="hint" data-lnote></div></div>
      </div>
      <div data-check style="margin-top:12px"></div>
    </div>
    <div class="wizard-foot"><button class="btn ghost" data-back>戻る</button><button class="btn primary big" data-next>次へ: メモリとポート</button></div></div>`;
  const mcSel = $("[data-mc]", body), ldSel = $("[data-loader]", body);
  const labels = { auto: "おまかせ（選んだMODに合わせる）", fabric: "Fabric（MOD）", neoforge: "NeoForge（MOD）", forge: "Forge（MOD）",
    paper: "Paper（プラグイン・軽い）", purpur: "Purpur（プラグイン・設定が多い）", vanilla: "バニラ（公式そのまま）" };
  const notes = { auto: "選んだMODに一番合う種類を自動で選びます。", fabric: "軽くて、新しいバージョンに早く対応です。軽量化MODが豊富です。", neoforge: "1.20.2 以降の大型MOD（工業・魔法など）が多いです。",
    forge: "1.20.1 以前の大型MODが多いです。古くからある定番です。", vanilla: "MODを使わない、普通のマイクラのサーバーです。",
    paper: "公式より軽く動くサーバーです。MODは使えないが、プラグイン（サーバーだけに入れる追加機能）が使えます。友達は普通のマイクラで入れます。",
    purpur: "Paper をもとに、さらに細かい設定ができるサーバーです。プラグインが使えます。友達は普通のマイクラで入れます。" };
  const checkCombo = () => {
    const out = $("[data-check]", body);
    if (!n || !plan || w.loader === "auto" || w.loader === "vanilla" || PLUGIN_LOADERS.includes(w.loader)) {
      out.innerHTML = (w.loader === "vanilla" || PLUGIN_LOADERS.includes(w.loader)) && n
        ? `<div class="note warn"><b>${labels[w.loader]}ではMODは使えません</b>選んだMODは入りません。${PLUGIN_LOADERS.includes(w.loader) ? "プラグインは、作ったあとに「プラグイン」タブから入れられます。" : ""}</div>` : "";
      return;
    }
    const ng = [...w.picked.entries()].filter(([id]) => !((plan.support[id] || {})[w.loader] || []).includes(w.mc)).map(([, t]) => t);
    out.innerHTML = ng.length ? `<div class="note warn"><b>この組み合わせでは使えないMODがあります（${ng.length}個）</b>${ng.map(esc).join("、")}<br>使えないMODは入りません。</div>`
      : `<div class="note ok"><b>選んだMODが全部使えます</b></div>`;
  };
  const loadLoaders = async () => {
    ldSel.innerHTML = `<option>調べています…</option>`;
    let sup = ["fabric", "neoforge", "forge", "paper", "purpur", "vanilla"];
    try { sup = (await api("supported_loaders", { mc: w.mc })).loaders; } catch (e) { /* そのまま */ }
    const list = ["auto", ...sup];
    if (!list.includes(w.loader)) w.loader = "auto";
    ldSel.innerHTML = list.map((l) => `<option value="${l}" ${l === w.loader ? "selected" : ""}>${labels[l] || l}</option>`).join("");
    $("[data-lnote]", body).textContent = notes[w.loader] || "";
    checkCombo();
  };
  mcSel.onchange = () => { w.mc = mcSel.value; highlight(); loadLoaders(); };
  ldSel.onchange = () => { w.loader = ldSel.value; $("[data-lnote]", body).textContent = notes[w.loader] || ""; highlight(); checkCombo(); };
  const highlight = () => $$("[data-pick]", body).forEach((tr) => tr.classList.toggle("sel", tr.dataset.pick === `${w.mc}|${w.loader}`));
  $$("[data-pick]", body).forEach((tr) => (tr.onclick = () => { [w.mc, w.loader] = tr.dataset.pick.split("|"); mcSel.value = w.mc; highlight(); loadLoaders(); }));
  loadLoaders();
  $("[data-back]", body).onclick = () => wizNext(root, 1);
  $("[data-next]", body).onclick = () => wizNext(root, 3);
}

async function wizMemory(body, root) {
  const w = S.wiz;
  body.innerHTML = `<div class="empty">計算しています…</div>`;
  let d, adv;
  try {
    d = w.defaults || (w.defaults = await api("create_defaults"));
    adv = await api("memory_advice", { mods: w.picked.size * 1.4, players: 10, loader: w.loader === "auto" ? "fabric" : w.loader });
  } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (!w.memory) w.memory = adv.recommended;
  if (!w.port) w.port = d.port;
  body.innerHTML = `<div class="stack">
    <div class="panel"><h3>メモリ ${help("memory")}</h3>
      <p class="small muted">選んだMOD（前提MODもふくめて約${Math.round(w.picked.size * 1.4)}個）と最大10人を目安に、おすすめを入れています。あとから「設定」で変えられます。</p>
      <div data-mem style="margin-top:10px">${memoryBlock(adv, w.memory, { selfPlay: S.settings.self_play })}</div>
      <label class="check small" style="margin-top:10px"><input type="checkbox" data-selfplay ${S.settings.self_play ? "checked" : ""}> このPCで自分もマイクラを遊ぶ（その分のメモリを残す）</label>
    </div>
    <div class="panel"><h3>ポート番号 ${help("port")}</h3>
      <div class="row" style="margin-top:10px"><input type="number" data-port value="${w.port}" style="width:150px"><button class="btn sm" data-pchk>空いているか確認</button><span data-pout class="small"></span></div>
      <ul class="small muted" style="padding-left:1.3em"><li>ポート番号は、PCの中の「部屋番号」です。マイクラは普通 25565 を使います。</li>
        <li>ほかのサーバーと同時に動かすときは、違う番号にします（自動で空いている番号を入れています）。</li>
        <li>友達を呼ぶ方法（playit.gg やポート開放）は、作ったあとに「友達を呼ぶ」タブで案内します。</li></ul>
    </div>
    <div class="wizard-foot"><button class="btn ghost" data-back>戻る</button><button class="btn primary big" data-next>次へ: 確認</button></div></div>`;
  bindMemory($("[data-mem]", body), adv, S.settings.self_play, (v) => (w.memory = v));
  $("input[type=range]", body).addEventListener("input", (e) => (w.memory = +e.target.value));
  $("[data-selfplay]", body).onchange = async (e) => { await api("set_settings", { self_play: e.target.checked }); S.settings.self_play = e.target.checked; w.memory = null; wizMemory(body, root); };
  $("[data-pchk]", body).onclick = async () => {
    const out = $("[data-pout]", body);
    try { const r = await api("port_check", { port: $("[data-port]", body).value }); out.innerHTML = r.free && !r.used_by ? '<span class="badge ok">空いています</span>' : r.used_by ? `<span class="badge warn">「${esc(r.used_by)}」と同じ番号です（同時に動かさなければ、そのままでOK）</span>` : '<span class="badge ng">ほかのソフトが使っています</span>'; } catch (e) { out.textContent = e.message; }
  };
  $("[data-back]", body).onclick = () => { w.port = +$("[data-port]", body).value; wizNext(root, 2); };
  $("[data-next]", body).onclick = () => {
    const port = +$("[data-port]", body).value;
    if (!(port >= 1024 && port <= 65535)) { toast("ポート番号は 1024〜65535 の数字にしてください", "error"); return; }
    w.port = port;
    wizNext(root, 4);
  };
}

function wizConfirm(body, root) {
  const w = S.wiz;
  const ld = { auto: "おまかせ", fabric: "Fabric", neoforge: "NeoForge", forge: "Forge", paper: "Paper（プラグイン）", purpur: "Purpur（プラグイン）", vanilla: "バニラ（MODなし）" }[w.loader];
  body.innerHTML = `<div class="panel"><table class="matrix">
      <tr><th>名前</th><td>${esc(w.name)}</td></tr>
      <tr><th>マイクラ</th><td>${esc(w.mc)}</td></tr><tr><th>MODの種類</th><td>${esc(ld)}</td></tr>
      <tr><th>MOD</th><td>${w.picked.size ? `${w.picked.size}個: ${[...w.picked.values()].map(esc).join("、")}` : "なし（あとから入れられます）"}</td></tr>
      <tr><th>メモリ</th><td>${gb(w.memory)}</td></tr><tr><th>ポート番号</th><td>${w.port}</td></tr></table>
    <p class="muted small" style="margin-top:14px">「作る」を押すと、マイクラのサーバー本体・MODの土台・Java（必要なら）・選んだMODを自動でダウンロードします。数分かかることがあります。</p>
    <div class="wizard-foot"><button class="btn ghost" data-back>戻る</button><button class="btn primary big" data-go>サーバーを作る</button></div></div>`;
  $("[data-back]", body).onclick = () => wizNext(root, 3);
  $("[data-go]", body).onclick = async () => {
    try {
      const r = await runJob(api("create_server", { name: w.name, mc: w.mc, loader: w.loader, memory_mb: w.memory, port: w.port,
        mods: [...w.picked.keys()], titles: Object.fromEntries(w.picked) }), "サーバーを作っています");
      S.wiz = null;
      await refreshState();
      let html = `<p>次は「▶ 起動」を押してみましょう。そのあと「友達を呼ぶ」→「メンバー」→「友達用パック」の順に進めます。</p>`;
      if (r.mods) {
        if (r.mods.installed.length) html += `<p><b>入れたMOD</b>: ${r.mods.installed.map(esc).join("、")}</p>`;
        if (r.mods.client_only.length) html += `<p><b>遊ぶ人だけのMOD</b>（友達用パックに入ります）: ${r.mods.client_only.map(esc).join("、")}</p>`;
        if (r.mods.errors.length) html += `<div class="note warn"><b>入れられなかったMOD</b><ul>${r.mods.errors.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
        if (r.mods.warnings.length) html += `<div class="note warn"><b>相性の注意</b><ul>${r.mods.warnings.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
      }
      go("server", { server: r.server, tab: "home" });
      info("サーバーができました", html);
    } catch (e) { if (!e.shown) fail(e); }
  };
}

// ================================================================ MODパックから作る
function viewModpack(root) {
  let chosen = null, upload_ = null;
  root.innerHTML = `<div class="page"><h1>MODパックから作る</h1>
    <p class="lead">MODパックは、たくさんのMODと設定がセットになったものです。選ぶだけで、そのまま遊べるサーバーができます。友達用パックもそのまま作れます。</p>
    <div class="mod-tools" style="margin-top:18px"><input type="search" placeholder="MODパックを検索（英語）" data-q><button class="btn" data-s>検索</button>
      <label class="btn">ファイルから（.mrpack）<input type="file" accept=".mrpack" hidden data-file></label></div>
    <div class="mod-grid" data-grid></div>
    <div class="tray"><div class="names" data-names>MODパックを選んでください</div>
      <label class="small">メモリ</label><select data-mem style="width:auto">${[4, 5, 6, 7, 8, 10, 12].map((g) => `<option value="${g * 1024}" ${g === 6 ? "selected" : ""}>${g}GB</option>`).join("")}</select>
      ${help("memory")}<button class="btn primary big" data-go disabled>このMODパックで作る</button></div></div>`;
  const grid = $("[data-grid]", root);
  const hits = new Map();
  const load = async (q = "") => {
    grid.innerHTML = `<div class="empty">探しています…</div>`;
    try {
      const r = await api("modpacks_search", { q });
      hits.clear();
      r.hits.forEach((h) => hits.set(h.id, h));
      grid.innerHTML = r.hits.map((h) => modCard(h, { pickable: true, picked: chosen === h.id })).join("") || `<div class="empty">見つかりませんでした</div>`;
      autoTranslate(grid);
    } catch (e) { grid.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  };
  const pick = (id) => {
    chosen = chosen === id ? null : id; upload_ = null;
    $$(".mod", grid).forEach((c) => c.classList.toggle("picked", c.dataset.id === chosen));
    const h = hits.get(chosen);
    $("[data-names]", root).innerHTML = h ? `<b>${esc(h.title)}</b> を選んでいます${h.versions_text ? `（${esc(h.loaders.join("・"))}）` : ""}` : "MODパックを選んでください";
    $("[data-go]", root).disabled = !chosen;
  };
  grid.addEventListener("click", (e) => {
    const c = e.target.closest(".mod");
    if (!c) return;
    if (e.target.closest(".pick")) pick(c.dataset.id);
    else openDetail(c.dataset.id, { picked: { has: (id) => id === chosen, set: (id) => pick(id), delete: () => pick(chosen) }, onPick: () => {} });
  });
  $("[data-s]", root).onclick = () => load($("[data-q]", root).value.trim());
  $("[data-q]", root).addEventListener("keydown", (e) => { if (e.key === "Enter") load(e.target.value.trim()); });
  $("[data-file]", root).onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try { upload_ = await upload(f); chosen = null; $$(".mod", grid).forEach((c) => c.classList.remove("picked")); $("[data-names]", root).innerHTML = `<b>${esc(f.name)}</b> を使います`; $("[data-go]", root).disabled = false; } catch (err) { fail(err); }
  };
  $("[data-go]", root).onclick = async () => {
    try {
      const r = await runJob(api("create_from_pack", { memory_mb: +$("[data-mem]", root).value, pack_id: chosen, upload: upload_ && upload_.path }), "MODパックからサーバーを作っています");
      await refreshState();
      go("server", { server: r.server, tab: "home" });
      info("サーバーができました", "<p>「▶ 起動」を押してみましょう。はじめての起動は数分かかります。</p>");
    } catch (e) { if (!e.shown) fail(e); }
  };
  load();
}

// ================================================================ 友達のサーバーに参加
function viewJoin(root) {
  const host = S.joinInfo && S.joinInfo.local_server;
  root.innerHTML = host
    ? `<div class="page"><h1>自分のマイクラに入れる（サーバーを立てた人用）</h1>
    <div class="note warn" style="margin-top:12px"><b>まだ入っていません。下の青い「入れる」ボタンを押してください。</b>押すと、自分のマイクラ（公式ランチャー）に、サーバーと同じMODの「（マルチ用）」が追加されます。</div>
    <p class="lead">この画面は「友達のサーバーに参加」と同じ画面を使っています。あなたが友達のサーバーに参加するわけではありません。</p>
    <div data-jbody style="margin-top:20px"></div></div>`
    : `<div class="page"><h1>友達のサーバーに参加する</h1>
    <p class="lead">友達からもらったファイル（.mrpack）か、アドレスで、同じMODがあなたのマイクラに入ります。ふだんのマイクラとは別の場所に入るので、混ざりません。</p>
    <ol class="fsteps"><li class="now"><b>①</b> アドレスを貼る</li><li><b>②</b> 「入れる」を押す</li><li><b>③</b> 「▶ 遊ぶ」を押す</li></ol>
    <div class="note" style="margin-top:10px"><b>サーバーを立てた人（ホスト）は、ここではなく</b>サーバーの「友達用パック」タブから、自分のマイクラに入れます。詳しくは左の「入り方ガイド」へ。</div>
    <div data-jbody style="margin-top:20px"></div></div>`;
  const body = $("[data-jbody]", root);
  if (S.joinInfo) { const ji = S.joinInfo; S.joinInfo = null; joinStep2(body, ji); return; }
  body.innerHTML = `<div data-joined></div><h2 style="margin:6px 0 12px">新しく参加する</h2>
    <div class="panel" style="margin-bottom:16px"><h3>アドレス（または招待リンク）で参加</h3>
      <p class="small muted">友達もこのソフトでサーバーを立てていれば、アドレスを入れるだけで友達用パックを自動でもらえます。</p>
      <div class="inline-field" style="margin-top:8px;max-width:640px"><input type="text" data-jaddr value="${esc(S.joinAddr || "")}" placeholder="例: xxxx.joinmc.link　または　mcmh://join/…"><button class="btn primary" data-jgo>参加する</button></div></div>
    <details class="panel" style="margin-bottom:16px"><summary style="cursor:pointer;font-weight:700">アドレスではなく、ファイル（.mrpack）をもらった人はこちら</summary>
    <div class="grid2" style="margin-top:12px"><div>
      <ol class="steps"><li><b>友達からファイルをもらう</b><span class="muted">「〇〇.mrpack」という名前のファイルです。</span></li>
        <li><b>公式ランチャーで一度マイクラを起動しておく</b><span class="muted">はじめての人だけです。マイクラ Java版（正規版）が必要です。</span></li>
        <li><b>下のボタンでファイルを選ぶ</b></li></ol>
      <label class="btn primary big">ファイルを選ぶ（.mrpack）<input type="file" accept=".mrpack" hidden data-file></label></div>
    <div class="note"><b>Prism Launcher や Modrinth App を使っている人は</b>そのアプリで .mrpack を開くだけでも入ります。このソフトは、公式ランチャーで遊ぶ人向けです。</div></div></details>`;
  $("[data-file]", body).onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try { const u = await upload(f); joinStep2(body, await api("join_info", { path: u.path })); } catch (err) { fail(err); }
  };
  const jgo = async () => {
    let a = $("[data-jaddr]", body).value.trim();
    const m = a.match(/^mcmh:\/\/join\/?(?:\?addr=)?(.+)$/i);
    if (m) a = decodeURIComponent(m[1]).replace(/\/$/, "");
    if (!a) return toast("アドレスを入れてください", "warn");
    try { const ji = await runJob(api("join_from_address", { address: a }), "友達用パックをもらっています"); S.joinAddr = ""; joinStep2(body, ji); } catch (err) { if (!err.shown) fail(err); }
  };
  $("[data-jgo]", body).onclick = jgo;
  $("[data-jaddr]", body).addEventListener("keydown", (e) => { if (e.key === "Enter") jgo(); });
  if (S.joinAddr) { const a = S.joinAddr; S.joinAddr = ""; $("[data-jaddr]", body).value = a; jgo(); }
  renderJoined($("[data-joined]", body));
}

async function renderJoined(el) {
  let list;
  try { list = (await api("joined_list")).joined; } catch (e) { return; }
  if (!list.length || !el.isConnected) { el.innerHTML = ""; return; }
  el.innerHTML = `<h2 style="margin-bottom:12px">参加しているサーバー</h2>
    <div class="stack" style="margin-bottom:26px">${list.map((j) => `<div class="panel" data-j="${esc(j.key)}"><div class="row">
      <div class="grow"><h3>${esc(j.name)}</h3><div class="small muted">マイクラ ${esc(j.mc || "?")}・パック ${esc(j.version || "?")}${j.address ? "・" + esc(j.address) : ""}</div>
        <div class="small" data-jst style="margin-top:4px"><span class="muted">新しいパックがないか確かめています…</span></div><div data-jch></div></div>
      <button class="btn go big" data-play>▶ 遊ぶ</button><button class="btn primary" data-upd hidden>今すぐ更新</button>
      <button class="btn ghost sm" data-rm>一覧から消す</button></div></div>`).join("")}</div>`;
  for (const j of list) {
    const card = $(`[data-j="${CSS.escape(j.key)}"]`, el);
    $("[data-play]", card).onclick = async () => {
      try {
        const r = await runJob(api("joined_play", { key: j.key }), "遊ぶ準備をしています", { note: "新しいパックがあれば入れてから、マイクラのランチャーを開きます。" });
        await info(r.updated ? "新しいパックを入れました" : "準備できました", `${precheckHtml(r.check)}<p>${r.opened ? "ランチャーを開きました。" : "マイクラのランチャーを開いてください。"}「${esc(r.name)}（マルチ用）」を選んで「プレイ」→「マルチプレイ」からサーバーに入ってください。</p>`);
        renderJoined(el);
      } catch (e) { if (!e.shown) fail(e); }
    };
    $("[data-upd]", card).onclick = async () => {
      try { await runJob(api("joined_update", { key: j.key }), "新しいパックを入れています"); toast("新しくしました"); renderJoined(el); } catch (e) { if (!e.shown) fail(e); }
    };
    $("[data-rm]", card).onclick = async () => {
      if (!(await ask("一覧から消す", `<p>「${esc(j.name)}」をこの一覧から消しますか？（入れたMODやランチャーの起動構成は残ります）</p>`, "消す"))) return;
      await api("joined_remove", { key: j.key }).catch(fail); renderJoined(el);
    };
  }
  try {
    const res = (await api("joined_check")).results;
    for (const j of list) {
      const card = el.isConnected && $(`[data-j="${CSS.escape(j.key)}"]`, el);
      const r = res[j.key];
      if (!card || !r) continue;
      const cls = { latest: "ok", update: "warn", offline: "", unsupported: "", other: "ng" }[r.status] || "";
      $("[data-jst]", card).innerHTML = `<span class="badge ${cls}">${esc(r.text)}</span>`;
      $("[data-upd]", card).hidden = r.status !== "update";
      $("[data-jch]", card).innerHTML = r.status === "update" ? changesHtml(r.changes) : "";
      card.classList.toggle("has-update", r.status === "update");
    }
  } catch (e) { /* 何もしない */ }
}

function joinStep2(body, ji) {
  const opt = ji.files.filter((f) => f.need === "optional"), req = ji.files.filter((f) => f.need !== "optional");
  body.innerHTML = `<div class="grid2">
    <div class="stack">
      <div class="panel"><h2>${esc(ji.name)}</h2><p class="muted" style="margin:4px 0 0">マイクラ ${esc(ji.mc)}・${esc(ji.loader_label)}・MOD ${ji.files.length}個</p></div>
      ${ji.risks && (ji.risks.extra_code.length || ji.risks.bad_urls.length) ? `<div class="note warn"><b>Modrinth 以外のファイルが入っています</b>
        ${ji.risks.extra_code.length ? `<p>パックの中に、Modrinth で確認できないプログラムのファイルが ${ji.risks.extra_code.length}個あります（${ji.risks.extra_code.slice(0, 3).map((x) => esc(x.split("/").pop())).join("、")}${ji.risks.extra_code.length > 3 ? " …" : ""}）。<b>信頼できる友達からもらったパックだけ</b>入れてください。</p>` : ""}
        ${ji.risks.bad_urls.length ? `<p>安全な場所以外からダウンロードするファイルが ${ji.risks.bad_urls.length}個あるので、それは入れません。</p>` : ""}</div>` : ""}
      <div class="panel">
        <label class="f">マイクラのフォルダ</label>
        <input type="text" data-dir value="${esc(ji.minecraft_dir)}">
        ${ji.minecraft_dir_exists ? `<p class="hint">見つかりました。普通はこのままでOKです。</p>` : `<div class="note warn" style="margin-top:8px"><b>マイクラのフォルダが見つかりません</b>公式ランチャーで一度マイクラを起動してから、もう一度ためしてください。</div>`}
        <label class="f" style="margin-top:14px">マイクラに使うメモリ ${help("memory")}</label>
        <div class="row"><select data-mem style="width:auto">${[3, 4, 5, 6, 8, 10, 12].map((g) => `<option value="${g}" ${g === ji.mem_gb ? "selected" : ""}>${g}GB</option>`).join("")}</select><span class="hint">MODが多いなら 6GB 以上がおすすめ（PCのメモリの半分まで）</span></div>
      </div>
      <button class="btn primary big" data-go>${ji.local_server ? "👉 入れる（自分のマイクラに追加します）" : "入れる"}</button>
      <div data-done></div>
    </div>
    <div><h3>入るMOD</h3><p class="small muted">🔒 はサーバーに入るのに必要なMODです。スイッチのMODは「なくてもOK」なので、PCが重いなら外せます。</p>
      <div class="list" style="max-height:520px;overflow-y:auto">
        ${opt.map((f) => `<div class="item noselect">${slotImg(f.icon, f.title, "sm")}<div style="min-width:0"><div class="t">${esc(f.title)}</div><div class="s">なくてもOK</div></div><label class="switch"><input type="checkbox" checked data-opt="${esc(f.path)}" aria-label="${esc(f.title)} を入れる"><span></span></label></div>`).join("")}
        ${req.map((f) => `<div class="item noselect">${slotImg(f.icon, f.title, "sm")}<div style="min-width:0"><div class="t">${esc(f.title)}</div><div class="s">必要</div></div><span aria-label="外せません">🔒</span></div>`).join("")}
      </div></div></div>`;
  $("[data-go]", body).onclick = async () => {
    const skip = $$("[data-opt]", body).filter((c) => !c.checked).map((c) => c.dataset.opt);
    try {
      const r = await runJob(api("join", { path: ji.path, skip, mem_gb: +$("[data-mem]", body).value, minecraft_dir: $("[data-dir]", body).value.trim(), local_server: ji.local_server || null }), "マイクラにMODセットを入れています");
      $("[data-done]", body).innerHTML = `<div class="note ok" style="margin-top:16px"><b>準備できました</b>
        <ol class="steps" style="margin-top:12px">${ji.local_server ? '<li><b>サーバーが「起動完了」になっているか確認</b><span class="muted">「ホーム」タブで確認してください。まだなら「▶ 起動」を押して待ちます。</span></li>' : ""}
        <li><b>公式ランチャーを開く</b><span class="muted">すでに開いていたら、一度閉じて開き直してください（開いたままだと新しい構成が出ません）。</span></li>
        <li><b>「${esc(r.name)}（マルチ用）」を選んで「プレイ」</b><span class="muted">「プレイ」ボタンのとなりの▼で選べます。</span></li>
        <li><b>「マルチプレイ」を開く</b><span class="muted">サーバーが最初から入っています。選んで「サーバーに接続」。${ji.local_server ? "つながらないときは「ダイレクト接続」で <b>localhost</b> と入れます。" : ""}</span></li></ol>
        <button class="btn primary" data-launch>公式ランチャーを開く</button>
        <p class="small" style="margin-top:12px">${r.update ? "次からは「友達のサーバーに参加」の画面の「▶ 遊ぶ」を押すだけです。MODが変わっても自動で新しくなります。" : "このパックは自動更新に対応していません。MODが変わったら、新しいパックをもらって入れ直してください。"}</p></div>`;
      $("[data-launch]", body).onclick = () => api("open_launcher").catch(fail);
    } catch (e) { if (!e.shown) fail(e); }
  };
}

// ================================================================ ガイド・用語集・設定
function viewGuide(root) {
  const t = S.guideTab || "host";
  root.innerHTML = `<div class="page"><h1>入り方ガイド</h1>
    <p class="lead">まず、自分がどちらか選んでください。</p>
    <div class="tabs">${[["host", "🛠 ホスト（サーバーを立てる人）"], ["friend", "🎮 参加者（友達）"], ["how", "💡 しくみ"], ["trouble", "入れないとき"]].map(([k, l]) => `<button class="tab" data-g="${k}" aria-selected="${t === k}">${l}</button>`).join("")}</div>
    <div class="tab-body" data-gb></div></div>`;
  $$("[data-g]", root).forEach((b) => (b.onclick = () => { S.guideTab = b.dataset.g; viewGuide(root); }));
  const gb_ = $("[data-gb]", root);
  const steps = (list) => `<ol class="steps">${list.map(([b, m]) => `<li><b>${b}</b><span class="muted">${m}</span></li>`).join("")}</ol>`;
  if (t === "host") {
    gb_.innerHTML = `<div class="note ok" style="margin-bottom:14px"><b>ホストがやること：</b>サーバーを作る → 友達を呼ぶ準備 → 友達に渡す → 自分も遊ぶ。<b>最初の準備は1回だけ</b>です。</div>
      <div class="grid2"><div>
      <h3>① 準備（最初の1回）</h3>
      ${steps([
        ["サーバーを作る", "左の「新しいサーバーを作る」。MODを選ぶと、そろうバージョンを教えてくれます。"],
        ["起動して確かめる", "「▶ 起動」→「起動完了」まで待ちます。EULA と Windows の許可は、両方OKです。"],
        ["アドレスを作る（playit.gg）", "「友達を呼ぶ」タブの手順どおりに進め、できたアドレスを<b>保存</b>します。ポート開放は不要です。"],
        ["友達の名前を登録", "「メンバー・BAN」タブで、友達の<b>マイクラの名前</b>を追加します（ないと入れません）。"]])}
      <h3 style="margin-top:18px">② 友達に渡す</h3>
      ${steps([
        ["友達用パックを作る", "「友達用パック」タブ →「パックを作る」。<b>アドレスを保存したあと</b>に作ってください。"],
        ["パックを送る", "できた .mrpack と、メッセージをコピーして送ります。最初の1回だけでOKです。"]])}
      <h3 style="margin-top:18px">③ 自分も遊ぶ</h3>
      ${steps([
        ["「友達用パック」タブの「② 自分のマイクラに入れる準備をする」", "押すと別の画面に移ります。<b>まだ入っていません。</b>"],
        ["青い「入れる」を押す", "「準備できました」と出たら完了です。"],
        ["公式ランチャーで「（マルチ用）」を選んで「プレイ」", "ランチャーを開き直してからにしてください。「マルチプレイ」で接続（出ないときは localhost）。"]])}
      </div>
      <div class="stack"><div class="note"><b>遊ぶ間は</b>PC・サーバー・playit の黒い画面を<b>開いたまま</b>にします。</div>
        <div class="note"><b>MODを変えたら</b>サーバーを再起動するだけです。パックは自動で新しくなります。</div>
        <div class="note warn"><b>data フォルダは渡さない</b>設定（Webhook など）が入っています。配るのは exe のフォルダだけです。</div>
        <div class="note warn"><b>アドレスが変わったら</b>保存し直して、パックも作り直します。</div></div></div>`;
  } else if (t === "friend") {
    gb_.innerHTML = `<div class="note ok" style="margin-bottom:14px"><b>参加者がやること：</b>ホストから2つもらう（<b>ソフト</b>と<b>パック or アドレス</b>）→ 入れる → 遊ぶ。</div>
      <div class="grid2"><div>
      <h3>① 準備（最初の1回）</h3>
      ${steps([
        ["マイクラ Java版を用意", "正規版が必要です。公式ランチャーで<b>一度起動</b>して、遊べる状態にします。"],
        ["ホストに名前を伝える", "自分の<b>マイクラの名前</b>（プレイヤー名）。ホストが登録しないと入れません。"],
        ["このソフトを開く", "ホストからもらったフォルダの <b>OneClickServerMC.exe</b>（または start.bat）をダブルクリックしてください。"]])}
      <h3 style="margin-top:18px">② MODを入れる</h3>
      ${steps([
        ["「友達のサーバーに参加」を押す", "左のメニューにあります。"],
        ["パックを選ぶ", "<b>.mrpack をもらった</b>なら「ファイルを選ぶ」。<b>アドレスをもらった</b>なら、アドレスを入れて「参加する」。"],
        ["「入れる」を押す", "「準備できました」と出たら完了です。<b>マイクラのフォルダ</b>の欄が、自分のPCの場所か確認してください。"]])}
      <h3 style="margin-top:18px">③ 遊ぶ</h3>
      ${steps([
        ["公式ランチャーで「（マルチ用）」を選んで「プレイ」", "ランチャーが開いていたら、一度閉じて開き直します。"],
        ["「マルチプレイ」→ サーバーを選ぶ", "一覧に出ていなければ「サーバーを追加」でアドレスを入れます。"]])}
      </div>
      <div class="stack"><div class="note"><b>2回目からは</b>このソフトの「▶ 遊ぶ」を押すだけです。新しいパックがあれば自動で入れ替わります。</div>
        <div class="note"><b>入れないとき</b>サーバーが起動しているか、ホストに名前を登録してもらったかを確認してください。</div>
        <div class="note"><b>Prism Launcher などの人</b>.mrpack をそのアプリで開き、マルチプレイでアドレスを入れます。</div></div></div>`;
  } else if (t === "how") {
    gb_.innerHTML = `<dl class="qa">
      <dt>アドレス</dt><dd>サーバーの住所です。友達はこれを使って、あなたのサーバーにつなぎます。<b>先に作って保存</b>しておきます。</dd>
      <dt>playit.gg</dt><dd>アドレスを無料で作るサービスです。<b>ルーターの設定（ポート開放）は要りません。</b>使っている間は、黒い画面を開いたままにします。</dd>
      <dt>友達用パック（.mrpack）</dt><dd>「同じMODを入れる一覧」と「サーバーのアドレス」が入った箱です。友達が入れると、MODがそろい、サーバー一覧にも最初から出ます。<b>アドレスがないまま作ると、一覧は空になります。</b></dd>
      <dt>（マルチ用）の起動構成</dt><dd>公式ランチャーに追加される、<b>このサーバー専用</b>の遊び方です。ふだんのマイクラとは別の場所に入るので、混ざりません。</dd>
      <dt>ホワイトリスト（メンバー）</dt><dd>入っていい人の名簿です。名簿にない人は、アドレスを知っていても入れません。</dd>
      <dt>ポート番号を変えたとき</dt><dd>playit は「どの番号の部屋に案内するか」を覚えています。サーバーのポートを変えたら、playit の <b>Local Port</b> も同じ番号に変えます（「友達を呼ぶ」タブに手順があります）。<b>全部のサーバーを同じ番号にしておく</b>と、この作業はいりません。</dd>
      <dt>前提MOD</dt><dd>ほかのMODを動かすために必要なMODです。MODを入れるときに、自動で一緒に入ります。</dd></dl>`;
  } else {
    gb_.innerHTML = `<p class="muted" style="margin-top:0">マイクラに出たメッセージから、原因と直し方を探せます。</p>
      <dl class="qa">${TROUBLE.map(([q, a]) => `<dt><code>${esc(q)}</code></dt><dd>${esc(a)}</dd>`).join("")}</dl>`;
  }
}

function viewGlossary(root) {
  root.innerHTML = `<div class="page"><h1>ことばの説明</h1><p class="lead">このソフトに出てくることばを、かんたんに説明します。画面の「?」ボタンでも同じ説明が見られます。</p>
    <div class="gloss" style="margin-top:20px">${GLOSSARY.map(([t, d]) => `<div><b>${esc(t)}</b><span class="small">${esc(d)}</span></div>`).join("")}</div></div>`;
}

function viewAppSettings(root) {
  const st = S.settings;
  root.innerHTML = `<div class="page"><h1>ソフトの設定</h1>
    <div class="panel" style="margin-top:20px"><div class="form-grid">
      <div class="lbl">見た目</div>
      <div>${stylePicker("theme-pick wide")}<div class="hint">「マイクラ風」はブロックやドット絵の見た目、「シンプル」は角の丸い、すっきりした見た目です。</div></div>
      <div class="lbl">画面の明るさ</div>
      <div>${themePicker("theme-pick wide")}<div class="hint">「自動」にすると、Windows の設定（ライト／ダーク）に合わせます。押すとすぐ変わります。</div></div>
      <div class="lbl">MODの説明を自動で日本語にする ${help("translate")}</div>
      <div><label class="switch"><input type="checkbox" data-ta ${st.translate_auto ? "checked" : ""}><span></span></label>
        <div class="hint">人気のMODには最初から日本語の説明があります。それ以外を無料の翻訳サービスで自動翻訳します。</div></div>
      <div class="lbl">上限のときは、予備の翻訳を使う</div>
      <div><label class="switch"><input type="checkbox" data-tf ${st.translate_fallback !== false ? "checked" : ""}><span></span></label>
        <div class="hint">無料の翻訳には1日の上限があります。上限が来たら、予備の翻訳サービス（Google の翻訳）で訳します。<b>送るのはMODの説明文だけ</b>です。OFFにすると、上限の間は英語のままになります。</div></div>
      <div class="lbl">翻訳用のメールアドレス（任意）</div>
      <div><input type="text" data-email value="${esc(st.translate_email || "")}" placeholder="入れなくてもOK"><div class="hint">入れると、1日に翻訳できる量が増えます（翻訳サービス MyMemory に送られます）。</div></div>
      <div class="lbl">表示の量</div>
      <div>
        <label class="row" style="gap:8px;align-items:flex-start"><input type="radio" name="uimode" value="simple" ${st.ui_mode !== "detail" ? "checked" : ""}><span><b>かんたん（おすすめ）</b><span class="hint" style="display:block">よく使うタブだけを出します（ホーム・MOD・友達を呼ぶ・友達用パック・メンバー・バックアップ）。</span></span></label>
        <label class="row" style="gap:8px;align-items:flex-start;margin-top:8px"><input type="radio" name="uimode" value="detail" ${st.ui_mode === "detail" ? "checked" : ""}><span><b>詳しい</b><span class="hint" style="display:block">起動テスト・重さ・管理コマンド・設定など、すべてのタブを出します。サーバーの画面の右上でも切り替えられます。</span></span></label>
      </div>
      <div class="lbl">入退出の音とお知らせ</div>
      <div><label class="switch"><input type="checkbox" data-sndset ${soundOn() ? "checked" : ""}><span></span></label>
        <div class="hint">誰かがサーバーに入ったとき・出たときに、「ピロン」と音を鳴らして、お知らせを出します（このソフトを開いているとき）。</div></div>
      <div class="lbl">「次にやること」の案内</div>
      <div><label class="switch"><input type="checkbox" data-nextshow ${st.hide_next_step ? "" : "checked"}><span></span></label>
        <div class="hint">ホーム画面の上に、いまやることを1つだけ大きく出します。OFFにすると隠れます。</div>
        <div class="row" style="margin-top:8px"><button class="btn sm" data-tour>はじめてガイドを、もう一度見る</button></div></div>
      <div class="lbl">サーバーのポート番号（部屋番号）</div>
      <div>
        <label class="row" style="gap:8px;align-items:flex-start"><input type="radio" name="pmode" value="same" ${st.port_mode !== "separate" ? "checked" : ""}><span><b>全部同じ番号にする（おすすめ）</b><span class="hint" style="display:block">新しく作るサーバーも、いつも <b>25565</b> になります。<b>同時に2つ動かさない</b>なら、これが一番かんたんです。友達に伝えるアドレスや、playit の設定を変えなくて済みます。</span></span></label>
        <label class="row" style="gap:8px;align-items:flex-start;margin-top:8px"><input type="radio" name="pmode" value="separate" ${st.port_mode === "separate" ? "checked" : ""}><span><b>サーバーごとに別の番号にする</b><span class="hint" style="display:block">25565、25566、25567…と増えていきます。<b>2つ以上を同時に動かしたい</b>ときに選びます。友達には「:25566」のように番号つきで伝える必要があります。</span></span></label>
        <div class="row" style="margin-top:10px"><button class="btn sm" data-pmapply>いまあるサーバーの番号も、この決め方にそろえる</button></div>
        <div class="hint">動いているサーバーは変えません（止めてからやり直してください）。番号が変わると、友達に伝えるアドレスと playit の「Local Port」も変える必要があります。</div>
        <div data-pmout></div>
      </div>
      <div class="lbl">このPCで自分も遊ぶ</div>
      <div><label class="switch"><input type="checkbox" data-sp ${st.self_play ? "checked" : ""}><span></span></label><div class="hint">メモリのおすすめを計算するときに、自分のマイクラの分を残します。</div></div>
      <div class="lbl">マイクラのフォルダ</div>
      <div><input type="text" data-dir value="${esc(st.minecraft_dir || "")}"><div class="hint">友達のサーバーに参加するときに使います。普通は変えなくてOKです。</div></div>
    </div><div class="row" style="margin-top:18px"><button class="btn primary" data-save>保存</button></div></div>
    <div class="panel" style="margin-top:18px"><h3>ソフトの更新</h3>
      <p class="small muted" style="max-width:76ch">いまの版: <b>${esc(S.version || "")}</b>。新しい版があるか調べて、ボタンを押すだけで更新できます。<b>サーバー・ワールド・設定は、そのまま残ります。</b></p>
      <div class="row"><button class="btn primary" data-updcheck>更新を確認する</button>
        <label class="row" style="gap:8px"><span class="switch"><input type="checkbox" data-updauto ${st.auto_update_check === false ? "" : "checked"}><span></span></span><span class="small">起動したとき、新しい版を自動で調べる</span></label></div>
      <div data-updres style="margin-top:12px">${updateNoticeHtml(S._updInfo)}</div>
      <p class="hint">${S.appExe ? "" : "いまは「Python」で動かしているので、自動の入れ替えはできません（新しい版の確認だけできます）。"}調べるときは、GitHub（配布場所）に接続します。送る個人情報はありません。</p></div>
    <div class="panel" style="margin-top:18px"><h3>困ったとき</h3>
      <p class="small muted" style="max-width:76ch">うまく動かないときに、原因を調べてもらうための情報を、zipにまとめます。<b>鍵・PCのユーザー名・IPアドレスは消します。</b>サーバーごとの診断は、サーバー画面の右上の「困ったとき診断」からできます。</p>
      <div class="row"><button class="btn" data-bundle>診断情報をまとめる（ソフト全体）</button></div></div>
    <div class="panel" style="margin-top:18px"><h3>CurseForge でもMODを探す（任意）</h3>
      <p class="small muted" style="max-width:76ch">ふだんは Modrinth から探します。CurseForge のMODも使いたいときは、<b>自分で無料の「APIキー」を作って</b>ここに入れます。キーはこのPCの中だけに保存します。</p>
      <ol class="small" style="padding-left:1.4em"><li><button class="linkbtn" data-url="https://console.curseforge.com/">console.curseforge.com</button> を開いて、アカウントを作る（Googleなどでログインできます）</li>
        <li>「API Keys」を開いて、出てきたキーをコピーする</li><li>下の欄に貼って「保存」</li></ol>
      <div class="inline-field" style="max-width:620px"><input type="password" data-cfkey placeholder="${st.cf_enabled ? "設定ずみ（変えるときだけ入れる）" : "$2a$10$ から始まる長い文字"}" autocomplete="off"><button class="btn" data-cfsave>保存</button>${st.cf_enabled ? '<button class="btn ghost" data-cfdel>消す</button>' : ""}</div>
      <p class="hint">${st.cf_enabled ? '<span class="badge ok">使えます</span> MODを探す画面で「CurseForge」を選べます。' : "入れると、MODを探す画面で「CurseForge」を選べるようになります。"}作者が許可していないMODは自動で入れられないので、そのときはサイトを開いて手で入れます。</p></div>
    <div class="panel" style="margin-top:18px"><h3>友達に配る用に書き出す</h3>
      <p class="small muted" style="max-width:76ch">このソフトを友達に渡すための zip を作ります。<b>設定・鍵（Webhook など）・ワールドは入りません</b>（data フォルダを除くため）。フォルダをそのまま渡すと、鍵が見えてしまうので、渡すときはこれを使ってください。</p>
      <div class="row"><button class="btn primary" data-dist>配布用の zip を作る</button></div><div data-distdone></div></div>
    <p class="small faint" style="margin-top:18px">OneClickServerMC。MODの情報は Modrinth から取得しています。このソフトは Mojang・Microsoft とは関係ありません。</p></div>`;
  $("[data-bundle]", root).onclick = (e) => supportBundle(null, e.target);
  $("[data-updauto]", root).onchange = async (e) => { try { await api("set_settings", { auto_update_check: e.target.checked }); await refreshState(); toast("保存しました"); } catch (err) { fail(err); e.target.checked = !e.target.checked; } };
  const updres = $("[data-updres]", root);
  bindUpdateButtons(updres);
  $("[data-updcheck]", root).onclick = async (e) => {
    e.target.disabled = true; updres.innerHTML = `<div class="empty">調べています…</div>`;
    try { S._updInfo = await api("update_check"); await refreshState(); updres.innerHTML = updateNoticeHtml(S._updInfo); bindUpdateButtons(updres); } catch (err) { fail(err); updres.innerHTML = ""; } finally { e.target.disabled = false; }
  };
  $$("[data-url]", root).forEach((b) => (b.onclick = () => api("open_url", { url: b.dataset.url }).catch(fail)));
  $("[data-cfsave]", root).onclick = async () => {
    const v = $("[data-cfkey]", root).value.trim();
    if (!v) return toast("キーを貼ってください", "warn");
    try { await api("set_settings", { cf_key: v }); await refreshState(); toast("CurseForge が使えるようになりました"); viewAppSettings(root); } catch (e) { fail(e); }
  };
  const cfd = $("[data-cfdel]", root);
  if (cfd) cfd.onclick = async () => { try { await api("set_settings", { cf_key: "" }); await refreshState(); toast("消しました"); viewAppSettings(root); } catch (e) { fail(e); } };
  $("[data-dist]", root).onclick = async (e) => {
    e.target.disabled = true;
    try {
      const r = await api("dist_export");
      $("[data-distdone]", root).innerHTML = `<div class="note ok" style="margin-top:12px"><b>できました（${r.files}ファイル）</b>デスクトップ（なければユーザーのフォルダ）に <b>OneClickServerMC_配布用.zip</b> を作りました。これを友達に送ってください。<br><span class="small muted">${esc(r.path)}</span></div>`;
    } catch (err) { fail(err); } finally { e.target.disabled = false; }
  };
  $("[data-sndset]", root).onchange = (e) => { setSound(e.target.checked); wakeAudio(); if (e.target.checked) beep("join"); toast(e.target.checked ? "音をONにしました" : "音をOFFにしました"); };
  $("[data-tour]", root).onclick = () => { const sv = S.servers[0]; if (!sv) return toast("サーバーを作ってから見られます", "warn"); go("server", { server: sv.id, tab: "home" }); setTimeout(() => startTour(true), 500); };
  $("[data-pmapply]", root).onclick = async (e) => {
    const mode = ($("[name=pmode]:checked", root) || {}).value || "same";
    e.target.disabled = true;
    try {
      const r = await api("apply_port_mode", { mode, apply_existing: true });
      await refreshState();
      $("[data-pmout]", root).innerHTML = `<div class="note ok" style="margin-top:10px"><b>${r.changed.length ? `${r.changed.length}個のサーバーの番号を変えました` : "変えるサーバーはありませんでした"}</b>${r.changed.length ? esc(r.changed.join("、")) + "<br>友達に伝えるアドレスと、playit の「Local Port」も、新しい番号に合わせてください。" : ""}${r.skipped.length ? `<br>動いているので変えなかったもの: ${esc(r.skipped.join("、"))}（止めてから、もう一度押してください）` : ""}</div>`;
    } catch (err) { fail(err); } finally { e.target.disabled = false; }
  };
  $("[data-save]", root).onclick = async () => {
    try {
      await api("set_settings", { translate_auto: $("[data-ta]", root).checked, translate_fallback: $("[data-tf]", root).checked, translate_email: $("[data-email]", root).value.trim(),
        port_mode: ($("[name=pmode]:checked", root) || {}).value || "same",
        ui_mode: ($("[name=uimode]:checked", root) || {}).value || "simple", hide_next_step: !$("[data-nextshow]", root).checked,
        self_play: $("[data-sp]", root).checked, minecraft_dir: $("[data-dir]", root).value.trim() });
      translateStopped = false;
      await refreshState();
      toast("保存しました");
    } catch (e) { fail(e); }
  };
  bindThemePicker(root);
}

async function checkJoinedUpdates() {
  try {
    const list = (await api("joined_list")).joined;
    if (!list.length) return;
    const res = (await api("joined_check")).results;
    const ups = list.filter((j) => res[j.key] && res[j.key].status === "update");
    S.joinUpdates = ups.length;
    renderSide();
    if (ups.length) toast(`「${ups[0].name}」に更新があります${changesBrief(res[ups[0].key].changes)}。「友達のサーバーに参加」から入れられます`, "warn", 9000);
  } catch (e) { /* 何もしない */ }
}

// ================================================================ 始まり
async function boot() {
  try {
    await refreshState();
  } catch (e) {
    $("#main").innerHTML = `<div class="page"><h1>つながりません</h1><p>${esc(e.message)}</p></div>`;
    return;
  }
  // 招待リンク（mcmh://join/…）から開いたとき
  const hm = location.hash.match(/^#join=(.+)$/);
  if (hm) {
    history.replaceState(null, "", location.pathname);
    go("join", { joinAddr: decodeURIComponent(hm[1]) });
  } else if (S.servers.length) go("server", { server: S.servers[0].id, tab: "home" });
  else go("welcome");
  setInterval(() => api("ping").catch(() => {}), 10000);
  checkJoinedUpdates();
  checkUpdateNotice();
  setInterval(checkJoinedUpdates, 15 * 60 * 1000);
  api("ping").catch(() => {});
  setInterval(async () => {
    S.tick++;
    if (S.view === "server" && S.tab === "home") pollConsole(false);
    if (S.view === "server" && S.tab === "perf" && S.tick % 4 === 0) {
      const el = $("[data-perf]"), pg = $("[data-pregen]");
      const s = cur();
      if (el && s && !el.querySelector(".tip:not([hidden])")) renderPerf(el, s);
      if (pg && s && pg.querySelector(".meter") && !pg.contains(document.activeElement)) renderPregen(pg, s);
      const lg = $("[data-lag]");
      if (lg && s && s.status === "running" && $("[data-scan][disabled]", lg)) renderLag(lg, s);
    }
    if (S.tick % 3 === 0) {
      const before = JSON.stringify(S.servers.map(srvSig));
      try { await refreshState(); } catch (e) { return; }
      if (S.view === "list") paintList();
      const after = JSON.stringify(S.servers.map(srvSig));
      if (before !== after && S.view === "server") refreshAll();
    }
  }, 1200);
}
boot();
