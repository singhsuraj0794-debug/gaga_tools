# Voice Agent Negotiation Dashboard

This dashboard is intentionally separate from the conversational agent. The AI
service receives a bounded, short-lived range and never receives floor or target
price data. The internal validator enforces the final offer independently.

## Run locally

From `voice-agent/dashboard`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt
uvicorn api.server:app --reload --port 8080
```

Open `http://localhost:8080/`. The static dashboard can also be served on port
8081 by any static file server.

Local demo credentials are `admin-token`, `elevated-admin-token`, and `ai-token`.
Replace them with an identity provider and separate service credentials before
deployment. The protected price store should be moved behind a dedicated pricing
service/database role in production.
