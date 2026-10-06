# API Security Boundary

Pricing decisions are made by a deterministic backend algorithm. The AI model is
a negotiation conversational layer only: it receives a bounded range and never
has access to, or the ability to query, the actual floor or target price.

- Public clients can call `GET /products` and receive list prices only.
- Admin clients can call `GET /products/:id/price-config`; only elevated admins receive protected values.
- AI clients can call only `POST /products/:id/negotiation-range` and receive a short-lived range grant.
- The internal validator is the enforcement point for accepted offers.

The example tokens in local development are intentionally simple. Production must
use an identity provider, separate service credentials, TLS, and a pricing store
that the AI-facing service cannot query directly.
