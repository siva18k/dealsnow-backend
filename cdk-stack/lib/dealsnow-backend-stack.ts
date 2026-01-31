import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import * as path from 'path';

export interface DealsNowBackendStackProps extends cdk.StackProps {
  deploymentRegion: 'us' | 'india';
  dbSchema: string;
  dbSecretName: string;
}

export class DealsNowBackendStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: DealsNowBackendStackProps) {
    super(scope, id, props);

    const { deploymentRegion, dbSchema, dbSecretName } = props;

    // ========================================================================
    // 1. SECRETS AND CONFIGURATION
    // ========================================================================
    
    // Database secret
    const dbSecret = secretsmanager.Secret.fromSecretNameV2(
      this,
      'DatabaseSecret',
      dbSecretName
    );

    // Amazon API secret (US only)
    const amazonSecret = deploymentRegion === 'us' 
      ? secretsmanager.Secret.fromSecretNameV2(this, 'AmazonSecret', 'dealsnow/amazon/paapi')
      : undefined;

    // ========================================================================
    // 2. S3 BUCKETS
    // ========================================================================
    
    const bucketName = deploymentRegion === 'us' ? 'dealsnow-data' : 'dealsnow-india';
    const dataBucket = s3.Bucket.fromBucketName(this, 'DataBucket', bucketName);

    // ========================================================================
    // 3. IAM ROLE FOR LAMBDA FUNCTIONS
    // ========================================================================
    
    const lambdaRole = new iam.Role(this, 'dealsnow-lambda-execution-role', {
      roleName: `dealsnow-lambda-role-${deploymentRegion}`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: `Execution role for DealsNow Lambda functions in ${deploymentRegion}`,
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaVPCAccessExecutionRole'),
      ],
    });

    // Grant access to Secrets Manager
    lambdaRole.addToPolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: [
        dbSecret.secretArn,
        ...(amazonSecret ? [amazonSecret.secretArn] : []),
        `arn:aws:secretsmanager:${this.region}:${this.account}:secret:dealsnow/*`,
        `arn:aws:secretsmanager:${this.region}:${this.account}:secret:prod/dealsnow*`,
      ],
    }));

    // Grant S3 access
    dataBucket.grantReadWrite(lambdaRole);

    // Grant CloudWatch Logs access
    lambdaRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        'logs:CreateLogGroup',
        'logs:CreateLogStream',
        'logs:PutLogEvents',
      ],
      resources: ['*'],
    }));

    // ========================================================================
    // 4. COMMON ENVIRONMENT VARIABLES
    // ========================================================================
    
    const commonEnv = {
      DB_SECRET_NAME: dbSecretName,
      DB_SCHEMA: dbSchema,
      REGION: deploymentRegion,
      S3_BUCKET: bucketName,
      DEPLOYMENT_REGION: deploymentRegion,
    };

    // ========================================================================
    // 5. LAMBDA FUNCTIONS
    // ========================================================================
    
    const lambdaAssetPath = path.resolve(__dirname, '../../lambda-functions');

    // Helper function to create Lambda functions
    const createFunction = (
      id: string,
      functionName: string,
      handler: string,
      description: string,
      timeout: number = 60,
      memorySize: number = 256,
      additionalEnv: Record<string, string> = {}
    ): lambda.Function => {
      return new lambda.Function(this, id, {
        functionName: `dealsnow-${functionName}-${deploymentRegion}`,
        runtime: lambda.Runtime.PYTHON_3_10,
        handler: handler,
        code: lambda.Code.fromAsset(lambdaAssetPath),
        role: lambdaRole,
        environment: { ...commonEnv, ...additionalEnv },
        timeout: cdk.Duration.seconds(timeout),
        memorySize: memorySize,
        description: description,
        logRetention: 7, // days
      });
    };

    // User Management
    const manageUsersFn = createFunction(
      'ManageUsersFunction',
      'user-management',
      'manage_users.lambda_handler',
      'User authentication, registration, and profile management'
    );

    // Bookmark Management
    const bookmarkFn = createFunction(
      'BookmarkFunction',
      'bookmark-management',
      'bookmark_management.lambda_handler',
      'User bookmark operations (add, remove, list)'
    );

    // Product Data Management
    const updateProductFn = createFunction(
      'UpdateProductFunction',
      'product-update',
      'update_product_data.lambda_handler',
      'Update product information in database',
      120,
      512
    );

    const productManagementFn = createFunction(
      'ProductManagementFunction',
      'product-management',
      'lambda-products-management.lambda_handler',
      'Product CRUD operations with authentication',
      90,
      512
    );

    const csvImportFn = createFunction(
      'CSVImportFunction',
      'csv-import',
      'csv_import_products.lambda_handler',
      'Bulk import products from CSV',
      300,
      1024
    );

    // Product Search
    const productSearchFn = createFunction(
      'ProductSearchFunction',
      'product-search',
      'product_search_embedded.lambda_handler',
      'Embedded product search functionality',
      30,
      512
    );

    // Promo Management
    const promoMasterFn = createFunction(
      'PromoMasterFunction',
      'promo-management',
      'promo_master_management.lambda_handler',
      'Manage promotional campaigns'
    );

    const updatePromoFn = createFunction(
      'UpdatePromoFunction',
      'promo-update-daily',
      'update_promo_products_daily.lambda_handler',
      'Daily promotional product updates',
      180,
      512
    );

    // External API Integrations
    const getProductDataFn = createFunction(
      'GetProductDataFunction',
      'product-data-fetch',
      'get_product_data.lambda_handler',
      'Generic product data fetching',
      90,
      512
    );

    const rakutenFn = createFunction(
      'RakutenFunction',
      'rakuten-integration',
      'get_product_data_rakuten.lambda_handler',
      'Fetch products from Rakuten API',
      90,
      512
    );

    const amazonFn = createFunction(
      'AmazonFunction',
      'amazon-integration',
      'get-product-data-amazon.lambda_handler',
      'Fetch products from Amazon',
      90,
      512
    );

    // ========================================================================
    // 6. API GATEWAY - MAIN API
    // ========================================================================
    
    const mainApi = new apigateway.RestApi(this, 'DealsNowMainAPI', {
      restApiName: `dealsnow-api-main-${deploymentRegion}`,
      description: `DealsNow Main API - ${deploymentRegion.toUpperCase()}`,
      deployOptions: {
        stageName: 'production',
        throttlingRateLimit: 1000,
        throttlingBurstLimit: 2000,
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        dataTraceEnabled: true,
        metricsEnabled: true,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: [
          'Content-Type',
          'Authorization',
          'X-Api-Key',
          'X-Country-Code',
          'X-Amz-Date',
          'X-Amz-Security-Token',
        ],
        allowCredentials: true,
      },
    });

    // /products
    const products = mainApi.root.addResource('products');
    products.addMethod('GET', new apigateway.LambdaIntegration(getProductDataFn));
    products.addMethod('POST', new apigateway.LambdaIntegration(getProductDataFn));

    // /products_web
    const productsWeb = mainApi.root.addResource('products_web');
    productsWeb.addMethod('GET', new apigateway.LambdaIntegration(getProductDataFn));

    // /product
    const product = mainApi.root.addResource('product');
    product.addMethod('GET', new apigateway.LambdaIntegration(getProductDataFn));

    // /products_management
    const productsManagement = mainApi.root.addResource('products_management');
    productsManagement.addMethod('GET', new apigateway.LambdaIntegration(productManagementFn));
    productsManagement.addMethod('POST', new apigateway.LambdaIntegration(productManagementFn));

    // /products_search_embeded
    const productsSearch = mainApi.root.addResource('products_search_embeded');
    productsSearch.addMethod('GET', new apigateway.LambdaIntegration(productSearchFn));
    productsSearch.addMethod('POST', new apigateway.LambdaIntegration(productSearchFn));

    // /get_amazon_products_by_url
    const amazonProducts = mainApi.root.addResource('get_amazon_products_by_url');
    amazonProducts.addMethod('GET', new apigateway.LambdaIntegration(amazonFn));
    amazonProducts.addMethod('POST', new apigateway.LambdaIntegration(amazonFn));

    // ========================================================================
    // 7. API GATEWAY - STAGING/UPDATE API
    // ========================================================================
    
    const stagingApi = new apigateway.RestApi(this, 'DealsNowStagingAPI', {
      restApiName: `dealsnow-api-staging-${deploymentRegion}`,
      description: `DealsNow Staging/Update API - ${deploymentRegion.toUpperCase()}`,
      deployOptions: {
        stageName: 'staging',
        throttlingRateLimit: 500,
        throttlingBurstLimit: 1000,
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: [
          'Content-Type',
          'Authorization',
          'X-Api-Key',
          'X-Country-Code',
          'Accept',
          'X-Amz-Date',
          'X-Amz-Security-Token',
        ],
      },
    });

    // /signup
    const signup = stagingApi.root.addResource('signup');
    signup.addMethod('GET', new apigateway.LambdaIntegration(manageUsersFn));
    signup.addMethod('POST', new apigateway.LambdaIntegration(manageUsersFn));

    // /bookmark_management
    const bookmarks = stagingApi.root.addResource('bookmark_management');
    bookmarks.addMethod('GET', new apigateway.LambdaIntegration(bookmarkFn));
    bookmarks.addMethod('POST', new apigateway.LambdaIntegration(bookmarkFn));

    // /submit_deals
    const submitDeals = stagingApi.root.addResource('submit_deals');
    submitDeals.addMethod('POST', new apigateway.LambdaIntegration(updateProductFn));

    // /promo_management
    const promo = stagingApi.root.addResource('promo_management');
    promo.addMethod('GET', new apigateway.LambdaIntegration(promoMasterFn));
    promo.addMethod('POST', new apigateway.LambdaIntegration(promoMasterFn));
    promo.addMethod('PUT', new apigateway.LambdaIntegration(promoMasterFn));

    // /get_product_data_rakuten
    const rakuten = stagingApi.root.addResource('get_product_data_rakuten');
    rakuten.addMethod('POST', new apigateway.LambdaIntegration(rakutenFn));

    // ========================================================================
    // 8. OUTPUTS
    // ========================================================================
    
    new cdk.CfnOutput(this, 'MainAPIUrl', {
      value: mainApi.url,
      description: 'Main API Gateway URL',
      exportName: `dealsnow-main-api-url-${deploymentRegion}`,
    });

    new cdk.CfnOutput(this, 'StagingAPIUrl', {
      value: stagingApi.url,
      description: 'Staging API Gateway URL',
      exportName: `dealsnow-staging-api-url-${deploymentRegion}`,
    });

    new cdk.CfnOutput(this, 'DataBucketName', {
      value: bucketName,
      description: 'S3 Data Bucket Name',
      exportName: `dealsnow-data-bucket-${deploymentRegion}`,
    });

    new cdk.CfnOutput(this, 'LambdaRoleArn', {
      value: lambdaRole.roleArn,
      description: 'Lambda Execution Role ARN',
      exportName: `dealsnow-lambda-role-arn-${deploymentRegion}`,
    });
  }
}
