"""画面（tkinter）。Python に最初から入っている部品だけで作っている。"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from . import client_install, loaders, modrinth, mrpack, net, network, server
from .modrinth import ModList
from .paths import CACHE_DIR, IS_WINDOWS, ensure_dirs, load_settings, save_settings

APP_TITLE = "OneClickServerMC"
FONT = "Yu Gothic UI" if IS_WINDOWS else "Noto Sans CJK JP"
MONO = "Consolas" if IS_WINDOWS else "DejaVu Sans Mono"

STATE_LABEL = {"stopped": ("● 停止中", "#888888"), "starting": ("● 起動中…", "#d08a00"),
               "running": ("● 起動完了（遊べます）", "#1a8f3a"), "stopping": ("● 停止中…", "#d08a00")}


def total_ram_mb() -> int:
    try:
        if IS_WINDOWS:
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return int(m.ullTotalPhys // (1024 * 1024))
        with open("/proc/meminfo") as f:
            return int(f.readline().split()[1]) // 1024
    except Exception:  # noqa: BLE001
        return 8192


def recommended_mem(total: int) -> int:
    if total >= 31000:
        return 8192
    if total >= 15000:
        return 6144
    if total >= 7500:
        return 4096
    return 2048


def open_folder(path: str):
    os.makedirs(path, exist_ok=True)
    if IS_WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}K"
    return str(n)


# =====================================================================
class CheckTree(ttk.Treeview):
    """先頭に ■（選んだ）/ □（選んでない）の列がある一覧。クリックで切りかえ。

    toggle_any=True なら行のどこをクリックしても切りかわる。
    False なら左の ■□ の列をクリックした時だけ切りかわる。
    🔒 の行（locked）は変えられない。
    """
    ON, OFF, LOCK = "■", "□", "🔒"

    def __init__(self, master, columns, on_toggle=None, toggle_any=True, check_label="選ぶ", **kw):
        kw["selectmode"] = "none"  # 青い選択色は使わず、■□ と緑色だけで見せる
        super().__init__(master, columns=("chk",) + tuple(c[0] for c in columns), show="headings", **kw)
        self.heading("chk", text=check_label)
        self.column("chk", width=56, anchor="center", stretch=False)
        for key, label, width in columns:
            self.heading(key, text=label)
            self.column(key, width=width, stretch=(width >= 200))
        self.tag_configure("on", background="#dff3df")
        self.tag_configure("lock", background="#eeeeee", foreground="#555555")
        self.checked: set[str] = set()
        self.locked: set[str] = set()
        self.on_toggle = on_toggle
        self.toggle_any = toggle_any
        self.bind("<Button-1>", self._click, add=True)
        self.bind("<space>", lambda e: [self.toggle(i) for i in self.selection()], add=True)

    def clear(self):
        self.delete(*self.get_children())
        self.locked.clear()

    def add_row(self, iid, values, checked=False, locked=False):
        if locked:
            self.locked.add(iid)
            checked = True
        if checked:
            self.checked.add(iid)
        else:
            self.checked.discard(iid)
        self.insert("", "end", iid=iid, values=(self._mark(iid),) + tuple(values))
        self._paint(iid)

    def _mark(self, iid):
        if iid in self.locked:
            return self.LOCK
        return self.ON if iid in self.checked else self.OFF

    def _paint(self, iid):
        tag = "lock" if iid in self.locked else ("on" if iid in self.checked else "")
        self.item(iid, tags=(tag,) if tag else ())
        self.set(iid, "chk", self._mark(iid))

    def _click(self, e):
        if self.identify("region", e.x, e.y) != "cell":
            return
        iid = self.identify_row(e.y)
        if not iid:
            return
        if self.toggle_any or self.identify_column(e.x) == "#1":
            self.toggle(iid)

    def toggle(self, iid, value=None):
        if iid in self.locked or not self.exists(iid):
            return
        on = (iid not in self.checked) if value is None else value
        if on:
            self.checked.add(iid)
        else:
            self.checked.discard(iid)
        self._paint(iid)
        if self.on_toggle:
            self.on_toggle(iid, on)

    def set_all(self, on: bool):
        for iid in self.get_children():
            if iid not in self.locked:
                self.toggle(iid, on)


# =====================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        ensure_dirs()
        self.settings = load_settings()
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(980, 640)
        self._q: queue.Queue = queue.Queue()
        self.runners: dict[str, server.Runner] = {}
        self.profile: server.Profile | None = None
        self.playit_proc = None
        self.ram = total_ram_mb()
        self._style()
        self._build()
        self.refresh_servers()
        self.after(80, self._pump)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------ 共通
    def _style(self):
        st = ttk.Style(self)
        if "vista" in st.theme_names():
            st.theme_use("vista")
        else:
            st.theme_use("clam")
        import tkinter.font as tkfont
        for named in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(named).configure(family=FONT, size=10)
            except tk.TclError:
                pass
        st.configure(".", font=(FONT, 10))
        st.configure("Treeview", rowheight=26)
        st.configure("Treeview.Heading", font=(FONT, 10, "bold"))
        st.configure("Title.TLabel", font=(FONT, 16, "bold"))
        st.configure("H.TLabel", font=(FONT, 12, "bold"))
        st.configure("Sub.TLabel", foreground="#666666")
        st.configure("Big.TButton", font=(FONT, 12, "bold"), padding=(18, 8))
        st.configure("TNotebook.Tab", padding=(14, 6))

    def bg(self, func, done=None, err=None, busy: str | None = None):
        """重い作業を裏で動かし、終わったら画面に結果を返す。"""
        if busy:
            self.set_status(busy)

        def run():
            try:
                res = func()
                self._q.put(("call", lambda: (self.set_status(""), done(res) if done else None)))
            except Exception as e:  # noqa: BLE001
                tb = traceback.format_exc()
                print(tb, file=sys.stderr)

                def show(e=e):
                    self.set_status("")
                    if err:
                        err(e)
                    else:
                        messagebox.showerror(APP_TITLE, str(e), parent=self)
                self._q.put(("call", show))
        threading.Thread(target=run, daemon=True).start()

    def ui(self, func):
        """裏の作業から画面を触りたい時に使う。"""
        self._q.put(("call", func))

    def _pump(self):
        try:
            for _ in range(400):
                kind, payload = self._q.get_nowait()
                if kind == "call":
                    payload()
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def set_status(self, text):
        self.status_var.set(text)

    # ------------------------------------------------------------ 画面の骨組み
    def _build(self):
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True)

        side = ttk.Frame(root, padding=10, width=250)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        ttk.Label(side, text="⛏ OneClickServerMC", style="Title.TLabel").pack(anchor="w")
        ttk.Label(side, text="あなたのサーバー", style="Sub.TLabel").pack(anchor="w", pady=(12, 2))
        self.server_list = tk.Listbox(side, height=12, activestyle="none", font=(FONT, 11),
                                      selectbackground="#3b7a3b", borderwidth=1, relief="solid")
        self.server_list.pack(fill="both", expand=True)
        self.server_list.bind("<<ListboxSelect>>", lambda e: self.on_select_server())
        ttk.Button(side, text="＋ 新しいサーバーを作る", command=self.new_server_dialog).pack(fill="x", pady=(8, 2))
        ttk.Button(side, text="📦 MODパックから作る", command=self.modpack_dialog).pack(fill="x", pady=2)
        ttk.Separator(side).pack(fill="x", pady=10)
        ttk.Button(side, text="🎮 友達のサーバーに参加する", command=self.join_dialog).pack(fill="x", pady=2)
        ttk.Button(side, text="❓ 使い方", command=self.help_dialog).pack(fill="x", pady=2)

        self.main = ttk.Frame(root, padding=(4, 10, 10, 6))
        self.main.pack(side="left", fill="both", expand=True)
        self.empty = ttk.Frame(self.main)
        ttk.Label(self.empty, text="ようこそ！", style="Title.TLabel").pack(pady=(80, 10))
        ttk.Label(self.empty, text="左の「＋ 新しいサーバーを作る」から始めましょう。\n"
                                   "友達にサーバーへ招待された人は「🎮 友達のサーバーに参加する」へ。",
                  justify="center").pack()
        self.nb = ttk.Notebook(self.main)
        self.tabs = {}
        for key, label in [("home", "🏠 ホーム"), ("mods", "🧩 MOD"), ("public", "🌐 友達を呼ぶ"),
                           ("pack", "📦 友達用パック"), ("members", "👥 メンバー"),
                           ("backup", "💾 バックアップ"), ("settings", "⚙ 設定")]:
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text=label)
            self.tabs[key] = f
        self._build_home()
        self._build_mods()
        self._build_public()
        self._build_pack()
        self._build_members()
        self._build_backup()
        self._build_settings()

        self.status_var = tk.StringVar()
        bar = ttk.Frame(self, padding=(10, 2))
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, textvariable=self.status_var, style="Sub.TLabel").pack(side="left")

    # ------------------------------------------------------------ サーバー一覧
    def refresh_servers(self, select: str | None = None):
        self.profiles = server.list_profiles()
        self.server_list.delete(0, "end")
        for p in self.profiles:
            r = self.runners.get(p.dir)
            mark = "🟢 " if r and r.running else "⚪ "
            self.server_list.insert("end", f"{mark}{p.name}")
        if select:
            for i, p in enumerate(self.profiles):
                if p.dir == select:
                    self.server_list.selection_clear(0, "end")
                    self.server_list.selection_set(i)
                    self.on_select_server()
                    return
        if not self.profiles:
            self.nb.pack_forget()
            self.empty.pack(fill="both", expand=True)
        elif self.profile is None:
            self.server_list.selection_set(0)
            self.on_select_server()

    def on_select_server(self):
        sel = self.server_list.curselection()
        if not sel:
            return
        self.profile = self.profiles[sel[0]]
        self.empty.pack_forget()
        self.nb.pack(fill="both", expand=True)
        self.load_home()
        self.load_mods_installed()
        self.load_public()
        self.load_members()
        self.load_backups()
        self.load_settings_tab()

    def runner(self) -> server.Runner:
        p = self.profile
        if p.dir not in self.runners:
            d = p.dir
            self.runners[d] = server.Runner(
                p, on_line=lambda line, d=d: self.ui(lambda: self.console_add(d, line)),
                on_state=lambda s, d=d: self.ui(lambda: self.state_changed(d)))
        return self.runners[p.dir]

    # ================================================================ ホーム
    def _build_home(self):
        t = self.tabs["home"]
        top = ttk.Frame(t)
        top.pack(fill="x")
        left = ttk.Frame(top)
        left.pack(side="left", fill="x", expand=True)
        self.h_name = ttk.Label(left, text="", style="Title.TLabel")
        self.h_name.pack(anchor="w")
        self.h_info = ttk.Label(left, text="", style="Sub.TLabel")
        self.h_info.pack(anchor="w")
        self.h_state = tk.Label(left, text="", font=(FONT, 12, "bold"))
        self.h_state.pack(anchor="w", pady=(6, 0))
        self.h_addr = ttk.Label(left, text="")
        self.h_addr.pack(anchor="w")
        btns = ttk.Frame(top)
        btns.pack(side="right")
        self.b_start = ttk.Button(btns, text="▶ 起動", style="Big.TButton", command=self.start_server)
        self.b_start.pack(side="left", padx=4)
        self.b_stop = ttk.Button(btns, text="■ 停止", style="Big.TButton", command=self.stop_server)
        self.b_stop.pack(side="left", padx=4)
        ttk.Button(btns, text="⟳ 再起動", command=self.restart_server).pack(side="left", padx=4)

        body = ttk.Frame(t)
        body.pack(fill="both", expand=True, pady=(10, 0))
        con = ttk.Frame(body)
        con.pack(side="left", fill="both", expand=True)
        ttk.Label(con, text="サーバーの画面（ログ）", style="H.TLabel").pack(anchor="w")
        txtf = ttk.Frame(con)
        txtf.pack(fill="both", expand=True)
        self.console = tk.Text(txtf, bg="#14171a", fg="#d6d6d6", insertbackground="white",
                               font=(MONO, 9), wrap="word", state="disabled", relief="flat")
        sb = ttk.Scrollbar(txtf, command=self.console.yview)
        self.console.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.console.pack(side="left", fill="both", expand=True)
        self.console.tag_configure("warn", foreground="#f0c060")
        self.console.tag_configure("err", foreground="#ff7070")
        self.console.tag_configure("me", foreground="#7fd0ff")
        cmd = ttk.Frame(con)
        cmd.pack(fill="x", pady=(6, 0))
        ttk.Label(cmd, text="コマンド:").pack(side="left")
        self.cmd_var = tk.StringVar()
        e = ttk.Entry(cmd, textvariable=self.cmd_var)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda ev: self.send_cmd())
        ttk.Button(cmd, text="送る", command=self.send_cmd).pack(side="left")
        quick = ttk.Frame(con)
        quick.pack(fill="x", pady=(4, 0))
        for label, c in [("☀ 朝にする", "time set day"), ("🌤 晴れにする", "weather clear"),
                         ("💾 保存", "save-all"), ("📋 参加者", "list")]:
            ttk.Button(quick, text=label, command=lambda c=c: self.send_cmd(c)).pack(side="left", padx=(0, 4))

        right = ttk.Frame(body, width=230, padding=(10, 0, 0, 0))
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        ttk.Label(right, text="参加中のプレイヤー", style="H.TLabel").pack(anchor="w")
        self.players = tk.Listbox(right, height=5, font=(FONT, 11), activestyle="none")
        self.players.pack(fill="both", expand=True)
        for label, c in [("👑 OPにする（管理者）", "op {}"), ("ホワイトリストに追加", "whitelist add {}"),
                         ("🚪 キックする", "kick {}"), ("🎨 クリエイティブ", "gamemode creative {}"),
                         ("⚔ サバイバル", "gamemode survival {}")]:
            ttk.Button(right, text=label, command=lambda c=c: self.player_cmd(c)).pack(fill="x", pady=1)
        ttk.Label(right, text="メモリ（使う量）", style="H.TLabel").pack(anchor="w", pady=(12, 0))
        self.mem_var = tk.IntVar(value=4096)
        self.mem_label = ttk.Label(right, text="")
        self.mem_label.pack(anchor="w")
        maxmem = max(2048, min(self.ram - 2048, 32768))
        self.mem_scale = ttk.Scale(right, from_=1024, to=maxmem, variable=self.mem_var,
                                   command=lambda v: self._mem_changed())
        self.mem_scale.pack(fill="x")
        ttk.Label(right, text=f"このPCのメモリ: {self.ram // 1024}GB\n（次の起動から反映）",
                  style="Sub.TLabel").pack(anchor="w")

    def _mem_changed(self):
        v = int(round(self.mem_var.get() / 512) * 512)
        self.mem_label.configure(text=f"{v / 1024:.1f} GB")
        if self.profile and self.profile.get("memory_mb") != v:
            self.profile.set(memory_mb=v)

    def load_home(self):
        p = self.profile
        self.h_name.configure(text=p.name)
        mods = len([m for m in ModList(p.dir).items if m.get("location") == "server"])
        self.h_info.configure(text=f"マイクラ {p.get('mc')}  /  {p.loader_label()}  /  サーバーMOD {mods} 個  /  "
                                   f"ポート {p.props().get('server-port', '25565')}")
        self.mem_var.set(p.get("memory_mb", 4096))
        self._mem_changed()
        addr = p.get("public_address")
        self.h_addr.configure(text=f"友達に伝えるアドレス: {addr}" if addr else
                              "友達に伝えるアドレス: まだ設定していません（「🌐 友達を呼ぶ」タブで設定）")
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        for line in getattr(self.runner(), "history", [])[-1500:]:
            self._console_insert(line)
        self.console.configure(state="disabled")
        self.state_changed(p.dir)

    def _console_insert(self, line):
        tag = ()
        if "WARN" in line or "⚠" in line:
            tag = ("warn",)
        if "ERROR" in line or "Exception" in line:
            tag = ("err",)
        if line.startswith(("▶", "■", "✅", "💾", "> ")):
            tag = ("me",)
        self.console.insert("end", line + "\n", tag)

    def console_add(self, d, line):
        r = self.runners.get(d)
        if r is not None:
            hist = getattr(r, "history", None)
            if hist is None:
                hist = r.history = []
            hist.append(line)
            if len(hist) > 3000:
                del hist[:1000]
        if not self.profile or self.profile.dir != d:
            return
        at_bottom = self.console.yview()[1] > 0.98
        self.console.configure(state="normal")
        self._console_insert(line)
        if int(self.console.index("end-1c").split(".")[0]) > 3000:
            self.console.delete("1.0", "1000.0")
        self.console.configure(state="disabled")
        if at_bottom:
            self.console.see("end")

    def state_changed(self, d):
        self.refresh_server_marks()
        if not self.profile or self.profile.dir != d:
            return
        r = self.runner()
        label, color = STATE_LABEL.get(r.state, STATE_LABEL["stopped"])
        self.h_state.configure(text=label, fg=color)
        self.b_start.state(["disabled"] if r.running else ["!disabled"])
        self.b_stop.state(["!disabled"] if r.running else ["disabled"])
        self.players.delete(0, "end")
        for name in sorted(r.players):
            self.players.insert("end", name)

    def refresh_server_marks(self):
        for i, p in enumerate(self.profiles):
            r = self.runners.get(p.dir)
            mark = "🟢 " if r and r.running else "⚪ "
            sel = self.server_list.curselection()
            self.server_list.delete(i)
            self.server_list.insert(i, f"{mark}{p.name}")
            if sel and sel[0] == i:
                self.server_list.selection_set(i)

    def start_server(self):
        p = self.profile
        if not server.eula_accepted(p):
            ok = messagebox.askyesno(
                APP_TITLE,
                "サーバーを動かすには、マイクラの利用規約（EULA）への同意が必要です。\n\n"
                "・ https://aka.ms/MinecraftEULA\n\n"
                "内容を確認して同意しますか？（「いいえ」で規約のページを開きます）", parent=self)
            if not ok:
                webbrowser.open("https://aka.ms/MinecraftEULA")
                return
            server.accept_eula(p)
        r = self.runner()
        self.bg(r.start, busy="サーバーを起動しています…")

    def stop_server(self):
        r = self.runner()
        self.bg(r.stop, busy="サーバーを止めています…（ワールドを保存中）")

    def restart_server(self):
        r = self.runner()

        def do():
            r.stop()
            time.sleep(2)
            r.start()
        self.bg(do, busy="再起動しています…")

    def send_cmd(self, c=None):
        c = c or self.cmd_var.get().strip()
        if not c:
            return
        if not self.runner().send(c):
            messagebox.showinfo(APP_TITLE, "サーバーが動いていません。先に「▶ 起動」を押してください。", parent=self)
            return
        self.console_add(self.profile.dir, f"> {c}")
        self.cmd_var.set("")

    def player_cmd(self, template):
        sel = self.players.curselection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "右のリストからプレイヤーを選んでください。", parent=self)
            return
        self.send_cmd(template.format(self.players.get(sel[0])))

    # ================================================================ MOD
    def _build_mods(self):
        t = self.tabs["mods"]
        pw = ttk.PanedWindow(t, orient="vertical")
        pw.pack(fill="both", expand=True)

        top = ttk.Frame(pw)
        pw.add(top, weight=3)
        bar = ttk.Frame(top)
        bar.pack(fill="x")
        ttk.Label(bar, text="MODをさがす", style="H.TLabel").pack(side="left")
        self.q_var = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.q_var, width=24)
        e.pack(side="left", padx=6)
        e.bind("<Return>", lambda ev: self.search_mods())
        self.cat_var = tk.StringVar(value="すべて")
        ttk.Combobox(bar, textvariable=self.cat_var, state="readonly", width=12,
                     values=[c[1] for c in modrinth.CATEGORIES]).pack(side="left")
        self.sort_var = tk.StringVar(value="人気順")
        ttk.Combobox(bar, textvariable=self.sort_var, state="readonly", width=9,
                     values=["人気順", "関係順", "新着順", "更新順"]).pack(side="left", padx=4)
        ttk.Button(bar, text="🔍 検索", command=self.search_mods).pack(side="left")
        ttk.Button(bar, text="もっと見る", command=lambda: self.search_mods(more=True)).pack(side="left", padx=4)

        self.pick: dict[str, str] = {}  # 選んだMOD（検索しなおしても覚えておく）
        self.results = CheckTree(top, [("title", "名前", 190), ("have", "入ってる", 95), ("dl", "人気", 55),
                                       ("server", "サーバー", 90), ("client", "遊ぶ人", 90),
                                       ("desc", "説明", 400)],
                                 on_toggle=self._pick_toggled, height=8)
        self.results.pack(fill="both", expand=True, pady=4)
        rb = ttk.Frame(top)
        rb.pack(fill="x")
        self.b_install = ttk.Button(rb, text="⬇ 選んだMODを入れる", style="Big.TButton",
                                    command=self.install_selected)
        self.b_install.pack(side="left")
        ttk.Button(rb, text="選択をクリア", command=self.clear_pick).pack(side="left", padx=6)
        ttk.Button(rb, text="🌐 MODのページを見る", command=self.open_mod_page).pack(side="left")
        self.pick_label = ttk.Label(top, text="", style="Sub.TLabel", wraplength=860, justify="left")
        self.pick_label.pack(anchor="w", pady=(4, 0))
        self._hits = {}
        self._search_offset = 0
        self._update_pick_label()

        bottom = ttk.Frame(pw)
        pw.add(bottom, weight=2)
        hb = ttk.Frame(bottom)
        hb.pack(fill="x", pady=(8, 0))
        ttk.Label(hb, text="入っているMOD", style="H.TLabel").pack(side="left")
        self.mods_count = ttk.Label(hb, text="", style="Sub.TLabel")
        self.mods_count.pack(side="left", padx=8)
        ttk.Button(hb, text="すべて選ぶ", command=lambda: self.installed.set_all(True)).pack(side="right")
        ttk.Button(hb, text="選択を外す", command=lambda: self.installed.set_all(False)).pack(side="right", padx=4)
        self.installed = CheckTree(bottom, [("title", "名前", 240), ("ver", "バージョン", 140),
                                            ("where", "どこに入る", 230), ("on", "状態", 70)],
                                   height=5)
        self.installed.pack(fill="both", expand=True, pady=4)
        ib = ttk.Frame(bottom)
        ib.pack(fill="x")
        for label, cmd in [("✅ ON にする", lambda: self.enable_mods(True)),
                           ("⛔ OFF にする", lambda: self.enable_mods(False)),
                           ("🗑 外す", self.remove_mods),
                           ("⟳ 更新をさがす", self.check_updates), ("📁 ファイルから追加", self.add_jar_files),
                           ("📂 modsフォルダ", lambda: open_folder(self.profile.mods_dir)),
                           ("再読み込み", self.load_mods_installed)]:
            ttk.Button(ib, text=label, command=cmd).pack(side="left", padx=(0, 4))
        ttk.Label(bottom, text="行をクリックすると ■（選んだ）/ □ が切りかわります。選んだMODにボタンの操作をします。",
                  style="Sub.TLabel").pack(anchor="w", pady=(4, 0))

    def _sort_key(self):
        return {"人気順": "downloads", "関係順": "relevance", "新着順": "newest", "更新順": "updated"}[self.sort_var.get()]

    def _pick_toggled(self, pid, on):
        h = self._hits.get(pid)
        if on and h:
            self.pick[pid] = h["title"]
        else:
            self.pick.pop(pid, None)
        self._update_pick_label()

    def _update_pick_label(self):
        n = len(self.pick)
        self.b_install.configure(text=f"⬇ 選んだ {n} 個のMODを入れる" if n else "⬇ 選んだMODを入れる")
        if n:
            names = "、".join(list(self.pick.values())[:12]) + (" …" if n > 12 else "")
            self.pick_label.configure(text=f"選んだMOD（{n} 個）: {names}　※ 検索しなおしても選んだまま残ります。前提MODは自動で入ります")
        else:
            self.pick_label.configure(text="入れたいMODの行をクリックして ■ にしてください（何個でも選べます・前提MODは自動で入ります）")

    def clear_pick(self):
        self.pick.clear()
        self.results.set_all(False)
        self._update_pick_label()

    def _installed_ids(self):
        ml = getattr(self, "_ml", None)
        return {m.get("project_id") for m in ml.items} if ml else set()

    def search_mods(self, more=False):
        p = self.profile
        if p.get("loader") == "vanilla":
            messagebox.showinfo(APP_TITLE, "このサーバーは「バニラ（MODなし）」です。\n"
                                           "MODを使うには Fabric などで新しいサーバーを作ってください。", parent=self)
            return
        cat = next(c[0] for c in modrinth.CATEGORIES if c[1] == self.cat_var.get())
        self._search_offset = self._search_offset + 30 if more else 0
        q, sort, off = self.q_var.get().strip(), self._sort_key(), self._search_offset

        def done(res):
            hits, total = res
            if not more:
                self.results.clear()
            have = self._installed_ids()
            for h in hits:
                pid = h["project_id"]
                if self.results.exists(pid):
                    continue
                self._hits[pid] = h
                self.results.add_row(pid, (
                    h["title"], "✔ 入ってる" if pid in have else "", fmt_num(h.get("downloads", 0)),
                    modrinth.SIDE_LABEL.get(h.get("server_side"), "?"),
                    modrinth.SIDE_LABEL.get(h.get("client_side"), "?"),
                    h.get("description", "")), checked=pid in self.pick)
            self.set_status(f"{total} 件見つかりました（マイクラ {p.get('mc')} / {p.loader_label()} 用だけ表示）")
        self.bg(lambda: modrinth.search(q, p.get("mc"), p.get("loader"), category=cat, sort=sort, offset=off),
                done, busy="検索中…")

    def open_mod_page(self):
        pid = self.results.focus() or next(iter(self.pick), None)
        h = self._hits.get(pid) if pid else None
        if h:
            webbrowser.open(f"https://modrinth.com/mod/{h.get('slug') or pid}")
        else:
            messagebox.showinfo(APP_TITLE, "見たいMODの行をクリックしてください。", parent=self)

    def install_selected(self):
        ids = list(self.pick)
        if not ids:
            messagebox.showinfo(APP_TITLE, "上のリストで、入れたいMODの行をクリックして ■ にしてください。", parent=self)
            return
        p = self.profile
        warn_running = self.runner().running
        prog = ProgressWindow(self, f"MODを {len(ids)} 個入れています")

        def done(res):
            result, errors = res
            prog.finish()
            msg = result.summary()
            if errors:
                msg += "\n\n入れられなかったもの:\n" + "\n".join(errors)
            if warn_running:
                msg += "\n\n※ サーバーが動いているので、「⟳ 再起動」すると反映されます。"
            if result.client_only or result.installed:
                msg += "\n\n友達にも同じMODが必要です。「📦 友達用パック」タブでパックを作り直して配ってください。"
            messagebox.showinfo(APP_TITLE, msg, parent=self)
            self.clear_pick()
            self.load_mods_installed(refresh_results=True)
            self.load_home()
        self.bg(lambda: install_many(p, ids, prog.log), done,
                err=lambda e: (prog.finish(), messagebox.showerror(APP_TITLE, str(e), parent=self)))

    def load_mods_installed(self, refresh_results=False):
        p = self.profile
        if not p:
            return

        def work():
            ml = ModList(p.dir)
            ml.sync_with_folder()
            return ml

        def done(ml):
            if self.profile is not p:
                return
            self._ml = ml
            keep = set(self.installed.checked)
            self.installed.clear()
            self.installed.checked.clear()
            where_label = {"server": "サーバー ＋ 遊ぶ人", "client_only": "遊ぶ人だけ（パックに入る）"}
            for m in sorted(ml.items, key=lambda m: m["title"].lower()):
                where = where_label.get(m.get("location"), "?")
                if m.get("location") == "server" and m.get("client_side") == "unsupported":
                    where = "サーバーだけ"
                if m.get("manual") and not m.get("project_id"):
                    where += "（手動）"
                key = m["path"]
                self.installed.add_row(key, (m["title"], m.get("version_number") or "-", where,
                                             "ON" if m.get("enabled", True) else "OFF"), checked=key in keep)
            self.mods_count.configure(text=f"{len(ml.items)} 個")
            if refresh_results:
                have = self._installed_ids()
                for pid in self.results.get_children():
                    self.results.set(pid, "have", "✔ 入ってる" if pid in have else "")
            self.load_pack_tab()
        self.bg(work, done)

    def _selected_mods(self):
        ml = getattr(self, "_ml", None)
        if not ml:
            return None, []
        ids = set(self.installed.checked)
        recs = [m for m in ml.items if m["path"] in ids]
        if not recs:
            messagebox.showinfo(APP_TITLE, "下のリストで、操作したいMODの行をクリックして ■ にしてください。", parent=self)
        return ml, recs

    def enable_mods(self, on: bool):
        ml, recs = self._selected_mods()
        for m in recs:
            ml.set_enabled(m, on)
        if recs:
            self.installed.set_all(False)
            self.load_mods_installed()

    def remove_mods(self):
        ml, recs = self._selected_mods()
        if not recs:
            return
        if not messagebox.askyesno(APP_TITLE, f"{len(recs)} 個のMODを外しますか？\n"
                                              + "\n".join(m["title"] for m in recs[:15]), parent=self):
            return
        for m in recs:
            ml.remove(m)
        self.installed.set_all(False)
        self.load_mods_installed(refresh_results=True)
        self.load_home()

    def check_updates(self):
        p = self.profile

        def done(ups):
            if not ups:
                messagebox.showinfo(APP_TITLE, "すべて最新です！", parent=self)
                return
            UpdateWindow(self, p, ups)
        self.bg(lambda: modrinth.check_updates(ModList(p.dir), p.get("mc"), p.get("loader")), done,
                busy="更新をさがしています…")

    def add_jar_files(self):
        files = filedialog.askopenfilenames(parent=self, title="MODファイル（.jar）を選ぶ",
                                            filetypes=[("MOD", "*.jar")])
        if not files:
            return
        import shutil
        for f in files:
            shutil.copy(f, self.profile.mods_dir)
        self.load_mods_installed()

    # ================================================================ 友達を呼ぶ
    def _build_public(self):
        t = self.tabs["public"]
        ttk.Label(t, text="友達がインターネットから入れるようにする", style="Title.TLabel").pack(anchor="w")
        ttk.Label(t, text="下の A〜C のどれか1つを使い、出てきたアドレスを一番下の欄に入れてください。",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 8))

        a = ttk.LabelFrame(t, text=" A. playit.gg（いちばんおすすめ・ルーター設定なし・無料） ", padding=10)
        a.pack(fill="x", pady=4)
        ttk.Label(a, justify="left", text=(
            "① 「playit を起動」を押す → 黒い画面が開きます\n"
            "② 黒い画面に出る https://playit.gg/claim/… をブラウザで開き、アカウントを作って「Claim」\n"
            "③ playit.gg のサイトで「Add Tunnel」→「Minecraft Java」を選び、Local Port をサーバーのポートにする\n"
            "④ サイトに出た アドレス（例: xxxx.joinmc.link）を下の欄に貼る\n"
            "※ 2回目からは①だけでOK。黒い画面は遊んでいる間ずっと開いておいてください。")).pack(anchor="w")
        ab = ttk.Frame(a)
        ab.pack(fill="x", pady=(6, 0))
        self.b_playit = ttk.Button(ab, text="▶ playit を起動", command=self.toggle_playit)
        self.b_playit.pack(side="left")
        ttk.Button(ab, text="🌐 playit.gg のサイトを開く",
                   command=lambda: webbrowser.open("https://playit.gg/account/tunnels")).pack(side="left", padx=6)

        b = ttk.LabelFrame(t, text=" B. 自動ポート開放（ルーターが対応していれば一瞬） ", padding=10)
        b.pack(fill="x", pady=4)
        ttk.Label(b, text="うまくいかない回線も多い（光回線の IPv6 方式など）です。だめなら A を使ってください。").pack(anchor="w")
        bb = ttk.Frame(b)
        bb.pack(fill="x", pady=(6, 0))
        ttk.Button(bb, text="🔓 ポートを開ける", command=self.upnp_open).pack(side="left")
        ttk.Button(bb, text="🔒 閉じる", command=self.upnp_close).pack(side="left", padx=6)

        c = ttk.LabelFrame(t, text=" C. 同じ家（同じWi-Fi）の人だけ ", padding=10)
        c.pack(fill="x", pady=4)
        self.lan_label = ttk.Label(c, text="")
        self.lan_label.pack(side="left")
        ttk.Button(c, text="これを使う", command=lambda: self.set_address(self._lan_addr())).pack(side="left", padx=8)

        d = ttk.LabelFrame(t, text=" 友達に伝えるアドレス ", padding=10)
        d.pack(fill="x", pady=(10, 4))
        self.addr_var = tk.StringVar()
        ttk.Entry(d, textvariable=self.addr_var, font=(FONT, 13), width=40).pack(side="left")
        ttk.Button(d, text="保存", command=lambda: self.set_address(self.addr_var.get().strip())).pack(side="left", padx=4)
        ttk.Button(d, text="📋 コピー", command=self.copy_address).pack(side="left")
        ttk.Label(t, text="保存したアドレスは友達用パックにも入り、友達のマイクラのサーバー一覧に最初から表示されます。",
                  style="Sub.TLabel").pack(anchor="w")

    def _port(self):
        try:
            return int(self.profile.props().get("server-port", "25565") or 25565)
        except ValueError:
            return 25565

    def _lan_addr(self):
        ip = network.local_ip()
        return ip if self._port() == 25565 else f"{ip}:{self._port()}"

    def load_public(self):
        self.addr_var.set(self.profile.get("public_address", ""))
        self.lan_label.configure(text=f"このPCのアドレス:  {self._lan_addr()}")
        self.b_playit.configure(text="■ playit を止める" if self._playit_running() else "▶ playit を起動")

    def set_address(self, addr):
        self.addr_var.set(addr)
        self.profile.set(public_address=addr)
        self.load_home()
        self.set_status("アドレスを保存しました")

    def copy_address(self):
        self.clipboard_clear()
        self.clipboard_append(self.addr_var.get())
        self.set_status("コピーしました。LINE や Discord に貼って友達に送りましょう")

    def _playit_running(self):
        return self.playit_proc is not None and self.playit_proc.poll() is None

    def toggle_playit(self):
        if self._playit_running():
            self.playit_proc.terminate()
            self.playit_proc = None
            self.load_public()
            return

        def done(proc):
            self.playit_proc = proc
            self.load_public()
        self.bg(lambda: network.start_playit(lambda m: self.ui(lambda: self.set_status(m))), done,
                busy="playit を準備しています…")

    def upnp_open(self):
        port = self._port()

        def done(addr):
            self.set_address(addr)
            messagebox.showinfo(APP_TITLE, f"ポート {port} を開けました！\n友達に伝えるアドレス: {addr}\n\n"
                                           "※ つながらない時は A の playit.gg を使ってください。", parent=self)
        self.bg(lambda: network.upnp_open(port, lambda m: self.ui(lambda: self.set_status(m))), done,
                busy="ルーターをさがしています…")

    def upnp_close(self):
        port = self._port()
        self.bg(lambda: network.upnp_close(port),
                lambda _: messagebox.showinfo(APP_TITLE, "ポートを閉じました。", parent=self))

    # ================================================================ 友達用パック
    def _build_pack(self):
        t = self.tabs["pack"]
        ttk.Label(t, text="友達用パックを作る", style="Title.TLabel").pack(anchor="w")
        ttk.Label(t, justify="left", text=(
            "サーバーと同じMODが入った「.mrpack」ファイルを作ります。これを友達に送ると、"
            "友達はワンクリックで同じMODセットを入れられます（サーバーのアドレスも最初から登録されます）。"
        ), wraplength=880).pack(anchor="w", pady=(0, 6))
        f = ttk.Frame(t)
        f.pack(fill="x")
        ttk.Label(f, text="パックのバージョン:").pack(side="left")
        self.packver_var = tk.StringVar(value="1.0.0")
        ttk.Entry(f, textvariable=self.packver_var, width=10).pack(side="left", padx=4)
        self.cfg_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="MODの設定（configフォルダ）も入れる", variable=self.cfg_var).pack(side="left", padx=12)
        g = ttk.Frame(t)
        g.pack(fill="x", pady=8)
        ttk.Button(g, text="📦 友達用パックを作る", style="Big.TButton", command=self.export_pack).pack(side="left")
        ttk.Button(g, text="🎮 自分のPCのマイクラにも入れる", style="Big.TButton",
                   command=self.install_self).pack(side="left", padx=8)
        self.pack_info = ttk.Label(t, text="", style="Sub.TLabel")
        self.pack_info.pack(anchor="w")

        body = ttk.Frame(t)
        body.pack(fill="both", expand=True, pady=(6, 0))
        left = ttk.LabelFrame(body, text=" パックに入れるMOD ", padding=8)
        left.pack(side="left", fill="both", expand=True)
        self.packlist = CheckTree(left, [("title", "名前", 220), ("need", "友達にとって", 170)],
                                  on_toggle=self._pack_toggled, check_label="入れる")
        self.packlist.pack(fill="both", expand=True)
        hb = ttk.Frame(left)
        hb.pack(fill="x", pady=4)
        self.pack_count = ttk.Label(hb, text="", style="Sub.TLabel")
        self.pack_count.pack(side="left")
        ttk.Button(hb, text="すべて入れる", command=lambda: self.packlist.set_all(True)).pack(side="right")
        ttk.Button(hb, text="選べるMODを外す", command=lambda: self.packlist.set_all(False)).pack(side="right", padx=4)
        ttk.Label(left, justify="left", style="Sub.TLabel", wraplength=480, text=(
            "🔒 必須 … サーバーに入るために全員に必要（外せません）\n"
            "■ / □ 選べる … 軽量化・ミニマップなど、なくても入れるMOD。行をクリックで切りかえ。"
            "友達も入れるか選べます")).pack(anchor="w")

        box = ttk.LabelFrame(body, text=" 友達に送るメッセージ ", padding=8)
        box.pack(side="left", fill="both", padx=(10, 0))
        self.invite = tk.Text(box, width=40, height=12, wrap="word", font=(FONT, 10))
        self.invite.pack(fill="both", expand=True)
        ttk.Button(box, text="📋 メッセージをコピー", command=self.copy_invite).pack(anchor="e", pady=(6, 0))

    def load_pack_tab(self):
        p = self.profile
        if not p:
            return

        def done(mods):
            if self.profile is not p:
                return
            excluded = set(p.get("pack_exclude", []))  # 表示する時点の選択を使う
            self.packlist.clear()
            self.packlist.checked.clear()
            req = opt = 0
            for m in sorted(mods, key=lambda m: (mrpack.friend_need(m) != "required", m["title"].lower())):
                need = mrpack.friend_need(m)
                if need == "required":
                    req += 1
                    self.packlist.add_row(m["path"], (m["title"], "必須（全員に必要）"), locked=True)
                else:
                    opt += 1
                    self.packlist.add_row(m["path"], (m["title"], "選べる（なくてもOK）"),
                                          checked=m["path"] not in excluded)
            self.pack_count.configure(text=f"必須 {req} 個 ／ 選べる {opt} 個")
        self.bg(lambda: mrpack.pack_candidates(p), done)

    def _pack_toggled(self, path, on):
        ex = set(self.profile.get("pack_exclude", []))
        if on:
            ex.discard(path)
        else:
            ex.add(path)
        self.profile.set(pack_exclude=sorted(ex))

    def _pack_path(self):
        from .paths import safe_name
        return os.path.join(self.profile.dir, "exports",
                            f"{safe_name(self.profile.name)}-{safe_name(self.packver_var.get())}.mrpack")

    def export_pack(self, after=None):
        p = self.profile
        if not p.get("public_address"):
            if not messagebox.askyesno(APP_TITLE, "友達に伝えるアドレスがまだ設定されていません。\n"
                                                  "このまま作りますか？（あとで作り直せます）", parent=self):
                return
        out = self._pack_path()
        os.makedirs(os.path.dirname(out), exist_ok=True)
        exclude = set(p.get("pack_exclude", []))

        def done(info):
            self.pack_info.configure(text=f"できました: {out}（Modrinth のMOD {info['files']} 個 ＋ 手動のMOD {info['manual']} 個）")
            self._write_invite(os.path.basename(out))
            if after:
                after(out)
            else:
                open_folder(os.path.dirname(out))
        self.bg(lambda: mrpack.export_pack(p, out, p.get("public_address"), self.packver_var.get(),
                                           self.cfg_var.get(), exclude=exclude), done, busy="パックを作っています…")

    def _write_invite(self, fname):
        p = self.profile
        addr = p.get("public_address") or "（アドレス）"
        msg = (f"マイクラのMODサーバー「{p.name}」を立てたよ！\n\n"
               f"■ バージョン: マイクラ Java版 {p.get('mc')}（{p.loader_label()}）\n"
               f"■ アドレス: {addr}\n\n"
               f"【入り方】添付の「{fname}」を使ってね。\n"
               "かんたんな方法（どれか1つ）:\n"
               "1) 「OneClickServerMC」を開いて「🎮 友達のサーバーに参加する」→ ファイルを選ぶ → 入れたいMODを選んで「入れる」\n"
               "2) Prism Launcher / Modrinth App を使っているなら、ファイルを開く（ドラッグ）だけ\n\n"
               "入れたら公式ランチャーで「" + p.name + "（マルチ用）」を選んでプレイ →\n"
               "「マルチプレイ」にサーバーが最初から入ってるよ。\n"
               "※ 入れないときは名前を教えて！（ホワイトリストに追加するよ）")
        self.invite.delete("1.0", "end")
        self.invite.insert("1.0", msg)

    def copy_invite(self):
        self.clipboard_clear()
        self.clipboard_append(self.invite.get("1.0", "end").strip())
        self.set_status("メッセージをコピーしました")

    def install_self(self):
        def go(path):
            JoinWindow(self, path)
        self.export_pack(after=go)

    # ================================================================ メンバー
    def _build_members(self):
        t = self.tabs["members"]
        ttk.Label(t, text="入れる人の管理", style="Title.TLabel").pack(anchor="w")
        ttk.Label(t, text="ホワイトリストがON（おすすめ）だと、ここに名前がある人だけ入れます。知らない人が入ってこないので安全です。",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 8))
        f = ttk.Frame(t)
        f.pack(fill="both", expand=True)
        self.lists = {}
        for kind, title in [("whitelist", "✅ ホワイトリスト（入ってOKな人）"), ("ops", "👑 OP（管理者）")]:
            col = ttk.LabelFrame(f, text=f" {title} ", padding=8)
            col.pack(side="left", fill="both", expand=True, padx=(0, 8))
            lb = tk.Listbox(col, font=(FONT, 11), height=14)
            lb.pack(fill="both", expand=True)
            row = ttk.Frame(col)
            row.pack(fill="x", pady=(6, 0))
            var = tk.StringVar()
            ent = ttk.Entry(row, textvariable=var)
            ent.pack(side="left", fill="x", expand=True)
            ent.bind("<Return>", lambda e, k=kind: self.member_add(k))
            ttk.Button(row, text="追加", command=lambda k=kind: self.member_add(k)).pack(side="left", padx=4)
            ttk.Button(row, text="削除", command=lambda k=kind: self.member_remove(k)).pack(side="left")
            self.lists[kind] = (lb, var)
        wl = ttk.Frame(t)
        wl.pack(fill="x", pady=(8, 0))
        self.wl_var = tk.BooleanVar()
        ttk.Checkbutton(wl, text="ホワイトリストを使う（ON推奨）", variable=self.wl_var,
                        command=self.toggle_whitelist).pack(side="left")
        ttk.Label(wl, text="  ※ マイクラ Java版の「プレイヤー名」を正確に入れてください", style="Sub.TLabel").pack(side="left")

    def load_members(self):
        p = self.profile
        for kind, (lb, _v) in self.lists.items():
            lb.delete(0, "end")
            for e in server.read_list(p, kind):
                lb.insert("end", e.get("name", "?"))
        self.wl_var.set(p.props().get("white-list", "false") == "true")

    def member_add(self, kind):
        lb, var = self.lists[kind]
        name = var.get().strip()
        if not name:
            return
        r = self.runner()
        if r.running:
            r.send(f"whitelist add {name}" if kind == "whitelist" else f"op {name}")
            var.set("")
            self.after(1500, self.load_members)
            return
        self.bg(lambda: server.add_to_list(self.profile, kind, name),
                lambda n: (var.set(""), self.load_members(), self.set_status(f"{n} を追加しました")),
                busy=f"{name} を調べています…")

    def member_remove(self, kind):
        lb, _var = self.lists[kind]
        sel = lb.curselection()
        if not sel:
            return
        name = lb.get(sel[0])
        r = self.runner()
        if r.running:
            r.send(f"whitelist remove {name}" if kind == "whitelist" else f"deop {name}")
            self.after(1500, self.load_members)
        else:
            server.remove_from_list(self.profile, kind, name)
            self.load_members()

    def toggle_whitelist(self):
        on = "true" if self.wl_var.get() else "false"
        server.write_props(self.profile.props_path, {"white-list": on, "enforce-whitelist": on})
        r = self.runner()
        if r.running:
            r.send("whitelist on" if on == "true" else "whitelist off")

    # ================================================================ バックアップ
    def _build_backup(self):
        t = self.tabs["backup"]
        ttk.Label(t, text="バックアップ（ワールドの保存）", style="Title.TLabel").pack(anchor="w")
        row = ttk.Frame(t)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="自動バックアップ:").pack(side="left")
        self.bk_int = tk.StringVar()
        ttk.Combobox(row, textvariable=self.bk_int, state="readonly", width=12,
                     values=["しない", "15分ごと", "30分ごと", "60分ごと", "120分ごと"]).pack(side="left", padx=4)
        ttk.Label(row, text="  自動バックアップを残す数:").pack(side="left")
        self.bk_keep = tk.StringVar()
        ttk.Spinbox(row, from_=1, to=100, textvariable=self.bk_keep, width=5).pack(side="left", padx=4)
        ttk.Button(row, text="設定を保存", command=self.save_backup_settings).pack(side="left", padx=8)

        self.bk_list = ttk.Treeview(t, columns=("when", "kind", "size"), show="headings", height=12)
        for c, label, w in [("when", "日時", 220), ("kind", "種類", 100), ("size", "大きさ", 100)]:
            self.bk_list.heading(c, text=label)
            self.bk_list.column(c, width=w)
        self.bk_list.pack(fill="both", expand=True)
        b = ttk.Frame(t)
        b.pack(fill="x", pady=6)
        ttk.Button(b, text="💾 今すぐバックアップ", style="Big.TButton", command=self.backup_now).pack(side="left")
        ttk.Button(b, text="⏪ 選んだ時点にもどす", command=self.restore).pack(side="left", padx=6)
        ttk.Button(b, text="📂 フォルダを開く",
                   command=lambda: open_folder(server.backups_dir(self.profile))).pack(side="left")

    def load_backups(self):
        p = self.profile
        iv = int(p.get("backup_interval_min", 30) or 0)
        self.bk_int.set("しない" if iv == 0 else f"{iv}分ごと")
        self.bk_keep.set(str(p.get("backup_keep", 10)))
        self.bk_list.delete(*self.bk_list.get_children())
        for path in server.list_backups(p):
            fn = os.path.basename(path)
            stamp, kind = fn[:-4].split("_", 1) if "_" in fn else (fn, "")
            when = f"{stamp[0:4]}/{stamp[4:6]}/{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}"
            self.bk_list.insert("", "end", iid=path, values=(
                when, {"auto": "自動", "manual": "手動"}.get(kind, kind),
                f"{os.path.getsize(path) / 1024 / 1024:.1f} MB"))

    def save_backup_settings(self):
        v = self.bk_int.get()
        iv = 0 if v == "しない" else int(v.replace("分ごと", ""))
        try:
            keep = max(1, int(self.bk_keep.get()))
        except ValueError:
            keep = 10
        self.profile.set(backup_interval_min=iv, backup_keep=keep)
        self.set_status("バックアップ設定を保存しました")

    def backup_now(self):
        r = self.runner()
        self.bg(lambda: r.backup(), lambda path: (self.load_backups(), self.set_status(f"保存しました: {os.path.basename(path)}")),
                busy="バックアップ中…")

    def restore(self):
        sel = self.bk_list.selection()
        if not sel:
            return
        if self.runner().running:
            messagebox.showinfo(APP_TITLE, "先にサーバーを止めてください。", parent=self)
            return
        if not messagebox.askyesno(APP_TITLE, "ワールドをこの時点にもどしますか？\n"
                                              "（今のワールドも念のため backups フォルダに残します）", parent=self):
            return
        self.bg(lambda: server.restore_backup(self.profile, sel[0]),
                lambda _: (self.load_backups(), messagebox.showinfo(APP_TITLE, "もどしました！", parent=self)),
                busy="もどしています…")

    # ================================================================ 設定
    def _build_settings(self):
        t = self.tabs["settings"]
        ttk.Label(t, text="サーバーの設定", style="Title.TLabel").pack(anchor="w")
        grid = ttk.Frame(t)
        grid.pack(fill="x", pady=8)
        self.prop_vars = {}
        for i, (key, label) in enumerate(server.PROP_LABELS):
            ttk.Label(grid, text=label).grid(row=i, column=0, sticky="w", pady=2)
            v = tk.StringVar()
            if key in ("difficulty", "gamemode", "pvp", "white-list", "allow-flight"):
                vals = {"difficulty": ["peaceful", "easy", "normal", "hard"],
                        "gamemode": ["survival", "creative", "adventure", "spectator"]}.get(key, ["true", "false"])
                w = ttk.Combobox(grid, textvariable=v, values=vals, state="readonly", width=34)
            else:
                w = ttk.Entry(grid, textvariable=v, width=36)
            w.grid(row=i, column=1, sticky="w", padx=8)
            self.prop_vars[key] = v
        opt = ttk.Frame(t)
        opt.pack(fill="x", pady=4)
        self.autorestart_var = tk.BooleanVar()
        ttk.Checkbutton(opt, text="サーバーが落ちたら自動で再起動する", variable=self.autorestart_var).pack(side="left")
        b = ttk.Frame(t)
        b.pack(fill="x", pady=8)
        ttk.Button(b, text="💾 設定を保存", style="Big.TButton", command=self.save_settings_tab).pack(side="left")
        ttk.Button(b, text="📂 サーバーのフォルダを開く", command=lambda: open_folder(self.profile.dir)).pack(side="left", padx=6)
        ttk.Button(b, text="🗑 このサーバーを削除", command=self.delete_server).pack(side="right")
        ttk.Label(t, text="※ 設定はサーバーを再起動すると反映されます。", style="Sub.TLabel").pack(anchor="w")

    def load_settings_tab(self):
        props = self.profile.props()
        for k, v in self.prop_vars.items():
            v.set(props.get(k, ""))
        self.autorestart_var.set(bool(self.profile.get("auto_restart", True)))

    def save_settings_tab(self):
        try:
            int(self.prop_vars["server-port"].get())
            int(self.prop_vars["max-players"].get())
        except ValueError:
            messagebox.showerror(APP_TITLE, "ポート番号と最大人数は数字で入れてください。", parent=self)
            return
        updates = {k: v.get() for k, v in self.prop_vars.items()}
        updates["enforce-whitelist"] = updates["white-list"]
        server.write_props(self.profile.props_path, updates)
        self.profile.set(auto_restart=self.autorestart_var.get())
        self.load_home()
        self.load_members()
        self.set_status("設定を保存しました（再起動で反映）")

    def delete_server(self):
        p = self.profile
        if self.runner().running:
            messagebox.showinfo(APP_TITLE, "先にサーバーを止めてください。", parent=self)
            return
        if not messagebox.askyesno(APP_TITLE, f"「{p.name}」を削除しますか？\nワールドやバックアップも全部消えます。", parent=self):
            return
        if not messagebox.askyesno(APP_TITLE, "本当に消しますか？（元にもどせません）", parent=self):
            return
        import shutil
        shutil.rmtree(p.dir, ignore_errors=True)
        self.runners.pop(p.dir, None)
        self.profile = None
        self.refresh_servers()

    # ================================================================ ダイアログ
    def new_server_dialog(self):
        NewServerWindow(self)

    def modpack_dialog(self):
        ModpackWindow(self)

    def join_dialog(self):
        path = filedialog.askopenfilename(parent=self, title="友達からもらったパック（.mrpack）を選ぶ",
                                          filetypes=[("MODパック", "*.mrpack"), ("すべて", "*.*")])
        if path:
            JoinWindow(self, path)

    def help_dialog(self):
        HelpWindow(self)

    def on_close(self):
        running = [r for r in self.runners.values() if r.running]
        if running:
            if not messagebox.askyesno(APP_TITLE, "動いているサーバーがあります。\n保存して止めてから終了しますか？", parent=self):
                return
            self.set_status("サーバーを止めています…")
            self.update()
            for r in running:
                r.stop()
        if self._playit_running():
            self.playit_proc.terminate()
        self.destroy()


# =====================================================================
class ProgressWindow(tk.Toplevel):
    def __init__(self, app: App, title: str):
        super().__init__(app)
        self.app = app
        self.title(title)
        self.geometry("560x320")
        self.transient(app)
        ttk.Label(self, text=title, style="H.TLabel").pack(anchor="w", padx=10, pady=(10, 4))
        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.pack(fill="x", padx=10)
        self.bar.start(12)
        self.text = tk.Text(self, height=12, font=(MONO, 9), state="disabled", wrap="word")
        self.text.pack(fill="both", expand=True, padx=10, pady=10)
        self.protocol("WM_DELETE_WINDOW", lambda: None)

    def log(self, msg):
        def add():
            if not self.winfo_exists():
                return
            self.text.configure(state="normal")
            self.text.insert("end", str(msg) + "\n")
            self.text.see("end")
            self.text.configure(state="disabled")
        self.app.ui(add)

    def finish(self):
        if self.winfo_exists():
            self.destroy()


# =====================================================================
def install_many(prof, ids, log):
    """選んだMODをまとめて入れる。1つ失敗しても残りは続ける。"""
    ml = ModList(prof.dir)
    result = modrinth.InstallResult()
    visiting: set = set()
    errors = []
    for pid in ids:
        try:
            modrinth.install(ml, pid, prof.get("mc"), prof.get("loader"), log, result, visiting)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    return result, errors


LOADER_SHORT = {"fabric": "Fabric", "neoforge": "NeoForge", "forge": "Forge"}


class NewServerWindow(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("新しいサーバーを作る")
        self.geometry("1120x720")
        self.minsize(980, 620)
        self.transient(app)
        self.grab_set()
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="新しいサーバーを作る", style="Title.TLabel").pack(anchor="w")
        cols = ttk.Frame(outer)
        cols.pack(fill="both", expand=True, pady=(8, 0))

        # ---- 左：基本の設定 ----
        f = ttk.Frame(cols, width=400)
        f.pack(side="left", fill="y")
        f.pack_propagate(False)
        g = ttk.Frame(f)
        g.pack(fill="x")
        ttk.Label(g, text="① サーバーの名前").grid(row=0, column=0, sticky="w", pady=4)
        self.name = tk.StringVar(value="みんなのMODサーバー")
        ttk.Entry(g, textvariable=self.name, width=24).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(g, text="② マイクラのバージョン").grid(row=1, column=0, sticky="w", pady=4)
        self.mc = tk.StringVar()
        self.mc_box = ttk.Combobox(g, textvariable=self.mc, state="readonly", width=16)
        self.mc_box.grid(row=1, column=1, sticky="w", padx=8)
        self.mc_box.bind("<<ComboboxSelected>>", lambda e: (self.check_loaders(), self.load_mods()))
        ttk.Label(g, text="③ MODの種類").grid(row=2, column=0, sticky="w", pady=4)
        self.loader = tk.StringVar(value="おまかせ")
        self.loader_box = ttk.Combobox(g, textvariable=self.loader, state="readonly", width=16,
                                       values=["おまかせ", "Fabric", "NeoForge", "Forge", "バニラ（MODなし）"])
        self.loader_box.grid(row=2, column=1, sticky="w", padx=8)
        self.loader_box.bind("<<ComboboxSelected>>", lambda e: self.load_mods())
        self.loader_note = ttk.Label(g, text="", style="Sub.TLabel", wraplength=230)
        self.loader_note.grid(row=3, column=1, sticky="w", padx=8)
        ttk.Label(g, text="メモリ").grid(row=4, column=0, sticky="w", pady=4)
        self.mem = tk.IntVar(value=recommended_mem(app.ram))
        mf = ttk.Frame(g)
        mf.grid(row=4, column=1, sticky="w", padx=8)
        self.mem_lbl = ttk.Label(mf, width=7)
        ttk.Scale(mf, from_=1024, to=max(2048, min(app.ram - 2048, 32768)), variable=self.mem, length=150,
                  command=lambda v: self.mem_lbl.configure(text=f"{round(float(v) / 512) / 2:.1f} GB")).pack(side="left")
        self.mem_lbl.pack(side="left", padx=4)
        self.mem_lbl.configure(text=f"{self.mem.get() / 1024:.1f} GB")
        ttk.Label(g, text="ポート番号").grid(row=5, column=0, sticky="w", pady=4)
        self.port = tk.StringVar(value=str(NewServerWindow._free_port()))
        ttk.Entry(g, textvariable=self.port, width=10).grid(row=5, column=1, sticky="w", padx=8)
        ttk.Label(f, style="Sub.TLabel", wraplength=380, justify="left", text=(
            "「おまかせ」にすると、右で選んだMODに一番多く対応している種類を自動で選びます。")).pack(anchor="w", pady=(6, 0))

        self.picked_label = ttk.Label(f, text="", wraplength=380, justify="left")
        self.picked_label.pack(anchor="w", pady=(10, 0))
        self.out = tk.Text(f, height=8, font=(MONO, 9), state="disabled")
        self.out.pack(fill="both", expand=True, pady=(8, 6))
        self.btn = ttk.Button(f, text="✨ サーバーを作る", style="Big.TButton", command=self.create)
        self.btn.pack(anchor="e")

        # ---- 右：MODを選ぶ ----
        r = ttk.LabelFrame(cols, text=" ④ 最初から入れるMODを選ぶ（あとからでも入れられます） ", padding=8)
        r.pack(side="left", fill="both", expand=True, padx=(12, 0))
        bar = ttk.Frame(r)
        bar.pack(fill="x")
        self.q = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.q, width=20)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self.load_mods())
        self.cat = tk.StringVar(value="すべて")
        cb = ttk.Combobox(bar, textvariable=self.cat, state="readonly", width=12,
                          values=[c[1] for c in modrinth.CATEGORIES])
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda ev: self.load_mods())
        ttk.Button(bar, text="🔍 検索", command=self.load_mods).pack(side="left")
        ttk.Button(bar, text="選択をクリア", command=self.clear_pick).pack(side="right")
        self.pick: dict[str, str] = {}
        self.hits: dict[str, dict] = {}
        self.list = CheckTree(r, [("title", "名前", 170), ("ok", "使える種類", 140), ("dl", "人気", 55),
                                  ("desc", "説明", 260)], on_toggle=self._toggled, selectmode="browse")
        self.list.pack(fill="both", expand=True, pady=4)
        ttk.Label(r, text="行をクリックで ■（入れる）/ □ を切りかえ。検索しなおしても選んだまま残ります。",
                  style="Sub.TLabel").pack(anchor="w")

        self.supported = []
        self._update_picked()
        app.bg(loaders.release_versions, self._got_versions,
               err=lambda e: self.log(f"バージョン一覧を取れませんでした: {e}"))

    @staticmethod
    def _free_port(_self=None):
        used = set()
        for p in server.list_profiles():
            try:
                used.add(int(p.props().get("server-port", 25565)))
            except ValueError:
                pass
        port = 25565
        while port in used:
            port += 1
        return port

    def log(self, msg):
        def add():
            if not self.winfo_exists():
                return
            self.out.configure(state="normal")
            self.out.insert("end", str(msg) + "\n")
            self.out.see("end")
            self.out.configure(state="disabled")
        self.app.ui(add)

    def _got_versions(self, vers):
        if not self.winfo_exists():
            return
        self.mc_box.configure(values=vers[:80])
        if vers:
            self.mc.set(vers[0])
            self.check_loaders()
            self.load_mods()

    def check_loaders(self):
        mc = self.mc.get()
        self.loader_note.configure(text="対応状況を調べています…")

        def done(sup):
            if not self.winfo_exists():
                return
            self.supported = sup
            names = [loaders.LOADER_LABEL[s] for s in sup if s != "vanilla"]
            self.loader_note.configure(text=("このバージョンで使える: " + "、".join(names)) if names
                                       else "このバージョンはMODローダー非対応です")
        self.app.bg(lambda: loaders.supported_loaders(mc), done)

    def _chosen_loader(self):
        """選んでいる種類（おまかせなら None）。"""
        label = self.loader.get()
        if label == "おまかせ":
            return None
        return {v: k for k, v in loaders.LOADER_LABEL.items()}[label]

    def load_mods(self):
        mc = self.mc.get()
        loader = self._chosen_loader()
        if not mc:
            return
        if loader == "vanilla":
            self.list.clear()
            self.log("バニラ（MODなし）ではMODは入れられません。")
            return
        cat = next(c[0] for c in modrinth.CATEGORIES if c[1] == self.cat.get())
        q = self.q.get().strip()

        def done(res):
            if not self.winfo_exists():
                return
            hits, _total = res
            self.list.clear()
            for h in hits:
                pid = h["project_id"]
                self.hits[pid] = h
                ok = " / ".join(v for k, v in LOADER_SHORT.items() if k in h.get("categories", [])
                                or k in h.get("display_categories", []))
                self.list.add_row(pid, (h["title"], ok or "-", fmt_num(h.get("downloads", 0)),
                                        h.get("description", "")), checked=pid in self.pick)
        self.app.bg(lambda: modrinth.search(q, mc, loader, category=cat,
                                            sort="downloads" if not q else "relevance", limit=50),
                    done, err=lambda e: self.log(f"MODの一覧を取れませんでした: {e}"))

    def _toggled(self, pid, on):
        if on and pid in self.hits:
            self.pick[pid] = self.hits[pid]["title"]
        else:
            self.pick.pop(pid, None)
        self._update_picked()

    def clear_pick(self):
        self.pick.clear()
        self.list.set_all(False)
        self._update_picked()

    def _update_picked(self):
        n = len(self.pick)
        if n:
            names = "、".join(list(self.pick.values())[:15]) + (" …" if n > 15 else "")
            self.picked_label.configure(text=f"選んだMOD（{n} 個）: {names}")
            self.btn.configure(text=f"✨ サーバーを作る（MOD {n} 個も入れる）")
        else:
            self.picked_label.configure(text="選んだMOD: まだありません（右の一覧から選べます）")
            self.btn.configure(text="✨ サーバーを作る")

    def create(self):
        name = self.name.get().strip() or "server"
        mc = self.mc.get()
        if not mc:
            return
        try:
            port = int(self.port.get())
        except ValueError:
            messagebox.showerror(APP_TITLE, "ポート番号は数字で入れてください。", parent=self)
            return
        chosen = self._chosen_loader()
        if chosen and self.supported and chosen not in self.supported:
            messagebox.showerror(APP_TITLE, f"{self.loader.get()} はマイクラ {mc} に対応していません。", parent=self)
            return
        picks = list(self.pick)
        if chosen == "vanilla" and picks:
            if not messagebox.askyesno(APP_TITLE, "バニラ（MODなし）ではMODは入れられません。\nMODなしで作りますか？", parent=self):
                return
            picks = []
        mem = int(round(self.mem.get() / 512) * 512)
        sup = list(self.supported) or ["fabric", "neoforge", "forge", "vanilla"]
        self.btn.state(["disabled"])

        def work():
            loader = chosen
            skipped = []
            if loader is None:  # おまかせ
                cands = [s for s in sup if s != "vanilla"]
                if picks and cands:
                    self.log("選んだMODに合う種類を調べています…")
                    best, support = modrinth.recommend_loader_ids(mc, picks, cands, self.log)
                    for c in cands:
                        self.log(f"  {loaders.LOADER_LABEL[c]}: {len(support[c])}/{len(picks)} 個対応")
                    loader = best
                    skipped = [self.pick[p] for p in picks if p not in support.get(best, [])]
                else:
                    loader = next((l for l in ("fabric", "neoforge", "forge") if l in sup), "vanilla")
                self.log(f"→ {loaders.LOADER_LABEL[loader]} にします")
            self.log(f"作成開始: {name}（{mc} / {loaders.LOADER_LABEL[loader]}）")
            prof = server.create_server(name, mc, loader, mem, port, self.log)
            result, errors = None, []
            if picks and loader != "vanilla":
                self.log(f"MODを {len(picks)} 個入れています…")
                result, errors = install_many(prof, picks, self.log)
            return prof, result, errors, skipped

        def done(res):
            prof, result, errors, skipped = res
            self.app.refresh_servers(select=prof.dir)
            if self.winfo_exists():
                self.destroy()
            msg = "サーバーができました！\n"
            if result:
                msg += "\n" + result.summary() + "\n"
            if errors:
                msg += "\n入れられなかったMOD（このバージョン・種類に対応していません）:\n・" + "\n・".join(errors) + "\n"
            elif skipped:
                msg += "\n対応していなかったMOD: " + "、".join(skipped) + "\n"
            msg += ("\n次は:\n① 「▶ 起動」を押す\n② 「🌐 友達を呼ぶ」でアドレスを作る\n"
                    "③ 「👥 メンバー」で友達の名前を追加\n④ 「📦 友達用パック」を作って送る")
            messagebox.showinfo(APP_TITLE, msg, parent=self.app)

        def err(e):
            if self.winfo_exists():
                self.btn.state(["!disabled"])
                self.log(f"失敗: {e}")
            messagebox.showerror(APP_TITLE, str(e), parent=self.app)
        self.app.bg(work, done, err)


# =====================================================================
class ModpackWindow(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("MODパックからサーバーを作る")
        self.geometry("820x600")
        self.transient(app)
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="MODパックからサーバーを作る", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text="たくさんのMODがセットになった「MODパック」を選ぶだけで、サーバーが完成します。",
                  style="Sub.TLabel").pack(anchor="w")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=8)
        self.q = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.q, width=30)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self.search())
        ttk.Button(bar, text="🔍 検索", command=self.search).pack(side="left", padx=4)
        ttk.Button(bar, text="📁 .mrpack ファイルから", command=self.from_file).pack(side="right")
        self.tree = ttk.Treeview(f, columns=("title", "dl", "desc"), show="headings")
        for c, label, w in [("title", "名前", 220), ("dl", "人気", 60), ("desc", "説明", 480)]:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, stretch=(c == "desc"))
        self.tree.pack(fill="both", expand=True)
        row = ttk.Frame(f)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="メモリ（GB）").pack(side="left")
        self.mem = tk.StringVar(value=str(max(6, recommended_mem(app.ram) // 1024)))
        ttk.Spinbox(row, from_=2, to=32, textvariable=self.mem, width=5).pack(side="left", padx=4)
        ttk.Label(row, text="  ※ 大きいMODパックは 6〜8GB 推奨").pack(side="left")
        self.btn = ttk.Button(row, text="✨ 選んだパックで作る", style="Big.TButton", command=self.create)
        self.btn.pack(side="right")
        self.hits = {}
        self.search()

    def search(self):
        def done(res):
            hits, _ = res
            self.tree.delete(*self.tree.get_children())
            self.hits = {h["project_id"]: h for h in hits}
            for h in hits:
                self.tree.insert("", "end", iid=h["project_id"], values=(
                    h["title"], fmt_num(h.get("downloads", 0)), h.get("description", "")))
        self.app.bg(lambda: modrinth.search(self.q.get(), "", None, project_type="modpack", sort="downloads"),
                    done, busy="MODパックを検索中…")

    def _mem_mb(self):
        try:
            return int(float(self.mem.get()) * 1024)
        except ValueError:
            return 6144

    def _make(self, get_pack, name_hint):
        port = NewServerWindow._free_port()
        prog = ProgressWindow(self.app, "MODパックからサーバーを作っています")
        mem = self._mem_mb()

        def work():
            path = get_pack(prog.log)
            return server.create_from_pack(name_hint, path, mem, port, prog.log)

        def done(prof):
            prog.finish()
            self.app.refresh_servers(select=prof.dir)
            if self.winfo_exists():
                self.destroy()
            messagebox.showinfo(APP_TITLE, "MODパックのサーバーができました！\n「▶ 起動」→「🌐 友達を呼ぶ」→"
                                           "「📦 友達用パック」の順に進めてください。", parent=self.app)
        self.app.bg(work, done, err=lambda e: (prog.finish(), messagebox.showerror(APP_TITLE, str(e), parent=self.app)))

    def create(self):
        sel = self.tree.selection()
        if not sel:
            return
        h = self.hits[sel[0]]
        self._make(lambda log: modrinth.download_modpack(h["project_id"], CACHE_DIR, log=log), h["title"])

    def from_file(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("MODパック", "*.mrpack")])
        if path:
            try:
                name = mrpack.pack_info(path)["name"]
            except Exception as e:  # noqa: BLE001
                messagebox.showerror(APP_TITLE, f"パックを読めませんでした: {e}", parent=self)
                return
            self._make(lambda log: path, name)


# =====================================================================
class JoinWindow(tk.Toplevel):
    """友達のサーバーに参加する（.mrpack を自分のマイクラに入れる）。"""

    def __init__(self, app: App, pack_path: str):
        super().__init__(app)
        self.app = app
        self.pack_path = pack_path
        self.title("サーバーに参加する")
        self.geometry("780x760")
        self.minsize(680, 620)
        self.transient(app)
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="🎮 サーバーに参加する準備", style="Title.TLabel").pack(anchor="w")
        try:
            info = mrpack.pack_info(pack_path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"このファイルは読めませんでした: {e}", parent=app)
            self.destroy()
            return
        self.files = [x for x in info["files"] if x.get("env", {}).get("client") != "unsupported"]
        ttk.Label(f, justify="left", text=(
            f"パック名: {info['name']}　／　マイクラ: {info['mc']}　／　"
            f"種類: {loaders.LOADER_LABEL.get(info['loader'], info['loader'])}")).pack(anchor="w", pady=(6, 8))
        g = ttk.Frame(f)
        g.pack(fill="x")
        ttk.Label(g, text="マイクラのフォルダ").grid(row=0, column=0, sticky="w")
        self.mcdir = tk.StringVar(value=app.settings.get("minecraft_dir"))
        ttk.Entry(g, textvariable=self.mcdir, width=50).grid(row=0, column=1, padx=6)
        ttk.Button(g, text="選ぶ", command=self.pick).grid(row=0, column=2)
        ttk.Label(g, text="マイクラに使うメモリ").grid(row=1, column=0, sticky="w", pady=6)
        self.mem = tk.StringVar(value=str(max(4, min(8, app.ram // 1024 // 3))))
        mr = ttk.Frame(g)
        mr.grid(row=1, column=1, sticky="w", padx=6)
        ttk.Spinbox(mr, from_=2, to=16, textvariable=self.mem, width=5).pack(side="left")
        ttk.Label(mr, text="GB（MODが多いなら 6GB 以上がおすすめ）").pack(side="left")

        box = ttk.LabelFrame(f, text=" 入れるMODを選ぶ ", padding=8)
        box.pack(fill="both", expand=True, pady=(8, 0))
        hb = ttk.Frame(box)
        hb.pack(fill="x")
        self.count = ttk.Label(hb, text="", style="Sub.TLabel")
        self.count.pack(side="left")
        ttk.Button(hb, text="すべて入れる", command=lambda: self.list.set_all(True)).pack(side="right")
        ttk.Button(hb, text="選べるMODを外す", command=lambda: self.list.set_all(False)).pack(side="right", padx=4)
        self.list = CheckTree(box, [("title", "名前", 300), ("need", "", 200)], check_label="入れる",
                              on_toggle=lambda i, on: self._count(), selectmode="browse", height=9)
        self.list.pack(fill="both", expand=True, pady=4)
        req = 0
        for x in sorted(self.files, key=lambda x: (x.get("env", {}).get("client") == "optional", x["path"])):
            name = os.path.basename(x["path"])
            if x.get("env", {}).get("client") == "optional":
                self.list.add_row(x["path"], (name, "選べる（なくても入れる）"), checked=True)
            else:
                req += 1
                self.list.add_row(x["path"], (name, "必須（ないと入れない）"), locked=True)
        self._count()
        ttk.Label(box, text="🔒 は必ず必要なMOD。■□ のMODは、行をクリックして入れるかどうか選べます（重いPCなら外すのもアリ）。",
                  style="Sub.TLabel", wraplength=700, justify="left").pack(anchor="w")
        self._lookup_names()

        ttk.Label(f, text="※ 先に公式ランチャーで一度マイクラを起動したことがある必要があります。"
                          "ふだんのマイクラとは別のフォルダに入るので、混ざりません。",
                  style="Sub.TLabel", wraplength=740, justify="left").pack(anchor="w", pady=6)
        self.out = tk.Text(f, height=6, font=(MONO, 9), state="disabled")
        self.out.pack(fill="x", pady=(0, 6))
        row = ttk.Frame(f)
        row.pack(fill="x")
        self.btn = ttk.Button(row, text="⬇ 入れる", style="Big.TButton", command=self.go)
        self.btn.pack(side="left")
        self.btn_launch = ttk.Button(row, text="▶ マイクラを開く", command=self.launch)
        self.btn_launch.pack(side="left", padx=8)
        self.btn_launch.state(["disabled"])

    def _count(self):
        n = len([i for i in self.list.checked if self.list.exists(i)])
        self.count.configure(text=f"入れるMOD: {n} / {len(self.files)} 個")

    def _lookup_names(self):
        """ファイル名だけだと分かりにくいので、Modrinth で正式な名前を調べる。"""
        hashes = {x["hashes"].get("sha1"): x["path"] for x in self.files if x.get("hashes", {}).get("sha1")}

        def work():
            found = net.post_json(f"{modrinth.API}/version_files", {"hashes": list(hashes), "algorithm": "sha1"})
            projs = modrinth.projects([v["project_id"] for v in found.values()]) if found else {}
            return {hashes[h]: projs[v["project_id"]]["title"] for h, v in found.items() if v["project_id"] in projs}

        def done(names):
            if not self.winfo_exists():
                return
            for path, title in names.items():
                if self.list.exists(path):
                    self.list.set(path, "title", title)
        if hashes:
            self.app.bg(work, done, err=lambda e: None)

    def pick(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.mcdir.get())
        if d:
            self.mcdir.set(d)

    def log(self, msg):
        def add():
            if not self.winfo_exists():
                return
            self.out.configure(state="normal")
            self.out.insert("end", str(msg) + "\n")
            self.out.see("end")
            self.out.configure(state="disabled")
        self.app.ui(add)

    def go(self):
        mcdir = self.mcdir.get()
        if not os.path.isdir(mcdir):
            if not messagebox.askyesno(APP_TITLE, "マイクラのフォルダが見つかりません。\n"
                                                  "公式ランチャーで一度マイクラを起動しましたか？\n\nこのまま続けますか？", parent=self):
                return
        self.app.settings["minecraft_dir"] = mcdir
        save_settings(self.app.settings)
        try:
            mem = int(self.mem.get())
        except ValueError:
            mem = 4
        skip = {x["path"] for x in self.files if x["path"] not in self.list.checked}
        self.btn.state(["disabled"])

        def done(res):
            self.btn.state(["!disabled"])
            self.btn_launch.state(["!disabled"])
            messagebox.showinfo(APP_TITLE, f"準備できました！\n\n公式ランチャーで\n「{res['name']}（マルチ用）」\n"
                                           "を選んで「プレイ」→「マルチプレイ」を開いてください。", parent=self)

        def err(e):
            self.btn.state(["!disabled"])
            self.log(f"失敗: {e}")
            messagebox.showerror(APP_TITLE, str(e), parent=self)
        self.app.bg(lambda: client_install.join_with_pack(
            self.pack_path, mcdir, mem, os.path.join(CACHE_DIR, "installers"), self.log, skip=skip), done, err)

    def launch(self):
        if not client_install.open_launcher():
            messagebox.showinfo(APP_TITLE, "ランチャーが見つかりませんでした。ふだん通りマイクラのランチャーを開いてください。",
                                parent=self)


# =====================================================================
class UpdateWindow(tk.Toplevel):
    """更新があるMODの一覧から、更新するものを選ぶ。"""

    def __init__(self, app: App, prof, ups):
        super().__init__(app)
        self.app, self.prof, self.ups = app, prof, ups
        self.title("MODの更新")
        self.geometry("720x480")
        self.transient(app)
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"{len(ups)} 個のMODに新しいバージョンがあります", style="H.TLabel").pack(anchor="w")
        ttk.Label(f, text="更新するMODを選んでください（念のため先にバックアップをとるのがおすすめです）",
                  style="Sub.TLabel").pack(anchor="w")
        self.list = CheckTree(f, [("title", "名前", 240), ("old", "今", 150), ("new", "新しい", 150)],
                              check_label="更新", selectmode="browse")
        self.list.pack(fill="both", expand=True, pady=8)
        for m, v in ups:
            self.list.add_row(m["sha1"], (m["title"], m.get("version_number") or "-", v.get("version_number") or "-"),
                              checked=True)
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Button(row, text="⟳ 選んだMODを更新", style="Big.TButton", command=self.go).pack(side="left")
        ttk.Button(row, text="すべて選ぶ", command=lambda: self.list.set_all(True)).pack(side="left", padx=6)
        ttk.Button(row, text="選択を外す", command=lambda: self.list.set_all(False)).pack(side="left")

    def go(self):
        chosen = [(m, v) for m, v in self.ups if m["sha1"] in self.list.checked]
        if not chosen:
            return
        app, p = self.app, self.prof
        self.destroy()
        prog = ProgressWindow(app, "MODを更新しています")

        def work():
            ml = ModList(p.dir)
            for m, v in chosen:
                rec = next((x for x in ml.items if x.get("sha1") == m.get("sha1")), None)
                if rec:
                    modrinth.apply_update(ml, rec, v, prog.log)

        def fin(_):
            prog.finish()
            app.load_mods_installed()
            messagebox.showinfo(APP_TITLE, f"{len(chosen)} 個更新しました。友達用パックも作り直してください。", parent=app)
        app.bg(work, fin, err=lambda e: (prog.finish(), messagebox.showerror(APP_TITLE, str(e), parent=app)))


# =====================================================================
class HelpWindow(tk.Toplevel):
    TEXT = """■ サーバーを立てる人（あなた）
 1. 「＋ 新しいサーバーを作る」… 名前とバージョンを選んで作成
     （入れたいMODの名前を書いて「判定する」と、合う種類を自動で選びます）
     MODがたくさん入ったセットで遊ぶなら「📦 MODパックから作る」が一番かんたん
 2. 「🧩 MOD」タブ … 検索して「入れる」を押すだけ。前提MODも自動で入ります
 3. 「▶ 起動」… 初回だけ利用規約（EULA）の確認が出ます
 4. 「🌐 友達を呼ぶ」… playit.gg を使うとルーター設定なしで公開できます
 5. 「👥 メンバー」… 友達のマイクラの名前を追加（知らない人は入れません）
 6. 「📦 友達用パック」… パックを作って、メッセージと一緒に友達へ送る
 7. 自分も遊ぶ時は「🎮 自分のPCのマイクラにも入れる」

■ 参加する人（友達）
 1. このソフトを開いて「🎮 友達のサーバーに参加する」
 2. もらった .mrpack ファイルを選んで「⬇ 入れる」
 3. 公式ランチャーで「○○（マルチ用）」を選んでプレイ → マルチプレイ

■ 困ったとき
 ・入れない → サーバーが「起動完了」か、ホワイトリストに名前があるか、アドレスが正しいか確認
 ・重い → 「ホーム」のメモリを増やす / 設定の「見える距離」を 6〜8 に下げる
 ・MODを入れたら起動しない → 最後に入れたMODを OFF にして再起動。ログの赤い行がヒントです
 ・ワールドを壊した → 「💾 バックアップ」から元にもどせます
"""

    def __init__(self, app):
        super().__init__(app)
        self.title("使い方")
        self.geometry("700x560")
        t = tk.Text(self, wrap="word", font=(FONT, 10), padx=12, pady=12)
        t.insert("1.0", self.TEXT)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True)


def main():
    app = App()
    app.mainloop()
