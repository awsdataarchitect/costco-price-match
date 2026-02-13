#!/bin/bash
# Run the Costco Scanner locally
# Requires: AWS credentials configured, Python 3.12+, .venv created

cd "$(dirname "$0")" && source .venv/bin/activate

export AWS_REGION=${AWS_REGION:-us-west-2}

# Auto-fetch resource names from CDK stack if not set
if [ -z "$DYNAMODB_RECEIPTS_TABLE" ]; then
  echo "Fetching resource names from CostcoScannerCommon stack..."
  export DYNAMODB_RECEIPTS_TABLE=$(aws cloudformation describe-stacks --stack-name CostcoScannerCommon --region $AWS_REGION --query 'Stacks[0].Outputs[?OutputKey==`ReceiptsTableName`].OutputValue' --output text)
  export DYNAMODB_PRICE_DROPS_TABLE=$(aws cloudformation describe-stacks --stack-name CostcoScannerCommon --region $AWS_REGION --query 'Stacks[0].Outputs[?OutputKey==`PriceDropsTableName`].OutputValue' --output text)
  export S3_BUCKET=$(aws cloudformation describe-stacks --stack-name CostcoScannerCommon --region $AWS_REGION --query 'Stacks[0].Outputs[?OutputKey==`ReceiptsBucketName`].OutputValue' --output text)
fi

echo "Region:  $AWS_REGION"
echo "Tables:  $DYNAMODB_RECEIPTS_TABLE, $DYNAMODB_PRICE_DROPS_TABLE"
echo "Bucket:  $S3_BUCKET"
echo "Starting on http://localhost:8000"

python app.py
