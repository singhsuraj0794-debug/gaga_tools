from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY


class SupabaseStore:
    def __init__(self):
        self._run_at = datetime.now(timezone.utc).isoformat()
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("[SUPABASE] Skipping — missing SUPABASE_URL or SUPABASE_KEY")
            self._client = None
            return
        self._client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    def upload_video(self, video_path: str) -> str | None:
        if not self._client:
            return None
        try:
            p = Path(video_path)
            if not p.exists():
                print(f"[SUPABASE] Video not found: {video_path}")
                return None
            ts = self._run_at.replace(":", "-").replace(".", "-")
            remote_path = f"recordings/{ts}.webm"
            with open(p, "rb") as f:
                self._client.storage.from_("monitoring").upload(remote_path, f, {"content-type": "video/webm"})
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/monitoring/{remote_path}"
            print(f"[SUPABASE] Video uploaded: {public_url} ({p.stat().st_size / 1024:.0f}KB)")
            return public_url
        except Exception as e:
            print(f"[SUPABASE] Video upload error: {e}")
            return None

    def store_result(self, page_or_flow: str, metric: str, value: float, status: str, step_failed: str | None = None, duration_ms: int | None = None, details: dict | None = None):
        if not self._client:
            print(f"[SUPABASE] Would store: {page_or_flow}/{metric}={value} status={status}")
            return
        row = {
            "run_at": self._run_at,
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

    def get_latest_runs(self, limit: int = 100):
        if not self._client:
            return []
        try:
            resp = self._client.table("monitoring_runs").select("*").order("run_at", desc=True).limit(limit).execute()
            return resp.data
        except Exception as e:
            print(f"[SUPABASE] Query error: {e}")
            return []
