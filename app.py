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
        self.next_schedule_check = 0
        self.archived_count = sum(c['archived'] for c in archive.creators())
        root.title(f"Creator Archive {VERSION}")
        root.geometry("1100x700")
        root.minsize(800, 530)
        root.configure(background="#111923")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background="#182330", foreground="#edf4fa")
        style.configure("Treeview", fieldbackground="#111923", rowheight=32)
        style.configure("Treeview.Heading", background="#24364a", foreground="#edf4fa")
        style.configure("TButton", padding=(10, 6), background="#293e55")
        style.configure("TEntry", fieldbackground="#111923", foreground="#edf4fa", insertcolor="white")
        style.configure("TCombobox", fieldbackground="#111923", foreground="#edf4fa")
        style.map("Treeview", background=[("selected", "#305d83")])
        style.map("TCombobox", fieldbackground=[("readonly", "#24364a")],
                  foreground=[("readonly", "#edf4fa")])
        style.map("TButton", background=[("active", "#385d7d")])
        self.status = tk.StringVar(value="Ready")
        ttk.Label(root, text="Creator Archive", font=("Segoe UI", 23, "bold"),
                  padding=(16, 10)).pack(fill="x")
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=10)
        self.dashboard, self.detail, self.downloads, self.settings_page = [
            ttk.Frame(self.tabs, padding=12) for _ in range(4)]
        for page, name in zip((self.dashboard, self.detail, self.downloads, self.settings_page),
                              ("Creators", "Creator details", "Downloads", "Settings")):
            self.tabs.add(page, text=name)
        ttk.Label(root, textvariable=self.status, padding=(12, 8)).pack(fill="x")
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

    def setup_dashboard(self):
        self.buttons(self.dashboard, [
            ("Add creator", self.add_creator), ("Scan selected", lambda: self.scan(self.creators.selection())),
            ("Scan All", lambda: self.scan([c["id"] for c in self.archive.creators()])),
            ("Open creator", self.open_creator), ("Remove", self.remove_creator),
        ])
        self.creators = self.tree(self.dashboard, (
            ("Creator", 250), ("Handle", 160), ("Archived", 80), ("Last successful scan", 225)), "browse", thumbnails=True)
        self.creators.bind("<Double-1>", lambda event: self.open_creator())

    def setup_detail(self):
        self.identity = tk.StringVar(value="Select a creator from the Creators page.")
        ttk.Label(self.detail, textvariable=self.identity, font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 10))
        row = self.buttons(self.detail, [
            ("Download selected", lambda: self.queue(self.items.selection())),
            ("Scan", lambda: self.scan([self.current] if self.current else [])),
            ("Creator settings", self.creator_settings), ("Scan history", self.history),
            ("Open on YouTube", self.open_video),
        ])
        self.category = tk.StringVar(value="All categories")
        ttk.Combobox(row, textvariable=self.category, values=("All categories", *TABS),
                     width=16, state="readonly").pack(side="right")
        self.items = self.tree(self.detail, (("Title", 345), ("Category", 110), ("Uploaded", 95),
                                             ("Status", 100), ("Message", 240)), thumbnails=True)

    def setup_downloads(self):
        self.buttons(self.downloads, [
            ("Pause / resume queue", self.toggle_pause),
            ("Retry selected", lambda: self.queue(self.jobs.selection())),
            ("Cancel selected", lambda: self.archive.cancel_items(self.jobs.selection())),
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
            ("Update yt-dlp", self.update_tool), ("Check for app update", self.check_update),
        ])
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
        dialog = tk.Toplevel(self.root)
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

    def open_creator(self):
        selected = self.creators.selection()
        if selected:
            self.current = selected[0]
            self.tabs.select(self.detail)
            self.refresh()

    def remove_creator(self):
        selected = self.creators.selection()
        if selected and messagebox.askyesno("Remove creator", "Stop monitoring this creator? Archived files are kept."):
            self.archive.remove(selected[0])
            if self.current == selected[0]:
                self.current = None
                self.identity.set("Select a creator from the Creators page.")

    def creator_settings(self):
        row = next((c for c in self.archive.creators() if c["id"] == self.current), None)
        if not row:
            return
        config = json.loads(row["config"])
        dialog = tk.Toplevel(self.root)
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
            f"{row['started']}   {row['result'] or 'Running'}"
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
                    self.archive.scan(ident)
                except Exception as error:
                    errors.append(str(error))
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

    def thumbnail(self, view, ident, url):
        if not isinstance(url, str) or not url:
            return
        if url in self.thumbnail_cache:
            image = self.thumbnail_cache[url]
            if image:
                view.item(ident, image=image)
            return
        if url in self.thumbnail_urls:
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
        self.thumbnail_urls.add(url)
        def load():
            data = None
            try:
                with urlopen(url, timeout=8) as response:
                    source = response.read(2_000_000)
                result = subprocess.run(
                    [ffmpeg, "-v", "error", "-i", "pipe:0", "-vf",
                     "scale=50:30:force_original_aspect_ratio=decrease", "-frames:v", "1",
                     "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                    input=source, capture_output=True, creationflags=NO_WINDOW, timeout=8)
                if result.returncode == 0:
                    data = base64.b64encode(result.stdout).decode("ascii")
            except (OSError, subprocess.TimeoutExpired, ValueError):
                pass
            self.messages.put((remember, data, None))

        def remember(data):
            self.thumbnail_urls.discard(url)
            try:
                self.thumbnail_cache[url] = tk.PhotoImage(data=data) if data else None
            except tk.TclError:
                self.thumbnail_cache[url] = None
        self.images.submit(load)

    def refresh(self):
        creators = self.archive.creators()
        self.fill(self.creators, [(c["id"], (c["name"], c["handle"] or "", c["archived"],
                                            (c["last_scan"] or "Never").replace("T", " "))) for c in creators])
        for creator in creators[:40]:
            self.thumbnail(self.creators, creator["id"], creator["thumbnail"])
        if self.current:
            creator = next((c for c in creators if c["id"] == self.current), None)
            if creator:
                self.identity.set(f"{creator['name']}   {creator['handle'] or ''}")
                category = self.category.get()
                videos = [v for v in self.archive.videos(self.current) if category in {"All categories", v["category"]}]
                self.fill(self.items, [(v["id"], (v["title"], v["category"], v["uploaded"] or "",
                                                 v["state"], v["error"] or ""))
                                       for v in videos])
                for v in videos[:40]:
                    self.thumbnail(self.items, v["id"], v["thumbnail"])
        else:
            self.fill(self.items, [])
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
