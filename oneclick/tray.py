"""タスクトレイ(時計の近く)のアイコン。窓を閉じても、ソフトを裏で動かし続けるために使う。

- アイコンをダブルクリック / 「開く」で、画面が出る
- 「終了」は、動いているサーバーがないときだけ(あるときは、先に止めるようにお知らせする)
- pystray が入っていない・Windows 以外のときは、使えない(今までどおり、窓を閉じて1分で終わる)
"""
from __future__ import annotations

import sys


def available() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def make_image(size: int = 64):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, size - 3, size - 3), radius=size // 4, fill=(47, 125, 225, 255))
    s = size
    d.polygon([(s * .38, s * .27), (s * .38, s * .73), (s * .74, s * .5)], fill=(255, 255, 255, 255))
    return img


class Tray:
    def __init__(self, on_open, on_quit, status_text, can_quit):
        import pystray
        self._pystray = pystray
        self.on_open, self.on_quit, self.status_text, self.can_quit = on_open, on_quit, status_text, can_quit
        menu = pystray.Menu(
            pystray.MenuItem("OneClickServer を開く", lambda *_: self.on_open(), default=True),
            pystray.MenuItem(lambda _i: self.status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("終了(裏で動くのをやめる)", self._quit))
        self.icon = pystray.Icon("OneClickServer", make_image(), "OneClickServer(裏で動いています)", menu)

    def _quit(self, *_):
        if not self.can_quit():
            self.notify("サーバーが動いています。先に、画面から「停止」してから終了してください。")
            return
        self.on_quit()

    def start(self):
        self.icon.run_detached()
        return self

    def notify(self, text: str, title: str = "OneClickServer"):
        try:
            self.icon.notify(text, title)
        except Exception:  # noqa: BLE001
            pass

    def stop(self):
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001
            pass
