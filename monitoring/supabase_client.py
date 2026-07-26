from __future__ import annotations
from datetime import datetime, timezone
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY


class SupabaseStore:
    def __init__(self):
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("[SUPABASE] Skipping — missing SUPABASE_URL or SUPABASE_KEY")
            self._client = None
            return
        self._client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self._run_at = datetime.now(timezone.utc).isoformat()

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
