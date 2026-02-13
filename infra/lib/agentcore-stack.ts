import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as agentcore from '@aws-cdk/aws-bedrock-agentcore-alpha';
import * as ses from 'aws-cdk-lib/aws-ses';
import { Construct } from 'constructs';
import { CommonStack } from './common-stack';

interface AgentCoreStackProps extends cdk.StackProps {
  commonStack: CommonStack;
  notifyEmail: string;
}

export class AgentCoreStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const { commonStack, notifyEmail } = props;

    const role = new iam.Role(this, 'AgentCoreRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      inlinePolicies: {
        AgentPerms: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['dynamodb:Scan', 'dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:Query', 'dynamodb:BatchWriteItem', 'dynamodb:DeleteItem', 'dynamodb:ListTables'],
              resources: [commonStack.receiptsTable.tableArn, commonStack.priceDropsTable.tableArn, '*'],
            }),
            new iam.PolicyStatement({
              actions: ['s3:GetObject', 's3:PutObject', 's3:ListBucket'],
              resources: [commonStack.receiptsBucket.bucketArn, `${commonStack.receiptsBucket.bucketArn}/*`],
            }),
            new iam.PolicyStatement({
              actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:Converse', 'bedrock:ConverseStream'],
              resources: ['arn:aws:bedrock:*::foundation-model/*', `arn:aws:bedrock:*:${this.account}:inference-profile/*`],
            }),
            new iam.PolicyStatement({
              actions: ['ses:SendEmail', 'ses:SendRawEmail'],
              resources: ['*'],
            }),
          ],
        }),
      },
    });

    const runtime = new agentcore.Runtime(this, 'CostcoScannerRuntime', {
      runtimeName: 'costco_scanner',
      description: 'Weekly Costco price match scan + SES email report',
      agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromAsset('../', {
        file: 'agentcore.Dockerfile',
      }),
      executionRole: role,
      environmentVariables: {
        DYNAMODB_RECEIPTS_TABLE: commonStack.receiptsTable.tableName,
        DYNAMODB_PRICE_DROPS_TABLE: commonStack.priceDropsTable.tableName,
        S3_BUCKET: commonStack.receiptsBucket.bucketName,
        NOTIFY_EMAIL: notifyEmail,
        AWS_DEFAULT_REGION: this.region,
      },
    });

    // SES identity for weekly email (sends verification on first deploy)
    new ses.CfnEmailIdentity(this, 'SesIdentity', { emailIdentity: notifyEmail });

    new cdk.CfnOutput(this, 'RuntimeId', {
      value: runtime.agentRuntimeId,
      description: 'AgentCore Runtime ID',
    });
    new cdk.CfnOutput(this, 'RuntimeArn', {
      value: runtime.agentRuntimeArn,
      description: 'AgentCore Runtime ARN',
    });

    // EventBridge Scheduler → AgentCore universal target (Friday 9pm ET)
    const schedulerRole = new iam.Role(this, 'SchedulerRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      inlinePolicies: {
        InvokeAgentCore: new iam.PolicyDocument({
          statements: [new iam.PolicyStatement({
            actions: ['bedrock-agentcore:InvokeAgentRuntime'],
            resources: [`${runtime.agentRuntimeArn}*`],
          })],
        }),
      },
    });

    new scheduler.CfnSchedule(this, 'WeeklyScan', {
      name: 'costco-scanner-weekly',
      scheduleExpression: 'cron(0 21 ? * FRI *)',
      scheduleExpressionTimezone: 'America/New_York',
      flexibleTimeWindow: { mode: 'OFF' },
      target: {
        arn: 'arn:aws:scheduler:::aws-sdk:bedrockagentcore:invokeAgentRuntime',
        roleArn: schedulerRole.roleArn,
        input: JSON.stringify({
          AgentRuntimeArn: runtime.agentRuntimeArn,
          Payload: JSON.stringify({ prompt: 'run weekly scan' }),
        }),
        retryPolicy: { maximumEventAgeInSeconds: 60, maximumRetryAttempts: 0 },
      },
    });
  }
}
