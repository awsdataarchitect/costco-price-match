#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { CommonStack } from '../lib/common-stack';
import { AmplifyStack } from '../lib/amplify-stack';

import { AgentCoreStack } from '../lib/agentcore-stack';

const app = new cdk.App();

// Get context parameters
const region = app.node.tryGetContext('region') || 'us-west-2';
const notifyEmail = app.node.tryGetContext('notifyEmail') || '';

const env = { region, account: process.env.CDK_DEFAULT_ACCOUNT };

// Common resources (DynamoDB, S3, ECR)
const commonStack = new CommonStack(app, 'CostcoScannerCommon', { env });

// Amplify stack (Cognito, Lambda, API Gateway, Amplify app)
const amplifyStack = new AmplifyStack(app, 'CostcoScannerAmplify', {
  env,
  commonStack,
  notifyEmail,
});

// AgentCore Runtime (weekly scan + SES email)
if (notifyEmail) {
  new AgentCoreStack(app, 'CostcoScannerAgentCore', {
    env,
    commonStack,
    notifyEmail,
  });
}
