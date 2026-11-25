import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

VERSION = "0.1.1"
DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CreatorArchive"
DEFAULTS = {
    "root": str(Path.home() / "Videos" / "Creator Archive"),
    "mode": "Automatic", "quality": "Best", "schedule": "Manual",
    "Videos": True, "Shorts": True, "Livestreams": True,
    "startup": False, "background": False, "notify": False,
    "auto_update": False, "release_repo": "",
}
TABS = {"Videos": "videos", "Shorts": "shorts", "Livestreams": "streams"}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def channel_url(value):
    value = value.strip()
    if value.startswith("@"):
        value = "https://www.youtube.com/" + value
    elif "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    path = parts.path.rstrip("/")
    if parts.scheme not in {"http", "https"} or parts.netloc.lower() not in {
        "youtube.com", "www.youtube.com", "m.youtube.com"
    }:
        raise ValueError("Enter a YouTube channel URL or @handle.")
    path = re.sub(r"/(?:videos|shorts|streams|featured)$", "", path)
    if not (re.fullmatch(r"/@[^/\\\s]+", path) or
            re.fullmatch(r"/(?:channel|c|user)/[\w.-]+", path)):
        raise ValueError("Enter a YouTube channel URL or @handle.")
    return "https://www.youtube.com" + path


def safe_name(value, limit=75):
    value = re.sub(r'[<>:"/\\|?*%\x00-\x1f]', "_", value)[:limit].strip(" .")
    return value or "Untitled"


def tool(name):
    bundled = Path(__file__).resolve().parent / "tools" / (name + ".exe")
    local = DATA / "tools" / (name + ".exe")
    for candidate in (local, bundled):
        if candidate.is_file():
            return str(candidate)
    installed = shutil.which(name)
    if installed:
        return installed
    raise RuntimeError(f"{name} is missing. See Settings for the tools folder.")


def ytdlp():
    args = [tool("yt-dlp"), "--ignore-config", "--no-plugin-dirs", "--encoding", "utf-8"]
    try:
        args += ["--js-runtimes", "deno:" + tool("deno")]
    except RuntimeError:
        pass
    return args


def friendly(message):
    lower = message.lower()
    if "private" in lower:
        return "This video or channel is private."
    if "sign in" in lower or "members-only" in lower or "age-restricted" in lower:
        return "YouTube requires sign-in or additional access for this content."
    if "unavailable" in lower or "removed" in lower:
        return "This content is unavailable on YouTube."
    if "network" in lower or "resolve" in lower or "timed out" in lower:
        return "Could not reach YouTube. Check your connection and retry."
    return "yt-dlp could not process this content. Check for a tool update and retry."


def flat(url, first=False):
    args = ytdlp() + ["--flat-playlist", "--dump-single-json", "--no-warnings"]
    if first:
        args += ["--playlist-items", "1"]
    result = subprocess.run(args + ["--", url], capture_output=True, encoding="utf-8",
                            errors="replace", timeout=600, creationflags=NO_WINDOW)
    if result.returncode:
        error = result.stderr.lower()
        if any(f"does not have a {tab} tab" in error for tab in ("videos", "shorts", "streams", "live")):
            return {}
        raise RuntimeError(friendly(result.stderr))
    return json.loads(result.stdout)


SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, handle TEXT, thumbnail TEXT,
 active INTEGER NOT NULL DEFAULT 1, baseline TEXT, last_scan TEXT, config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS videos (
 id TEXT PRIMARY KEY, creator TEXT NOT NULL REFERENCES creators(id), title TEXT NOT NULL,
 category TEXT NOT NULL, uploaded TEXT, thumbnail TEXT, discovered TEXT NOT NULL,
 state TEXT NOT NULL, path TEXT, completed TEXT, metadata TEXT, error TEXT,
 queue_time TEXT, file_hash TEXT
);
CREATE TABLE IF NOT EXISTS scans (
 id INTEGER PRIMARY KEY, creator TEXT NOT NULL REFERENCES creators(id),
 started TEXT NOT NULL, finished TEXT, result TEXT, count INTEGER
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS videos_creator ON videos(creator, category);
CREATE INDEX IF NOT EXISTS videos_queue ON videos(state, queue_time);
"""


class Archive:
    def __init__(self, directory=DATA):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.database = directory / "archive.sqlite3"
        with self.db() as db:
            db.executescript(SCHEMA)
            db.execute("UPDATE videos SET state='Queued' WHERE state='Downloading'")
            db.execute("UPDATE scans SET finished=?, result='Interrupted' WHERE finished IS NULL", (now(),))
        self.scan_lock = threading.Lock()
        self.queue_lock = threading.Lock()
        self.paused = threading.Event()
        if self.settings().get("queue_paused"):
            self.paused.set()
        self.stop = threading.Event()
        self.cancel = threading.Event()
        self.progress = ""
        self.scanning = ""

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self.db() as db:
            return DEFAULTS | {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM settings")}

    def save(self, changes):
        with self.db() as db:
            db.executemany("INSERT OR REPLACE INTO settings VALUES (?,?)",
                           [(key, json.dumps(value)) for key, value in changes.items()])

    def creators(self):
        with self.db() as db:
            return db.execute("""SELECT c.*, (SELECT count(*) FROM videos v
                WHERE v.creator=c.id AND v.state='Archived') AS archived
                FROM creators c WHERE active=1 ORDER BY name COLLATE NOCASE""").fetchall()

    def videos(self, creator=None):
        with self.db() as db:
            if creator:
                return db.execute("SELECT * FROM videos WHERE creator=? ORDER BY uploaded DESC, discovered DESC",
                                  (creator,)).fetchall()
            return db.execute("SELECT * FROM videos ORDER BY queue_time DESC, discovered DESC").fetchall()

    def add(self, value, baseline):
        url = channel_url(value)
        info = flat(url, first=True)
        first = next((x for x in info.get("entries", []) if x), {})
        ident = info.get("channel_id") or info.get("id") or first.get("channel_id")
        if not CHANNEL_ID.fullmatch(ident or ""):
            raise RuntimeError("YouTube did not return a stable channel ID.")
        name = info.get("channel") or info.get("uploader") or info.get("title") or ident
        handle = info.get("uploader_id") or (url.rsplit("/", 1)[-1] if "/@" in url else "")
        thumbs = info.get("thumbnails") or []
        settings = self.settings()
        config = {key: settings[key] for key in ("mode", "quality", *TABS)}
        config["schedule"] = "Global"
        with self.db() as db:
            db.execute("""INSERT INTO creators(id,name,handle,thumbnail,baseline,config)
                        VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                        active=1,name=excluded.name,handle=excluded.handle,thumbnail=excluded.thumbnail""",
                       (ident, name, handle, thumbs[-1].get("url") if thumbs else None,
                        "Future" if baseline else "Full", json.dumps(config)))
        return ident

    def remove(self, creator):
        with self.db() as db:
            db.execute("UPDATE creators SET active=0 WHERE id=?", (creator,))
            db.execute("UPDATE videos SET state='Pending' WHERE creator=? AND state='Queued'", (creator,))

    def configure(self, creator, config):
        with self.db() as db:
            db.execute("UPDATE creators SET config=? WHERE id=?", (json.dumps(config), creator))

    def verify(self):
        with self.db() as db:
            rows = db.execute("SELECT id,path FROM videos WHERE state='Archived'").fetchall()
            for row in rows:
                if not row["path"]:
                    exists = False
                else:
                    try:
                        exists = Path(row["path"]).stat().st_size > 0
                    except FileNotFoundError:
                        exists = False
                    except OSError:
                        continue
                if not exists:
                    db.execute("UPDATE videos SET state='Missing' WHERE id=? AND state='Archived'", (row["id"],))

    def scan(self, creator_id):
        with self.scan_lock:
            with self.db() as db:
                creator = db.execute("SELECT * FROM creators WHERE id=? AND active=1", (creator_id,)).fetchone()
                if creator is None:
                    return
                scan_id = db.execute("INSERT INTO scans(creator,started) VALUES(?,?)",
                                     (creator_id, now())).lastrowid
            self.scanning = creator["name"]
            try:
                config = json.loads(creator["config"])
                found = {}
                for category in ("Shorts", "Livestreams", "Videos"):
                    if self.stop.is_set():
                        raise RuntimeError("Scan interrupted.")
                    if not config[category] and creator["baseline"] != "Future":
                        continue
                    info = flat(f"https://www.youtube.com/channel/{creator_id}/{TABS[category]}")
                    for entry in info.get("entries") or []:
                        if not entry or not VIDEO_ID.fullmatch(entry.get("id") or ""):
                            continue
                        found.setdefault(entry["id"], (category, entry))
                self.verify()
                stamp = now()
                with self.db() as db:
                    current = db.execute("SELECT * FROM creators WHERE id=?", (creator_id,)).fetchone()
                    if not current["active"] or self.stop.is_set():
                        raise RuntimeError("Scan interrupted.")
                    config = json.loads(current["config"])
                    for ident, (category, entry) in found.items():
                        live = entry.get("live_status") in {"is_live", "is_upcoming", "post_live"}
                        availability = entry.get("availability")
                        restricted = availability in {"private", "premium_only", "subscriber_only", "needs_auth"}
                        enabled = config[category]
                        if current["baseline"] == "Future":
                            state = "Baseline"
                        elif live or restricted:
                            state = "Unavailable"
                        elif enabled and config["mode"] == "Automatic":
                            state = "Queued"
                        else:
                            state = "Pending"
                        thumbs = entry.get("thumbnails") or []
                        db.execute("""INSERT INTO videos
                           (id,creator,title,category,uploaded,thumbnail,discovered,state,queue_time,metadata)
                           VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                           title=excluded.title, uploaded=coalesce(excluded.uploaded,videos.uploaded),
                           thumbnail=coalesce(excluded.thumbnail,videos.thumbnail),
                           state=CASE WHEN videos.state='Unavailable' THEN excluded.state ELSE videos.state END,
                           queue_time=CASE WHEN videos.state='Unavailable' THEN excluded.queue_time
                                           ELSE videos.queue_time END""",
                                   (ident, creator_id, entry.get("title") or ident, category, entry.get("upload_date"),
                                    thumbs[-1].get("url") if thumbs else entry.get("thumbnail"), stamp, state,
                                    stamp if state == "Queued" else None, json.dumps({"availability": availability})))
                    db.execute("UPDATE creators SET last_scan=?, baseline=NULL WHERE id=?", (stamp, creator_id))
                    db.execute("UPDATE scans SET finished=?, result='Success', count=? WHERE id=?",
                               (stamp, len(found), scan_id))
            except Exception as error:
                with self.db() as db:
                    db.execute("UPDATE scans SET finished=?,result=? WHERE id=?",
                               (now(), str(error), scan_id))
                raise
            finally:
                self.scanning = ""

    def queue(self, ids):
        with self.db() as db:
            db.executemany("""UPDATE videos SET state='Queued',queue_time=?,error=NULL WHERE id=?
                           AND state IN ('Pending','Baseline','Failed','Missing','Cancelled')""",
                           [(now(), ident) for ident in ids])

    def cancel_items(self, ids):
        with self.queue_lock, self.db() as db:
            for ident in ids:
                db.execute("UPDATE videos SET state='Cancelled' WHERE id=? AND state='Queued'", (ident,))
                if db.execute("SELECT 1 FROM videos WHERE id=? AND state='Downloading'", (ident,)).fetchone():
                    self.cancel.set()

    def next_download(self):
        with self.queue_lock, self.db() as db:
            if self.paused.is_set() or self.stop.is_set():
                return False
            item = db.execute("""SELECT v.*, c.name, c.config FROM videos v JOIN creators c
                ON c.id=v.creator WHERE v.state='Queued' ORDER BY v.queue_time,v.id LIMIT 1""").fetchone()
            if item is None:
                return False
            self.cancel.clear()
            db.execute("UPDATE videos SET state='Downloading' WHERE id=?", (item["id"],))
        self.progress = f"Starting {item['title']}"
        try:
            path, metadata = self.download(item)
            with self.db() as db:
                db.execute("""UPDATE videos SET state='Archived',path=?,completed=?,metadata=?,error=NULL,
                           uploaded=coalesce(?,uploaded) WHERE id=?""",
                           (str(path), now(), json.dumps(metadata), metadata.get("upload_date"), item["id"]))
        except Exception as error:
            state = "Queued" if self.stop.is_set() else "Cancelled" if self.cancel.is_set() else "Failed"
            with self.db() as db:
                db.execute("UPDATE videos SET state=?,error=? WHERE id=?", (state, str(error), item["id"]))
        finally:
            self.progress = ""
        return True

    def download(self, item):
        ident = item["id"]
        settings = self.settings()
        with self.db() as db:
            folder_key = "folder:" + item["creator"]
            stored = db.execute("SELECT value FROM settings WHERE key=?", (folder_key,)).fetchone()
            folder = json.loads(stored["value"]) if stored else safe_name(item["name"], 45) + " [" + item["creator"] + "]"
            if not stored:
                db.execute("INSERT INTO settings VALUES (?,?)", (folder_key, json.dumps(folder)))
        target = Path(settings["root"]).resolve() / folder / item["category"] / ident
        target.mkdir(parents=True, exist_ok=True)
        prefix = safe_name(item["title"]) + " [" + ident + "]"
        info_path = target / (prefix + ".info.json")
        path_log = target / (prefix + ".path.tmp")
        path_log.unlink(missing_ok=True)
        quality = json.loads(item["config"]).get("quality", settings["quality"])
        selector = "bv*+ba/b" if quality == "Best" else f"bv*[height<={quality}]+ba/b[height<={quality}]"
        args = ytdlp() + [
            "--ffmpeg-location", tool("ffmpeg"), "--no-playlist", "--no-overwrites", "--windows-filenames",
            "--socket-timeout", "30", "--newline", "--progress", "--progress-template",
            "download:PROGRESS %(progress._percent_str)s · %(progress._speed_str)s",
            "--match-filters", "!is_live & live_status !=? is_upcoming & live_status !=? post_live",
            "--write-info-json", "--write-description", "--write-thumbnail", "-f", selector,
            "-P", str(target), "-o", prefix + ".%(ext)s",
            "--print-to-file", "after_move:filepath", str(path_log),
            "--", "https://www.youtube.com/watch?v=" + ident,
        ]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        errors = []
        def read_output():
            for line in process.stdout:
                line = line.strip()
                if line.startswith("PROGRESS "):
                    self.progress = item["title"] + " · " + line.removeprefix("PROGRESS ")
                elif line.startswith("ERROR:"):
                    errors.append(line)
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        while process.poll() is None:
            if self.stop.wait(0.2) or self.cancel.is_set():
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, creationflags=NO_WINDOW)
                if process.poll() is None:
                    process.kill()
                process.wait()
                raise RuntimeError("Download interrupted.")
        reader.join(timeout=2)
        if process.returncode:
            raise RuntimeError(friendly(" ".join(errors)))
        if not path_log.is_file():
            raise RuntimeError("No downloadable media was returned. Ongoing and upcoming livestreams are skipped.")
        lines = path_log.read_text(encoding="utf-8").splitlines()
        path_log.unlink(missing_ok=True)
        path = Path(lines[-1]).resolve() if lines else target
        if path.parent != target.resolve() or not path.is_file() or not path.stat().st_size:
            raise RuntimeError("The media file could not be verified.")
        metadata = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
        return path, {key: metadata[key] for key in (
            "title", "upload_date", "duration", "width", "height", "fps", "vcodec", "acodec", "ext"
        ) if key in metadata}

    def due(self, startup=False):
        settings = self.settings()
        for creator in self.creators():
            schedule = json.loads(creator["config"]).get("schedule", "Global")
            if schedule == "Global":
                schedule = settings["schedule"]
            interval = {"Daily": 86400, "Weekly": 604800}.get(schedule)
            last = creator["last_scan"]
            if (startup and settings["startup"]) or (interval and (
                not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() >= interval
            )):
                yield creator["id"]
