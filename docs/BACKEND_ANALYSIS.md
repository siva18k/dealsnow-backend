# DealsNow Backend Infrastructure Analysis

## Overview
This document provides a comprehensive analysis of the DealsNow backend infrastructure extracted from AWS. All components are consolidated for unified deployment via CDK.

## API Gateways

### 1. products_api (h0w993oije)
**Base URL:** `https://h0w993oije.execute-api.us-east-2.amazonaws.com/initial`

**Endpoints:**
- `GET /products` - Get product listings
- `POST /products` - Create/update products
- `GET /products_web` - Get products for web display
- `GET /product` - Get single product details
- `GET,POST /products_management` - Product management operations
- `GET,POST /get_amazon_products_by_url` - Fetch Amazon product data
- `GET,POST /products_search_embeded` - Embedded product search

### 2. product_ap_update (o2vjydhm1e)
**Base URL:** `https://o2vjydhm1e.execute-api.us-east-2.amazonaws.com/staging`

**Endpoints:**
- `POST /postgres_to_s3_dump` - Export database to S3
- `GET,POST,PUT /promo_management` - Promo management
- `POST /presigned_url_s3` - Generate S3 presigned URLs
- `POST /submit_deals` - Submit new deals
- `POST /get_product_data_rakuten` - Fetch Rakuten product data
- `POST /social/post` - Social media posting
- `GET,POST /signup` - User signup/management
- `GET,POST /submit_deals_cja` - Submit CJ Affiliate deals
- `GET,POST /bookmark_management` - Bookmark operations

## Lambda Functions

### Core User Management
1. **manage_users** (python3.13)
   - Handler: `manage_users.lambda_handler`
   - Purpose: User authentication, registration, profile management
   - Dependencies: pg8000, boto3
   - Secrets: Uses `DB_SECRET_NAME` environment variable

2. **bookmark_management** (python3.13)
   - Handler: `lambda_function.lambda_handler`
   - Purpose: User bookmark operations (add, remove, list)
   - Dependencies: pg8000, boto3

### Product Data Management
3. **update_product_data** (python3.13)
   - Handler: `lambda_function.lambda_handler`
   - Purpose: Update product information in database
   - Environment: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT

4. **lambda-products-management** (python3.13)
   - Handler: `lambda_function.lambda_handler`
   - Purpose: Product CRUD operations, authentication
   - Dependencies: Google OAuth2, Facebook auth

5. **csv_import_products** (python3.13)
   - Handler: `lambda_function.lambda_handler`
   - Purpose: Bulk import products from CSV
   - Environment: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD

### Product Search
6. **product_search_embedded** (python3.13)
   - Handler: `lambda_function.lambda_handler`
   - Purpose: Embedded search functionality
   - Dependencies: Google OAuth2, Facebook auth

7. **s3_vectors_product_search** (python3.13)
   - Handler: TBD
   - Purpose: Vector-based product search using S3

### Promotional Management
8. **promo_master_management** (python3.13)
   - Handler: `lambda_function.lambda_handler`
   - Purpose: Manage promotional campaigns
   - Environment: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT

9. **update_promo_products_daily** (python3.13)
   - Handler: `lambda_function.lambda_handler`
   - Purpose: Daily promo product updates
   - Environment: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD

### External API Integrations
10. **get_product_data** (python3.13)
    - Handler: `lambda_function.lambda_handler`
    - Purpose: Generic product data fetching
    - Dependencies: pg8000, boto3

11. **get_product_data_rakuten** (python3.13)
    - Handler: `lambda_function.lambda_handler`
    - Purpose: Fetch products from Rakuten API
    - Secrets: `dealsnow/rakuten/api`

12. **get-product-data-amazon** (python3.13)
    - Handler: `lambda_function.lambda_handler`
    - Purpose: Fetch products from Amazon
    - Dependencies: requests, BeautifulSoup

13. **get_product_data_cj_affiliates_graph_ql** (python3.13)
    - Handler: TBD
    - Purpose: Fetch products from CJ Affiliates GraphQL API

## AWS Resources

### S3 Buckets
1. **dealsnow-data** (us-east-2)
   - Purpose: Main data storage
   - Contents: product_data.json.gz, promo_data.json, categories.json, retailers.json

2. **dealsnow-india** (ap-south-1)
   - Purpose: India-specific data
   - Contents: deals_india data

3. **dealsnow-vector-us** (us-east-2)
   - Purpose: Vector embeddings for search

### Secrets Manager
1. **prod/dealsnow_master/aurora_db**
   - Purpose: US database credentials

2. **prod/dealsnow_india/aurora_db**
   - Purpose: India database credentials

3. **dealsnow/amazon/paapi**
   - Purpose: Amazon Product Advertising API credentials

4. **prod/dealsnow/pgsecret**
   - Purpose: PostgreSQL credentials

### IAM Roles (Sample)
- `lambda-products-management-role-br08a5pu`
- `s3-vectors-lambda-role`
- `Lambda-secretManager-readWrite`
- Various function-specific roles

## Database Configuration

### Environment Variables Pattern
Most Lambda functions use:
- `DB_HOST` - Database hostname
- `DB_NAME` - Database name
- `DB_USER` - Database username
- `DB_PASSWORD` - Database password
- `DB_PORT` - Database port (default: 5432)
- `DB_SECRET_NAME` - Secrets Manager secret name (newer pattern)

### Schemas
- `deals_master` - US data
- `deals_india` - India data

## Frontend Integration Points

### dealsnow-app (Flutter)
**API Endpoints Used:**
- `https://h0w993oije.execute-api.us-east-2.amazonaws.com/initial` (US)
- `https://h0w993oije.execute-api.ap-south-1.amazonaws.com/initial` (India)
- `https://o2vjydhm1e.execute-api.us-east-2.amazonaws.com/staging/bookmark_management`

**S3 Resources:**
- `https://dealsnow-data.s3.us-east-2.amazonaws.com/deals_master/*`
- `https://dealsnow-india.s3.ap-south-1.amazonaws.com/deals_india/*`

### dealsnow-aws (React/Vite)
**API Endpoints Used:**
- Same as dealsnow-app
- Additional admin endpoints for deal submission

**Environment Variables:**
- `VITE_API_BASE_URL`
- `VITE_S3_PRODUCTS_URL`
- `VITE_S3_PROMOS_URL`

## Naming Convention Issues

### Current State
Functions use inconsistent naming:
- Some use underscores: `manage_users`, `bookmark_management`
- Some use hyphens: `get-product-data-amazon`, `lambda-products-management`
- No consistent prefix

### Proposed Naming Convention
All resources should use `dealsnow-` prefix:
- Lambda: `dealsnow-user-management`, `dealsnow-bookmark-management`
- API Gateway: `dealsnow-api-main`, `dealsnow-api-staging`
- S3: Already compliant (`dealsnow-data`, `dealsnow-india`)
- Secrets: `dealsnow/master/db`, `dealsnow/india/db`

## Deployment Strategy

### Phase 1: Create Consolidated Stack
1. Create new CDK stack with all resources
2. Use `dealsnow-` prefix for all new resources
3. Deploy to separate environment for testing

### Phase 2: Migration
1. Update frontend apps to use new API endpoints
2. Migrate data if needed
3. Test thoroughly

### Phase 3: Cutover
1. Switch DNS/endpoints
2. Decommission old resources
3. Clean up

## Next Steps
1. ✅ Extract Lambda function code from AWS
2. ⏳ Create consolidated CDK stack
3. ⏳ Rename all resources with `dealsnow-` prefix
4. ⏳ Set up CI/CD pipeline
5. ⏳ Test deployment
6. ⏳ Update frontend configurations
