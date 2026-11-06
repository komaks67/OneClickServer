// 画面が出る前に、前回の見た目（マイクラ風／シンプル）と明るさをつける（ちらつき防止）
"use strict";
(function () {
  var t = "auto", st = "simple";
  try { t = localStorage.getItem("mcmh-theme") || "auto"; st = localStorage.getItem("mcmh-style") || "simple"; } catch (e) { /* 使えないときは決まりの値 */ }
  if (t !== "light" && t !== "dark") t = "auto";
  if (st !== "mc") st = "simple";
  document.documentElement.dataset.theme = t;
  document.documentElement.dataset.style = st;
})();
