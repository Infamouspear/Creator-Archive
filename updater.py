import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from engine import DATA, NO_WINDOW, VERSION

REPO = "Infamouspear/Creator-Archive"
API = f"https://api.github.com/repos/{REPO}"
VERSION_TAG = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


class GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        destination = urlsplit(newurl)
        if destination.scheme != "https" or destination.hostname != "release-assets.githubusercontent.com":
            raise URLError("Unexpected GitHub download redirect.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def github_get(url, accept, token=None):
    request = Request(url, headers={"User-Agent": "Creator-Archive", "Accept": accept})
    if token:
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    return build_opener(GitHubRedirect()).open(request, timeout=20)


def saved_token():
    owner = REPO.split("/")[0]
    try:
        result = subprocess.run(
            ["git", "-c", f"credential.username={owner}", "credential", "fill"],
            input=f"protocol=https\nhost=github.com\nusername={owner}\n\n",
            capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("password="):
                return line.removeprefix("password=")
    return None


def releases():
    url = f"{API}/releases?per_page=100"
    try:
        with github_get(url, "application/vnd.github+json") as response:
            return json.load(response), False
    except HTTPError as error:
        if error.code not in (401, 403, 404):
            raise RuntimeError(f"GitHub update check failed (HTTP {error.code}).") from None
    token = saved_token()
    if token:
        try:
            with github_get(url, "application/vnd.github+json", token) as response:
                return json.load(response), True
        except HTTPError as error:
            if error.code not in (401, 403, 404):
                raise RuntimeError(f"GitHub update check failed (HTTP {error.code}).") from None
    raise RuntimeError("GitHub release access is unavailable. For the private repository, sign in to Git Credential Manager as Infamouspear and try again.")


def find_update():
    current = tuple(map(int, VERSION.split(".")))
    candidates = []
    entries, private = releases()
    for release in entries:
        match = VERSION_TAG.fullmatch(release.get("tag_name", ""))
        if release.get("draft") or not match:
            continue
        version = tuple(map(int, match.groups()))
        if version <= current:
            continue
        name = f"CreatorArchive-{'.'.join(map(str, version))}-x64-Setup.exe"
        for asset in release.get("assets", []):
            digest = asset.get("digest") or ""
            if (asset.get("name") == name and asset.get("state") == "uploaded"
                    and type(asset.get("id")) is int and type(asset.get("size")) is int
                    and asset["size"] > 0 and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)):
                candidates.append((version, release["tag_name"], asset, private))
    return max(candidates, key=lambda item: item[0])[1:] if candidates else None


def download_installer(update):
    tag, asset, private = update
    if private:
        token = saved_token()
        if not token:
            raise RuntimeError("Sign in to Git Credential Manager to download this private release.")
        response = github_get(f"{API}/releases/assets/{asset['id']}", "application/octet-stream", token)
    else:
        public_url = f"https://github.com/{REPO}/releases/download/{quote(tag, safe='')}/{asset['name']}"
        response = github_get(public_url, "application/octet-stream")

    folder = DATA / "updates"
    with response:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder) as temporary:
            partial = Path(temporary) / asset["name"]
            digest = hashlib.sha256()
            size = 0
            with partial.open("wb") as output:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > asset["size"]:
                        raise RuntimeError("The installer exceeded its expected download size.")
                    digest.update(chunk)
                    output.write(chunk)
            if size != asset["size"] or f"sha256:{digest.hexdigest()}" != asset["digest"].lower():
                raise RuntimeError("The installer failed its integrity check.")
            installer = folder / asset["name"]
            partial.replace(installer)
    return installer
