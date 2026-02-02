# postgres to s3 dump script
import os
import json
import boto3
import pg8000
import gzip
from decimal import Decimal
from datetime import datetime, date

# Database configuration from environment
DB_HOST = os.environ.get('DB_HOST')
DB_NAME = os.environ.get('DB_NAME')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_PORT = int(os.environ.get('DB_PORT', 5432))

# S3 configuration from environment
S3_BUCKET = os.environ.get('S3_BUCKET')
S3_KEY = os.environ.get('S3_KEY')  # Will be set dynamically based on country
# Optional overrides for the compact latest window file
S3_LATEST_KEY = os.environ.get('S3_LATEST_KEY')  # Will be set dynamically based on country
LATEST_LIMIT = int(os.environ.get('LATEST_LIMIT', '200'))  # number of newest items to keep

# Cache-Control policies
CACHE_CONTROL_DEFAULT = os.environ.get('CACHE_CONTROL_DEFAULT', 'public, max-age=300, stale-while-revalidate=30')
CACHE_CONTROL_LATEST = os.environ.get('CACHE_CONTROL_LATEST', 'public, max-age=60, stale-while-revalidate=30')

# Custom encoder for JSON serialization
class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            # Ensure consistent ISO format with timezone info
            if hasattr(obj, 'isoformat'):
                return obj.isoformat()
            else:
                return obj.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

def lambda_handler(event, context):
    global S3_BUCKET, S3_KEY, S3_LATEST_KEY, S3_BASE_PATH  # Declare global variables
    global DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT

    # Load DB credentials from Secrets Manager (DB_SECRET_NAME from CDK, or DB_SECRET_ARN)
    # DB_NAME from env overrides secret (secret may have wrong dbname e.g. "dealsnow_prod"); default 'postgres'
    secret_name = os.environ.get('DB_SECRET_NAME') or os.environ.get('DB_SECRET_ARN')
    if secret_name:
        try:
            print(f"Fetching database credentials from secret: {secret_name}")
            secrets_client = boto3.client('secretsmanager')
            secret_response = secrets_client.get_secret_value(SecretId=secret_name)
            if 'SecretString' in secret_response:
                secret = json.loads(secret_response['SecretString'])
                DB_HOST = secret.get('host') or secret.get('endpoint') or DB_HOST
                secret_db = secret.get('dbname') or secret.get('database')
                DB_NAME = os.environ.get('DB_NAME') or secret_db or DB_NAME or 'postgres'
                DB_USER = secret.get('username') or secret.get('user') or DB_USER
                DB_PASSWORD = secret.get('password') or DB_PASSWORD
                DB_PORT = int(secret.get('port', DB_PORT or 5432))
        except Exception as e:
            print(f"Warning: Failed to fetch DB secret: {e}")
    if not DB_NAME:
        DB_NAME = os.environ.get('DB_NAME') or 'postgres'

    print('starting to copy file to s3 ')
    
    # Only keep useful messages, remove debug prints
    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'OPTIONS,POST'
    }
    
    # Parse the request body if it's a JSON string
    request_data = {}
    if isinstance(event, dict) and 'body' in event and event['body']:
        try:
            request_data = json.loads(event['body'])
        except json.JSONDecodeError as e:
            request_data = {}

    # Initialize variables to avoid UnboundLocalError
    S3_BASE_PATH = 'deals_master'  # Default value

    # Check for selective file processing
    selected_files = None
    if 'files' in event:
        selected_files = event['files']
    elif 'files' in request_data:
        selected_files = request_data['files']

    # Convert single file to list for consistent processing
    if selected_files and isinstance(selected_files, str):
        selected_files = [selected_files]
    elif selected_files and isinstance(selected_files, list):
        pass  # Already a list
    else:
        selected_files = None  # Process all files if no selection

    # Helper function to check if a file should be processed
    def should_process_file(file_key):
        if selected_files is None:
            return True  # Process all files if no selection
        return file_key in selected_files

    # Add country and schema selection logic
    COUNTRY = os.environ.get('COUNTRY', 'US')
    SCHEMA = os.environ.get('SCHEMA', 'deals_master')  # Default schema
    
    print('COUNTRY: ',COUNTRY)
    print('SCHEMA: ',SCHEMA)
    print('S3_BUCKET: ',S3_BUCKET)
    print('S3_BASE_PATH: ', S3_BASE_PATH)
    print('Raw event:', json.dumps(event, default=str))  # Debug the full event

    # Parse country and schema from request (handle both direct event and API Gateway format)
    if isinstance(event, dict):
        # Check for direct event parameters first
        if 'country' in event:
            COUNTRY = event['country'].upper()
            print(f'Found country in direct event: {COUNTRY}')
        if 'schema' in event:
            SCHEMA = event['schema']
            print(f'Found schema in direct event: {SCHEMA}')
        
        # Also check the request body (API Gateway format)
        if 'body' in event and event['body']:
            try:
                body_data = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
                if 'country' in body_data:
                    COUNTRY = body_data['country'].upper()
                    print(f'Found country in request body: {COUNTRY}')
                if 'schema' in body_data:
                    SCHEMA = body_data['schema']
                    print(f'Found schema in request body: {SCHEMA}')
            except Exception as e:
                print(f'Error parsing request body: {e}')

    # Initialize S3 variables with default values to avoid UnboundLocalError
    if not S3_KEY:
        S3_KEY = os.environ.get('S3_KEY')
    if not S3_LATEST_KEY:
        S3_LATEST_KEY = os.environ.get('S3_LATEST_KEY')
    if not S3_BUCKET:
        S3_BUCKET = os.environ.get('S3_BUCKET')
    
    # Initialize variables to avoid UnboundLocalError
    S3_BASE_PATH = 'deals_master'  # Default value

    # Set S3 bucket and schema based on country parameter (database always in US, but different schemas)
    if COUNTRY == 'INDIA':
        # India deployment: Use deals_india schema and upload to India S3 bucket
        SCHEMA = SCHEMA if SCHEMA != 'deals_master' else 'deals_india'  # Use India schema for India data
        S3_BUCKET = 'dealsnow-india'
        S3_BASE_PATH = 'deals_india'  # Use deals_india path for India data
        S3_BASE_URL = f'https://dealsnow-india.s3.ap-south-1.amazonaws.com/{S3_BASE_PATH}/'
    else:
        # US deployment: Use deals_master schema and US S3 bucket
        SCHEMA = SCHEMA if SCHEMA != 'deals_india' else 'deals_master'  # Use US schema for US data
        # Ensure S3_BUCKET has a default value if not set in environment
        if not S3_BUCKET:
            S3_BUCKET = 'dealsnow-data'  # Default US bucket
        S3_BASE_PATH = 'deals_master'  # Use deals_master path for US data
        S3_BASE_URL = f'https://{S3_BUCKET}.s3.amazonaws.com/{S3_BASE_PATH}/'

    # Set dynamic S3 keys based on the country/schema - Always set these values
    S3_KEY = f'{S3_BASE_PATH}/product_data.json'
    S3_LATEST_KEY = f'{S3_BASE_PATH}/latest.json'
    
    print('FINAL VALUES AFTER PROCESSING:')
    print(f'COUNTRY: {COUNTRY}')
    print(f'SCHEMA: {SCHEMA}') 
    print(f'S3_BUCKET: {S3_BUCKET}')
    print(f'S3_BASE_PATH: {S3_BASE_PATH}')
    print(f'S3_KEY: {S3_KEY}')
    print(f'S3_LATEST_KEY: {S3_LATEST_KEY}')

    # Remove all time-based filtering; always dump all products
    # Connect to DB
    try:
        conn = pg8000.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            port=DB_PORT
        )
        cur = conn.cursor()
    except Exception as e:
        return {'statusCode': 500, 'headers': cors_headers, 'body': f'Database connection failed: {e}'}

    # No time check; always dump all products

    # Query all columns with consistent timestamp formatting, including category_group and category_group_image_url (excluding description)
    columns = [
        'p.product_id', 'p.product_name', 'p.original_price', 'p.deal_price', 'p.image_url', 'p.sale_url',
        'p.category_id', 'p.deal_type_id', 'p.seller_id', 'p.ts_vector', 
        'to_char(p.created_at, \'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\') as created_at',
        'to_char(p.updated_at, \'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\') as updated_at',
        'p.is_active', 'p.wix_id', 'p.owner', 'p.deal_type', 'p.category', 'p.retailer', 'p.image_url_1', 'p.image_url_2', 'p.image_url_3',
        'p.product_keywords', 'p.product_key', 'p.product_rating', 'p.discount_percent', 'p.product_type',
        'p.brand', 'p.coupon_info', 'p.coupon_exp_dt', 'p.category_list', 'p.start_date', 'p.end_date', 'p.stock_status', 'p.promo_label',
        'c.category_group', 'c.category_group_image_url'
    ]
    
    # Query for product_data.json (deal_type_id=1 AND is_active=true) with LEFT JOIN to categories table
    query_filtered = f"""
        SELECT {', '.join(columns)} 
        FROM {SCHEMA}.product p
        LEFT JOIN {SCHEMA}.categories c ON p.category = c.category
        WHERE p.is_active = true AND p.deal_type_id = 1
        and COALESCE(p.promo_label,'all') not in ('deals_now_pick','deal_of_the_day')
    """
    
    print('product_data.json query_filtered ',query_filtered)
    try:
        cur.execute(query_filtered)
        rows_filtered = cur.fetchall()
        result_filtered = [dict(zip([desc[0] for desc in cur.description], row)) for row in rows_filtered]
    except Exception as e:
        return {'statusCode': 500, 'headers': cors_headers, 'body': f'Filtered query failed: {e}'}
    finally:
        cur.close()
        conn.close()

    # Initialize variables for tracking results
    processed_files = []
    total_records = 0

    # Initialize S3 client
    s3 = boto3.client('s3')

    # Dump to JSON for filtered products (deal_type_id=1)
    json_data_filtered = json.dumps(result_filtered, cls=CustomEncoder)
    print(f"Uploading {len(result_filtered)} filtered products (deal_type_id=1) to S3 at {S3_BUCKET}/{S3_KEY}")

    # Upload products to S3 - only if selected or all files
    if should_process_file('product_data.json'):
        try:
            # Upload filtered products (deal_type_id=1) - original JSON
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=S3_KEY,
                Body=json_data_filtered,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
            )
            print(f"Filtered products upload finished: {S3_BUCKET}/{S3_KEY}")

            # Upload gzipped version
            gzipped_data = gzip.compress(json_data_filtered.encode('utf-8'))
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=S3_KEY + '.gz',
                Body=gzipped_data,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
                ContentEncoding='gzip',
            )
            print(f"Gzipped filtered products upload finished: {S3_BUCKET}/{S3_KEY}.gz")
            processed_files.append('product_data.json')
            total_records += len(result_filtered)
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Failed to upload product_data.json to S3: {e}'}
    else:
        print("Skipping product_data.json - not selected")

    # --- Create a minimal, compact latest.json for app-side polling ---
    if should_process_file('latest.json'):
        try:
            # Sort newest-first by updated_at (ISO string allows lexicographic sort)
            sorted_newest = sorted(
                result_filtered,
                key=lambda x: x.get('updated_at') or '',
                reverse=True,
            )
            limited = sorted_newest[: max(0, LATEST_LIMIT)]

            latest_payload = {
                'generated_at': datetime.utcnow().isoformat() + 'Z',
                'items': [
                    {
                        'id': item.get('product_id'),
                        'updated_at': item.get('updated_at'),
                        'name': item.get('product_name'),
                        'price': item.get('deal_price'),
                        'retailer': item.get('retailer'),
                    }
                    for item in limited
                    if item.get('product_id') is not None and item.get('updated_at') is not None
                ],
            }

            # Prepare next step payload for push notification Lambda (Step Functions)
            latest_product = sorted_newest[0] if sorted_newest else None
            next_send_event = None
            if latest_product is not None:
                # Build a concise title/body; adjust as needed
                lp_name = latest_product.get('product_name') or 'New Deal'
                lp_price = latest_product.get('deal_price')
                lp_retailer = latest_product.get('retailer') or ''
                title = f"New deal{f' at {lp_retailer}' if lp_retailer else ''}!"
                body = f"{lp_name}{f' - ${lp_price:.2f}' if isinstance(lp_price, (int, float)) else (f' - ${lp_price}' if lp_price else '')}"

                next_send_event = {
                    "action": "send_notification",
                    "type": "topic",            # app subscribes to 'new_deals'
                    "target": "new_deals",
                    "title": title,
                    "body": body,
                    "data": {
                        "product_id": str(latest_product.get('product_id')) if latest_product.get('product_id') is not None else "",
                        "product_name": lp_name,
                        "price": (f"{lp_price:.2f}" if isinstance(lp_price, (int, float)) else (str(lp_price) if lp_price is not None else "")),
                        "retailer": lp_retailer or "",
                        "sale_url": latest_product.get('sale_url') or "",
                        "image_url": latest_product.get('image_url') or latest_product.get('image_url_1') or ""
                    }
                }

            latest_json = json.dumps(latest_payload, cls=CustomEncoder)
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=S3_LATEST_KEY,
                Body=latest_json,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_LATEST,
            )
            print(
                f"Uploaded compact latest window: {len(latest_payload['items'])} items to {S3_BUCKET}/{S3_LATEST_KEY}"
            )
            processed_files.append('latest.json')
            total_records += len(latest_payload['items'])
        except Exception as e:
            # Do not fail the whole lambda for latest.json issues; log and continue
            print(f"Warning: failed to create/upload latest.json: {e}")
            latest_product = None
            next_send_event = None
    else:
        print("Skipping latest.json - not selected")
        latest_product = None
        next_send_event = None

    # --- Create separate dump for product_id and description only ---
    if should_process_file('product_descriptions.json'):
        try:
            conn = pg8000.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                port=DB_PORT
            )
            cur = conn.cursor()

            # Query for product_id and description only
            query_description_only = f"""
                SELECT p.product_id, p.description
                FROM {SCHEMA}.product p
                WHERE p.is_active = true AND p.deal_type_id = 1
            """
            cur.execute(query_description_only)
            rows_description_only = cur.fetchall()
            result_description_only = [dict(zip([desc[0] for desc in cur.description], row)) for row in rows_description_only]

            cur.close()
            conn.close()
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Description-only query failed: {e}'}

        # Dump description-only data to JSON
        description_json_data = json.dumps(result_description_only, cls=CustomEncoder)
        description_s3_key = f'{S3_BASE_PATH}/product_descriptions.json'
        print(f"Uploading {len(result_description_only)} product descriptions to S3 at {S3_BUCKET}/{description_s3_key}")
        try:
            # Upload product descriptions - original JSON
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=description_s3_key,
                Body=description_json_data,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
            )
            print(f"Product descriptions upload finished: {S3_BUCKET}/{description_s3_key}")

            # Upload gzipped version
            gzipped_descriptions = gzip.compress(description_json_data.encode('utf-8'))
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=description_s3_key + '.gz',
                Body=gzipped_descriptions,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
                ContentEncoding='gzip',
            )
            print(f"Gzipped product descriptions upload finished: {S3_BUCKET}/{description_s3_key}.gz")
            processed_files.append('product_descriptions.json')
            total_records += len(result_description_only)

        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Failed to upload product descriptions to S3: {e}'}
    else:
        print("Skipping product_descriptions.json - not selected")
        result_description_only = []

    # --- Fetch promo data from promo_master with consistent timestamp formatting ---
    if should_process_file('promo_data.json'):
        promo_columns = [
            'promo_label', 'promo_label_image_url', 'promo_mobile_image_url',
            'to_char(promo_validity_start_dt, \'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\') as promo_validity_start_dt',
            'to_char(promo_validity_end_dt, \'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\') as promo_validity_end_dt',
            'to_char(updated_at, \'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\') as updated_at',
            'promo_id', 'is_active', 'promo_image_width_px', 'promo_image_height_px',
            'promo_title', 'promo_position', 'platform', 'badge_colors',
            'promo_sale_url', 'promo_type', 'promo_script'
        ]
        promo_query = f"SELECT {', '.join(promo_columns)} FROM {SCHEMA}.promo_master WHERE is_active = true"
        try:
            conn = pg8000.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                port=DB_PORT
            )
            cur = conn.cursor()
            cur.execute(promo_query)
            promo_rows = cur.fetchall()
            promo_result = [dict(zip([desc[0] for desc in cur.description], row)) for row in promo_rows]
            cur.close()
            conn.close()
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Promo query failed: {e}'}

        # Dump promo data to JSON
        promo_json_data = json.dumps(promo_result, cls=CustomEncoder)
        promo_s3_key = f'{S3_BASE_PATH}/promo_data.json'
        print(f"Uploading {len(promo_result)} promos to S3 at {S3_BUCKET}/{promo_s3_key}")
        try:
            # Upload promo data - original JSON
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=promo_s3_key,
                Body=promo_json_data,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
            )
            print(f"Promos upload finished: {S3_BUCKET}/{promo_s3_key}")

            # Upload gzipped version
            gzipped_promo_data = gzip.compress(promo_json_data.encode('utf-8'))
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=promo_s3_key + '.gz',
                Body=gzipped_promo_data,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
                ContentEncoding='gzip',
            )
            print(f"Gzipped promo data upload finished: {S3_BUCKET}/{promo_s3_key}.gz")
            processed_files.append('promo_data.json')
            total_records += len(promo_result)
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Failed to upload promo data to S3: {e}'}
    else:
        print("Skipping promo_data.json - not selected")
        promo_result = []

    # --- Fetch categories data from {SCHEMA}.categories ---
    if should_process_file('categories.json'):
        try:
            conn = pg8000.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                port=DB_PORT
            )
            cur = conn.cursor()

            # Get all columns from categories table
            cur.execute(f"SELECT * FROM {SCHEMA}.categories")
            categories_rows = cur.fetchall()
            categories_result = [dict(zip([desc[0] for desc in cur.description], row)) for row in categories_rows]

            cur.close()
            conn.close()
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Categories query failed: {e}'}

        # Dump categories data to JSON
        categories_json_data = json.dumps(categories_result, cls=CustomEncoder)
        categories_s3_key = f'{S3_BASE_PATH}/categories.json'
        print(f"Uploading {len(categories_result)} categories to S3 at {S3_BUCKET}/{categories_s3_key}")
        try:
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=categories_s3_key,
                Body=categories_json_data,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
            )
            print(f"Categories upload finished: {S3_BUCKET}/{categories_s3_key}")
            processed_files.append('categories.json')
            total_records += len(categories_result)
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Failed to upload categories data to S3: {e}'}
    else:
        print("Skipping categories.json - not selected")
        categories_result = []

    # --- Fetch retailers data from {SCHEMA}.retailers ---
    if should_process_file('retailers.json'):
        try:
            conn = pg8000.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                port=DB_PORT
            )
            cur = conn.cursor()

            # Get all columns from retailers table, explicitly including retailer_deep_link
            cur.execute(f"SELECT * FROM {SCHEMA}.retailers")
            retailers_rows = cur.fetchall()
            retailers_result = [dict(zip([desc[0] for desc in cur.description], row)) for row in retailers_rows]

            cur.close()
            conn.close()
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Retailers query failed: {e}'}

        # Dump retailers data to JSON
        retailers_json_data = json.dumps(retailers_result, cls=CustomEncoder)
        retailers_s3_key = f'{S3_BASE_PATH}/retailers.json'
        print(f"Uploading {len(retailers_result)} retailers to S3 at {S3_BUCKET}/{retailers_s3_key}")
        try:
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=retailers_s3_key,
                Body=retailers_json_data,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
            )
            print(f"Retailers upload finished: {S3_BUCKET}/{retailers_s3_key}")
            processed_files.append('retailers.json')
            total_records += len(retailers_result)
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Failed to upload retailers data to S3: {e}'}
    else:
        print("Skipping retailers.json - not selected")
        retailers_result = []

    # --- Query for promo_product_data.json (products with non-empty promo_label and active promo in promo_master) ---
    if should_process_file('promo_product_data.json'):
        promo_product_s3_key = f'{S3_BASE_PATH}/promo_product_data.json'
        print('About to upload promo_product_data.json')
        query_promo_product = f"""
            SELECT {', '.join(columns)}
            FROM {SCHEMA}.product p
            LEFT JOIN {SCHEMA}.categories c ON p.category = c.category
            WHERE p.is_active = true
              AND p.promo_label IS NOT NULL
              AND p.promo_label != ''
              AND length(trim(p.promo_label)) > 0
              AND EXISTS (
                SELECT 1 FROM {SCHEMA}.promo_master b
                WHERE p.promo_label = b.promo_label
                  AND b.is_active = true
              )
        """
        try:
            conn = pg8000.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                port=DB_PORT
            )
            cur = conn.cursor()
            cur.execute(query_promo_product)
            rows_promo_product = cur.fetchall()
            result_promo_product = [dict(zip([desc[0] for desc in cur.description], row)) for row in rows_promo_product]
            cur.close()
            conn.close()
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Promo product query failed: {e}'}

        # Dump promo product data to JSON
        promo_product_json_data = json.dumps(result_promo_product, cls=CustomEncoder)
        print(f"Uploading {len(result_promo_product)} promo products to S3 at {S3_BUCKET}/{promo_product_s3_key}")
        try:
            # Upload promo product data - original JSON
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=promo_product_s3_key,
                Body=promo_product_json_data,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
            )
            print(f"Promo products upload finished: {S3_BUCKET}/{promo_product_s3_key}")

            # Upload gzipped version
            gzipped_promo_product = gzip.compress(promo_product_json_data.encode('utf-8'))
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=promo_product_s3_key + '.gz',
                Body=gzipped_promo_product,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
                ContentEncoding='gzip',
            )
            print(f"Gzipped promo products upload finished: {S3_BUCKET}/{promo_product_s3_key}.gz")
            processed_files.append('promo_product_data.json')
            total_records += len(result_promo_product)

        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Failed to upload promo product data to S3: {e}'}
    else:
        print("Skipping promo_product_data.json - not selected")
        result_promo_product = []

    # --- Fetch price history data ---
    if should_process_file('products_price_history.json'):
        try:
            conn = pg8000.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                port=DB_PORT
            )
            cur = conn.cursor()

            # Query for price history data
            price_history_query = f"""
                SELECT b.product_id as product_id, a.product_key as product_key, a.deal_price as deal_price, a.discount_percent as discount_percent, a.updated_at as updated_at
                FROM {SCHEMA}.product_history a,
                {SCHEMA}.product b
                WHERE a.product_key = b.product_key
            """
            cur.execute(price_history_query)
            price_history_rows = cur.fetchall()
            price_history_result = [dict(zip([desc[0] for desc in cur.description], row)) for row in price_history_rows]

            cur.close()
            conn.close()
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Price history query failed: {e}'}

        # Dump price history data to JSON
        price_history_json_data = json.dumps(price_history_result, cls=CustomEncoder)
        price_history_s3_key = f'{S3_BASE_PATH}/products_price_history.json'
        print(f"Uploading {len(price_history_result)} price history records to S3 at {S3_BUCKET}/{price_history_s3_key}")
        try:
            # Upload price history data - original JSON
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=price_history_s3_key,
                Body=price_history_json_data,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
            )
            print(f"Price history upload finished: {S3_BUCKET}/{price_history_s3_key}")

            # Upload gzipped version
            gzipped_price_history = gzip.compress(price_history_json_data.encode('utf-8'))
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=price_history_s3_key + '.gz',
                Body=gzipped_price_history,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
                ContentEncoding='gzip',
            )
            print(f"Gzipped price history upload finished: {S3_BUCKET}/{price_history_s3_key}.gz")
            processed_files.append('products_price_history.json')
            total_records += len(price_history_result)

        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Failed to upload price history data to S3: {e}'}
    else:
        print("Skipping products_price_history.json - not selected")
        price_history_result = []

    # --- Minimal SEO dataset: product_data_all.json (product_id, name, category, is_active, lastmod) ---
    if should_process_file('product_data_all.json'):
        try:
            conn = pg8000.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                port=DB_PORT
            )
            cur = conn.cursor()

            minimal_query = f"""
                SELECT
                    p.product_id AS product_id,
                    p.product_name AS name,
                    p.category AS category,
                    p.is_active AS is_active,
                    p.coupon_exp_dt AS coupon_exp_dt,
                    to_char(p.updated_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS lastmod
                FROM {SCHEMA}.product p
                WHERE p.product_id IS NOT NULL AND p.is_active = true
            """
            cur.execute(minimal_query)
            rows_minimal = cur.fetchall()
            minimal_results = [dict(zip([desc[0] for desc in cur.description], row)) for row in rows_minimal]
            cur.close()
            conn.close()
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Minimal SEO query failed: {e}'}

        minimal_json = json.dumps({ 'products': minimal_results }, cls=CustomEncoder)
        minimal_s3_key = f'{S3_BASE_PATH}/product_data_all.json'
        print(f"Uploading {len(minimal_results)} minimal product records to S3 at {S3_BUCKET}/{minimal_s3_key}")
        try:
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=minimal_s3_key,
                Body=minimal_json,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
            )
            print(f"Minimal product dataset upload finished: {S3_BUCKET}/{minimal_s3_key}")

            # Upload gzipped version
            gzipped_minimal = gzip.compress(minimal_json.encode('utf-8'))
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=minimal_s3_key + '.gz',
                Body=gzipped_minimal,
                ContentType='application/json',
                CacheControl=CACHE_CONTROL_DEFAULT,
                ContentEncoding='gzip',
            )
            print(f"Gzipped minimal product dataset upload finished: {S3_BUCKET}/{minimal_s3_key}.gz")
            processed_files.append('product_data_all.json')
            total_records += len(minimal_results)
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': f'Failed to upload minimal product dataset to S3: {e}'}
    else:
        print("Skipping product_data_all.json - not selected")
        minimal_results = []

    # Generate response message based on processed files
    if processed_files:
        file_summary = []
        if 'product_data.json' in processed_files:
            file_summary.append(f"{len(result_filtered)} product records")
        if 'latest.json' in processed_files:
            file_summary.append(f"{len(latest_payload['items']) if 'latest_payload' in locals() and latest_payload else 0} latest items")
        if 'product_descriptions.json' in processed_files:
            file_summary.append(f"{len(result_description_only)} descriptions")
        if 'promo_data.json' in processed_files:
            file_summary.append(f"{len(promo_result)} promo records")
        if 'categories.json' in processed_files:
            file_summary.append(f"{len(categories_result)} categories")
        if 'retailers.json' in processed_files:
            file_summary.append(f"{len(retailers_result)} retailers")
        if 'promo_product_data.json' in processed_files:
            file_summary.append(f"{len(result_promo_product)} promo products")
        if 'products_price_history.json' in processed_files:
            file_summary.append(f"{len(price_history_result)} price history records")
        if 'product_data_all.json' in processed_files:
            file_summary.append(f"{len(minimal_results)} minimal products")

        message = f'Selective dump successful for {COUNTRY} deployment using {SCHEMA} schema. Processed files: {", ".join(processed_files)}. Total records: {total_records}. Files uploaded to S3 bucket: {S3_BUCKET}/{S3_BASE_PATH}/'
    else:
        message = f'No files selected for processing in {COUNTRY} deployment using {SCHEMA} schema.'

    response_body = {
        'message': message,
        'processed_files': processed_files,
        'total_records': total_records,
        'country': COUNTRY,
        'schema': SCHEMA,
        's3_bucket': S3_BUCKET,
        's3_base_path': S3_BASE_PATH,
        'latest_product': {
            'product_id': latest_product.get('product_id') if latest_product else None,
            'product_name': latest_product.get('product_name') if latest_product else None,
            'deal_price': latest_product.get('deal_price') if latest_product else None,
            'retailer': latest_product.get('retailer') if latest_product else None,
            'updated_at': latest_product.get('updated_at') if latest_product else None,
        },
        # Payload ready for scripts/send_push_notification_lambda.py
        'next_send_event': next_send_event
    }

    return {
        'statusCode': 200,
        'headers': cors_headers,
        'body': json.dumps(response_body, cls=CustomEncoder)
    }
