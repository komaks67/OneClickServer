/* OneClickServer ホーム画面 */
const $ = id => document.getElementById(id);
const TOKEN = document.querySelector('meta[name="token"]').content;
let H = null, scanBusy = false, updTried = false;

async function post(name, params) {
  const r = await fetch("/api/" + name, {
    method: "POST", headers: {"Content-Type": "application/json", "X-Token": TOKEN},
    body: JSON.stringify(params || {})
  });
  let j = {};
  try { j = await r.json(); } catch (e) { /* 空の返事 */ }
  if (!r.ok) throw new Error(j.error || "エラーが起きました");
  return j;
}
function toast(t) {
  const e = $("toast"); e.textContent = t; e.classList.add("show");
  clearTimeout(toast.t); toast.t = setTimeout(() => e.classList.remove("show"), 4000);
}
function applyTheme(t) { document.documentElement.dataset.theme = t || "auto"; }
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// ゲームのマーク(文字は使わず、かんたんな線の絵)。公式アイコン(web/icons/<key>.png)があれば、そちらを使う
const GLYPH = {
  minecraft: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/></svg>',
  ark: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"><path d="M6 20c-1-5 0-11 3-16M12 21c-1-6 0-12 3-17M18 20c0-5 1-9 3-12"/></svg>',
  palworld: '<svg viewBox="0 0 24 24" fill="#fff"><circle cx="6.5" cy="11" r="2.2"/><circle cx="10" cy="6.5" r="2.2"/><circle cx="14.5" cy="6.5" r="2.2"/><circle cx="18" cy="11" r="2.2"/><path d="M12 12c-3 0-5.5 3-5.5 5.2 0 1.8 1.6 2.3 3 2 1-.2 1.7-.5 2.5-.5s1.5.3 2.5.5c1.4.3 3-.2 3-2C17.5 15 15 12 12 12z"/></svg>',
  terraria: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.9" stroke-linejoin="round" stroke-linecap="round"><path d="M12 3l6 8h-3l4 6H5l4-6H6z"/><path d="M12 17v4"/></svg>'
};
function makeIcon(key, badge) {
  const d = el("div", "badge");
  d.dataset.game = key;
  d.innerHTML = GLYPH[key.startsWith("ark") ? "ark" : key] || "";
  const img = new Image();
  img.alt = ""; img.className = "gicon";
  img.onload = () => { d.innerHTML = ""; d.classList.add("hasimg"); d.append(img); };
  img.src = "/static/icons/" + encodeURIComponent(key) + ".png";
  return d;
}

const STATUS = {running: "起動中", busy: "作業中", stopped: "停止中"};

function renderCards(cards) {
  const box = $("cards"); box.innerHTML = "";
  $("empty").hidden = cards.length > 0;
  cards.forEach(c => {
    const d = el("div", "srv"); d.dataset.game = c.game;
    const head = el("div", "head");
    head.append(makeIcon(c.game, c.badge));
    const nm = el("div"); nm.style.flex = "1";
    nm.append(el("div", "nm", c.name));
    nm.append(el("div", "small", c.detail));
    head.append(nm);
    head.append(el("span", "pill " + c.status, c.busy || STATUS[c.status] || c.status));
    if (c.update) head.append(el("span", "pill upd", "更新あり"));
    d.append(head);
    if (c.players !== null && c.players !== undefined) d.append(el("div", "small", "いま入っている人: " + c.players + "人"));
    const b = el("button", "btn main", "開く");
    b.onclick = () => { location.href = c.url; };
    d.append(b);
    box.append(d);
  });
}

function renderPick(choices) {
  const box = $("pick"); box.innerHTML = "";
  choices.forEach(g => {
    const b = el("button"); b.dataset.game = g.key;
    b.append(makeIcon(g.key, g.badge));
    const t = el("div"); t.append(el("div", "nm", g.name));
    b.append(t);
    b.onclick = async () => {
      try { const r = await post("create_game", {key: g.key}); location.href = r.url; }
      catch (e) { toast(e.message); }
    };
    box.append(b);
  });
}

function renderUpdate(h) {
  const req = h.update_required, st = h.update || {};
  const ov = $("updOverlay"), bn = $("updBanner");
  bn.hidden = true;
  if (st.running) {                       // 更新の作業中は、他の操作ができないようにする
    ov.hidden = false;
    $("updTitle").textContent = "更新しています…";
    $("updText").textContent = "このまま、お待ちください。終わると、自動で開き直します。";
    $("updNotes").hidden = true;
    $("updProgress").textContent = (st.lines || []).slice(-1)[0] || "用意しています…";
    $("updGo").hidden = true;
    return;
  }
  if (!req) { ov.hidden = true; return; }
  if (h.anything_running) {               // サーバーが動いている間は、止めずに知らせるだけ
    ov.hidden = true;
    bn.hidden = false;
    bn.textContent = "新しい版 " + req.latest + " があります。動いているサーバーを止めると、更新できます(更新は必要です)。";
    return;
  }
  ov.hidden = false;
  $("updTitle").textContent = "新しい版 " + req.latest + " があります";
  $("updText").textContent = "更新が必要です。下のボタンを押すと、自動で更新して、開き直します。";
  const n = $("updNotes"); n.hidden = !req.notes; n.textContent = req.notes || "";
  $("updProgress").textContent = st.error ? ("更新できませんでした: " + st.error) : "";
  $("updGo").hidden = false;
  $("updGo").textContent = st.error ? "もう一度ためす" : "更新して、開き直す";
}

async function refresh() {
  try {
    H = await post("get_home");
  } catch (e) {
    if (H && H.update && H.update.running) $("updProgress").textContent = "入れ替えています。まもなく開き直します…";
    return;
  }
  $("ver").textContent = "版 " + H.version;
  if (H.notice) setTimeout(() => alert(H.notice), 200);
  applyTheme(H.theme); $("theme").value = H.theme;
  renderCards(H.cards); renderPick(H.choices); renderUpdate(H);
  $("migUndo").hidden = !H.migrated;
  if (H.migrated && !$("migOut").dataset.shown) {
    $("migOut").dataset.shown = "1";
    showReport("前回の引き継ぎ(" + H.migrated.from + ")", H.migrated.report);
  }
}

function showReport(title, lines) {
  const o = $("migOut"); o.hidden = false;
  o.textContent = title + "\n" + lines.map(x => "・" + x).join("\n");
}

async function scan() {
  if (scanBusy) return;
  scanBusy = true; $("migScan").disabled = true; $("migFound").textContent = "探しています…";
  try {
    const r = await post("migration_scan", {path: $("migPath").value.trim()});
    const box = $("migFound"); box.innerHTML = "";
    if (r.message) toast(r.message);
    if (!r.found.length) box.append(el("p", "small", "古いデータは見つかりませんでした。フォルダの場所が分かるときは、上の欄に入れてください。"));
    r.found.forEach(f => {
      const row = el("div", "srv"); row.style.marginBottom = "8px";
      row.append(el("div", "nm", f.summary));
      row.append(el("div", "small", f.path));
      const b = el("button", "btn main", "このデータを引き継ぐ");
      b.onclick = () => runMigration(f);
      row.append(b);
      box.append(row);
    });
    return r.found;
  } catch (e) { toast(e.message); $("migFound").textContent = ""; }
  finally { scanBusy = false; $("migScan").disabled = false; }
}

async function runMigration(f) {
  if (!confirm(f.summary + " を引き継ぎます。\n\n場所: " + f.path +
      "\n\n古いソフトは閉じてありますか?(同じドライブなら、データは新しい場所へ移動します。後で「元に戻す」もできます)")) return;
  try {
    const r = await post("migration_run", {path: f.path});
    showReport("引き継ぎが終わりました", r.report);
    $("migFound").innerHTML = "";
    $("migBanner").hidden = true;
    toast("引き継ぎが終わりました。");
    refresh();
  } catch (e) { toast(e.message); }
}

async function undo() {
  if (!confirm("直前の引き継ぎを、元に戻します。よろしいですか?")) return;
  try {
    const r = await post("migration_undo");
    showReport("元に戻しました", r.report);
    refresh();
  } catch (e) { toast(e.message); }
}

async function autoDetect() {
  if (H && (H.migrated || H.migrate_dismissed)) return;
  try {
    const r = await post("migration_scan", {});
    if (r.found.length) {
      $("migBanner").hidden = false;
      $("migBannerText").textContent = r.found.length + " か所: " + r.found.slice(0, 3).map(f => f.summary + "(" + f.path + ")").join(" / ") + (r.found.length > 3 ? " 他 " + (r.found.length - 3) + " か所(下の「データの引き継ぎ」で選べます)" : "");
    }
  } catch (e) { /* 見つからなくてもよい */ }
}

async function loadApp() {
  try {
    const a = await post("get_app_settings");
    $("appResident").checked = a.resident; $("appResident").disabled = !a.resident_available;
    if (!a.resident_available) $("appResNote").textContent = "この環境では、常駐は使えません(Windows の配布版で使えます)。";
    $("appWinStart").checked = a.windows_start; $("appWinStart").disabled = !a.windows_start_available;
  } catch (e) { /* 設定が読めなくても、ほかは使える */ }
}
$("appResident").onchange = async () => {
  try { await post("set_app_settings", {resident: $("appResident").checked}); toast("保存しました"); } catch (e) { toast(e.message); loadApp(); }
};
$("appWinStart").onchange = async () => {
  try { await post("set_app_settings", {windows_start: $("appWinStart").checked}); toast("保存しました"); } catch (e) { toast(e.message); loadApp(); }
};
loadApp();
$("bNew").onclick = () => { $("pickCard").hidden = !$("pickCard").hidden; };
$("theme").onchange = async () => {
  try { await post("set_theme", {theme: $("theme").value}); applyTheme($("theme").value); } catch (e) { toast(e.message); }
};
$("migScan").onclick = scan;
$("migUndo").onclick = undo;
$("migOpen").onclick = () => { $("migCard").scrollIntoView({behavior: "smooth"}); scan(); };
$("migDismiss").onclick = async () => { $("migBanner").hidden = true; try { await post("migration_dismiss"); } catch (e) {} };
$("updGo").onclick = async () => {
  try { await post("update_apply"); } catch (e) { toast(e.message); }
  refresh();
};

refresh().then(autoDetect);
setInterval(refresh, 2500);
setInterval(() => post("ping").catch(() => {}), 20000);
