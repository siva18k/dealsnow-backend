import json
import pg8000
import os
import boto3
import re
import time
from botocore.config import Config
import datetime
import urllib.request
# from botocore.auth import SigV4Auth
# from botocore.awsrequest import AWSRequest
# from botocore.credentials import Credentials

# Database configuration
DB_HOST = os.environ.get('DB_HOST')
DB_NAME = os.environ.get('DB_NAME')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')

# AWS configuration
AWS_REGION = 'us-east-2'
BEDROCK_MODEL_ID = 'amazon.titan-embed-text-v2:0'
S3_BUCKET = 'dealsnow-data'
S3_ALL_KEY = 'deals_master/product_data_all.json'

# Comprehend Custom Model ARNs (to be set after model creation)
CUSTOM_ENTITY_RECOGNIZER_ARN = os.environ.get('CUSTOM_ENTITY_RECOGNIZER_ARN', '')
CUSTOM_CLASSIFIER_ARN = os.environ.get('CUSTOM_CLASSIFIER_ARN', '')

# Global clients for Lambda optimization (reuse across invocations)
bedrock_client = None
comprehend_client = None
s3_client = None

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

def get_bedrock_client():
    """Get Bedrock client for embeddings"""
    global bedrock_client
    try:
        if bedrock_client is None:
            config = Config(
                region_name=AWS_REGION,
                signature_version='v4',
                retries={'max_attempts': 3}
            )
            bedrock_client = boto3.client('bedrock-runtime', region_name=AWS_REGION, config=config)
        return bedrock_client
    except Exception as e:
        print(f"Error creating Bedrock client: {e}")
        return None

def get_comprehend_client():
    """Get Comprehend client"""
    global comprehend_client
    try:
        if comprehend_client is None:
            comprehend_client = boto3.client('comprehend', region_name=AWS_REGION)
            print(f"Comprehend client created successfully in region: {AWS_REGION}")
        return comprehend_client
    except Exception as e:
        print(f"Error creating Comprehend client: {e}")
        return None

def get_s3_client():
    """Get S3 client"""
    global s3_client
    try:
        if s3_client is None:
            config = Config(
                region_name=AWS_REGION,
                signature_version='v4',
                retries={'max_attempts': 3}
            )
            s3_client = boto3.client('s3', region_name=AWS_REGION, config=config)
        return s3_client
    except Exception as e:
        print(f"Error creating S3 client: {e}")
        return None

def load_products_from_s3():
    """Load products from S3 for text-based search"""
    try:
        s3 = get_s3_client()
        if not s3:
            print("S3 client not available")
            return []
        
        print(f"Loading products from S3: {S3_BUCKET}/{S3_ALL_KEY}")
        response = s3.get_object(Bucket=S3_BUCKET, Key=S3_ALL_KEY)
        products_data = json.loads(response['Body'].read().decode('utf-8'))
        
        # Handle both array and object formats
        if isinstance(products_data, list):
            products = products_data
        elif isinstance(products_data, dict) and 'products' in products_data:
            products = products_data['products']
        else:
            products = []
        
        print(f"Loaded {len(products)} products from S3")
        return products
        
    except Exception as e:
        print(f"Error loading products from S3: {e}")
        return []

def extract_price_from_search(search_term):
    """Extract price range from search term using simple regex"""
    if not search_term:
        return None, None
    
    search_lower = search_term.lower()
    min_price = None
    max_price = None
    
    # Common price patterns
    price_patterns = [
        # "under $30", "less than $30", "max $30", "up to $30"
        (r'under\s+\$?(\d+(?:\.\d{2})?)', None, lambda x: float(x)),
        (r'less\s+than\s+\$?(\d+(?:\.\d{2})?)', None, lambda x: float(x)),
        (r'max\s+\$?(\d+(?:\.\d{2})?)', None, lambda x: float(x)),
        (r'up\s+to\s+\$?(\d+(?:\.\d{2})?)', None, lambda x: float(x)),
        (r'below\s+\$?(\d+(?:\.\d{2})?)', None, lambda x: float(x)),
        
        # "over $30", "more than $30", "above $30"
        (r'over\s+\$?(\d+(?:\.\d{2})?)', lambda x: float(x), None),
        (r'more\s+than\s+\$?(\d+(?:\.\d{2})?)', lambda x: float(x), None),
        (r'above\s+\$?(\d+(?:\.\d{2})?)', lambda x: float(x), None),
        
        # "$30 to $50", "$30-$50", "between $30 and $50"
        (r'\$?(\d+(?:\.\d{2})?)\s+to\s+\$?(\d+(?:\.\d{2})?)', lambda x: float(x), lambda y: float(y)),
        (r'\$?(\d+(?:\.\d{2})?)-\$?(\d+(?:\.\d{2})?)', lambda x: float(x), lambda y: float(y)),
        (r'between\s+\$?(\d+(?:\.\d{2})?)\s+and\s+\$?(\d+(?:\.\d{2})?)', lambda x: float(x), lambda y: float(y)),
        
        # "around $30", "about $30" (give some range)
        (r'around\s+\$?(\d+(?:\.\d{2})?)', lambda x: float(x) * 0.8, lambda x: float(x) * 1.2),
        (r'about\s+\$?(\d+(?:\.\d{2})?)', lambda x: float(x) * 0.8, lambda x: float(x) * 1.2),
        
        # Simple price mentions "$30"
        (r'\$(\d+(?:\.\d{2})?)', lambda x: float(x) * 0.9, lambda x: float(x) * 1.1),
    ]
    
    for pattern, min_func, max_func in price_patterns:
        match = re.search(pattern, search_lower)
        if match:
            if len(match.groups()) == 1:
                price = match.group(1)
                if min_func:
                    min_price = min_func(price)
                if max_func:
                    max_price = max_func(price)
            elif len(match.groups()) == 2:
                min_price = min_func(match.group(1))
                max_price = max_func(match.group(2))
            break
    
    print(f"Extracted price range from '{search_term}': min=${min_price}, max=${max_price}")
    return min_price, max_price

def clean_search_term_with_comprehend(search_term):
    """Use AWS Comprehend to clean and enhance search terms"""
    try:
        if not search_term:
            return search_term
        
        comprehend = get_comprehend_client()
        if not comprehend:
            print("Comprehend client not available, using basic cleaning")
            return clean_search_term_basic(search_term)
        
        print(f"Using AWS Comprehend to clean search term: '{search_term}'")
        
        # Extract key phrases using Comprehend
        key_phrase_response = comprehend.detect_key_phrases(
            Text=search_term,
            LanguageCode='en'
        )
        
        # Extract entities using Comprehend
        entity_response = comprehend.detect_entities(
            Text=search_term,
            LanguageCode='en'
        )
        
        # Combine key phrases and entities
        key_phrases = [phrase.get('Text', '').lower() for phrase in key_phrase_response.get('KeyPhrases', [])]
        entities = [entity.get('Text', '').lower() for entity in entity_response.get('Entities', [])]
        
        # Remove price-related terms
        price_terms = ['under', 'over', 'less than', 'more than', 'max', 'up to', 'below', 'above', 'around', 'about', 'between', 'to']
        filtered_phrases = [phrase for phrase in key_phrases if not any(price_term in phrase for price_term in price_terms)]
        filtered_entities = [entity for entity in entities if not any(price_term in entity for price_term in price_terms)]
        
        # Combine and deduplicate
        all_terms = list(set(filtered_phrases + filtered_entities))
        
        # If Comprehend didn't find anything useful, fall back to basic cleaning
        if not all_terms:
            return clean_search_term_basic(search_term)
        
        cleaned_term = ' '.join(all_terms)
        print(f"Comprehend cleaned search term: '{search_term}' -> '{cleaned_term}'")
        return cleaned_term
        
    except Exception as e:
        print(f"Error in Comprehend cleaning: {e}, falling back to basic cleaning")
        return clean_search_term_basic(search_term)

def clean_search_term_basic(search_term):
    """Basic search term cleaning without Comprehend"""
    if not search_term:
        return search_term
    
    # Remove price-related patterns
    price_patterns_to_remove = [
        r'under\s+\$?\d+(?:\.\d{2})?',
        r'less\s+than\s+\$?\d+(?:\.\d{2})?',
        r'max\s+\$?\d+(?:\.\d{2})?',
        r'up\s+to\s+\$?\d+(?:\.\d{2})?',
        r'below\s+\$?\d+(?:\.\d{2})?',
        r'over\s+\$?\d+(?:\.\d{2})?',
        r'more\s+than\s+\$?\d+(?:\.\d{2})?',
        r'above\s+\$?\d+(?:\.\d{2})?',
        r'\$?\d+(?:\.\d{2})?\s+to\s+\$?\d+(?:\.\d{2})?',
        r'\$?\d+(?:\.\d{2})?-\$?\d+(?:\.\d{2})?',
        r'between\s+\$?\d+(?:\.\d{2})?\s+and\s+\$?\d+(?:\.\d{2})?',
        r'around\s+\$?\d+(?:\.\d{2})?',
        r'about\s+\$?\d+(?:\.\d{2})?',
        r'\$\d+(?:\.\d{2})?'
    ]
    
    cleaned_term = search_term
    for pattern in price_patterns_to_remove:
        cleaned_term = re.sub(pattern, '', cleaned_term, flags=re.IGNORECASE)
    
    # Clean up extra whitespace and common stop words
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were', 'e', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can'}
    words = cleaned_term.lower().split()
    filtered_words = [word for word in words if word not in stop_words and len(word) > 2]
    
    cleaned_term = ' '.join(filtered_words)
    print(f"Basic cleaned search term: '{search_term}' -> '{cleaned_term}'")
    return cleaned_term

def text_search_s3(products, search_term, min_price=None, max_price=None):
    """text-based search using S3 data"""
    try:
        start_time = time.time()
        search_words = search_term.lower().split()
        
        if not search_words:
            return []
        
        filtered_products = []
        
        for product in products:
            if not product.get('is_active', True):
                continue
            
            # Create searchable text from relevant fields only
            searchable_text = ' '.join([
                str(product.get('product_name', '')),
                str(product.get('category', '')),
                str(product.get('category_list', '')),
                str(product.get('product_key', '')),
                str(product.get('retailer', '')),
                str(product.get('promo_label', ''))
            ]).lower()
            
            # Check if all search words are found in the searchable text
            all_words_found = True
            for word in search_words:
                if len(word) > 2 and word not in searchable_text:
                    all_words_found = False
                    break
            
            if not all_words_found:
                continue
            
            # Apply price filtering
            if min_price is not None or max_price is not None:
                product_price = float(product.get('deal_price', 0))
                if min_price is not None and product_price < min_price:
                    continue
                if max_price is not None and product_price > max_price:
                    continue
            
            # Calculate relevance score
            relevance_score = 0
            product_name = str(product.get('product_name', '')).lower()
            category = str(product.get('category', '')).lower()
            brand = str(product.get('brand', '')).lower()
            
            for word in search_words:
                # Higher score for exact matches in product name
                if word in product_name:
                    relevance_score += 3
                # Medium score for category matches
                elif word in category:
                    relevance_score += 2
                # Lower score for brand matches
                elif word in brand:
                    relevance_score += 1
                # Base score for being found in searchable text
                else:
                    relevance_score += 0.5      
            # Add discount bonus
            discount = float(product.get('discount_percent', 0))
            discount_bonus = min(discount / 10, 3)  # Cap at 3 points for 30%+ discount
            relevance_score += discount_bonus
            
            product['_relevance_score'] = relevance_score
            filtered_products.append(product)
        
        # Sort by relevance score
        filtered_products.sort(key=lambda x: x['_relevance_score'], reverse=True)
        
        # Limit results
        results = filtered_products[:20]
        
        # Convert to expected format
        formatted_results = []
        for product in results:
            formatted_product = {
                'product_id': product.get('product_id'),
                'name': product.get('product_name'),
                'description': product.get('description'),
                'price': float(product.get('deal_price', 0)),
                'orig_price': float(product.get('original_price', 0)),
                'discount_percent': float(product.get('discount_percent', 0)),
                'coupon_info': product.get('coupon_info'),
                'image': product.get('image_url'),
                'category_id': product.get('category_id'),
                'category': product.get('category'),
                'seller_id': product.get('seller_id'),
                'retailer': product.get('retailer'),
                'deal_type_id': product.get('deal_type_id'),
                'deal_type': product.get('deal_type'),
                'sale_url': product.get('sale_url'),
                'image_url_2': product.get('image_url_2'),
                'image_url_3': product.get('image_url_3'),
                'brand': product.get('brand'),
                'start_date': product.get('start_date'),
                'end_date': product.get('end_date'),
                'promo_label': product.get('promo_label'),
                'similarity_score': product.get('_relevance_score'),
                'similarity_percentage': min(product.get('_relevance_score', 0) * 10, 100),  # Convert to percentage
                'created_at': product.get('created_at'),
                'updated_at': product.get('updated_at'),
                'category_list': product.get('category_list'),
                'is_active': product.get('is_active', True),
                'matched_terms': [], # No matched terms for text search
                'sentiment_score': 0 # No sentiment score for text search
            }
            formatted_results.append(formatted_product)
        
        search_time = time.time() - start_time
        print(f"S3 text search found {len(formatted_results)} results in {search_time:.3f}s")
        return formatted_results
        
    except Exception as e:
        print(f"Error in S3 text search: {e}")
        return []

def text_search_database(cur, search_term, min_price=None, max_price=None):
    """text-based search using database"""
    try:
        start_time = time.time()
        search_words = search_term.lower().split()
        
        if not search_words:
            return []
        
        # Build dynamic query for multiple word search
        word_conditions = []
        params = []
        
        for word in search_words:
            if len(word) > 2:
                word_conditions.append("""
                    (LOWER(product_name) LIKE %s OR 
                     LOWER(category) LIKE %s OR 
                     LOWER(category_list) LIKE %s OR 
                     LOWER(product_key) LIKE %s OR 
                     LOWER(retailer) LIKE %s OR 
                     LOWER(promo_label) LIKE %s)
                """)
                params.extend([f'%{word}%'] * 6)
        
        if not word_conditions:
            return []
        
        # Build the main query
        query = f"""
            SELECT 
                product_id, 
                product_name as name, 
                description, 
                deal_price::float as price, 
                original_price::float as orig_price, 
                discount_percent,
                coupon_info,
                image_url as image, 
                category_id, 
                category,
                seller_id, 
                retailer, 
                deal_type_id, 
                deal_type, 
                sale_url, 
                image_url_2, 
                image_url_3,
                brand,
                start_date,
                end_date,
                promo_label,
                NULL::float as similarity_score,
                NULL::float as similarity_percentage,
                to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as created_at,
                to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as updated_at,
                category_list,
                is_active
            FROM deals_master.product
            WHERE is_active = true
            AND ({' OR '.join(word_conditions)})
        """
        
        # Add price filtering if specified
        if min_price is not None or max_price is not None:
            if min_price is not None and max_price is not None:
                query += " AND deal_price::float BETWEEN %s AND %s"
                params.extend([min_price, max_price])
            elif min_price is not None:
                query += " AND deal_price::float >= %s"
                params.append(min_price)
            elif max_price is not None:
                query += " AND deal_price::float <= %s"
                params.append(max_price)
        
        query += " ORDER BY product_id DESC LIMIT 20"
        cur.execute(query, params)
        results = cur.fetchall()
        formatted_results = process_results(cur, results)
        
        search_time = time.time() - start_time
        print(f"Database text search found {len(formatted_results)} results in {search_time:.3f}s")
        return formatted_results
        
    except pg8000.Error as e:
        print(f"Database error in text_search_database: {e}")
        if hasattr(cur, 'connection') and cur.connection:
            cur.connection.rollback()
        return []
    except Exception as e:
        print(f"Unexpected error in text_search_database: {e}")
        return []

def get_embedding(text):
    """Get embedding for text using Bedrock"""
    try:
        if not text:
            return []
        bedrock_runtime = get_bedrock_client()
        if not bedrock_runtime:
            return []
        request_body = {
            "inputText": text,
            "dimensions": 1024,
            "normalize": True
        }
        response = bedrock_runtime.invoke_model(
            body=json.dumps(request_body),
            modelId=BEDROCK_MODEL_ID,
            accept='application/json',
            contentType='application/json'
        )
        response_body = json.loads(response.get('body').read())
        return response_body.get('embedding', [])
    except Exception as e:
        print(f"Error getting embedding: {e}")
        return []

def vector_search(cur, embedding):
    """Perform vector search using embeddings"""
    try:
        start_time = time.time()
        embedding_str = f"[{','.join(map(str, embedding))}]"
        query = """
            SELECT 
                product_id, 
                product_name as name, 
                description, 
                deal_price::float as price, 
                original_price::float as orig_price, 
                discount_percent,
                coupon_info,
                image_url as image, 
                category_id, 
                category,
                seller_id, 
                retailer, 
                deal_type_id, 
                deal_type, 
                sale_url, 
                image_url_2, 
                image_url_3,
                brand,
                start_date,
                end_date,
                promo_label,
                (1 - (embedding <=> cast(%s as vector)))::float as similarity_score,
                ((1 - (embedding <=> cast(%s as vector))) * 100)::float as similarity_percentage,
                to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as created_at,
                to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as updated_at,
                category_list,
                is_active
            FROM deals_master.product
            WHERE is_active = true
            AND embedding IS NOT NULL
            AND (1 - (embedding <=> cast(%s as vector))) > 0.1
            ORDER BY 
                similarity_score DESC
            LIMIT 20
        """
        params = (embedding_str,) * 3
        cur.execute(query, params)
        results = cur.fetchall()
        formatted_results = process_results(cur, results)
        
        search_time = time.time() - start_time
        print(f"Vector search found {len(formatted_results)} results in {search_time:.3f}s")
        return formatted_results
    except pg8000.Error as e:
        print(f"Database error in vector_search: {e}")
        if hasattr(cur, 'connection') and cur.connection:
            cur.connection.rollback()
        return []
    except Exception as e:
        print(f"Unexpected error in vector_search: {e}")
        return []

def process_results(cur, products):
    """Process database results into JSON format"""
    if not products:
        return []
    columns = [desc[0] for desc in cur.description]
    results = []
    for row in products:
        row_dict = dict(zip(columns, row))
        for k, v in row_dict.items():
            if hasattr(v, 'isoformat'):
                row_dict[k] = v.isoformat()
        results.append(row_dict)
    return results

def lambda_handler(event, context):
    """Main Lambda handler for optimized search functionality"""
    # Handle CORS preflight requests first
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token,Cache-Control,If-None-Match',
                'Access-Control-Allow-Methods': 'GET,OPTIONS,POST,PUT,DELETE',
                'Access-Control-Max-Age': '86400'
            },
            'body': ''
        }
    
    conn = None
    cur = None

    try:
        # Extract parameters from API Gateway event structure
        path_parameters = event.get('pathParameters') or {}
        query_parameters = event.get('queryStringParameters') or {}
        
        # Handle both JSON body and query parameters
        body_parameters = {}
        if event.get('body'):
            try:
                body_parameters = json.loads(event.get('body', '{}'))
            except json.JSONDecodeError:
                print("Error parsing JSON body")
                body_parameters = {}
        
        # Get search term from multiple sources (body takes precedence)
        search_term = (
            body_parameters.get('searchString', '') or 
            query_parameters.get('searchString', '') or
            event.get('searchString', '')  # Direct event parameter
        ).strip()

        print(f'Search Event: {json.dumps(event, default=str)}')
        print(f'Original Search Term: "{search_term}"')
        
        # Require search term for this lambda
        if not search_term:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Search term is required'}),
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'no-cache'
                }
            }

        # Extract price range from search term
        min_price, max_price = extract_price_from_search(search_term)
        print(f"Extracted price range: min=${min_price}, max=${max_price}")
        
        # Step1 Use Comprehend to clean the search input
        cleaned_search_term = clean_search_term_with_comprehend(search_term)
        print(f'Cleaned Search Term: "{cleaned_search_term}"')
        
        # If cleaning removed all words, return empty results
        if not cleaned_search_term or cleaned_search_term.strip() == '':
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No meaningful search terms found after cleaning',
                    'searchTerm': search_term,
                    'cleanedSearchTerm': cleaned_search_term,
                    'priceRange': {'min': min_price, 'max': max_price},
                    'searchMethod': 'comprehend_cleaning',
                    'results': []
                }),
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'no-cache'
                }
            }

        results = []
        search_method = 'unknown'
        
        # Always use database text search
        print("Step 2: Using database for text-based search...")
        conn = get_db_connection()
        if not conn:
            print("Database connection failed")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Database connection failed'}),
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'no-cache'
                }
            }
        cur = conn.cursor()
        text_results = text_search_database(cur, cleaned_search_term, min_price, max_price)
        search_method = 'database_text'
        print(f"Text search found {len(text_results)} results")
        
        # Step3 If text search has sufficient results, use them
        if len(text_results) >= 5:
            results = text_results
            print(f"Using {search_method} results ({len(results)} found)")
        else:
            # Step 4: Fall back to vector search if text search has low/no results
            print(f"Text search found insufficient results ({len(text_results)}), falling back to vector search...")
            embedding = get_embedding(cleaned_search_term)
            if embedding:
                vector_results = vector_search(cur, embedding)
                if len(vector_results) > len(text_results):
                    results = vector_results
                    search_method = 'vector'
                    print(f"Vector search found {len(vector_results)} results")
                else:
                    results = text_results
                    search_method = 'text_only'
                    print(f"Keeping text search results ({len(text_results)})")
            else:
                results = text_results
                search_method = 'text_only'
                print("Failed to get embedding, keeping text search results")

        # Prepare response headers
        response_headers = {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'no-cache, no-store, must-revalidate'  # Search results should not be cached
        }

        if not results:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No search results found',
                    'searchTerm': search_term,
                    'cleanedSearchTerm': cleaned_search_term,
                    'searchMethod': search_method,
                    'priceRange': {'min': min_price, 'max': max_price},
                    'results': []
                }),
                'headers': response_headers
            }

        # Prepare response
        response_data = {
            'searchTerm': search_term,
            'cleanedSearchTerm': cleaned_search_term,
            'searchMethod': search_method,
            'priceRange': {'min': min_price, 'max': max_price},
            'results': results,
            'totalResults': len(results)
        }

        return {
            'statusCode': 200,
            'body': json.dumps(response_data),
            'headers': response_headers
        }

    except Exception as e:
        print(f"Search Lambda handler error: {e}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Search server error: {str(e)}'}),
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'no-cache'
            }
        }

    finally:
        # Ensure resources are always closed
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