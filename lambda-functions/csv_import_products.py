import json
import pg8000
import os
from decimal import Decimal
import datetime
import uuid

# Database configuration
DB_HOST = os.environ.get('DB_HOST')
DB_NAME = os.environ.get('DB_NAME')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')

# Custom JSON encoder to handle datetime objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        elif isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

def get_db_connection():
    """Create database connection"""
    try:
        conn = pg8000.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            port=5432
        )
        return conn
    except pg8000.Error as e:
        print(f"Database connection error: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error during database connection: {e}")
        return None

def validate_product_data(product):
    """Validate product data before insertion"""
    errors = []
    
    # Required fields
    if not product.get('product_name'):
        errors.append('product_name is required')
    if not product.get('sale_url'):
        errors.append('sale_url is required')
    if not product.get('product_key'):
        errors.append('product_key is required')
    
    # Validate URLs
    if product.get('sale_url') and not product['sale_url'].startswith(('http://', 'https://')):
        errors.append('sale_url must be a valid URL')
    if product.get('image_url') and not product['image_url'].startswith(('http://', 'https://')):
        errors.append('image_url must be a valid URL')
    
    # Validate prices
    try:
        if product.get('original_price'):
            float(product['original_price'])
        if product.get('deal_price'):
            float(product['deal_price'])
    except (ValueError, TypeError):
        errors.append('Prices must be valid numbers')
    
    return errors

def generate_ts_vector(product_name, description, category_list):
    """Generate ts_vector for full-text search"""
    text_parts = []
    if product_name:
        text_parts.append(product_name)
    if description:
        text_parts.append(description)
    if category_list:
        text_parts.append(category_list)
    
    combined_text = ' '.join(text_parts)
    return combined_text

def insert_or_update_product(cur, product, schema):
    """Insert or update product in the database"""
    try:
        # Check if product already exists
        check_query = f"""
            SELECT product_id, product_key FROM {schema}.product 
            WHERE product_key = %s
        """
        cur.execute(check_query, (product['product_key'],))
        existing = cur.fetchone()
        
        # Generate ts_vector for search
        ts_vector = generate_ts_vector(
            product.get('product_name'),
            product.get('description'),
            product.get('category_list')
        )
        
        # Prepare product data with defaults
        product_data = {
            'product_name': product.get('product_name', ''),
            'description': product.get('description', ''),
            'original_price': float(product.get('original_price', 0)) if product.get('original_price') else 0,
            'deal_price': float(product.get('deal_price', 0)) if product.get('deal_price') else 0,
            'image_url': product.get('image_url', ''),
            'sale_url': product.get('sale_url', ''),
            'category_id': None,  # Will be set based on category mapping
            'deal_type_id': 1,  # Default deal type ID
            'seller_id': None,  # Will be set based on retailer
            'ts_vector': ts_vector,
            'created_at': datetime.datetime.now(),
            'updated_at': datetime.datetime.now(),
            'is_active': product.get('is_active', True),
            'wix_id': None,
            'owner': 'csv_import',
            'deal_type': product.get('deal_type', 'Hot Deal'),
            'category': product.get('category', ''),
            'retailer': product.get('retailer', 'CSV Import'),
            'image_url_1': product.get('image_url', ''),
            'image_url_2': '',
            'image_url_3': '',
            'embedding': None,
            'product_key': product.get('product_key', ''),
            'product_keywords': ts_vector,  # Use ts_vector as keywords for now
            'product_rating': None,
            'product_type': product.get('product_type', ''),
            'brand': product.get('brand', ''),
            'coupon_info': '',
            'category_list': product.get('category_list', ''),
            'start_date': product.get('start_date'),
            'end_date': product.get('end_date'),
            'discount_percent': product.get('discount_percent', 0),
            'source_product_id': product.get('product_key', ''),
            'stock_status': product.get('stock_status', 'in stock'),
            'promo_label': None
        }
        
        if existing:
            # Update existing product
            update_query = f"""
                UPDATE {schema}.product SET
                    product_name = %(product_name)s,
                    description = %(description)s,
                    original_price = %(original_price)s,
                    deal_price = %(deal_price)s,
                    image_url = %(image_url)s,
                    sale_url = %(sale_url)s,
                    deal_type = %(deal_type)s,
                    category = %(category)s,
                    retailer = %(retailer)s,
                    image_url_1 = %(image_url_1)s,
                    product_keywords = %(product_keywords)s,
                    product_type = %(product_type)s,
                    brand = %(brand)s,
                    category_list = %(category_list)s,
                    start_date = %(start_date)s,
                    end_date = %(end_date)s,
                    discount_percent = %(discount_percent)s,
                    stock_status = %(stock_status)s,
                    updated_at = %(updated_at)s
                WHERE product_key = %(product_key)s
                RETURNING product_id
            """
            cur.execute(update_query, product_data)
            result = cur.fetchone()
            return {'action': 'updated', 'product_id': result[0] if result else None}
        else:
            # Insert new product
            insert_query = f"""
                INSERT INTO {schema}.product (
                    product_name, description, original_price, deal_price, image_url, sale_url,
                    category_id, deal_type_id, seller_id, ts_vector, created_at, updated_at,
                    is_active, wix_id, owner, deal_type, category, retailer, image_url_1,
                    image_url_2, image_url_3, embedding, product_key, product_keywords,
                    product_rating, product_type, brand, coupon_info, category_list,
                    start_date, end_date, discount_percent, source_product_id, stock_status, promo_label
                ) VALUES (
                    %(product_name)s, %(description)s, %(original_price)s, %(deal_price)s, %(image_url)s, %(sale_url)s,
                    %(category_id)s, %(deal_type_id)s, %(seller_id)s, %(ts_vector)s, %(created_at)s, %(updated_at)s,
                    %(is_active)s, %(wix_id)s, %(owner)s, %(deal_type)s, %(category)s, %(retailer)s, %(image_url_1)s,
                    %(image_url_2)s, %(image_url_3)s, %(embedding)s, %(product_key)s, %(product_keywords)s,
                    %(product_rating)s, %(product_type)s, %(brand)s, %(coupon_info)s, %(category_list)s,
                    %(start_date)s, %(end_date)s, %(discount_percent)s, %(source_product_id)s, %(stock_status)s, %(promo_label)s
                ) RETURNING product_id
            """
            cur.execute(insert_query, product_data)
            result = cur.fetchone()
            return {'action': 'inserted', 'product_id': result[0] if result else None}
            
    except Exception as e:
        print(f"Error inserting/updating product {product.get('product_key', 'unknown')}: {e}")
        raise e

def lambda_handler(event, context):
    """Main Lambda handler for CSV import"""
    conn = None
    cur = None
    
    try:
        # Parse request body
        if 'body' in event:
            if isinstance(event['body'], str):
                body = json.loads(event['body'])
            else:
                body = event['body']
        else:
            body = event
        
        # Extract parameters
        products = body.get('products', [])
        environment = body.get('environment', 'staging')
        source = body.get('source', 'csv_import')
        
        if not products:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type',
                    'Access-Control-Allow-Methods': 'POST, OPTIONS'
                },
                'body': json.dumps({
                    'error': 'No products provided',
                    'message': 'Please provide products data in the request body'
                })
            }
        
        # Determine schema based on environment
        if environment == 'production':
            schema = 'deals_master'
        else:
            schema = 'deals_master'  # Use same schema for both environments
        
        print(f"Processing {len(products)} products for {environment} environment using schema {schema}")
        
        # Connect to database
        conn = get_db_connection()
        if not conn:
            return {
                'statusCode': 500,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type',
                    'Access-Control-Allow-Methods': 'POST, OPTIONS'
                },
                'body': json.dumps({
                    'error': 'Database connection failed',
                    'message': 'Unable to connect to the database'
                })
            }
        
        cur = conn.cursor()
        
        # Process products
        results = {
            'total': len(products),
            'inserted': 0,
            'updated': 0,
            'errors': 0,
            'error_details': []
        }
        
        for i, product in enumerate(products):
            try:
                # Validate product data
                validation_errors = validate_product_data(product)
                if validation_errors:
                    results['errors'] += 1
                    results['error_details'].append({
                        'row': i + 1,
                        'product_key': product.get('product_key', 'unknown'),
                        'errors': validation_errors
                    })
                    continue
                
                # Insert or update product
                result = insert_or_update_product(cur, product, schema)
                
                if result['action'] == 'inserted':
                    results['inserted'] += 1
                elif result['action'] == 'updated':
                    results['updated'] += 1
                    
            except Exception as e:
                results['errors'] += 1
                results['error_details'].append({
                    'row': i + 1,
                    'product_key': product.get('product_key', 'unknown'),
                    'errors': [str(e)]
                })
                print(f"Error processing product {i + 1}: {e}")
        
        # Commit transaction
        conn.commit()
        
        print(f"Import completed: {results['inserted']} inserted, {results['updated']} updated, {results['errors']} errors")
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Allow-Methods': 'POST, OPTIONS'
            },
            'body': json.dumps({
                'message': 'CSV import completed successfully',
                'results': results,
                'environment': environment,
                'schema': schema,
                'source': source
            }, cls=DateTimeEncoder)
        }
        
    except Exception as e:
        print(f"Lambda error: {e}")
        
        # Rollback transaction if there was an error
        if conn:
            try:
                conn.rollback()
            except:
                pass
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Allow-Methods': 'POST, OPTIONS'
            },
            'body': json.dumps({
                'error': 'Internal server error',
                'message': str(e)
            })
        }
        
    finally:
        # Close database connections
        if cur:
            try:
                cur.close()
            except:
                pass
        if conn:
            try:
                conn.close()
            except:
                pass
