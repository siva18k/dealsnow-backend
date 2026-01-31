# DealsNow Backend - Consolidated Infrastructure

## Overview

This is the consolidated backend infrastructure for DealsNow, managing all AWS resources (Lambda functions, API Gateways, S3 buckets, IAM roles, and Secrets) for both US and India deployments.

## Project Structure

```
dealsnow-backend-consolidated/
├── cdk-stack/                  # CDK Infrastructure as Code
│   ├── bin/
│   │   └── dealsnow-stack.ts  # CDK app entry point
│   ├── lib/
│   │   └── dealsnow-backend-stack.ts  # Main stack definition
│   ├── package.json
│   ├── cdk.json
│   └── tsconfig.json
├── lambda-functions/           # Lambda function source code
│   ├── manage_users.py
│   ├── bookmark_management.py
│   ├── update_product_data.py
│   ├── promo_master_management.py
│   ├── product_search_embedded.py
│   ├── lambda-products-management.py
│   ├── get_product_data.py
│   ├── get_product_data_rakuten.py
│   ├── get-product-data-amazon.py
│   ├── csv_import_products.py
│   └── update_promo_products_daily.py
└── docs/
    ├── BACKEND_ANALYSIS.md     # Detailed analysis
    └── DEPLOYMENT_GUIDE.md     # This file
```

## Resources Created

### Lambda Functions (with `dealsnow-` prefix)

| Function Name | Purpose | Runtime |
|--------------|---------|---------|
| `dealsnow-user-management-{region}` | User auth, registration, profile | Python 3.13 |
| `dealsnow-bookmark-management-{region}` | Bookmark operations | Python 3.13 |
| `dealsnow-product-update-{region}` | Update product data | Python 3.13 |
| `dealsnow-product-management-{region}` | Product CRUD with auth | Python 3.13 |
| `dealsnow-csv-import-{region}` | Bulk product import | Python 3.13 |
| `dealsnow-product-search-{region}` | Product search | Python 3.13 |
| `dealsnow-promo-management-{region}` | Promo campaigns | Python 3.13 |
| `dealsnow-promo-update-daily-{region}` | Daily promo updates | Python 3.13 |
| `dealsnow-product-data-fetch-{region}` | Generic product fetch | Python 3.13 |
| `dealsnow-rakuten-integration-{region}` | Rakuten API | Python 3.13 |
| `dealsnow-amazon-integration-{region}` | Amazon integration | Python 3.13 |

### API Gateways

#### Main API: `dealsnow-api-main-{region}`
- **Stage:** production
- **Base URL:** `https://{api-id}.execute-api.{region}.amazonaws.com/production`
- **Endpoints:**
  - `GET,POST /products` - Product listings
  - `GET /products_web` - Web product display
  - `GET /product` - Single product
  - `GET,POST /products_management` - Product management
  - `GET,POST /products_search_embeded` - Search
  - `GET,POST /get_amazon_products_by_url` - Amazon products

#### Staging API: `dealsnow-api-staging-{region}`
- **Stage:** staging
- **Base URL:** `https://{api-id}.execute-api.{region}.amazonaws.com/staging`
- **Endpoints:**
  - `GET,POST /signup` - User signup
  - `GET,POST /bookmark_management` - Bookmarks
  - `POST /submit_deals` - Submit deals
  - `GET,POST,PUT /promo_management` - Promos
  - `POST /get_product_data_rakuten` - Rakuten

### IAM Roles
- `dealsnow-lambda-role-us` - Lambda execution role (US)
- `dealsnow-lambda-role-india` - Lambda execution role (India)

### S3 Buckets (Existing)
- `dealsnow-data` (us-east-2)
- `dealsnow-india` (ap-south-1)

### Secrets Manager (Existing)
- `prod/dealsnow_master/aurora_db` - US database
- `prod/dealsnow_india/aurora_db` - India database
- `dealsnow/amazon/paapi` - Amazon API credentials

## Prerequisites

1. **AWS CLI** configured with appropriate credentials
2. **Node.js** 18+ and npm
3. **AWS CDK** installed globally: `npm install -g aws-cdk`
4. **TypeScript** installed
5. **AWS Account** with appropriate permissions

## Installation

```bash
cd dealsnow-backend-consolidated/cdk-stack
npm install
```

## Deployment

### 1. Bootstrap CDK (First Time Only)

```bash
# Bootstrap US region
cdk bootstrap aws://ACCOUNT-ID/us-east-2

# Bootstrap India region
cdk bootstrap aws://ACCOUNT-ID/ap-south-1
```

### 2. Synthesize CloudFormation Template

```bash
# View what will be created
cdk synth

# View specific stack
cdk synth DealsNowBackendStack-US
cdk synth DealsNowBackendStack-India
```

### 3. Deploy Stacks

```bash
# Deploy US stack only
cdk deploy DealsNowBackendStack-US

# Deploy India stack only
cdk deploy DealsNowBackendStack-India

# Deploy both stacks
cdk deploy --all

# Deploy with auto-approval (CI/CD)
cdk deploy --all --require-approval never
```

### 4. Deploy Specific Lambda Function

To deploy only a specific Lambda function that changed:

```bash
# This will only update the changed resources
cdk deploy DealsNowBackendStack-US --exclusively

# Or use hotswap for faster Lambda-only updates (dev only)
cdk deploy DealsNowBackendStack-US --hotswap
```

## CI/CD Pipeline

### GitHub Actions Example

```yaml
name: Deploy Backend

on:
  push:
    branches: [main]
    paths:
      - 'dealsnow-backend-consolidated/**'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: '18'
      
      - name: Install dependencies
        run: |
          cd dealsnow-backend-consolidated/cdk-stack
          npm ci
      
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v2
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-east-2
      
      - name: Deploy US Stack
        run: |
          cd dealsnow-backend-consolidated/cdk-stack
          npx cdk deploy DealsNowBackendStack-US --require-approval never
      
      - name: Deploy India Stack
        run: |
          cd dealsnow-backend-consolidated/cdk-stack
          npx cdk deploy DealsNowBackendStack-India --require-approval never
```

## Environment Variables

Lambda functions receive these environment variables:

- `DB_SECRET_NAME` - Secrets Manager secret name for database
- `DB_SCHEMA` - Database schema (deals_master or deals_india)
- `REGION` - Deployment region (us or india)
- `S3_BUCKET` - S3 bucket name
- `DEPLOYMENT_REGION` - Deployment region identifier

## Updating Frontend Apps

After deployment, update the API endpoints in your frontend applications:

### dealsnow-app (Flutter)

Update `lib/theme/us_theme.dart`:
```dart
static const String apiBaseUrl = 'https://{NEW-API-ID}.execute-api.us-east-2.amazonaws.com/production';
```

Update `lib/services/bookmark_sync_service.dart`:
```dart
static const String bookmarkApiUrl = 'https://{NEW-API-ID}.execute-api.us-east-2.amazonaws.com/staging/bookmark_management';
```

### dealsnow-aws (React/Vite)

Update `.env`:
```bash
VITE_API_BASE_URL="https://{NEW-API-ID}.execute-api.us-east-2.amazonaws.com/production"
VITE_API_STAGING_BASE_URL="https://{NEW-API-ID}.execute-api.us-east-2.amazonaws.com/staging"
```

## Monitoring and Logs

### View Lambda Logs

```bash
# View logs for a specific function
aws logs tail /aws/lambda/dealsnow-user-management-us --follow

# View logs from last hour
aws logs tail /aws/lambda/dealsnow-user-management-us --since 1h
```

### CloudWatch Metrics

All Lambda functions and API Gateways automatically send metrics to CloudWatch:
- Invocations
- Errors
- Duration
- Throttles
- API Gateway 4xx/5xx errors

## Cost Optimization

1. **Lambda**: Pay per invocation and duration
2. **API Gateway**: Pay per million requests
3. **CloudWatch Logs**: Set retention to 7 days (configurable in stack)
4. **S3**: Existing buckets, no additional cost

## Rollback

If deployment fails or issues occur:

```bash
# Rollback to previous version
aws cloudformation rollback-stack --stack-name dealsnow-backend-us

# Or destroy and redeploy
cdk destroy DealsNowBackendStack-US
cdk deploy DealsNowBackendStack-US
```

## Cleanup

To remove all resources:

```bash
# Destroy US stack
cdk destroy DealsNowBackendStack-US

# Destroy India stack
cdk destroy DealsNowBackendStack-India

# Destroy all stacks
cdk destroy --all
```

**Note:** This will NOT delete:
- S3 buckets (manual deletion required)
- Secrets Manager secrets (manual deletion required)
- CloudWatch log groups (manual deletion required)

## Troubleshooting

### Issue: Lambda function not updating

**Solution:** Use hotswap deployment:
```bash
cdk deploy DealsNowBackendStack-US --hotswap
```

### Issue: API Gateway CORS errors

**Solution:** CORS is pre-configured in the stack. Verify the frontend is sending correct headers.

### Issue: Database connection errors

**Solution:** Verify:
1. Secrets Manager secrets exist
2. Lambda has VPC access (if database is in VPC)
3. Security groups allow Lambda to access database

### Issue: Permission denied errors

**Solution:** Check IAM role has necessary permissions:
```bash
aws iam get-role --role-name dealsnow-lambda-role-us
```

## Support

For issues or questions:
1. Check CloudWatch Logs
2. Review `docs/BACKEND_ANALYSIS.md`
3. Contact the development team

## License

Proprietary - DealsNow, LLC
