/* 統合版: どの画面からでも「ホーム(サーバー一覧)」へ戻れる小さなボタン */
(function () {
  var m = document.querySelector('meta[name="home-url"]');
  var url = m && m.content;
  if (!url || url.indexOf("__") === 0) return;
  var a = document.createElement("a");
  a.href = url;
  a.textContent = "\u25C0 ホーム(サーバー一覧)";
  a.setAttribute("aria-label", "OneClickServer のホームへ戻る");
  a.style.cssText = "position:fixed;right:14px;bottom:14px;z-index:9999;padding:9px 16px;border-radius:99px;" +
    "background:#1f2a36;color:#fff;font:13px/1 system-ui,'Yu Gothic UI',sans-serif;text-decoration:none;" +
    "box-shadow:0 4px 14px rgba(0,0,0,.35);opacity:.92";
  a.onmouseenter = function () { a.style.opacity = "1"; };
  a.onmouseleave = function () { a.style.opacity = ".92"; };
  document.body.appendChild(a);
})();
