import json
import urllib.request
import urllib.parse
import boto3
import pg8000
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime

# Configuration
SECRET_NAME = "dealsnow/rakuten/api"
REGION = "us-east-1"
HOST = "api.linksynergy.com"
ENDPOINT = f"https://{HOST}/productsearch/1.0"

DB_CONFIG = {
    'host': os.environ.get('DB_HOST'),
    'database': os.environ.get('DB_NAME') or 'postgres',
    'user': os.environ.get('DB_USER'),
    'password': os.environ.get('DB_PASSWORD'),
    'port': int(os.environ.get('DB_PORT', 5432))  # Aurora PostgreSQL standard port
}

DEFAULT_CATEGORIES = [
    "accessories", "appliance", "apparel", "beauty", "books", "electronics",
    "health", "home", "jewelry", "shoes", "sports", "toys"
]

# Default merchant IDs for popular retailers on Rakuten
DEFAULT_MERCHANTS = [
    "42078",   # Geekbuying.com
    "53521",   # Newegg
    "44583",  # Geenworks
    "53538",   # Seagate
]


def get_db_connection():
    """Connect via AWS Secrets Manager (Aurora PostgreSQL) or DB_* env. Port 5432 for Aurora."""
    try:
        secret_name = os.environ.get('DB_SECRET_NAME') or os.environ.get('DB_SECRET_ARN')
        if secret_name:
            db_region = os.environ.get('AWS_REGION', 'us-east-2')  # Aurora secret region
            client = boto3.client('secretsmanager', region_name=db_region)
            r = client.get_secret_value(SecretId=secret_name)
            cred = json.loads(r['SecretString'])
            return pg8000.connect(
                host=cred.get('host') or cred.get('endpoint'),
                port=int(cred.get('port', 5432)),
                database=cred.get('dbname') or cred.get('database') or 'postgres',
                user=cred.get('username') or cred.get('user'),
                password=cred.get('password')
            )
        if not all([DB_CONFIG['host'], DB_CONFIG['user'], DB_CONFIG['password']]):
            raise ValueError("Missing database configuration (DB_SECRET_NAME or DB_HOST/DB_USER/DB_PASSWORD)")
        return pg8000.connect(**DB_CONFIG)
    except Exception as e:
        print(f"Database connection error: {str(e)}")
        return None

def get_secret():
    # First try to get API token from environment variable
    api_token = os.environ.get('RAKUTEN_API_TOKEN')
    if api_token:
        print("Using API token from environment variable RAKUTEN_API_TOKEN")
        return {'API_TOKEN': api_token}
    
    # Fallback to AWS Secrets Manager if environment variable is not set
    try:
        client = boto3.client('secretsmanager', region_name=REGION)
        response = client.get_secret_value(SecretId=SECRET_NAME)
        print("Using API token from AWS Secrets Manager")
        return json.loads(response['SecretString'])
    except client.exceptions.ResourceNotFoundException:
        print(f"Secret {SECRET_NAME} not found and RAKUTEN_API_TOKEN environment variable not set")
        raise ValueError(f"API token not found. Please set RAKUTEN_API_TOKEN environment variable or create secret {SECRET_NAME}")
    except Exception as e:
        print(f"Error retrieving secret: {str(e)}")
        raise ValueError(f"Failed to get API token: {str(e)}")

def insert_products(products, table_name):
    conn = get_db_connection()
    if not conn:
        return False, "Database connection failed", []
    try:
        cur = conn.cursor()
        upserted_ids = []
        for product in products:
            db_fields = {
                'product_name': product.get('name'),
                'description': product.get('description'),
                'deal_price': product.get('price'),
                'original_price': product.get('orig_price'),
                'image_url': product.get('image'),
                'image_url_1': product.get('image_url_1'),
                'image_url_2': product.get('image_url_2'),
                'image_url_3': product.get('image_url_3'),
                'sale_url': product.get('sale_url'),
                'product_key': product.get('product_key'),
                'brand': product.get('brand'),
                'category': product.get('category'),
                'discount_percent': product.get('discount_percent'),
                'start_date': product.get('start_date'),
                'end_date': product.get('end_date'),
                'stock_status': product.get('stock_status'),
                'updated_at': datetime.now(),
                'is_active': True,
                'deal_type': 'Hot Deal',
                'retailer': product.get('retailer')  # Use the merchant name as retailer
            }
            # For new records, also set created_at
            db_fields_insert = dict(db_fields)
            db_fields_insert['created_at'] = datetime.now()
            columns = ', '.join([f'"{k}"' for k in db_fields_insert.keys()])
            placeholders = ', '.join(['%s'] * len(db_fields_insert))
            update_assignments = ', '.join([f'"{k}" = EXCLUDED."{k}"' for k in db_fields.keys() if k != 'product_key' and k != 'created_at'])

            query = f"""
                INSERT INTO {table_name} ({columns})
                VALUES ({placeholders})
                ON CONFLICT (product_key) DO UPDATE
                SET {update_assignments}
                RETURNING product_id
            """
            cur.execute(query, list(db_fields_insert.values()))
            upserted_ids.append(cur.fetchone()[0])
        conn.commit()
        return True, f"Upserted {len(upserted_ids)} products", upserted_ids
    except pg8000.Error as e:
        conn.rollback()
        return False, f"Database error: {str(e)}", []
    except Exception as e:
        conn.rollback()
        return False, f"Upsert error: {str(e)}", []
    finally:
        if conn:
            conn.close()

def parse_xml_response(xml_data):
    """Parse XML response from Rakuten API and extract product information"""
    try:
        root = ET.fromstring(xml_data)
        products = []
        
        # Find all item elements in the XML
        items = root.findall('.//item')
        
        for item in items:
            try:
                # Extract product details
                name = item.find('productname')
                name = name.text if name is not None else "Unknown Product"
                
                # Extract description from short and long descriptions
                desc_elem = item.find('description')
                description = ""
                if desc_elem is not None:
                    short_desc = desc_elem.find('short')
                    long_desc = desc_elem.find('long')
                    short_text = short_desc.text if short_desc is not None and short_desc.text else ""
                    long_text = long_desc.text if long_desc is not None and long_desc.text else ""
                    description = f"{short_text} {long_text}".strip()
                
                # Extract price information
                price_elem = item.find('price')
                try:
                    current_price = float(price_elem.text) if price_elem is not None else 0.0
                except (ValueError, TypeError):
                    current_price = 0.0
                
                # Extract sale price
                sale_price_elem = item.find('saleprice')
                try:
                    sale_price_val = float(sale_price_elem.text) if sale_price_elem is not None and sale_price_elem.text != "0" else 0.0
                except (ValueError, TypeError):
                    sale_price_val = 0.0
                
                # Determine deal price and original price
                if sale_price_val > 0 and sale_price_val < current_price:
                    deal_price = sale_price_val
                    orig_price = current_price
                    discount_percent = ((orig_price - deal_price) / orig_price) * 100
                else:
                    # If no sale price, use current price for both (no discount)
                    deal_price = current_price
                    orig_price = current_price
                    discount_percent = 0.0
                
                # Skip if price is too low or no actual discount for sale items
                if deal_price <= 1.0:
                    continue
                
                # Extract other fields
                image_url = item.find('imageurl')
                image_url = image_url.text if image_url is not None else None
                
                link_url = item.find('linkurl')
                link_url = link_url.text if link_url is not None else None
                
                # Extract category information
                category_elem = item.find('category')
                category = "Unknown"
                if category_elem is not None:
                    primary_cat = category_elem.find('primary')
                    if primary_cat is not None and primary_cat.text:
                        category = primary_cat.text
                
                # Extract merchant info
                merchant_name = item.find('merchantname')
                merchant_name = merchant_name.text if merchant_name is not None else "Unknown"
                
                # Extract brand info - look for actual product brand
                brand_elem = item.find('brand')
                product_brand = ""
                if brand_elem is not None and brand_elem.text:
                    product_brand = brand_elem.text.strip()
                # If no brand field found, try manufacturer
                elif item.find('manufacturer') is not None and item.find('manufacturer').text:
                    product_brand = item.find('manufacturer').text.strip()
                
                # Generate unique product key using SKU or fallback
                sku = item.find('sku')
                merchant_id = item.find('mid')
                if sku is not None and sku.text:
                    product_key = f"rakuten_{merchant_id.text if merchant_id is not None else 'unknown'}_{sku.text}"
                else:
                    # Fallback to using product name
                    product_key = f"rakuten_{merchant_id.text if merchant_id is not None else 'unknown'}_{name.replace(' ', '_').replace('/', '_')[:50]}"
                
                products.append({
                    "name": name,
                    "description": description[:1000],  # Limit description length
                    "price": deal_price,
                    "orig_price": orig_price,
                    "discount_percent": round(discount_percent, 2),
                    "image": image_url,
                    "image_url_1": image_url,  # Use main image as fallback for image_url_1
                    "image_url_2": None,  # Rakuten typically provides one image
                    "image_url_3": None,
                    "sale_url": link_url,
                    "product_key": product_key,
                    "brand": product_brand,  # Use actual product brand from API, empty if not available
                    "category": category,
                    "retailer": merchant_name,  # Use merchant name as retailer
                    "start_date": None,
                    "end_date": None,
                    "stock_status": "In Stock"  # Default to in stock
                })
                
            except Exception as e:
                print(f"Error parsing individual item: {str(e)}")
                continue
                
        return products
        
    except ET.ParseError as e:
        print(f"XML parsing error: {str(e)}")
        return []
    except Exception as e:
        print(f"Error parsing XML response: {str(e)}")
        return []

def fetch_rakuten_products(token, merchant_id, category, keyword=None, max_results=20):
    """Fetch products from Rakuten API for a specific merchant and category"""
    try:
        # Build query parameters
        params = {
            'mid': merchant_id,
            'pagenumber': '1',
            'max': str(max_results),
            'cat': category,
            'locale': 'en_US'  # Set default language to en_US
        }
        
        # Add keyword if provided
        if keyword:
            params['keyword'] = keyword
        
        query_string = urllib.parse.urlencode(params)
        url = f"{ENDPOINT}?{query_string}"

        print('debug url: ', url)
        
        headers = {
            'accept': 'application/xml',
            'authorization': f'Bearer {token}'
        }
        
        req = urllib.request.Request(url, headers=headers, method="GET")
        
        with urllib.request.urlopen(req) as response:
            xml_data = response.read().decode('utf-8')
            return parse_xml_response(xml_data)
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"Rakuten API HTTP error for merchant {merchant_id}, category {category}: {error_body}")
        return []
    except Exception as e:
        print(f"Error fetching Rakuten products for merchant {merchant_id}, category {category}: {str(e)}")
        return []

def lambda_handler(event, context):
    # Add CORS headers for all responses
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
    }
    
    # Handle preflight OPTIONS request
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'message': 'CORS preflight successful'})
        }
    
    try:
        # Handle both direct invocation and API Gateway request
        if 'body' in event and event['body']:
            # API Gateway request - parse the body
            if isinstance(event['body'], str):
                body = json.loads(event['body'])
            else:
                body = event['body']
        else:
            # Direct invocation - use event directly
            body = event
        
        input_categories = body.get('categories', DEFAULT_CATEGORIES)
        input_merchants = body.get('merchants', DEFAULT_MERCHANTS)
        input_keywords = body.get('keywords', [])
        max_products_per_request = body.get('max_products', 10)
        environment = body.get('environment', 'staging')  # Default to staging
        
        # Remove duplicates while preserving order
        seen = set()
        categories = [x for x in input_categories if not (x in seen or seen.add(x))]
        seen.clear()
        merchants = [x for x in input_merchants if not (x in seen or seen.add(x))]
        seen.clear()
        keywords = [x for x in input_keywords if not (x in seen or seen.add(x))] if input_keywords else []
        
        # Determine table name based on environment
        if environment == 'production':
            table_name = 'deals_master.product'
        else:
            table_name = 'deals_master.product_staging'  # Default for staging or any other value
        
        print(f"Using environment: {environment}, table: {table_name}")
        
        # Limit processing to avoid timeouts - be more aggressive
        total_combinations = len(merchants) * len(categories)
        if keywords:
            total_combinations *= len(keywords)
        
        # Reduce max combinations to prevent timeouts (max 10 calls instead of 15)
        max_combinations = 10
        if total_combinations > max_combinations:
            print(f"Warning: Limiting processing to {max_combinations} combinations to avoid timeout")
            # Truncate merchants and categories to fit within limit
            if len(keywords) > 0:
                max_per_dimension = int((max_combinations / len(keywords)) ** 0.5)
                merchants = merchants[:max(1, max_per_dimension)]
                categories = categories[:max(1, max_per_dimension)]
            else:
                max_merchants = min(len(merchants), max_combinations // len(categories))
                merchants = merchants[:max_merchants]
        
        print(f"Processing {len(merchants)} merchants × {len(categories)} categories × {len(keywords) if keywords else 1} keywords = {len(merchants) * len(categories) * (len(keywords) if keywords else 1)} API calls")
        
        creds = get_secret()
        token = creds['API_TOKEN']
        all_inserted_ids = []
        
        # Track start time for timeout prevention
        start_time = time.time()
        max_execution_time = 25  # seconds (AWS Lambda has 30s timeout)
        
        for merchant_id in merchants:
            for category in categories:
                # Check if we're approaching timeout
                if time.time() - start_time > max_execution_time:
                    print(f"Approaching timeout limit, stopping processing with {len(all_inserted_ids)} products inserted")
                    break
                    
                # If keywords are provided, use each keyword, otherwise do one call without keywords
                keyword_list = keywords if keywords else [None]
                
                for keyword in keyword_list:
                    # Check timeout before each API call
                    if time.time() - start_time > max_execution_time:
                        print(f"Timeout limit reached, stopping with {len(all_inserted_ids)} products inserted")
                        break
                        
                    print(f"Processing merchant: {merchant_id}, category: {category}, keyword: {keyword}")
                    
                    products = fetch_rakuten_products(token, merchant_id, category, keyword, max_products_per_request)

                    print('debug products: ',products)
                    
                    # Limit to top 2 products per merchant/category/keyword combination (reduced from 3)
                    products = products[:2]
                    
                    if products:
                        success, message, ids = insert_products(products, table_name)
                        if success:
                            all_inserted_ids.extend(ids)
                            print(f"Inserted {len(ids)} products for merchant {merchant_id}, category {category}, keyword: {keyword}")
                        else:
                            print(f"Failed to insert products for merchant {merchant_id}, category {category}, keyword: {keyword}: {message}")
                    else:
                        print(f"No products found for merchant {merchant_id}, category {category}, keyword: {keyword}")
                    
                    # Reduced rate limiting - sleep between requests (reduced from 2 seconds)
                    time.sleep(0.5)  # Further reduced from 1 second
                
                # Break outer loop if timeout reached
                if time.time() - start_time > max_execution_time:
                    break
        
        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({
                "processed_merchants": merchants,
                "processed_categories": categories,
                "processed_keywords": keywords,
                "total_inserted": len(all_inserted_ids),
                "inserted_ids": all_inserted_ids,
                "environment": environment,
                "table_used": table_name
            })
        }
        
    except Exception as e:
        print(f"Lambda handler error: {str(e)}")
        return {
            "statusCode": 500,
            "headers": headers,
            "body": json.dumps({"error": str(e)})
        }