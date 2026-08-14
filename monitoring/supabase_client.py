from __future__ import annotations
import os
import urllib.request
from urllib.error import HTTPError
from datetime import datetime, timezone
from pathlib import Path
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY


class SupabaseStore:
    def __init__(self):
        self._run_at = datetime.now(timezone.utc).isoformat()
        # Shared run id so split jobs (india + browser) group as one run in the dashboard.
        self._run_id = os.environ.get("MONITOR_RUN_ID") or self._run_at
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("[SUPABASE] Skipping — missing SUPABASE_URL or SUPABASE_KEY")
            self._client = None
            return
        self._client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    def upload_video(self, video_path: str, platform: str = "") -> str | None:
        if not self._client:
            return None
        try:
            p = Path(video_path)
            if not p.exists():
                print(f"[SUPABASE] Video not found: {video_path}")
                return None
            ts = self._run_at.replace(":", "-").replace(".", "-")
            suffix = f"_{platform}" if platform else ""
            remote_path = f"recordings/{ts}{suffix}.webm"
            url = f"{SUPABASE_URL}/storage/v1/object/monitoring/{remote_path}"
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "video/webm",
            }
            import urllib.request
            with open(p, "rb") as f:
                data = f.read()
            req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
            urllib.request.urlopen(req, timeout=30)
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/monitoring/{remote_path}"
            print(f"[SUPABASE] Video uploaded: {public_url} ({p.stat().st_size / 1024:.0f}KB)")
            self.clean_old_recordings()
            return public_url
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            print(f"[SUPABASE] Video upload failed: HTTP {e.code} {err[:200]}")
            return None
        except Exception as e:
            print(f"[SUPABASE] Video upload error: {e}")
            return None

    def upload_screenshot(self, screenshot_path: str, platform: str = "") -> str | None:
        if not self._client or not screenshot_path:
            return None
        try:
            p = Path(screenshot_path)
            if not p.exists():
                print(f"[SUPABASE] Screenshot not found: {screenshot_path}")
                return None
            ts = self._run_at.replace(":", "-").replace(".", "-")
            fname = p.name
            suffix = f"_{platform}" if platform else ""
            remote_path = f"screenshots/{ts}{suffix}_{fname}"
            url = f"{SUPABASE_URL}/storage/v1/object/monitoring/{remote_path}"
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "image/png",
            }
            import urllib.request
            with open(p, "rb") as f:
                data = f.read()
            req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
            urllib.request.urlopen(req, timeout=15)
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/monitoring/{remote_path}"
            print(f"[SUPABASE] Screenshot uploaded: {public_url} ({len(data) / 1024:.0f}KB)")
            return public_url
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            print(f"[SUPABASE] Screenshot upload failed: HTTP {e.code} {err[:200]}")
            return None
        except Exception as e:
            print(f"[SUPABASE] Screenshot upload error: {e}")
            return None

    def store_result(self, page_or_flow: str, metric: str, value: float, status: str, step_failed: str | None = None, duration_ms: int | None = None, details: dict | None = None):
        if not self._client:
            print(f"[SUPABASE] Would store: {page_or_flow}/{metric}={value} status={status}")
            return
        row = {
            "run_at": self._run_at,
            "run_id": self._run_id,
            "page": page_or_flow,
            "metric": metric,
            "value": value,
            "status": status,
            "step_failed": step_failed,
            "duration_ms": duration_ms,
        }
        if details:
            row["details"] = details
        try:
            self._client.table("monitoring_runs").insert(row).execute()
        except Exception as e:
            if "details" in str(e) and details:
                row.pop("details", None)
                try:
                    self._client.table("monitoring_runs").insert(row).execute()
                except Exception as e2:
                    print(f"[SUPABASE] Insert error (retry): {e2}")
            else:
                print(f"[SUPABASE] Insert error: {e}")

    def store_audit_results(self, page: str, metrics: dict, status: str):
        for metric, value in metrics.items():
            self.store_result(page_or_flow=page, metric=metric, value=value, status=status)

    def store_flow_step(self, flow_name: str, step: str, duration_ms: int, status: str, error: str | None = None, details: dict | None = None):
        self.store_result(
            page_or_flow=flow_name,
            metric=f"step_{step}",
            value=duration_ms,
            status=status,
            step_failed=error,
            duration_ms=duration_ms,
            details=details,
        )

    def upload_session(self, filepath: str) -> str | None:
        if not self._client:
            return None
        try:
            p = Path(filepath)
            if not p.exists():
                return None
            url = f"{SUPABASE_URL}/storage/v1/object/monitoring/gajab_session.json"
            headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
            import urllib.request
            with open(p, "rb") as f:
                data = f.read()
            req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
            urllib.request.urlopen(req, timeout=30)
            pub_url = f"{SUPABASE_URL}/storage/v1/object/public/monitoring/gajab_session.json"
            print(f"[SUPABASE] Session uploaded: {pub_url}")
            return pub_url
        except Exception as e:
            print(f"[SUPABASE] Session upload error: {e}")
            return None

    @staticmethod
    def download_session(filepath: str) -> bool:
        url = f"{SUPABASE_URL}/storage/v1/object/public/monitoring/gajab_session.json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "gajab-monitor"})
            resp = urllib.request.urlopen(req, timeout=15)
            data = resp.read()
            with open(filepath, "wb") as f:
                f.write(data)
            print(f"[SUPABASE] Session downloaded: {filepath} ({len(data)}b)")
            return True
        except HTTPError as e:
            if e.code == 404:
                print("[SUPABASE] No session found in storage")
            else:
                print(f"[SUPABASE] Session download error: HTTP {e.code}")
            return False
        except Exception as e:
            print(f"[SUPABASE] Session download error: {e}")
            return False

    def clean_old_recordings(self):
        """Delete recordings older than 24h or keep max 24 most recent."""
        if not self._client:
            return
        try:
            resp = self._client.storage.from_("monitoring").list(path="recordings/")
            if not resp:
                return
            all_files = sorted(resp, key=lambda x: x.get("created_at", ""), reverse=True)
            if len(all_files) <= 24:
                return
            # Keep 24 most recent, delete rest
            for f in all_files[24:]:
                try:
                    self._client.storage.from_("monitoring").remove([f"recordings/{f['name']}"])
                    print(f"[SUPABASE] Deleted old recording: {f['name']}")
                except Exception as e:
                    print(f"[SUPABASE] Delete error: {e}")
        except Exception as e:
            print(f"[SUPABASE] Clean error: {e}")

    def get_latest_runs(self, limit: int = 100):
        if not self._client:
            return []
        try:
            resp = self._client.table("monitoring_runs").select("*").order("run_at", desc=True).limit(limit).execute()
            return resp.data
        except Exception as e:
            print(f"[SUPABASE] Query error: {e}")
            return []
