import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as amplify from '@aws-cdk/aws-amplify-alpha';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import { CommonStack } from './common-stack';

interface AmplifyStackProps extends cdk.StackProps {
  commonStack: CommonStack;
  notifyEmail?: string;
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

    // Enable email OTP passwordless sign-in
    const cfnUserPool = this.userPool.node.defaultChild as cognito.CfnUserPool;
    cfnUserPool.addPropertyOverride('Policies.SignInPolicy.AllowedFirstAuthFactors', ['EMAIL_OTP', 'PASSWORD']);

    // Web app client
    this.webAppClient = this.userPool.addClient('WebAppClient', {
      userPoolClientName: 'costco-scanner-web',
      generateSecret: false,
      authFlows: {
        userSrp: true,
        userPassword: true,
        custom: true,
      },
    });

    // Add ALLOW_USER_AUTH (not exposed in L2 construct)
    const cfnWebClient = this.webAppClient.node.defaultChild as cognito.CfnUserPoolClient;
    cfnWebClient.addPropertyOverride('ExplicitAuthFlows', [
      'ALLOW_USER_AUTH',
      'ALLOW_USER_SRP_AUTH',
      'ALLOW_USER_PASSWORD_AUTH',
      'ALLOW_REFRESH_TOKEN_AUTH',
    ]);

    // iOS app client
    this.iosAppClient = this.userPool.addClient('IosAppClient', {
      userPoolClientName: 'costco-scanner-ios',
      generateSecret: false,
      authFlows: {
        userSrp: true,
        userPassword: true,
        custom: true,
      },
    });

    const cfnIosClient = this.iosAppClient.node.defaultChild as cognito.CfnUserPoolClient;
    cfnIosClient.addPropertyOverride('ExplicitAuthFlows', [
      'ALLOW_USER_AUTH',
      'ALLOW_USER_SRP_AUTH',
      'ALLOW_USER_PASSWORD_AUTH',
      'ALLOW_REFRESH_TOKEN_AUTH',
    ]);

    // App credentials in Secrets Manager (auto-generated password)
    const defaultEmail = props.notifyEmail || 'admin@costscanner.local';

    const appSecret = new secretsmanager.Secret(this, 'AppCredentials', {
      secretName: 'costco-scanner/app-credentials',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: defaultEmail }),
        generateStringKey: 'password',
        passwordLength: 24,
        excludePunctuation: false,
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Create default Cognito user via custom resource
    const createUser = new cr.AwsCustomResource(this, 'CreateDefaultUser', {
      onCreate: {
        service: 'CognitoIdentityServiceProvider',
        action: 'adminCreateUser',
        parameters: {
          UserPoolId: this.userPool.userPoolId,
          Username: defaultEmail,
          TemporaryPassword: appSecret.secretValueFromJson('password').unsafeUnwrap(),
          UserAttributes: [{ Name: 'email', Value: defaultEmail }, { Name: 'email_verified', Value: 'true' }],
          MessageAction: 'SUPPRESS',
        },
        physicalResourceId: cr.PhysicalResourceId.of('default-user'),
        ignoreErrorCodesMatching: 'UsernameExistsException',
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ['cognito-idp:AdminCreateUser'],
          resources: [this.userPool.userPoolArn],
        }),
      ]),
    });

    // Set permanent password
    const setPassword = new cr.AwsCustomResource(this, 'SetDefaultPassword', {
      onCreate: {
        service: 'CognitoIdentityServiceProvider',
        action: 'adminSetUserPassword',
        parameters: {
          UserPoolId: this.userPool.userPoolId,
          Username: defaultEmail,
          Password: appSecret.secretValueFromJson('password').unsafeUnwrap(),
          Permanent: true,
        },
        physicalResourceId: cr.PhysicalResourceId.of('default-user-password'),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ['cognito-idp:AdminSetUserPassword'],
          resources: [this.userPool.userPoolArn],
        }),
      ]),
    });
    setPassword.node.addDependency(createUser);

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
        USER_POOL_ID: this.userPool.userPoolId,
        USER_POOL_CLIENT_ID: this.webAppClient.userPoolClientId,
        APP_SECRET_ARN: appSecret.secretArn,
      },
    });

    // Grant Lambda access to Secrets Manager
    appSecret.grantRead(this.lambdaFunction);

    // JWT Authorizer
    const jwtAuthorizer = new authorizers.HttpJwtAuthorizer('JwtAuthorizer', 
      `https://cognito-idp.${this.region}.amazonaws.com/${this.userPool.userPoolId}`,
      {
        jwtAudience: [this.webAppClient.userPoolClientId, this.iosAppClient.userPoolClientId],
      }
    );

    // HTTP API Gateway
    this.httpApi = new apigateway.HttpApi(this, 'HttpApi', {
      apiName: 'costco-scanner-api',
      corsPreflight: {
        allowOrigins: ['https://costco.dunkinspeeps.com', 'http://localhost:8000'],
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

    // Unauthenticated config endpoint for iOS BYOI flow
    this.httpApi.addRoutes({
      path: '/api/config',
      methods: [apigateway.HttpMethod.GET],
      integration: lambdaIntegration,
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
