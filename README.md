# DealsNow Backend - Consolidated Infrastructure

## 🎯 Project Overview

This repository contains the **complete, consolidated backend infrastructure** for DealsNow, extracted from AWS and reorganized for unified deployment via AWS CDK.

**All resources now use the `dealsnow-` prefix** for easy identification and management.

## 📁 Repository Structure

```
dealsnow-backend-consolidated/
├── README.md                   # This file
├── cdk-stack/                  # AWS CDK Infrastructure
│   ├── bin/                    # CDK app entry point
│   ├── lib/                    # Stack definitions
│   ├── package.json
│   ├── cdk.json
│   └── tsconfig.json
├── lambda-functions/           # Lambda source code (Python 3.13)
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
    ├── BACKEND_ANALYSIS.md     # Detailed infrastructure analysis
    ├── DEPLOYMENT_GUIDE.md     # Deployment instructions
    └── MIGRATION_PLAN.md       # Migration strategy
```

## 🚀 Quick Start

### Prerequisites
- AWS CLI configured
- Node.js 18+
- AWS CDK installed: `npm install -g aws-cdk`

### Installation
```bash
cd cdk-stack
npm install
```

### Deploy
```bash
# Deploy US stack
cdk deploy DealsNowBackendStack-US

# Deploy India stack
cdk deploy DealsNowBackendStack-India

# Deploy both
cdk deploy --all
```

## 📊 What's Included

### ✅ Lambda Functions (11 total)
- User Management & Authentication
- Bookmark Management
- Product Data Management
- Product Search
- Promotional Management
- External API Integrations (Amazon, Rakuten)

### ✅ API Gateways (2 per region)
- **Main API** (`dealsnow-api-main-{region}`) - Production endpoints
- **Staging API** (`dealsnow-api-staging-{region}`) - Update/admin endpoints

### ✅ IAM Roles
- Unified Lambda execution role per region
- Proper permissions for Secrets Manager, S3, CloudWatch

### ✅ Integration with Existing Resources
- S3 Buckets: `dealsnow-data`, `dealsnow-india`
- Secrets: Database credentials, API keys
- Databases: Aurora PostgreSQL (US & India)

## 🎨 Key Features

### 1. **Consistent Naming Convention**
All resources use `dealsnow-` prefix:
- Lambda: `dealsnow-user-management-us`
- API Gateway: `dealsnow-api-main-us`
- IAM Roles: `dealsnow-lambda-role-us`

### 2. **Multi-Region Support**
- US Region: `us-east-2`
- India Region: `ap-south-1`
- Separate stacks for isolation

### 3. **CI/CD Ready**
- Deploy entire stack or individual functions
- GitHub Actions workflow included
- Hotswap support for fast Lambda updates

### 4. **Production Ready**
- CloudWatch logging (7-day retention)
- API throttling configured
- CORS pre-configured
- Proper error handling

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [BACKEND_ANALYSIS.md](docs/BACKEND_ANALYSIS.md) | Complete analysis of extracted AWS resources |
| [DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) | Step-by-step deployment instructions |
| [MIGRATION_PLAN.md](docs/MIGRATION_PLAN.md) | Strategy for migrating from old to new infrastructure |

## 🔄 Frontend Integration

After deployment, update API endpoints in:

### dealsnow-app (Flutter)
```dart
// lib/theme/us_theme.dart
static const String apiBaseUrl = 'https://{API-ID}.execute-api.us-east-2.amazonaws.com/production';
```

### dealsnow-aws (React/Vite)
```bash
# .env
VITE_API_BASE_URL="https://{API-ID}.execute-api.us-east-2.amazonaws.com/production"
```

## 🛠️ Development Workflow

### Deploy Specific Lambda
```bash
# Only deploy changed resources
cdk deploy DealsNowBackendStack-US --exclusively

# Fast Lambda-only update (dev)
cdk deploy DealsNowBackendStack-US --hotswap
```

### View Changes Before Deploy
```bash
cdk diff DealsNowBackendStack-US
```

### Monitor Logs
```bash
aws logs tail /aws/lambda/dealsnow-user-management-us --follow
```

## 📦 Deployment Outputs

After deployment, you'll get:

```
Outputs:
DealsNowBackendStack-US.MainAPIUrl = https://abc123.execute-api.us-east-2.amazonaws.com/production/
DealsNowBackendStack-US.StagingAPIUrl = https://def456.execute-api.us-east-2.amazonaws.com/staging/
DealsNowBackendStack-US.DataBucketName = dealsnow-data
DealsNowBackendStack-US.LambdaRoleArn = arn:aws:iam::123456789:role/dealsnow-lambda-role-us
```

## 🔐 Security

- All database credentials stored in AWS Secrets Manager
- IAM roles follow least-privilege principle
- API Gateway with throttling enabled
- CloudWatch logging for audit trail

## 💰 Cost Estimation

- **Lambda**: ~$0.20 per 1M requests
- **API Gateway**: ~$3.50 per 1M requests
- **CloudWatch Logs**: ~$0.50/GB
- **Total estimated**: $50-200/month depending on traffic

## 🚨 Important Notes

1. **DO NOT deploy yet** - Review all configurations first
2. **Backup current setup** before migration
3. **Test in staging** environment first
4. **Update frontend apps** after deployment
5. **Monitor CloudWatch** during migration

## 📞 Support

For questions or issues:
1. Check `docs/` directory
2. Review CloudWatch Logs
3. Contact development team

## 📝 License

Proprietary - DealsNow, LLC

---

**Status**: ✅ Ready for Review - DO NOT DEPLOY YET

**Next Steps**:
1. Review all code and configurations
2. Test CDK synthesis: `cdk synth`
3. Plan migration timeline
4. Update frontend configurations
5. Deploy to staging first
