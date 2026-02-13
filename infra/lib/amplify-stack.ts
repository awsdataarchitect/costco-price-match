import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as amplify from '@aws-cdk/aws-amplify-alpha';
import { Construct } from 'constructs';
import { CommonStack } from './common-stack';

interface AmplifyStackProps extends cdk.StackProps {
  commonStack: CommonStack;
}

export class AmplifyStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly webAppClient: cognito.UserPoolClient;
  public readonly iosAppClient: cognito.UserPoolClient;
  public readonly lambdaFunction: lambda.Function;
  public readonly httpApi: apigateway.HttpApi;
  public readonly amplifyApp: amplify.App;

  constructor(scope: Construct, id: string, props: AmplifyStackProps) {
    super(scope, id, props);

    const { commonStack } = props;

    // Cognito User Pool with self-signup enabled
    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'costco-scanner-users',
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      autoVerify: { email: true },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Web app client
    this.webAppClient = this.userPool.addClient('WebAppClient', {
      userPoolClientName: 'costco-scanner-web',
      generateSecret: false,
      authFlows: {
        userSrp: true,
        userPassword: true,
      },
    });

    // iOS app client
    this.iosAppClient = this.userPool.addClient('IosAppClient', {
      userPoolClientName: 'costco-scanner-ios',
      generateSecret: false,
      authFlows: {
        userSrp: true,
        userPassword: true,
      },
    });

    // Lambda IAM role
    const lambdaRole = new iam.Role(this, 'LambdaRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
      inlinePolicies: {
        DynamoDBAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'dynamodb:GetItem',
                'dynamodb:PutItem',
                'dynamodb:UpdateItem',
                'dynamodb:DeleteItem',
                'dynamodb:Query',
                'dynamodb:Scan',
                'dynamodb:BatchGetItem',
                'dynamodb:BatchWriteItem',
              ],
              resources: [
                commonStack.receiptsTable.tableArn,
                commonStack.priceDropsTable.tableArn,
              ],
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['dynamodb:ListTables'],
              resources: ['*'],
            }),
          ],
        }),
        S3Access: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                's3:GetObject',
                's3:PutObject',
                's3:DeleteObject',
              ],
              resources: [`${commonStack.receiptsBucket.bucketArn}/*`],
            }),
          ],
        }),
        BedrockAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'bedrock:InvokeModel',
                'bedrock:InvokeModelWithResponseStream',
                'bedrock:Converse',
                'bedrock:ConverseStream',
              ],
              resources: [
                'arn:aws:bedrock:*::foundation-model/*',
                `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
              ],
            }),
          ],
        }),
        NotifyAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['ses:SendEmail', 'ses:SendRawEmail'],
              resources: ['*'],
            }),
          ],
        }),
      },
    });

    // Lambda function using CDK Docker build
    this.lambdaFunction = new lambda.DockerImageFunction(this, 'ApiFunction', {
      functionName: 'costco-scanner-api',
      code: lambda.DockerImageCode.fromImageAsset('../', {
        file: 'lambda.Dockerfile',
      }),
      architecture: lambda.Architecture.ARM_64,
      role: lambdaRole,
      timeout: cdk.Duration.seconds(300),
      memorySize: 1024,
      environment: {
        DYNAMODB_RECEIPTS_TABLE: commonStack.receiptsTable.tableName,
        DYNAMODB_PRICE_DROPS_TABLE: commonStack.priceDropsTable.tableName,
        S3_BUCKET: commonStack.receiptsBucket.bucketName,
      },
    });

    // JWT Authorizer
    const jwtAuthorizer = new authorizers.HttpJwtAuthorizer('JwtAuthorizer', 
      `https://cognito-idp.${this.region}.amazonaws.com/${this.userPool.userPoolId}`,
      {
        jwtAudience: [this.webAppClient.userPoolClientId],
      }
    );

    // HTTP API Gateway
    this.httpApi = new apigateway.HttpApi(this, 'HttpApi', {
      apiName: 'costco-scanner-api',
      corsPreflight: {
        allowOrigins: ['*'],
        allowMethods: [apigateway.CorsHttpMethod.ANY],
        allowHeaders: ['*'],
      },
    });

    // Lambda integration
    const lambdaIntegration = new integrations.HttpLambdaIntegration('LambdaIntegration', this.lambdaFunction);

    // Routes with JWT auth
    this.httpApi.addRoutes({
      path: '/{proxy+}',
      methods: [apigateway.HttpMethod.ANY],
      integration: lambdaIntegration,
      authorizer: jwtAuthorizer,
    });

    // OPTIONS route without auth (CORS preflight)
    this.httpApi.addRoutes({
      path: '/{proxy+}',
      methods: [apigateway.HttpMethod.OPTIONS],
      integration: lambdaIntegration,
    });

    // Amplify App
    this.amplifyApp = new amplify.App(this, 'AmplifyApp', {
      appName: 'costco-scanner',
      description: 'Costco Receipt Scanner & Price Match',
    });

    this.amplifyApp.addBranch('main');

    // Outputs
    new cdk.CfnOutput(this, 'UserPoolId', {
      value: this.userPool.userPoolId,
      exportName: `${this.stackName}-UserPoolId`,
    });

    new cdk.CfnOutput(this, 'WebAppClientId', {
      value: this.webAppClient.userPoolClientId,
      exportName: `${this.stackName}-WebAppClientId`,
    });

    new cdk.CfnOutput(this, 'IosAppClientId', {
      value: this.iosAppClient.userPoolClientId,
      exportName: `${this.stackName}-IosAppClientId`,
    });

    new cdk.CfnOutput(this, 'ApiUrl', {
      value: this.httpApi.apiEndpoint,
      exportName: `${this.stackName}-ApiUrl`,
    });

    new cdk.CfnOutput(this, 'AmplifyAppUrl', {
      value: `https://main.${this.amplifyApp.appId}.amplifyapp.com`,
      exportName: `${this.stackName}-AmplifyAppUrl`,
    });
  }
}
