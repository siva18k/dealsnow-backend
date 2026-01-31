#!/usr/bin/env python3
"""
Helper script to add Secrets Manager database credential retrieval to Lambda functions.
This script prepends the necessary code to each Lambda function.
"""

import os
import re

# Standard Secrets Manager helper code to add to each Lambda function
SECRETS_MANAGER_CODE = '''import os
import json
import boto3
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
    import pg8000
    
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

'''

def process_lambda_file(filepath):
    """Process a single Lambda function file"""
    print(f"\nProcessing: {filepath}")
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Check if already has Secrets Manager code
    if 'get_db_credentials' in content:
        print(f"  ✓ Already has Secrets Manager code")
        return False
    
    # Check if file uses database connections
    if 'pg8000' not in content and 'psycopg2' not in content:
        print(f"  → Skipping (no database connection)")
        return False
    
    # Find where to insert the code (after imports)
    lines = content.split('\n')
    insert_index = 0
    
    # Find the last import statement
    for i, line in enumerate(lines):
        if line.strip().startswith('import ') or line.strip().startswith('from '):
            insert_index = i + 1
    
    # Insert the Secrets Manager code
    lines.insert(insert_index, SECRETS_MANAGER_CODE)
    
    # Write back
    new_content = '\n'.join(lines)
    
    # Create backup
    backup_path = filepath + '.backup'
    with open(backup_path, 'w') as f:
        f.write(content)
    
    with open(filepath, 'w') as f:
        f.write(new_content)
    
    print(f"  ✓ Added Secrets Manager code")
    print(f"  ✓ Backup saved to {backup_path}")
    return True

def main():
    """Main function"""
    lambda_dir = os.path.join(os.path.dirname(__file__), '..', 'lambda-functions')
    
    if not os.path.exists(lambda_dir):
        print(f"Error: Lambda functions directory not found: {lambda_dir}")
        return
    
    print("=" * 60)
    print("Lambda Functions - Secrets Manager Migration")
    print("=" * 60)
    
    updated_count = 0
    skipped_count = 0
    
    # Process all Python files
    for filename in os.listdir(lambda_dir):
        if filename.endswith('.py') and not filename.endswith('.backup'):
            filepath = os.path.join(lambda_dir, filename)
            if process_lambda_file(filepath):
                updated_count += 1
            else:
                skipped_count += 1
    
    print("\n" + "=" * 60)
    print(f"Summary:")
    print(f"  Updated: {updated_count} files")
    print(f"  Skipped: {skipped_count} files")
    print("=" * 60)
    
    print("\nNext steps:")
    print("1. Review the updated Lambda functions")
    print("2. Update database connection code to use get_db_connection()")
    print("3. Remove hardcoded DB_HOST, DB_USER, DB_PASSWORD references")
    print("4. Test locally")
    print("5. Deploy with CDK")

if __name__ == '__main__':
    main()
