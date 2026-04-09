# Costco Receipt Scanner & Price Match Agent

AI-powered tool that scans your Costco receipts, cross-references purchases against active US deals, and tells you exactly which items dropped in price and how much you can get back at the membership counter.

A weekly agent runs every Friday at 9pm ET, generates a formatted HTML report, and emails it to you via SES.

Forked from [the original Canadian version](https://github.com/waltsims/costco-price-match) and adapted for US Costco deal sources.

![Architecture](diagrams/architecture.png)

## How It Works

1. Upload receipt PDFs or snap a photo with your phone's camera
2. Amazon Nova AI parses every line item, price, item number, and TPD (Temporary Price Drop)
3. Scrapers pull current deals from Reddit r/Costco, Reddit r/CostcoDeals, KrazyCouponLady, and CostcoFan
4. AI cross-references your purchases against active deals
5. Weekly agent emails you a report with price adjustment opportunities and TPD savings already applied

![Weekly Flow](diagrams/weekly-flow.png)

## What's Different from the Original

- **US deal sources** — Replaced 6 Canadian sources (CocoWest, CocoEast, RedFlagDeals, SmartCanucks, etc.) with 5 US sources (Reddit r/Costco, Reddit r/CostcoDeals, KCL Costco Deals, KCL Coupon Book, CostcoFan)
- **Camera upload** — Snap a photo of your receipt directly from the web app on mobile, no PDF scanning needed
- **Image support** — Upload JPG, PNG, WebP alongside PDFs; images are sent directly to Bedrock Nova
- **Per-source observability** — Scan results show status, deal count, and duration for each scraper
- **Passwordless auth** — Email OTP sign-in via Cognito (no passwords to manage)
- **Mobile-responsive UI** — Styled for phone use with camera capture, touch-friendly modals
- **Deploy improvements** — `--static-only` flag for quick frontend deploys, Windows/Git Bash compatibility

## Architecture

- **Web Frontend**: Static HTML on AWS Amplify with Cognito email OTP authentication
- **iOS App**: Native SwiftUI, zero third-party dependencies, 0.9s builds
- **API**: API Gateway HTTP API → Lambda (FastAPI + Mangum), streaming analysis responses
- **AI**: Amazon Nova 2 Lite for parsing + analysis, Nova Premier for complex receipts
- **Automation**: AgentCore Runtime triggered by EventBridge Scheduler universal target (no Lambda middleman), SES for email
- **Storage**: DynamoDB (receipts + deals), S3 (receipt files with presigned URLs)
- **Infrastructure**: CDK (TypeScript), 3 stacks, deploy to any region

## Prerequisites

- AWS CLI configured with credentials
- Node.js 18+ and npm
- Docker running
- Python 3.12+

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows Git Bash: source .venv/Scripts/activate
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
  -c region=us-east-1 \
  -c notifyEmail=your-email@example.com \
  --require-approval never

# Deploy frontend changes only (no CDK/Docker rebuild)
./deploy.sh --static-only
```

After deploy, the CDK output shows your API Gateway URL. Paste it into the iOS app's Settings to connect.

## Cleanup

```bash
cd infra
npx cdk destroy CostcoScannerAgentCore -c region=us-east-1 -c notifyEmail=your-email@example.com
npx cdk destroy CostcoScannerAmplify -c region=us-east-1
npx cdk destroy CostcoScannerCommon -c region=us-east-1
```

## Backlog

- **Disable self-signup** — Lock down Cognito registration once family accounts are created. Currently anyone who discovers the Amplify URL can sign up and see all receipts (no per-user data isolation).
- **Custom domain SSL** — `costco.dunkinspeeps.com` is configured and working. Consider moving the main domain DNS to Route53 for tighter integration.
- **Scraper resilience** — Deal sources change HTML structure without warning. The per-source observability helps detect failures, but scrapers may need periodic updates when sites change.
- **Receipt parsing accuracy** — Camera photos of handheld receipts can produce OCR errors (wrong dates, missed items). The edit/delete item UI helps correct these, but improving the prompt or adding a second-pass validation could reduce manual fixes.
- **Per-user data isolation** — All authenticated users share all receipts. Fine for family use with signup disabled, but would need row-level filtering (e.g., by Cognito sub) if opened to more users.
- **Activate cost allocation tag** — The `project: costco-price-match` tag is on all resources but needs to be activated in AWS Billing as a cost allocation tag (takes 24h after first tagging) to filter in Cost Explorer.

## Cost

Under $1/month for personal use. Bedrock Nova tokens are the main cost (~$0.10-0.20/week). Lambda, SES, DynamoDB, API Gateway, and Amplify fall within free tier. All resources are tagged with `project: costco-price-match` for cost tracking in Cost Explorer.

## Built With

- [Claude Code](https://claude.ai/code) — AI coding assistant by Anthropic (US adaptation)
- [Kiro CLI](https://kiro.dev) — AI coding assistant by AWS (original version)
- [Amazon Bedrock](https://aws.amazon.com/bedrock/) — Nova 2 Lite + Nova Premier
- [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/) — Runtime for the weekly agent
- [AWS CDK](https://aws.amazon.com/cdk/) — Infrastructure as code
