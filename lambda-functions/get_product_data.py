import json
import os
import datetime
import urllib.request
import threading
import re
import pg8000
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError
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

# Amazon PA API configuration
# AMAZON_SECRET_NAME = "dealsnow/amazon/paapi"
# AMAZON_REGION = "us-east-1"
# AMAZON_HOST = "webservices.amazon.com"
# AMAZON_ENDPOINT = f"https://{AMAZON_HOST}/paapi5/searchitems"

# Global clients for Lambda optimization (reuse across invocations)
bedrock_client = None
comprehend_client = None

def get_comprehend_client():
    """Get AWS Comprehend client."""
    global comprehend_client
    try:
        if comprehend_client is None:
            comprehend_client = boto3.client('comprehend', region_name=AWS_REGION)
        return comprehend_client
    except BotoCoreError as e:
        print(f"Error creating Comprehend client: {e}")
        return None

def extract_product_intent_with_comprehend(text):
    """
    Use AWS Comprehend to extract product intent from natural language queries.
    Returns a structured object with extracted entities and cleaned query.
    """
    if not text or not isinstance(text, str) or len(text.strip()) < 3:
        return {
            'original_query': text,
            'cleaned_query': text,
            'entities': {},
            'product_keywords': [],
            'categories': [],
            'brands': [],
            'product_types': [],
            'confidence': 0.0
        }
    
    try:
        comprehend = get_comprehend_client()
        if not comprehend:
            return fallback_query_processing(text)
        
        # Use Comprehend to detect entities
        response = comprehend.detect_entities(
            Text=text,
            LanguageCode='en'
        )
        
        entities = response.get('Entities', [])
        
        # Categorize entities by type
        product_keywords = []
        categories = []
        brands = []
        product_types = []
        
        # Common product categories and their variations
        product_categories = {
            'electronics': ['laptop', 'computer', 'phone', 'smartphone', 'tablet', 'headphones', 'earbuds', 'camera', 'tv', 'television', 'monitor', 'keyboard', 'mouse', 'speaker', 'gaming', 'console', 'playstation', 'xbox', 'nintendo'],
            'clothing': ['shirt', 'pants', 'jeans', 'dress', 'shoes', 'sneakers', 'boots', 'jacket', 'coat', 'hoodie', 'sweater', 't-shirt', 'tshirt', 'blouse', 'skirt', 'shorts', 'underwear', 'socks'],
            'home': ['furniture', 'chair', 'table', 'bed', 'sofa', 'couch', 'lamp', 'mirror', 'rug', 'curtain', 'pillow', 'blanket', 'sheet', 'towel', 'kitchen', 'appliance', 'refrigerator', 'microwave', 'oven', 'dishwasher'],
            'beauty': ['makeup', 'cosmetic', 'skincare', 'lotion', 'cream', 'shampoo', 'conditioner', 'perfume', 'cologne', 'brush', 'mirror', 'nail', 'lipstick', 'foundation', 'mascara'],
            'sports': ['fitness', 'gym', 'exercise', 'workout', 'running', 'basketball', 'football', 'soccer', 'tennis', 'golf', 'yoga', 'bike', 'bicycle', 'treadmill', 'dumbbell'],
            'books': ['book', 'novel', 'textbook', 'magazine', 'journal', 'diary', 'planner', 'calendar'],
            'toys': ['toy', 'game', 'puzzle', 'doll', 'action figure', 'lego', 'building block', 'board game', 'card game'],
            'automotive': ['car', 'truck', 'vehicle', 'tire', 'battery', 'oil', 'filter', 'brake', 'engine', 'transmission'],
            'baby': ['diaper', 'formula', 'bottle', 'pacifier', 'stroller', 'crib', 'car seat', 'baby food', 'wipes'],
            'pet': ['dog', 'cat', 'pet', 'food', 'toy', 'collar', 'leash', 'bed', 'crate', 'treat']
        }
        
        # Process entities from Comprehend
        for entity in entities:
            entity_text = entity.get('Text', '').lower().strip()
            entity_type = entity.get('Type', '').lower()
            score = entity.get('Score', 0.0)
            
            if score < 0.3:  # Skip low confidence entities
                continue
                
            # Categorize by entity type
            if entity_type in ['organization', 'commercial_item']:
                brands.append(entity_text)
            elif entity_type in ['location', 'event']:
                # Skip locations and events as they're not product-related
                continue
            else:
                # Check if it's a product category
                found_category = False
                for category, keywords in product_categories.items():
                    if entity_text in keywords or any(keyword in entity_text for keyword in keywords):
                        categories.append(category)
                        product_types.append(entity_text)
                        found_category = True
                        break
                
                if not found_category:
                    product_keywords.append(entity_text)
        
        # If no entities found, try keyword extraction
        if not product_keywords and not categories and not brands:
            return fallback_query_processing(text)
        
        # Build cleaned query from extracted entities
        all_keywords = product_keywords + product_types + brands
        cleaned_query = ' '.join(all_keywords) if all_keywords else text
        
        # Calculate confidence based on entity scores
        confidence = sum(entity.get('Score', 0.0) for entity in entities) / len(entities) if entities else 0.0
        
        return {
            'original_query': text,
            'cleaned_query': cleaned_query,
            'entities': {entity.get('Type', ''): entity.get('Text', '') for entity in entities},
            'product_keywords': product_keywords,
            'categories': list(set(categories)),  # Remove duplicates
            'brands': list(set(brands)),
            'product_types': list(set(product_types)),
            'confidence': confidence
        }
        
    except Exception as e:
        print(f"Error in Comprehend entity extraction: {e}")
        return fallback_query_processing(text)

def fallback_query_processing(text):
    """
    Fallback processing when Comprehend fails or returns no results.
    Uses basic NLP techniques to extract product intent.
    """
    if not text:
        return {
            'original_query': text,
            'cleaned_query': text,
            'entities': {},
            'product_keywords': [],
            'categories': [],
            'brands': [],
            'product_types': [],
            'confidence': 0.0
        }
    
    # Common noise words to filter out
    noise_words = {
        'find', 'an', 'amazing', 'deal', 'discount', 'offer', 'nice', 'good', 'great', 'awesome', 'excellent',
        'perfect', 'wonderful', 'fantastic', 'outstanding', 'superb', 'terrific', 'brilliant', 'fabulous',
        'incredible', 'unbelievable', 'phenomenal', 'exceptional', 'extraordinary', 'magnificent',
        'splendid', 'gorgeous', 'beautiful', 'lovely', 'charming', 'delightful', 'enjoyable',
        'pleasing', 'satisfying', 'rewarding', 'valuable', 'worthwhile', 'beneficial', 'helpful',
        'useful', 'practical', 'convenient', 'handy', 'efficient', 'effective', 'productive',
        'successful', 'profitable', 'lucrative', 'advantageous', 'favorable', 'positive', 'promising',
        'encouraging', 'hopeful', 'optimistic', 'bright', 'cheerful', 'happy', 'joyful', 'merry',
        'jolly', 'lively', 'energetic', 'dynamic', 'vibrant', 'enthusiastic', 'passionate',
        'dedicated', 'committed', 'devoted', 'loyal', 'faithful', 'reliable', 'trustworthy',
        'dependable', 'consistent', 'stable', 'secure', 'safe', 'protected', 'guarded',
        'defended', 'shielded', 'sheltered', 'preserved', 'maintained', 'sustained', 'supported',
        'backed', 'endorsed', 'approved', 'accepted', 'agreed', 'consented', 'permitted',
        'allowed', 'authorized', 'sanctioned', 'validated', 'verified', 'confirmed', 'certified',
        'guaranteed', 'assured', 'ensured', 'secured', 'obtained', 'acquired', 'gained',
        'achieved', 'attained', 'reached', 'accomplished', 'completed', 'finished', 'done',
        'fulfilled', 'satisfied', 'content', 'pleased', 'grateful', 'thankful', 'appreciative',
        'blessed', 'fortunate', 'lucky', 'privileged', 'honored', 'respected', 'admired',
        'esteemed', 'valued', 'cherished', 'treasured', 'prized', 'precious', 'valuable',
        'expensive', 'costly', 'pricey', 'high-end', 'premium', 'luxury', 'exclusive',
        'elite', 'superior', 'top-tier', 'first-class', 'world-class', 'champion', 'winner',
        'victor', 'leader', 'pioneer', 'innovator', 'trailblazer', 'groundbreaker', 'trendsetter',
        'influencer', 'authority', 'expert', 'specialist', 'professional', 'master', 'guru',
        'wizard', 'genius', 'prodigy', 'talent', 'gift', 'skill', 'ability', 'capability',
        'competence', 'proficiency', 'expertise', 'knowledge', 'wisdom', 'intelligence',
        'smart', 'clever', 'bright', 'brilliant', 'intelligent', 'wise', 'sensible', 'rational',
        'logical', 'reasonable', 'practical', 'realistic', 'sensible', 'prudent', 'careful',
        'cautious', 'wary', 'vigilant', 'alert', 'attentive', 'focused', 'concentrated',
        'determined', 'resolute', 'steadfast', 'unwavering', 'firm', 'strong', 'powerful',
        'mighty', 'forceful', 'influential', 'impactful', 'effective', 'efficient', 'productive',
        'successful', 'profitable', 'lucrative', 'advantageous', 'favorable', 'positive',
        'for', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'from', 'with',
        'by', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had',
        'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'can',
        'this', 'that', 'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they',
        'me', 'him', 'her', 'us', 'them', 'my', 'your', 'his', 'her', 'its', 'our', 'their',
        'mine', 'yours', 'his', 'hers', 'ours', 'theirs', 'myself', 'yourself', 'himself',
        'herself', 'itself', 'ourselves', 'yourselves', 'themselves'
    }
    
    # Split into words and filter out noise
    words = text.lower().split()
    filtered_words = [word for word in words if word not in noise_words and len(word) > 2]
    
    # Rejoin into cleaned query
    cleaned_query = ' '.join(filtered_words) if filtered_words else text
    
    return {
        'original_query': text,
        'cleaned_query': cleaned_query,
        'entities': {},
        'product_keywords': filtered_words,
        'categories': [],
        'brands': [],
        'product_types': [],
        'confidence': 0.5  # Medium confidence for fallback
    }

# def get_amazon_secret():
#     """Get Amazon PA API credentials from Secrets Manager."""
#     try:
#         client = boto3.client('secretsmanager', region_name=AMAZON_REGION)
#         response = client.get_secret_value(SecretId=AMAZON_SECRET_NAME)
#         return json.loads(response['SecretString'])
#     except boto3.exceptions.Boto3Error as e:
#         print(f"Error getting Amazon secrets: {e}")
#         return None

# def sign_amazon_request(method, url, body, access_key, secret_key):
#     """Sign Amazon PA API request"""
#     try:
#         credentials = Credentials(access_key, secret_key)
#         request = AWSRequest(
#             method=method,
#             url=url,
#             data=body,
#             headers={
#                 "Host": AMAZON_HOST,
#                 "Content-Type": "application/json; charset=UTF-8",
#                 "X-Amz-Target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems",
#                 "Content-Encoding": "amz-1.0"
#             }
#         )
#         SigV4Auth(credentials, 'ProductAdvertisingAPI', AMAZON_REGION).add_auth(request)
#         return dict(request.headers)
#     except Exception as e:
#         print(f"Error signing Amazon request: {e}")
#         return None

# def format_amazon_products(api_data):
#     """Format Amazon PA API response to match database product structure."""
#     formatted = []
#     try:
#         for item in api_data.get('SearchResult', {}).get('Items', []):
#             offer = item.get('Offers', {}).get('Listings', [{}])[0]
#             price_info = offer.get('Price', {})
#             saving_basis = offer.get('SavingBasis', {})

#             # Use HighRes if available, else Large, for all images
#             primary_image = item.get('Images', {}).get('Primary', {})
#             image_url = (
#                 primary_image.get('HighRes', {}).get('URL') or
#                 primary_image.get('Large', {}).get('URL')
#             )

#             variants = item.get('Images', {}).get('Variants', [])
#             image_urls = []
#             for v in variants[:3]:
#                 url = v.get('HighRes', {}).get('URL') or v.get('Large', {}).get('URL')
#                 if url:
#                     image_urls.append(url)
#             while len(image_urls) < 3:
#                 image_urls.append("")

#             # Price calculations and filter
#             try:
#                 deal_price = float(price_info.get('Amount', 0))
#                 orig_price = float(saving_basis.get('Amount', deal_price))
#                 if deal_price >= orig_price:
#                     continue  # Only keep discounted products
#                 discount = ((orig_price - deal_price) / orig_price) * 100
#             except (ValueError, TypeError):
#                 continue  # Skip if price data is invalid

#             # Description from features
#             features = item.get('ItemInfo', {}).get('Features', {}).get('DisplayValues', [])
#             description = "\n".join(features) if features else ""

#             # Category from classifications, fallback to Title if not found
#             classifications = item.get('ItemInfo', {}).get('Classifications', {})
#             category = (
#                 classifications.get('Binding', {}).get('DisplayValue') or
#                 classifications.get('ProductGroup', {}).get('DisplayValue') or
#                 item.get('ItemInfo', {}).get('Title', {}).get('DisplayValue', '')[:30] or
#                 "Unknown"
#             )

#             # Product name/title (fallback to ASIN if missing)
#             product_name = item.get('ItemInfo', {}).get('Title', {}).get('DisplayValue') or item.get('ASIN', '')

#             formatted.append({
#                 "product_id": item.get('ASIN', ''),
#                 "id": item.get('ASIN', ''),
#                 "name": product_name,
#                 "product_name": product_name,
#                 "description": description,
#                 "price": deal_price,
#                 "deal_price": deal_price,
#                 "orig_price": orig_price,
#                 "original_price": orig_price,
#                 "discount_percent": round(discount, 2),
#                 "image": image_url or (image_urls[0] if image_urls else ""),
#                 "image_url": image_url or (image_urls[0] if image_urls else ""),
#                 "image_url_2": image_urls[1] if len(image_urls) > 1 else "",
#                 "image_url_3": image_urls[2] if len(image_urls) > 2 else "",
#                 "sale_url": item.get('DetailPageURL', ''),
#                 "asin": item.get('ASIN', ''),
#                 "brand": item.get('ItemInfo', {}).get('ByLineInfo', {}).get('Brand', {}).get('DisplayValue', ''),
#                 "category": category,
#                 "retailer": "Amazon",
#                 "seller_id": "amazon",
#                 "deal_type": "Hot Deal",
#                 "start_date": price_info.get('StartDate'),
#                 "end_date": price_info.get('EndDate'),
#                 "coupon_info": offer.get('Promotions', [{}])[0].get('DisplayText', '') if offer.get('Promotions') else '',
#                 "similarity_score": None,
#                 "similarity_percentage": None,
#                 "created_at": datetime.datetime.now().isoformat(),
#                 "updated_at": datetime.datetime.now().isoformat(),
#                 "category_list": category,
#                 "is_active": True,
#                 "promo_label": "Amazon Deal"
#             })
#         return formatted
#     except Exception as e:
#         print(f"Error formatting Amazon products: {e}")
#         return []

# def insert_amazon_products_to_staging(products):
#     """Insert Amazon products into staging table asynchronously"""
#     if not products:
#         return
#     
#     def async_insert():
#         try:
#             conn = get_db_connection()
#             if not conn:
#                 return
#             
#             cur = conn.cursor()
#             
#             for product in products:
#                 try:
#                     cur.execute("""
#                         INSERT INTO deals_master.product_staging (
#                             product_name, description, deal_price, original_price,
#                             image_url, image_url_1, image_url_2, image_url_3,
#                             category, deal_type, deal_type_id, retailer,
#                             sale_url, product_keywords, product_rating,
#                             is_active, brand, product_type, coupon_info,
#                             category_list, start_date, end_date, promo_label,
#                             product_key, created_at, updated_at
#                         ) VALUES (
#                             %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
#                             %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
#                         ) ON CONFLICT (product_key) DO NOTHING
#                     """, (
#                         product.get('name', ''),
#                         product.get('description', ''),
#                         product.get('price', 0),
#                         product.get('orig_price', 0),
#                         product.get('image', ''),
#                         product.get('image_url_2', ''),
#                         product.get('image_url_3', ''),
#                         product.get('image_url_1', ''),
#                         product.get('category', 'Home'),
#                         product.get('deal_type', 'Hot Deal'),
#                         1,
#                         product.get('retailer', 'Amazon'),
#                         product.get('sale_url', ''),
#                         product.get('product_keywords', ''),
#                         product.get('product_rating', 4.5),
#                         product.get('is_active', True),
#                         product.get('brand', ''),
#                         product.get('product_type', 'Electronics'),
#                         product.get('coupon_info', ''),
#                         product.get('category_list', ''),
#                         product.get('start_date'),
#                         product.get('end_date'),
#                         product.get('promo_label', ''),
#                         product.get('asin', '99999'),
#                         datetime.datetime.now().isoformat(),
#                         datetime.datetime.now().isoformat()
#                     ))
#                 except Exception as e:
#                     print(f"Error inserting Amazon product: {e}")
#                     continue
#             
#             conn.commit()
#         except Exception as e:
#             print(f"Error in async Amazon product insertion: {e}")
#         finally:
#             if cur:
#                 cur.close()
#             if conn:
#                 conn.close()
#     
#     # Start async insertion (fire and forget)
#     import threading
#     thread = threading.Thread(target=async_insert)
#     thread.daemon = True
#     thread.start()

# def fetch_amazon_products(search_term):
#     """Fetch products from Amazon PA API using search term and insert into staging."""
#     try:
#         # Get Amazon credentials
#         creds = get_amazon_secret()
#         if not creds:
#             return []

#         # Build payload for Amazon PA API search
#         payload = {
#             "Resources": [
#                 "ItemInfo.Title",
#                 "ItemInfo.ByLineInfo",
#                 "ItemInfo.Features",
#                 "ItemInfo.Classifications",
#                 "Images.Primary.Large",
#                 "Images.Primary.HighRes",
#                 "Images.Variants.Large",
#                 "Images.Variants.HighRes",
#                 "Offers.Listings.Price",
#                 "Offers.Listings.SavingBasis",
#                 "Offers.Listings.Availability.Message",
#                 "Offers.Listings.Promotions"
#             ],
#             "PartnerTag": "dealsnow99-20",
#             "PartnerType": "Associates",
#             "OfferCount": 1,
#             "SearchIndex": "All",
#             "Keywords": search_term,
#             "ItemCount": 5  # Reduced from 10 for faster response
#         }

#         body = json.dumps(payload).encode('utf-8')

#         # Sign the request
#         headers = sign_amazon_request(
#             method="POST",
#             url=AMAZON_ENDPOINT,
#             body=body,
#             access_key=creds['ACCESS_KEY'],
#             secret_key=creds['SECRET_KEY']
#         )

#         if not headers:
#             return []

#         # Make the request with shorter timeout
#         req = urllib.request.Request(
#             AMAZON_ENDPOINT,
#             data=body,
#             headers=headers,
#             method="POST"
#         )

#         with urllib.request.urlopen(req, timeout=1.5) as response:  # Reduced timeout for faster response
#             data = json.loads(response.read().decode('utf-8'))
            
#             # Format products to match database structure
#             products = format_amazon_products(data)
#             products = products[:2]  # Limit to 2 products for faster response
            
#             # Insert products into staging table asynchronously (fire and forget)
#             try:
#                 insert_amazon_products_to_staging(products)
#             except Exception:
#                 pass  # Silent fail for insertion, continue with response

#             return products

#     except Exception:
#         return []

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

def get_active_promos(cur):
    """Get all active promo labels and image URLs using is_active flag."""
    try:
        check_query = "SELECT COUNT(*) FROM deals_master.promo_master"
        cur.execute(check_query)
        total_count = cur.fetchone()[0]
        
        if total_count == 0:
            return []
        
        active_check_query = """
            SELECT COUNT(*) FROM deals_master.promo_master WHERE is_active = true
        """
        cur.execute(active_check_query)
        active_count = cur.fetchone()[0]
        
        query = """
            SELECT promo_label, promo_label_image_url, promo_validity_start_dt, promo_validity_end_dt, is_active, promo_image_width_px, promo_image_height_px, promo_title, promo_position
            FROM deals_master.promo_master
            WHERE is_active = true
            ORDER BY promo_validity_start_dt DESC
        """
        cur.execute(query)
        results = cur.fetchall()
        
        promo_list = []
        for result in results:
            promo_data = {
                'promo_label': result[0],
                'promo_label_image_url': result[1],
                'promo_validity_start_dt': result[2].isoformat() if result[2] else None,
                'promo_validity_end_dt': result[3].isoformat() if result[3] else None,
                'is_active': result[4],
                'promo_image_width_px': result[5] if result[5] else None,
                'promo_image_height_px': result[6] if result[6] else None,
                'promo_title': result[7] if result[7] else None,
                'promo_position': result[8] if result[8] else None

            }
            promo_list.append(promo_data)
        return promo_list
    except pg8000.Error as e:
        print(f"Database error in get_active_promos: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error in get_active_promos: {e}")
        return []

def get_bedrock_client():
    """Create Bedrock client."""
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
    except boto3.exceptions.Boto3Error as e:
        print(f"Error creating Bedrock client: {e}")
        return None

def get_embedding(text):
    """Get embedding from Bedrock."""
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

    except boto3.exceptions.Boto3Error as e:
        print(f"Error getting embedding: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error getting embedding: {e}")
        return []

def vector_search(cur, embedding):
    """Perform vector similarity search with similarity threshold."""
    try:
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
        return process_results(cur, results)
    except pg8000.Error as e:
        print(f"Database error in vector_search: {e}")
        if hasattr(cur, 'connection') and cur.connection:
            cur.connection.rollback()
        return []
    except Exception as e:
        print(f"Unexpected error in vector_search: {e}")
        return []

def get_product_by_id(cur, product_id):
    """Fetch single product by ID."""
    try:
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
                to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as created_at,
                to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as updated_at,
                category_list,
                is_active
            FROM deals_master.product
            WHERE product_id = %s
        """
        cur.execute(query, (product_id,))
        result = cur.fetchone()
        return process_results(cur, [result]) if result else []
    except pg8000.Error as e:
        print(f"Database error during product fetch: {e}")
        if hasattr(cur, 'connection') and cur.connection:
            cur.connection.rollback()
        return []
    except Exception as e:
        print(f"Unexpected error during product fetch: {e}")
        return []

def text_search(cur, search_term):
    """Search for all words in product_keywords field using flexible word matching that handles plurals and possessives."""
    try:
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
                NULL::float as similarity_score,
                NULL::float as similarity_percentage,
                to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as created_at,
                to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as updated_at,
                category_list,
                is_active
            FROM deals_master.product
            WHERE is_active = true
            AND product_keywords IS NOT NULL
            AND (
                SELECT COUNT(*) 
                FROM UNNEST(STRING_TO_ARRAY(%s, ' ')) AS search_word
                WHERE product_keywords ~* (
                    '\\y' || 
                    -- Normalize search word (remove 's and ')
                    REGEXP_REPLACE(REGEXP_REPLACE(search_word, '''s$', '', 'i'), 's$', '', 'i') ||
                    -- Match variations: base word, with s, with 's
                    '(s|''s)?\\y'
                )
            ) = ARRAY_LENGTH(STRING_TO_ARRAY(%s, ' '), 1)
            ORDER BY product_id DESC
            LIMIT 30
        """
        
        cur.execute(query, (search_term, search_term))
        results = cur.fetchall()
        return process_results(cur, results)
        
    except pg8000.Error as e:
        print(f"Database error in text_search: {e}")
        if hasattr(cur, 'connection') and cur.connection:
            cur.connection.rollback()
        return []
    except Exception as e:
        print(f"Unexpected error in text_search: {e}")
        return []

def every_n_hours(n):
    """Decorator to run function every n hours."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorator

@every_n_hours(3)
def refresh_amazon_products():
    """Refresh Amazon products periodically."""
    try:
        search_terms = ["deals", "discounts", "electronics deals", "home deals"]
        for term in search_terms:
            # products = fetch_amazon_products(term)
            # if products:
            #     insert_amazon_products_to_staging(products)
            pass # Commented out Amazon API call
    except Exception as e:
        print(f"Error refreshing Amazon products: {e}")

def process_results(cur, products):
    """Convert query results to list of dicts with minimal processing for performance and JSON serialization."""
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

def get_products_by_promo_label(cur, promo_label):
    """Fetch products by promo_label (case-insensitive, trimmed)."""
    try:
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
                NULL::float as similarity_score,
                NULL::float as similarity_percentage,
                to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as created_at,
                to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as updated_at,
                category_list,
                is_active
            FROM deals_master.product
            WHERE is_active = true
            AND promo_label = %s
            ORDER BY product_id DESC
        """
        cur.execute(query, (promo_label.strip(),))
        columns = [desc[0] for desc in cur.description]
        results = []
        for row in cur.fetchall():
            row_dict = dict(zip(columns, row))
            for k, v in row_dict.items():
                if hasattr(v, 'isoformat'):
                    row_dict[k] = v.isoformat()
            results.append(row_dict)
        return results
    except Exception as e:
        print(f"Error in get_products_by_promo_label: {e}")
        return []

def lambda_handler(event, context):
    """Main Lambda handler."""
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
        path_parameters = event.get('pathParameters') or {}
        query_parameters = event.get('queryStringParameters') or {}
        
        body_parameters = {}
        if event.get('body'):
            try:
                body_parameters = json.loads(event.get('body', '{}'))
            except json.JSONDecodeError as e:
                print(f"Error parsing JSON body: {e}")
                body_parameters = {}
        
        product_id = path_parameters.get('productId') or path_parameters.get('id')
        search_term = (
            body_parameters.get('searchString', '') or 
            body_parameters.get('q', '') or
            query_parameters.get('searchString', '') or
            query_parameters.get('q', '') or
            event.get('searchString', '')
        ).strip()
        
        print(f'Product ID: {product_id}, Search Term: "{search_term}"')
        
        conn = get_db_connection()
        if not conn:
            print("Database connection failed")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Database connection failed'}),
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                    'Access-Control-Allow-Methods': 'GET,OPTIONS,POST,PUT,DELETE',
                    'Cache-Control': 'no-cache'
                }
            }

        cur = conn.cursor()
        results = []
        active_promos = []
        try:
            active_promos = get_active_promos(cur)
        except Exception as promo_error:
            print(f"Error fetching active promos: {promo_error}")

        if not search_term:
            try:
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
                        NULL::float as similarity_score,
                        NULL::float as similarity_percentage,
                        to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as created_at,
                        to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as updated_at,
                        category_list,
                        is_active
                    FROM deals_master.product
                    WHERE is_active = true
                    and deal_type_id in (1,3,4,5)
                    ORDER BY product_id DESC
                """
                cur.execute(query)
                columns = [desc[0] for desc in cur.description]
                raw_results = cur.fetchall()
                results = []
                for row in raw_results:
                    row_dict = dict(zip(columns, row))
                    for k, v in row_dict.items():
                        if hasattr(v, 'isoformat'):
                            row_dict[k] = v.isoformat()
                    results.append(row_dict)
            except pg8000.Error as e:
                print(f"Error fetching products: {e}")
                results = []
            response_data = {
                'products': results,
                'active_promos': active_promos
            }
            return {
                'statusCode': 200,
                'body': json.dumps(response_data),
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'no-cache, no-store, must-revalidate'
                }
            }

        response_data = {
            'products': [],
            'active_promos': active_promos,
            'query_analysis': {}
        }
        if search_term:
            try:
                # Step 1: Use Comprehend to extract product intent
                query_analysis = extract_product_intent_with_comprehend(search_term)
                cleaned_query = query_analysis['cleaned_query']
                word_count = len(query_analysis['product_keywords']) + len(query_analysis['product_types']) + len(query_analysis['brands'])
                
                print(f"Original query: '{search_term}'")
                print(f"Cleaned query: '{cleaned_query}'")
                print(f"Extracted keywords: {query_analysis['product_keywords']}")
                print(f"Extracted categories: {query_analysis['categories']}")
                print(f"Extracted brands: {query_analysis['brands']}")
                print(f"Extracted product types: {query_analysis['product_types']}")
                print(f"Confidence: {query_analysis['confidence']}")
                print(f"Word count: {word_count}")
                
                # Step 2: Perform search based on extracted intent
                text_results = []
                embedding_results = []
                
                if cleaned_query and cleaned_query != search_term:
                    # Use cleaned query for text search
                    text_results = text_search(cur, cleaned_query)
                
                # If text search returns no results, try original query
                if not text_results:
                    text_results = text_search(cur, search_term)
                
                # Step 3: Use vector search for longer queries or if text search fails
                if word_count > 3 or not text_results:
                    embedding = get_embedding(cleaned_query if cleaned_query else search_term)
                    if embedding:
                        embedding_results = vector_search(cur, embedding)
                
                # Step 4: Combine results
                all_results = text_results + embedding_results
                
                # Remove duplicates based on product_id
                seen_ids = set()
                unique_results = []
                for product in all_results:
                    product_id = product.get('product_id') or product.get('id')
                    if product_id and product_id not in seen_ids:
                        seen_ids.add(product_id)
                        unique_results.append(product)
                
                response_data['products'] = unique_results
                response_data['query_analysis'] = query_analysis
                
            except Exception as e:
                print(f"Error in search logic: {e}")
                response_data['products'] = []
                response_data['query_analysis'] = {
                    'original_query': search_term,
                    'cleaned_query': search_term,
                    'error': str(e)
                }
            
            # --- PROMO LABEL SEARCH LOGIC ---
            promo_labels = set((p['promo_label'] or '').strip() for p in active_promos if p.get('promo_label'))
            if search_term and search_term in promo_labels:
                promo_products = get_products_by_promo_label(cur, search_term)
                response_data = {
                    'products': promo_products,
                    'active_promos': active_promos
                }
                return {
                    'statusCode': 200,
                    'body': json.dumps(response_data),
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*',
                        'Cache-Control': 'no-cache, no-store, must-revalidate'
                    }
                }
            
            return {
                'statusCode': 200,
                'body': json.dumps(response_data),
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'no-cache, no-store, must-revalidate'
                }
            }
    except Exception as e:
        print(f"Error in lambda_handler: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error', 'details': str(e)}),
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'no-cache, no-store, must-revalidate'
            }
        }
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()