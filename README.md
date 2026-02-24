# Costco Receipt Scanner & Price Match Agent

AI-powered tool that scans your Costco receipts, cross-references purchases against active deals, and tells you exactly which items dropped in price and how much you can get back at the membership counter.

A weekly agent runs every Friday at 9pm ET, generates a formatted HTML report, and emails it to you via SES.

![Architecture](diagrams/architecture.png)

## How It Works

1. Upload receipt PDFs through the web UI or iOS app
2. Amazon Nova AI parses every line item, price, item number, and TPD (Temporary Price Drop)
3. Scrapers pull current deals from cocowest, cocoeast, redflagdeals, and the Costco coupon book
4. AI cross-references your purchases against active deals
5. Weekly agent emails you a report with price adjustment opportunities and TPD savings already applied

![Weekly Flow](diagrams/weekly-flow.png)

## Architecture

- **Web Frontend**: Static HTML on AWS Amplify with Cognito authentication
- **iOS App**: Native SwiftUI, zero third-party dependencies, 0.9s builds
- **API**: API Gateway HTTP API → Lambda (FastAPI + Mangum), streaming analysis responses
- **AI**: Amazon Nova 2 Lite for parsing + analysis, Nova Premier for complex receipts
- **Automation**: AgentCore Runtime triggered by EventBridge Scheduler universal target (no Lambda middleman), SES for email
- **Storage**: DynamoDB (receipts + deals), S3 (receipt PDFs with presigned URLs)
- **Infrastructure**: CDK (TypeScript), 3 stacks, deploy to any region

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

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./run.sh
```

Opens on `http://localhost:8000`. Auto-fetches DynamoDB/S3 resource names from the CDK stack.

## Deploy

```bash
cd infra && npm install && cd ..

# Deploy Lambda, Amplify, API Gateway, Cognito, DynamoDB, S3
NOTIFY_EMAIL=your-email@example.com ./deploy.sh

# Deploy weekly agent (SES verification email sent on first deploy)
cd infra && npx cdk deploy CostcoScannerAgentCore \
  -c region=us-west-2 \
  -c notifyEmail=your-email@example.com \
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

Under $1/month for personal use. Bedrock Nova tokens are the main cost (~$0.10-0.20/week). Lambda, SES, DynamoDB, API Gateway, and Amplify fall within free tier.

## Built With ❤️

- [Kiro CLI](https://kiro.dev) — AI coding assistant by AWS
- [Amazon Bedrock](https://aws.amazon.com/bedrock/) — Nova 2 Lite + Nova Premier
- [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/) — Runtime for the weekly agent
- [AWS CDK](https://aws.amazon.com/cdk/) — Infrastructure as code
- [Xcode MCP Bridge](https://developer.apple.com/documentation/xcode/giving-agentic-coding-tools-access-to-xcode) — AI agent access to Xcode builds, previews, and tests
