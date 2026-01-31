import json
import os
import datetime
import pg8000

# Database configuration
DB_HOST = os.environ.get('DB_HOST')
DB_NAME = os.environ.get('DB_NAME')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')

# Schema mapping for different countries
SCHEMA_MAPPING = {
    'us': 'deals_master',
    'usa': 'deals_master',
    'united_states': 'deals_master',
    'in': 'deals_india',
    'IN': 'deals_india',
    'india': 'deals_india',
    'INDIA': 'deals_india'
}

def get_db_connection():
    """Create database connection."""
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

def get_schema_name(country):
    """Get the appropriate schema name for the given country."""
    if not country:
        return 'deals_master'
    
    # Try exact match first
    if country in SCHEMA_MAPPING:
        return SCHEMA_MAPPING[country]
    
    # Try lowercase match
    country_lower = country.lower()
    if country_lower in SCHEMA_MAPPING:
        return SCHEMA_MAPPING[country_lower]
    
    # Try uppercase match
    country_upper = country.upper()
    if country_upper in SCHEMA_MAPPING:
        return SCHEMA_MAPPING[country_upper]
    
    print(f"Warning: Country '{country}' not found in schema mapping, using default 'deals_master'")
    return 'deals_master'

def verify_schema_and_tables(cur, schema_name):
    """Verify schema exists and create necessary tables if missing."""
    try:
        print(f"Checking if schema {schema_name} exists...")
        # Check if schema exists
        schema_check_query = """
            SELECT EXISTS (
                SELECT FROM information_schema.schemata 
                WHERE schema_name = %s
            )
        """
        cur.execute(schema_check_query, (schema_name,))
        schema_exists = cur.fetchone()[0]
        print(f"Schema {schema_name} exists: {schema_exists}")
        
        if not schema_exists:
            raise Exception(f"Schema {schema_name} does not exist")
        
        print(f"Checking if product table exists in {schema_name}...")
        # Check if product table exists
        product_table_check = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = %s 
                AND table_name = 'product'
            )
        """
        cur.execute(product_table_check, (schema_name,))
        product_table_exists = cur.fetchone()[0]
        print(f"Product table exists in {schema_name}: {product_table_exists}")
        
        if not product_table_exists:
            raise Exception(f"Product table does not exist in schema {schema_name}")
        
        # Test a simple query on the product table to ensure we can access it
        print(f"Testing access to {schema_name}.product table...")
        test_query = f"SELECT COUNT(*) FROM {schema_name}.product LIMIT 1"
        cur.execute(test_query)
        count_result = cur.fetchone()[0]
        print(f"Product table access test successful. Total products: {count_result}")
        
        # Check the columns in the product table
        print(f"Checking columns in {schema_name}.product table...")
        columns_query = """
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema = %s AND table_name = 'product'
            ORDER BY ordinal_position
        """
        cur.execute(columns_query, (schema_name,))
        columns = cur.fetchall()
        print(f"Product table columns: {[col[0] for col in columns]}")
        
        # Check for required columns
        column_names = [col[0] for col in columns]
        required_columns = ['product_id', 'product_name', 'deal_price', 'original_price', 'discount_percent', 'is_active', 'promo_label', 'updated_at']
        missing_columns = [col for col in required_columns if col not in column_names]
        
        if missing_columns:
            print(f"Warning: Missing columns in {schema_name}.product: {missing_columns}")
        else:
            print("All required columns found in product table")
        
        print(f"Schema {schema_name} and required tables verified successfully")
        return True
        
    except pg8000.Error as e:
        error_msg = f"Database error during schema verification: {e}"
        print(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Schema verification failed: {e}"
        print(error_msg)
        raise e

def clear_previous_deal_of_the_day(cur, schema_name):
    """Clear previous deal of the day records by setting promo_label to empty string."""
    try:
        print(f"Executing clear previous deal of the day query on {schema_name}...")
        update_query = f"""
            UPDATE {schema_name}.product 
            SET promo_label = '',
                deal_type_id = 1,  
                updated_at = %s
            WHERE promo_label = 'deal_of_the_day'
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        print(f"Query: {update_query}")
        cur.execute(update_query, (now,))
        updated_count = cur.rowcount
        print(f"Clear deal of the day query executed successfully. Updated {updated_count} rows")
        if updated_count > 0:
            print(f"Cleared {updated_count} previous deal of the day from {schema_name}")
        return updated_count
    except pg8000.Error as e:
        error_msg = f"Database error clearing previous deal of the day: {e}"
        print(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error clearing previous deal of the day: {e}"
        print(error_msg)
        raise

def get_previously_picked_products(cur, schema_name):
    """Get list of product IDs that were previously picked for any promo label."""
    try:
        # First check if the table exists
        check_table_query = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = %s 
                AND table_name = 'product_promo_history'
            )
        """
        cur.execute(check_table_query, (schema_name,))
        table_exists = cur.fetchone()[0]
        
        if not table_exists:
            print(f"Table {schema_name}.product_promo_history does not exist, returning empty list")
            return []
        
        # Get products that were picked in the last 7 days to avoid re-picking recent selections
        select_query = f"""
            SELECT DISTINCT product_id
            FROM {schema_name}.product_promo_history
            WHERE created_at >= NOW() - INTERVAL '7 days'
            ORDER BY product_id
        """
        cur.execute(select_query)
        results = cur.fetchall()
        previously_picked = [row[0] for row in results]
        print(f"Found {len(previously_picked)} previously picked products in last 7 days from {schema_name}")
        return previously_picked
    except pg8000.Error as e:
        print(f"Database error getting previously picked products: {e}")
        # If table doesn't exist or error, return empty list
        return []
    except Exception as e:
        print(f"Unexpected error getting previously picked products: {e}")
        return []

def record_promo_selection(cur, product_id, promo_label, schema_name):
    """Record that a product was selected for a promo label."""
    try:
        # First check if the table exists, if not create it
        check_table_query = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = %s 
                AND table_name = 'product_promo_history'
            )
        """
        cur.execute(check_table_query, (schema_name,))
        table_exists = cur.fetchone()[0]
        
        if not table_exists:
            # Create the table if it doesn't exist
            create_table_query = f"""
                CREATE TABLE IF NOT EXISTS {schema_name}.product_promo_history (
                    id SERIAL PRIMARY KEY,
                    product_id VARCHAR(255) NOT NULL,
                    promo_label VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """
            cur.execute(create_table_query)
            print(f"Created table {schema_name}.product_promo_history")
        
        insert_query = f"""
            INSERT INTO {schema_name}.product_promo_history 
            (product_id, promo_label, created_at) 
            VALUES (%s, %s, %s)
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        cur.execute(insert_query, (product_id, promo_label, now))
        print(f"Recorded {promo_label} selection for product {product_id} in {schema_name}")
    except pg8000.Error as e:
        print(f"Database error recording promo selection: {e}")
        # Continue execution even if recording fails
    except Exception as e:
        print(f"Unexpected error recording promo selection: {e}")
        # Continue execution even if recording fails

def find_and_update_deal_of_the_day(cur, previously_picked_products, schema_name):
    """Find the most recent product meeting criteria and set as deal of the day."""
    try:
        # Find the most recent product that meets criteria
        # Requirements: discount_percent > 30% and deal_price > $36
        select_query = f"""
            SELECT product_id, product_name, deal_price, original_price, discount_percent
            FROM {schema_name}.product
            WHERE is_active = true
            AND deal_price > 36
            AND discount_percent > 27
            AND promo_label != 'deal_of_the_day' and promo_label != 'deals_now_pick'
        """
        
        # Add exclusion for previously picked products
        if previously_picked_products:
            placeholders = ','.join(['%s'] * len(previously_picked_products))
            select_query += f" AND product_id NOT IN ({placeholders})"
        
        select_query += """
            ORDER BY updated_at DESC
            LIMIT 1
        """
        
        # Execute with parameters if we have previously picked products
        if previously_picked_products:
            cur.execute(select_query, previously_picked_products)
        else:
            cur.execute(select_query)
        
        result = cur.fetchone()
        
        if not result:
            print("No products found for deal of the day")
            return None
        
        product_id = result[0]
        product_name = result[1]
        deal_price = result[2]
        original_price = result[3]
        discount_percent = result[4]
        
        # Update the selected product
        update_query = f"""
            UPDATE {schema_name}.product 
            SET promo_label = 'deal_of_the_day',
                deal_type_id=1,
                updated_at = %s
            WHERE product_id = %s
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        cur.execute(update_query, (now, product_id))
        
        if cur.rowcount > 0:
            # Record this selection in history
            record_promo_selection(cur, product_id, 'deal_of_the_day', schema_name)
            
            print(f"Deal of the day: {product_name} (${deal_price}, {discount_percent}% off)")
            return {
                'product_id': product_id,
                'product_name': product_name,
                'deal_price': float(deal_price),
                'original_price': float(original_price),
                'discount_percent': float(discount_percent)
            }
        else:
            print(f"Failed to update product {product_id}")
            return None
            
    except pg8000.Error as e:
        print(f"Database error finding/updating deal of the day: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error finding/updating deal of the day: {e}")
        raise

def clear_previous_deals_now_pick(cur, schema_name):
    """Clear previous deals_now_pick records by setting promo_label to empty string."""
    try:
        update_query = f"""
            UPDATE {schema_name}.product 
            SET promo_label = '', 
                deal_type_id=1,
                updated_at = %s
            WHERE promo_label = 'deals_now_pick'
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        cur.execute(update_query, (now,))
        updated_count = cur.rowcount
        if updated_count > 0:
            print(f"Cleared {updated_count} previous deals now pick from {schema_name}")
        return updated_count
    except pg8000.Error as e:
        print(f"Database error clearing previous deals_now_pick: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error clearing previous deals_now_pick: {e}")
        raise

def find_and_update_deals_now_pick(cur, previously_picked_products, schema_name, exclude_product_id=None):
    """Find and update three most recent products as deals_now_pick based on discount and price criteria."""
    try:
        # Requirements: discount_percent > 27% and deal_price > $27
        # Get 3 most recent products meeting criteria, excluding deal of the day if provided
        select_query = f"""
            SELECT product_id, product_name, deal_price, original_price, discount_percent
            FROM {schema_name}.product
            WHERE is_active = true
            AND deal_price > 25
            AND discount_percent > 25
            AND promo_label != 'deal_of_the_day' and promo_label != 'deals_now_pick' 
        """
        
        # Build exclusion list
        excluded_ids = []
        if exclude_product_id:
            excluded_ids.append(exclude_product_id)
        if previously_picked_products:
            excluded_ids.extend(previously_picked_products)
        
        # Add exclusions to query
        if excluded_ids:
            placeholders = ','.join(['%s'] * len(excluded_ids))
            select_query += f" AND product_id NOT IN ({placeholders})"
        
        select_query += """
            ORDER BY updated_at DESC
            LIMIT 3
        """
        
        # Execute with parameters if we have exclusions
        if excluded_ids:
            cur.execute(select_query, excluded_ids)
        else:
            cur.execute(select_query)
        
        results = cur.fetchall()
        
        if not results:
            print("No products found for deals now pick")
            return []
        
        picks = []
        now = datetime.datetime.now(datetime.timezone.utc)
        
        for result in results:
            product_id = result[0]
            product_name = result[1]
            deal_price = result[2]
            original_price = result[3]
            discount_percent = result[4]
            
            # Update the selected product
            update_query = f"""
                UPDATE {schema_name}.product 
                SET promo_label = 'deals_now_pick',
                    updated_at = %s
                WHERE product_id = %s
            """
            cur.execute(update_query, (now, product_id))
            
            if cur.rowcount > 0:
                # Record this selection in history
                record_promo_selection(cur, product_id, 'deals_now_pick', schema_name)
                
                picks.append({
                    'product_id': product_id,
                    'product_name': product_name,
                    'deal_price': float(deal_price),
                    'original_price': float(original_price),
                    'discount_percent': float(discount_percent)
                })
        
        if picks:
            print(f"Deals now pick: {len(picks)} products selected")
            for pick in picks:
                print(f"  - {pick['product_name']} (${pick['deal_price']}, {pick['discount_percent']}% off)")
        
        return picks
        
    except pg8000.Error as e:
        print(f"Database error finding/updating deals_now_pick: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error finding/updating deals_now_pick: {e}")
        raise

def check_available_products(cur, schema_name):
    """Check how many products are available that meet our criteria."""
    try:
        # Check for deal of the day candidates
        deal_of_day_query = f"""
            SELECT COUNT(*) as count
            FROM {schema_name}.product
            WHERE is_active = true
            AND deal_price > 36
            AND discount_percent > 30
            AND promo_label != 'deal_of_the_day'
        """
        cur.execute(deal_of_day_query)
        deal_of_day_count = cur.fetchone()[0]
        
        # Check for deals now pick candidates
        deals_now_pick_query = f"""
            SELECT COUNT(*) as count
            FROM {schema_name}.product
            WHERE is_active = true
            AND deal_price > 27
            AND discount_percent > 27
            AND promo_label != 'deals_now_pick'
        """
        cur.execute(deals_now_pick_query)
        deals_now_pick_count = cur.fetchone()[0]
        
        print(f"Available candidates in {schema_name}: {deal_of_day_count} for deal of the day, {deals_now_pick_count} for deals now pick")
        
        return {
            'deal_of_day_candidates': deal_of_day_count,
            'deals_now_pick_candidates': deals_now_pick_count
        }
        
    except Exception as e:
        print(f"Error checking available products: {e}")
        return None

def run_update(country='us', schema_name=None):
    conn = None
    cur = None
    try:
        # Use provided schema_name or derive from country
        if schema_name is None:
            schema_name = get_schema_name(country)
        
        print(f"Starting promo products update for {country.upper()} using schema: {schema_name}")
        
        conn = get_db_connection()
        if not conn:
            return {
                'success': False,
                'error': 'Failed to establish database connection',
                'country': country,
                'schema': schema_name
            }
        
        # Set autocommit to False for transaction control
        conn.autocommit = False
        cur = conn.cursor()
        
        try:
            # Step 1: Verify schema and tables exist
            print("Step 1: Verifying schema and tables...")
            verify_schema_and_tables(cur, schema_name)
            print("Step 1: Schema verification completed")
            
            # Step 2: Get previously picked products to avoid re-selection
            print("Step 2: Getting previously picked products...")
            previously_picked_products = get_previously_picked_products(cur, schema_name)
            print(f"Step 2: Found {len(previously_picked_products)} previously picked products")
            
            # Step 3: Check available products first
            print("Step 3: Checking available products...")
            available_products = check_available_products(cur, schema_name)
            print("Step 3: Available products check completed")
            
            # Step 4: Clear previous selections
            print("Step 4: Clearing previous deal of the day...")
            cleared_count = clear_previous_deal_of_the_day(cur, schema_name)
            print(f"Step 4: Cleared {cleared_count} previous deal of the day")
            
            print("Step 5: Clearing previous deals now pick...")
            cleared_deals_now_pick = clear_previous_deals_now_pick(cur, schema_name)
            print(f"Step 5: Cleared {cleared_deals_now_pick} previous deals now pick")
            
            # Step 6: Find and update deal of the day (excluding previously picked products)
            print("Step 6: Finding and updating deal of the day...")
            new_deal = find_and_update_deal_of_the_day(cur, previously_picked_products, schema_name)
            print(f"Step 6: Deal of the day update completed: {new_deal is not None}")
            
            # Step 7: Find and update deals now pick (excluding previously picked products and deal of the day)
            print("Step 7: Finding and updating deals now pick...")
            deal_of_day_id = new_deal['product_id'] if new_deal else None
            new_deals_now_pick = find_and_update_deals_now_pick(cur, previously_picked_products, schema_name, exclude_product_id=deal_of_day_id)
            print(f"Step 7: Deals now pick update completed: {len(new_deals_now_pick)} products")
            
            # Step 8: Commit all changes
            print("Step 8: Committing transaction...")
            conn.commit()
            print("Step 8: Transaction committed successfully")
            
            response_data = {
                'success': True,
                'country': country,
                'schema': schema_name,
                'available_products': available_products,
                'previously_picked_count': len(previously_picked_products),
                'cleared_previous_deals': cleared_count,
                'cleared_previous_deals_now_pick': cleared_deals_now_pick,
                'deal_of_the_day': new_deal,
                'deals_now_pick': new_deals_now_pick,
                'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            
            print(f"Promo products update completed for {country.upper()} using schema {schema_name}")
            return response_data
            
        except Exception as e:
            # Rollback the transaction on any error
            print(f"Error during transaction at step, rolling back: {str(e)}")
            try:
                conn.rollback()
                print("Transaction rolled back successfully")
            except Exception as rollback_error:
                print(f"Error during rollback: {rollback_error}")
            raise e
        
    except Exception as e:
        error_msg = f"Error updating promo products for {country}: {str(e)}"
        print(f"Error: {error_msg}")
        return {
            'success': False, 
            'error': error_msg,
            'country': country,
            'schema': schema_name or get_schema_name(country)
        }
    finally:
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

def lambda_handler(event, context):
    # Extract parameters from event payload
    country = 'us'  # default
    schema_name = None  # default (will be derived from country)
    
    print(f"Raw event received: {json.dumps(event) if event else 'None'}")
    
    if event:
        # Support direct parameters
        country = event.get('country', 'us')
        schema_name = event.get('schema', None)
        
        # Support nested structure (Step Functions)
        if 'Input' in event:
            country = event['Input'].get('country', 'us')
            schema_name = event['Input'].get('schema', None)
    
    # Normalize country code
    country_normalized = country.lower() if country else 'us'
    derived_schema = get_schema_name(country)
    
    print(f"Lambda handler called:")
    print(f"  - Original country: {country}")
    print(f"  - Normalized country: {country_normalized}")
    print(f"  - Provided schema: {schema_name}")
    print(f"  - Derived schema: {derived_schema}")
    print(f"  - Final schema to use: {schema_name or derived_schema}")
    
    result = run_update(country, schema_name)
    status_code = 200 if result.get('success') else 500
    return {
        'statusCode': status_code,
        'body': json.dumps(result),
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        }
    }

def main():
    # For local testing, you can specify country and schema as environment variables
    country = os.environ.get('COUNTRY', 'us')
    schema_name = os.environ.get('SCHEMA', None)
    result = run_update(country, schema_name)
    if not result.get('success'):
        exit(1)

if __name__ == "__main__":
    main()