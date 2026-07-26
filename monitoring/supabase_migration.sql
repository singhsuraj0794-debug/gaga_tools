-- Run this in Supabase SQL Editor to create the monitoring_runs table
CREATE TABLE IF NOT EXISTS monitoring_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  page TEXT NOT NULL,
  metric TEXT NOT NULL,
  value DOUBLE PRECISION,
  status TEXT NOT NULL CHECK (status IN ('pass', 'fail', 'degraded')),
  step_failed TEXT,
  duration_ms INTEGER,
  details JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for querying latest runs
CREATE INDEX IF NOT EXISTS idx_monitoring_runs_run_at ON monitoring_runs (run_at DESC);

-- Index for filtering by page/flow
CREATE INDEX IF NOT EXISTS idx_monitoring_runs_page ON monitoring_runs (page);

-- Index for filtering by status
CREATE INDEX IF NOT EXISTS idx_monitoring_runs_status ON monitoring_runs (status);

-- RLS: allow anon key to read and insert (for frontend dashboard + monitoring script)
ALTER TABLE monitoring_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_can_select" ON monitoring_runs
  FOR SELECT USING (true);

CREATE POLICY "anon_can_insert" ON monitoring_runs
  FOR INSERT WITH CHECK (true);

CREATE POLICY "service_can_all" ON monitoring_runs
  FOR ALL USING (true);
