# Creator Archive 0.1.4

Windows desktop app using Python 3.12, Tk/ttk and SQLite. The GUI submits work to a scan executor. Discovery returns a complete snapshot before a transaction applies baseline or queue rules. A separate worker claims persisted download jobs one at a time. The Windows scheduled task opens the same executable in headless mode. A per-user file lock prevents concurrent engines.

Build with `powershell -ExecutionPolicy Bypass -File build.ps1` from this directory. Install PyInstaller and Inno Setup 6 first. For a self-contained media workflow, place official yt-dlp.exe and ffmpeg.exe (and optionally ffprobe.exe and deno.exe) in tools before building, and include their required redistribution notices. Alternatively install the tools separately on PATH, or put them in the app's data tools folder. A supported JavaScript runtime may be required by yt-dlp for YouTube.

The application stores its database in %LOCALAPPDATA%\CreatorArchive. Each new download stores the video and its metadata under the creator and category in a folder named `Video title [YouTube ID]`. On launch, recognized ID-only folders for archived videos are renamed in place and their recorded paths are updated. A storage change applies to future downloads. The scheduler runs under the signed-in user. Uninstall removes the scheduled task and preserves the database and archive.

Settings → Check for app update looks for newer GitHub releases, including published previews. With your approval, it downloads the Windows x64 installer, verifies its size and GitHub SHA-256 digest, closes Creator Archive, and opens Windows Setup. For the private repository, install Git for Windows with Git Credential Manager and sign in as Infamouspear. Public releases can be downloaded without a GitHub login.

Before publishing, review the source and assets for personal information and confirm the repository visibility. Attach the matching Windows x64 installer to each versioned GitHub release. yt-dlp updates are separate.
