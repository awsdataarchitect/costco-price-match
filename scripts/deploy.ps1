# Deploy script for Costco Scanner - CDK-based deployment
$ErrorActionPreference = "Stop"

# Refresh PATH to pick up recently installed tools (e.g. AWS CLI)
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

$REGION = if ($env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION } else { "us-east-2" }
$NOTIFY_EMAIL = if ($env:NOTIFY_EMAIL) { $env:NOTIFY_EMAIL } else { "" }
$COSTCO_COUNTRY = if ($env:COSTCO_COUNTRY) { $env:COSTCO_COUNTRY } else { "US" }

Write-Host "Deploying Costco Scanner to $REGION (country: $COSTCO_COUNTRY)..."

# Step 1: CDK deploy
Write-Host "Running CDK deploy..."
Push-Location "$PSScriptRoot\..\infra"

$CDK_CONTEXT = "-c region=$REGION -c costcoCountry=$COSTCO_COUNTRY"
if ($NOTIFY_EMAIL) { $CDK_CONTEXT += " -c notifyEmail=$NOTIFY_EMAIL" }

$cdkArgs = @("cdk", "deploy", "--all", "--require-approval", "never") + ($CDK_CONTEXT -split " ")
npx @cdkArgs
Pop-Location

# Step 2: Read CDK stack outputs
Write-Host "Reading CDK stack outputs..."
$API_URL = aws cloudformation describe-stacks --stack-name CostcoScannerAmplify --region $REGION --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' --output text
$USER_POOL_ID = aws cloudformation describe-stacks --stack-name CostcoScannerAmplify --region $REGION --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text
$WEB_CLIENT_ID = aws cloudformation describe-stacks --stack-name CostcoScannerAmplify --region $REGION --query 'Stacks[0].Outputs[?OutputKey==`WebAppClientId`].OutputValue' --output text
$AMPLIFY_URL = aws cloudformation describe-stacks --stack-name CostcoScannerAmplify --region $REGION --query 'Stacks[0].Outputs[?OutputKey==`AmplifyAppUrl`].OutputValue' --output text
$AMPLIFY_APP_ID = aws amplify list-apps --region $REGION --query 'apps[?name==`costco-scanner`].appId' --output text

Write-Host "   API URL: $API_URL"
Write-Host "   User Pool: $USER_POOL_ID"
Write-Host "   Web Client: $WEB_CLIENT_ID"
Write-Host "   Amplify App: $AMPLIFY_APP_ID"

# Step 3: Generate config.js
Write-Host "Generating config.js..."
@"
window.CONFIG = {
  API_URL: '$API_URL',
  COGNITO_USER_POOL_ID: '$USER_POOL_ID',
  COGNITO_CLIENT_ID: '$WEB_CLIENT_ID',
  REGION: '$REGION'
};
"@ | Set-Content -Path "$PSScriptRoot\..\static\config.js" -Encoding UTF8

# Step 4: Deploy static files to Amplify
Write-Host "Deploying static files to Amplify..."

# Cancel any pending jobs first
$PENDING_JOB = aws amplify list-jobs --app-id $AMPLIFY_APP_ID --branch-name main --region $REGION --query 'jobSummaries[?status==`PENDING`].jobId' --output text 2>$null
if ($PENDING_JOB -and $PENDING_JOB -ne "None") {
    aws amplify stop-job --app-id $AMPLIFY_APP_ID --branch-name main --job-id $PENDING_JOB --region $REGION >$null 2>&1
    Start-Sleep -Seconds 2
}

$DEPLOY_RESULT = aws amplify create-deployment --app-id $AMPLIFY_APP_ID --branch-name main --region $REGION --output json | ConvertFrom-Json
$UPLOAD_URL = $DEPLOY_RESULT.zipUploadUrl
$JOB_ID = $DEPLOY_RESULT.jobId

# Create zip of static files
$zipPath = "$PSScriptRoot\..\amplify-deploy.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath }
Compress-Archive -Path "$PSScriptRoot\..\static\*" -DestinationPath $zipPath

# Upload zip
curl.exe -s -T $zipPath $UPLOAD_URL

# Start deployment
aws amplify start-deployment --app-id $AMPLIFY_APP_ID --branch-name main --job-id $JOB_ID --region $REGION >$null

# Wait for deployment
Write-Host "Waiting for Amplify deployment..."
while ($true) {
    $STATUS = aws amplify get-job --app-id $AMPLIFY_APP_ID --branch-name main --job-id $JOB_ID --region $REGION --query 'job.summary.status' --output text
    if ($STATUS -eq "SUCCEED") {
        Write-Host "Amplify deployment complete!"
        break
    } elseif ($STATUS -eq "FAILED" -or $STATUS -eq "CANCELLED") {
        Write-Host "Amplify deployment $STATUS"
        exit 1
    }
    Start-Sleep -Seconds 5
}

Remove-Item $zipPath -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Deployment complete!"
Write-Host "Amplify: $AMPLIFY_URL"
Write-Host "API: $API_URL"
Write-Host "Local: http://localhost:8000"
