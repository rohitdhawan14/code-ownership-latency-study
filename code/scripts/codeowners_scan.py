#!/usr/bin/env python3
"""
codeowners_scan.py
Scan a list of GitHub repos for CODEOWNERS presence, adoption date, and owners count.

Input CSV (default: active_repos.csv) must contain a 'repo_name' column (e.g., owner/repo).
Output CSV (default: codeowners_meta.csv) columns:
  repo_name,has_codeowners,codeowners_created_at,owners_count

Env:
  GITHUB_TOKEN  Personal access token with at least public_repo scope.

Usage examples:
  python codeowners_scan.py active_repos.csv codeowners_meta.csv
  python codeowners_scan.py active_repos_top2000.csv codeowners_meta.csv --limit 2000
"""

import os
import sys
import time
import base64
import re
import csv
import argparse
from datetime import timezone
from dateutil import parser as dtparse
import requests
import pandas as pd
from tqdm import tqdm

# ---------- Config ----------
DEFAULT_IN  = "active_repos.csv"
DEFAULT_OUT = "codeowners_meta.csv"
CODEOWNERS_PATHS = ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"]
HANDLE_RE = re.compile(r'@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:/[A-Za-z0-9_.-]+)?)')
TIMEOUT_S = 30
RETRIES   = 3
BACKOFF_S = 2

# ---------- Session / Auth ----------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    print("ERROR: Set GITHUB_TOKEN environment variable (classic token with public_repo).", file=sys.stderr)
    sys.exit(1)

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "codeowners-scan-msr2026/1.1"
})

# ---------- HTTP helpers ----------
def gh_get(url, params=None, ok404=False, retries=RETRIES):
    """GET with primary/secondary rate-limit handling + retries/backoff."""
    backoff = BACKOFF_S
    for attempt in range(retries + 1):
        try:
            r = SESSION.get(url, params=params, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            if attempt < retries:
                time.sleep(backoff); backoff *= 2; continue
            raise

        # Primary rate limit
        if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(r.headers.get("X-RateLimit-Reset", "0") or 0)
            sleep_s = max(0, reset - int(time.time()) + 2)
            print(f"[rate-limit] Primary limit hit. Sleeping {sleep_s}s …", file=sys.stderr)
            time.sleep(sleep_s)
            continue

        # Secondary/abuse or transient throttling
        if r.status_code in (403, 429) and r.headers.get("Retry-After"):
            try:
                retry_after = int(r.headers["Retry-After"])
            except ValueError:
                retry_after = backoff
            print(f"[rate-limit] Secondary limit. Retry-After {retry_after}s …", file=sys.stderr)
            time.sleep(retry_after)
            continue

        # Transient server errors
        if r.status_code in (500, 502, 503, 504):
            if attempt < retries:
                time.sleep(backoff); backoff *= 2; continue

        if ok404 and r.status_code == 404:
            return None

        # Raise for other errors
        try:
            r.raise_for_status()
        except requests.HTTPError:
            if attempt < retries:
                time.sleep(backoff); backoff *= 2; continue
            raise
        return r

    raise RuntimeError(f"Failed after retries: {url}")

# ---------- CODEOWNERS logic ----------
def find_codeowners_location(repo_full):
    """Return (path, content_base64) if found, else (None, None)."""
    owner, repo = repo_full.split("/", 1)
    for path in CODEOWNERS_PATHS:
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        r = gh_get(url, ok404=True)
        if r is None:
            continue
        j = r.json()
        if isinstance(j, dict) and j.get("type") == "file":
            return path, j.get("content", "")
    return None, None

def earliest_commit_date_for_path(repo_full, path):
    """
    Find the oldest commit date touching 'path' in repo.
    Uses per_page=1 and Link rel=last to jump to oldest.
    Returns timezone-aware datetime or None.
    """
    owner, repo = repo_full.split("/", 1)
    base = f"https://api.github.com/repos/{owner}/{repo}/commits"
    params = {"path": path, "per_page": 1}
    r = gh_get(base, params=params)
    # If path never changed in git history, r.json() may be empty
    if r.status_code == 200 and not r.json():
        return None

    link = r.headers.get("Link", "")
    last_url = None
    if link:
        for part in link.split(","):
            segs = [s.strip() for s in part.split(";")]
            if len(segs) >= 2 and segs[1] == 'rel="last"':
                last_url = segs[0].lstrip("<").rstrip(">")
                break

    if last_url:
        commits = gh_get(last_url).json()
    else:
        commits = r.json()

    if not commits:
        return None

    oldest = commits[-1]  # with per_page=1 on last page, that's the oldest
    date_str = oldest["commit"]["author"]["date"]
    return dtparse.parse(date_str).astimezone(timezone.utc)

def count_unique_owners_from_content_b64(content_b64):
    """Count distinct @handles in CODEOWNERS file content."""
    if not content_b64:
        return 0
    try:
        text = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
    except Exception:
        return 0
    owners = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for m in HANDLE_RE.findall(line):
            owners.add(m.lower())
    return len(owners)

# ---------- Main ----------
def scan_repo(repo):
    """Return dict with scan results for a single repo."""
    try:
        path, content_b64 = find_codeowners_location(repo)
        has = path is not None
        owners_count = count_unique_owners_from_content_b64(content_b64) if has else 0
        created_at_iso = ""
        if has:
            dt = earliest_commit_date_for_path(repo, path)
            created_at_iso = dt.isoformat() if dt else ""
        return {
            "repo_name": repo,
            "has_codeowners": bool(has),
            "codeowners_created_at": created_at_iso,
            "owners_count": int(owners_count)
        }
    except Exception as e:
        # Be resilient: return a safe default row
        return {
            "repo_name": repo,
            "has_codeowners": False,
            "codeowners_created_at": "",
            "owners_count": 0
        }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_csv", nargs="?", default=DEFAULT_IN, help="Input CSV with repo_name column")
    ap.add_argument("output_csv", nargs="?", default=DEFAULT_OUT, help="Output CSV path")
    ap.add_argument("--limit", type=int, default=None, help="Process only the first N repos")
    args = ap.parse_args()

    # Load repos
    df = pd.read_csv(args.input_csv)
    if "repo_name" not in df.columns:
        raise ValueError("Input CSV must have a 'repo_name' column (e.g., owner/repo).")

    repos = df["repo_name"].astype(str).tolist()
    if args.limit:
        repos = repos[: args.limit]

    # Prepare writer (incremental flush to avoid losing progress)
    out_exists = os.path.exists(args.output_csv)
    out_file = open(args.output_csv, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(out_file,
                            fieldnames=["repo_name","has_codeowners","codeowners_created_at","owners_count"])
    if not out_exists:
        writer.writeheader()

    # Track already written repos to support resume/appends
    written = set()
    if out_exists:
        try:
            prev = pd.read_csv(args.output_csv)
            if "repo_name" in prev.columns:
                written = set(prev["repo_name"].astype(str).tolist())
        except Exception:
            pass

    # Scan
    pbar = tqdm(repos, desc="Scanning repos")
    n = 0
    for repo in pbar:
        if repo in written:
            continue
        row = scan_repo(repo)
        writer.writerow(row)
        n += 1
        if n % 100 == 0:
            out_file.flush()

    out_file.flush()
    out_file.close()
    print(f"✅ Done. Wrote/updated: {args.output_csv}")

if __name__ == "__main__":
    main()
