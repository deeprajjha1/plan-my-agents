# AWS Hosting Guide

This is the production-minded path for hosting PlanMyAgents at
`www.planmyagents.com` on AWS without over-engineering the first launch.

## Short Answer

Use a small AWS setup first:

- `Route 53` for `planmyagents.com` DNS.
- `CloudFront` + `ACM` TLS for `www.planmyagents.com`.
- `EC2` for the web app and API, or split to two EC2 services once traffic
  justifies it.
- `RDS PostgreSQL 16` with `pgvector` enabled for discovery, benchmark,
  verification, marketplace, and promoted-provider stores.
- `S3` for generated recipe exports and future downloadable artifacts.
- `CloudWatch` for logs, alarms, and basic health checks.
- `Secrets Manager` or SSM Parameter Store for API keys and database URLs.

This keeps the first hosted version simple: one deploy target, one managed
database, one domain, and clear upgrade paths.

## Can You Follow This Guide Now?

Yes, with one expectation: this is a launch runbook for a technical operator,
not a fully automated one-command Terraform deployment. It is good enough to
onboard the project behind `www.planmyagents.com` if you are comfortable
creating AWS resources in the console, setting environment variables, and
running the deploy commands from an EC2 shell.

Before public DNS cutover, do one local sanity pass:

```bash
make web-typecheck
make web-build
make compile
make json-validate
```

Then deploy to AWS, run `scripts/apply_migrations.py` against production RDS,
start API + web services, and only then point Route 53 / CloudFront to the
running app.

## Cost-Efficient Launch Profile

For the first public version, prefer this low-cost shape:

| Component | Recommended v0 choice | Rough monthly cost |
|---|---:|---:|
| Domain | Route 53 registered domain + hosted zone | `$1-2` plus annual domain fee |
| Web + API compute | One `t3.small` EC2 instance | `$15-25` |
| Disk | 30-50 GB gp3 EBS | `$3-5` |
| Database | Single-AZ RDS Postgres `db.t4g.micro` / `db.t4g.small` | `$15-45` |
| CDN/TLS | CloudFront + ACM | usually `<$5` at low traffic |
| Logs | CloudWatch basic logs | `$0-5` |
| Secrets | SSM Parameter Store first; Secrets Manager only where needed | `$0-5` |
| Artifacts | S3 for exports | usually `<$1` |

Expected v0 total: roughly `$35-90/month`, depending on region, instance size,
free-tier eligibility, log volume, and RDS size. The database is the main cost,
but it is worth keeping managed because discovery, benchmark, verification, and
trust-tier migrations are core product state.

Avoid these until the app has real traffic or security requirements:

- `ALB` for the first single-instance launch. It adds recurring cost; Nginx on
  EC2 or CloudFront to EC2 is enough initially.
- `NAT Gateway`. It is expensive for v0. Keep the instance public with a tight
  security group, or use VPC endpoints later.
- Multi-AZ RDS. Use it once uptime matters more than burn.
- ECS/EKS. The API Dockerfile is useful, but a single EC2/systemd deployment is
  cheaper and easier first.
- Separate web/API instances. Split them only when deploy cadence, load, or
  isolation demands it.
- Managed Redis, queues, WAF, and vendor-portal infrastructure. These are later
  upgrades, not launch blockers.

## What Changed Since The Earlier Plan

The AWS plan should be updated for the current repo state:

- The frontend is now `Next.js 16` and the repo uses Turbopack in both dev and
  build scripts: `next dev --turbopack` and `next build --turbopack`.
- Production cannot be treated as a JSON/SQLite-only demo anymore. Hosted
  Postgres with `pgvector` should be the default.
- `scripts/apply_migrations.py` now includes idempotent data migrations,
  including the `provider_verified` to `known_provider` trust-tier migration.
  Run it during deploys before starting the app.
- Live request-time discovery now includes curated local sources, so the API
  container must include `packages/discovery/**` and `packages/registry/**`.
  The existing `infra/api/Dockerfile` already copies `packages/`.
- User-facing labels now distinguish known/listed providers from providers that
  are actually tested by PlanMyAgents. Do not use old deployment copy that says
  every known provider is "verified".

## Target Domain

Recommended DNS shape:

- `www.planmyagents.com` -> CloudFront distribution for the web app.
- `api.planmyagents.com` -> API origin on EC2 or an ALB.
- `planmyagents.com` -> redirect to `www.planmyagents.com`.
- Later: `vendor.planmyagents.com` -> separate
  vendor portal deployment for blast-radius isolation.

Use ACM certificates in `us-east-1` for CloudFront. If you place an ALB in
front of the API, create a regional ACM cert for that ALB too.

## Recommended V0 Topology

```text
User
  |
  v
Route 53
  |
  v
CloudFront + ACM
  |
  +--> Next.js web origin on EC2 :3000
  |
  +--> /api/* or api.planmyagents.com -> FastAPI origin on EC2 :8000
                                      |
                                      v
                              RDS PostgreSQL + pgvector
```

For the first launch, running web and API on one EC2 instance is acceptable if
you use systemd services and keep RDS managed. Move the API behind an ALB and
split web/API EC2 instances once traffic, security, or deploy cadence demands
it.

## EC2 Instance

Start with:

- `t3.small` or `t3.medium` for a low-traffic v0.
- Ubuntu 24.04 LTS.
- 30-50 GB gp3 root volume.
- Security group allowing:
  - `22` only from your IP.
  - `80` and `443` from the internet if terminating TLS on EC2.
  - If using CloudFront/ALB in front, restrict origin access later.

Install:

```bash
sudo apt-get update
sudo apt-get install -y git curl build-essential python3.12 python3.12-venv \
  nodejs npm nginx
```

If using Docker for the API image:

```bash
sudo apt-get install -y docker.io
sudo usermod -aG docker ubuntu
```

## RDS PostgreSQL

Create an RDS PostgreSQL 16 instance and enable `pgvector`:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Use one database initially. The app already supports separate URLs, but they
can point to the same RDS database:

- `PLANMYAGENTS_DISCOVERY_STORE_URL`
- `PLANMYAGENTS_BENCHMARK_STORE_URL`
- `PLANMYAGENTS_VERIFICATION_STORE_URL`
- `PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL`
- `PLANMYAGENTS_MARKETPLACE_STORE_URL`

Run migrations on every deploy:

```bash
PYTHONPATH=apps/api python3 scripts/apply_migrations.py
```

This is currently idempotent and includes schema setup plus one-time data
migrations.

## API Deployment

The repo already has a production API image:

```bash
make deploy-build-api
docker run --rm -p 8000:8000 \
  -e PLANMYAGENTS_DISCOVERY_STORE_URL="$PLANMYAGENTS_DISCOVERY_STORE_URL" \
  -e PLANMYAGENTS_BENCHMARK_STORE_URL="$PLANMYAGENTS_BENCHMARK_STORE_URL" \
  -e PLANMYAGENTS_VERIFICATION_STORE_URL="$PLANMYAGENTS_VERIFICATION_STORE_URL" \
  -e PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL="$PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL" \
  -e PLANMYAGENTS_CORS_ALLOW_ORIGINS="https://www.planmyagents.com" \
  planmyagents-api:local
```

For a long-running EC2 deployment, wrap the Docker command in a systemd unit or
push the image to ECR and pull it during deploy.

Minimum production API env:

```bash
PLANMYAGENTS_ENVIRONMENT=production
PLANMYAGENTS_CORS_ALLOW_ORIGINS=https://www.planmyagents.com
PLANMYAGENTS_DISCOVERY_STORE_URL=postgresql://...
PLANMYAGENTS_BENCHMARK_STORE_URL=postgresql://...
PLANMYAGENTS_VERIFICATION_STORE_URL=postgresql://...
PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL=postgresql://...
PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB=true
PLANMYAGENTS_COST_CAP_ENABLED=true
PLANMYAGENTS_COST_CAP_PER_GOAL_USD=0.50
PLANMYAGENTS_COST_CAP_DAILY_USD=20.00
```

Add provider keys only when the corresponding feature is ready to run in
production. Keep live payment or external execution credentials in Secrets
Manager or SSM, not in `.env` committed to the instance.

## Web Deployment

Build from `apps/web`:

```bash
cd apps/web
npm ci
npm run build
PLANMYAGENTS_API_BASE_URL=https://api.planmyagents.com \
NEXT_PUBLIC_PLANMYAGENTS_API_BASE_URL=https://api.planmyagents.com \
npm run start
```

Minimum production web env:

```bash
PLANMYAGENTS_API_BASE_URL=https://api.planmyagents.com
NEXT_PUBLIC_PLANMYAGENTS_API_BASE_URL=https://api.planmyagents.com
```

Use systemd to keep `npm run start` alive, or containerize the web app later.

## Nginx Reverse Proxy Option

If one EC2 instance serves both apps:

```nginx
server {
  listen 80;
  server_name www.planmyagents.com;

  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}

server {
  listen 80;
  server_name api.planmyagents.com;

  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```

Prefer CloudFront/ACM or ALB/ACM for TLS. If terminating TLS directly on EC2,
use Certbot and keep renewals monitored.

## Background Upkeep

The API can serve without a separate worker, but discovery quality depends on
recurring upkeep:

```bash
0 * * * * cd /srv/agent-manager && ./scripts/upkeep_loop.sh >> .planmyagents_runs/upkeep.log 2>&1
```

For production, run this as a systemd timer or a separate worker service. It
refreshes discovery, verification, embeddings, and benchmark freshness without
needing user traffic to trigger everything.

## Deploy Checklist

Before pointing DNS at production:

```bash
make web-typecheck
make web-build
make compile
make json-validate
PYTHONPATH=apps/api python3 scripts/apply_migrations.py
```

Then smoke test:

```bash
curl https://api.planmyagents.com/health
curl https://api.planmyagents.com/categories
```

In the browser, verify:

- `https://www.planmyagents.com/`
- `https://www.planmyagents.com/goal`
- `https://www.planmyagents.com/categories`
- A refused goal shows honest blocker copy instead of claiming execution.
- Provider labels say `Known provider`, `Registry-listed`, or
  `Tested by PlanMyAgents` accurately.

## Pre-Launch Cleanup

Do this before copying code or starting services on EC2:

- Do not copy the local `.env` wholesale to production. Create a fresh
  production env file with only required keys.
- Stop local dev-only processes before packaging or testing deploy commands:
  `make api`, `make web`, old discovery tests, and stale background curls.
- Clear local build artifacts if the web server behaved oddly:
  `rm -rf apps/web/.next`, then rebuild with `npm run build`.
- Keep live provider credentials out of the repo and instance shell history.
  Prefer SSM Parameter Store for v0.
- Confirm `PLANMYAGENTS_ENVIRONMENT=production` so dev auth bypasses are refused
  at boot.
- Keep payment-provider live keys disabled unless the corresponding adapter has
  passed sandbox validation and cost caps are enabled.

## Upgrade Path

When the v0 is stable:

- Move API behind an ALB and autoscale EC2 or ECS tasks.
- Move web to S3/CloudFront only if the Next.js routes are made fully static;
  otherwise keep a Node origin or use Amplify/Vercel.
- Move upkeep to ECS scheduled tasks or EventBridge Scheduler.
- Add Sentry/PostHog and CloudWatch alarms for API 5xx, high latency, RDS CPU,
  and failed upkeep runs.
- Split vendor portal into a separate deployment when marketplace features go
  live.
