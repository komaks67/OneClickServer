// ルール・ワールドの地図・荒らしの巻き戻し・おすすめMODセット・PCの重さの警告
"use strict";

// ---------------- 「ルール・地図」タブ ----------------
async function tabWorld(body, s) {
  let rules, marks;
  try { [rules, marks] = await Promise.all([api("rules_get", { id: s.id }), api("landmarks_get", { id: s.id })]); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  body.innerHTML = `<div class="grid2">
    <div class="panel"><h2>みんなのルール</h2>
      <p class="muted small" style="margin-top:0">サーバーで守ってほしいことを書きます（1行に1つ、12行まで）。入ってきた人に、ゲームの中で見せたり、「友達用の入り方カード」にのせたりできます。</p>
      <textarea data-rules rows="7" style="width:100%" placeholder="例:&#10;ひとのチェストを開けない&#10;TNTは使わない&#10;建築は自分の土地の中で">${esc(rules.text)}</textarea>
      <label class="check small" style="margin-top:8px"><input type="checkbox" data-onjoin ${rules.on_join ? "checked" : ""}> 入ってきた人に、ゲームの画面（チャット）でルールを見せる</label>
      <label class="check small"><input type="checkbox" data-incard ${rules.in_card ? "checked" : ""}> 「友達用の入り方カード」にものせる</label>
      <div class="row" style="margin-top:10px"><button class="btn primary" data-saverules>保存する</button><span class="small muted" data-rulesout></span></div>
      <div class="small muted" style="margin-top:6px">ゲームの画面に出すのは、サーバーが動いているときに入ってきた人だけです。</div>
    </div>
    <div class="panel"><h2>ワールドの地図（場所の目印）</h2>
      <p class="muted small" style="margin-top:0">家・拠点・ネザーゲートなどの場所に、名前を付けて残せます。座標は、ゲームの中で <span class="kbd">F3</span> キーを押すと、左上の「XYZ」で見られます。</p>
      <div data-map></div>
      <div class="row" style="margin-top:10px;gap:6px;align-items:flex-end">
        <label class="small col">名前<input type="text" data-mname style="width:130px" placeholder="例: 拠点"></label>
        <label class="small col">X<input type="number" data-mx style="width:90px"></label><label class="small col">Y<input type="number" data-my value="64" style="width:70px"></label><label class="small col">Z<input type="number" data-mz style="width:90px"></label>
        <label class="small col">場所<select data-mdim>${Object.entries(marks.dims).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <button class="btn" data-madd>追加</button></div>
      <label class="small" style="display:block;margin-top:6px">ひとこと（なくてもOK）<input type="text" data-mnote placeholder="例: みんなの家。ベッドあり"></label>
    </div></div>
    <div class="panel" style="margin-top:16px"><h3>場所の一覧</h3><div data-mlist></div></div>`;
  $("[data-saverules]", body).onclick = async (e) => {
    e.target.disabled = true;
    try { await api("rules_set", { id: s.id, text: $("[data-rules]", body).value, on_join: $("[data-onjoin]", body).checked, in_card: $("[data-incard]", body).checked }); $("[data-rulesout]", body).textContent = "保存しました"; toast("ルールを保存しました"); } catch (err) { fail(err); } finally { e.target.disabled = false; }
  };
  const paint = (items) => {
    $("[data-map]", body).innerHTML = mapSvg(items);
    $("[data-mlist]", body).innerHTML = items.length ? `<table class="tbl"><tbody>${items.map((m) => `<tr><td><b>${esc(m.name)}</b>${m.note ? `<div class="small muted">${esc(m.note)}</div>` : ""}</td><td class="small">${esc(marks.dims[m.dim] || "")}</td><td class="small"><code>X ${m.x} / Y ${m.y} / Z ${m.z}</code></td><td style="text-align:right"><button class="btn sm" data-tp="${esc(m.id)}">コマンドをコピー</button> <button class="btn sm ghost" data-del="${esc(m.id)}">消す</button></td></tr>`).join("")}</tbody></table><div class="small muted" style="margin-top:6px">「コマンドをコピー」は、ゲーム内のチャットに貼ると、その場所に移動できます（管理者だけ）。</div>`
      : `<div class="faint small">まだありません。上のフォームから追加してください。</div>`;
    $$("[data-tp]", body).forEach((b) => (b.onclick = () => { const m = items.find((x) => x.id === b.dataset.tp); navigator.clipboard.writeText(`/tp @s ${m.x} ${m.y} ${m.z}`); toast("コピーしました"); }));
    $$("[data-del]", body).forEach((b) => (b.onclick = async () => {
      if (!(await ask("目印を消す", "<p>この目印を消しますか？（ワールドには、何も起きません）</p>", "消す", { danger: true }))) return;
      try { paint((await api("landmark_remove", { id: s.id, lid: b.dataset.del })).items); } catch (err) { fail(err); }
    }));
  };
  paint(marks.items);
  $("[data-madd]", body).onclick = async () => {
    try {
      const r = await api("landmark_add", { id: s.id, name: $("[data-mname]", body).value, x: $("[data-mx]", body).value, y: $("[data-my]", body).value || 64, z: $("[data-mz]", body).value, dim: $("[data-mdim]", body).value, note: $("[data-mnote]", body).value });
      paint(r.items); $("[data-mname]", body).value = ""; $("[data-mnote]", body).value = ""; toast("追加しました");
    } catch (err) { fail(err); }
  };
}

function mapSvg(items) {
  const pts = items.filter((m) => m.dim === "overworld");
  const W = 520, H = 320, pad = 34;
  if (!pts.length) return `<div class="faint small" style="padding:30px;text-align:center;border:1px dashed var(--line);border-radius:12px">地上の目印を追加すると、ここに地図が出ます</div>`;
  const xs = pts.map((m) => m.x).concat([0]), zs = pts.map((m) => m.z).concat([0]);
  let x0 = Math.min(...xs), x1 = Math.max(...xs), z0 = Math.min(...zs), z1 = Math.max(...zs);
  const span = Math.max(x1 - x0, z1 - z0, 100), cx = (x0 + x1) / 2, cz = (z0 + z1) / 2;
  const sc = (Math.min(W, H) - pad * 2) / span;
  const px = (x) => W / 2 + (x - cx) * sc, pz = (z) => H / 2 + (z - cz) * sc;
  const grid = [];
  const step = span > 4000 ? 1000 : span > 1000 ? 500 : span > 400 ? 100 : 50;
  for (let gx = Math.ceil((cx - W / 2 / sc) / step) * step; gx < cx + W / 2 / sc; gx += step) grid.push(`<line x1="${px(gx)}" y1="0" x2="${px(gx)}" y2="${H}" stroke="currentColor" opacity=".08"/>`);
  for (let gz = Math.ceil((cz - H / 2 / sc) / step) * step; gz < cz + H / 2 / sc; gz += step) grid.push(`<line x1="0" y1="${pz(gz)}" x2="${W}" y2="${pz(gz)}" stroke="currentColor" opacity=".08"/>`);
  const dots = pts.map((m) => `<g><circle cx="${px(m.x)}" cy="${pz(m.z)}" r="6" fill="#10b981" stroke="#fff" stroke-width="2"/><text x="${px(m.x) + 10}" y="${pz(m.z) + 4}" font-size="13" fill="currentColor" font-weight="700">${esc(m.name)}</text></g>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-height:340px;border-radius:12px;background:var(--panel-2);color:var(--bone)" role="img" aria-label="目印の地図">
    ${grid.join("")}<line x1="${px(0)}" y1="0" x2="${px(0)}" y2="${H}" stroke="currentColor" opacity=".25"/><line x1="0" y1="${pz(0)}" x2="${W}" y2="${pz(0)}" stroke="currentColor" opacity=".25"/>
    <text x="${px(0) + 4}" y="${pz(0) - 4}" font-size="10" fill="currentColor" opacity=".5">(0, 0)</text>
    <text x="${W - 8}" y="14" font-size="11" fill="currentColor" opacity=".5" text-anchor="end">北</text>${dots}
    <text x="8" y="${H - 8}" font-size="10" fill="currentColor" opacity=".5">1マス = ${step}ブロック（上が北）</text></svg>`;
}

// ---------------- 荒らしの巻き戻し（バックアップのタブの下） ----------------
async function renderGrief(el, s) {
  let d;
  try { d = await api("grief_backups", { id: s.id }); } catch (e) { el.innerHTML = ""; return; }
  el.innerHTML = `<h2 style="margin-top:30px">荒らし（いたずら）を、その場所だけ巻き戻す</h2>
    <p class="muted" style="max-width:76ch">ワールド全体ではなく、<b>壊された場所だけ</b>を、前のバックアップの状態に戻します。ほかの場所の建築や進み具合は、そのまま残ります。</p>
    <div class="panel"><div class="row" style="gap:10px;align-items:flex-end;flex-wrap:wrap">
      <label class="small col">どのバックアップに戻す？<select data-gfile style="min-width:230px">${d.backups.map((b) => `<option value="${esc(b.file)}">${esc(b.when)}（${esc(b.kind)}）</option>`).join("") || "<option value=''>バックアップがありません</option>"}</select></label>
      <label class="small col">場所<select data-gdim>${Object.entries(d.dims).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
      <label class="small col">中心 X<input type="number" data-gx style="width:100px" value="0"></label>
      <label class="small col">中心 Z<input type="number" data-gz style="width:100px" value="0"></label>
      <label class="small col">範囲（半径）<input type="number" data-gr style="width:90px" value="64" min="16" max="1000"></label>
      <button class="btn" data-gprev>調べる</button><button class="btn danger" data-grun>この場所だけ、戻す</button></div>
      <div data-gout style="margin-top:10px"></div>
      <ul class="small muted" style="padding-left:1.3em;margin-bottom:0"><li>座標は、ゲームの中で <span class="kbd">F3</span> キーを押すと見られます（荒らされた場所の、真ん中あたり）。</li>
        <li>範囲（半径）は、<b>少し広め</b>がおすすめです。64 なら、中心から 64 ブロックの四角です。</li>
        <li>戻す前に、今のワールドも自動でバックアップします。<b>サーバーを止めてから</b>やってください。</li>
        <li>その範囲に、バックアップのあとで作った建築があると、<b>消えます</b>。</li></ul></div>`;
  const args = () => ({ id: s.id, file: $("[data-gfile]", el).value, dim: $("[data-gdim]", el).value, x: $("[data-gx]", el).value, z: $("[data-gz]", el).value, radius: $("[data-gr]", el).value });
  $("[data-gprev]", el).onclick = async (e) => {
    e.target.disabled = true;
    try {
      const r = await api("grief_preview", args());
      $("[data-gout]", el).innerHTML = `<div class="note ${r.chunks ? "ok" : "warn"}"><b>${r.chunks ? `${r.chunks}個の区画（チャンク）が、戻ります` : "戻る場所は、ありませんでした"}</b>範囲: ${esc(r.area)}${r.running ? "<br>※ サーバーが動いています。戻す前に、止めてください。" : ""}</div>`;
    } catch (err) { fail(err); } finally { e.target.disabled = false; }
  };
  $("[data-grun]", el).onclick = async (e) => {
    const a = args();
    if (!a.file) return toast("バックアップがありません", "warn");
    if (!(await ask("その場所だけ、戻す", `<p><b>${esc(a.file)}</b> の状態に、この範囲だけを戻します。</p><ul class="small"><li>場所: X ${esc(a.x)} / Z ${esc(a.z)}、半径 ${esc(a.radius)}</li><li>その範囲の、あとから作ったものは消えます。</li><li>今のワールドは、先に自動でバックアップします。</li></ul>`, "戻す", { danger: true }))) return;
    e.target.disabled = true;
    try {
      const r = await runJob(api("grief_revert", a), "その場所だけを、戻しています");
      $("[data-gout]", el).innerHTML = `<div class="note ok"><b>${r.chunks}個の区画を、戻しました</b>サーバーを起動して、確認してください。間違えたときは、上の「ワンクリック巻き戻し」の「巻き戻し前」に戻せます。</div>`;
      await refreshState();
    } catch (err) { if (!err.shown) fail(err); } finally { e.target.disabled = false; }
  };
}

// ---------------- おすすめMODの組み合わせ集 ----------------
async function openPresets(serverId) {
  const W = modWordFor(serverId);
  let r;
  try { r = await runJob(api("mod_presets", { server: serverId }), "このサーバーで使えるものを調べています"); } catch (e) { if (!e.shown) fail(e); return; }
  const have = new Set();
  const card = (p) => `<div class="panel" style="margin-bottom:12px"><div class="row top"><div class="grow"><h3 style="margin:0">${esc(p.title)}</h3><div class="small muted">${esc(p.for)}　${p.usable}/${p.mods.length}個 使えます</div>
      <p class="small" style="margin:6px 0">${esc(p.desc)}</p>
      <ul class="small" style="margin:0;padding-left:1.2em">${p.mods.map((m) => `<li class="${m.ok ? "" : "faint"}"><b>${esc(m.slug)}</b>　${esc(m.note)}${m.ok ? "" : "　<span class='badge'>このサーバーでは使えません</span>"}</li>`).join("")}</ul></div>
      <button class="btn primary" data-pset="${esc(p.id)}" ${p.usable ? "" : "disabled"}>このセットを入れる</button></div></div>`;
  const dlg = modal(`<h2>おすすめの組み合わせ</h2><div class="plain"><p class="small muted">マイクラ ${esc(r.mc)}・${esc(r.loader)} で使えるものだけが、入ります。入れたあとで、「起動テスト」をすると安心です。</p><div style="max-height:62vh;overflow:auto">${r.presets.map(card).join("")}</div></div>
    <div class="modal-foot"><button class="btn ghost" data-close>閉じる</button></div>`, {});
  $("[data-close]", dlg.el).onclick = () => dlg.close();
  $$("[data-pset]", dlg.el).forEach((b) => (b.onclick = async () => {
    const p = r.presets.find((x) => x.id === b.dataset.pset);
    const ids = [...new Set(p.mods.filter((m) => m.ok).map((m) => m.slug))];
    dlg.close();
    await installMods(serverId, ids);
    const sb = $("[data-subbody]"); if (sb) subInstalled(sb, cur());
  }));
}

// ---------------- PCの重さの警告 ----------------
async function pollPcLoad() {
  const el = $("[data-pcwarn]");
  if (!el || S.view !== "server") return;
  const s = cur();
  if (!s || s.status === "stopped") { el.innerHTML = ""; return; }
  let r;
  try { r = await api("pc_load"); } catch (e) { return; }
  if (r.level === "ok" || (S.pcQuietUntil && Date.now() < S.pcQuietUntil)) { el.innerHTML = ""; return; }
  el.innerHTML = `<div class="note ${r.level === "ng" ? "error" : "warn"}" style="margin:10px 0"><div class="row top"><div class="grow"><b>${r.level === "ng" ? "PCがとても重くなっています" : "PCが、重くなりかけています"}</b>
    ${r.messages.map((m) => `<div>${esc(m)}</div>`).join("")}
    <details class="small" style="margin-top:4px"><summary style="cursor:pointer">どうすればいい？</summary><ul>${r.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></details></div>
    <button class="linkbtn" data-pcquiet>10分、出さない</button></div></div>`;
  $("[data-pcquiet]", el).onclick = () => { S.pcQuietUntil = Date.now() + 10 * 60 * 1000; el.innerHTML = ""; };
}
setInterval(pollPcLoad, 8000);
setTimeout(pollPcLoad, 3000);

// ---------------- 入りたい人の申請 ----------------
async function renderApplicants(el, s, reload) {
  let d;
  try { d = await api("applicants", { id: s.id }); } catch (e) { el.innerHTML = ""; return; }
  const msg = "サーバーに入りたいときは、一度サーバーのアドレスで入ろうとしてみてください（断られますが、それが申請になります）。そのあと、ホストに「入ろうとしたよ」と教えてください。";
  el.innerHTML = `<div class="panel" style="margin-bottom:16px"><h3>入りたい人の申請 ${d.items.length ? `<span class="badge">${d.items.length}人</span>` : ""}</h3>
    <p class="small muted" style="margin-top:0">ホワイトリストにいない人が入ろうとすると、ここに名前が出ます。<b>「承認」を押すだけで、入れるようになります。</b>名前を打ち間違える心配もありません。</p>
    ${d.enabled ? "" : `<div class="note warn small">今はホワイトリストがOFFで、誰でも入れます。申請を使うには、上のスイッチをONにしてください。</div>`}
    <div class="list">${d.items.map((a) => `<div class="row" style="gap:10px;padding:8px 0;border-bottom:1px solid var(--line)"><div class="grow" style="min-width:0"><b>${esc(a.name)}</b> ${cpBtn(a.name, "名前", "名前")}<div class="small muted">${a.count}回 ・ 最後 ${esc(a.last_text)}</div></div>
      <button class="btn sm primary" style="flex:none;width:auto" data-ap="${esc(a.name)}">承認</button><button class="btn sm ghost" style="flex:none;width:auto" data-no="${esc(a.name)}">無視</button></div>`).join("") || `<p class="faint small">いま、申請はありません</p>`}</div>
    <details class="small" style="margin-top:8px"><summary style="cursor:pointer">友達に送る案内文</summary><p>${esc(msg)}</p><button class="btn sm" data-cpmsg>コピー</button></details></div>`;
  $$("[data-ap]", el).forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try { await api("member_add", { id: s.id, kind: "whitelist", name: b.dataset.ap }); toast(b.dataset.ap + " を承認しました"); reload(); } catch (e) { fail(e); b.disabled = false; }
  }));
  $$("[data-no]", el).forEach((b) => (b.onclick = async () => { try { await api("applicant_dismiss", { id: s.id, name: b.dataset.no }); renderApplicants(el, s, reload); } catch (e) { fail(e); } }));
  const c = $("[data-cpmsg]", el); if (c) c.onclick = () => { navigator.clipboard.writeText(msg); toast("コピーしました"); };
}

// ---------------- かんたん設定 ----------------
const EASY_PROPS = ["motd", "difficulty", "gamemode", "max-players", "white-list", "pvp", "spawn-monsters", "allow-nether", "allow-flight", "view-distance"];
const EASY_RULES = [/keep_?inventory/i, /mob_?griefing/i, /^(minecraft:)?pvp$/i, /players_?sleeping_?percentage/i];

async function tabEasy(body, s) {
  body.innerHTML = `<div class="empty">読み込んでいます…</div>`;
  let d, g = { running: false, rules: [] };
  try { d = await api("props_get", { id: s.id }); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  try { g = await api("gamerules_get", { id: s.id }); } catch (e) { /* 止まっているとき */ }
  const items = EASY_PROPS.map((k) => d.items.find((i) => i.key === k)).filter(Boolean);
  const row = (label, help, ctl) => `<div class="prop"><div class="lbl">${esc(label)}${help ? `<div class="small muted">${esc(help)}</div>` : ""}</div><div>${ctl}</div></div>`;
  const ctl = (it) => {
    if (it.type === "bool") return `<label class="switch"><input type="checkbox" data-pk="${esc(it.key)}" ${it.value === "true" ? "checked" : ""}><span></span></label>`;
    if (it.type === "enum") return `<select data-pk="${esc(it.key)}">${it.options.map(([v, l]) => `<option value="${esc(v)}" ${v === it.value ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    if (it.type === "int") return `<input type="number" data-pk="${esc(it.key)}" value="${esc(it.value)}" ${it.min != null ? `min="${it.min}"` : ""} ${it.max != null ? `max="${it.max}"` : ""} style="width:130px">`;
    return `<input type="text" data-pk="${esc(it.key)}" value="${esc(it.value)}" style="width:100%">`;
  };
  const rules = EASY_RULES.map((rx) => g.rules.find((r) => rx.test(r.key))).filter((r, i, a) => r && a.indexOf(r) === i && !items.some((it) => it.key === "pvp" && /pvp/i.test(r.key)));
  body.innerHTML = `<p class="muted" style="margin-top:0;max-width:72ch">よく変える設定だけを、まとめました。<b>変えたら「保存する」</b>を押してください。もっと細かい設定は、画面の右上の「詳しい機能も表示する」を押すと出る「設定」タブにあります。</p>
    <div class="panel"><h3>基本の遊び方</h3>${items.map((it) => row(it.label, it.help, ctl(it))).join("") || `<p class="faint small">一度サーバーを起動すると、ここに設定が出ます。</p>`}</div>
    <div class="panel" style="margin-top:16px"><h3>ゲームのルール</h3>
      ${g.running ? (rules.map((r) => row(r.label, r.help, r.type === "boolean" ? `<label class="switch"><input type="checkbox" data-gr="${esc(r.key)}" ${r.value ? "checked" : ""}><span></span></label>` : `<input type="number" data-gri="${esc(r.key)}" value="${esc(r.value)}" style="width:130px">`)).join("") || `<p class="faint small">この版では、ここに出せるルールがありません。</p>`)
        : `<div class="note small"><b>サーバーを起動すると変えられます</b>ここのルールは、ワールドの中に保存されているので、サーバーが動いている間だけ変えられます。</div>`}</div>
    <div class="row" style="margin-top:16px"><button class="btn primary" data-save>保存する</button><span class="small muted" data-out></span></div>
    <div data-memdiag style="margin-top:22px"></div><div data-tpl style="margin-top:16px"></div><div data-wcheck style="margin-top:16px"></div><div data-move style="margin-top:16px"></div>`;
  renderMemDiag($("[data-memdiag]", body), s);
  renderTemplates($("[data-tpl]", body), s, () => tabEasy(body, s));
  renderWorldCheck($("[data-wcheck]", body), s);
  renderMove($("[data-move]", body), s);
  $("[data-save]", body).onclick = async (e) => {
    e.target.disabled = true;
    try {
      const values = {};
      $$("[data-pk]", body).forEach((el) => { values[el.dataset.pk] = el.type === "checkbox" ? String(el.checked) : el.value; });
      let r = { live: [], restart: [] };
      if (Object.keys(values).length) r = await api("props_set", { id: s.id, values });
      for (const el of $$("[data-gr]", body)) await api("gamerule_set", { id: s.id, key: el.dataset.gr, value: el.checked });
      for (const el of $$("[data-gri]", body)) await api("gamerule_set", { id: s.id, key: el.dataset.gri, value: +el.value });
      $("[data-out]", body).textContent = r.restart && r.restart.length ? "保存しました。一部は、サーバーを再起動すると反映されます" : "保存しました";
      toast("保存しました");
    } catch (err) { fail(err); } finally { e.target.disabled = false; }
  };
}

// ---------------- メモリ診断 ----------------
async function renderMemDiag(el, s) {
  let d;
  try { d = await api("mem_diag", { id: s.id }); } catch (e) { el.innerHTML = ""; return; }
  const gb = (m) => (m / 1024).toFixed(m % 1024 ? 1 : 0) + "GB";
  const lv = { ok: ["ok", "だいじょうぶです"], tight: ["warn", "ぎりぎりです"], short: ["error", "メモリが足りません"] }[d.level];
  const pct = Math.min(100, Math.round((d.need / Math.max(d.allocated, 1)) * 100));
  el.innerHTML = `<div class="panel"><h3>メモリの診断</h3>
    <div class="note ${lv[0]}" style="margin:8px 0"><b>${lv[1]}</b>${d.level === "short" ? `今のMOD${d.mods}個と、最大${d.players}人には、<b>${gb(d.need)}</b> くらい必要です。今は <b>${gb(d.allocated)}</b> です。` : d.slots > 0 ? `今の設定（${gb(d.allocated)}）なら、<b>あと ${d.slots >= 300 ? "たくさん" : d.slots + "個"}くらい</b>、MODを入れても大丈夫そうです。` : `今の設定（${gb(d.allocated)}）では、MODを増やすと足りなくなりそうです。`}</div>
    <div style="height:10px;border-radius:6px;background:var(--panel-2);overflow:hidden"><div style="height:100%;width:${pct}%;background:${d.level === "ok" ? "#10b981" : d.level === "tight" ? "#f59e0b" : "#ef4444"}"></div></div>
    <div class="small muted" style="margin-top:4px">必要な目安 ${gb(d.need)} ／ 今の設定 ${gb(d.allocated)} ／ このPCで使っていい上限 ${gb(d.max_safe)}（PC全体 ${gb(d.total)}）</div>
    ${d.level !== "ok" && d.can_raise ? `<div class="row" style="margin-top:8px"><button class="btn" data-raise>メモリを ${gb(d.recommended)} にする</button><span class="small muted">次に起動したときから変わります</span></div>` : ""}
    ${d.level !== "ok" && !d.can_raise ? `<p class="small muted" style="margin-bottom:0">これ以上は、PCに余裕がありません。重いMODを減らすのがおすすめです。</p>` : ""}</div>`;
  const b = $("[data-raise]", el);
  if (b) b.onclick = async () => { try { await api("server_set", { id: s.id, profile: { memory_mb: d.recommended } }); toast("メモリを変えました"); renderMemDiag(el, s); } catch (e) { fail(e); } };
}

// ---------------- ひな形 ----------------
async function renderTemplates(el, s, reload) {
  let d;
  try { d = await api("templates_list"); } catch (e) { el.innerHTML = ""; return; }
  el.innerHTML = `<div class="panel"><h3>ひな形（まとめて設定）</h3><p class="small muted" style="margin-top:0">遊び方に合わせて、難しさやゲームモードなどを、まとめて変えます。<b>今の設定は上書きされます。</b>ワールドの中身は変わりません。</p>
    <div class="list">${d.items.map((t) => `<div class="row" style="gap:10px;padding:8px 0;border-bottom:1px solid var(--line)"><div class="grow" style="min-width:0"><b>${esc(t.title)}</b><div class="small muted">${esc(t.desc)}</div></div>
      <button class="btn sm" style="flex:none;width:auto" data-tpl="${esc(t.id)}">設定だけ変える</button><button class="btn sm primary" style="flex:none;width:auto" data-tplmod="${esc(t.id)}">設定＋おすすめMOD</button></div>`).join("")}</div></div>`;
  const apply = async (id, withMods) => {
    const t = d.items.find((x) => x.id === id);
    if (!(await ask("ひな形を使う", `<p>「<b>${esc(t.title)}</b>」の設定にします。</p><p class="small muted">${esc(t.desc)}</p><p class="small">今の「難しさ」「ゲームモード」などが、上書きされます。</p>`, "変える"))) return;
    try {
      const r = await api("template_apply", { id: s.id, tid: id });
      toast(`「${r.title}」にしました`);
      if (r.skipped.length) await ask("お知らせ", `<p>次の設定は、このバージョンでは変えられなかったので、そのままです。</p><ul class="small">${r.skipped.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`, "OK", { cancelText: "閉じる" });
      reload();
      if (withMods && typeof openPresets === "function") openPresets(s.id);
    } catch (e) { fail(e); }
  };
  $$("[data-tpl]", el).forEach((b) => (b.onclick = () => apply(b.dataset.tpl, false)));
  $$("[data-tplmod]", el).forEach((b) => (b.onclick = () => apply(b.dataset.tplmod, true)));
}

// ---------------- ワールドの点検 ----------------
async function renderWorldCheck(el, s) {
  let last = null;
  try { last = (await api("world_check_last", { id: s.id })).last; } catch (e) { /* なし */ }
  const show = (r, when) => `<div class="note ${r.problems.length ? "warn" : "ok"}" style="margin-top:10px"><b>${r.problems.length ? `${r.problems.length}件、気になるところがありました` : "問題は見つかりませんでした"}</b>${when ? `点検した日時: ${esc(when)}（${r.files}個のファイル）` : ""}
    ${r.problems.length ? `<ul class="small">${r.problems.slice(0, 10).map((p) => `<li><code>${esc(p.file)}</code> ${esc(p.msg)}</li>`).join("")}</ul><div class="small">直したいときは、「バックアップ」タブで、前のバックアップに戻すか、壊れた場所だけ戻してください。</div>` : ""}</div>`;
  el.innerHTML = `<div class="panel"><h3>ワールドの点検</h3><p class="small muted" style="margin-top:0">ワールドのデータが、壊れていないかを調べます。<b>サーバーが止まっているとき</b>、1日1回、自動でも調べます（このソフトが開いている間だけ）。おかしければ、Discordにも知らせます。</p>
    <div class="row"><button class="btn" data-wc>いま点検する</button></div><div data-wcout>${last ? show(last, last.text) : ""}</div></div>`;
  $("[data-wc]", el).onclick = async (e) => {
    e.target.disabled = true;
    try { const r = await runJob(api("world_check", { id: s.id }), "ワールドを点検しています"); if (r) $("[data-wcout]", el).innerHTML = show(r, "いま"); } catch (err) { if (!err.shown) fail(err); } finally { e.target.disabled = false; }
  };
}

// ---------------- サーバーの引っ越し（書き出し） ----------------
function renderMove(el, s) {
  el.innerHTML = `<div class="panel"><h3>サーバーの引っ越し</h3><p class="small muted" style="margin-top:0">別のPCに移すときに使います。ワールド・MOD・設定を、<b>1つのzipファイル</b>にまとめます。新しいPCでは、左の「引っ越しファイルから作る」で開きます。</p>
    <ul class="small muted" style="padding-left:1.3em"><li>バックアップ、ログ、Discordなどのひみつの設定は、入りません。</li><li>サーバーが止まっているときに作れます。ワールドが大きいと、時間がかかります。</li></ul>
    <button class="btn" data-mv>引っ越しファイルを作る</button><div data-mvout></div></div>`;
  $("[data-mv]", el).onclick = async (e) => {
    e.target.disabled = true;
    try {
      const r = await runJob(api("server_export", { id: s.id }), "引っ越しファイルを作っています");
      if (r) $("[data-mvout]", el).innerHTML = `<div class="note ok" style="margin-top:10px"><b>できました（約 ${r.mb}MB）</b>デスクトップ（なければユーザーのフォルダ）に保存しました。<br><span class="small muted">${esc(r.path)}</span></div>`;
    } catch (err) { if (!err.shown) fail(err); } finally { e.target.disabled = false; }
  };
}

// ---------------- 引っ越しファイルから作る ----------------
function viewImport(root) {
  root.innerHTML = `<div class="page"><h1>引っ越しファイルから作る</h1>
    <p class="muted" style="max-width:70ch">別のPCで作った「引っ越しファイル（zip）」から、サーバーを作ります。ワールド・MOD・設定が、そのまま入ります。</p>
    <div class="panel" style="max-width:640px"><label class="small col">引っ越しファイル（.zip）<input type="file" data-f accept=".zip"></label>
      <div data-info style="margin-top:10px"></div>
      <label class="small col" style="margin-top:10px">サーバーの名前（変えなければ、そのまま）<input type="text" data-name placeholder="例: 友達サーバー"></label>
      <div class="row" style="margin-top:12px"><button class="btn primary" data-go disabled>この内容で作る</button></div>
      <ul class="small muted" style="padding-left:1.3em"><li>ポート番号と「友達が入るアドレス」は、新しいPCで作り直してください（「友達を呼ぶ」タブ）。</li><li>Javaは、足りなければ、自動で入ります。</li></ul></div></div>`;
  let up = null;
  $("[data-f]", root).onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      up = await upload(f);
      const info = await api("server_import_inspect", { path: up.path });
      $("[data-info]", root).innerHTML = `<div class="note ok"><b>${esc(info.name)}</b>マイクラ ${esc(info.mc || "?")} ／ ${esc(info.loader || "?")}</div>`;
      $("[data-name]", root).value = info.name; $("[data-go]", root).disabled = false;
    } catch (err) { up = null; $("[data-go]", root).disabled = true; fail(err); }
  };
  $("[data-go]", root).onclick = async (e) => {
    e.target.disabled = true;
    try {
      const r = await runJob(api("server_import", { path: up.path, name: $("[data-name]", root).value }), "引っ越しファイルを開いています");
      if (r) { await refreshState(); toast("サーバーを作りました"); go("server", { server: r.server, tab: "home", consoleNext: 0 }); }
    } catch (err) { if (!err.shown) fail(err); } finally { e.target.disabled = false; }
  };
}
