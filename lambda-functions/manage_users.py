# User Management Lambda - Authentication and Preferences Only
import json
import os
import pg8000
import hashlib
import base64
import urllib.request
import boto3
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# AWS clients
secrets_client = boto3.client('secretsmanager')

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization"
}

def hash_password(password):
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
    return base64.b64encode(salt + key).decode('utf-8')

def verify_password(password, stored_hash):
    try:
        decoded = base64.b64decode(stored_hash.encode('utf-8'))
        salt = decoded[:16]
        key = decoded[16:]
        new_key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
        return new_key == key
    except Exception as e:
        print(f"Password verification error: {e}")
        return False

def verify_google_token(token):
    try:
        google_client_id = os.environ.get('GOOGLE_CLIENT_ID')
        print(f"Verifying Google token with client ID: {google_client_id}")
        
        request = google_requests.Request()
        idinfo = id_token.verify_oauth2_token(token, request, google_client_id)
        
        print(f"Google token verified successfully: {idinfo.get('email')}")
        return idinfo
    except Exception as e:
        print(f"Google token verification failed: {e}")
        return None

def verify_facebook_token(token):
    try:
        app_id = os.environ.get('FACEBOOK_APP_ID')
        app_secret = os.environ.get('FACEBOOK_APP_SECRET')
        debug_url = f"https://graph.facebook.com/debug_token?input_token={token}&access_token={app_id}|{app_secret}"
        with urllib.request.urlopen(debug_url) as resp:
            data = json.loads(resp.read().decode())
        if data.get('data', {}).get('is_valid'):
            user_info_url = f"https://graph.facebook.com/me?fields=id,name,email&access_token={token}"
            with urllib.request.urlopen(user_info_url) as user_resp:
                return json.loads(user_resp.read().decode())
        return None
    except Exception as e:
        print(f"Facebook token verification failed: {e}")
        return None

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
        db_port = int(credentials.get('port', 1369)) # Default to 1369 as seen in secret
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

def resolve_user_id(conn, user_identifier, schema):
    """
    Resolve user identifier (email or ID) to actual database user ID within the target schema
    User IDs are schema-specific and should not be used across schemas
    Returns (user_id, error_response) tuple
    """
    try:
        cur = conn.cursor()
        
        # Convert to string for consistent handling
        user_str = str(user_identifier).strip()
        
        print(f"Resolving user identifier '{user_str}' in schema '{schema}'")
        
        # If it's a number, only use it directly within the same schema
        if user_str.isdigit():
            user_id = int(user_str)
            print(f"Numeric identifier {user_id} - checking if exists in {schema}")
            
            # Check if user exists in target schema
            cur.execute(f"SELECT id, email FROM {schema}.users WHERE id = %s", (user_id,))
            result = cur.fetchone()
            
            if result:
                print(f"✅ User ID {user_id} found in {schema}: {result[1]}")
                return user_id, None
            else:
                print(f"❌ User ID {user_id} not found in {schema}")
                # For numeric IDs, we need to find the email first from other schemas
                return resolve_numeric_id_cross_schema(conn, user_id, schema)
        
        # If it's an email, find user by email in target schema
        print(f"Email identifier '{user_str}' - looking up in {schema}")
        cur.execute(f"SELECT id FROM {schema}.users WHERE email = %s", (user_str,))
        result = cur.fetchone()
        
        if result:
            print(f"✅ User found by email in {schema}: ID={result[0]}")
            return result[0], None
        
        # If user not found by email in current schema, try cross-schema lookup and auto-create
        print(f"User email '{user_str}' not found in {schema}, attempting cross-schema lookup")
        if schema == 'deals_india':
            return auto_create_user_in_india(conn, user_str)
        elif schema == 'deals_master':
            return auto_create_user_in_master(conn, user_str)
        
        # User not found in any schema
        print(f"❌ User '{user_str}' not found in any schema")
        return None, {
            "statusCode": 404,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "error": "User not found",
                "details": f"No user found with identifier: {user_identifier}"
            })
        }
            
    except Exception as e:
        print(f"Error resolving user ID: {e}")
        return None, handle_database_error(e, "user ID resolution")

def resolve_numeric_id_cross_schema(conn, user_id, target_schema):
    """
    Resolve a numeric user ID from one schema to the target schema using email lookup
    """
    try:
        cur = conn.cursor()
        
        print(f"Attempting cross-schema resolution for user ID {user_id} to {target_schema}")
        
        # Find the user's email in the opposite schema
        if target_schema == 'deals_india':
            # Look for user in master schema
            cur.execute("SELECT email FROM deals_master.users WHERE id = %s", (user_id,))
        else:
            # Look for user in india schema  
            cur.execute("SELECT email FROM deals_india.users WHERE id = %s", (user_id,))
        
        result = cur.fetchone()
        
        if result:
            email = result[0]
            print(f"Found email '{email}' for user ID {user_id}, now resolving in {target_schema}")
            
            # Now look up by email in target schema
            cur.execute(f"SELECT id FROM {target_schema}.users WHERE email = %s", (email,))
            target_result = cur.fetchone()
            
            if target_result:
                target_user_id = target_result[0]
                print(f"✅ Cross-schema resolution successful: ID {user_id} → {email} → ID {target_user_id} in {target_schema}")
                return target_user_id, None
            else:
                # User exists in source schema but not target - auto-create
                print(f"User '{email}' exists in source but not in {target_schema} - auto-creating")
                if target_schema == 'deals_india':
                    return auto_create_user_in_india(conn, email)
                else:
                    return auto_create_user_in_master(conn, email)
        else:
            print(f"❌ User ID {user_id} not found in source schema either")
            return None, {
                "statusCode": 404,
                "headers": CORS_HEADERS,
                "body": json.dumps({
                    "error": "User not found",
                    "details": f"User ID {user_id} does not exist in any schema"
                })
            }
            
    except Exception as e:
        print(f"Error in cross-schema resolution: {e}")
        return None, handle_database_error(e, "cross-schema user resolution")

def auto_create_user_in_india(conn, user_identifier):
    """Auto-create user in India schema if they exist in master schema (email-based lookup)"""
    try:
        cur = conn.cursor()
        print(f"Auto-creating user in India schema for: {user_identifier}")
        
        # Always look up by email for cross-schema operations
        # user_identifier should be an email at this point
        cur.execute("""
            SELECT id, name, email, password_hash, preferred_categories, preferred_stores,
                   gender, city, notifications, notification_frequency
            FROM deals_master.users 
            WHERE email = %s
        """, (user_identifier,))
        
        master_user = cur.fetchone()
        
        if master_user:
            print(f"Found user in master schema: ID={master_user[0]}, Email={master_user[2]}")
            
            # Check if user already exists in India schema (by email)
            cur.execute("SELECT id FROM deals_india.users WHERE email = %s", (master_user[2],))
            existing_india_user = cur.fetchone()
            
            if existing_india_user:
                print(f"✅ User already exists in India schema with ID: {existing_india_user[0]}")
                return existing_india_user[0], None
            
            print(f"Creating new user in India schema...")
            
            # Create user in India schema with explicit transaction handling
            try:
                cur.execute("""
                    INSERT INTO deals_india.users 
                    (name, email, password_hash, preferred_categories, preferred_stores,
                     gender, city, notifications, notification_frequency)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, master_user[1:])  # Skip the original ID from master
                
                result = cur.fetchone()
                
                if result:
                    # Commit the transaction immediately
                    conn.commit()
                    new_user_id = result[0]
                    print(f"✅ Successfully created user in India schema")
                    print(f"   Master: ID={master_user[0]}, Email={master_user[2]}")
                    print(f"   India:  ID={new_user_id}, Email={master_user[2]}")
                    
                    # Verify the user was actually created
                    cur.execute("SELECT id, email FROM deals_india.users WHERE id = %s", (new_user_id,))
                    verification = cur.fetchone()
                    if verification:
                        print(f"✅ User creation verified: ID={verification[0]}, Email={verification[1]}")
                        return new_user_id, None
                    else:
                        print("❌ User creation verification failed")
                        return None, {
                            "statusCode": 500,
                            "headers": CORS_HEADERS,
                            "body": json.dumps({
                                "error": "User creation verification failed",
                                "details": "User was created but cannot be found immediately after"
                            })
                        }
                else:
                    print("❌ Failed to get new user ID after insertion")
                    conn.rollback()
                    return None, {
                        "statusCode": 500,
                        "headers": CORS_HEADERS,
                        "body": json.dumps({
                            "error": "Failed to create user in India schema",
                            "details": "User insertion succeeded but no ID returned"
                        })
                    }
            except Exception as insert_error:
                print(f"❌ Error during user insertion: {insert_error}")
                conn.rollback()
                return None, {
                    "statusCode": 500,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({
                        "error": "Failed to insert user in India schema",
                        "details": str(insert_error)
                    })
                }
        
        print(f"❌ User not found in master schema: {user_identifier}")
        return None, {
            "statusCode": 404,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "error": "User not found in master schema",
                "details": f"No user found with email: {user_identifier}"
            })
        }
        
    except Exception as e:
        print(f"❌ Error auto-creating user in India: {e}")
        conn.rollback()
        return None, handle_database_error(e, "user auto-creation")

def auto_create_user_in_master(conn, user_identifier):
    """Auto-create user in master schema if they exist in India schema"""
    try:
        cur = conn.cursor()
        print(f"Attempting to auto-create user in master schema: {user_identifier}")
        
        # Look for user in India schema
        cur.execute("""
            SELECT id, name, email, password_hash, preferred_categories, preferred_stores,
                   gender, city, notifications, notification_frequency
            FROM deals_india.users 
            WHERE email = %s OR id::text = %s
        """, (user_identifier, user_identifier))
        
        india_user = cur.fetchone()
        
        if india_user:
            print(f"Found user in India schema, creating in master schema")
            # Create user in master schema
            cur.execute("""
                INSERT INTO deals_master.users 
                (name, email, password_hash, preferred_categories, preferred_stores,
                 gender, city, notifications, notification_frequency)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    name = EXCLUDED.name,
                    preferred_categories = EXCLUDED.preferred_categories,
                    preferred_stores = EXCLUDED.preferred_stores,
                    gender = EXCLUDED.gender,
                    city = EXCLUDED.city,
                    notifications = EXCLUDED.notifications,
                    notification_frequency = EXCLUDED.notification_frequency,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
            """, india_user[1:])  # Skip the ID from India
            
            result = cur.fetchone()
            conn.commit()
            
            if result:
                print(f"Successfully auto-created user in master schema with ID: {result[0]}")
                return result[0], None
        
        print(f"User not found in India schema either: {user_identifier}")
        return None, {
            "statusCode": 404,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "error": "User not found in any schema",
                "details": f"No user found with identifier: {user_identifier}"
            })
        }
        
    except Exception as e:
        print(f"Error auto-creating user in master: {e}")
        return None, handle_database_error(e, "user auto-creation")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization"
}



def handle_user_preferences_operations(event, body, schema):
    """Handle user preferences operations with REST API style"""
    try:
        method = event.get('httpMethod', '')
        path = event.get('path', '')
        path_params = event.get('pathParameters') or {}
        
        user_identifier = path_params.get('userId') or body.get('user_id')
        
        if not user_identifier:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "User ID is required"})
            }
        
        # Connect to database with error handling
        conn, error_response = get_database_connection()
        if error_response:
            return error_response
        
        # Resolve user identifier to actual user ID
        user_id, error_response = resolve_user_id(conn, user_identifier, schema)
        if error_response:
            conn.close()
            return error_response
        
        try:
            cur = conn.cursor()
            
            if method == 'GET' and '/user-preferences/' in path:
                # GET /api/user-preferences/{userId} - Get user preferences
                cur.execute(f"""
                    SELECT name, email, gender, preferred_categories, preferred_stores, 
                           city, notifications, notification_frequency, updated_at
                    FROM {schema}.users 
                    WHERE id = %s
                """, (user_id,))
                
                user_row = cur.fetchone()
                
                if not user_row:
                    return {
                        "statusCode": 404,
                        "headers": CORS_HEADERS,
                        "body": json.dumps({"error": "User not found"})
                    }
                
                # Parse categories and stores from comma-separated strings
                preferred_categories = []
                preferred_stores = []
                
                if user_row[3]:  # preferred_categories
                    preferred_categories = [cat.strip() for cat in user_row[3].split(',') if cat.strip()]
                
                if user_row[4]:  # preferred_stores
                    preferred_stores = [store.strip() for store in user_row[4].split(',') if store.strip()]
                
                preferences = {
                    'user_id': user_identifier,
                    'name': user_row[0],
                    'email': user_row[1],
                    'gender': user_row[2],
                    'preferred_categories': preferred_categories,
                    'preferred_stores': preferred_stores,
                    'city': user_row[5],
                    'notifications': user_row[6],
                    'notification_frequency': user_row[7],
                    'updated_at': user_row[8].isoformat() if user_row[8] else None
                }
                
                return {
                    "statusCode": 200,
                    "headers": CORS_HEADERS,
                    "body": json.dumps(preferences)
                }
            
            elif method == 'PUT' and '/user-preferences/' in path:
                # PUT /api/user-preferences/{userId} - Update user preferences
                gender = body.get('gender')
                preferred_categories = body.get('preferred_categories', [])
                preferred_stores = body.get('preferred_stores', [])
                city = body.get('city')
                notifications = body.get('notifications')
                notification_frequency = body.get('notification_frequency')
                
                # Convert lists to comma-separated strings
                categories_str = ','.join(preferred_categories) if preferred_categories else ''
                stores_str = ','.join(preferred_stores) if preferred_stores else ''
                
                # Update user preferences
                cur.execute(f"""
                    UPDATE {schema}.users 
                    SET gender = %s, 
                        preferred_categories = %s, 
                        preferred_stores = %s,
                        city = %s,
                        notifications = %s,
                        notification_frequency = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING id, name, email, gender, preferred_categories, preferred_stores, 
                             city, notifications, notification_frequency, updated_at
                """, (gender, categories_str, stores_str, city, notifications, 
                      notification_frequency, user_id))
                
                result = cur.fetchone()
                conn.commit()
                
                if not result:
                    return {
                        "statusCode": 404,
                        "headers": CORS_HEADERS,
                        "body": json.dumps({"error": "User not found"})
                    }
                
                # Parse the updated data
                updated_categories = []
                updated_stores = []
                
                if result[4]:  # preferred_categories
                    updated_categories = [cat.strip() for cat in result[4].split(',') if cat.strip()]
                
                if result[5]:  # preferred_stores
                    updated_stores = [store.strip() for store in result[5].split(',') if store.strip()]
                
                updated_preferences = {
                    'user_id': str(result[0]),
                    'name': result[1],
                    'email': result[2],
                    'gender': result[3],
                    'preferred_categories': updated_categories,
                    'preferred_stores': updated_stores,
                    'city': result[6],
                    'notifications': result[7],
                    'notification_frequency': result[8],
                    'updated_at': result[9].isoformat() if result[9] else None
                }
                
                return {
                    "statusCode": 200,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({
                        "success": True,
                        "message": "Preferences updated successfully",
                        "preferences": updated_preferences
                    })
                }
            
            else:
                return {
                    "statusCode": 404,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({"error": "User preferences endpoint not found"})
                }
                
        finally:
            cur.close()
            conn.close()
            
    except Exception as e:
        return handle_database_error(e, "user preferences operation")

def lambda_handler(event, context):
    try:
        print(f"Received event: {json.dumps(event)}")
        
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
        country_code = body.get('country_code', '').upper()  # Normalize to uppercase
        
        # Auto-detect schema based on domain, country, or path (case-insensitive)
        if ('india' in domain.lower() or 
            country_code in ['IN', 'INDIA'] or 
            '/india/' in path.lower()):
            schema = 'deals_india'
        else:
            schema = body.get('schema', os.environ.get('SCHEMA', 'deals_master'))
        
        print(f"Using schema: {schema} (domain: {domain}, country_code: {country_code}, path: {path})")
        
        # Route to user preferences handler
        if '/user-preferences' in path:
            return handle_user_preferences_operations(event, body, schema)
        
        # Legacy preferences operations (for backward compatibility)
        action = body.get('action')
        if action in ('get_user_preferences', 'update_user_preferences'):
            try:
                user_id = body.get('user_id')
                if not user_id:
                    return {
                        "statusCode": 400,
                        "headers": CORS_HEADERS,
                        "body": json.dumps({"error": "user_id is required for preferences operations"})
                    }
                
                # Convert legacy action to REST-style operation
                if action == 'get_user_preferences':
                    event['httpMethod'] = 'GET'
                    event['path'] = '/api/user-preferences/' + str(user_id)
                    return handle_user_preferences_operations(event, body, schema)
                elif action == 'update_user_preferences':
                    event['httpMethod'] = 'PUT'
                    event['path'] = '/api/user-preferences/' + str(user_id)
                    return handle_user_preferences_operations(event, body, schema)
                
            except Exception as e:
                print(f"Error in legacy preferences operation: {e}")
                return {
                    "statusCode": 500,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({"error": f"Legacy preferences operation failed: {str(e)}"})
                }
        
        # Original user authentication logic
        email = body.get('email')
        password = body.get('password')
        name = body.get('name')
        categories = body.get('preferred_categories', body.get('categories', ''))
        
        # If categories is a list, join as string
        if isinstance(categories, list):
            categories = ','.join(categories)
        
        # Social login: provider and token present
        provider = body.get('provider')
        social_token = body.get('token')
        
        if provider in ('google', 'facebook') and social_token:
            print(f"Processing {provider} login")
            
            if provider == 'google':
                idinfo = verify_google_token(social_token)
                if not idinfo or not idinfo.get('email'):
                    print('Invalid Google token')
                    return {"statusCode": 401, "headers": CORS_HEADERS, "body": json.dumps({"error": "Invalid Google token"})}
                email = idinfo['email']
                name = idinfo.get('name', '')
            else:
                fbinfo = verify_facebook_token(social_token)
                if not fbinfo or not fbinfo.get('email'):
                    print('Invalid Facebook token')
                    return {"statusCode": 401, "headers": CORS_HEADERS, "body": json.dumps({"error": "Invalid Facebook token"})}
                email = fbinfo['email']
                name = fbinfo.get('name', '')
            
            # Connect to database
            conn, error_response = get_database_connection()
            if error_response:
                return error_response
            
            try:
                cur = conn.cursor()
                cur.execute(f"""
                    SELECT id, name, email, preferred_categories, preferred_stores, 
                           gender, city, notifications, notification_frequency 
                    FROM {schema}.users WHERE email = %s
                """, (email,))
                user_row = cur.fetchone()
                
                if user_row:
                    # User exists, return user data with parsed categories/stores
                    preferred_categories = []
                    preferred_stores = []
                    
                    if user_row[3]:  # preferred_categories
                        preferred_categories = [cat.strip() for cat in user_row[3].split(',') if cat.strip()]
                    
                    if user_row[4]:  # preferred_stores
                        preferred_stores = [store.strip() for store in user_row[4].split(',') if store.strip()]
                    
                    user = {
                        'id': user_row[0],
                        'name': user_row[1],
                        'email': user_row[2],
                        'preferred_categories': preferred_categories,
                        'preferred_stores': preferred_stores,
                        'gender': user_row[5],
                        'city': user_row[6],
                        'notifications': user_row[7],
                        'notification_frequency': user_row[8]
                    }
                    print(f"Existing user found: {user['email']}")
                else:
                    # Insert new user for social login (with empty password_hash)
                    print(f"Creating new user: {email}")
                    cur.execute(
                        f"""
                        INSERT INTO {schema}.users (name, email, password_hash, preferred_categories)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id, name, email, preferred_categories
                        """,
                        (name, email, '', categories or '')
                    )
                    result = cur.fetchone()
                    
                    if result:
                        preferred_categories = []
                        if result[3]:
                            preferred_categories = [cat.strip() for cat in result[3].split(',') if cat.strip()]
                        
                        user = {
                            'id': result[0],
                            'name': result[1],
                            'email': result[2],
                            'preferred_categories': preferred_categories,
                            'preferred_stores': [],
                            'gender': None,
                            'city': None,
                            'notifications': None,
                            'notification_frequency': None
                        }
                    else:
                        user = None
                    
                    conn.commit()
                    print(f"New user created: {user['email'] if user else 'Failed'}")
                
                return {
                    "statusCode": 200,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({"user": user})
                }
                
            finally:
                cur.close()
                conn.close()
        
        # Regular email/password signup or login
        elif email and password:
            print(f"Processing email/password for: {email}")
            
            # Connect to database
            conn, error_response = get_database_connection()
            if error_response:
                return error_response
            
            try:
                cur = conn.cursor()
                
                if name:  # Signup
                    print("Processing signup")
                    hashed = hash_password(password)
                    
                    # Check if user exists
                    cur.execute(f"SELECT 1 FROM {schema}.users WHERE email = %s", (email,))
                    existing = cur.fetchone()
                    if existing:
                        return {"statusCode": 409, "headers": CORS_HEADERS, "body": json.dumps({"error": "Email already exists"})}
                    
                    # Create new user
                    cur.execute(
                        f"""
                        INSERT INTO {schema}.users (name, email, password_hash, preferred_categories)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id, name, email, preferred_categories
                        """,
                        (name, email, hashed, categories or '')
                    )
                    result = cur.fetchone()
                    
                    if result:
                        preferred_categories = []
                        if result[3]:
                            preferred_categories = [cat.strip() for cat in result[3].split(',') if cat.strip()]
                        
                        user = {
                            'id': result[0],
                            'name': result[1],
                            'email': result[2],
                            'preferred_categories': preferred_categories,
                            'preferred_stores': [],
                            'gender': None,
                            'city': None,
                            'notifications': None,
                            'notification_frequency': None
                        }
                    else:
                        user = None
                    
                    conn.commit()
                    
                    return {
                        "statusCode": 201,
                        "headers": CORS_HEADERS,
                        "body": json.dumps({"user": user})
                    }
                    
                else:  # Login
                    print("Processing login")
                    cur.execute(f"""
                        SELECT id, name, email, password_hash, preferred_categories, preferred_stores,
                               gender, city, notifications, notification_frequency 
                        FROM {schema}.users WHERE email = %s
                    """, (email,))
                    user_row = cur.fetchone()
                    
                    if not user_row:
                        return {"statusCode": 401, "headers": CORS_HEADERS, "body": json.dumps({"error": "Invalid email or password"})}
                    
                    if not verify_password(password, user_row[3]):  # password_hash is at index 3
                        return {"statusCode": 401, "headers": CORS_HEADERS, "body": json.dumps({"error": "Invalid email or password"})}
                    
                    # Parse categories and stores
                    preferred_categories = []
                    preferred_stores = []
                    
                    if user_row[4]:  # preferred_categories
                        preferred_categories = [cat.strip() for cat in user_row[4].split(',') if cat.strip()]
                    
                    if user_row[5]:  # preferred_stores
                        preferred_stores = [store.strip() for store in user_row[5].split(',') if store.strip()]
                    
                    user = {
                        'id': user_row[0],
                        'name': user_row[1],
                        'email': user_row[2],
                        'preferred_categories': preferred_categories,
                        'preferred_stores': preferred_stores,
                        'gender': user_row[6],
                        'city': user_row[7],
                        'notifications': user_row[8],
                        'notification_frequency': user_row[9]
                    }
                    
                    return {"statusCode": 200, "headers": CORS_HEADERS, "body": json.dumps({"user": user})}
                    
            finally:
                cur.close()
                conn.close()
        
        else:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "Missing required fields"})
            }
            
    except Exception as e:
        print(f"Lambda error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": f"Internal server error: {str(e)}"})
        }