#!/usr/bin/env python3
"""Preserve branch tip commits under archive tags before deleting old branch refs."""
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

CONFIG = Path(".github/maintenance/stale-branches-2026-11.json")
API = "https://api.github.com/repos/"

def call(repo, path, method="GET", payload=None, missing=False):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Power-Projects-Branch-Cleanup",
               "X-GitHub-Api-Version": "2022-11-28",
               "Authorization": "Bearer " + os.environ["GH_TOKEN"]}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(API + repo + ("/" + path if path else ""), data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return None if response.status == 204 else json.load(response)
    except HTTPError as error:
        if missing and error.code == 404:
            return None
        raise

def run():
    cfg = json.loads(CONFIG.read_text())
    repo = cfg["repository"]
    assert repo == os.environ["GITHUB_REPOSITORY"]
    assert cfg["confirm"] == "ARCHIVE_STALE_BRANCHES_2026_11"
    assert call(repo, "")["default_branch"] == "main"
    targets = cfg["branches"]
    names = [b["name"] for b in targets]
    assert len(names) == len(set(names)) and 0 < len(names) < 100
    assert all(n != "main" and not n.startswith("archive/") for n in names)
    assert all(re.fullmatch(r"[0-9a-f]{40}", b["sha"]) for b in targets)
    open_heads = set()
    for page in range(1, 21):
        prs = call(repo, f"pulls?state=open&per_page=100&page={page}")
        open_heads.update(p["head"]["ref"] for p in prs
                          if p["head"]["repo"] and p["head"]["repo"]["full_name"] == repo)
        if len(prs) < 100:
            break
    else:
        raise RuntimeError("Open PR pagination exceeded limit")
    assert not open_heads.intersection(names), "Would delete an open pull request branch"
    latest = call(repo, "releases/latest")["tag_name"]
    planned = []
    for b in targets:
        name, sha = b["name"], b["sha"]
        tag = "archive/branch-cleanup-2026-11/" + name + "/" + sha[:12]
        current = call(repo, "branches/" + quote(name, safe=""), missing=True)
        archived = call(repo, "git/ref/tags/" + quote(tag, safe=""), missing=True)
        if archived:
            assert archived["object"]["type"] == "commit" and archived["object"]["sha"] == sha
        if current is None:
            assert archived, "Branch vanished without archive: " + name
            continue
        assert not current["protected"] and current["commit"]["sha"] == sha, "Branch changed: " + name
        planned.append((name, sha, tag, bool(archived)))

    # First save every branch head. Deletion cannot begin if tagging fails.
    for name, sha, tag, already in planned:
        if not already:
            call(repo, "git/refs", "POST", {"ref": "refs/tags/" + tag, "sha": sha})
        saved = call(repo, "git/ref/tags/" + quote(tag, safe=""))
        assert saved["object"]["type"] == "commit" and saved["object"]["sha"] == sha
        print("PRESERVED", name, sha, "as", tag)

    assert os.environ.get("ARCHIVE_APPLY") == "true", "Preview mode: no deletions"
    deleted = []
    for name, sha, tag, _ in planned:
        branch = call(repo, "git/ref/heads/" + quote(name, safe=""))
        assert branch["object"]["sha"] == sha, "Branch moved since archiving: " + name
        assert call(repo, "")["default_branch"] != name
        current_prs = call(repo, "pulls?state=open&per_page=100")
        assert not any(p["head"]["ref"] == name and p["head"]["repo"]
                       and p["head"]["repo"]["full_name"] == repo for p in current_prs)
        call(repo, "git/refs/heads/" + quote(name, safe=""), "DELETE")
        deleted.append(name)
        print("REMOVED", name)
    assert call(repo, "releases/latest")["tag_name"] == latest
    print("Archived:", len(planned), "Removed:", len(deleted))
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(path).write_text("# Branch cleanup: " + repo + "\n\nRemoved " + str(len(deleted))
            + " old branch refs, after preserving each exact SHA under an archive tag.\n")

if __name__ == "__main__":
    run()
