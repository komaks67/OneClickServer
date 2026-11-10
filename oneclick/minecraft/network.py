"""友達がインターネット経由で入れるようにする機能。

1. playit.gg（おすすめ）… ルーターの設定なしで公開できる無料サービス。どの回線でも使える。
2. 自動ポート開放（UPnP）… ルーターが対応していれば、ボタン1つで穴を開ける。
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import net
from .paths import IS_WINDOWS, TOOLS_DIR

# ---------------- IP アドレス ----------------


def local_ip() -> str:
    """このPCの家の中でのアドレス（例 192.168.1.5）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def global_ip() -> str | None:
    """インターネットから見たこの家のアドレス。"""
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            return net.request(url, timeout=8, retries=0).decode().strip()
        except net.NetError:
            continue
    return None


def is_not_public(ip: str) -> bool:
    """プライベート / キャリアの共有アドレス（＝ポート開放しても外から入れない）なら True。"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return a.is_private or a in ipaddress.ip_network("100.64.0.0/10") or a.is_loopback


# ---------------- UPnP（自動ポート開放） ----------------

_lan_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SSDP_ADDR = ("239.255.255.250", 1900)
SEARCH_TARGETS = [
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
]


class UpnpError(Exception):
    pass


class Gateway:
    def __init__(self, control_url: str, service_type: str, lan_ip: str):
        self.control_url = control_url
        self.service_type = service_type
        self.lan_ip = lan_ip

    def _soap(self, action: str, args: dict) -> str:
        body_args = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
        body = ('<?xml version="1.0"?>'
                '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action} xmlns:u="{self.service_type}">{body_args}</u:{action}>'
                '</s:Body></s:Envelope>').encode()
        req = urllib.request.Request(self.control_url, data=body, method="POST", headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{self.service_type}#{action}"'})
        try:
            with _lan_opener.open(req, timeout=6) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            code = re.search(r"<errorCode>(\d+)</errorCode>", detail)
            desc = re.search(r"<errorDescription>(.*?)</errorDescription>", detail)
            raise UpnpError(f"ルーターに断られました（{code.group(1) if code else e.code} "
                            f"{desc.group(1) if desc else ''}）") from e
        except OSError as e:
            raise UpnpError(f"ルーターと通信できません（{e}）") from e

    def external_ip(self) -> str | None:
        xml = self._soap("GetExternalIPAddress", {})
        m = re.search(r"<NewExternalIPAddress>([^<]*)</NewExternalIPAddress>", xml)
        return m.group(1) if m else None

    def add_port(self, port: int, proto="TCP", desc="Minecraft (mc-multi-helper)"):
        self._soap("AddPortMapping", {
            "NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": proto,
            "NewInternalPort": port, "NewInternalClient": self.lan_ip, "NewEnabled": 1,
            "NewPortMappingDescription": desc, "NewLeaseDuration": 0})

    def delete_port(self, port: int, proto="TCP"):
        self._soap("DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": proto})


def _parse_description(xml_text: str, location: str):
    root = ET.fromstring(xml_text)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"
    base = root.findtext(f"{ns}URLBase") or location
    for svc in root.iter(f"{ns}service"):
        st = svc.findtext(f"{ns}serviceType") or ""
        if "WANIPConnection" in st or "WANPPPConnection" in st:
            ctrl = svc.findtext(f"{ns}controlURL") or ""
            return urllib.parse.urljoin(base, ctrl), st
    return None


def discover(timeout=3.0) -> Gateway:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(timeout)
    try:
        for st in SEARCH_TARGETS:
            msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                   f'MAN: "ssdp:discover"\r\nMX: 2\r\nST: {st}\r\n\r\n')
            sock.sendto(msg.encode(), SSDP_ADDR)
        locations = []
        while True:
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            m = re.search(r"^location:\s*(\S+)", data.decode("utf-8", "replace"), re.I | re.M)
            if m and m.group(1) not in locations:
                locations.append(m.group(1))
    finally:
        sock.close()
    if not locations:
        raise UpnpError("ルーターが自動ポート開放（UPnP）に対応していないか、オフになっています")
    for loc in locations:
        try:
            with _lan_opener.open(loc, timeout=5) as r:
                found = _parse_description(r.read().decode("utf-8", "replace"), loc)
        except (OSError, ET.ParseError):
            continue
        if found:
            host = urllib.parse.urlparse(loc).hostname
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect((host, 1900))
                lan = s.getsockname()[0]
            finally:
                s.close()
            return Gateway(found[0], found[1], lan)
    raise UpnpError("ルーターは見つかりましたが、ポート開放の機能が見つかりませんでした")


def upnp_open(port: int, log=print) -> str:
    """ポートを開けて、友達に伝えるアドレスを返す。"""
    gw = discover()
    log(f"ルーターを見つけました。ポート {port} を開けます…")
    gw.add_port(port)
    ext = gw.external_ip()
    real = global_ip()
    if ext and is_not_public(ext):
        raise UpnpError(
            "ポートは開けましたが、この回線は「外から直接入れないタイプ」です"
            "（IPv6 IPoE や マンションの共有回線など）。playit.gg を使ってください。")
    if ext and real and ext != real:
        log("⚠ ルーターの外側にもう1台ルーターがあるようです。つながらない時は playit.gg を使ってください。")
    addr = real or ext
    return f"{addr}:{port}" if port != 25565 else addr


def upnp_close(port: int):
    discover().delete_port(port)


# ---------------- playit.gg ----------------

# playit 1.0 系の .exe は「裏で動くだけの部品（playitd）」で、単体では接続用のURLが出ません。
# 単体で動く 0.17.1 を使います（playit 公式が「非推奨ではない版」としている版）。
PLAYIT_VERSION = "0.17.1"
PLAYIT_RELEASE = f"https://api.github.com/repos/playit-cloud/playit-agent/releases/tags/v{PLAYIT_VERSION}"
PLAYIT_DIRECT = (f"https://github.com/playit-cloud/playit-agent/releases/download/v{PLAYIT_VERSION}/"
                 + ("playit-windows-x86_64.exe" if IS_WINDOWS else "playit-linux-amd64"))


def playit_path() -> str:
    return os.path.join(TOOLS_DIR, "playit.exe" if IS_WINDOWS else "playit")


def _playit_marker() -> str:
    return playit_path() + ".version"


def ensure_playit(log=print) -> str:
    p = playit_path()
    try:
        with open(_playit_marker(), encoding="utf-8") as f:
            ok = f.read().strip() == PLAYIT_VERSION
    except OSError:
        ok = False
    if os.path.exists(p) and ok:
        return p
    if os.path.exists(p):  # 古い版（playitd）が入っていたら入れ替える
        log("playit を単体で動く版に入れ替えます…")
        try:
            os.remove(p)
        except OSError:
            pass
    url, sha256, name = PLAYIT_DIRECT, None, os.path.basename(PLAYIT_DIRECT)
    try:
        rel = net.get_json(PLAYIT_RELEASE)
        want_os = "windows" if IS_WINDOWS else "linux"
        assets = [a for a in rel.get("assets", [])
                  if want_os in a["name"].lower() and ("x86_64" in a["name"] or "amd64" in a["name"])]
        if IS_WINDOWS:
            assets = [a for a in assets if a["name"].endswith(".exe")]
            assets.sort(key=lambda a: "signed" not in a["name"])  # 署名つきを優先
        if assets:
            a = assets[0]
            url, name = a["browser_download_url"], a["name"]
            digest = (a.get("digest") or "")
            sha256 = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
    except Exception:  # noqa: BLE001  情報が取れなければ、決まったURLから取る
        pass
    log(f"playit をダウンロード中…（{name}）")
    net.download(url, p, sha256=sha256)
    if not IS_WINDOWS:
        os.chmod(p, 0o755)
    with open(_playit_marker(), "w", encoding="utf-8") as f:
        f.write(PLAYIT_VERSION)
    return p


def start_playit(log=print) -> subprocess.Popen:
    exe = ensure_playit(log)
    flags = subprocess.CREATE_NEW_CONSOLE if IS_WINDOWS else 0
    return subprocess.Popen([exe], cwd=TOOLS_DIR, creationflags=flags)


# ---------------- ポートまわりの補助 ----------------

def port_free(port: int) -> bool:
    """そのポート番号が、いま空いているか（ほかのソフトが使っていないか）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":  # Linux などでは、閉じたばかりの接続の名残りで使えない扱いにならないように
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def gateway_ip() -> str | None:
    """ルーター（家のネットの出入り口）のアドレス。ルーターの設定画面を開くのに使う。"""
    try:
        if IS_WINDOWS:
            out = subprocess.run(["route", "print", "-4", "0.0.0.0"], capture_output=True, text=True,
                                 errors="replace", timeout=8, creationflags=0x08000000).stdout or ""
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                    if re.match(r"^\d+\.\d+\.\d+\.\d+$", parts[2]):
                        return parts[2]
        else:
            with open("/proc/net/route") as f:
                for line in f.readlines()[1:]:
                    p = line.split()
                    if p[1] == "00000000":
                        return socket.inet_ntoa(bytes.fromhex(p[2])[::-1])
    except (OSError, subprocess.SubprocessError, IndexError, ValueError, AttributeError):
        pass
    ip = local_ip()
    return ".".join(ip.split(".")[:3] + ["1"]) if ip.count(".") == 3 else None


def firewall_allow(port: int) -> None:
    """Windows のファイアウォール（PCの門番）に「このポートは通してOK」と登録する。管理者の確認が出る。"""
    if not IS_WINDOWS:
        raise RuntimeError("この機能は Windows だけです")
    rule = f"Minecraft Server {port} (mc-multi-helper)"
    inner = (f"New-NetFirewallRule -DisplayName '{rule}' -Direction Inbound -Protocol TCP "
             f"-LocalPort {int(port)} -Action Allow -Profile Any")
    cmd = ["powershell", "-NoProfile", "-Command",
           f"Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile','-Command',\"{inner}\""]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=120, creationflags=0x08000000)
    if r.returncode != 0:
        raise RuntimeError("登録できませんでした（「はい」を押さなかったか、管理者の権限がありません）")


# ---------------- 外から届くかのテスト ----------------

REACH_API = "https://api.mcsrvstat.us/3/"


def reach_test(address: str) -> dict:
    """外のサービス（mcsrvstat.us）に「このアドレスにつながるか」を試してもらう。"""
    from . import net
    import json as _json
    import urllib.parse as _up
    data = _json.loads(net.request(REACH_API + _up.quote(address, safe=":.[]-"), timeout=20, retries=1).decode("utf-8"))
    players = data.get("players") or {}
    motd = " ".join((data.get("motd") or {}).get("clean") or [])
    return {"online": bool(data.get("online")), "ip": data.get("ip"), "port": data.get("port"),
            "version": data.get("version"), "players": players.get("online"), "max": players.get("max"),
            "motd": motd, "cached": bool((data.get("debug") or {}).get("cachehit")),
            "srv": bool((data.get("debug") or {}).get("srv"))}


# ---------------- ネットワーク診断 ----------------

def global_ipv6() -> str | None:
    """このPCの、インターネットで使える IPv6 アドレス（なければ None）。"""
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    except OSError:
        return None
    try:
        s.connect(("2001:4860:4860::8888", 80))
        ip = s.getsockname()[0].split("%")[0]
        a = ipaddress.ip_address(ip)
        return ip if a.is_global else None
    except (OSError, ValueError):
        return None
    finally:
        s.close()


def diagnose(port: int, log=print) -> dict:
    """友達を呼ぶ方法（IPv6 直接・自動ポート開放・playit）のうち、この回線で使えるものを調べる。"""
    out: dict = {"port": port}
    log("インターネットから見たアドレスを調べています…")
    out["ipv4"] = global_ip()
    log("ルーターが自動ポート開放（UPnP）に対応しているか調べています…")
    out["upnp"], out["upnp_ip"], out["upnp_error"] = False, None, ""
    try:
        gw = discover(timeout=3.0)
        out["upnp"] = True
        try:
            out["upnp_ip"] = gw.external_ip()
        except UpnpError:
            pass
    except UpnpError as e:
        out["upnp_error"] = str(e)
    except Exception as e:  # noqa: BLE001
        out["upnp_error"] = str(e)
    wan = out["upnp_ip"]
    # ルーターの外側のアドレスが、インターネットから見たアドレスと違う → 共有アドレスの回線（v6プラス・マンション回線など）
    out["shared_v4"] = bool((wan and (is_not_public(wan) or (out["ipv4"] and wan != out["ipv4"]))))
    log("IPv6 が使えるか調べています…")
    out["ipv6"] = global_ipv6()
    out["ipv6_address"] = (f"[{out['ipv6']}]" + ("" if port == 25565 else f":{port}")) if out["ipv6"] else ""
    if out["upnp"] and not out["shared_v4"] and out["ipv4"]:
        out["best"] = "upnp"
    elif out["ipv6"]:
        out["best"] = "ipv6_or_playit"
    else:
        out["best"] = "playit"
    return out
