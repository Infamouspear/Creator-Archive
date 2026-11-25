# Creator Archive 0.1.1

Windows desktop app using Python 3.12, Tk/ttk and SQLite. The GUI submits work to a scan executor. Discovery returns a complete snapshot before a transaction applies baseline or queue rules. A separate worker claims persisted download jobs one at a time. The Windows scheduled task opens the same executable in headless mode. A per-user file lock prevents concurrent engines.

Build with `powershell -ExecutionPolicy Bypass -File build.ps1` from this directory. Install PyInstaller and Inno Setup 6 first. For a self-contained media workflow, place official yt-dlp.exe and ffmpeg.exe (and optionally ffprobe.exe and deno.exe) in tools before building, and include their required redistribution notices. Alternatively install the tools separately on PATH, or put them in the app's data tools folder. A supported JavaScript runtime may be required by yt-dlp for YouTube.

The application stores its database in %LOCALAPPDATA%\CreatorArchive. New downloads place each video and its metadata in a folder named for the YouTube video ID under the creator and category. A storage change applies to future downloads. Existing records retain absolute paths. The scheduler runs under the signed-in user. Uninstall removes the scheduled task and preserves the database and archive.

Before publishing, review the source and assets for personal information, confirm repository visibility and configure the exact owner/repo in release_repo.txt before building. Attach CreatorArchive-0.1.1-x64-Setup.exe to the v0.1.1 release. The application checks the latest stable release and opens its page for user-controlled installation. yt-dlp updates are separate.
