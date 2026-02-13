#!/bin/bash

# Deploy script for Costco Scanner - CDK-based deployment
set -e

REGION=${AWS_DEFAULT_REGION:-us-west-2}
NOTIFY_EMAIL=${NOTIFY_EMAIL:-}

echo "🚀 Deploying Costco Scanner to $REGION...\"

# Step 1: CDK deploy
echo "📦 Running CDK deploy..."
cd "$(dirname "$0")/infra"

CDK_CONTEXT="-c region=$REGION"
[ -n "$NOTIFY_EMAIL" ] && CDK_CONTEXT="$CDK_CONTEXT -c notifyEmail=$NOTIFY_EMAIL"

npx cdk deploy --all --require-approval never $CDK_CONTEXT
cd ..

# Step 2: Read CDK stack outputs
echo "📋 Reading CDK stack outputs..."
API_URL=$(aws cloudformation describe-stacks --stack-name CostcoScannerAmplify --region $REGION --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' --output text)
USER_POOL_ID=$(aws cloudformation describe-stacks --stack-name CostcoScannerAmplify --region $REGION --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)
WEB_CLIENT_ID=$(aws cloudformation describe-stacks --stack-name CostcoScannerAmplify --region $REGION --query 'Stacks[0].Outputs[?OutputKey==`WebAppClientId`].OutputValue' --output text)
AMPLIFY_URL=$(aws cloudformation describe-stacks --stack-name CostcoScannerAmplify --region $REGION --query 'Stacks[0].Outputs[?OutputKey==`AmplifyAppUrl`].OutputValue' --output text)
AMPLIFY_APP_ID=$(aws amplify list-apps --region $REGION --query 'apps[?name==`costco-scanner`].appId' --output text)

echo "   API URL: $API_URL"
echo "   User Pool: $USER_POOL_ID"
echo "   Web Client: $WEB_CLIENT_ID"
echo "   Amplify App: $AMPLIFY_APP_ID"

# Step 3: Generate config.js
echo "📝 Generating config.js..."
cat > static/config.js << EOF
window.CONFIG = {
  API_URL: '$API_URL',
  COGNITO_USER_POOL_ID: '$USER_POOL_ID',
  COGNITO_CLIENT_ID: '$WEB_CLIENT_ID',
  REGION: '$REGION'
};
EOF

# Step 4: Deploy static files to Amplify
echo "🌐 Deploying static files to Amplify..."

# Cancel any pending jobs first
PENDING_JOB=$(aws amplify list-jobs --app-id $AMPLIFY_APP_ID --branch-name main --region $REGION --query 'jobSummaries[?status==`PENDING`].jobId' --output text 2>/dev/null)
if [ -n "$PENDING_JOB" ] && [ "$PENDING_JOB" != "None" ]; then
  aws amplify stop-job --app-id $AMPLIFY_APP_ID --branch-name main --job-id $PENDING_JOB --region $REGION > /dev/null 2>&1
  sleep 2
fi

DEPLOY_RESULT=$(aws amplify create-deployment --app-id $AMPLIFY_APP_ID --branch-name main --region $REGION --output json)
UPLOAD_URL=$(echo $DEPLOY_RESULT | python3 -c "import sys,json; print(json.load(sys.stdin)['zipUploadUrl'])")
JOB_ID=$(echo $DEPLOY_RESULT | python3 -c "import sys,json; print(json.load(sys.stdin)['jobId'])")

# Create zip of static files (Amplify expects files at root of zip)
cd static && zip -r ../amplify-deploy.zip . && cd ..

# Upload zip
curl -s -T amplify-deploy.zip "$UPLOAD_URL"

# Start deployment
aws amplify start-deployment --app-id $AMPLIFY_APP_ID --branch-name main --job-id $JOB_ID --region $REGION > /dev/null

# Wait for deployment
echo "⏳ Waiting for Amplify deployment..."
while true; do
  STATUS=$(aws amplify get-job --app-id $AMPLIFY_APP_ID --branch-name main --job-id $JOB_ID --region $REGION --query 'job.summary.status' --output text)
  if [ "$STATUS" = "SUCCEED" ]; then
    echo "✅ Amplify deployment complete!"
    break
  elif [ "$STATUS" = "FAILED" ] || [ "$STATUS" = "CANCELLED" ]; then
    echo "❌ Amplify deployment $STATUS"
    exit 1
  fi
  sleep 5
done

rm -f amplify-deploy.zip

echo ""
echo "🎉 Deployment complete!"
echo "🔗 Amplify: $AMPLIFY_URL"
echo "🔗 API: $API_URL"
echo "🔗 Local: http://localhost:8000"
