"""ソフトの更新は、統合版の共通部品(oneclick.selfupdate)にまかせる。

マイクラの画面にある「更新」ボタンも、同じ仕組みで動く。
サーバーが動いているかの確認には、マイクラ以外のゲームのサーバーも含める。
"""
from __future__ import annotations

from .. import selfupdate as _su
from ..selfupdate import (FROZEN, UpdateError, cached, check, cleanup, last_checked,  # noqa: F401
                          parse_version, pop_failure)


def apply(job, any_server_running, shutdown):
    from .. import registry

    def anything_running():
        return bool(any_server_running()) or registry.steam_running()
    return _su.apply(job, anything_running, shutdown)
