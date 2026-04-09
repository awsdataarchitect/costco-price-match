# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Costco Receipt Scanner & Price Match Agent — AI-powered tool that scans Costco receipts (PDF or camera photo), cross-references purchases against active US deals from 5 sources, and identifies price adjustment opportunities. Includes a web UI, native iOS app, and a weekly automated email agent.

BYOI (Bring Your Own Infrastructure) model: users deploy CDK to their own AWS account. No SaaS backend.

## Commands

### Local development
```bash
python3 -m venv .venv && source .venv/bin/activate  # Linux/Mac
# Windows (Git Bash): source .venv/Scripts/activate
pip install -r requirements.txt
./run.sh  # starts uvicorn on localhost:8000, auto-fetches resource names from CloudFormation
```

### Deploy (all stacks)
```bash
cd infra && npm install && cd ..
NOTIFY_EMAIL=your-email@example.com ./deploy.sh
```

### Deploy static files only (frontend changes)
```bash
./deploy.sh --static-only
```

### Deploy AgentCore separately
```bash
cd infra && npx cdk deploy CostcoScannerAgentCore \
  -c region=us-east-1 -c notifyEmail=your-email@example.com --require-approval never
```

### Cleanup
```bash
cd infra
npx cdk destroy CostcoScannerAgentCore -c region=us-east-1 -c notifyEmail=your-email
npx cdk destroy CostcoScannerAmplify -c region=us-east-1
npx cdk destroy CostcoScannerCommon -c region=us-east-1
```

No automated test suite exists.

## Architecture

### Two entry points
- **`app.py`** — FastAPI web API, runs on Lambda (ARM64, Mangum adapter) behind API Gateway v2 with Cognito JWT auth. Serves uploads (PDF + images), receipt CRUD, price scanning, and SSE streaming analysis.
- **`agent.py`** — AgentCore Runtime entry point, triggered by EventBridge Scheduler every Friday 9pm ET. Scans deals, runs analysis, emails HTML report via SES.

### Backend services (`services/`)
- **`db.py`** — DynamoDB (CostcoReceipts, CostcoPriceDrops tables) + S3 (receipt files with presigned URLs). Deduplicates receipts by file hash.
- **`receipt_parser.py`** — Parses receipts via Bedrock Nova. Three modes: Lite (single-call PDF), Premier (converts to PNG, 3 parallel calls for accuracy), and direct image parsing (JPG/PNG from camera). Post-processes TPD merging, item number extraction with OCR correction (O→0, B→8).
- **`price_scanner.py`** — Scrapes 5 US deal sources (Reddit r/Costco, Reddit r/CostcoDeals, KCL Costco Deals, KCL Coupon Book, CostcoFan). Returns `(deals, source_results)` tuple for per-source observability. Caches per calendar day, deduplicates by (item_name, promo_end).
- **`analyzer.py`** — Strands Agents framework with Nova 2 Lite. Tool-based matching: exact item number → partial item number → keyword overlap. Streams results via SSE.

### Infrastructure (`infra/`, AWS CDK TypeScript)
Three stacks: **CommonStack** (DynamoDB, S3, ECR), **AmplifyStack** (Cognito with email OTP, Lambda, API Gateway, Amplify hosting), **AgentCoreStack** (runtime, EventBridge scheduler, SES).

### Frontends
- **Web** (`static/index.html`) — Single HTML file, SPA with passwordless email OTP auth (Cognito USER_AUTH flow, no SDK dependency). Config injected at deploy via `static/config.js`. Mobile-responsive with camera capture for receipt photos.
- **iOS** (`ios/CostcoScanner/`) — Native SwiftUI, zero dependencies, pure URLSession + Cognito REST API. BYOI flow: paste API URL → auto-fetches credentials from `/api/config`.

### Key patterns
- `/api/config` is the only unauthenticated endpoint (returns Cognito pool info for BYOI)
- `/api/upload` accepts PDF, JPG, PNG, WebP, GIF — images are sent directly to Bedrock Nova
- `/api/analyze` uses Server-Sent Events for streaming agent output
- Receipt files stored in S3 with 7-day presigned URLs
- Price scanner uses random User-Agent rotation and 1-second rate limiting between sources
- CORS locked to `https://costco.dunkinspeeps.com` + `localhost:8000`
- All resources tagged with `project: costco-price-match` for cost tracking
