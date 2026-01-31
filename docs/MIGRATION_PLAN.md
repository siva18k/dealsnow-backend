# DealsNow Backend Migration Plan

## Executive Summary

This document outlines the strategy for migrating from the current scattered backend infrastructure to the new consolidated CDK-managed stack with proper `dealsnow-` naming conventions.

## Current State

### Existing Resources

**Lambda Functions (inconsistent naming):**
- `manage_users`
- `bookmark_management`
- `update_product_data`
- `promo_master_management`
- `get_product_data`
- `get_product_data_rakuten`
- `get-product-data-amazon`
- `lambda-products-management`
- `product_search_embedded`
- `update_promo_products_daily`
- `csv_import_products`

**API Gateways:**
- `products_api` (h0w993oije) - us-east-2
- `product_ap_update` (o2vjydhm1e) - us-east-2

**S3 Buckets:**
- `dealsnow-data` (us-east-2)
- `dealsnow-india` (ap-south-1)

**Secrets:**
- `prod/dealsnow_master/aurora_db`
- `prod/dealsnow_india/aurora_db`
- `dealsnow/amazon/paapi`

### Issues with Current Setup

1. **Inconsistent Naming**: Mix of underscores, hyphens, no prefix
2. **Manual Management**: Resources created/updated manually
3. **No Version Control**: Infrastructure changes not tracked
4. **Difficult Deployment**: No CI/CD pipeline
5. **Hard to Identify**: Resources not easily identifiable as DealsNow

## Target State

### New Resources (with `dealsnow-` prefix)

**Lambda Functions:**
- `dealsnow-user-management-{region}`
- `dealsnow-bookmark-management-{region}`
- `dealsnow-product-update-{region}`
- `dealsnow-product-management-{region}`
- `dealsnow-csv-import-{region}`
- `dealsnow-product-search-{region}`
- `dealsnow-promo-management-{region}`
- `dealsnow-promo-update-daily-{region}`
- `dealsnow-product-data-fetch-{region}`
- `dealsnow-rakuten-integration-{region}`
- `dealsnow-amazon-integration-{region}`

**API Gateways:**
- `dealsnow-api-main-{region}` (production stage)
- `dealsnow-api-staging-{region}` (staging stage)

**IAM Roles:**
- `dealsnow-lambda-role-{region}`

### Benefits

1. ✅ **Consistent Naming**: All resources use `dealsnow-` prefix
2. ✅ **Infrastructure as Code**: CDK manages everything
3. ✅ **Version Control**: All changes tracked in Git
4. ✅ **CI/CD Ready**: Automated deployments
5. ✅ **Easy Identification**: Clear ownership
6. ✅ **Multi-Region**: Separate stacks for US and India

## Migration Strategy

### Phase 1: Preparation (Week 1)

#### Tasks:
1. ✅ Extract Lambda code from AWS
2. ✅ Create CDK stack structure
3. ✅ Document all resources
4. ⏳ Review and test CDK stack locally
5. ⏳ Set up staging environment

#### Deliverables:
- CDK stack code
- Documentation
- Migration plan (this document)

### Phase 2: Staging Deployment (Week 2)

#### Tasks:
1. Deploy new stack to staging/test environment
2. Test all Lambda functions
3. Test all API endpoints
4. Verify database connectivity
5. Load test API Gateway
6. Monitor CloudWatch logs

#### Success Criteria:
- All Lambda functions execute successfully
- All API endpoints return expected responses
- No errors in CloudWatch logs
- Performance meets or exceeds current setup

### Phase 3: Frontend Updates (Week 2-3)

#### dealsnow-app (Flutter)

**Files to Update:**
```
lib/theme/us_theme.dart
lib/theme/india_theme.dart
lib/services/api_service.dart
lib/services/bookmark_sync_service.dart
lib/services/user_sync_service.dart
```

**Changes:**
```dart
// OLD
static const String apiBaseUrl = 
  'https://h0w993oije.execute-api.us-east-2.amazonaws.com/initial';

// NEW
static const String apiBaseUrl = 
  'https://{NEW-API-ID}.execute-api.us-east-2.amazonaws.com/production';
```

#### dealsnow-aws (React/Vite)

**Files to Update:**
```
.env
.env.india
src/services/bookmarkService.js
src/pages/ManageDeals.jsx
src/pages/SharePreview.jsx
```

**Changes:**
```bash
# OLD
VITE_API_BASE_URL="https://h0w993oije.execute-api.us-east-2.amazonaws.com/initial"

# NEW
VITE_API_BASE_URL="https://{NEW-API-ID}.execute-api.us-east-2.amazonaws.com/production"
```

### Phase 4: Production Deployment (Week 3)

#### Pre-Deployment Checklist:
- [ ] All tests passing in staging
- [ ] Frontend apps updated and tested
- [ ] Database backups completed
- [ ] Rollback plan documented
- [ ] Team notified of deployment window
- [ ] Monitoring dashboards ready

#### Deployment Steps:

**Step 1: Deploy New Infrastructure (Low Traffic Time)**
```bash
# Deploy US stack
cdk deploy DealsNowBackendStack-US --require-approval never

# Deploy India stack  
cdk deploy DealsNowBackendStack-India --require-approval never
```

**Step 2: Verify New Resources**
```bash
# Test new API endpoints
curl https://{NEW-API-ID}.execute-api.us-east-2.amazonaws.com/production/products

# Check Lambda logs
aws logs tail /aws/lambda/dealsnow-user-management-us --follow
```

**Step 3: Update Frontend Apps**

**dealsnow-app:**
```bash
# Update API endpoints
# Build new version
flutter build apk --release
flutter build ios --release

# Deploy to app stores
```

**dealsnow-aws:**
```bash
# Update .env files
# Build and deploy
npm run build
# Deploy to hosting (Amplify/S3)
```

**Step 4: Monitor**
- Watch CloudWatch metrics
- Monitor error rates
- Check API Gateway throttling
- Verify database connections

**Step 5: Gradual Cutover**
- Keep old APIs running for 24-48 hours
- Monitor both old and new endpoints
- Verify all traffic moved to new APIs

### Phase 5: Cleanup (Week 4)

#### Tasks:
1. Verify all traffic on new infrastructure
2. Delete old Lambda functions
3. Delete old API Gateways
4. Remove old IAM roles
5. Update documentation
6. Archive old code

#### Old Resources to Delete:
```bash
# Lambda functions
aws lambda delete-function --function-name manage_users
aws lambda delete-function --function-name bookmark_management
# ... (all old functions)

# API Gateways
aws apigateway delete-rest-api --rest-api-id h0w993oije
aws apigateway delete-rest-api --rest-api-id o2vjydhm1e

# IAM roles
aws iam delete-role --role-name lambda-products-management-role-br08a5pu
# ... (all old roles)
```

## Rollback Plan

### If Issues Occur During Migration:

**Immediate Rollback (< 1 hour):**
1. Revert frontend apps to old API endpoints
2. Redeploy previous app versions
3. Keep new infrastructure running for investigation

**Full Rollback (> 1 hour):**
1. Destroy new CDK stacks:
   ```bash
   cdk destroy DealsNowBackendStack-US
   cdk destroy DealsNowBackendStack-India
   ```
2. Verify old infrastructure still working
3. Investigate issues
4. Plan re-deployment

## Risk Assessment

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| API endpoint changes break apps | High | Medium | Thorough testing, gradual rollout |
| Database connection issues | High | Low | Test in staging, verify secrets |
| Lambda timeout/errors | Medium | Low | Monitor CloudWatch, set alarms |
| Cost increase | Low | Low | Monitor AWS Cost Explorer |
| User disruption | High | Low | Deploy during low-traffic time |

## Success Metrics

### Technical Metrics:
- ✅ All Lambda functions deployed successfully
- ✅ All API endpoints responding correctly
- ✅ 0 errors in CloudWatch logs (first 24 hours)
- ✅ API latency < 500ms (p95)
- ✅ No database connection errors

### Business Metrics:
- ✅ No user-reported issues
- ✅ App functionality unchanged
- ✅ No increase in support tickets
- ✅ Same or better performance

## Timeline

| Week | Phase | Activities |
|------|-------|------------|
| Week 1 | Preparation | Review code, test CDK, setup staging |
| Week 2 | Staging | Deploy to staging, test thoroughly |
| Week 2-3 | Frontend Updates | Update apps, test integration |
| Week 3 | Production | Deploy to production, monitor |
| Week 4 | Cleanup | Remove old resources, documentation |

## Communication Plan

### Stakeholders:
- Development team
- QA team
- Product management
- Support team

### Communication Schedule:
- **Week 1**: Kickoff meeting, share migration plan
- **Week 2**: Daily standup updates
- **Week 3**: Pre-deployment notification (24 hours before)
- **Week 3**: Deployment status updates (hourly during deployment)
- **Week 4**: Post-deployment review

## Post-Migration

### Monitoring (First 30 Days):
- Daily CloudWatch metrics review
- Weekly cost analysis
- User feedback monitoring
- Performance benchmarking

### Documentation Updates:
- Update README files
- Update API documentation
- Update deployment guides
- Create runbooks for common issues

### Training:
- Team training on CDK deployment
- Documentation on new infrastructure
- Runbook for troubleshooting

## Approval

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Tech Lead | | | |
| DevOps | | | |
| Product Manager | | | |

---

**Status**: Draft - Pending Review

**Last Updated**: 2026-01-31

**Next Review**: Before Phase 2 deployment
