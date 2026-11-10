/* 統合版: どの画面からでも「ホーム(サーバー一覧)」へ戻れるボタン。
   左のメニューの一番上に入れる(以前のように画面の右下に浮かせないので、ほかの表示と被らない)。
   メニューは画面の切り替えで描き直されるので、消えたら入れ直す。 */
(function () {
  var m = document.querySelector('meta[name="home-url"]');
  var url = m && m.content;
  if (!url || url.indexOf("__") === 0) return;
  var side = document.getElementById("side");
  if (!side) return;
  function put() {
    if (side.querySelector(".homelink")) return;
    var a = document.createElement("a");
    a.className = "homelink";
    a.href = url;
    a.textContent = "\u2039 ホームへ";
    a.setAttribute("aria-label", "OneClickServer のホームへ戻る");
    side.insertBefore(a, side.firstChild);
  }
  put();
  new MutationObserver(put).observe(side, { childList: true });
})();
