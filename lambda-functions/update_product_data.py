import json
import os
import sys
import time
from datetime import datetime
import pg8000
import boto3

# Database configuration (env fallback when not using Secrets Manager)
DB_HOST = os.environ.get('DB_HOST')
DB_NAME = os.environ.get('DB_NAME')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_PORT = int(os.environ.get('DB_PORT', 5432))

def clean_text_field(text):
    """Clean text fields by replacing common HTML entities and formatting issues."""
    if not text:
        return text
    
    # Replace &quot with inch
    text = text.replace('&quot', 'inch')
    
    return text

def get_db_connection():
    """Connect via AWS Secrets Manager (Aurora PostgreSQL) or DB_* env. Port 5432 for Aurora."""
    try:
        secret_name = os.environ.get('DB_SECRET_NAME') or os.environ.get('DB_SECRET_ARN')
        if secret_name:
            client = boto3.client('secretsmanager')
            r = client.get_secret_value(SecretId=secret_name)
            cred = json.loads(r['SecretString'])
            return pg8000.connect(
                host=cred.get('host') or cred.get('endpoint'),
                port=int(cred.get('port', 5432)),
                database=cred.get('dbname') or cred.get('database') or 'postgres',
                user=cred.get('username') or cred.get('user'),
                password=cred.get('password')
            )
        if not all([DB_HOST, DB_USER, DB_PASSWORD]):
            print("Missing required database configuration (DB_SECRET_NAME or DB_HOST/DB_USER/DB_PASSWORD)")
            return None
        return pg8000.connect(
            host=DB_HOST,
            database=DB_NAME or 'postgres',
            user=DB_USER,
            password=DB_PASSWORD,
            port=DB_PORT
        )
    except pg8000.Error as e:
        print(f"Database connection error: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error during database connection: {e}")
        return None

def insert_product(product_data, table_name):
    """Insert a new product into the database."""
    conn = None
    cur = None
    
    try:
        conn = get_db_connection()
        if not conn:
            return {
                "success": False,
                "message": "Failed to connect to database"
            }
            
        cur = conn.cursor()
        
        # Map of frontend field names to database column names
        # Based on actual product_staging table columns
        field_mapping = {
            'product_name': 'product_name',
            'description': 'description',
            'deal_price': 'deal_price',
            'original_price': 'original_price',
            'image_url': 'image_url',
            'image_url_1': 'image_url_1',
            'image_url_2': 'image_url_2', 
            'image_url_3': 'image_url_3',
            'category': 'category',
            'deal_type': 'deal_type',
            'retailer': 'retailer',
            'sale_url': 'sale_url',
            'product_key': 'product_key',
            'product_rating': 'product_rating',
            'product_keywords': 'product_keywords',
            'is_active': 'is_active',
            'deal_type_id': 'deal_type_id',
            'brand': 'brand',
            'discount_percent': 'discount_percent',
            'product_type': 'product_type',
            'coupon_info': 'coupon_info',
            'category_list': 'category_list',
            'start_date': 'start_date',
            'end_date': 'end_date',
            'promo_label': 'promo_label',
            'stock_status': 'stock_status'
            # Note: Excluded columns that are auto-generated or not provided by CSV:
            # product_id, category_id, seller_id, ts_vector, wix_id, owner, embedding, 
            # iscount_percent, source_product_id
        }
        
        # Prepare column names and values for insertion
        columns = []
        placeholders = []
        values = []
        
        # Add timestamps
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        columns.extend(['created_at', 'updated_at'])
        placeholders.extend(['%s', '%s'])
        values.extend([now, now])
        
        # Add product fields
        for frontend_field, db_field in field_mapping.items():
            if frontend_field in product_data:
                columns.append(db_field)
                placeholders.append('%s')
                
                # Handle special cases for empty dates
                if db_field in ('start_date', 'end_date') and product_data[frontend_field] == '':
                    values.append(None)
                # Handle discount_percent as integer
                elif db_field == 'discount_percent':
                    try:
                        discount_value = float(product_data[frontend_field]) if product_data[frontend_field] else 0
                        values.append(int(round(discount_value)))
                    except (ValueError, TypeError):
                        values.append(0)
                # Clean text fields (product_name and description)
                elif db_field in ('product_name', 'description'):
                    values.append(clean_text_field(product_data[frontend_field]))
                else:
                    values.append(product_data[frontend_field])
        
        # Build and execute upsert query (INSERT or UPDATE on conflict)
        # First, check if product_key exists
        product_key = None
        existing_product = None
        
        for i, col in enumerate(columns):
            if col == 'product_key':
                product_key = values[i]
                break
        
        if product_key:
            # Check if product already exists
            cur.execute(f"SELECT product_id FROM {table_name} WHERE product_key = %s", (product_key,))
            existing_product = cur.fetchone()
            
            if existing_product:
                # Product exists, update it instead of inserting
                print(f"Product with key {product_key} already exists, updating...")
                
                # Build update query (exclude created_at and product_id from updates)
                update_columns = [col for col in columns if col not in ['created_at', 'product_id']]
                update_values = [values[i] for i, col in enumerate(columns) if col not in ['created_at', 'product_id']]
                
                update_parts = []
                for col in update_columns:
                    update_parts.append(f"{col} = %s")
                
                update_query = f"""
                    UPDATE {table_name}
                    SET {', '.join(update_parts)}
                    WHERE product_key = %s
                    RETURNING product_id
                """
                update_values.append(product_key)
                
                print(f"Update query: {update_query}")
                print(f"Update values: {update_values}")
                
                cur.execute(update_query, update_values)
                new_product_id = cur.fetchone()[0]
                print(f"✅ Updated existing product with ID: {new_product_id}")
            else:
                # Product doesn't exist, insert new one
                insert_query = f"""
                    INSERT INTO {table_name} (
                        {', '.join(columns)}
                    ) VALUES (
                        {', '.join(placeholders)}
                    ) RETURNING product_id
                """
                
                print(f"Insert query: {insert_query}")
                print(f"Values: {values}")
                
                cur.execute(insert_query, values)
                new_product_id = cur.fetchone()[0]
                print(f"✅ Inserted new product with ID: {new_product_id}")
        else:
            # No product_key provided, use regular insert
            insert_query = f"""
                INSERT INTO {table_name} (
                    {', '.join(columns)}
                ) VALUES (
                    {', '.join(placeholders)}
                ) RETURNING product_id
            """
            
            print(f"Insert query: {insert_query}")
            print(f"Values: {values}")
            
            cur.execute(insert_query, values)
            new_product_id = cur.fetchone()[0]
            print(f"✅ Inserted new product with ID: {new_product_id}")
        
        # Create initial history entry for new product
        try:
            # Extract schema from table_name for history table
            schema_name = table_name.split('.')[0]
            history_table = f'{schema_name}.product_history'
            
            # Get the inserted product data for history
            cur.execute(f"SELECT * FROM {table_name} WHERE product_id = %s", (new_product_id,))
            product_row = cur.fetchone()
            if product_row:
                colnames = [desc[0] for desc in cur.description]
                product_data = dict(zip(colnames, product_row))
                
                # Create history entry with key fields
                hist_cols = ['product_id', 'product_key', 'product_name', 'original_price', 'deal_price', 'discount_percent', 'updated_at']
                hist_vals = [
                    product_data.get('product_id'),
                    product_data.get('product_key'),
                    product_data.get('product_name'),
                    product_data.get('original_price'),
                    product_data.get('deal_price'),
                    product_data.get('discount_percent'),
                    product_data.get('updated_at')
                ]
                
                cur.execute(
                    f"INSERT INTO {history_table} ({', '.join(hist_cols)}) VALUES ({', '.join(['%s']*len(hist_cols))})",
                    hist_vals
                )
                print(f"✅ Created initial history entry for new product {new_product_id}")
        except Exception as e:
            print(f"⚠️ Warning: Could not create history entry for new product {new_product_id}: {e}")
            # Don't fail the entire operation if history creation fails
        
        conn.commit()
        
        # Determine if this was an insert or update
        action = "updated" if existing_product else "inserted"
        
        return {
            "success": True,
            "message": f"Product {action} successfully with ID: {new_product_id}",
            "product_id": new_product_id,
            "action": action
        }
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Error during insert: {e}")
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def update_products(products, table_name):
    import sys
    import time
    start_time = time.time()
    print(f"[DEBUG] update_products called for table: {table_name}", file=sys.stderr)
    print(f"[DEBUG] Products received: {products}", file=sys.stderr)
    if not products:
        return {
            "success": False,
            "message": "No products provided for update"
        }
        
    field_mapping = {
        'product_name': 'product_name',
        'description': 'description',
        'deal_price': 'deal_price',
        'original_price': 'original_price',
        'image_url': 'image_url',
        'image_url_1': 'image_url_1',
        'image_url_2': 'image_url_2', 
        'image_url_3': 'image_url_3',
        'category': 'category',
        'deal_type': 'deal_type',
        'retailer': 'retailer',
        'sale_url': 'sale_url',
        'product_key': 'product_key',
        'product_rating': 'product_rating',
        'product_keywords': 'product_keywords',
        'is_active': 'is_active',
        'deal_type_id': 'deal_type_id',
        'brand': 'brand',
        'discount_percent': 'discount_percent',
        'product_type': 'product_type',
        'coupon_info': 'coupon_info',
        'category_list': 'category_list',
        'start_date': 'start_date',
        'end_date': 'end_date',
        'promo_label': 'promo_label',
        'stock_status': 'stock_status'
        # Note: Excluded columns that are auto-generated or not provided by CSV:
        # product_id, category_id, seller_id, ts_vector, wix_id, owner, embedding, 
        # iscount_percent, source_product_id
    }
    
    conn = None
    cur = None
    
    try:
        conn = get_db_connection()
        if not conn:
            print("[ERROR] No DB connection", file=sys.stderr)
            return {
                "success": False,
                "message": "Database connection failed"
            }
        cur = conn.cursor()
        
        success_count = 0
        failed_products = []
        
        for product in products:
            if not isinstance(product, dict):
                print(f"Invalid product data, expected dictionary but got: {type(product)}")
                continue
            try:
                product_id = product.get('id')
                if not product_id:
                    failed_products.append({
                        "reason": "Missing product ID",
                        "product": product.get('name', 'Unknown product')
                    })
                    continue
                print(f"Processing product ID: {product_id}")
                print(f"Product data received: {product}")
                print(f"Promo label in product: {product.get('promo_label', 'NOT_FOUND')}")

                # --- PRODUCTION TABLE SPECIAL LOGIC ---
                # Extract schema from table_name for history table
                schema_name = table_name.split('.')[0]
                if table_name == f'{schema_name}.product':
                    # Fetch current record
                    cur.execute(f"SELECT * FROM {table_name} WHERE product_id = %s", (product_id,))
                    current_row = cur.fetchone()
                    if current_row:
                        colnames = [desc[0] for desc in cur.description]
                        current_data = dict(zip(colnames, current_row))
                        new_deal_price = float(product.get('deal_price', current_data.get('deal_price', 0)))
                        new_orig_price = float(product.get('original_price', current_data.get('original_price', 0)))
                        price_changed = (
                            new_deal_price != float(current_data.get('deal_price', 0)) or
                            new_orig_price != float(current_data.get('original_price', 0))
                        )
                        
                        if price_changed:
                            # PRICES CHANGED: Create history and new record
                            print(f"Price changed for product {product_id}, creating history and new record")
                            
                            # Move only selected columns to history (now including product_key)
                            hist_cols = ['product_id', 'product_key', 'product_name', 'original_price', 'deal_price', 'discount_percent', 'updated_at']
                            hist_vals = [
                                current_data.get('product_id'),
                                current_data.get('product_key'),
                                current_data.get('product_name'),
                                current_data.get('original_price'),
                                current_data.get('deal_price'),
                                current_data.get('discount_percent'),
                                current_data.get('updated_at')
                            ]
                            cur.execute(
                                f"INSERT INTO {schema_name}.product_history ({', '.join(hist_cols)}) VALUES ({', '.join(['%s']*len(hist_cols))})",
                                hist_vals
                            )
                            # Remove old record from product table before inserting new one
                            cur.execute(f"DELETE FROM {table_name} WHERE product_id = %s", (product_id,))
                            # Insert new record into product with all changed fields from incoming product
                            update_data = current_data.copy()
                            for frontend_field, db_field in field_mapping.items():
                                # Always set product_keywords from incoming product, even if empty or missing
                                if db_field == 'product_keywords':
                                    update_data[db_field] = product.get('product_keywords', '')
                                elif frontend_field in product:
                                    # Handle discount_percent as integer
                                    if db_field == 'discount_percent':
                                        try:
                                            discount_value = float(product[frontend_field]) if product[frontend_field] else 0
                                            update_data[db_field] = int(round(discount_value))
                                        except (ValueError, TypeError):
                                            update_data[db_field] = 0
                                    # Clean text fields (product_name and description)
                                    elif db_field in ('product_name', 'description'):
                                        update_data[db_field] = clean_text_field(product[frontend_field])
                                    else:
                                        update_data[db_field] = product[frontend_field]
                            # Overwrite all fields in update_data with any present in the incoming product
                            for frontend_field, db_field in field_mapping.items():
                                if frontend_field in product:
                                    if db_field == 'discount_percent':
                                        try:
                                            discount_value = float(product[frontend_field]) if product[frontend_field] else 0
                                            update_data[db_field] = int(round(discount_value))
                                        except (ValueError, TypeError):
                                            update_data[db_field] = 0
                                    elif db_field in ('product_name', 'description'):
                                        update_data[db_field] = clean_text_field(product[frontend_field])
                                    else:
                                        update_data[db_field] = product[frontend_field]
                            # Handle empty string for start_date/end_date
                            for date_field in ['start_date', 'end_date']:
                                if date_field in update_data and update_data[date_field] == '':
                                    update_data[date_field] = None
                            update_data['updated_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            update_data.pop('product_id', None)
                            # Use the original product_key (no timestamp)
                            insert_cols = ','.join(update_data.keys())
                            insert_vals = ','.join(['%s'] * len(update_data))
                            cur.execute(
                                f"INSERT INTO {table_name} ({insert_cols}) VALUES ({insert_vals}) RETURNING product_id",
                                tuple(update_data.values())
                            )
                            new_product_id = cur.fetchone()[0]
                            success_count += 1
                            continue  # Skip normal update logic
                        else:
                            # PRICES DIDN'T CHANGE: Use normal UPDATE logic (no history, no new record)
                            print(f"Price unchanged for product {product_id}, using normal UPDATE")
                            # Continue to the normal update logic below
                # Build update query dynamically based on provided fields
                update_parts = []
                params = []
                update_parts.append('updated_at = %s')
                params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                # Always include product_name, deal_price, original_price
                if 'product_name' in product:
                    update_parts.append("product_name = %s")
                    params.append(clean_text_field(product['product_name']))
                # Always update deal_price and original_price from deal_price and original_price fields
                update_parts.append("deal_price = %s")
                params.append(product.get('deal_price'))
                update_parts.append("original_price = %s")
                params.append(product.get('original_price'))
                for frontend_field, db_field in field_mapping.items():
                    if frontend_field in product and db_field not in ['product_name', 'deal_price', 'original_price']:
                        if db_field in ('start_date', 'end_date') and product[frontend_field] == '':
                            update_parts.append(f"{db_field} = NULL")
                        # Handle discount_percent as integer
                        elif db_field == 'discount_percent':
                            update_parts.append(f"{db_field} = %s")
                            try:
                                discount_value = float(product[frontend_field]) if product[frontend_field] else 0
                                params.append(int(round(discount_value)))
                            except (ValueError, TypeError):
                                params.append(0)
                        # Clean text fields (product_name and description)
                        elif db_field in ('product_name', 'description'):
                            update_parts.append(f"{db_field} = %s")
                            params.append(clean_text_field(product[frontend_field]))
                        else:
                            update_parts.append(f"{db_field} = %s")
                            params.append(product[frontend_field])
                if len(update_parts) <= 1:
                    failed_products.append({
                        "reason": "No valid fields to update",
                        "product": product.get('name', 'Unknown product'),
                        "product_id": product_id
                    })
                    continue
                update_query = f"""
                    UPDATE {table_name}
                    SET {', '.join(update_parts)}
                    WHERE product_id = %s
                """
                params.append(product_id)
                # ...existing code...
                cur.execute(update_query, params)
                if cur.rowcount > 0:
                    success_count += 1
                else:
                    failed_products.append({
                        "reason": f"Product not found in {table_name}",
                        "product": product.get('name', f"ID: {product_id}"),
                        "product_id": product_id
                    })
            except Exception as e:
                print(f"Error processing product: {e}")
                if conn:
                    conn.rollback()
                failed_products.append({
                    "reason": str(e),
                    "product": product.get('name', 'Unknown product')
                })

        conn.commit()

        # Prepare result message
        end_time = time.time()
        print(f"[DEBUG] update_products completed in {end_time - start_time:.2f} seconds", file=sys.stderr)
        
        result_message = f"Successfully updated {success_count} out of {len(products)} products"
        if failed_products:
            result_message += f". {len(failed_products)} products failed to update."
            print("Failed products details:", failed_products)
            
        return {
            "success": success_count > 0,
            "message": result_message,
            "details": {
                "success_count": success_count,
                "failed_count": len(failed_products),
                "failed_products": failed_products[:10] if len(failed_products) > 10 else failed_products
            }
        }
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Error during update: {e}")
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def move_to_production(products_data, schema='deals_master'):
    """Move products from staging to production using complete product data and delete from staging."""
    if not products_data:
        return {
            "success": False,
            "message": "No products data provided"
        }
    
    conn = None
    cur = None
    
    try:
        conn = get_db_connection()
        if not conn:
            return {
                "success": False,
                "message": "Failed to connect to database"
            }
            
        cur = conn.cursor()
        
        # Set timestamps
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Insert products into production and track successful moves
        success_count = 0
        failed_products = []
        successful_product_keys = []  # Track successfully moved products for deletion
        
        for product in products_data:
            try:
                # Map frontend fields to database columns
                product_dict = {
                    'product_name': clean_text_field(product.get('name', '')),
                    'description': clean_text_field(product.get('description', '')),
                    'original_price': float(product.get('orig_price', 0)),
                    'deal_price': float(product.get('price', 0)),
                    'image_url': product.get('image', ''),
                    'image_url_1': product.get('image_url_1', ''),
                    'image_url_2': product.get('image_url_2', ''),
                    'image_url_3': product.get('image_url_3', ''),
                    'category': product.get('category', 'Home'),
                    'deal_type': product.get('deal_type', 'Hot Deal'),
                    'deal_type_id': int(product.get('deal_type_id', 1)),
                    'retailer': product.get('retailer', 'Amazon'),
                    'sale_url': product.get('sale_url', ''),
                    'product_key': product.get('product_key', ''),
                    'product_rating': product.get('product_rating', '4.5'),
                    'product_keywords': product.get('product_keywords', ''),
                    'is_active': product.get('is_active', True),
                    'brand': product.get('brand', ''),
                    'discount_percent': int(round(float(product.get('discount_percent', 0)))),
                    'product_type': product.get('product_type', 'Tech'),
                    'coupon_info': product.get('coupon_info', ''),
                    'category_list': product.get('category_list', ''),
                    'start_date': product.get('start_date') if product.get('start_date') else None,
                    'end_date': product.get('end_date') if product.get('end_date') else None,
                    'promo_label': product.get('promo_label', ''),
                    'created_at': now,
                    'updated_at': now
                }
                
                print(f"🏷️ Processing product: {product_dict['product_name']}")
                print(f"🏷️ Product promo_label: {product_dict['promo_label']}")
                
                # Build the upsert query (INSERT if not exists, UPDATE if exists)
                columns = list(product_dict.keys())
                placeholders = ', '.join(['%s'] * len(columns))
                columns_str = ', '.join(columns)
                
                # Create the SET clause for UPDATE (exclude created_at and updated_at from automatic updates)
                update_clause = ', '.join([f"{col} = EXCLUDED.{col}" for col in columns if col not in ['created_at', 'updated_at']])
                
                upsert_query = f"""
                    INSERT INTO {schema}.product (
                        {columns_str}
                    ) VALUES (
                        {placeholders}
                    ) 
                    ON CONFLICT (product_key) 
                    DO UPDATE SET 
                        {update_clause},
                        updated_at = EXCLUDED.updated_at
                    RETURNING product_id
                """
                
                cur.execute(upsert_query, list(product_dict.values()))
                result = cur.fetchone()
                if result:
                    product_id = result[0]
                    success_count += 1
                    successful_product_keys.append(product_dict['product_key'])
                    print(f"✅ Successfully moved/updated product {product_dict['product_name']} to production with ID: {product_id}")
                    
                    # Create initial history entry for product moved to production
                    try:
                        history_table = f'{schema}.product_history'
                        hist_cols = ['product_id', 'product_key', 'product_name', 'original_price', 'deal_price', 'discount_percent', 'updated_at']
                        hist_vals = [
                            product_id,
                            product_dict['product_key'],
                            product_dict['product_name'],
                            product_dict['original_price'],
                            product_dict['deal_price'],
                            product_dict['discount_percent'],
                            product_dict['updated_at']
                        ]
                        
                        cur.execute(
                            f"INSERT INTO {history_table} ({', '.join(hist_cols)}) VALUES ({', '.join(['%s']*len(hist_cols))})",
                            hist_vals
                        )
                        print(f"✅ Created initial history entry for product moved to production {product_id}")
                    except Exception as e:
                        print(f"⚠️ Warning: Could not create history entry for product moved to production {product_id}: {e}")
                        # Don't fail the entire operation if history creation fails
                else:
                    print(f"⚠️ Product {product_dict['product_name']} was not inserted/updated")
                
            except Exception as e:
                print(f"❌ Error moving product to production: {e}")
                failed_products.append({
                    "reason": str(e),
                    "product": product.get('name', 'Unknown product')
                })
        
        # Delete successfully moved products from staging table
        if successful_product_keys:
            try:
                # Build the DELETE query for staging table
                placeholders = ', '.join(['%s'] * len(successful_product_keys))
                delete_query = f"""
                    DELETE FROM {schema}.product_staging 
                    WHERE product_key IN ({placeholders})
                """
                
                cur.execute(delete_query, successful_product_keys)
                deleted_count = cur.rowcount
                print(f"🗑️ Deleted {deleted_count} products from staging table")
                
            except Exception as e:
                print(f"❌ Error deleting products from staging: {e}")
                # Don't fail the entire operation if deletion fails
                failed_products.append({
                    "reason": f"Failed to delete from staging: {str(e)}",
                    "product": "Multiple products"
                })
        
        conn.commit()
        
        result_message = f"Successfully moved {success_count} out of {len(products_data)} products to production"
        if successful_product_keys:
            result_message += f" and deleted {len(successful_product_keys)} from staging"
        if failed_products:
            result_message += f". {len(failed_products)} products failed to move."
            
        return {
            "success": success_count > 0,
            "message": result_message,
            "details": {
                "success_count": success_count,
                "failed_count": len(failed_products),
                "deleted_from_staging": len(successful_product_keys),
                "failed_products": failed_products[:10] if len(failed_products) > 10 else failed_products
            }
        }
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Error during move to production: {e}")
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def fetch_dropdown_options(environment='staging', schema='deals_master'):
    """Fetch dropdown options from database tables."""
    conn = None
    cur = None
    
    try:
        conn = get_db_connection()
        if not conn:
            return {
                "success": False,
                "message": "Failed to connect to database"
            }
            
        cur = conn.cursor()
        
        # Fetch categories
        cur.execute(f"SELECT DISTINCT category FROM {schema}.categories ORDER BY category")
        categories = [row[0] for row in cur.fetchall()]
        
        # Fetch deal types
        cur.execute(f"SELECT deal_type_id, deal_type FROM {schema}.deal_types ORDER BY deal_type")
        deal_types = [{"id": row[0], "name": row[1]} for row in cur.fetchall()]
        
        # Fetch retailers
        cur.execute(f"SELECT DISTINCT retailer FROM {schema}.retailers ORDER BY retailer")
        retailers = [row[0] for row in cur.fetchall()]
        
        # Fetch product types
        cur.execute(f"SELECT DISTINCT product_type FROM {schema}.product_types ORDER BY product_type")
        product_types = [row[0] for row in cur.fetchall()]
        
        # Fetch promo labels
        cur.execute(f"SELECT DISTINCT promo_label FROM {schema}.promo_master ORDER BY promo_label")
        promo_labels = [row[0] for row in cur.fetchall()]
        
        return {
            "success": True,
            "options": {
                "categories": categories,
                "dealTypes": deal_types,
                "retailers": retailers,
                "productTypes": product_types,
                "promoLabels": promo_labels
            }
        }
        
    except Exception as e:
        print(f"Error fetching dropdown options: {e}")
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def bulk_insert_products(products_data, table_name):
    """Bulk insert products with upsert logic using a single SQL statement."""
    if not products_data:
        return {
            "success": False,
            "message": "No products provided for bulk insertion"
        }
    
    conn = None
    cur = None
    
    try:
        conn = get_db_connection()
        if not conn:
            return {
                "success": False,
                "message": "Failed to connect to database"
            }
            
        cur = conn.cursor()
        
        # Map of frontend field names to database column names
        # Based on actual product_staging table columns
        field_mapping = {
            'product_name': 'product_name',
            'description': 'description',
            'deal_price': 'deal_price',
            'original_price': 'original_price',
            'image_url': 'image_url',
            'image_url_1': 'image_url_1',
            'image_url_2': 'image_url_2', 
            'image_url_3': 'image_url_3',
            'category': 'category',
            'deal_type': 'deal_type',
            'retailer': 'retailer',
            'sale_url': 'sale_url',
            'product_key': 'product_key',
            'product_rating': 'product_rating',
            'product_keywords': 'product_keywords',
            'is_active': 'is_active',
            'deal_type_id': 'deal_type_id',
            'brand': 'brand',
            'discount_percent': 'discount_percent',
            'product_type': 'product_type',
            'coupon_info': 'coupon_info',
            'category_list': 'category_list',
            'start_date': 'start_date',
            'end_date': 'end_date',
            'promo_label': 'promo_label',
            'stock_status': 'stock_status'
            # Note: Excluded columns that are auto-generated or not provided by CSV:
            # product_id, category_id, seller_id, ts_vector, wix_id, owner, embedding, 
            # iscount_percent, source_product_id
        }
        
        # Prepare data for bulk insert
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        all_values = []
        inserted_count = 0
        updated_count = 0
        error_count = 0
        error_details = []
        
        # First, get all existing product_keys to determine which are updates vs inserts
        product_keys = []
        for product in products_data:
            if 'product_key' in product and product['product_key']:
                product_keys.append(product['product_key'])
        
        existing_keys = set()
        if product_keys:
            # Query existing product_keys in batches to avoid parameter limits
            batch_size = 1000
            for i in range(0, len(product_keys), batch_size):
                batch_keys = product_keys[i:i + batch_size]
                placeholders = ','.join(['%s'] * len(batch_keys))
                cur.execute(f"SELECT product_key FROM {table_name} WHERE product_key IN ({placeholders})", batch_keys)
                existing_keys.update(row[0] for row in cur.fetchall())
        
        # Process each product and prepare values
        for i, product in enumerate(products_data):
            try:
                # Prepare values for this product - use timestamps from product data if available
                created_at = product.get('created_at', now)
                updated_at = product.get('updated_at', now)
                values = [created_at, updated_at]  # created_at, updated_at
                
                # Add product fields in the same order as field_mapping
                for frontend_field, db_field in field_mapping.items():
                    if frontend_field in product and frontend_field not in ['created_at', 'updated_at']:
                        # Handle special cases
                        if db_field in ('start_date', 'end_date') and product[frontend_field] == '':
                            values.append(None)
                        elif db_field == 'discount_percent':
                            try:
                                discount_value = float(product[frontend_field]) if product[frontend_field] and product[frontend_field] != '' else 0
                                values.append(int(round(discount_value)))
                            except (ValueError, TypeError):
                                values.append(0)
                        elif db_field == 'deal_type_id':
                            # Use deal_type from Import Configuration, default to 2
                            deal_type_value = product.get('deal_type', 'Hot Deal')
                            # Map deal_type text to deal_type_id
                            if deal_type_value.lower() in ['hot deal', 'hotdeal']:
                                values.append(1)
                            elif deal_type_value.lower() in ['sale', 'clearance', 'discount']:
                                values.append(2)
                            else:
                                values.append(2)  # Default to 2 for unknown types
                        elif db_field == 'product_rating':
                            try:
                                rating_value = float(product[frontend_field]) if product[frontend_field] and product[frontend_field] != '' else 4.5
                                values.append(rating_value)
                            except (ValueError, TypeError):
                                values.append(4.5)
                        elif db_field in ('product_name', 'description'):
                            values.append(clean_text_field(product[frontend_field]))
                        else:
                            values.append(product[frontend_field])
                    else:
                        # Add default values for missing fields
                        if db_field == 'discount_percent':
                            values.append(0)
                        elif db_field == 'deal_type_id':
                            # Use deal_type from Import Configuration, default to 2
                            deal_type_value = product.get('deal_type', 'Hot Deal')
                            # Map deal_type text to deal_type_id
                            if deal_type_value.lower() in ['hot deal', 'hotdeal']:
                                values.append(1)
                            elif deal_type_value.lower() in ['sale', 'clearance', 'discount']:
                                values.append(2)
                            else:
                                values.append(2)  # Default to 2 for unknown types
                        elif db_field == 'product_rating':
                            values.append(4.5)
                        elif db_field in ('start_date', 'end_date'):
                            values.append(None)
                        else:
                            values.append('')
                
                all_values.append(values)
                
                # Track whether this will be an insert or update
                product_key = product.get('product_key')
                if product_key and product_key in existing_keys:
                    updated_count += 1
                else:
                    inserted_count += 1
                    
            except Exception as e:
                error_count += 1
                error_details.append({
                    'row': i + 1,
                    'product_key': product.get('product_key', 'unknown'),
                    'errors': [str(e)]
                })
                print(f"Error preparing product {i + 1}: {e}")
        
        if not all_values:
            return {
                "success": False,
                "message": "No valid products to insert",
                "results": {
                    'total': len(products_data),
                    'inserted': 0,
                    'updated': 0,
                    'errors': error_count,
                    'error_details': error_details
                }
            }
        
        # Build the bulk upsert query
        columns = ['created_at', 'updated_at'] + list(field_mapping.values())
        columns_str = ', '.join(columns)
        placeholders = ', '.join(['%s'] * len(columns))
        
        # Create the SET clause for UPDATE (exclude created_at, updated_at, and product_id from updates)
        update_clause = ', '.join([f"{col} = EXCLUDED.{col}" for col in columns if col not in ['created_at', 'updated_at', 'product_id']])
        
        # Use PostgreSQL's ON CONFLICT for upsert
        upsert_query = f"""
            INSERT INTO {table_name} (
                {columns_str}
            ) VALUES (
                {placeholders}
            ) 
            ON CONFLICT (product_key) 
            DO UPDATE SET 
                {update_clause},
                updated_at = EXCLUDED.updated_at
        """
        
        print(f"Executing bulk upsert for {len(all_values)} products")
        print(f"Query: {upsert_query}")
        print(f"Sample values (first product): {all_values[0] if all_values else 'No values'}")
        print(f"Columns: {columns}")
        print(f"Column count: {len(columns)}, Values count: {len(all_values[0]) if all_values else 0}")
        
        # Debug: Check for empty strings in numeric fields
        if all_values:
            for i, (col, val) in enumerate(zip(columns, all_values[0])):
                if col in ['discount_percent', 'product_rating', 'deal_type_id'] and val == '':
                    print(f"WARNING: Empty string found in numeric field {col} at position {i}: '{val}'")
        
        # Execute the bulk insert
        try:
            cur.executemany(upsert_query, all_values)
            conn.commit()
            print(f"✅ Bulk upsert completed: {inserted_count} new, {updated_count} updated, {error_count} errors")
        except Exception as db_error:
            print(f"❌ Database error during bulk upsert: {db_error}")
            conn.rollback()
            raise db_error
        
        return {
            "success": True,
            "message": f'Bulk import completed: {inserted_count} inserted, {updated_count} updated, {error_count} errors',
            "results": {
                'total': len(products_data),
                'inserted': inserted_count,
                'updated': updated_count,
                'errors': error_count,
                'error_details': error_details[:10] if len(error_details) > 10 else error_details
            }
        }
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Error during bulk insert: {e}")
        return {
            "success": False,
            "message": f"An error occurred during bulk insert: {str(e)}",
            "results": {
                'total': len(products_data),
                'inserted': 0,
                'updated': 0,
                'errors': len(products_data),
                'error_details': [{'row': 'all', 'product_key': 'unknown', 'errors': [str(e)]}]
            }
        }
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def add_promo_label(promo_label, schema='deals_master'):
    """Add a new promo label to the database."""
    conn = None
    cur = None
    
    try:
        conn = get_db_connection()
        if not conn:
            return {
                "success": False,
                "message": "Failed to connect to database"
                
            }
            
        cur = conn.cursor()
        
        # Check if promo label already exists
        cur.execute(f"SELECT promo_label FROM {schema}.promo_master WHERE promo_label = %s", (promo_label,))
        if cur.fetchone():
            return {
                "success": False,
                "message": f"Promo label '{promo_label}' already exists"
            }
        
        # Insert new promo label
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            f"INSERT INTO {schema}.promo_master (promo_label, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s)",
            (promo_label, True, now, now)
        )
        conn.commit()
        
        return {
            "success": True,
            "message": f"Promo label '{promo_label}' added successfully"
        }
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Error adding promo label: {e}")
        return {
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def lambda_handler(event, context):
    """Handler for the Lambda function."""
    print("Event received:", json.dumps(event, default=str))
    print(f"Lambda timeout: {context.get_remaining_time_in_millis()}ms")
    print(f"Lambda memory: {context.memory_limit_in_mb}MB")
    
    # Handle CORS preflight requests
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE',
                'Access-Control-Max-Age': '86400'
            },
            'body': ''
        }
    
    # Only try to parse body for non-OPTIONS requests
    try:
        # Parse body - handle multiple possible event structures
        body = None
        
        # Check if this is an API Gateway event
        if isinstance(event, dict):
            if 'body' in event:
                body_content = event['body']
                if isinstance(body_content, str):
                    try:
                        body = json.loads(body_content)
                    except json.JSONDecodeError:
                        return {
                            'statusCode': 400,
                            'headers': {
                                'Content-Type': 'application/json',
                                'Access-Control-Allow-Origin': '*',
                                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                                'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                            },
                            'body': json.dumps({
                                'success': False,
                                'message': "Invalid JSON in request body"
                            })
                        }
                else:
                    body = body_content  # Already parsed JSON
            else:
                # Direct invocation with the body as the event
                body = event
        
        if not body or not isinstance(body, dict):
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                    'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                },
                'body': json.dumps({
                    'success': False,
                    'message': "Missing or invalid request body"
                })
            }
        
        # Extract operation type, environment, country, and schema
        operation = body.get('operation', 'update_data')
        environment = body.get('environment', 'staging')
        country = body.get('country', os.environ.get('COUNTRY', 'US')).upper()
        schema = body.get('schema', None)
        
        # Determine schema based on country if not explicitly provided
        if not schema:
            # Try to get from environment variables first
            schema = os.environ.get('SCHEMA', None)
            if not schema:
                if country == 'INDIA':
                    schema = 'deals_india'
                else:
                    schema = 'deals_master'  # Default US schema
        
        print(f"Operation: {operation}, Environment: {environment}, Country: {country}, Schema: {schema}")
        
        # For bulk_insert operations, always use staging table regardless of environment
        if operation == 'bulk_insert':
            table_name = f'{schema}.product_staging'
        else:
            # For other operations, use environment-based table selection
            table_name = f'{schema}.product_staging' if environment == 'staging' else f'{schema}.product'
        
        # Process based on operation type
        if operation == 'update_data':
            # Check if this is a new product insertion (no ID provided)
            if 'id' not in body and body.get('product_name'):
                # This is a new product insertion
                print(f"Inserting new product in {table_name}")
                result = insert_product(body, table_name)
            else:
                # This is an update to existing products
                products = body.get('products', [])
                
                if not products or not isinstance(products, list):
                    # Check if we have a single product in the body
                    if body.get('product_name'):
                        # Create a list with the single product
                        products = [{'id': body.get('id'), **body}]
                    else:
                        return {
                            'statusCode': 400,
                            'headers': {
                                'Content-Type': 'application/json',
                                'Access-Control-Allow-Origin': '*',
                                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                                'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                            },
                            'body': json.dumps({
                                'success': False,
                                'message': "No products provided for update"
                            })
                        }
                
                print(f"Updating {len(products)} products in {table_name}")
                result = update_products(products, table_name)
            
            # Return with 200 status if the operation was successful
            return {
                'statusCode': 200,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                    'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                },
                'body': json.dumps(result)
            }
        elif operation == 'move_to_production':
            # Handle moving products from staging to production
            products_data = body.get('products', [])
            if not products_data:
                # Try the old format for backward compatibility
                product_ids = body.get('productIds', [])
                if not product_ids:
                    return {
                        'statusCode': 400,
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*',
                            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                            'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                        },
                        'body': json.dumps({
                            'success': False,
                            'message': "No products or product IDs provided for move to production"
                        })
                    }
                # Handle old format - would need to fetch products from staging first
                return {
                    'statusCode': 400,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                    },
                    'body': json.dumps({
                        'success': False,
                        'message': "Please provide complete product data for move to production"
                    })
                }
                
            print(f"🏷️ Moving {len(products_data)} products to production")
            result = move_to_production(products_data, schema)
            
            return {
                'statusCode': 200 if result.get('success', False) else 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                    'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                },
                'body': json.dumps(result)
            }
        elif operation == 'fetch_options':
            # Handle fetching dropdown options
            environment = body.get('environment', 'staging')
            print(f"Fetching dropdown options for environment: {environment}")
            result = fetch_dropdown_options(environment, schema)
            
            return {
                'statusCode': 200 if result.get('success', False) else 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                    'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                },
                'body': json.dumps(result)
            }
        elif operation == 'add_promo_label':
            # Handle adding a new promo label
            promo_label = body.get('promo_label')
            if not promo_label:
                return {
                    'statusCode': 400,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                    },
                    'body': json.dumps({
                        'success': False,
                        'message': "Missing promo label in request body"
                    })
                }
            
            print(f"Adding new promo label: {promo_label}")
            result = add_promo_label(promo_label, schema)
            
            return {
                'statusCode': 200 if result.get('success', False) else 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                    'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                },
                'body': json.dumps(result)
            }
        elif operation == 'bulk_insert':
            # Handle bulk insertion of products (for CSV import)
            products_data = body.get('products', [])
            if not products_data or not isinstance(products_data, list):
                return {
                    'statusCode': 400,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                    },
                    'body': json.dumps({
                        'success': False,
                        'message': "No products provided for bulk insertion"
                    })
                }
            
            print(f"Bulk inserting {len(products_data)} products into {table_name}")
            
            # Use bulk insert with upsert logic
            result = bulk_insert_products(products_data, table_name)
            
            return {
                'statusCode': 200 if result.get('success', False) else 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                    'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                },
                'body': json.dumps({
                    'success': result.get('success', False),
                    'message': result.get('message', 'Bulk import completed'),
                    'results': result.get('results', {}),
                    'environment': environment,
                    'schema': schema
                })
            }
        elif operation == 'delete_products':
            # Handle deleting products
            product_ids = body.get('product_ids', [])
            if not product_ids or not isinstance(product_ids, list):
                return {
                    'statusCode': 400,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                    },
                    'body': json.dumps({
                        'success': False,
                        'message': "No product IDs provided or invalid format"
                    })
                }
            
            try:
                conn = get_db_connection()
                if not conn:
                    return {
                        'statusCode': 500,
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*',
                            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                            'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                        },
                        'body': json.dumps({
                            'success': False,
                            'message': "Database connection failed"
                        })
                    }
                
                cur = conn.cursor()
                
                # First, check what products exist with these IDs
                check_placeholders = ','.join(['%s'] * len(product_ids))
                check_query = f"SELECT product_id, product_name FROM {table_name} WHERE product_id IN ({check_placeholders})"
                
                cur.execute(check_query, product_ids)
                existing_products = cur.fetchall()
                
                if not existing_products:
                    return {
                        'statusCode': 400,
                        'headers': {
                            'Content-Type': 'application/json',
                            'Access-Control-Allow-Origin': '*',
                            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                            'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                        },
                        'body': json.dumps({
                            'success': False,
                            'message': 'No products found with the provided IDs',
                            'product_ids_received': product_ids,
                            'table': table_name
                        })
                    }
                
                # Now proceed with deletion
                placeholders = ','.join(['%s'] * len(product_ids))
                delete_query = f"DELETE FROM {table_name} WHERE product_id IN ({placeholders})"
                
                cur.execute(delete_query, product_ids)
                deleted_count = cur.rowcount
                conn.commit()
                
                return {
                    'statusCode': 200,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                    },
                    'body': json.dumps({
                        'success': True,
                        'message': f'Successfully deleted {deleted_count} products',
                        'deleted_count': deleted_count,
                        'environment': environment
                    })
                }
                
            except Exception as e:
                print(f"Error executing delete query: {e}")
                if conn:
                    conn.rollback()
                return {
                    'statusCode': 500,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                    },
                    'body': json.dumps({
                        'success': False,
                        'message': f'Database delete error: {str(e)}',
                        'table': table_name
                    })
                }
            finally:
                if cur:
                    cur.close()
                if conn:
                    conn.close()
        else:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                    'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
                },
                'body': json.dumps({
                    'success': False,
                    'message': f"Unsupported operation: {operation}"
                })
            }
        
    except Exception as e:
        print(f"Error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Requested-With',
                'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
            },
            'body': json.dumps({
                'success': False,
                'message': f"Error processing request: {str(e)}"
            })
        } 