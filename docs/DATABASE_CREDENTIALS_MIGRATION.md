# Database Credentials Migration Guide

## Overview

All DealsNow applications must use AWS Secrets Manager for database credentials instead of hardcoded or environment variable credentials.

## Secrets Configuration

### US Deployment (dealsnow-app, dealsnow-aws)
**Secret Name**: `prod/dealsnow_master/aurora_db`
**Region**: `us-east-2`

### India Deployment (dealsnow-india)
**Secret Name**: `prod/dealsnow_india/aurora_db`
**Region**: `ap-south-1`

## Secret Structure

Each secret contains the following JSON structure:
```json
{
  "username": "postgres_user",
  "password": "secure_password",
  "engine": "postgres",
  "host": "database-host.region.rds.amazonaws.com",
  "port": 5432,
  "dbname": "dealsnow_db",
  "dbClusterIdentifier": "cluster-name"
}
```

## Lambda Function Updates

### Current State (INCORRECT)
Many Lambda functions use hardcoded environment variables:
```python
DB_HOST = os.environ.get('DB_HOST')
DB_NAME = os.environ.get('DB_NAME')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
```

### Target State (CORRECT)
All Lambda functions should use Secrets Manager:
```python
import boto3
import json

def get_db_credentials(secret_name, region):
    """Retrieve database credentials from Secrets Manager"""
    client = boto3.client('secretsmanager', region_name=region)
    
    try:
        response = client.get_secret_value(SecretId=secret_name)
        secret = json.loads(response['SecretString'])
        return secret
    except Exception as e:
        print(f"Error retrieving secret: {e}")
        raise

def lambda_handler(event, context):
    # Get secret name from environment
    secret_name = os.environ.get('DB_SECRET_NAME')
    region = os.environ.get('AWS_REGION', 'us-east-2')
    
    # Retrieve credentials
    db_creds = get_db_credentials(secret_name, region)
    
    # Connect to database
    conn = pg8000.connect(
        host=db_creds['host'],
        database=db_creds['dbname'],
        user=db_creds['username'],
        password=db_creds['password'],
        port=db_creds.get('port', 5432)
    )
```

## CDK Stack Configuration

The CDK stack is already configured to:
1. ✅ Reference the correct secrets
2. ✅ Grant Lambda functions permission to read secrets
3. ✅ Set `DB_SECRET_NAME` environment variable

**US Stack**:
```typescript
dbSecretName: 'prod/dealsnow_master/aurora_db'
```

**India Stack**:
```typescript
dbSecretName: 'prod/dealsnow_india/aurora_db'
```

## Lambda Functions to Update

All 11 Lambda functions need to use Secrets Manager:

1. ✅ `manage_users.py` - Already uses secrets
2. ⚠️ `bookmark_management.py` - Needs update
3. ⚠️ `update_product_data.py` - Needs update
4. ⚠️ `promo_master_management.py` - Needs update
5. ⚠️ `product_search_embedded.py` - Needs update
6. ⚠️ `lambda-products-management.py` - Needs update
7. ⚠️ `get_product_data.py` - Needs update
8. ⚠️ `get_product_data_rakuten.py` - Needs update
9. ⚠️ `get-product-data-amazon.py` - Needs update
10. ⚠️ `csv_import_products.py` - Needs update
11. ⚠️ `update_promo_products_daily.py` - Needs update

## Environment Variables

### Remove These (Insecure)
```bash
DB_HOST=xxx
DB_NAME=xxx
DB_USER=xxx
DB_PASSWORD=xxx
DB_PORT=xxx
```

### Keep These (Secure)
```bash
DB_SECRET_NAME=prod/dealsnow_master/aurora_db  # or prod/dealsnow_india/aurora_db
DB_SCHEMA=deals_master                          # or deals_india
REGION=us                                       # or india
AWS_REGION=us-east-2                           # or ap-south-1
```

## IAM Permissions Required

Lambda execution role needs:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": [
        "arn:aws:secretsmanager:us-east-2:*:secret:prod/dealsnow_master/aurora_db*",
        "arn:aws:secretsmanager:ap-south-1:*:secret:prod/dealsnow_india/aurora_db*"
      ]
    }
  ]
}
```

✅ **Already configured in CDK stack!**

## Testing

### Test Secret Access
```python
import boto3
import json

def test_secret_access():
    client = boto3.client('secretsmanager', region_name='us-east-2')
    
    try:
        response = client.get_secret_value(
            SecretId='prod/dealsnow_master/aurora_db'
        )
        secret = json.loads(response['SecretString'])
        print(f"✅ Successfully retrieved secret")
        print(f"Host: {secret['host']}")
        print(f"Database: {secret['dbname']}")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

test_secret_access()
```

### Test Database Connection
```python
import pg8000
import boto3
import json

def test_db_connection():
    # Get credentials from Secrets Manager
    client = boto3.client('secretsmanager', region_name='us-east-2')
    response = client.get_secret_value(SecretId='prod/dealsnow_master/aurora_db')
    db_creds = json.loads(response['SecretString'])
    
    # Connect to database
    try:
        conn = pg8000.connect(
            host=db_creds['host'],
            database=db_creds['dbname'],
            user=db_creds['username'],
            password=db_creds['password'],
            port=db_creds.get('port', 5432)
        )
        print("✅ Database connection successful")
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False

test_db_connection()
```

## Migration Checklist

### Before Deployment
- [ ] Verify secrets exist in Secrets Manager
- [ ] Verify secret structure is correct
- [ ] Update all Lambda function code to use secrets
- [ ] Remove hardcoded credentials from code
- [ ] Test locally with AWS credentials

### During Deployment
- [ ] Deploy CDK stack with secret references
- [ ] Verify Lambda functions can access secrets
- [ ] Test database connections
- [ ] Monitor CloudWatch logs for errors

### After Deployment
- [ ] Remove old environment variables
- [ ] Delete any hardcoded credentials
- [ ] Update documentation
- [ ] Rotate secrets (security best practice)

## Security Benefits

### Before (Insecure)
- ❌ Credentials in environment variables
- ❌ Credentials visible in AWS Console
- ❌ Credentials in CloudFormation templates
- ❌ Hard to rotate credentials
- ❌ No audit trail

### After (Secure)
- ✅ Credentials encrypted in Secrets Manager
- ✅ Credentials not visible in Console
- ✅ Automatic rotation supported
- ✅ Easy to rotate credentials
- ✅ Full audit trail in CloudTrail

## Troubleshooting

### Error: "Access Denied" when accessing secret
**Solution**: Verify IAM role has `secretsmanager:GetSecretValue` permission

### Error: "Secret not found"
**Solution**: Verify secret name and region are correct

### Error: "Unable to connect to database"
**Solution**: Verify secret contains correct credentials and database is accessible

### Error: "Invalid JSON in secret"
**Solution**: Verify secret structure matches expected format

## Secret Rotation

To rotate database credentials:

```bash
# 1. Update secret in Secrets Manager
aws secretsmanager update-secret \
  --secret-id prod/dealsnow_master/aurora_db \
  --secret-string '{"username":"new_user","password":"new_pass",...}'

# 2. Lambda functions will automatically use new credentials on next invocation
# No redeployment needed!
```

## Best Practices

1. ✅ **Always use Secrets Manager** for database credentials
2. ✅ **Never hardcode** credentials in code
3. ✅ **Rotate secrets** regularly (every 90 days)
4. ✅ **Use different secrets** for different environments
5. ✅ **Monitor secret access** via CloudTrail
6. ✅ **Cache credentials** in Lambda (but refresh periodically)

## Example: Complete Lambda Function

```python
import os
import json
import boto3
import pg8000
from botocore.exceptions import ClientError

# Cache credentials to avoid repeated Secrets Manager calls
_db_credentials_cache = None

def get_db_credentials():
    """Get database credentials from Secrets Manager with caching"""
    global _db_credentials_cache
    
    if _db_credentials_cache is not None:
        return _db_credentials_cache
    
    secret_name = os.environ.get('DB_SECRET_NAME')
    region = os.environ.get('AWS_REGION', 'us-east-2')
    
    if not secret_name:
        raise ValueError("DB_SECRET_NAME environment variable not set")
    
    client = boto3.client('secretsmanager', region_name=region)
    
    try:
        response = client.get_secret_value(SecretId=secret_name)
        _db_credentials_cache = json.loads(response['SecretString'])
        return _db_credentials_cache
    except ClientError as e:
        print(f"Error retrieving secret {secret_name}: {e}")
        raise

def get_db_connection():
    """Create database connection using Secrets Manager credentials"""
    creds = get_db_credentials()
    
    try:
        conn = pg8000.connect(
            host=creds['host'],
            database=creds['dbname'],
            user=creds['username'],
            password=creds['password'],
            port=creds.get('port', 5432)
        )
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        raise

def lambda_handler(event, context):
    """Lambda handler using Secrets Manager for DB credentials"""
    try:
        # Get database connection
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Your database operations here
        cursor.execute("SELECT version()")
        version = cursor.fetchone()
        print(f"Database version: {version}")
        
        cursor.close()
        conn.close()
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Success'})
        }
    except Exception as e:
        print(f"Error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
```

---

**Status**: Ready for Implementation
**Priority**: High (Security)
**Impact**: All Lambda functions
