"""荒らし（いたずら）の巻き戻し: バックアップから「その場所だけ」を戻す。

マイクラのワールドは「リージョンファイル（r.X.Z.mca）」に、32×32 個のチャンク（16×16ブロックの区画）が入っている。
戻したい範囲のチャンクだけをバックアップのものに入れ替えて、ほかのチャンクは今のまま残す。
"""
from __future__ import annotations

import io
import math
import os
import re
import struct
import zipfile

SECTOR = 4096
SUBS = ("region", "entities", "poi")


def chunks_in_area(x: int, z: int, radius: int) -> set[tuple[int, int]]:
    """中心(x, z)から、半径 radius ブロックにかかるチャンクの集まり。"""
    c0x, c1x = math.floor((x - radius) / 16), math.floor((x + radius) / 16)
    c0z, c1z = math.floor((z - radius) / 16), math.floor((z + radius) / 16)
    return {(cx, cz) for cx in range(c0x, c1x + 1) for cz in range(c0z, c1z + 1)}


def parse_region(data: bytes) -> dict[int, tuple[bytes, int]]:
    """リージョンファイルを読んで {番号: (チャンクの中身, 更新時刻)} にする。"""
    out: dict[int, tuple[bytes, int]] = {}
    if len(data) < 2 * SECTOR:
        return out
    for i in range(1024):
        off = int.from_bytes(data[i * 4:i * 4 + 3], "big")
        cnt = data[i * 4 + 3]
        if off == 0 or cnt == 0:
            continue
        start = off * SECTOR
        if start + 5 > len(data):
            continue
        length = struct.unpack(">I", data[start:start + 4])[0]
        if length < 1 or start + 4 + length > len(data):
            continue
        ts = struct.unpack(">I", data[SECTOR + i * 4:SECTOR + i * 4 + 4])[0]
        out[i] = (data[start:start + 4 + length], ts)
    return out


def build_region(entries: dict[int, tuple[bytes, int]]) -> bytes:
    header = bytearray(SECTOR * 2)
    body = io.BytesIO()
    sector = 2
    for i in sorted(entries):
        payload, ts = entries[i]
        n = math.ceil(len(payload) / SECTOR)
        header[i * 4:i * 4 + 3] = sector.to_bytes(3, "big")
        header[i * 4 + 3] = n
        header[SECTOR + i * 4:SECTOR + i * 4 + 4] = struct.pack(">I", ts)
        body.write(payload + b"\0" * (n * SECTOR - len(payload)))
        sector += n
    return bytes(header) + body.getvalue()


def _external(payload: bytes) -> bool:
    return len(payload) > 4 and payload[4] > 128   # 大きすぎて別ファイル（.mcc）に入っているチャンク


def merge(cur: bytes | None, bak: bytes | None, rx: int, rz: int, chunks: set[tuple[int, int]]) -> tuple[bytes, int]:
    """今のリージョンに、バックアップの該当チャンクを入れる。戻したチャンク数も返す。"""
    cur_e = parse_region(cur) if cur else {}
    bak_e = parse_region(bak) if bak else {}
    n = 0
    for cx, cz in chunks:
        if cx >> 5 != rx or cz >> 5 != rz:
            continue
        idx = (cx & 31) + (cz & 31) * 32
        if idx in cur_e and _external(cur_e[idx][0]):
            continue  # 特別な保存のしかたのチャンクは、そのまま残す
        if idx in bak_e and not _external(bak_e[idx][0]):
            cur_e[idx] = bak_e[idx]
            n += 1
        elif idx not in bak_e and idx in cur_e:
            del cur_e[idx]          # バックアップのときは、まだ無かった場所 → 今ある分を消す（作り直される）
            n += 1
    return build_region(cur_e), n


DIM_BASES = {
    "overworld": [""],
    "nether": ["/DIM-1", "_nether/DIM-1"],
    "end": ["/DIM1", "_the_end/DIM1"],
}
_REG = re.compile(r"^r\.(-?\d+)\.(-?\d+)\.mca$")


def _bases(world_name: str, dim: str):
    for suffix in DIM_BASES[dim]:
        if suffix == "":
            yield world_name
        elif suffix.startswith("/"):
            yield world_name + suffix
        else:
            yield world_name + suffix


def revert_area(server_dir: str, world_name: str, zip_path: str, dim: str, x: int, z: int, radius: int,
                dry_run: bool = False) -> dict:
    """バックアップ(zip_path)から、指定の範囲だけを戻す。"""
    if dim not in DIM_BASES:
        raise ValueError("ディメンションは overworld / nether / end のどれかです")
    chunks = chunks_in_area(x, z, radius)
    regions = {(cx >> 5, cz >> 5) for cx, cz in chunks}
    restored = 0
    files = 0
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for base in _bases(world_name, dim):
            for sub in SUBS:
                for rx, rz in sorted(regions):
                    rel = f"{base}/{sub}/r.{rx}.{rz}.mca"
                    cur_path = os.path.join(server_dir, rel)
                    in_bak = rel in names
                    if not in_bak and not os.path.exists(cur_path):
                        continue
                    cur = open(cur_path, "rb").read() if os.path.exists(cur_path) else None
                    bak = zf.read(rel) if in_bak else None
                    new, n = merge(cur, bak, rx, rz, chunks)
                    if n == 0:
                        continue
                    if sub == "region":
                        restored += n
                    files += 1
                    if not dry_run:
                        os.makedirs(os.path.dirname(cur_path), exist_ok=True)
                        tmp = cur_path + ".tmp"
                        with open(tmp, "wb") as f:
                            f.write(new)
                        os.replace(tmp, cur_path)
    return {"chunks": restored, "files": files, "area": f"X {x - radius}〜{x + radius}、Z {z - radius}〜{z + radius}"}
