#!/usr/bin/env python3
"""Print GitHub Actions runner availability for the monitor workflow.

Writes shell-style key=value lines so the caller can append them to
$GITHUB_OUTPUT. Kept as a file (not an inline heredoc) because a heredoc inside
a YAML block scalar breaks the workflow syntax.

The India self-hosted runner is offline most of the time; a job pinned to it
silently never runs, which is why the monitor had hours of missing data. The
workflow uses linux_labels to fall back to a GitHub-hosted runner.
"""
from __future__ import annotations

import json
import os
import urllib.request


def main() -> int:
    repo = os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        print("::warning::GH_TOKEN/REPO missing - falling back to GitHub-hosted runners")
        print("windows_online=false")
        print("linux_online=false")
        print('linux_labels=["ubuntu-latest"]')
        return 0

    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/runners?per_page=100",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        runners = json.load(urllib.request.urlopen(req, timeout=20)).get("runners", [])
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::could not list runners ({type(exc).__name__}) - using GitHub-hosted")
        print("windows_online=false")
        print("linux_online=false")
        print('linux_labels=["ubuntu-latest"]')
        return 0

    def online(pred) -> bool:
        return any(r.get("status") == "online" and pred(r) for r in runners)

    windows = online(lambda r: r.get("name") == "gajab-windows")
    linux = online(lambda r: "Linux" in [lbl.get("name") for lbl in r.get("labels", [])])

    print(f"windows_online={'true' if windows else 'false'}")
    print(f"linux_online={'true' if linux else 'false'}")
    labels = ["self-hosted", "Linux"] if linux else ["ubuntu-latest"]
    print("linux_labels=" + json.dumps(labels))
    print(f"::notice::windows_online={windows} linux_online={linux}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
