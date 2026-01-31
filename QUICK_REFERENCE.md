# DealsNow Backend - Quick Reference

## 🚀 Quick Commands

### Setup
```bash
cd dealsnow-backend-consolidated/cdk-stack
npm install
```

### Test/Validate
```bash
# Synthesize CloudFormation template
cdk synth

# Check what will change
cdk diff DealsNowBackendStack-US
cdk diff DealsNowBackendStack-India
```

### Deploy
```bash
# Deploy US stack
cdk deploy DealsNowBackendStack-US

# Deploy India stack
cdk deploy DealsNowBackendStack-India

# Deploy both
cdk deploy --all

# Auto-approve (CI/CD)
cdk deploy --all --require-approval never

# Fast Lambda update (dev only)
cdk deploy DealsNowBackendStack-US --hotswap
```

### Monitor
```bash
# View Lambda logs
aws logs tail /aws/lambda/dealsnow-user-management-us --follow

# View API Gateway logs
aws logs tail /aws/apigateway/dealsnow-api-main-us --follow

# List all DealsNow Lambda functions
aws lambda list-functions --region us-east-2 | grep dealsnow
```

### Cleanup
```bash
# Destroy stack
cdk destroy DealsNowBackendStack-US
cdk destroy DealsNowBackendStack-India
```

## 📋 Resource Naming Convention

| Resource Type | Pattern | Example |
|--------------|---------|---------|
| Lambda Function | `dealsnow-{function}-{region}` | `dealsnow-user-management-us` |
| API Gateway | `dealsnow-api-{type}-{region}` | `dealsnow-api-main-us` |
| IAM Role | `dealsnow-lambda-role-{region}` | `dealsnow-lambda-role-us` |
| S3 Bucket | `dealsnow-{purpose}` | `dealsnow-data` |
| Secret | `dealsnow/{category}/{name}` | `dealsnow/amazon/paapi` |

## 🔗 API Endpoints

### Main API (Production)
```
Base URL: https://{api-id}.execute-api.{region}.amazonaws.com/production

GET,POST  /products                    - Product listings
GET       /products_web                - Web products
GET       /product                     - Single product
GET,POST  /products_management         - Product management
GET,POST  /products_search_embeded     - Search
GET,POST  /get_amazon_products_by_url  - Amazon products
```

### Staging API (Admin/Updates)
```
Base URL: https://{api-id}.execute-api.{region}.amazonaws.com/staging

GET,POST  /signup                      - User signup
GET,POST  /bookmark_management         - Bookmarks
POST      /submit_deals                - Submit deals
GET,POST,PUT /promo_management         - Promos
POST      /get_product_data_rakuten    - Rakuten
```

## 🗂️ Lambda Functions

| Function | Purpose | Timeout | Memory |
|----------|---------|---------|--------|
| user-management | Auth, registration, profile | 60s | 256MB |
| bookmark-management | Bookmark operations | 60s | 256MB |
| product-update | Update product data | 120s | 512MB |
| product-management | Product CRUD | 90s | 512MB |
| csv-import | Bulk import | 300s | 1024MB |
| product-search | Search functionality | 30s | 512MB |
| promo-management | Promo campaigns | 60s | 256MB |
| promo-update-daily | Daily promo updates | 180s | 512MB |
| product-data-fetch | Generic fetch | 90s | 512MB |
| rakuten-integration | Rakuten API | 90s | 512MB |
| amazon-integration | Amazon integration | 90s | 512MB |

## 🔐 Environment Variables

All Lambda functions receive:
- `DB_SECRET_NAME` - Database secret name
- `DB_SCHEMA` - Database schema (deals_master/deals_india)
- `REGION` - Deployment region (us/india)
- `S3_BUCKET` - S3 bucket name
- `DEPLOYMENT_REGION` - Region identifier

## 📱 Frontend Integration

### dealsnow-app (Flutter)

**Files to update:**
- `lib/theme/us_theme.dart`
- `lib/theme/india_theme.dart`
- `lib/services/bookmark_sync_service.dart`

**Example:**
```dart
static const String apiBaseUrl = 
  'https://{API-ID}.execute-api.us-east-2.amazonaws.com/production';
```

### dealsnow-aws (React/Vite)

**Files to update:**
- `.env`
- `.env.india`

**Example:**
```bash
VITE_API_BASE_URL="https://{API-ID}.execute-api.us-east-2.amazonaws.com/production"
VITE_API_STAGING_BASE_URL="https://{API-ID}.execute-api.us-east-2.amazonaws.com/staging"
```

## 🔍 Troubleshooting

### Lambda not updating
```bash
cdk deploy DealsNowBackendStack-US --hotswap
```

### View CloudFormation stack
```bash
aws cloudformation describe-stacks --stack-name dealsnow-backend-us
```

### Check API Gateway
```bash
aws apigateway get-rest-apis --region us-east-2 | grep dealsnow
```

### Test API endpoint
```bash
curl https://{API-ID}.execute-api.us-east-2.amazonaws.com/production/products
```

### View Lambda function
```bash
aws lambda get-function --function-name dealsnow-user-management-us
```

## 📊 Monitoring

### CloudWatch Dashboards
- Lambda invocations, errors, duration
- API Gateway requests, 4xx, 5xx errors
- Custom metrics

### Alarms (Recommended)
```bash
# Lambda errors
aws cloudwatch put-metric-alarm \
  --alarm-name dealsnow-lambda-errors \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --statistic Sum \
  --period 300 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold

# API Gateway 5xx errors
aws cloudwatch put-metric-alarm \
  --alarm-name dealsnow-api-5xx \
  --metric-name 5XXError \
  --namespace AWS/ApiGateway \
  --statistic Sum \
  --period 300 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold
```

## 💰 Cost Monitoring

```bash
# View current month costs
aws ce get-cost-and-usage \
  --time-period Start=2026-01-01,End=2026-01-31 \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --filter file://filter.json

# filter.json
{
  "Tags": {
    "Key": "Project",
    "Values": ["DealsNow"]
  }
}
```

## 🔄 CI/CD Integration

### GitHub Actions
```yaml
- name: Deploy Backend
  run: |
    cd dealsnow-backend-consolidated/cdk-stack
    npm ci
    npx cdk deploy --all --require-approval never
```

### GitLab CI
```yaml
deploy:
  script:
    - cd dealsnow-backend-consolidated/cdk-stack
    - npm ci
    - npx cdk deploy --all --require-approval never
```

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| [README.md](README.md) | Project overview |
| [SUMMARY.md](SUMMARY.md) | Completion summary |
| [docs/BACKEND_ANALYSIS.md](docs/BACKEND_ANALYSIS.md) | Resource analysis |
| [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) | Deployment instructions |
| [docs/MIGRATION_PLAN.md](docs/MIGRATION_PLAN.md) | Migration strategy |

## ⚡ Common Tasks

### Update a single Lambda function
```bash
# 1. Modify lambda-functions/{function}.py
# 2. Deploy
cdk deploy DealsNowBackendStack-US --hotswap
```

### Add a new Lambda function
```bash
# 1. Add .py file to lambda-functions/
# 2. Update lib/dealsnow-backend-stack.ts
# 3. Add API Gateway endpoint
# 4. Deploy
cdk deploy DealsNowBackendStack-US
```

### Change API Gateway configuration
```bash
# 1. Update lib/dealsnow-backend-stack.ts
# 2. Deploy
cdk deploy DealsNowBackendStack-US
```

### Update IAM permissions
```bash
# 1. Update lambdaRole in lib/dealsnow-backend-stack.ts
# 2. Deploy
cdk deploy DealsNowBackendStack-US
```

## 🆘 Emergency Contacts

- **CloudWatch Logs**: Check first for errors
- **AWS Support**: For infrastructure issues
- **Development Team**: For code issues

## 📞 Support Resources

- AWS CDK Documentation: https://docs.aws.amazon.com/cdk/
- AWS Lambda Documentation: https://docs.aws.amazon.com/lambda/
- AWS API Gateway Documentation: https://docs.aws.amazon.com/apigateway/

---

**Last Updated**: 2026-01-31
**Version**: 1.0.0
