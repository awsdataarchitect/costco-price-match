import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import { Construct } from 'constructs';

export class CommonStack extends cdk.Stack {
  public readonly receiptsTable: dynamodb.Table;
  public readonly priceDropsTable: dynamodb.Table;
  public readonly receiptsBucket: s3.Bucket;
  public readonly lambdaEcrRepo: ecr.Repository;
  public readonly agentCoreEcrRepo: ecr.Repository;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // DynamoDB Tables
    this.receiptsTable = new dynamodb.Table(this, 'ReceiptsTable', {
      partitionKey: { name: 'receipt_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.priceDropsTable = new dynamodb.Table(this, 'PriceDropsTable', {
      partitionKey: { name: 'item_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // S3 Bucket for receipts with unique name
    this.receiptsBucket = new s3.Bucket(this, 'ReceiptsBucket', {
      versioned: false,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ECR Repositories - let CDK generate names
    this.lambdaEcrRepo = new ecr.Repository(this, 'LambdaEcrRepo', {
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.agentCoreEcrRepo = new ecr.Repository(this, 'AgentCoreEcrRepo', {
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Outputs
    new cdk.CfnOutput(this, 'ReceiptsTableName', {
      value: this.receiptsTable.tableName,
      exportName: `${this.stackName}-ReceiptsTable`,
    });

    new cdk.CfnOutput(this, 'PriceDropsTableName', {
      value: this.priceDropsTable.tableName,
      exportName: `${this.stackName}-PriceDropsTable`,
    });

    new cdk.CfnOutput(this, 'ReceiptsBucketName', {
      value: this.receiptsBucket.bucketName,
      exportName: `${this.stackName}-ReceiptsBucket`,
    });
  }
}
