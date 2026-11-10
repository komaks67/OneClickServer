/* ホーム画面の「Discord から操作する」(5ステップの設定)。開いたときと、操作したときだけ描き直す */
(function () {
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const q = (sel, root) => (root || document).querySelector(sel);
  const body = document.getElementById("botBody"), card = document.getElementById("botCard"), tgl = document.getElementById("botToggle");
  let opened = false;

  async function api(name, params) { return post(name, params); }       // home.js の post()
  const fail = e => toast(e.message || String(e));
  const done = ok => `<span class="bbadge ${ok ? "ok" : ""}">${ok ? "✓ できた" : "まだ"}</span>`;

  async function render() {
    let d;
    try { d = await api("bot_get"); } catch (e) { body.innerHTML = `<p class="small">${esc(e.message)}</p>`; return; }
    const servers = d.servers.length
      ? d.servers.map(s => `<div class="bsrv"><span class="nm">${esc(s.name)}</span>
          <span class="bbadge ${s.panel ? "ok" : ""}">${s.panel ? "パネルあり" : "パネルなし"}</span>
          <button class="btn main" data-panel="${esc(s.sid)}">${s.panel ? "パネルを置き直す" : "パネルを置く"}</button></div>`).join("")
      : `<p class="small">まだサーバーがありません。先に、上の「＋ 新しく作る」でサーバーを作ってください。</p>`;
    body.innerHTML = `
      <div style="margin:0 0 12px"><span class="bbadge ${d.ok ? "ok" : ""}">${d.ok ? "● " : "○ "}${esc(d.status)}</span></div>
      <section class="bstep"><h3>${done(d.token_set)}① ボットを作って、トークンを貼る</h3>
        <details><summary><b>ボットの作りかた(はじめての人・約3分)</b></summary>
          <ol><li>下の「開発者ページを開く」を押して、Discord にログインします。</li>
            <li>右上の <b>「New Application」</b> → 好きな名前を付けて「Create」。</li>
            <li>左の <b>「Bot」</b> を開いて、<b>「Reset Token」</b> → 出た長い文字を<b>コピー</b>します。(この文字は一度しか出ません)</li>
            <li>その文字を、下の欄に貼ります。もうボットがあるなら、そのトークンが使えます。</li></ol></details>
        <div class="btns"><button class="btn" data-open="https://discord.com/developers/applications">開発者ページを開く</button></div>
        <label class="small">ボットのトークン
          <input type="password" id="bTok" placeholder="${d.token_set ? "設定ずみ(変えるときだけ貼る)" : "トークンを貼る"}" autocomplete="off" spellcheck="false"></label>
        <div class="btns"><button class="btn main" id="bSaveTok">トークンを保存する</button>
          ${d.token_set ? '<button class="btn" id="bDel">ボットを使うのをやめる</button>' : ""}</div></section>
      <section class="bstep"><h3>② ボットを、あなたのサーバーに入れる</h3>
        <p class="small">ボタンを押すと Discord の画面が開きます。入れたいサーバーを選んで「認証」を押してください。必要な権限は、最初から選ばれています。</p>
        <div class="btns"><button class="btn" id="bInv" ${d.token_set ? "" : "disabled"}>ボットをサーバーに入れる(招待リンクを開く)</button></div></section>
      <section class="bstep"><h3>${done(!!d.channel)}③ ボタンを置くチャンネルを選ぶ</h3>
        <div class="btns"><button class="btn" id="bChLoad" ${d.token_set ? "" : "disabled"}>チャンネルを読み込む</button>
          <select id="bChSel" style="min-width:260px" disabled><option>${d.channel ? "選んであります(ID: " + esc(d.channel) + ")" : "← 先に「読み込む」を押す"}</option></select></div>
        <p class="small" id="bChNote"></p></section>
      <section class="bstep"><h3>${done(d.users.length > 0)}④ 操作できる人を決める</h3>
        <details><summary><b>自分のIDの調べかた</b></summary>
          <ol><li>Discord の「ユーザー設定」(歯車)→ <b>「詳細設定」</b> → <b>「開発者モード」をON</b>。</li>
            <li>チャンネルのメンバー一覧などで、<b>自分の名前を右クリック</b> → <b>「ユーザーIDをコピー」</b>。</li>
            <li>下の欄に貼ります。複数の人は、カンマ(,)で区切ります。</li></ol></details>
        <label class="small">ユーザーID(数字)
          <input type="text" id="bUs" value="${esc(d.users.join(", "))}" placeholder="例: 1022680353053356114, 2233445566778899001"></label>
        <div class="btns"><button class="btn main" id="bSaveUs">保存する</button></div></section>
      <section class="bstep"><h3>⑤ 確認して、サーバーごとにパネルを置く</h3>
        <div class="btns"><button class="btn" id="bTest">つながるかテスト</button></div>
        <div class="small" id="bRes" style="margin:8px 0"></div>
        ${servers}</section>
      <section class="bstep"><h3>⑥ PCの見張り(ディスクの空き・重さ)</h3>
        <p class="small">PCが危ないときだけ、パネルを置いたチャンネルに知らせます。同じお知らせは<b>1時間に1回</b>まで。直ったときは、1回だけ「もとに戻りました」と出ます。</p>
        <label class="chk"><input type="checkbox" id="aOn" ${d.alerts.on ? "checked" : ""}>警告を出す</label>
        <div class="row">
          <label>ディスクの空きが、この GB より少ない<input type="number" id="aDisk" min="1" max="2000" value="${esc(d.alerts.disk_gb)}"></label>
          <label>CPU が、この % 以上(5分続いたとき)<input type="number" id="aCpu" min="50" max="100" value="${esc(d.alerts.cpu)}"></label>
          <label>メモリが、この % 以上<input type="number" id="aMem" min="50" max="100" value="${esc(d.alerts.mem)}"></label></div>
        <div class="btns"><button class="btn main" id="bSaveAl">保存する</button></div></section>
      <p class="small">トークンは、ボットを自由に動かせる鍵です。人に見せないでください(このPCの中だけに、暗号化して保存します)。決めた人以外がボタンを押しても、動きません。</p>`;
    const again = (ms = 800) => setTimeout(render, ms);
    const sel = q("#bChSel", body), note = q("#bChNote", body), res = q("#bRes", body);
    body.querySelectorAll("[data-open]").forEach(b => b.onclick = async () => {
      try { await api("open_url", {url: b.dataset.open}); } catch (e) { fail(e); }
    });
    q("#bSaveTok", body).onclick = async () => {
      const t = q("#bTok", body).value.trim();
      if (!t) return toast("トークンを貼ってください");
      try { await api("bot_set", {token: t}); toast("保存しました。ボットをつなぎます"); again(2500); } catch (e) { fail(e); }
    };
    const del = q("#bDel", body);
    if (del) del.onclick = async () => {
      if (!confirm("保存してあるトークンを消して、Discord からの操作をやめますか?\n(Discord に置いたパネルのメッセージは、手で消してください)")) return;
      try { await api("bot_set", {token: ""}); toast("やめました"); render(); } catch (e) { fail(e); }
    };
    q("#bInv", body).onclick = async () => {
      try { const r = await api("bot_invite"); await api("open_url", {url: r.url}); toast("Discord の画面を開きました。入れるサーバーを選んで「認証」を押してください"); } catch (e) { fail(e); }
    };
    q("#bChLoad", body).onclick = async ev => {
      const b = ev.currentTarget; b.disabled = true; note.textContent = "読み込み中…";
      try {
        const r = await api("bot_channels");
        if (!r.guilds.length) { note.textContent = r.note; sel.disabled = true; return; }
        sel.innerHTML = `<option value="">チャンネルを選ぶ…</option>` + r.guilds.map(g =>
          `<optgroup label="${esc(g.name)}">${g.channels.map(c => `<option value="${esc(c.id)}" ${c.id === d.channel ? "selected" : ""}># ${esc(c.name)}</option>`).join("")}</optgroup>`).join("");
        sel.disabled = false; note.textContent = "選ぶと、すぐ保存されます。";
      } catch (e) { note.textContent = ""; fail(e); } finally { b.disabled = false; }
    };
    sel.onchange = async () => {
      if (!sel.value) return;
      try { await api("bot_set", {channel: sel.value}); toast("チャンネルを保存しました"); again(); } catch (e) { fail(e); }
    };
    q("#bSaveUs", body).onclick = async () => {
      try { await api("bot_set", {users: q("#bUs", body).value}); toast("保存しました"); again(); } catch (e) { fail(e); }
    };
    q("#bSaveAl", body).onclick = async () => {
      try {
        await api("bot_set", {alerts: {on: q("#aOn", body).checked, disk_gb: q("#aDisk", body).value,
                                       cpu: q("#aCpu", body).value, mem: q("#aMem", body).value}});
        toast("保存しました"); again();
      } catch (e) { fail(e); }
    };
    q("#bTest", body).onclick = async ev => {
      const b = ev.currentTarget; b.disabled = true; res.textContent = "調べています…";
      try {
        const r = await api("bot_test");
        res.innerHTML = r.checks.map(c => `<div>${c.ok ? "✅" : "⚠️"} ${esc(c.text)}</div>`).join("") +
          (r.all_ok ? '<div style="margin-top:6px"><b>準備OKです。下の「パネルを置く」を押してください。</b></div>' : "");
      } catch (e) { res.textContent = ""; fail(e); } finally { b.disabled = false; }
    };
    body.querySelectorAll("[data-panel]").forEach(b => b.onclick = async () => {
      b.disabled = true;
      try { const r = await api("bot_panel", {sid: b.dataset.panel}); toast(r.note || "Discord に操作パネルを置きました"); render(); }
      catch (e) { fail(e); b.disabled = false; }
    });
  }

  tgl.onclick = () => {
    opened = !opened; card.hidden = !opened; tgl.textContent = opened ? "設定を閉じる" : "設定を開く";
    if (opened) render();
  };
})();
