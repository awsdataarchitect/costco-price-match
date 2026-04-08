# Costco Receipt Scanner & Price Match Agent

AI-powered tool that scans your Costco receipts, cross-references purchases against active deals, and tells you exactly which items dropped in price and how much you can get back at the membership counter.

A weekly agent runs every Friday at 9pm ET, generates a formatted HTML report, and emails it to you via SES.

![Architecture](diagrams/architecture.png)

## How It Works

1. Upload receipt PDFs through the web UI, iOS app, or Telegram (via ClawdBot)
2. Receipt parser auto-detects PDF type — text-based PDFs parse free with PyMuPDF; scanned/image PDFs route to Amazon Nova AI
3. Scrapers pull current deals from cocowest, cocoeast, redflagdeals, and the Costco coupon book
4. AI cross-references your purchases against active deals
5. Weekly agent emails you a report with price adjustment opportunities and TPD savings already applied

![Weekly Flow](diagrams/weekly-flow.png)

## Architecture

- **Web Frontend**: Static HTML on AWS Amplify with Cognito authentication
- **iOS App**: Native SwiftUI, zero third-party dependencies, 0.9s builds
- **API**: API Gateway HTTP API → Lambda (FastAPI + Mangum), streaming analysis responses
- **Receipt Parsing**: Auto-routing — PyMuPDF text extraction (free) → Nova 2 Lite fallback → Nova Premier for scanned images
- **AI Analysis**: Amazon Nova for deal cross-referencing and weekly reports
- **Chat Integration**: OpenClaw/ClawdBot skill for Telegram and Discord
- **Automation**: AgentCore Runtime triggered by EventBridge Scheduler, SES for email
- **Storage**: DynamoDB (receipts + deals), S3 (receipt PDFs)
- **Infrastructure**: CDK (TypeScript), 3 stacks, deploy to any region

## Receipt Parsing

Receipts are parsed with a smart fallback chain — most Costco PDFs are text-based and parse for free:

```
Upload PDF
    │
    ▼ inspect_pdf() — instant, free
    ├─ Text PDF (text_chars > 100)  →  PyMuPDF regex parser    [FREE]
    ├─ Image PDF (image_count > 0)  →  Nova Premier (300dpi)   [Bedrock tokens]
    └─ Unknown                      →  Nova 2 Lite             [Bedrock tokens]
```

The upload response includes `parsed_by: "text" | "bedrock-lite" | "bedrock-premier"` so you always know whether tokens were consumed. If parsing looks wrong, call `POST /api/reparse/<receipt_id>` to re-run with Nova Premier.

## Project Structure

```
├── docker/        # Dockerfiles for Lambda and AgentCore
├── infra/         # CDK infrastructure (TypeScript)
├── ios/           # Native SwiftUI iOS app
├── scripts/       # Build, deploy, and run scripts
├── services/      # Python backend services (scrapers, parser, analyzer)
├── skills/        # OpenClaw skill definitions
├── static/        # Web frontend (HTML/CSS/JS)
├── tests/         # API test scripts
├── agent.py       # AgentCore weekly runner entry point
├── app.py         # FastAPI application entry point
└── requirements.txt
```

## API Flow

Three independent pipelines — each can be triggered separately:

```
PIPELINE 1: Receipt Ingestion  (triggered on upload)
─────────────────────────────────────────────────────
POST /api/upload
    │
    ├─ receipt_parser.parse_receipt_pdf()
    │      ├─ inspect_pdf() — is it text-based or image-based?
    │      ├─ TEXT PDF → _parse_text() state machine  (free, no Bedrock)
    │      └─ IMAGE PDF → Bedrock Nova Premier OCR
    │
    ├─ db.put_receipt()  → DynamoDB receipts table
    └─ db.upload_pdf()   → S3  (stored for reparse)


PIPELINE 2: Deal Scanning  (manual trigger or weekly AgentCore)
────────────────────────────────────────────────────────────────
POST /api/scan-prices?force_refresh=true
    │
    └─ price_scanner.scan_price_drops()
           ├─ _scrape_costcoinsider()  ─┐
           ├─ _scrape_hip2save()        │  text scraping — no Bedrock
           ├─ _scrape_slickdeals()      │  (set COSTCO_COUNTRY=CA for
           ├─ _scrape_reddit("Costco") ─┘   Canadian sources instead)
           └─ _scrape_coupon_book_us()  ──  Bedrock Nova Lite (image OCR)
           │
           └─ db.put_price_drop()  → DynamoDB price_drops table
              (deduplicates by name + promo_end, skips expired deals)


PIPELINE 3: Price Match Analysis  (reads from both tables — no scraping)
─────────────────────────────────────────────────────────────────────────

  PATH A — Bot-side, preferred  (zero Bedrock)
  ─────────────────────────────
  GET /api/price-match-candidates
      │
      ├─ db.get_recent_receipts(30)   ← DynamoDB receipts table
      ├─ db.get_all_price_drops()     ← DynamoDB price_drops table
      │
      └─ Pure Python matching:
             1. Exact item number match        (highest confidence)
             2. Partial item number (first 5 digits)
             3. Name keyword overlap (2+ words)
             4. Filter: deal_price < receipt_price  (real savings only)
             └─ Returns JSON  →  ClawdBot's own LLM formats the response

  PATH B — Bedrock streaming fallback
  ────────────────────────────────────
  GET /api/analyze
      │
      ├─ db.get_all_price_drops()   ← DynamoDB price_drops table
      ├─ db.get_recent_receipts()   ← DynamoDB receipts table
      │
      └─ AWS Strands agent with 3 tools:
             tool 1: get_receipt_items_local()
             tool 2: get_current_price_drops_local()
             tool 3: find_potential_matches_local()  ← same logic as Path A
             └─ Claude reasons over matches  →  SSE stream to caller
```

> **Note:** Deal scanning must run before analysis. If the price_drops table is
> empty, both analysis paths return no candidates. Deals are cached per day —
> omit `force_refresh` to reuse today's scan and avoid re-scraping.

## iOS App — CostScanner

Native SwiftUI app with a BYOI (Bring Your Own Infrastructure) model. Deploy the CDK stack, paste your API URL in Settings, and the app auto-connects. No sign-in screen. No account creation. The infrastructure IS the account.

**Available on the [App Store](https://apps.apple.com/ca/app/costscanner/id6759347927)** (published by Waltsoft Inc.)

### Features

- 4-tab interface: Receipts, Deals, Analyze, Settings
- Upload receipt PDFs via file picker
- Browse 700+ active deals with source chips, date filters, and urgency badges
- AI-powered analysis with streaming responses from Amazon Nova
- Tracks Temporary Price Drops (TPD) already applied at checkout
- Pull-to-refresh, swipe-to-delete, PDF viewer
- Cold start retry with user-friendly loading states

### The Numbers

- 15 Swift files, 0 third-party dependencies
- 0.9 second incremental builds
- Pure URLSession + direct Cognito REST API (no Amplify SDK)
- Built with [Kiro CLI](https://kiro.dev) and Apple's [Xcode MCP bridge](https://developer.apple.com/documentation/xcode/giving-agentic-coding-tools-access-to-xcode)

### BYOI Model

CostScanner is not a SaaS. No servers hosted for users. No data stored on our end. Users deploy their own AWS CDK stack and the app connects to their infrastructure.

1. Deploy the CDK stack (see below)
2. Open CostScanner → Settings → paste your API Gateway URL
3. App calls `/api/config`, fetches Cognito credentials from Secrets Manager, signs in automatically
4. Done. Your data stays in your AWS account.

Privacy policy: we collect nothing. Every receipt, deal, and AI analysis lives in the user's own AWS account. `cdk destroy` and everything disappears.

```
ios/CostcoScanner/
├── CostcoScannerApp.swift       # Entry point, auto-refresh on launch
├── APIClient.swift              # URLSession, auto-retry 401, cancellation handling
├── BackendConfig.swift          # Cognito REST auth, /api/config, Secrets Manager
├── Models.swift                 # Receipt, PriceDrop, API responses
└── Views/
    ├── MainTabView.swift        # 4 tabs + ConnectPrompt overlay
    ├── ReceiptsView.swift       # List, upload, pull-to-refresh, cold start retry
    ├── ReceiptDetailView.swift  # Line items, TPD badges, PDF viewer
    ├── DealsView.swift          # Search, source chips, date filter, urgency badges
    ├── AnalysisView.swift       # SSE streaming, receipt selector, share
    ├── SettingsView.swift       # API URL, BYOI Terms/Privacy, About
    └── PDFViewer.swift          # Full-screen receipt PDF
```

## Prerequisites

- AWS CLI configured with credentials
- Node.js 18+ and npm
- Docker running
- Python 3.12+
- Xcode 26.3+ (for iOS development with MCP bridge)

## Run Locally

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./scripts/run.sh
```

**Windows**
```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
.\scripts\run.ps1
```

Opens on `http://localhost:8000`. Auto-fetches DynamoDB/S3 resource names from the CDK stack.

## Deploy

**macOS / Linux**
```bash
cd infra && npm install && cd ..

# Deploy Lambda, Amplify, API Gateway, Cognito, DynamoDB, S3
NOTIFY_EMAIL=your-email@example.com ./scripts/deploy.sh

# Deploy weekly agent (SES verification email sent on first deploy)
cd infra && npx cdk deploy CostcoScannerAgentCore \
  -c region=us-west-2 \
  -c notifyEmail=your-email@example.com \
  --require-approval never
```

**Windows**
```powershell
cd infra; npm install; cd ..

# Deploy Lambda, Amplify, API Gateway, Cognito, DynamoDB, S3
$env:NOTIFY_EMAIL = "your-email@example.com"; .\scripts\deploy.ps1

# Deploy weekly agent
cd infra
npx cdk deploy CostcoScannerAgentCore `
  -c region=us-west-2 `
  -c notifyEmail=your-email@example.com `
  --require-approval never
```

After deploy, the CDK output shows your API Gateway URL. Paste it into the iOS app's Settings to connect.

## Cleanup

```bash
cd infra
npx cdk destroy CostcoScannerAgentCore -c region=us-west-2 -c notifyEmail=your-email@example.com
npx cdk destroy CostcoScannerAmplify -c region=us-west-2
npx cdk destroy CostcoScannerCommon -c region=us-west-2
```

## Cost

Under $1/month for personal use. Lambda, SES, DynamoDB, API Gateway, and Amplify fall within free tier.

- **Receipt parsing**: Free for text-based PDFs (PyMuPDF). Bedrock Nova tokens only consumed for scanned/image PDFs or manual reparse.
- **Deal scanning**: Bedrock Nova 2 Lite used for the Costco coupon book scraper (~$0.10-0.20/week). The daily free-tier quota covers typical personal use; a throttle guard stops scanning after 3 consecutive quota errors.
- **Weekly analysis**: Nova 2 Lite for the Friday email report.

## ClawdBot / OpenClaw Integration

Use this app from Telegram (or Discord) via [OpenClaw](https://github.com/openclaw/openclaw) — a self-hosted AI gateway that wraps skills around tools and routes chat messages to them.

### What is OpenClaw?

OpenClaw is a lightweight, self-hosted AI agent gateway. You connect it to Telegram or Discord, point it at your OpenClaw skill files, and it can call your APIs on your behalf in natural language.

### Installing the costco-scanner skill

**1. Install OpenClaw**

```bash
npm install -g openclaw
openclaw init
```

**2. Copy the skill**

```bash
# Copy skill to OpenClaw's skills directory
node scripts/setup-skills.js
```

Or manually copy `skills/costco-scanner/SKILL.md` to `~/.openclaw/skills/costco-scanner/SKILL.md`.

**3. Set the environment variable**

In `~/.openclaw/openclaw.json`, add under `skills.entries`:

```json
{
  "skills": {
    "entries": {
      "costco-scanner": {
        "COSTCO_SCANNER_URL": "https://<your-api-gateway-id>.execute-api.<region>.amazonaws.com"
      }
    }
  }
}
```

Get your API Gateway URL from the CDK deploy output (`CostcoScannerCommon.ApiUrl`).

**4. Start a new session in Telegram**

Send `/new` in your Telegram chat to start a fresh session — OpenClaw snapshots eligible skills at session start.

### Triggering the skill

The skill activates on phrases like:
- *"Check my Costco receipts for price matches"*
- *"What deals are on right now?"*
- *"Scan for fresh Costco deals"*
- *"Show my Bedrock token usage"*

The agent authenticates against your Cognito pool automatically (credentials come from `/api/config`), calls the relevant API endpoints, and streams the analysis back to you.

### Authentication

The API uses AWS Cognito. The skill fetches credentials at runtime from `/api/config` (backed by Secrets Manager) — no tokens or passwords need to be stored in OpenClaw config.

### Sync skill updates

After editing `skills/costco-scanner/SKILL.md`, run `node scripts/setup-skills.js` to push changes to the live OpenClaw directory and send `/new` in Telegram.

---

## Built With ❤️

- [Kiro CLI](https://kiro.dev) — AI coding assistant by AWS
- [Amazon Bedrock](https://aws.amazon.com/bedrock/) — Nova 2 Lite + Nova Premier
- [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/) — Runtime for the weekly agent
- [AWS CDK](https://aws.amazon.com/cdk/) — Infrastructure as code
- [Xcode MCP Bridge](https://developer.apple.com/documentation/xcode/giving-agentic-coding-tools-access-to-xcode) — AI agent access to Xcode builds, previews, and tests
