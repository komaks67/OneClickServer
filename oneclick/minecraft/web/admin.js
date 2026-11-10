// 設定・ゲームルール・管理コマンド・BAN・外から操作・操作の記録
"use strict";

const SET_SUBS = [["props", "サーバーの設定"], ["rules", "ゲームルール"], ["memory", "メモリ・そのほか"], ["ops", "自動化・バージョン"], ["notify", "通知"], ["remote", "外から操作"], ["log", "操作の記録"]];

function tip(title, text) {
  return text ? `<button type="button" class="help" data-tip="${esc(text)}" data-title="${esc(title)}" aria-label="${esc(title)}の説明">?</button>` : "";
}

async function tabSettings(body, s) {
  S.setSub = S.setSub || "props";
  body.innerHTML = `<div class="row" style="margin-bottom:16px;gap:6px">${SET_SUBS.map(([k, l]) => `<button class="btn ${S.setSub === k ? "primary" : ""}" data-ss="${k}">${l}</button>`).join("")}</div><div data-ssb></div>`;
  $$("[data-ss]", body).forEach((b) => (b.onclick = () => { S.setSub = b.dataset.ss; tabSettings(body, s); }));
  const sb = $("[data-ssb]", body);
  ({ props: subProps, rules: subRules, memory: subMemory, ops: subOps, notify: subNotify, remote: subRemote, log: subLog }[S.setSub] || subProps)(sb, s);
}

// ---------------- サーバーの設定（server.properties の全部） ----------------
async function subProps(root, s) {
  root.innerHTML = `<div class="empty">読み込んでいます…</div>`;
  let d;
  try { d = await api("props_get", { id: s.id }); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const orig = Object.fromEntries(d.items.map((i) => [i.key, i.value]));
  const changes = {};
  const field = (it) => {
    const dis = it.managed ? "disabled" : "";
    const id = "p_" + it.key.replace(/[^a-z0-9]/gi, "_");
    if (it.type === "bool") return `<label class="switch"><input type="checkbox" id="${id}" data-k="${esc(it.key)}" ${it.value === "true" ? "checked" : ""} ${dis}><span></span></label>`;
    if (it.type === "enum") {
      const opts = [...it.options];
      if (it.value && !opts.some(([v]) => v === it.value)) opts.unshift([it.value, it.value]);
      return `<select id="${id}" data-k="${esc(it.key)}" ${dis}>${opts.map(([v, l]) => `<option value="${esc(v)}" ${v === it.value ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    }
    if (it.type === "int") return `<input type="number" id="${id}" data-k="${esc(it.key)}" value="${esc(it.value)}" ${it.min != null ? `min="${it.min}"` : ""} ${it.max != null ? `max="${it.max}"` : ""} style="width:160px" ${dis}>`;
    return `<input type="${it.type === "secret" ? "password" : "text"}" id="${id}" data-k="${esc(it.key)}" value="${esc(it.value)}" ${dis} autocomplete="off">`;
  };
  const row = (it) => `<div class="prop" data-row="${esc(it.key)}" data-search="${esc((it.label + " " + it.key + " " + (it.help || "")).toLowerCase())}">
      <div class="lbl"><label for="p_${it.key.replace(/[^a-z0-9]/gi, "_")}">${esc(it.label)}</label> ${tip(it.label, it.help)}
        <div class="key">${esc(it.key)}${it.live ? ' <span class="badge ok">動かしたまま変わる</span>' : ""}${it.managed ? ' <span class="badge">このソフトが管理</span>' : ""}${it.unknown ? ' <span class="badge warn">知らない設定</span>' : ""}</div></div>
      <div>${field(it)}${it.managed ? `<div class="hint">「外から操作」の画面で変えます。</div>` : ""}${it.danger ? `<div class="hint" style="color:var(--gold)">⚠ ${esc(it.danger)}</div>` : ""}</div></div>`;
  const groups = d.groups.map(([g, label]) => {
    const items = d.items.filter((i) => i.group === g);
    return items.length ? `<details class="pgroup" ${g === "basic" || g === "play" ? "open" : ""}><summary><h3>${esc(label)}</h3><span class="small muted">${items.length}個</span></summary>${items.map(row).join("")}</details>` : "";
  }).join("");
  root.innerHTML = `
    ${d.first_run ? `<div class="note" style="margin-bottom:14px"><b>まだ一度も起動していません</b>一度起動すると、このバージョンで使えるすべての設定が表示されます。今は主な設定だけ出ています。</div>` : ""}
    ${d.running ? `<div class="note ${d.msmp ? "ok" : ""}" style="margin-bottom:14px"><b>サーバーが動いています</b>${d.msmp ? "「動かしたまま変わる」と書いてある設定は、保存するとすぐ変わります。ほかは再起動で変わります。" : "保存した設定は、再起動すると変わります。"}</div>` : ""}
    <div class="row" style="margin-bottom:12px"><input type="search" data-q placeholder="設定を探す（例: 距離、ホワイトリスト、ポート）" style="flex:1;min-width:240px"><button class="btn sm" data-openall>全部開く</button></div>
    <div data-groups>${groups}</div>
    <div class="savebar" data-bar hidden><span data-count></span><span class="grow"></span><button class="btn ghost" data-undo>元に戻す</button><button class="btn primary" data-save>保存する</button></div>`;
  const bar = $("[data-bar]", root);
  const update = () => {
    const n = Object.keys(changes).length;
    bar.hidden = !n;
    $("[data-count]", root).textContent = `${n}個の設定を変えています`;
  };
  $$("[data-k]", root).forEach((el) => {
    const ev = el.type === "checkbox" || el.tagName === "SELECT" ? "change" : "input";
    el.addEventListener(ev, () => {
      const k = el.dataset.k;
      const v = el.type === "checkbox" ? (el.checked ? "true" : "false") : el.value;
      if (v === orig[k]) delete changes[k]; else changes[k] = v;
      el.closest(".prop").classList.toggle("changed", k in changes);
      update();
    });
  });
  $("[data-q]", root).addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    $$(".prop", root).forEach((r) => (r.hidden = q && !r.dataset.search.includes(q)));
    $$(".pgroup", root).forEach((g) => { g.open = !!q || g.open; g.hidden = q && !$$(".prop", g).some((r) => !r.hidden); });
  });
  $("[data-openall]", root).onclick = () => $$(".pgroup", root).forEach((g) => (g.open = true));
  $("[data-undo]", root).onclick = () => subProps(root, s);
  $("[data-save]", root).onclick = async () => {
    const risky = [];
    if (changes["online-mode"] === "false") risky.push("正規版チェックを OFF にすると、誰でも他人の名前で入れてしまいます。");
    if (changes["white-list"] === "false") risky.push("ホワイトリストを OFF にすると、アドレスを知っている人なら誰でも入れます。");
    if (changes["prevent-proxy-connections"] === "true") risky.push("playit や「友達用パックを自動で配る」を使っていると、誰も入れなくなることがあります。");
    if (risky.length && !(await ask("この設定で大丈夫ですか？", `<ul>${risky.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`, "保存する", { danger: true }))) return;
    try {
      const r = await api("props_set", { id: s.id, values: changes });
      const lab = (k) => (d.items.find((i) => i.key === k) || { label: k }).label;
      let msg = "保存しました";
      if (r.live && r.live.length) msg += `。すぐ変わったもの: ${r.live.map(lab).join("、")}`;
      if (r.restart && r.restart.length) msg += `。再起動で変わるもの: ${r.restart.map(lab).join("、")}`;
      toast(msg, "ok", 7000);
      await refreshState();
      subProps(root, cur());
    } catch (e) { fail(e); }
  };
}

// ---------------- ゲームルール ----------------
async function subRules(root, s) {
  root.innerHTML = `<div class="empty">読み込んでいます…</div>`;
  let d;
  try { d = await api("gamerules_get", { id: s.id }); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const head = `<p class="muted" style="margin-top:0">ゲームの決まりごとです。サーバーを動かしたまま変えられて、すぐ反映されます。</p>`;
  if (!d.running) { root.innerHTML = head + `<div class="note"><b>サーバーを起動すると変えられます</b>ゲームルールはワールドの中に保存されているので、サーバーが動いている間に変えます。</div>`; return; }
  const groups = d.groups.map(([g, label]) => {
    const rules = d.rules.filter((r) => r.group === g);
    return rules.length ? `<details class="pgroup" open><summary><h3>${esc(label)}</h3><span class="small muted">${rules.length}個</span></summary>${rules.map((r) => `
      <div class="prop" data-search="${esc((r.label + " " + r.key + " " + (r.help || "")).toLowerCase())}">
        <div class="lbl">${esc(r.label)} ${tip(r.label, r.help)}<div class="key">${esc(r.key)}</div></div>
        <div>${r.type === "boolean" ? `<label class="switch"><input type="checkbox" data-gr="${esc(r.key)}" ${r.value ? "checked" : ""} aria-label="${esc(r.label)}"><span></span></label>`
          : `<div class="row" style="gap:6px"><input type="number" data-gri="${esc(r.key)}" value="${esc(r.value)}" style="width:140px" aria-label="${esc(r.label)}"><button class="btn sm" data-grset="${esc(r.key)}">変える</button></div>`}</div></div>`).join("")}</details>` : "";
  }).join("");
  root.innerHTML = head + `
    <div class="panel" style="margin-bottom:16px"><h3>おすすめのセット</h3><div class="row" style="margin-top:10px">${d.presets.map((p) => `<button class="btn" data-preset="${p.i}" title="${esc(p.desc)}">${esc(p.title)}</button>`).join("")}</div>
      <p class="hint">${d.presets.map((p) => `${esc(p.title)}: ${esc(p.desc)}`).join("<br>")}</p></div>
    <div class="row" style="margin-bottom:12px"><input type="search" data-q placeholder="ルールを探す（例: 持ち物、朝、火）" style="flex:1"><span class="small muted">${d.via === "msmp" ? "管理用の窓口で読み込み" : "コンソールで読み込み"}・${d.rules.length}個</span></div>
    ${d.rules.length ? groups : `<div class="note warn"><b>ゲームルールを読み込めませんでした</b>サーバーが起動しきってから、もう一度開いてください。</div>`}`;
  const set = async (key, value, el) => {
    try {
      await api("gamerule_set", { id: s.id, key, value });
      toast("変えました");
    } catch (e) {
      fail(e);
      if (el && el.type === "checkbox") el.checked = !el.checked;
    }
  };
  $$("[data-gr]", root).forEach((el) => (el.onchange = () => set(el.dataset.gr, el.checked, el)));
  $$("[data-grset]", root).forEach((b) => (b.onclick = () => set(b.dataset.grset, +$(`[data-gri="${CSS.escape(b.dataset.grset)}"]`, root).value)));
  $$("[data-preset]", root).forEach((b) => (b.onclick = async () => {
    try { const r = await api("gamerule_preset", { id: s.id, index: +b.dataset.preset }); toast(`「${r.title}」にしました（${r.done}個）`); subRules(root, s); } catch (e) { fail(e); }
  }));
  $("[data-q]", root).addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    $$(".prop", root).forEach((r) => (r.hidden = q && !r.dataset.search.includes(q)));
  });
}

// ---------------- メモリ・そのほか ----------------
async function subMemory(root, s) {
  let inf;
  try { inf = await api("server_info", { id: s.id }); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  root.innerHTML = `
    <h2>メモリ ${help("memory")}</h2>
    <p class="muted">サーバーに使わせるメモリの量です。MODの数（${inf.memory.mods}個）と最大人数から、おすすめを計算しています。次の起動から変わります。</p>
    <div data-mem style="margin-top:12px">${memoryBlock(inf.memory, s.memory_mb, { selfPlay: S.settings.self_play })}</div>
    <label class="check small" style="margin-top:10px"><input type="checkbox" data-selfplay ${S.settings.self_play ? "checked" : ""}> このPCで自分もマイクラを遊ぶ（その分のメモリを残す）</label>
    <hr class="sep">
    <div class="form-grid">
      <div class="lbl">このソフトの中での名前</div><div class="inline-field"><input type="text" data-name value="${esc(s.name)}" maxlength="40"><button class="btn" data-savename>保存</button></div>
      <div class="lbl">落ちたら自動で再起動</div><div><label class="switch"><input type="checkbox" data-auto ${inf.auto_restart ? "checked" : ""}><span></span></label><div class="hint">起動したあとに急に止まったとき、10秒後に自動で起動し直します（起動の途中で止まったときはしません）。</div></div>
      <div class="lbl">自動で再起動する回数の上限</div><div><select data-armax style="max-width:240px">${[1, 2, 3, 5, 10].map((n) => `<option value="${n}" ${+(inf.auto_restart_max || 3) === n ? "selected" : ""}>30分のうち ${n}回まで</option>`).join("")}</select>
        <div class="hint">上限をこえて落ちたら、くり返しになるので、自動の再起動をやめて知らせます（Discord にも送ります）。</div></div>
      <div class="lbl">メモリを自動で調整する</div><div><label class="switch"><input type="checkbox" data-memauto ${inf.memory_auto ? "checked" : ""}><span></span></label>
        <div class="hint">起動のとき、MODの数や人数に合わせて、おすすめの量まで<b>増やします</b>（減らしません）。メモリ不足で落ちたときは、1GBずつ増やします（PCの安全な上限まで）。</div></div>
    </div>
    <hr class="sep">
    <h2>フォルダ</h2>
    <div class="row" style="margin-top:10px"><button class="btn sm" data-of="server">サーバーのフォルダ</button><button class="btn sm" data-of="mods">mods</button><button class="btn sm" data-of="logs">ログ</button><button class="btn sm" data-of="exports">友達用パック</button></div>
    <hr class="sep">
    <h2>サーバーを消す</h2><p class="muted">ワールド・MOD・バックアップもすべて消えます。元に戻せません。</p>
    <button class="btn danger" data-del>このサーバーを消す</button>`;
  bindMemory($("[data-mem]", root), inf.memory, S.settings.self_play, async (v) => {
    try { await api("server_set", { id: s.id, profile: { memory_mb: v } }); toast(`メモリを ${gb(v)} にしました（次の起動から）`); refreshState(); } catch (e) { fail(e); }
  });
  $("[data-selfplay]", root).onchange = async (e) => { await api("set_settings", { self_play: e.target.checked }); await refreshState(); subMemory(root, cur()); };
  $("[data-savename]", root).onclick = async () => { try { await api("server_set", { id: s.id, profile: { name: $("[data-name]", root).value.trim() } }); await refreshState(); viewServer($("#main")); toast("保存しました"); } catch (e) { fail(e); } };
  $("[data-auto]", root).onchange = (e) => api("server_set", { id: s.id, profile: { auto_restart: e.target.checked } }).then(() => toast("保存しました")).catch(fail);
  $("[data-armax]", root).onchange = (e) => api("server_set", { id: s.id, profile: { auto_restart_max: +e.target.value } }).then(() => toast("保存しました")).catch(fail);
  $("[data-memauto]", root).onchange = (e) => api("server_set", { id: s.id, profile: { memory_auto: e.target.checked } }).then(() => toast(e.target.checked ? "メモリを自動で調整します" : "自動の調整をやめました")).catch(fail);
  $$("[data-of]", root).forEach((b) => (b.onclick = () => api("open_folder", { id: s.id, which: b.dataset.of }).catch(fail)));
  $("[data-del]", root).onclick = () => deleteServerFlow(s);
}

// サーバーを消す（設定タブと、サーバー画面の右上、どちらからでも使う）
async function deleteServerFlow(s) {
  if (s.status !== "stopped") { toast("先にサーバーを止めてください"); return; }
  if (!(await ask("サーバーを消す", `<p>「${esc(s.name)}」を消しますか？ワールドもバックアップも全部消えます。</p>`, "消す", { danger: true }))) return;
  if (!(await ask("本当に消しますか？", "<p>元に戻せません。</p>", "消す", { danger: true }))) return;
  try {
    await api("server_delete", { id: s.id });
    toast("消しました");
    await refreshState();
    go(S.servers.length ? "server" : "welcome", { server: S.servers[0] && S.servers[0].id, tab: "home" });
  } catch (e) { fail(e); }
}

// ---------------- 自動化・バージョン ----------------
async function subOps(root, s) {
  root.innerHTML = `<div class="empty">読み込んでいます…</div>`;
  let inf, au, vo;
  try {
    [inf, au] = await Promise.all([api("server_info", { id: s.id }), api("autostart_get")]);
  } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const me = au.servers.find((x) => x.id === s.id) || {};
  root.innerHTML = `
    <h2>自動で止める・自動で起動する</h2>
    <div class="form-grid" style="margin-top:12px">
      <div class="lbl">誰もいなくなったら止める<div class="hint">PCのメモリ・電気代の節約</div></div>
      <div><select data-astop style="max-width:280px">${[[0, "止めない"], [5, "5分後に止める"], [15, "15分後に止める"], [30, "30分後に止める"], [60, "1時間後に止める"]].map(([v, l]) => `<option value="${v}" ${+inf.auto_stop_min === v ? "selected" : ""}>${l}</option>`).join("")}</select>
        <div class="hint">誰もいない時間が続いたら、ワールドを保存してサーバーを止めます（事前生成の途中は止めません）。</div></div>
      <div class="lbl">PCを起動したら、このサーバーも起動</div>
      <div>${au.supported ? `<label class="switch"><input type="checkbox" data-ast ${me.on && au.enabled ? "checked" : ""}><span></span></label>
        <div class="hint">Windows にログインすると、このソフトが画面を出さずに起動して、このサーバーを動かします。止めたいときはこのソフトを開いて「停止」を押します。</div>`
        : `<div class="note warn">この機能は Windows だけで使えます。</div>`}</div>
    </div>
    <hr class="sep">
    <div data-sched></div>
    <hr class="sep">
    <h2>バージョンを変える（アップデート）</h2>
    <div data-ver><div class="empty">新しいバージョンを調べています…</div></div>`;
  $("[data-astop]", root).onchange = (e) => api("server_set", { id: s.id, profile: { auto_stop_min: +e.target.value } }).then(() => toast("保存しました")).catch(fail);
  const ast = $("[data-ast]", root);
  if (ast) ast.onchange = async (e) => {
    const on = e.target.checked;
    try {
      const others = au.servers.some((x) => x.id !== s.id && x.on);
      await api("autostart_set", { server_id: s.id, on, enabled: on || others });
      toast(on ? "PCを起動したら、このサーバーも起動します" : "自動で起動しないようにしました");
    } catch (er) { e.target.checked = !on; fail(er); }
  };
  renderSchedule($("[data-sched]", root), s);
  const vel = $("[data-ver]", root);
  try { vo = await api("version_options", { id: s.id }); } catch (e) { vel.innerHTML = `<div class="note warn">${esc(e.message)}</div>`; return; }
  vel.innerHTML = `<p class="muted" style="max-width:76ch">今は <b>マイクラ ${esc(vo.mc)}・${esc(vo.loader_label)}</b> です。ワールドを残したまま新しいバージョンにできます。先にワールドを自動でバックアップし、${modWord(s)}も新しいバージョン用に入れ替えます。<b>古いバージョンには戻せません</b>（ワールドが壊れるため）。</p>
    ${vo.newer_loader ? `<div class="note" style="margin:10px 0"><b>${esc(vo.loader_label.split(" ")[0])} の新しい版（${esc(vo.newer_loader)}）があります</b>マイクラのバージョンはそのままで、土台だけ新しくできます（不具合の修正など）。<div style="margin-top:8px"><button class="btn sm" data-vsame>土台を新しくする</button></div></div>` : ""}
    ${vo.candidates.length ? `<div class="row" style="margin-top:10px"><select data-vmc style="max-width:220px">${vo.candidates.map((v) => `<option>${esc(v)}</option>`).join("")}</select><button class="btn" data-vchk>このバージョンにできるか調べる</button></div>`
      : `<div class="note ok"><b>一番新しいバージョンです</b>マイクラ ${esc(vo.mc)} より新しい正式版はまだありません。</div>`}
    <div data-vplan style="margin-top:12px"></div>`;
  const showPlan = async (mc) => {
    const out = $("[data-vplan]", vel);
    out.innerHTML = `<div class="empty">調べています…</div>`;
    let pl;
    try { pl = await api("version_plan", { id: s.id, mc }); } catch (e) { out.innerHTML = `<div class="note warn">${esc(e.message)}</div>`; return; }
    if (!pl.ok) { out.innerHTML = `<div class="note error"><b>できません</b>${esc(pl.reason)}</div>`; return; }
    const W = modWord(s);
    out.innerHTML = `<div class="panel">
      <h3>${pl.same_mc ? `${esc(pl.loader_label)} を ${esc(pl.loader_version)} に新しくする` : `マイクラ ${esc(pl.from_mc)} → ${esc(pl.mc)}`}</h3>
      <div class="grid2" style="margin-top:10px">
        <div><b style="color:var(--ok-text)">使える${W}（${pl.mods_ok.length}個）</b><ul class="small">${pl.mods_ok.slice(0, 30).map((m) => `<li>${esc(m.title)} ${m.same ? "（そのまま）" : `${esc(m.from || "")} → ${esc(m.to || "")}`}</li>`).join("") || "<li>なし</li>"}</ul></div>
        <div><b style="color:var(--ng-text)">新しいバージョン用がない${W}（${pl.mods_ng.length}個）</b><ul class="small">${pl.mods_ng.map((m) => `<li>${esc(m.title)}</li>`).join("") || "<li>なし</li>"}</ul>
          ${pl.mods_ng.length ? `<p class="hint">これらは OFF にします（消しません）。対応したら「更新」でまた使えます。</p>` : ""}
          ${pl.manual.length ? `<p class="hint">手で入れた${W}（${pl.manual.map(esc).join("、")}）は自動で入れ替えられないので、自分で確認してください。</p>` : ""}</div>
      </div>
      ${pl.mods_ng.length ? `<div class="note warn" style="margin-top:10px"><b>使えなくなる${W}があります</b>その${W}で作ったブロックやアイテムは、ワールドから消えることがあります。</div>` : ""}
      <div class="row" style="margin-top:12px"><button class="btn primary" data-vgo ${s.status === "stopped" ? "" : "disabled"}>バックアップしてから変える</button>${s.status === "stopped" ? "" : '<span class="small muted">先にサーバーを止めてください</span>'}</div></div>`;
    $("[data-vgo]", out).onclick = async () => {
      if (!(await ask("バージョンを変える", `<p>${pl.same_mc ? "土台を新しくしますか？" : `マイクラ <b>${esc(pl.mc)}</b> にしますか？`}</p><p class="small muted">先にワールドをバックアップします。変えたあと、友達用パックも自動で新しくなります（友達も新しいバージョンで入ります）。</p>`, "変える"))) return;
      try {
        const r = await runJob(api("version_change", { id: s.id, mc: pl.mc }), "バージョンを変えています");
        await refreshState();
        info("バージョンを変えました", `<p>マイクラ ${esc(r.from)} → <b>${esc(r.mc)}</b></p>${r.changed.length ? `<p class="small">入れ替えた${W}: ${r.changed.map(esc).join("、")}</p>` : ""}${r.disabled.length ? `<p class="small">OFFにした${W}: ${r.disabled.map(esc).join("、")}</p>` : ""}<p class="small muted">まず「起動テスト」で、ちゃんと起動するか試すのがおすすめです。</p>`);
        viewServer($("#main"));
      } catch (e) { if (!e.shown) fail(e); }
    };
  };
  const vchk = $("[data-vchk]", vel);
  if (vchk) vchk.onclick = () => showPlan($("[data-vmc]", vel).value);
  const vsame = $("[data-vsame]", vel);
  if (vsame) vsame.onclick = () => showPlan(vo.mc);
}

// ---------------- 通知（Discord・デスクトップ） ----------------
async function subNotify(root, s) {
  let d;
  try { d = await api("notify_get", { id: s.id }); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  root.innerHTML = `<h2>Discord に知らせる</h2>
    <p class="muted" style="max-width:76ch">サーバーの起動・停止・トラブル・あやしいアクセスなどを、Discord のチャンネルに自動で書き込みます。BOT を作る必要はありません（「Webhook」という URL を貼るだけ）。</p>
    <ol class="small" style="padding-left:1.4em"><li>Discord で、知らせたいチャンネルの ⚙（チャンネルの編集）を開く</li><li>「連携サービス」→「ウェブフック」→「新しいウェブフック」</li><li>「ウェブフックURLをコピー」を押して、下に貼る</li></ol>
    <div class="inline-field" style="max-width:720px;margin-top:8px"><input type="password" data-wh placeholder="${d.webhook_set ? "設定ずみ（変えるときだけ貼る）" : "https://discord.com/api/webhooks/…"}" autocomplete="off">
      <button class="btn primary" data-whsave>保存</button><button class="btn" data-whtest>テスト送信</button>${d.webhook_set ? '<button class="btn ghost" data-whdel>やめる</button>' : ""}</div>
    <p class="hint">${d.webhook_set ? '<span class="badge ok">設定ずみ</span> ' : ""}Webhook URL を知っている人は、そのチャンネルに書き込めます。人に見せないでください（このPCの中だけに保存します）。すべてのサーバーで同じチャンネルを使います。</p>
    ${d.error ? `<div class="note warn"><b>最後の送信に失敗しました</b>${esc(d.error)}</div>` : ""}
    <h3 style="margin-top:16px">Discord に出る名前</h3>
    <div class="inline-field" style="max-width:520px;margin-top:6px"><input type="text" data-whname value="${esc(d.name)}" maxlength="80"><button class="btn" data-whnamesave>保存</button></div>
    <h3 style="margin-top:16px">このサーバーで知らせること</h3>
    <div class="stack" style="margin-top:8px">${Object.entries(d.events).map(([k, l]) => `<label class="check"><input type="checkbox" data-ev value="${k}" ${d.on.includes(k) ? "checked" : ""}> ${esc(l)}</label>`).join("")}</div>
    <hr class="sep">
    <h2>このPCに知らせる（デスクトップ通知）</h2>
    <div class="row" style="margin-top:10px"><label class="switch"><input type="checkbox" data-desk ${d.desktop ? "checked" : ""}><span></span></label>
      <div><b>サーバーが落ちたとき・あやしいアクセスがあったとき</b><div class="small muted">Windows の右下にお知らせを出します（画面を閉じていても出ます）。</div></div></div>
    <hr class="sep"><div class="note">💬 <b>Discord から操作する</b>設定は、<b>ホーム画面</b>（サーバー一覧のページ）の「Discord から操作する（ボット）」に移りました。マイクラ・ARK・Palworld・Terraria 共通で、ボットは1つだけです。</div>`;
  $("[data-whsave]", root).onclick = async () => {
    const v = $("[data-wh]", root).value.trim();
    if (!v) return toast("Webhook URL を貼ってください", "warn");
    try { await api("notify_set", { id: s.id, webhook: v }); toast("保存しました"); subNotify(root, s); } catch (e) { fail(e); }
  };
  $("[data-whnamesave]", root).onclick = async () => {
    try { await api("notify_set", { name: $("[data-whname]", root).value }); toast("名前を変えました"); subNotify(root, s); } catch (e) { fail(e); }
  };
  $("[data-whtest]", root).onclick = async () => {
    try { await api("notify_test", { webhook: $("[data-wh]", root).value.trim() || null }); toast("送りました。Discord を見てください"); } catch (e) { fail(e); }
  };
  const del = $("[data-whdel]", root);
  if (del) del.onclick = async () => { await api("notify_set", { id: s.id, webhook: "" }).catch(fail); subNotify(root, s); };
  $$("[data-ev]", root).forEach((c) => (c.onchange = () => api("notify_set", { id: s.id, events: $$("[data-ev]:checked", root).map((x) => x.value) }).then(() => toast("保存しました")).catch(fail)));
  $("[data-desk]", root).onchange = (e) => api("notify_set", { desktop: e.target.checked }).then(() => toast("保存しました")).catch(fail);
}

// ---------------- 外から操作 ----------------
async function subRemote(root, s) {
  let d;
  try { d = await api("remote_get", { id: s.id }); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const card = (kind, title, on, desc, extra = "", disabled = false) => `<div class="panel" style="margin-bottom:12px"><div class="row top">
      <label class="switch" style="margin-top:4px"><input type="checkbox" data-rk="${kind}" ${on ? "checked" : ""} ${disabled ? "disabled" : ""} aria-label="${esc(title)}"><span></span></label>
      <div class="grow"><h3>${title}</h3><div class="small muted" style="max-width:78ch">${desc}</div>${extra}</div></div></div>`;
  root.innerHTML = `<p class="muted" style="margin-top:0">ほかのソフトから、このサーバーを見たり操作したりする仕組みです。<b style="color:var(--bone)">普通は「管理用の窓口」以外は OFF のままで大丈夫です。</b>変えたら、次の起動から使えます。</p>
    ${card("msmp", "管理用の窓口（おすすめ・ON）", d.msmp && d.msmp_supported,
      d.msmp_supported ? "このソフトが、参加者・ホワイトリスト・設定・ゲームルールを正確に、動かしたまま扱うのに使います。<br>このPCの中（127.0.0.1）だけで待ち受け、40文字の合言葉で守るので、外からは使えません。"
        : "マイクラ 1.21.9 以降の機能です。このサーバーのバージョンでは使えません（ログを読む方法で動きます）。",
      d.msmp_supported ? `<div class="chips" style="margin-top:8px">${d.running ? (d.msmp_connected ? '<span class="badge ok">つながっています</span>' : `<span class="badge warn">つながっていません</span>${d.msmp_error ? ` <span class="small">${esc(d.msmp_error)}</span>` : ""}`) : '<span class="badge">サーバーが止まっています</span>'}${d.msmp_port ? `<span class="badge">ポート ${esc(d.msmp_port)}（PCの中だけ）</span>` : ""}</div>` : "", !d.msmp_supported)}
    ${card("rcon", "RCON（外からコマンドを送る）", d.rcon,
      "ほかの管理ソフトやBOTから、パスワードつきでコマンドを送れるようにします。<br><b style='color:var(--gold)'>⚠ パスワードを知った人はサーバーを自由に操作できます。RCON のポートは、ポート開放しないでください。</b>",
      d.rcon ? `<div class="row" style="margin-top:8px"><span class="badge">ポート ${esc(d.rcon_port)}</span><span class="small">パスワード:</span><code class="kbd" data-pw>••••••••</code><button class="btn sm" data-showpw>見る</button><button class="btn sm" data-copypw>コピー</button></div><p class="hint">パスワードは自動で作った強いものです。ONにし直すと新しくなります。</p>` : "")}
    ${card("query", "Query（状態の問い合わせ）", d.query, "サーバー一覧サイトや監視ツールが、人数やMOD・バージョンを調べられるようにします。プレイヤーの名前が見えるので、必要なときだけ ON にします。",
      d.query ? `<div class="chips" style="margin-top:8px"><span class="badge">UDP ポート ${esc(d.query_port)}</span></div>` : "")}
    ${card("jmx", "JMX（処理時間の数字）", d.jmx, "Java の監視ツール（JConsole など）で、1コマの処理時間を見られるようにします。詳しい人向けです。このソフトの「重さ」タブだけなら不要です。")}`;
  $$("[data-rk]", root).forEach((el) => (el.onchange = async () => {
    if (el.checked && el.dataset.rk === "rcon" && !(await ask("RCON を ON にする", "<p>パスワードを知った人は、このサーバーを自由に操作できます。RCON のポートはポート開放しないでください。</p>", "ON にする", { danger: true }))) { el.checked = false; return; }
    try { await api("remote_set", { id: s.id, kind: el.dataset.rk, on: el.checked }); toast("変えました（次の起動から）"); subRemote(root, s); } catch (e) { fail(e); el.checked = !el.checked; }
  }));
  const sp = $("[data-showpw]", root);
  if (sp) sp.onclick = () => { const c = $("[data-pw]", root); c.textContent = c.textContent.startsWith("•") ? d.rcon_password : "••••••••"; };
  const cp = $("[data-copypw]", root);
  if (cp) cp.onclick = () => { navigator.clipboard.writeText(d.rcon_password); toast("コピーしました"); };
}

// ---------------- 操作の記録 ----------------
async function subLog(root, s) {
  let d;
  try { d = await api("audit_get", { id: s.id }); } catch (e) { root.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  root.innerHTML = `<p class="muted" style="margin-top:0">このソフトで行った管理の操作（設定の変更・BAN・管理コマンドなど）の記録です。誰が何をしたか、あとで確かめられます。</p>
    ${d.items.length ? `<table class="tbl"><thead><tr><th style="width:180px">日時</th><th style="width:200px">操作</th><th>内容</th></tr></thead><tbody>
      ${d.items.map((x) => `<tr><td class="small">${esc(x.when)}</td><td>${esc(x.action)}</td><td class="small" style="word-break:break-all">${esc(x.detail)}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">まだ記録はありません</div>`}`;
}

// ---------------- 管理コマンド ----------------
const ACTION_FIELDS = {
  time: [["value", "時間", "select", [["day", "朝"], ["noon", "昼"], ["night", "夜"], ["midnight", "真夜中"]]]],
  weather: [["value", "天気", "select", [["clear", "晴れ"], ["rain", "雨"], ["thunder", "雷雨"]]]],
  difficulty: [["value", "難しさ", "select", [["peaceful", "ピース"], ["easy", "イージー"], ["normal", "ノーマル"], ["hard", "ハード"]]]],
  setworldspawn: [["x", "X", "number"], ["y", "Y", "number"], ["z", "Z", "number"]],
  worldborder: [["size", "大きさ（ブロック）", "number", 10000]],
  say: [["message", "メッセージ", "text"]],
  gamemode: [["mode", "モード", "select", [["survival", "サバイバル"], ["creative", "クリエイティブ"], ["adventure", "アドベンチャー"], ["spectator", "スペクテイター"]]], ["player", "だれ", "player*"]],
  tp: [["player", "誰を", "player*"], ["target", "だれのところへ", "player"]],
  give: [["player", "誰に", "player*"], ["item", "アイテムのID（入れると候補が出ます）", "item", "minecraft:diamond"], ["count", "個数", "number", 1]],
  xp: [["player", "誰に", "player*"], ["amount", "レベル", "number", 5]],
  heal: [["player", "誰を", "player*"]], clear_effects: [["player", "だれの", "player*"]], spawnpoint: [["player", "だれの", "player"]],
  kick: [["player", "誰を", "player"], ["reason", "理由（なくてもOK）", "text"]],
  ban: [["player", "誰を", "player"], ["reason", "理由（なくてもOK）", "text"]],
  pardon: [["player", "だれの", "player"]], ban_ip: [["target", "IPアドレス か 名前", "text"], ["reason", "理由", "text"]],
  pardon_ip: [["target", "IPアドレス", "text"]], op: [["player", "誰を", "player"]], deop: [["player", "誰を", "player"]],
  whitelist_add: [["player", "誰を", "player"]], whitelist_remove: [["player", "誰を", "player"]],
};
const DANGER_ACTIONS = new Set(["ban", "ban_ip", "op", "kill_items", "reload", "worldborder", "setworldspawn"]);

async function tabCommands(body, s) {
  let d;
  try { d = await api("op_catalog", { id: s.id }); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const groups = [["world", "ワールド"], ["people", "プレイヤー"], ["manage", "管理"]];
  const players = d.players;
  const input = (a, f) => {
    const [k, label, type, def] = f;
    if (type === "select") return `<label class="small">${esc(label)}<select data-f="${k}">${def.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select></label>`;
    if (type && type.startsWith("player")) {
      const sel = type.endsWith("*");
      return `<label class="small">${esc(label)}<input type="text" data-f="${k}" list="pl_${a}" placeholder="名前" autocomplete="off"></label>
        <datalist id="pl_${a}">${sel ? '<option value="@a">全員</option>' : ""}${players.map((p) => `<option value="${esc(p)}">`).join("")}</datalist>`;
    }
    if (type === "item") return `<label class="small item-pick">${esc(label)}<input type="text" data-f="${k}" data-itempick value="${esc(def || "")}" autocomplete="off" placeholder="例: ダイヤ / diamond / create:"><div class="item-sug" data-sug hidden></div></label>`;
    return `<label class="small">${esc(label)}<input type="${type === "number" ? "number" : "text"}" data-f="${k}" value="${def != null ? esc(def) : ""}" ${type === "number" ? 'style="width:110px"' : ""}></label>`;
  };
  body.innerHTML = `${!d.running ? `<div class="note" style="margin-bottom:14px"><b>サーバーを起動すると使えます</b></div>` : ""}
    <p class="muted" style="margin-top:0">よく使う管理コマンドを、入力欄つきのボタンにしました。実行すると、実際に送ったコマンドと、サーバーの返事が下に出ます。名前や数字はチェックしてから送るので、打ち間違いで変なことは起きません。</p>
    <div class="cmd-grid">${groups.map(([g, gl]) => `<div class="panel"><h3>${gl}</h3><div class="stack" style="margin-top:10px">${d.actions.filter((a) => a.group === g).map((a) => `
      <form class="cmd" data-act="${a.id}"><div class="row" style="gap:8px;align-items:flex-end">${(ACTION_FIELDS[a.id] || []).map((f) => input(a.id, f)).join("")}
        <button class="btn sm ${DANGER_ACTIONS.has(a.id) ? "danger" : ""}" type="submit" ${d.running ? "" : "disabled"}>${esc(a.label)}</button></div></form>`).join("")}</div></div>`).join("")}</div>
    <div class="panel" style="margin-top:16px"><div class="row"><h3 class="grow">アイテムのID一覧 ${help("itemid")}</h3>${itemLangToggle()}<button class="btn sm" data-itemload>一覧を読み込む</button></div>
      <p class="muted small" style="margin:6px 0">/give などに入れる<b>アイテムのID</b>です。名前（日本語・英語）か、MODの名前で探せます。「コピー」を押すと、IDをコピーできます。入れたMODのアイテムも出ます。</p>
      <input type="text" data-itemq placeholder="探す（例: ダイヤ、sword、create）" disabled>
      <div data-itemlist style="margin-top:8px"><div class="faint small">「一覧を読み込む」を押してください（初回は、公式のデータを取るので、少し時間がかかります）。</div></div></div>
    <div class="panel" style="margin-top:16px" data-dict></div>
    <div class="panel" style="margin-top:16px"><h3>結果</h3><div class="console" data-out style="height:180px;margin-top:8px"><div class="faint">（コマンドを実行すると、ここに返事が出ます）</div></div></div>`;
  renderCmdDict($("[data-dict]", body), s, d.running);
  // ---- アイテムID（候補とID一覧）
  let catalog = null, loading = null;
  const loadCatalog = () => {
    if (catalog) return Promise.resolve(catalog);
    if (!loading) loading = runJob(api("item_catalog", { id: s.id }), "アイテムの一覧を作っています", { note: "初回だけ、公式のデータを取って作ります（数十秒）。2回目からは、すぐ出ます。" })
      .then((r) => { catalog = r.items.map((x) => ({ ...x, key: (x.id + " " + x.name + " " + x.ja + " " + x.src).toLowerCase() })); if (r.warn) toast(r.warn, "warn"); return catalog; })
      .catch((e) => { loading = null; if (!e.shown) fail(e); return null; });
    return loading;
  };
  const search = (q, n) => {
    q = q.trim().toLowerCase();
    if (!catalog) return [];
    const words = q.split(/\s+/).filter(Boolean);
    const out = [];
    for (const x of catalog) { if (words.every((w) => x.key.includes(w))) { out.push(x); if (out.length >= n) break; } }
    return out;
  };
  const rowHtml = (x) => `<button type="button" class="item-row" data-iid="${esc(x.id)}">${itemIcon(x.id, s)}<span class="nm">${esc(itemNames(x)[0])}</span><code>${esc(x.id)}</code>${x.src !== "マイクラ" ? `<span class="badge">${esc(x.src)}</span>` : ""}</button>`;
  $$("[data-itempick]", body).forEach((inp) => {
    const sug = inp.parentElement.querySelector("[data-sug]");
    const pv = document.createElement("span");
    pv.style.cssText = "position:absolute;right:10px;bottom:7px;pointer-events:none;line-height:0";
    inp.style.paddingRight = "42px";
    inp.parentElement.style.position = "relative";
    inp.after(pv);
    let pvId = "";
    const paintPv = () => {
      let v = inp.value.trim();
      if (v && !v.includes(":")) v = "minecraft:" + v;
      if (!/^[a-z0-9_.-]+:[a-z0-9_.-]+$/.test(v)) { pv.innerHTML = ""; pvId = ""; return; }
      if (v !== pvId) { pvId = v; pv.innerHTML = itemIcon(v, s, 28).replace("<img ", "<img data-pv "); }
    };
    inp.addEventListener("input", paintPv);
    paintPv();
    const show = async () => {
      if (!catalog) { const c = await loadCatalog(); if (!c) return; }
      const q = inp.value.replace(/^minecraft:/, "");
      const r = search(q === "diamond" && inp.value === "minecraft:diamond" ? "" : q, 30);
      sug.innerHTML = r.length ? r.map(rowHtml).join("") : `<div class="faint small" style="padding:6px">見つかりません（IDを直接入れてもOKです）</div>`;
      sug.hidden = false;
    };
    inp.addEventListener("focus", () => { if (catalog) show(); });
    inp.addEventListener("click", show);
    inp.addEventListener("input", show);
    sug.addEventListener("mousedown", (e) => {
      const b = e.target.closest("[data-iid]");
      if (!b) return;
      e.preventDefault(); inp.value = b.dataset.iid; sug.hidden = true; paintPv();
    });
    inp.addEventListener("blur", () => setTimeout(() => (sug.hidden = true), 150));
  });
  const il = $("[data-itemlist]", body), iq = $("[data-itemq]", body);
  const paintList = () => {
    const r = search(iq.value, 80);
    il.innerHTML = r.length ? `<table class="tbl"><tbody>${r.map((x) => { const [a, b] = itemNames(x); return `<tr><td style="width:40px">${itemIcon(x.id, s, 32)}</td><td>${esc(a)}${b ? `<div class="small muted">${esc(b)}</div>` : ""}</td><td><code>${esc(x.id)}</code></td><td class="small muted">${esc(x.src)}</td><td style="text-align:right"><button class="btn sm" data-icopy="${esc(x.id)}">コピー</button></td></tr>`; }).join("")}</tbody></table><div class="small muted" style="margin-top:6px">${r.length >= 80 ? "上から80件を出しています。文字を入れて、しぼってください。" : ""}</div>` : `<div class="faint small">見つかりません</div>`;
    $$("[data-icopy]", il).forEach((b) => (b.onclick = () => { navigator.clipboard.writeText(b.dataset.icopy); toast("コピーしました: " + b.dataset.icopy); }));
  };
  $("[data-itemload]", body).onclick = async () => { const c = await loadCatalog(); if (!c) return; iq.disabled = false; iq.focus(); paintList(); };
  iq.addEventListener("input", paintList);
  $$("[data-ilang]", body).forEach((b) => (b.onclick = () => {
    setItemLang(b.dataset.ilang);
    $$("[data-ilang]", body).forEach((x) => x.classList.toggle("primary", x.dataset.ilang === b.dataset.ilang));
    if (catalog) paintList();
  }));
  $$("form.cmd", body).forEach((f) => f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const action = f.dataset.act;
    const args = Object.fromEntries($$("[data-f]", f).map((i) => [i.dataset.f, i.value]));
    const label = d.actions.find((a) => a.id === action).label;
    if (DANGER_ACTIONS.has(action) && !(await ask(label, `<p>「${esc(label)}」を実行しますか？</p>`, "実行する", { danger: true }))) return;
    const out = $("[data-out]", body);
    try {
      const r = await api("op_action", { id: s.id, action, args });
      out.innerHTML = `<div class="me">&gt; ${esc(r.command)}</div>${(r.output.length ? r.output : ["（返事はありませんでした）"]).map((l) => `<div>${esc(l)}</div>`).join("")}`;
    } catch (err) { out.innerHTML = `<div class="e">${esc(err.message)}</div>`; }
  }));
}

// ---------------- BAN（メンバーのタブの下） ----------------
async function renderBans(el, s) {
  let b;
  try { b = await api("bans_get", { id: s.id }); } catch (e) { el.innerHTML = ""; return; }
  const list = (items, kind, keyf) => items.length ? `<div class="list" style="margin-top:10px">${items.map((x) => `<div class="item noselect"><span class="face player" style="padding:0;box-shadow:none;background:none"><span class="face" style="background:var(--redstone)"></span></span>
      <div><div class="t">${esc(keyf(x))}</div><div class="s">${esc(x.reason || "")}${x.created ? "　" + esc(x.created) : ""}</div></div>${cpBtn(keyf(x), "コピー", kind === "ip" ? "IPアドレス" : "名前")}<button class="btn sm ghost" data-unban="${kind}" data-t="${esc(keyf(x))}">解除</button></div>`).join("")}</div>` : `<p class="faint small">いません</p>`;
  el.innerHTML = `<h2 style="margin-top:26px">BAN（入れなくする）</h2><p class="muted small">荒らしなどを、二度と入れないようにします。名前を変えて来る人には IPアドレスでの BAN もできます（家族など同じ回線の人も入れなくなるので注意）。</p>
    <div class="grid2" style="margin-top:12px">
      <div class="panel"><h3>名前で BAN</h3><div class="inline-field" style="margin-top:8px"><input type="text" data-bn placeholder="マイクラの名前"><input type="text" data-br placeholder="理由（なくてもOK）"><button class="btn danger" data-ban="player">BAN</button></div>${list(b.players, "player", (x) => x.name)}</div>
      <div class="panel"><h3>IPアドレスで BAN</h3><div class="inline-field" style="margin-top:8px"><input type="text" data-in placeholder="IPアドレス"><input type="text" data-ir placeholder="理由（なくてもOK）"><button class="btn danger" data-ban="ip">BAN</button></div>${list(b.ips, "ip", (x) => x.ip)}</div>
    </div>`;
  $$("[data-ban]", el).forEach((btn) => (btn.onclick = async () => {
    const kind = btn.dataset.ban;
    const target = (kind === "player" ? $("[data-bn]", el) : $("[data-in]", el)).value.trim();
    const reason = (kind === "player" ? $("[data-br]", el) : $("[data-ir]", el)).value.trim();
    if (!target) return;
    if (!(await ask("BAN する", `<p>「${esc(target)}」を BAN しますか？（あとで解除できます）</p>`, "BAN する", { danger: true }))) return;
    try { await api("ban_add", { id: s.id, kind, target, reason }); toast("BAN しました"); renderBans(el, s); } catch (e) { fail(e); }
  }));
  $$("[data-unban]", el).forEach((btn) => (btn.onclick = async () => {
    try { await api("ban_remove", { id: s.id, kind: btn.dataset.unban, target: btn.dataset.t }); toast("解除しました"); renderBans(el, s); } catch (e) { fail(e); }
  }));
}
