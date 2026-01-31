import os
import json
import pg8000
from decimal import Decimal
from datetime import datetime, date

def get_db_connection():
    try:
        conn = pg8000.connect(
            host=os.environ.get('DB_HOST'),
            database=os.environ.get('DB_NAME'),
            user=os.environ.get('DB_USER'),
            password=os.environ.get('DB_PASSWORD'),
            port=int(os.environ.get('DB_PORT', 5432))
        )
        return conn
    except Exception as e:
        return None

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

def lambda_handler(event, context):
    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key',
        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
    }
    
    # Handle CORS preflight OPTIONS request
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': cors_headers,
            'body': json.dumps({'message': 'CORS preflight'})
        }
    
    try:
        # Log the incoming event for debugging
        print(f"🔍 Incoming Lambda event: {json.dumps(event, default=str)}")
        
        # Parse request body
        body = event.get('body')
        if body and isinstance(body, str):
            body = json.loads(body)
        elif not body:
            body = event
            
        print(f"🔍 Parsed body: {json.dumps(body, default=str)}")
        
        # Handle GET requests (query parameters)
        if event.get('httpMethod') == 'GET':
            query_params = event.get('queryStringParameters') or {}
            country = query_params.get('country', 'US').upper()
            schema = query_params.get('schema')
        else:
            # Handle POST/PUT/DELETE requests (body parameters)
            country = body.get('country', 'US').upper()
            schema = body.get('schema')
        
        operation = body.get('operation', 'fetch_all')
        
        # Auto-determine schema based on country if not explicitly provided
        if not schema:
            if country in ['INDIA', 'IN']:
                schema = 'deals_india'
            elif country in ['US', 'USA', 'UNITED STATES']:
                schema = 'deals_master'
            else:
                schema = 'deals_master'  # Default to US
        
        # Validate and sanitize schema name to prevent SQL injection
        allowed_schemas = ['deals_master', 'deals_india', 'deals_master_eu']
        if schema not in allowed_schemas:
            # Fallback based on country
            if country in ['INDIA', 'IN']:
                schema = 'deals_india'
            else:
                schema = 'deals_master'
        
        conn = get_db_connection()
        if not conn:
            print("❌ Database connection failed")
            return {
                'statusCode': 500,
                'headers': cors_headers,
                'body': json.dumps({'error': 'Database connection failed'})
            }
        cur = conn.cursor()
        
        # Use dynamic schema-based table name
        table = f'{schema}.promo_master'
        
        # Debug logging
        print(f"🔍 Promo Master Management Debug:")
        print(f"   Operation: {operation}")
        print(f"   Country: {country}")
        print(f"   Schema: {schema}")
        print(f"   Table: {table}")
        print(f"   HTTP Method: {event.get('httpMethod', 'N/A')}")
        
        # Check if table exists before proceeding
        try:
            cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
            print(f"✅ Table {table} exists and is accessible")
        except Exception as table_error:
            print(f"❌ Table {table} check failed: {str(table_error)}")
            
            # If India schema table doesn't exist, try to fallback to master schema
            if schema == 'deals_india':
                print("🔄 Attempting fallback to deals_master.promo_master for India")
                fallback_table = 'deals_master.promo_master'
                try:
                    cur.execute(f"SELECT 1 FROM {fallback_table} LIMIT 1")
                    print(f"✅ Fallback table {fallback_table} exists, using it instead")
                    table = fallback_table
                    schema = 'deals_master'
                except Exception as fallback_error:
                    print(f"❌ Fallback table {fallback_table} also failed: {str(fallback_error)}")
                    cur.close(); conn.close()
                    return {
                        'statusCode': 500,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'error': f'Neither {table} nor {fallback_table} exists or is accessible',
                            'details': f'Original: {str(table_error)}, Fallback: {str(fallback_error)}',
                            'suggestion': 'Please ensure at least deals_master.promo_master table exists in the database'
                        })
                    }
            else:
                cur.close(); conn.close()
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': f'Table {table} does not exist or is not accessible',
                        'details': str(table_error),
                        'suggestion': f'Please ensure the {schema}.promo_master table exists in the database'
                    })
                }
        if operation == 'fetch_all':
            try:
                cur.execute(f"SELECT * FROM {table} ORDER BY promo_id DESC")
                rows = cur.fetchall()
                columns = [desc[0] for desc in cur.description]
                result = [dict(zip(columns, row)) for row in rows]
                cur.close(); conn.close()
                print(f"✅ Successfully fetched {len(result)} records from {table}")
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps(result, cls=CustomEncoder)
                }
            except Exception as fetch_error:
                print(f"❌ Fetch operation failed: {str(fetch_error)}")
                cur.close(); conn.close()
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': f'Failed to fetch records from {table}',
                        'details': str(fetch_error)
                    })
                }
        elif operation == 'insert':
            data = body.get('data', {})
            columns = [
                'promo_label', 'promo_label_image_url', 'promo_mobile_image_url', 'promo_validity_start_dt', 'promo_validity_end_dt',
                'updated_at', 'is_active', 'promo_image_width_px', 'promo_image_height_px', 'promo_title', 'promo_position',
                'platform', 'badge_colors', 'promo_sale_url', 'promo_type', 'promo_script'
            ]
            values = [data.get(col) for col in columns]
            insert_query = f"""
                INSERT INTO {table} ({', '.join(columns)})
                VALUES ({', '.join(['%s']*len(columns))}) RETURNING promo_id
            """
            cur.execute(insert_query, values)
            new_id = cur.fetchone()[0]
            conn.commit()
            cur.close(); conn.close()
            print(f"✅ Successfully inserted record with promo_id {new_id} into {table}")
            return {
                'statusCode': 200,
                'headers': cors_headers,
                'body': json.dumps({'success': True, 'promo_id': new_id})
            }
        elif operation == 'update':
            data = body.get('data', {})
            promo_id = data.get('promo_id')
            if not promo_id:
                return {
                    'statusCode': 400,
                    'headers': cors_headers,
                    'body': json.dumps({'error': 'Missing promo_id'})
                }
            columns = [
                'promo_label', 'promo_label_image_url', 'promo_mobile_image_url', 'promo_validity_start_dt', 'promo_validity_end_dt',
                'updated_at', 'is_active', 'promo_image_width_px', 'promo_image_height_px', 'promo_title', 'promo_position',
                'platform', 'badge_colors', 'promo_sale_url', 'promo_type', 'promo_script'
            ]
            set_clause = ', '.join([f"{col} = %s" for col in columns])
            update_query = f"""
                UPDATE {table} SET {set_clause} WHERE promo_id = %s
            """
            cur.execute(update_query, [data.get(col) for col in columns] + [promo_id])
            rows_affected = cur.rowcount
            conn.commit()
            cur.close(); conn.close()
            print(f"✅ Successfully updated promo_id {promo_id} in {table} ({rows_affected} rows affected)")
            return {
                'statusCode': 200,
                'headers': cors_headers,
                'body': json.dumps({'success': True, 'promo_id': promo_id, 'rows_affected': rows_affected})
            }
        elif operation == 'delete':
            promo_id = body.get('promo_id')
            print(f"🗑️ Delete operation - promo_id: {promo_id}, type: {type(promo_id)}")
            
            if not promo_id:
                print("❌ Delete failed: Missing promo_id")
                return {
                    'statusCode': 400,
                    'headers': cors_headers,
                    'body': json.dumps({'error': 'Missing promo_id'})
                }
            
            # First check if record exists
            cur.execute(f"SELECT promo_id FROM {table} WHERE promo_id = %s", [promo_id])
            existing_record = cur.fetchone()
            print(f"🔍 Record exists check: {existing_record}")
            
            if not existing_record:
                print(f"❌ Delete failed: No record found with promo_id {promo_id}")
                cur.close(); conn.close()
                return {
                    'statusCode': 404,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'No record found with promo_id {promo_id}'})
                }
            
            # Proceed with delete
            delete_query = f"DELETE FROM {table} WHERE promo_id = %s"
            print(f"🗑️ Executing delete query: {delete_query} with promo_id: {promo_id}")
            
            cur.execute(delete_query, [promo_id])
            rows_affected = cur.rowcount
            conn.commit()
            cur.close(); conn.close()
            
            print(f"✅ Successfully deleted promo_id {promo_id} from {table} ({rows_affected} rows affected)")
            return {
                'statusCode': 200,
                'headers': cors_headers,
                'body': json.dumps({'success': True, 'promo_id': promo_id, 'rows_affected': rows_affected})
            }
        else:
            cur.close(); conn.close()
            return {
                'statusCode': 400,
                'headers': cors_headers,
                'body': json.dumps({'error': f'Unsupported operation: {operation}'})
            }
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': cors_headers,
            'body': json.dumps({'error': str(e)})
        }
