# Run the Costco Scanner locally
# Requires: AWS credentials configured, Python 3.12+, .venv created

# Refresh PATH to pick up recently installed tools (e.g. AWS CLI)
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
Set-Location "$PSScriptRoot\.."

if (-not $env:AWS_REGION) { $env:AWS_REGION = "us-east-2" }
if (-not $env:COSTCO_COUNTRY) { $env:COSTCO_COUNTRY = "US" }

# Auto-fetch resource names from CDK stack if not set
if (-not $env:DYNAMODB_RECEIPTS_TABLE) {
    Write-Host "Fetching resource names from CostcoScannerCommon stack..."
    $env:DYNAMODB_RECEIPTS_TABLE = aws cloudformation describe-stacks --stack-name CostcoScannerCommon --region $env:AWS_REGION --query 'Stacks[0].Outputs[?OutputKey==`ReceiptsTableName`].OutputValue' --output text
    $env:DYNAMODB_PRICE_DROPS_TABLE = aws cloudformation describe-stacks --stack-name CostcoScannerCommon --region $env:AWS_REGION --query 'Stacks[0].Outputs[?OutputKey==`PriceDropsTableName`].OutputValue' --output text
    $env:S3_BUCKET = aws cloudformation describe-stacks --stack-name CostcoScannerCommon --region $env:AWS_REGION --query 'Stacks[0].Outputs[?OutputKey==`ReceiptsBucketName`].OutputValue' --output text
}

Write-Host "Region:  $env:AWS_REGION"
Write-Host "Tables:  $env:DYNAMODB_RECEIPTS_TABLE, $env:DYNAMODB_PRICE_DROPS_TABLE"
Write-Host "Bucket:  $env:S3_BUCKET"
Write-Host "Starting on http://localhost:8000"

python app.py
