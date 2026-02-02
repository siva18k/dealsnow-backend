import json
import pg8000
import os
from decimal import Decimal
import boto3
from botocore.config import Config
import datetime  # Import datetime module for serialization

# AWS configuration
AWS_REGION = 'us-east-2'
BEDROCK_MODEL_ID = 'amazon.titan-embed-text-v2:0'
secrets_client = boto3.client('secretsmanager', region_name=AWS_REGION)

# Custom JSON encoder to handle datetime objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        elif isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

def format_results(cur, products):
    """Format database results into JSON-compatible dictionary"""
    if not products:
        return []

    columns = [desc[0] for desc in cur.description]
    results = []
    for product in products:
        product_dict = dict(zip(columns, product))
        # Convert Decimal objects to float for numeric fields and datetime objects to ISO format
        for key, value in product_dict.items():
            if isinstance(value, Decimal):
                if key in ['price', 'orig_price']:
                    product_dict[key] = float(value)
                elif key == 'similarity_score':
                    product_dict[key] = float(value) if value is not None else None
                elif key == 'similarity_percentage':
                    product_dict[key] = float(value) if value is not None else None
                else:
                    product_dict[key] = str(value)
            elif isinstance(value, (datetime.datetime, datetime.date)):
                # Convert datetime objects to ISO format string
                product_dict[key] = value.isoformat()
        results.append(product_dict)
    return results

def get_db_connection():
    """Create database connection via Secrets Manager or env (DB_SECRET_NAME from CDK)."""
    try:
        secret_name = os.environ.get('DB_SECRET_NAME')
        if secret_name:
            secret_response = secrets_client.get_secret_value(SecretId=secret_name)
            credentials = json.loads(secret_response['SecretString'])
            db_host = credentials.get('host') or credentials.get('endpoint')
            db_port = int(credentials.get('port', 5432))
            db_name = credentials.get('dbname') or credentials.get('database') or 'postgres'
            db_user = credentials.get('username') or credentials.get('user')
            db_pass = credentials.get('password')
            conn = pg8000.connect(
                host=db_host,
                port=db_port,
                database=db_name,
                user=db_user,
                password=db_pass
            )
            return conn
        if os.environ.get('DB_HOST') or os.environ.get('PG_HOST'):
            host = os.environ.get('DB_HOST') or os.environ.get('PG_HOST')
            database = os.environ.get('DB_NAME') or os.environ.get('PG_DATABASE') or 'postgres'
            user = os.environ.get('DB_USER') or os.environ.get('PG_USER')
            password = os.environ.get('DB_PASSWORD') or os.environ.get('PG_PASSWORD')
            port = int(os.environ.get('DB_PORT', os.environ.get('PG_PORT', 5432)))
            return pg8000.connect(host=host, database=database, user=user, password=password, port=port)
        print("Database connection: DB_SECRET_NAME or DB_HOST/PG_* not set")
        return None
    except pg8000.Error as e:
        print(f"Database connection error: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error during database connection: {e}")
        return None

def lambda_handler(event, context):
    """Main Lambda handler"""
    conn = None
    cur = None

    try:
        # --- PROXY INTEGRATION SUPPORT ---
        environment = None
        operation = None
        filters = {}
        product_ids = []
        schema = None
        country = None

        # 1. Try queryStringParameters (for GET requests)
        if 'queryStringParameters' in event and event['queryStringParameters']:
            query_params = event['queryStringParameters']
            environment = query_params.get('environment')
            operation = query_params.get('operation')
            schema = query_params.get('schema')
            country = query_params.get('country')

            # Extract limit parameter with validation
            limit_param = query_params.get('limit')
            if limit_param:
                try:
                    limit = int(limit_param)
                    # Cap limit to prevent excessive queries (max 1000)
                    limit = min(max(limit, 1), 1000)
                except (ValueError, TypeError):
                    limit = 100  # Default if invalid
            else:
                limit = 100  # Default limit

            # Extract filter parameters from query string
            filters = {
                'name': query_params.get('name'),
                'category': query_params.get('category'),
                'dealType': query_params.get('dealType'),
                'dealTypeId': query_params.get('dealTypeId'),
                'retailer': query_params.get('retailer'),
                'promoDeal': query_params.get('promoDeal'),
                'is_active': query_params.get('is_active'),
                'isActive': query_params.get('isActive'),
                'discountMin': query_params.get('discountMin'),
                'discountMax': query_params.get('discountMax'),
                'showActiveDealsOnly': query_params.get('showActiveDealsOnly') == 'true'
            }
            # Remove None values
            filters = {k: v for k, v in filters.items() if v is not None}

        # 2. Try body (for POST requests)
        if event.get('body'):
            try:
                if isinstance(event['body'], str):
                    body = json.loads(event['body'])
                else:
                    body = event['body']

                # Extract data from POST body
                if not environment and 'environment' in body:
                    environment = body['environment']
                if not operation and 'operation' in body:
                    operation = body['operation']
                if not schema and 'schema' in body:
                    schema = body['schema']
                if not country and 'country' in body:
                    country = body['country']
                if 'product_ids' in body:
                    product_ids = body['product_ids']
                if 'filters' in body:
                    filters.update(body['filters'])

                # Extract limit from POST body if not already set from query params
                if 'limit' not in locals() and 'limit' in body:
                    try:
                        limit = int(body['limit'])
                        # Cap limit to prevent excessive queries (max 1000)
                        limit = min(max(limit, 1), 1000)
                    except (ValueError, TypeError):
                        limit = 100  # Default if invalid

            except Exception as e:
                print(f"Error parsing body: {e}")
                return {
                    'statusCode': 400,
                    'body': json.dumps({'error': f'Invalid JSON in request body: {str(e)}'}),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }

        # Default to production if no environment specified
        environment = environment or 'production'

        # Default country and determine schema based on country
        country = (country or os.environ.get('COUNTRY', 'US')).upper()

        # Default limit if not set
        if 'limit' not in locals():
            limit = 100
        
        # Determine schema based on country if not explicitly provided
        if not schema:
            if country in ['INDIA', 'IN']:
                schema = 'deals_india'
            elif country in ['US', 'USA', 'UNITED STATES']:
                schema = 'deals_master'
            else:
                # Default to US schema for unknown countries
                schema = 'deals_master'
                print(f"⚠️ Unknown country '{country}', defaulting to deals_master schema")

        # Debug logging
        print(f"🔍 Lambda products_management Debug:")
        print(f"   Environment: {environment}")
        print(f"   Country: {country}")
        print(f"   Schema: {schema}")
        print(f"   Operation: {operation}")
        print(f"   Limit: {limit}")
        print(f"   Operation type: {type(operation)}")
        print(f"   Operation is None: {operation is None}")
        print(f"   Operation is empty string: {operation == ''}")
        print(f"   Bool(operation): {bool(operation)}")
        if operation:
            print(f"   'staging' in operation: {'staging' in operation}")

        # Validate schema exists for the country
        valid_schemas = ['deals_master', 'deals_india']
        if schema not in valid_schemas:
            print(f"❌ Invalid schema '{schema}', must be one of: {valid_schemas}")
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'Invalid schema: {schema}. Valid schemas: {valid_schemas}',
                    'country': country,
                    'schema': schema
                }),
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                }
            }

        # Determine the correct table based on environment and schema
        table_name = f'{schema}.product_staging' if environment == 'staging' else f'{schema}.product'
        print(f"   Table: {table_name}")
        
        # Validate country-schema consistency
        expected_schema = 'deals_india' if country in ['INDIA', 'IN'] else 'deals_master'
        if schema != expected_schema:
            print(f"⚠️ Schema mismatch: Country '{country}' typically uses '{expected_schema}' but got '{schema}'")

        conn = get_db_connection()
        if not conn:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Database connection failed'}),
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                }
            }

        cur = conn.cursor()
        products = []

        # --- Handle delete_products operation ---
        if operation == 'delete_products':
            if not product_ids or not isinstance(product_ids, list):
                return {
                    'statusCode': 400,
                    'body': json.dumps({'error': 'No product IDs provided or invalid format'}),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }
            
            try:
                # First, check what products exist with these IDs
                check_placeholders = ','.join(['%s'] * len(product_ids))
                check_query = f"SELECT product_id, product_name FROM {table_name} WHERE product_id IN ({check_placeholders})"
                
                cur.execute(check_query, product_ids)
                existing_products = cur.fetchall()
                
                if not existing_products:
                    return {
                        'statusCode': 400,
                        'body': json.dumps({
                            'error': 'No products found with the provided IDs',
                            'product_ids_received': product_ids,
                            'table': table_name
                        }),
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*'
                        }
                    }
                
                # Now proceed with deletion
                placeholders = ','.join(['%s'] * len(product_ids))
                delete_query = f"DELETE FROM {table_name} WHERE product_id IN ({placeholders})"
                
                cur.execute(delete_query, product_ids)
                deleted_count = cur.rowcount
                conn.commit()
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'success': True,
                        'message': f'Successfully deleted {deleted_count} products',
                        'deleted_count': deleted_count,
                        'environment': environment
                    }, cls=DateTimeEncoder),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }
                
            except Exception as e:
                print(f"Error executing delete query: {e}")
                if hasattr(cur, 'connection') and cur.connection:
                    cur.connection.rollback()
                return {
                    'statusCode': 500,
                    'body': json.dumps({
                        'error': f'Database delete error: {str(e)}',
                        'table': table_name
                    }),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }

        # --- Handle update_data operation ---
        elif operation == 'update_data':
            try:
                # Extract products data from body
                if event.get('body'):
                    if isinstance(event['body'], str):
                        body_data = json.loads(event['body'])
                    else:
                        body_data = event['body']
                else:
                    return {
                        'statusCode': 400,
                        'body': json.dumps({'error': 'No data provided'}),
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*'
                        }
                    }

                products = body_data.get('products', [])
                if not products or not isinstance(products, list):
                    return {
                        'statusCode': 400,
                        'body': json.dumps({'error': 'No products provided for update'}),
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*'
                        }
                    }

                updated_count = 0
                errors = []

                for product in products:
                    try:
                        # Build update query dynamically based on provided fields
                        update_fields = []
                        update_values = []
                        
                        # Map frontend field names to database column names
                        field_mapping = {
                            'name': 'product_name',
                            'description': 'description',
                            'price': 'deal_price',
                            'orig_price': 'original_price',
                            'image': 'image_url',
                            'image_url_1': 'image_url_1',
                            'image_url_2': 'image_url_2',
                            'image_url_3': 'image_url_3',
                            'category': 'category',
                            'deal_type': 'deal_type',
                            'deal_type_id': 'deal_type_id',
                            'retailer': 'retailer',
                            'product_key': 'product_key',
                            'product_rating': 'product_rating',
                            'keywords': 'product_keywords',
                            'sale_url': 'sale_url',
                            'is_active': 'is_active',
                            'brand': 'brand',
                            'discount_percent': 'discount_percent',
                            'product_type': 'product_type',
                            'coupon_info': 'coupon_info',
                            'category_list': 'category_list',
                            'start_date': 'start_date',
                            'end_date': 'end_date',
                            'promo_label': 'promo_label'
                        }

                        for frontend_field, db_field in field_mapping.items():
                            if frontend_field in product and product[frontend_field] is not None:
                                update_fields.append(f"{db_field} = %s")
                                update_values.append(product[frontend_field])

                        if not update_fields:
                            errors.append(f"Product {product.get('id', 'unknown')}: No valid fields to update")
                            continue

                        # Add updated_at timestamp
                        update_fields.append("updated_at = NOW()")
                        
                        # Add product_id to values for WHERE clause
                        update_values.append(product['id'])
                        
                        update_query = f"""
                            UPDATE {table_name} 
                            SET {', '.join(update_fields)}
                            WHERE product_id = %s
                        """
                        
                        cur.execute(update_query, update_values)
                        if cur.rowcount > 0:
                            updated_count += 1
                        else:
                            errors.append(f"Product {product['id']}: No rows affected (product not found)")
                            
                    except Exception as e:
                        errors.append(f"Product {product.get('id', 'unknown')}: {str(e)}")

                conn.commit()

                if errors:
                    return {
                        'statusCode': 200,
                        'body': json.dumps({
                            'success': True,
                            'message': f'Updated {updated_count} products with {len(errors)} errors',
                            'updated_count': updated_count,
                            'errors': errors,
                            'environment': environment
                        }, cls=DateTimeEncoder),
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*'
                        }
                    }
                else:
                    return {
                        'statusCode': 200,
                        'body': json.dumps({
                            'success': True,
                            'message': f'Successfully updated {updated_count} products',
                            'updated_count': updated_count,
                            'environment': environment
                        }, cls=DateTimeEncoder),
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*'
                        }
                    }

            except Exception as e:
                print(f"Error updating products: {e}")
                if hasattr(cur, 'connection') and cur.connection:
                    cur.connection.rollback()
                return {
                    'statusCode': 500,
                    'body': json.dumps({
                        'error': f'Database update error: {str(e)}',
                        'table': table_name
                    }),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }

        # --- Handle submit_deal operation ---
        elif operation == 'submit_deal':
            try:
                # Extract deal data from body
                if event.get('body'):
                    if isinstance(event['body'], str):
                        deal_data = json.loads(event['body'])
                    else:
                        deal_data = event['body']
                else:
                    return {
                        'statusCode': 400,
                        'body': json.dumps({'error': 'No deal data provided'}),
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*'
                        }
                    }

                # Validate required fields
                required_fields = ['title', 'description', 'price', 'list_price', 'image_url', 'sale_url']
                missing_fields = [field for field in required_fields if not deal_data.get(field)]
                
                if missing_fields:
                    return {
                        'statusCode': 400,
                        'body': json.dumps({
                            'error': f'Missing required fields: {", ".join(missing_fields)}'
                        }),
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*'
                        }
                    }

                # Insert new deal into database
                insert_query = f"""
                    INSERT INTO {table_name} (
                        product_name, description, deal_price, original_price, 
                        image_url, image_url_1, image_url_2, image_url_3,
                        category, deal_type, deal_type_id, retailer, 
                        sale_url, product_keywords, product_rating, 
                        is_active, brand, product_type, coupon_info, 
                        category_list, start_date, end_date, promo_label,
                        product_key, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW()
                    ) RETURNING product_id
                """
                
                # Prepare values for insertion
                values = (
                    deal_data.get('title'),
                    deal_data.get('description'),
                    float(deal_data.get('price', 0)),
                    float(deal_data.get('list_price', 0)),
                    deal_data.get('image_url'),
                    deal_data.get('image_url_1', ''),
                    deal_data.get('image_url_2', ''),
                    deal_data.get('image_url_3', ''),
                    deal_data.get('category', 'Home'),
                    deal_data.get('deal_type', 'Hot Deal'),
                    int(deal_data.get('deal_type_id', 1)),
                    deal_data.get('retailer', 'Amazon'),
                    deal_data.get('sale_url'),
                    deal_data.get('product_keywords', ''),
                    float(deal_data.get('rating', 4.5)),
                    deal_data.get('is_active', True),
                    deal_data.get('brand', ''),
                    deal_data.get('product_type', 'Electronics'),
                    deal_data.get('coupon_info', ''),
                    deal_data.get('category_list', ''),
                    deal_data.get('start_date') if deal_data.get('start_date') else None,
                    deal_data.get('end_date') if deal_data.get('end_date') else None,
                    deal_data.get('promo_label') if deal_data.get('promo_label') else None,
                    deal_data.get('asin', '99999')
                )
                
                cur.execute(insert_query, values)
                result = cur.fetchone()
                new_product_id = result[0] if result else None
                conn.commit()
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'success': True,
                        'message': f'Successfully submitted deal: {deal_data.get("title")}',
                        'product_id': new_product_id,
                        'environment': environment
                    }, cls=DateTimeEncoder),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }
                
            except Exception as e:
                print(f"Error submitting deal: {e}")
                if hasattr(cur, 'connection') and cur.connection:
                    cur.connection.rollback()
                return {
                    'statusCode': 500,
                    'body': json.dumps({
                        'error': f'Database insert error: {str(e)}',
                        'table': table_name
                    }),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }

        # --- Handle fetch_options operation ---
        elif operation == 'fetch_options':
            try:
                print(f"🔍 Fetching dropdown options for environment: {environment}")
                print(f"🔍 Using table: {table_name}")
                
                # Get categories from {schema}.categories table
                try:
                    categories_query = f"SELECT DISTINCT category FROM {schema}.categories WHERE is_active = true ORDER BY category"
                    print(f"🔍 Categories query with is_active: {categories_query}")
                    cur.execute(categories_query)
                    categories_result = cur.fetchall()
                    categories = [row[0] for row in categories_result] if categories_result else []
                except Exception as e:
                    print(f"⚠️ is_active column not found or table doesn't exist, trying without filter: {e}")
                    try:
                        categories_query = f"SELECT DISTINCT category FROM {schema}.categories ORDER BY category"
                        print(f"🔍 Categories query without is_active: {categories_query}")
                        cur.execute(categories_query)
                        categories_result = cur.fetchall()
                        categories = [row[0] for row in categories_result] if categories_result else []
                    except Exception as e2:
                        print(f"❌ Categories table not found in schema {schema}: {e2}")
                        categories = []
                print(f"🔍 Found {len(categories)} categories: {categories}")
                
                # Get retailers from {schema}.retailers table
                try:
                    retailers_query = f"SELECT DISTINCT retailer FROM {schema}.retailers WHERE is_active = true ORDER BY retailer"
                    print(f"🔍 Retailers query with is_active: {retailers_query}")
                    cur.execute(retailers_query)
                    retailers_result = cur.fetchall()
                    retailers = [row[0] for row in retailers_result] if retailers_result else []
                except Exception as e:
                    print(f"⚠️ is_active column not found or table doesn't exist, trying without filter: {e}")
                    try:
                        retailers_query = f"SELECT DISTINCT retailer FROM {schema}.retailers ORDER BY retailer"
                        print(f"🔍 Retailers query without is_active: {retailers_query}")
                        cur.execute(retailers_query)
                        retailers_result = cur.fetchall()
                        retailers = [row[0] for row in retailers_result] if retailers_result else []
                    except Exception as e2:
                        print(f"❌ Retailers table not found in schema {schema}: {e2}")
                        retailers = []
                print(f"🔍 Found {len(retailers)} retailers: {retailers}")
                
                # Get promo labels from {schema}.promo_master table
                try:
                    promo_labels_query = f"SELECT DISTINCT promo_label FROM {schema}.promo_master WHERE is_active = true ORDER BY promo_label"
                    print(f"🔍 Promo labels query with is_active: {promo_labels_query}")
                    cur.execute(promo_labels_query)
                    promo_labels_result = cur.fetchall()
                    promo_labels = [row[0] for row in promo_labels_result] if promo_labels_result else []
                except Exception as e:
                    print(f"⚠️ is_active column not found or table doesn't exist, trying without filter: {e}")
                    try:
                        promo_labels_query = f"SELECT DISTINCT promo_label FROM {schema}.promo_master ORDER BY promo_label"
                        print(f"🔍 Promo labels query without is_active: {promo_labels_query}")
                        cur.execute(promo_labels_query)
                        promo_labels_result = cur.fetchall()
                        promo_labels = [row[0] for row in promo_labels_result] if promo_labels_result else []
                    except Exception as e2:
                        print(f"❌ Promo_master table not found in schema {schema}: {e2}")
                        promo_labels = []
                print(f"🔍 Found {len(promo_labels)} promo labels: {promo_labels}")
                
                # Get deal types and product types from the main product table for backward compatibility
                product_options_query = f"""
                    SELECT 
                        ARRAY_AGG(DISTINCT deal_type) FILTER (WHERE deal_type IS NOT NULL AND deal_type != '') as deal_types,
                        ARRAY_AGG(DISTINCT product_type) FILTER (WHERE product_type IS NOT NULL AND product_type != '') as product_types
                    FROM {table_name}
                """
                print(f"🔍 Product options query: {product_options_query}")
                cur.execute(product_options_query)
                product_options_result = cur.fetchone()
                
                deal_types = sorted(product_options_result[0] or []) if product_options_result else []
                product_types = sorted(product_options_result[1] or []) if product_options_result else []
                print(f"🔍 Found {len(deal_types)} deal types: {deal_types}")
                print(f"🔍 Found {len(product_types)} product types: {product_types}")
                
                options = {
                    'categories': categories,
                    'dealTypes': deal_types,
                    'retailers': retailers,
                    'productTypes': product_types,
                    'promoLabels': promo_labels
                }
                
                print(f"🔍 Final options: {options}")
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'success': True,
                        'options': options,
                        'environment': environment
                    }, cls=DateTimeEncoder),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }
                
            except Exception as e:
                print(f"❌ Error fetching options: {e}")
                # Return empty options instead of error to prevent frontend issues
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'success': True,
                        'options': {
                            'categories': [],
                            'dealTypes': [],
                            'retailers': [],
                            'productTypes': [],
                            'promoLabels': []
                        },
                        'environment': environment,
                        'error': f'Database options error: {str(e)}'
                    }, cls=DateTimeEncoder),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }

        # --- Handle fetch_filtered_products operation ---
        elif operation == 'fetch_filtered_products':
            # Normalize filter keys for is_active (accept both is_active and isActive)
            if 'isActive' in filters and 'is_active' not in filters:
                filters['is_active'] = filters['isActive']
            # Build WHERE clauses based on filters
            where_clauses = []
            params = []

            if filters.get('name'):
                where_clauses.append("LOWER(product_name) LIKE %s")
                params.append(f"%{filters['name'].lower()}%")
                
            if filters.get('category'):
                where_clauses.append("category = %s")
                params.append(filters['category'])
                
            if filters.get('dealType'):
                where_clauses.append("deal_type = %s")
                params.append(filters['dealType'])
                
            if filters.get('retailer'):
                where_clauses.append("retailer = %s")
                params.append(filters['retailer'])
                
            if filters.get('promoDeal'):
                where_clauses.append("promo_label = %s")
                params.append(filters['promoDeal'])
            # Add is_active filter support
            if filters.get('is_active') == 'true':
                where_clauses.append("is_active = TRUE")
            elif filters.get('is_active') == 'false':
                where_clauses.append("is_active = FALSE")
            # Add discount percent filter support
            if filters.get('discountMin'):
                where_clauses.append("(CASE WHEN original_price != 0 THEN ((original_price - deal_price) / original_price * 100) ELSE 0 END) >= %s")
                params.append(float(filters['discountMin']))
            if filters.get('discountMax'):
                where_clauses.append("(CASE WHEN original_price != 0 THEN ((original_price - deal_price) / original_price * 100) ELSE 0 END) <= %s")
                params.append(float(filters['discountMax']))
            if filters.get('dealTypeId'):
                where_clauses.append("deal_type_id = %s")
                params.append(int(filters['dealTypeId']))

            where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            
            query = f"""
                SELECT
                    product_id,
                    product_name as name,
                    description,
                    deal_price as price,
                    original_price as orig_price,
                    CASE
                        WHEN original_price != 0 THEN ((original_price - deal_price) / original_price * 100)
                        ELSE 0
                    END as discount_percent,
                    image_url as image,
                    category_id,
                    category,
                    seller_id,
                    retailer,
                    deal_type_id,
                    deal_type,
                    sale_url,
                    image_url_1,
                    image_url_2,
                    image_url_3,
                    NULL as similarity_score,
                    NULL as similarity_percentage,
                    product_keywords,
                    product_key,
                    product_rating,
                    is_active,
                    brand,
                    product_type,
                    coupon_info,
                    category_list,
                    start_date,
                    end_date,
                    promo_label
                FROM {table_name}
                {where_sql}
                ORDER BY updated_at DESC
                LIMIT {limit};
            """
            
            try:
                cur.execute(query, tuple(params) if params else ())
                products = cur.fetchall()
            except Exception as e:
                print(f"Error executing filtered query: {e}")
                return {
                    'statusCode': 500,
                    'body': json.dumps({
                        'error': f'Database query error: {str(e)}',
                        'limit': limit
                    }),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }

        # --- Handle get_staging_products operation ---
        elif operation == 'get_staging_products':
            try:
                print(f"🔍 get_staging_products: Environment = {environment}, Table = {table_name}")
                print(f"🔍 Operation recognized: {operation}")
                print(f"🔍 Event received: {event}")
                print(f"🔍 Database connection status: {conn is not None}")
                print(f"🔍 Cursor status: {cur is not None}")
                
                # Check if database connection is working
                if not conn:
                    print("❌ Database connection is None")
                    return {
                        'statusCode': 500,
                        'body': json.dumps({'error': 'Database connection failed'}),
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*'
                        }
                    }
                
                # Query to get latest staging products sorted by updated_at DESC
                query = f"""
                    SELECT
                        product_id,
                        product_name,
                        description,
                        sale_url,
                        image_url,
                        image_url_1,
                        image_url_2,
                        image_url_3,
                        product_key,
                        original_price,
                        deal_price,
                        discount_percent,
                        category_list,
                        brand,
                        retailer,
                        updated_at,
                        end_date,
                        deal_type,
                        category,
                        source_product_id,
                        product_keywords,
                        product_type,
                        coupon_info,
                        start_date,
                        product_rating,
                        is_active,
                        promo_label,
                        deal_type_id
                    FROM {schema}.product_staging
                    ORDER BY product_id DESC
                    LIMIT {limit}
                """
                
                print(f"🔍 Executing query: {query}")
                try:
                    cur.execute(query)
                    results = cur.fetchall()
                    print(f"🔍 Found {len(results)} products in product_staging table")
                except Exception as query_error:
                    print(f"❌ Query execution error: {query_error}")
                    print(f"❌ Query that failed: {query}")
                    raise query_error
                
                # Also check the main product table to see if there are products there
                check_main_table_query = f"""
                    SELECT COUNT(*) as count, 
                           COUNT(CASE WHEN deal_type_id IS NULL OR deal_type_id = 0 THEN 1 END) as null_deal_type_count
                    FROM {schema}.product 
                    WHERE is_active = true
                """
                cur.execute(check_main_table_query)
                main_table_stats = cur.fetchone()
                print(f"🔍 Main product table stats: Total={main_table_stats[0]}, Null deal_type_id={main_table_stats[1]}")
                
                # Convert results to list of dictionaries
                products = []
                for row in results:
                    try:
                        product = {
                            'product_id': row[0],
                            'product_name': row[1],
                            'description': row[2],
                            'sale_url': row[3],
                            'image_url': row[4],
                            'image_url_1': row[5],
                            'image_url_2': row[6],
                            'image_url_3': row[7],
                            'product_key': row[8],
                            'original_price': float(row[9]) if row[9] else None,
                            'deal_price': float(row[10]) if row[10] else None,
                            'discount_percent': float(row[11]) if row[11] else None,
                            'category_list': row[12],
                            'brand': row[13],
                            'retailer': row[14],
                            'updated_at': row[15].isoformat() if row[15] else None,
                            'end_date': row[16].isoformat() if row[16] else None,
                            'deal_type': row[17],
                            'category': row[18],
                            'source_product_id': row[19],
                            'product_keywords': row[20],
                            'product_type': row[21],
                            'coupon_info': row[22],
                            'start_date': row[23].isoformat() if row[23] else None,
                            'product_rating': float(row[24]) if row[24] else None,
                            'is_active': row[25],
                            'promo_label': row[26],
                            'deal_type_id': row[27]
                        }
                        products.append(product)
                    except Exception as row_error:
                        print(f"❌ Error processing row: {row_error}, row data: {row}")
                        continue
                
                print(f"🔍 Returning {len(products)} products from staging")
                if products:
                    print(f"🔍 Sample product: {products[0]}")
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'success': True,
                        'products': products,
                        'count': len(products),
                        'limit': limit,
                        'debug_info': {
                            'environment': environment,
                            'table_queried': f'{schema}.product_staging',
                            'main_table_total': main_table_stats[0],
                            'main_table_null_deal_type': main_table_stats[1]
                        }
                    }, cls=DateTimeEncoder),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }
                
            except Exception as e:
                print(f"❌ Error fetching staging products: {e}")
                import traceback
                print(f"❌ Full traceback: {traceback.format_exc()}")
                return {
                    'statusCode': 500,
                    'body': json.dumps({'error': f'Failed to fetch staging products: {str(e)}'}),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }

        # --- Handle get_staging_products_from_main operation ---
        elif operation == 'get_staging_products_from_main':
            try:
                print(f"🔍 get_staging_products_from_main: Environment = {environment}, Table = {table_name}")
                
                # Check if database connection is working
                if not conn:
                    print("❌ Database connection is None")
                    return {
                        'statusCode': 500,
                        'body': json.dumps({'error': 'Database connection failed'}),
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*'
                        }
                    }
                
                # Query to get latest products from main table sorted by updated_at DESC
                query = f"""
                    SELECT
                        product_id,
                        product_name,
                        description,
                        sale_url,
                        image_url,
                        image_url_1,
                        image_url_2,
                        image_url_3,
                        product_key,
                        original_price,
                        deal_price,
                        discount_percent,
                        category_list,
                        brand,
                        retailer,
                        updated_at,
                        end_date,
                        deal_type,
                        category,
                        source_product_id,
                        product_keywords,
                        product_type,
                        coupon_info,
                        start_date,
                        product_rating,
                        is_active,
                        promo_label,
                        deal_type_id
                    FROM {schema}.product
                    WHERE is_active = true
                    ORDER BY updated_at DESC
                    LIMIT {limit}
                """
                
                print(f"🔍 Executing query: {query}")
                try:
                    cur.execute(query)
                    results = cur.fetchall()
                    print(f"🔍 Found {len(results)} products in main product table")
                except Exception as query_error:
                    print(f"❌ Query execution error: {query_error}")
                    print(f"❌ Query that failed: {query}")
                    raise query_error
                
                # Convert results to list of dictionaries
                products = []
                for row in results:
                    try:
                        product = {
                            'product_id': row[0],
                            'product_name': row[1],
                            'description': row[2],
                            'sale_url': row[3],
                            'image_url': row[4],
                            'image_url_1': row[5],
                            'image_url_2': row[6],
                            'image_url_3': row[7],
                            'product_key': row[8],
                            'original_price': float(row[9]) if row[9] else None,
                            'deal_price': float(row[10]) if row[10] else None,
                            'discount_percent': float(row[11]) if row[11] else None,
                            'category_list': row[12],
                            'brand': row[13],
                            'retailer': row[14],
                            'updated_at': row[15].isoformat() if row[15] else None,
                            'end_date': row[16].isoformat() if row[16] else None,
                            'deal_type': row[17],
                            'category': row[18],
                            'source_product_id': row[19],
                            'product_keywords': row[20],
                            'product_type': row[21],
                            'coupon_info': row[22],
                            'start_date': row[23].isoformat() if row[23] else None,
                            'product_rating': float(row[24]) if row[24] else None,
                            'is_active': row[25],
                            'promo_label': row[26],
                            'deal_type_id': row[27]
                        }
                        products.append(product)
                    except Exception as row_error:
                        print(f"❌ Error processing row: {row_error}, row data: {row}")
                        continue
                
                print(f"🔍 Returning {len(products)} products from main table (staging)")
                if products:
                    print(f"🔍 Sample product: {products[0]}")
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'success': True,
                        'products': products,
                        'count': len(products),
                        'limit': limit,
                        'debug_info': {
                            'environment': environment,
                            'table_queried': f'{schema}.product',
                            'filter': 'deal_type_id IS NULL OR deal_type_id = 0 OR deal_type_id = ""'
                        }
                    }, cls=DateTimeEncoder),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }
                
            except Exception as e:
                print(f"❌ Error fetching staging products from main table: {e}")
                import traceback
                print(f"❌ Full traceback: {traceback.format_exc()}")
                return {
                    'statusCode': 500,
                    'body': json.dumps({'error': f'Failed to fetch staging products from main table: {str(e)}'}),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }

        else:
            # Debug logging for this else case
            print(f"🔍 No specific operation matched. operation = '{operation}'")
            print(f"🔍 operation type: {type(operation)}")
            print(f"🔍 bool(operation): {bool(operation)}")
            
            # Check if this is a staging operation that wasn't recognized
            if operation and 'staging' in operation:
                print(f"❌ Staging operation not recognized: {operation}")
                return {
                    'statusCode': 400,
                    'body': json.dumps({
                        'error': f'Staging operation not implemented: {operation}',
                        'available_operations': ['get_staging_products', 'get_staging_products_from_main']
                    }),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }
            
            # Default query for initial load
            try:
                query = f"""
                    SELECT
                        product_id,
                        product_name as name,
                        description,
                        deal_price as price,
                        original_price as orig_price,
                        CASE
                            WHEN original_price != 0 THEN ((original_price - deal_price) / original_price * 100)
                            ELSE 0
                        END as discount_percent,
                        image_url as image,
                        category_id,
                        category,
                        seller_id,
                        retailer,
                        deal_type_id,
                        deal_type,
                        sale_url,
                        image_url_1,
                        image_url_2,
                        image_url_3,
                        NULL as similarity_score,
                        NULL as similarity_percentage,
                        product_keywords,
                        product_key,
                        product_rating,
                        is_active,
                        brand,
                        product_type,
                        coupon_info,
                        category_list,
                        start_date,
                        end_date,
                        promo_label
                    FROM {table_name}
                    ORDER BY updated_at DESC
                    LIMIT {limit};
                """
                cur.execute(query)
                products = cur.fetchall()
            except pg8000.Error as e:
                print(f"Database error during default search: {e}")
                if hasattr(cur, 'connection') and cur.connection:
                    cur.connection.rollback()
                return {
                    'statusCode': 500,
                    'body': json.dumps({'error': f'Database error: {str(e)}'}),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }
            except Exception as e:
                print(f"Unexpected error during default search: {e}")
                return {
                    'statusCode': 500,
                    'body': json.dumps({'error': f'Query error: {str(e)}'}),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    }
                }

        # Format results
        results = format_results(cur, products)

        # Create the response data in the format expected by React
        response_data = {
            'products': results,
            'environment': environment,
            'source': table_name,
            'operation': operation or 'default',
            'limit': limit,
            'count': len(results)
        }

        if not results:
            response_data['message'] = 'No products found'

        return {
            'statusCode': 200,
            'body': json.dumps(response_data, cls=DateTimeEncoder),
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            }
        }

    except Exception as e:
        print(f"Lambda handler error: {e}")
        if hasattr(cur, 'connection') and cur.connection:
            cur.connection.rollback()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Server error: {str(e)}'}, cls=DateTimeEncoder),
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            }
        }

    finally:
        if cur:
            try:
                cur.close()
            except Exception as e:
                print(f"Error closing cursor: {e}")
        if conn:
            try:
                conn.close()
            except Exception as e:
                print(f"Error closing connection: {e}")
