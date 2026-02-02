import json
import os
import pg8000
import boto3

# AWS clients
secrets_client = boto3.client('secretsmanager')

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization"
}

def handle_database_error(e, operation="database operation"):
    """Handle database errors with specific error messages"""
    error_str = str(e)
    print(f"Database error during {operation}: {error_str}")
    
    if "permission denied" in error_str.lower():
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "error": "Database permission error. Please contact administrator.",
                "details": "The database user lacks necessary permissions for this operation."
            })
        }
    elif "relation" in error_str.lower() and "does not exist" in error_str.lower():
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "error": "Database schema error. Required tables do not exist.",
                "details": "Please run the database schema updates."
            })
        }
    elif "connection" in error_str.lower():
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "error": "Database connection failed.",
                "details": "Unable to connect to the database server."
            })
        }
    else:
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "error": f"Database error during {operation}",
                "details": "An unexpected database error occurred."
            })
        }

def get_database_connection():
    """Get database connection using Secrets Manager"""
    try:
        secret_name = os.environ.get('DB_SECRET_NAME')
        if not secret_name:
            if os.environ.get('PG_HOST'):
                conn = pg8000.connect(
                    host=os.environ['PG_HOST'],
                    database=os.environ['PG_DATABASE'],
                    user=os.environ['PG_USER'],
                    password=os.environ['PG_PASSWORD'],
                    port=5432
                )
                return conn, None
            raise ValueError("DB_SECRET_NAME environment variable is not set")

        # Get Secret
        secret_response = secrets_client.get_secret_value(SecretId=secret_name)
        credentials = json.loads(secret_response['SecretString'])
        
        db_host = credentials.get('host') or credentials.get('endpoint')
        db_port = int(credentials.get('port', 5432))  # Aurora PostgreSQL standard port
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
        print("Database connection successful via Secrets Manager")
        return conn, None
    except Exception as e:
        print(f"Database connection failed: {e}")
        error_response = handle_database_error(e, "database connection")
        return None, error_response

def resolve_user_id_fast(conn, user_identifier, schema):
    """
    Fast user ID resolution optimized for bookmark operations
    Returns (user_id, error_response) tuple
    """
    try:
        cur = conn.cursor()
        user_str = str(user_identifier).strip()
        
        print(f"Fast resolving user '{user_str}' in schema '{schema}'")
        
        if user_str.isdigit():
            user_id = int(user_str)
            
            # Check if user exists in target schema
            cur.execute(f"SELECT id, email FROM {schema}.users WHERE id = %s", (user_id,))
            user_in_target = cur.fetchone()
            
            if user_in_target:
                print(f"✅ User {user_id} found in {schema}: {user_in_target[1]}")
                return user_in_target[0], None
            else:
                # Cross-schema resolution
                print(f"🔄 User {user_id} not in {schema}, resolving cross-schema...")
                
                opposite_schema = 'deals_master' if schema == 'deals_india' else 'deals_india'
                cur.execute(f"SELECT email FROM {opposite_schema}.users WHERE id = %s", (user_id,))
                email_result = cur.fetchone()
                
                if email_result:
                    email = email_result[0]
                    print(f"📧 Found email {email} for user {user_id} in {opposite_schema}")
                    
                    # Look up by email in target schema
                    cur.execute(f"SELECT id FROM {schema}.users WHERE email = %s", (email,))
                    target_user = cur.fetchone()
                    
                    if target_user:
                        print(f"✅ Resolved to user {target_user[0]} in {schema}")
                        return target_user[0], None
                    else:
                        # Auto-create user in target schema
                        print(f"🔧 Auto-creating user in {schema}")
                        return auto_create_user_cross_schema(conn, email, opposite_schema, schema)
                else:
                    return None, {
                        "statusCode": 404,
                        "headers": CORS_HEADERS,
                        "body": json.dumps({"error": "User not found in any schema"})
                    }
        else:
            # Email-based lookup
            cur.execute(f"SELECT id FROM {schema}.users WHERE email = %s LIMIT 1", (user_str,))
            result = cur.fetchone()
            if result:
                return result[0], None
            else:
                return None, {
                    "statusCode": 404,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({"error": "User not found by email"})
                }
                
    except Exception as e:
        print(f"Error resolving user ID: {e}")
        return None, handle_database_error(e, "user ID resolution")

def auto_create_user_cross_schema(conn, email, source_schema, target_schema):
    """Auto-create user in target schema from source schema"""
    try:
        cur = conn.cursor()
        print(f"Auto-creating user {email} in {target_schema} from {source_schema}")
        
        # Get user data from source schema
        cur.execute(f"""
            SELECT name, email, password_hash, preferred_categories, preferred_stores,
                   gender, city, notifications, notification_frequency
            FROM {source_schema}.users WHERE email = %s
        """, (email,))
        
        source_user = cur.fetchone()
        
        if source_user:
            cur.execute(f"""
                INSERT INTO {target_schema}.users 
                (name, email, password_hash, preferred_categories, preferred_stores,
                 gender, city, notifications, notification_frequency)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    name = EXCLUDED.name,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
            """, source_user)
            
            new_user = cur.fetchone()
            if new_user:
                conn.commit()
                print(f"✅ Created user {new_user[0]} in {target_schema}")
                return new_user[0], None
            else:
                return None, {
                    "statusCode": 500,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({"error": "Failed to create user"})
                }
        else:
            return None, {
                "statusCode": 404,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "Source user not found"})
            }
            
    except Exception as e:
        print(f"Error in auto-create: {e}")
        conn.rollback()
        return None, handle_database_error(e, "user auto-creation")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization"
}

def handle_bookmark_operations(event, body, schema):
    """Handle all bookmark operations with REST API style - OPTIMIZED VERSION"""
    try:
        method = event.get('httpMethod', '')
        path = event.get('path', '')
        path_params = event.get('pathParameters') or {}
        
        user_identifier = path_params.get('userId') or body.get('user_id')
        product_id = path_params.get('productId') or body.get('product_id')
        
        if not user_identifier:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "User ID is required"})
            }
        
        # Connect to database
        conn, error_response = get_database_connection()
        if error_response:
            return error_response
        
        # Fast user ID resolution
        user_id, error_response = resolve_user_id_fast(conn, user_identifier, schema)
        if error_response:
            conn.close()
            return error_response
        
        try:
            cur = conn.cursor()
            
            if method == 'GET' and '/bookmarks/' in path:
                # GET /api/bookmarks/{userId} - Get all bookmarks for user
                cur.execute(f"""
                    SELECT product_id, product_data, created_at 
                    FROM {schema}.user_bookmarks 
                    WHERE user_id = %s 
                    ORDER BY created_at DESC
                """, (user_id,))
                
                bookmarks = []
                for row in cur.fetchall():
                    bookmark = {
                        'product_id': row[0],
                        'user_id': user_identifier,
                        'created_at': row[2].isoformat() if row[2] else None
                    }
                    # Add product data if available
                    if row[1]:
                        try:
                            product_data = json.loads(row[1])
                            bookmark.update(product_data)
                        except:
                            pass
                    bookmarks.append(bookmark)
                
                return {
                    "statusCode": 200,
                    "headers": CORS_HEADERS,
                    "body": json.dumps(bookmarks)
                }
            
            elif method == 'POST' and '/bookmarks' in path:
                # POST /api/bookmarks - Add bookmark
                product_id = body.get('product_id')
                if not product_id:
                    return {
                        "statusCode": 400,
                        "headers": CORS_HEADERS,
                        "body": json.dumps({"error": "Product ID is required"})
                    }
                
                # Extract product data for storage (optional)
                product_data = {
                    'product_name': body.get('product_name'),
                    'product_image': body.get('product_image'),
                    'product_price': body.get('product_price'),
                    'retailer': body.get('retailer'),
                    'category': body.get('category'),
                    'category_group': body.get('category_group'),
                    'brand': body.get('brand'),
                    'deal_type': body.get('deal_type'),
                    'product_category': body.get('product_category'),
                    'main_category': body.get('main_category'),
                    'sub_category': body.get('sub_category'),
                    'discount_percent': body.get('discount_percent'),
                    'original_price': body.get('original_price'),
                    'description': body.get('description'),
                    'rating': body.get('rating'),
                    'review_count': body.get('review_count'),
                    'sale_url': body.get('sale_url')
                }
                
                # Fast bookmark operation - use UPSERT for maximum speed
                cur.execute(f"""
                    INSERT INTO {schema}.user_bookmarks (user_id, product_id, product_data)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, product_id) 
                    DO UPDATE SET 
                        product_data = EXCLUDED.product_data,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id
                """, (user_id, product_id, json.dumps(product_data)))
                
                result = cur.fetchone()
                conn.commit()
                
                return {
                    "statusCode": 200,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({
                        "success": True,
                        "message": "Bookmark added successfully",
                        "bookmark_id": result[0] if result else None
                    })
                }
            
            elif method == 'DELETE' and product_id:
                # DELETE /api/bookmarks/{userId}/{productId} - Remove bookmark
                cur.execute(f"""
                    DELETE FROM {schema}.user_bookmarks 
                    WHERE user_id = %s AND product_id = %s
                    RETURNING id
                """, (user_id, product_id))
                
                result = cur.fetchone()
                conn.commit()
                
                return {
                    "statusCode": 200,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({
                        "success": True,
                        "message": "Bookmark removed successfully",
                        "removed": result is not None
                    })
                }
            
            elif method == 'GET' and '/bookmarks/' in path and '/check' in path:
                # GET /api/bookmarks/{userId}/check/{productId} - Check if bookmarked
                if not product_id:
                    return {
                        "statusCode": 400,
                        "headers": CORS_HEADERS,
                        "body": json.dumps({"error": "Product ID is required for bookmark check"})
                    }
                
                cur.execute(f"""
                    SELECT id FROM {schema}.user_bookmarks 
                    WHERE user_id = %s AND product_id = %s
                """, (user_id, product_id))
                
                result = cur.fetchone()
                
                return {
                    "statusCode": 200,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({
                        "bookmarked": result is not None,
                        "user_id": user_identifier,
                        "product_id": product_id
                    })
                }
            
            else:
                return {
                    "statusCode": 404,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({"error": "Bookmark endpoint not found"})
                }
                
        finally:
            cur.close()
            conn.close()
            
    except Exception as e:
        error_str = str(e)
        print(f"Bookmark operation error: {error_str}")
        
        # Provide specific error messages for common issues
        if "relation" in error_str.lower() and "user_bookmarks" in error_str.lower():
            return {
                "statusCode": 500,
                "headers": CORS_HEADERS,
                "body": json.dumps({
                    "error": "Database table missing",
                    "details": f"The user_bookmarks table does not exist in schema {schema}. Please run database setup."
                })
            }
        elif "foreign key" in error_str.lower():
            return {
                "statusCode": 500,
                "headers": CORS_HEADERS,
                "body": json.dumps({
                    "error": "User reference error",
                    "details": "User ID not found. The user may not exist in the database."
                })
            }
        elif "permission denied" in error_str.lower():
            return {
                "statusCode": 500,
                "headers": CORS_HEADERS,
                "body": json.dumps({
                    "error": "Database permission error",
                    "details": "Insufficient permissions to access the bookmarks table."
                })
            }
        else:
            return handle_database_error(e, "bookmark operation")

def lambda_handler(event, context):
    try:
        print(f"Bookmark Lambda - Received event: {json.dumps(event)}")
        
        # Handle CORS preflight OPTIONS request
        if event.get('httpMethod', '') == 'OPTIONS':
            print("Handling OPTIONS request")
            return {
                "statusCode": 200,
                "headers": CORS_HEADERS,
                "body": json.dumps({"message": "CORS preflight"})
            }
        
        # Get path to determine operation type
        path = event.get('path', '')
        method = event.get('httpMethod', '')
        
        # Parse request body
        body = {}
        if 'body' in event and event['body']:
            try:
                body = json.loads(event['body'])
                print(f"Parsed body: {body}")
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
                return {
                    "statusCode": 400,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({"error": "Invalid JSON in request body"})
                }
        
        # Determine schema based on domain, country_code, or path
        domain = body.get('domain', '')
        country_code = body.get('country_code', '').upper()
        
        # Auto-detect schema based on domain, country, or path (case-insensitive)
        if ('india' in domain.lower() or 
            country_code in ['IN', 'INDIA'] or 
            '/india/' in path.lower()):
            schema = 'deals_india'
        else:
            schema = body.get('schema', os.environ.get('SCHEMA', 'deals_master'))
        
        print(f"Using schema: {schema} (domain: {domain}, country_code: {country_code}, path: {path})")
        
        # Route to bookmark operations
        if '/bookmarks' in path:
            return handle_bookmark_operations(event, body, schema)
        
        # Legacy bookmark operations (for backward compatibility)
        action = body.get('action')
        if action in ('get_bookmarks', 'add_bookmark', 'remove_bookmark', 'check_bookmark'):
            try:
                user_id = body.get('user_id')
                if not user_id:
                    return {
                        "statusCode": 400,
                        "headers": CORS_HEADERS,
                        "body": json.dumps({"error": "user_id is required for bookmark operations"})
                    }
                
                # Convert legacy action to REST-style operation
                if action == 'get_bookmarks':
                    event['httpMethod'] = 'GET'
                    event['path'] = '/api/bookmarks/' + str(user_id)
                    return handle_bookmark_operations(event, body, schema)
                elif action == 'add_bookmark':
                    event['httpMethod'] = 'POST'
                    event['path'] = '/api/bookmarks'
                    return handle_bookmark_operations(event, body, schema)
                elif action == 'remove_bookmark':
                    product_id = body.get('product_id')
                    if not product_id:
                        return {
                            "statusCode": 400,
                            "headers": CORS_HEADERS,
                            "body": json.dumps({"error": "product_id is required for remove_bookmark operation"})
                        }
                    event['httpMethod'] = 'DELETE'
                    event['path'] = f"/api/bookmarks/{str(user_id)}/{str(product_id)}"
                    return handle_bookmark_operations(event, body, schema)
                elif action == 'check_bookmark':
                    product_id = body.get('product_id')
                    if not product_id:
                        return {
                            "statusCode": 400,
                            "headers": CORS_HEADERS,
                            "body": json.dumps({"error": "product_id is required for check_bookmark operation"})
                        }
                    event['httpMethod'] = 'GET'
                    event['path'] = f"/api/bookmarks/{str(user_id)}/check/{str(product_id)}"
                    return handle_bookmark_operations(event, body, schema)
                
            except Exception as e:
                print(f"Error in legacy bookmark operation: {e}")
                return {
                    "statusCode": 500,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({"error": f"Legacy bookmark operation failed: {str(e)}"})
                }
        
        # If no bookmark operations found, return error
        return {
            "statusCode": 400,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "error": "Invalid request",
                "details": "This lambda only handles bookmark operations. Use /bookmarks endpoints or legacy bookmark actions."
            })
        }
            
    except Exception as e:
        print(f"Bookmark Lambda error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": f"Internal server error: {str(e)}"})
        }