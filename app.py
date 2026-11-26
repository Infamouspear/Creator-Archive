import base64
import json
import msvcrt
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit
from urllib.request import urlopen

from engine import Archive, DATA, NO_WINDOW, TABS, VERSION, tool
from updater import download_installer, find_update

TASK = "Creator Archive Background"
MODES = ("Automatic", "Review")
QUALITIES = ("Best", "2160", "1440", "1080", "720", "480")
SCHEDULES = ("Manual", "Daily", "Weekly")
BG = "#14171e"
SIDEBAR = "#1b1f28"
HEADER = "#222733"
BORDER = "#3d4452"
TEXT = "#f2f4f8"
MUTED = "#b8c0d1"


def local_scan(value):
    if not value:
        return "Never scanned"
    stamp = datetime.fromisoformat(value).astimezone()
    today = datetime.now().astimezone().date()
    time = stamp.strftime("%I:%M %p").lstrip("0")
    if stamp.date() == today:
        return f"Today, {time}"
    if stamp.date() == today - timedelta(days=1):
        return f"Yesterday, {time}"
    return f"{stamp:%b %d, %Y}, {time}"


def schedule_task(enabled):
    if not getattr(sys, "frozen", False):
        if enabled:
            raise RuntimeError("Install Creator Archive before enabling background scheduling.")
        return
    exe = sys.executable
    if not enabled:
        for name in (TASK, TASK + " Startup"):
            subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
        return
    for name, trigger, extra, flag in (
        (TASK, "MINUTE", ["/MO", "15"], "--background"),
        (TASK + " Startup", "ONLOGON", [], "--startup"),
    ):
        result = subprocess.run(["schtasks", "/Create", "/TN", name, "/SC", trigger, *extra,
                                 "/TR", f'"{exe}" {flag}', "/IT", "/F"],
                                capture_output=True, creationflags=NO_WINDOW)
        if result.returncode:
            raise RuntimeError("Windows could not register the background task.")


class App:
    def __init__(self, root, archive):
        self.root, self.archive = root, archive
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.images = ThreadPoolExecutor(max_workers=2)
        self.thumbnail_cache = {}
        self.thumbnail_urls = set()
        self.messages = SimpleQueue()
        self.current = None
        self.settings_dirty = False
        self.update_running = False
        self.update_installer = None
        self.archived_count = 0
        self.closing = False
        self.scan_batch = False
        self.scan_current = None
        self.next_schedule_check = 0
        self.archived_count = sum(c['archived'] for c in archive.creators())
        self.scale = root.winfo_fpixels("1i") / 96
        root.title(f"Creator Archive {VERSION}")
        root.geometry(f"{round(1100 * self.scale)}x{round(700 * self.scale)}")
        root.minsize(round(800 * self.scale), round(530 * self.scale))
        root.configure(background=BG)
        root.option_add("*TCombobox*Listbox.background", HEADER)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", "#305d83")
        root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=BG, foreground=TEXT)
        style.configure("Surface.TFrame", background=SIDEBAR)
        style.configure("Header.TFrame", background=HEADER)
        style.configure("Header.TLabel", background=HEADER, foreground=MUTED, padding=(0, 8))
        style.configure("Title.TLabel", font=("Segoe UI", 25, "bold"))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Error.TLabel", foreground="#ffacaa")
        style.configure("Treeview", background=BG, fieldbackground=BG, foreground=TEXT,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        rowheight=round(32 * self.scale))
        style.configure("Treeview.Heading", background=HEADER, foreground=TEXT,
                        bordercolor=BORDER, lightcolor=HEADER, darkcolor=HEADER)
        style.configure("TScrollbar", background=HEADER, troughcolor=BG, arrowcolor=MUTED,
                        bordercolor=BORDER, lightcolor=HEADER, darkcolor=HEADER)
        style.configure("TCheckbutton", indicatorbackground=HEADER, indicatorforeground=TEXT)
        style.map("TCheckbutton", indicatorbackground=[("selected", "#305d83"), ("!selected", HEADER)])
        style.configure("TButton", padding=(9, 5), background=SIDEBAR, bordercolor=BORDER)
        style.configure("Primary.TButton", background="#087bea", foreground="#ffffff", bordercolor="#087bea")
        style.configure("Nav.TButton", anchor="w", padding=(12, 9), background=SIDEBAR, borderwidth=0)
        style.configure("Selected.Nav.TButton", background="#254568")
        style.configure("Creator.TButton", anchor="w", padding=(0, 4), background=BG, borderwidth=0,
                        font=("Segoe UI", 12, "bold"))
        for control in ("TEntry", "TCombobox"):
            style.configure(control, fieldbackground=HEADER, foreground=TEXT, insertcolor=TEXT,
                            selectbackground="#305d83", selectforeground=TEXT, bordercolor=BORDER,
                            lightcolor=HEADER, darkcolor=HEADER)
            style.map(control, fieldbackground=[("disabled", SIDEBAR), ("!disabled", HEADER)],
                      foreground=[("disabled", MUTED), ("!disabled", TEXT)])
        style.configure("TCombobox", arrowcolor=TEXT)
        style.map("Treeview", background=[("selected", "#305d83"), ("!selected", BG)],
                  foreground=[("selected", TEXT), ("!selected", TEXT)])
        style.map("Treeview.Heading", background=[("active", "#324762"), ("!active", HEADER)])
        style.map("TButton", background=[("active", "#324762")])
        style.map("Primary.TButton", background=[("active", "#006ad1")])
        self.status = tk.StringVar(value="Ready")
        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)
        sidebar = ttk.Frame(body, style="Surface.TFrame", padding=(10, 20))
        sidebar.pack(side="left", fill="y")
        ttk.Label(sidebar, text="▣  Creator Archive", background=SIDEBAR,
                  font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=4, pady=(0, 24))
        ttk.Separator(body, orient="vertical").pack(side="left", fill="y")
        content = ttk.Frame(body)
        content.pack(side="left", fill="both", expand=True)
        self.dashboard, self.detail, self.downloads, self.settings_page = [
            ttk.Frame(content, padding=(18, 16)) for _ in range(4)]
        self.nav = {}
        for page, name in ((self.dashboard, "Creators"), (self.downloads, "Downloads"),
                           (self.settings_page, "Settings")):
            button = ttk.Button(sidebar, text=name, style="Nav.TButton", width=17,
                                command=lambda page=page: self.show_page(page))
            button.pack(fill="x", pady=2)
            self.nav[page] = button
        self.page = None
        self.show_page(self.dashboard)
        ttk.Separator(root).pack(fill="x")
        ttk.Label(root, textvariable=self.status, style="Muted.TLabel", padding=(12, 7)).pack(fill="x")
        self.setup_dashboard()
        self.setup_detail()
        self.setup_downloads()
        self.setup_settings()
        self.worker = threading.Thread(target=self.run_downloads, daemon=True)
        self.worker.start()
        self.submit(archive.verify)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.tick()
        if archive.settings()["auto_update"]:
            self.check_update(quiet=True)

    def buttons(self, parent, actions):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 8))
        for label, command in actions:
            ttk.Button(row, text=label, command=command).pack(side="left", padx=(0, 6))
        return row

    def tree(self, parent, columns, mode="extended", thumbnails=False):
        panel = ttk.Frame(parent)
        panel.pack(fill="both", expand=True)
        view = ttk.Treeview(panel, columns=[c[0] for c in columns], show="tree headings" if thumbnails else "headings", selectmode=mode)
        if thumbnails:
            view.column("#0", width=58, minwidth=58, stretch=False)
        scroll = ttk.Scrollbar(panel, orient="vertical", command=view.yview)
        view.configure(yscrollcommand=scroll.set)
        for label, width in columns:
            view.heading(label, text=label)
            view.column(label, width=width, minwidth=50, stretch=label in {"Creator", "Title", "Message"})
        view.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return view

    def show_page(self, page):
        if self.page:
            self.page.pack_forget()
        self.page = page
        page.pack(fill="both", expand=True)
        active = self.dashboard if page == self.detail else page
        for destination, button in self.nav.items():
            button.configure(style="Selected.Nav.TButton" if destination == active else "Nav.TButton")

    def setup_dashboard(self):
        heading = ttk.Frame(self.dashboard)
        heading.pack(fill="x", pady=(0, 14))
        labels = ttk.Frame(heading)
        labels.pack(side="left")
        ttk.Label(labels, text="Creators", style="Title.TLabel").pack(anchor="w")
        self.creator_count = tk.StringVar()
        ttk.Label(labels, textvariable=self.creator_count, style="Muted.TLabel").pack(anchor="w")
        ttk.Button(heading, text="+  Add creator", style="Primary.TButton",
                   command=self.add_creator).pack(side="right", padx=(8, 0))
        self.scan_all = ttk.Button(heading, text="Scan all",
                                   command=lambda: self.scan([c["id"] for c in self.archive.creators()]))
        self.scan_all.pack(side="right")
        panel = ttk.Frame(self.dashboard)
        panel.pack(fill="both", expand=True)
        canvas = tk.Canvas(panel, background=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(panel, orient="vertical", command=canvas.yview)
        def scroll_state(first, last):
            scroll.set(first, last)
            if float(first) > 0 or float(last) < 1:
                if not scroll.winfo_manager():
                    scroll.pack(side="right", fill="y", before=canvas)
            else:
                scroll.pack_forget()
        canvas.configure(yscrollcommand=scroll_state)
        canvas.pack(side="left", fill="both", expand=True)
        table = ttk.Frame(canvas)
        window = canvas.create_window(0, 0, window=table, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        table.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>", self.scroll_creators, add="+")
        self.creator_canvas = canvas
        self.creator_table = table
        self.creator_rows = {}
        self.creator_ids = None
        header = ttk.Frame(table, style="Header.TFrame", padding=(12, 0))
        header.pack(fill="x")
        self.creator_columns(header)
        for column, text in ((0, "Creator"), (1, "Archived"), (2, "Last successful scan")):
            ttk.Label(header, text=text, style="Header.TLabel").grid(row=0, column=column, sticky="w")
        self.empty_creators = ttk.Label(table, text="Add a YouTube creator to start your archive.",
                                        style="Muted.TLabel", padding=(12, 20))

    def scroll_creators(self, event):
        if self.page == self.dashboard:
            self.creator_canvas.yview_scroll(-int(event.delta / 120), "units")

    def creator_columns(self, frame):
        for column, width in enumerate((140, 110, 175, 68, 34)):
            frame.columnconfigure(column, weight=int(column == 0), minsize=round(width * self.scale))

    def creator_row(self, creator):
        ident = creator["id"]
        block = ttk.Frame(self.creator_table)
        block.pack(fill="x")
        ttk.Separator(block).pack(fill="x")
        row = ttk.Frame(block, padding=(12, 7))
        row.pack(fill="x")
        self.creator_columns(row)
        identity = ttk.Frame(row)
        identity.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        avatar = tk.Label(identity, text="•", background=HEADER, foreground=MUTED,
                          font=("Segoe UI", 15), cursor="hand2")
        avatar.pack(side="left", padx=(0, 9), ipadx=6, ipady=3)
        avatar.bind("<Button-1>", lambda event: self.open_creator(ident))
        labels = ttk.Frame(identity)
        labels.pack(side="left", fill="x", expand=True)
        name = ttk.Button(labels, style="Creator.TButton", command=lambda: self.open_creator(ident))
        name.pack(fill="x")
        handle = ttk.Label(labels, style="Muted.TLabel")
        handle.pack(anchor="w")
        handle.bind("<Button-1>", lambda event: self.open_creator(ident))
        archived = ttk.Label(row)
        archived.grid(row=0, column=1, sticky="w")
        scan_info = ttk.Frame(row)
        scan_info.grid(row=0, column=2, sticky="ew", padx=(0, 5))
        last = ttk.Label(scan_info)
        last.pack(anchor="w")
        state = ttk.Label(scan_info, style="Muted.TLabel", wraplength=round(145 * self.scale))
        scan = ttk.Button(row, text="Scan", width=6, command=lambda: self.scan([ident]))
        scan.grid(row=0, column=3, sticky="w")
        menu = tk.Menu(row, tearoff=False, background=HEADER, foreground=TEXT,
                       activebackground="#305d83", activeforeground=TEXT)
        menu.add_command(label="Open creator", command=lambda: self.open_creator(ident))
        menu.add_command(label="Remove", command=lambda: self.remove_creator(ident))
        more = ttk.Button(row, text="⋯", width=2)
        more.configure(command=lambda: menu.tk_popup(more.winfo_rootx(), more.winfo_rooty() + more.winfo_height()))
        more.grid(row=0, column=4, sticky="e")
        return block, avatar, name, handle, archived, last, state, scan

    def refresh_creators(self, creators):
        ids = tuple(c["id"] for c in creators)
        if ids != self.creator_ids:
            self.creator_ids = ids
            for block, *_ in self.creator_rows.values():
                block.destroy()
            self.creator_rows = {c["id"]: self.creator_row(c) for c in creators}
            self.creator_count.set(f"{len(ids)} creator" + ("" if len(ids) == 1 else "s"))
            self.empty_creators.pack_forget()
            if not ids:
                self.empty_creators.pack(anchor="w")
        self.scan_all.configure(state="disabled" if self.scan_batch or not ids else "normal")
        for creator in creators:
            ident = creator["id"]
            _, avatar, name, handle, archived, last, state, scan = self.creator_rows[ident]
            title = creator["name"]
            name.configure(text=title if len(title) <= 32 else title[:31] + "…")
            handle.configure(text=creator["handle"] or "")
            archived.configure(text=creator["archived"])
            last.configure(text=local_scan(creator["last_scan"]))
            scanning = ident == self.scan_current
            result = creator["scan_result"]
            if scanning:
                state.configure(text="Scanning…", style="Muted.TLabel")
                state.pack(anchor="w")
            elif result and result != "Success":
                state.configure(text=result, style="Error.TLabel")
                state.pack(anchor="w")
            else:
                state.pack_forget()
            scan.configure(state="disabled" if self.scan_batch else "normal")
            self.thumbnail(creator["thumbnail"], lambda image: avatar.configure(image=image, text=""),
                           avatar=True)

    def setup_detail(self):
        self.buttons(self.detail, [("‹  Back to creators", lambda: self.show_page(self.dashboard))])
        self.identity = tk.StringVar(value="Select a creator from the Creators page.")
        ttk.Label(self.detail, textvariable=self.identity, style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        self.buttons(self.detail, [
            ("Download selected", lambda: self.queue(self.items.selection())),
            ("Scan", lambda: self.scan([self.current] if self.current else [])),
            ("Creator settings", self.creator_settings), ("Scan history", self.history),
        ])
        self.buttons(self.detail, [("Open on YouTube", self.open_video)])
        row = ttk.Frame(self.detail)
        row.pack(fill="x", pady=(0, 7))
        self.video_search = tk.StringVar()
        self.video_results = tk.StringVar()
        self.video_filter_job = None
        ttk.Label(row, text="Search titles").pack(side="left", padx=(0, 6))
        search = ttk.Entry(row, textvariable=self.video_search, width=15)
        search.pack(side="left", fill="x", expand=True)
        search.bind("<Escape>", lambda event: self.video_search.set(""))
        ttk.Button(row, text="Clear", command=lambda: self.video_search.set("")).pack(side="left", padx=6)
        self.category = tk.StringVar(value="All categories")
        ttk.Combobox(row, textvariable=self.category, values=("All categories", *TABS),
                     width=16, state="readonly").pack(side="left")
        ttk.Label(self.detail, textvariable=self.video_results, style="Muted.TLabel").pack(anchor="w", pady=(0, 5))
        self.items = self.tree(self.detail, (("Title", 345), ("Category", 110), ("Uploaded", 95),
                                             ("Status", 100), ("Message", 240)), thumbnails=True)
        self.video_search.trace_add("write", self.schedule_video_filter)
        self.category.trace_add("write", self.schedule_video_filter)

    def schedule_video_filter(self, *_):
        if self.video_filter_job is not None:
            self.root.after_cancel(self.video_filter_job)
        self.video_filter_job = self.root.after(100, self.refresh_videos)

    def refresh_videos(self):
        if self.video_filter_job is not None:
            self.root.after_cancel(self.video_filter_job)
            self.video_filter_job = None
        videos = self.archive.videos(self.current) if self.current else []
        category = self.category.get()
        query = self.video_search.get().strip()
        terms = query.casefold().split()
        matches = []
        for video in videos:
            if category != "All categories" and video["category"] != category:
                continue
            title = video["title"].casefold()
            if all(term in title for term in terms) or query == video["id"]:
                matches.append(video)
        if not matches and (terms or category != "All categories"):
            self.video_results.set(f"No matching videos · {len(videos):,} in this archive")
        else:
            self.video_results.set(f"{len(matches):,} of {len(videos):,} videos")
        self.fill(self.items, [(v["id"], (v["title"], v["category"], v["uploaded"] or "",
                                         v["state"], v["error"] or "")) for v in matches])
        for video in matches[:40]:
            self.thumbnail(video["thumbnail"],
                           lambda image, ident=video["id"]: self.items.item(ident, image=image))

    def setup_downloads(self):
        ttk.Label(self.downloads, text="Downloads", style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        self.buttons(self.downloads, [
            ("Pause / resume queue", self.toggle_pause),
            ("Retry selected", lambda: self.queue(self.jobs.selection())),
            ("Cancel selected", lambda: self.archive.cancel_items(self.jobs.selection())),
        ])
        self.buttons(self.downloads, [
            ("Verify local files", lambda: self.submit(self.archive.verify)),
            ("Open local folder", self.open_local),
        ])
        self.jobs = self.tree(self.downloads, (
            ("Title", 385), ("Category", 100), ("Status", 100), ("Message", 300)))

    def field(self, frame, label, var, values=None):
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=24).pack(side="left")
        if values:
            ttk.Combobox(row, textvariable=var, values=values, state="readonly", width=18).pack(side="left")
        else:
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        return row

    def setup_settings(self):
        ttk.Label(self.settings_page, text="Settings", style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        page = self.settings_page
        self.options = {}
        settings = self.archive.settings()
        for key, title, choices in (
            ("root", "Archive folder", None), ("mode", "Default download mode", MODES),
            ("quality", "Default quality", QUALITIES), ("schedule", "Scan frequency", SCHEDULES),
        ):
            var = self.options[key] = tk.StringVar(value=settings[key])
            row = self.field(page, title, var, choices)
            if key == "root":
                ttk.Button(row, text="Browse", command=self.browse).pack(side="right", padx=6)
        for key, title in [
            *((tab, f"Include {tab.lower()} by default") for tab in TABS),
            ("startup", "Scan at Windows sign-in"), ("background", "Scan while the window is closed"),
            ("notify", "Notify when a batch downloads new videos"),
            ("auto_update", "Check for app updates on launch"),
        ]:
            var = self.options[key] = tk.BooleanVar(value=settings[key])
            ttk.Checkbutton(page, text=title, variable=var).pack(anchor="w")
        self.buttons(page, [
            ("Save settings", self.save_settings), ("Open tools folder", self.open_tools),
            ("Update yt-dlp", self.update_tool),
        ])
        self.buttons(page, [("Check for app update", self.check_update)])
        self.tool_status = tk.StringVar()
        ttk.Label(page, textvariable=self.tool_status, wraplength=700).pack(anchor="w")
        self.buttons(page, [
            ("Get yt-dlp", lambda: webbrowser.open("https://github.com/yt-dlp/yt-dlp/releases/latest")),
            ("Get FFmpeg", lambda: webbrowser.open("https://ffmpeg.org/download.html")),
            ("Get Deno", lambda: webbrowser.open("https://deno.com/")),
            ("Refresh tools", self.refresh_tools),
        ])
        self.refresh_tools()

    def refresh_tools(self):
        missing = []
        for name in ("yt-dlp", "ffmpeg"):
            try:
                tool(name)
            except RuntimeError:
                missing.append(name)
        self.tool_status.set("Required tools available." if not missing else
                             "Missing " + " and ".join(missing) + ". Put the Windows executables in the tools folder or on PATH.")

    def browse(self):
        folder = filedialog.askdirectory(parent=self.root)
        if folder:
            self.options["root"].set(folder)

    def open_tools(self):
        folder = DATA / "tools"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def update_tool(self):
        def update():
            if self.archive.scanning or self.archive.progress or self.scan_batch:
                raise RuntimeError("Wait for active scans and downloads to finish.")
            from shutil import copy2
            folder = DATA / "tools"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "yt-dlp.exe"
            if not target.exists():
                copy2(tool("yt-dlp"), target)
            result = subprocess.run([str(target), "--ignore-config", "--no-plugin-dirs", "-U"],
                                    capture_output=True, creationflags=NO_WINDOW, timeout=120)
            if result.returncode:
                raise RuntimeError("yt-dlp could not update. Install the latest official executable in the tools folder.")
            return "yt-dlp update check finished."
        self.submit(update, callback=lambda message: (self.refresh_tools(), self.status.set(message)))

    def check_update(self, quiet=False):
        if self.update_running:
            return

        def install(path):
            self.update_installer = path
            self.close()

        def failed():
            self.update_running = False

        def show(update):
            self.update_running = False
            if update:
                tag = update[0]
                if messagebox.askyesno(
                    "App update",
                    f"{tag} is available. Download it and close Creator Archive to run the installer?"
                    "\n\nAny active download will be queued to resume when the app is reopened.",
                ):
                    self.update_running = True
                    self.status.set(f"Downloading Creator Archive {tag}…")
                    self.submit(lambda: download_installer(update), callback=install, on_error=failed)
            elif not quiet:
                messagebox.showinfo("App update", "You have the latest version.")

        self.update_running = True
        self.submit(find_update, callback=show, quiet=quiet, on_error=failed)

    def save_settings(self):
        values = {key: var.get() for key, var in self.options.items()}
        if not values["root"].strip() or not Path(values["root"]).is_absolute():
            messagebox.showerror("Settings", "Choose an absolute archive folder.")
            return
        def save():
            before = self.archive.settings()
            if (before["background"] or before["startup"]) != (values["background"] or values["startup"]):
                schedule_task(values["background"] or values["startup"])
            self.archive.save(values)
            return "Settings saved."
        self.submit(save, callback=self.status.set)

    def add_creator(self):
        dialog = tk.Toplevel(self.root, background=BG)
        dialog.title("Add creator")
        panel = ttk.Frame(dialog, padding=15)
        panel.pack(fill="both", expand=True)
        url, method = tk.StringVar(), tk.StringVar(value="Full Archive")
        self.field(panel, "Channel URL or @handle", url)
        self.field(panel, "Initial import", method, ("Full Archive", "Future Uploads Only"))
        def add():
            value, baseline = url.get(), method.get() == "Future Uploads Only"
            dialog.destroy()
            self.submit(lambda: self.archive.add(value, baseline), callback=lambda ident: self.scan([ident]))
        self.buttons(panel, [("Add and scan", add)])

    def open_creator(self, ident):
        if ident != self.current:
            self.video_search.set("")
        self.current = ident
        self.show_page(self.detail)
        self.refresh()

    def remove_creator(self, ident):
        if messagebox.askyesno("Remove creator", "Stop monitoring this creator? Archived files are kept."):
            self.archive.remove(ident)
            if self.current == ident:
                self.current = None
            self.refresh()

    def creator_settings(self):
        row = next((c for c in self.archive.creators() if c["id"] == self.current), None)
        if not row:
            return
        config = json.loads(row["config"])
        dialog = tk.Toplevel(self.root, background=BG)
        dialog.title(f"Settings · {row['name']}")
        panel = ttk.Frame(dialog, padding=15)
        panel.pack(fill="both", expand=True)
        variables = {}
        for key, label, values in (("mode", "Mode", MODES), ("quality", "Quality", QUALITIES),
                                   ("schedule", "Schedule", ("Global", *SCHEDULES))):
            var = variables[key] = tk.StringVar(value=config.get(key, values[0]))
            self.field(panel, label, var, values)
        for tab in TABS:
            var = variables[tab] = tk.BooleanVar(value=config[tab])
            ttk.Checkbutton(panel, text=tab, variable=var).pack(anchor="w")
        def save():
            self.archive.configure(row["id"], {key: var.get() for key, var in variables.items()})
            dialog.destroy()
        self.buttons(panel, [("Save", save)])

    def history(self):
        if not self.current:
            return
        with self.archive.db() as db:
            rows = db.execute("SELECT * FROM scans WHERE creator=? ORDER BY id DESC LIMIT 20",
                              (self.current,)).fetchall()
        messagebox.showinfo("Scan history", "\n".join(
            f"{local_scan(row['started'])}   {row['result'] or 'Running'}"
            + (f" ({row['count']} found)" if row["count"] is not None else "") for row in rows
        ) or "No scans yet.")

    def open_video(self):
        selected = self.items.selection()
        if selected:
            webbrowser.open("https://www.youtube.com/watch?v=" + selected[0])

    def open_local(self):
        selected = self.jobs.selection()
        if not selected:
            return
        with self.archive.db() as db:
            row = db.execute("SELECT path FROM videos WHERE id=?", (selected[0],)).fetchone()
        if row and row["path"] and Path(row["path"]).parent.is_dir():
            os.startfile(Path(row["path"]).parent)

    def toggle_pause(self):
        if self.archive.paused.is_set():
            self.archive.paused.clear()
            self.archive.save({"queue_paused": False})
            self.status.set("Queue resumed.")
        else:
            self.archive.paused.set()
            self.archive.save({"queue_paused": True})
            self.status.set("Queue paused after the active download.")

    def queue(self, selected):
        self.archive.queue(selected)

    def scan(self, ids):
        if not ids or self.scan_batch:
            return
        self.scan_batch = True
        def batch():
            errors = []
            for ident in ids:
                if self.archive.stop.is_set():
                    break
                try:
                    self.scan_current = ident
                    self.archive.scan(ident)
                except Exception as error:
                    errors.append(str(error))
                finally:
                    self.scan_current = None
            return errors
        def finished(errors):
            self.scan_batch = False
            self.status.set("Scan finished." if not errors else f"{len(errors)} scan(s) failed. See scan history.")
            if errors:
                messagebox.showerror("Scan", "\n".join(dict.fromkeys(errors)))
        self.submit(batch, callback=finished)

    def submit(self, fn, callback=None, quiet=False, on_error=None):
        def run():
            try:
                self.messages.put((callback, fn(), None))
            except Exception as error:
                if on_error:
                    self.messages.put((lambda _: on_error(), None, None))
                self.messages.put((None, None, None if quiet else str(error)))
        self.pool.submit(run)

    def run_downloads(self):
        while not self.archive.stop.is_set():
            try:
                if not self.archive.next_download():
                    self.archive.stop.wait(1)
            except Exception as error:
                self.messages.put((None, None, str(error)))
                self.archive.stop.wait(3)

    @staticmethod
    def fill(view, rows):
        old = set(view.get_children())
        for index, (ident, values) in enumerate(rows):
            if ident in old:
                old.remove(ident)
                if view.item(ident, "values") != tuple(str(value) for value in values):
                    view.item(ident, values=values)
                if view.index(ident) != index:
                    view.move(ident, "", index)
            else:
                view.insert("", index, iid=ident, values=values)
        if old:
            view.delete(*old)

    def thumbnail(self, url, display, avatar=False):
        if not isinstance(url, str) or not url:
            return
        key = (url, avatar)
        if key in self.thumbnail_cache:
            image = self.thumbnail_cache[key]
            if image:
                display(image)
            return
        if key in self.thumbnail_urls:
            return
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
            if parts.scheme != "https" or not (
                host == "i.ytimg.com" or host.endswith(".ytimg.com") or host.endswith(".ggpht.com")
                or host.endswith(".googleusercontent.com")
            ):
                return
            ffmpeg = tool("ffmpeg")
        except (RuntimeError, ValueError):
            return
        self.thumbnail_urls.add(key)
        size = max(34, round(38 * self.root.winfo_fpixels("1i") / 96))
        scale = "scale=50:30:force_original_aspect_ratio=decrease"
        if avatar:
            scale = (f"scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size},format=rgba,"
                     "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                     "a='if(lte(hypot(X-(W-1)/2,Y-(H-1)/2),W/2),255,0)'")
        def load():
            data = None
            try:
                with urlopen(url, timeout=8) as response:
                    source = response.read(2_000_000)
                result = subprocess.run(
                    [ffmpeg, "-v", "error", "-i", "pipe:0", "-vf",
                     scale, "-frames:v", "1",
                     "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                    input=source, capture_output=True, creationflags=NO_WINDOW, timeout=8)
                if result.returncode == 0:
                    data = base64.b64encode(result.stdout).decode("ascii")
            except (OSError, subprocess.TimeoutExpired, ValueError):
                pass
            self.messages.put((remember, data, None))

        def remember(data):
            self.thumbnail_urls.discard(key)
            try:
                self.thumbnail_cache[key] = tk.PhotoImage(data=data) if data else None
            except tk.TclError:
                self.thumbnail_cache[key] = None
        self.images.submit(load)

    def refresh(self):
        creators = self.archive.creators()
        self.refresh_creators(creators)
        if self.page == self.detail and self.current:
            creator = next((c for c in creators if c["id"] == self.current), None)
            if creator:
                self.identity.set(f"{creator['name']}   {creator['handle'] or ''}")
                self.refresh_videos()
        with self.archive.db() as db:
            jobs = db.execute("""SELECT id,title,category,state,error FROM videos WHERE state IN
                              ('Queued','Downloading','Archived','Failed','Cancelled','Missing')
                              ORDER BY CASE state WHEN 'Downloading' THEN 0 WHEN 'Queued' THEN 1 ELSE 2 END,
                              queue_time DESC LIMIT 1000""").fetchall()
            queued = db.execute("SELECT count(*) FROM videos WHERE state='Queued'").fetchone()[0]
        self.fill(self.jobs, [(v["id"], (v["title"], v["category"], v["state"], v["error"] or "")) for v in jobs])
        total = sum(c["archived"] for c in creators)
        busy = []
        if self.archive.scanning:
            busy.append(f"Scanning {self.archive.scanning}")
        if self.archive.progress:
            busy.append(self.archive.progress)
        if self.archive.paused.is_set():
            busy.append("Paused")
        if queued:
            busy.append(f"{queued} queued")
        if busy:
            self.status.set("   |   ".join(busy))
        elif self.last_busy:
            self.status.set("Ready")
            if self.archive.settings()["notify"] and total > self.archived_count:
                messagebox.showinfo("Creator Archive", f"Archived {total - self.archived_count} new videos.")
        if not busy:
            self.archived_count = total
        self.last_busy = bool(busy)

    last_busy = False

    def tick(self):
        if self.closing:
            return
        try:
            while True:
                callback, value, error = self.messages.get_nowait()
                if error:
                    self.status.set(error)
                    messagebox.showerror("Creator Archive", error)
                elif callback:
                    callback(value)
                    if self.closing:
                        return
        except Empty:
            pass
        self.refresh()
        if not self.scan_batch and time.monotonic() >= self.next_schedule_check:
            self.next_schedule_check = time.monotonic() + 900
            self.scan(list(self.archive.due()))
        self.root.after(1200, self.tick)

    def close(self):
        self.closing = True
        self.archive.stop.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.images.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    background = "--background" in sys.argv or "--startup" in sys.argv
    with (DATA / "engine.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            if not background:
                root = tk.Tk()
                root.withdraw()
                messagebox.showinfo("Creator Archive", "Creator Archive is already open or running a background scan.")
                root.destroy()
            return
        archive = Archive()
        if background:
            settings = archive.settings()
            startup = "--startup" in sys.argv
            if not (settings["startup"] if startup else settings["background"]):
                return
            archive.verify()
            for ident in archive.due(startup=startup):
                try:
                    archive.scan(ident)
                except Exception:
                    continue
            while archive.next_download():
                pass
        else:
            root = tk.Tk()
            app = App(root, archive)
            root.mainloop()
            app.pool.shutdown(wait=True, cancel_futures=True)
            app.worker.join()
    if not background and app.update_installer:
        try:
            subprocess.Popen([str(app.update_installer)], cwd=app.update_installer.parent, close_fds=True)
        except OSError as error:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("App update", f"Could not open the installer: {error}")
            root.destroy()


if __name__ == "__main__":
    main()
