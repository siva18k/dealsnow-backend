import json
import urllib.request
import boto3
import pg8000
import os
import time
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from datetime import datetime

# Configuration
SECRET_NAME = "dealsnow/amazon/paapi"
REGION = "us-east-1"
HOST = "webservices.amazon.com"
ENDPOINT = f"https://{HOST}/paapi5/searchitems"

DB_CONFIG = {
    'host': os.environ.get('DB_HOST'),
    'database': os.environ.get('DB_NAME') or 'postgres',
    'user': os.environ.get('DB_USER'),
    'password': os.environ.get('DB_PASSWORD'),
    'port': int(os.environ.get('DB_PORT', 5432))  # Aurora PostgreSQL standard port
}

DEFAULT_CATEGORIES = [
    "Apparel", "Fashion", "FashionGirls", "FashionMen", "FashionWomen",
    "Beauty", "Jewelry", "LuxuryBeauty", "Shoes", "Watches",
    "ArtsAndCrafts", "Baby", "Luggage", "ToysAndGames",
    "Appliances", "Automotive", "Books", "Computers", "Electronics",
    "GardenAndOutdoor", "GiftCards", "HealthPersonalCare", "HomeAndKitchen",
    "LocalServices", "MobileAndAccessories", "OfficeProducts", "PetSupplies",
    "Software", "SportsAndOutdoors", "ToolsAndHomeImprovement"
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
    client = boto3.client('secretsmanager', region_name=REGION)
    response = client.get_secret_value(SecretId=SECRET_NAME)
    return json.loads(response['SecretString'])

def sign_request(method, url, body, access_key, secret_key):
    credentials = Credentials(access_key, secret_key)
    request = AWSRequest(
        method=method,
        url=url,
        data=body,
        headers={
            "Host": HOST,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Amz-Target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems",
            "Content-Encoding": "amz-1.0"
        }
    )
    SigV4Auth(credentials, 'ProductAdvertisingAPI', REGION).add_auth(request)
    return dict(request.headers)

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
                'retailer': 'Amazon'
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
                ON CONFLICT (product_key) DO UPDATE  -- Explicit conflict target
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

def format_products(api_data):
    formatted = []
    for item in api_data.get('SearchResult', {}).get('Items', []):
        offer = item.get('Offers', {}).get('Listings', [{}])[0]
        price_info = offer.get('Price', {})
        saving_basis = offer.get('SavingBasis', {})

        # Use HighRes if available, else Large, for all images
        primary_image = item.get('Images', {}).get('Primary', {})
        image_url = (
            primary_image.get('HighRes', {}).get('URL') or
            primary_image.get('Large', {}).get('URL')
        )

        variants = item.get('Images', {}).get('Variants', [])
        highres_variants = []
        for v in variants[:3]:
            url = v.get('HighRes', {}).get('URL') or v.get('Large', {}).get('URL')
            highres_variants.append(url if url else None)
        while len(highres_variants) < 3:
            highres_variants.append(None)

        # Price calculations and filter
        try:
            deal_price = float(price_info.get('Amount', 0))
            orig_price = float(saving_basis.get('Amount', deal_price))
            if deal_price >= orig_price:
                continue  # Only keep discounted products
            if deal_price <= 25:
                continue  # Only keep products with deal_price > $25
            discount = ((orig_price - deal_price)/orig_price)*100
        except:
            continue  # Skip if price data is invalid

        # Description from features
        features = item.get('ItemInfo', {}).get('Features', {}).get('DisplayValues', [])
        description = "\n".join(features) if features else ""

        # Category and category_list from Classifications
        classifications = item.get('ItemInfo', {}).get('Classifications', {})
        category = classifications.get('Binding', {}).get('DisplayValue') or "Unknown"
        category_list = classifications.get('ProductGroup', {}).get('DisplayValue')

        formatted.append({
            "name": item.get('ItemInfo', {}).get('Title', {}).get('DisplayValue'),
            "description": description,
            "price": deal_price,
            "orig_price": orig_price,
            "discount_percent": round(discount, 2),
            "image": image_url,
            "image_url_1": highres_variants[0],
            "image_url_2": highres_variants[1],
            "image_url_3": highres_variants[2],
            "sale_url": item.get('DetailPageURL'),
            "product_key": item.get('ASIN'),
            "brand": item.get('ItemInfo', {}).get('ByLineInfo', {}).get('Brand', {}).get('DisplayValue'),
            "category": category,
            "category_list": category_list,
            "start_date": price_info.get('StartDate'),
            "end_date": price_info.get('EndDate'),
            "stock_status": "In Stock" if "In Stock" in offer.get('Availability', {}).get('Message', '') else "Out of Stock"
        })
    return formatted

def invoke_embedding(payload, function_name):
    client = boto3.client('lambda')
    response = client.invoke(
        FunctionName=function_name,
        InvocationType='Event',  # 'Event' for async, 'RequestResponse' for sync
        Payload=json.dumps(payload)
    )
    return response

def lambda_handler(event, context):
    success=None
    try:
        input_categories = event.get('categories', DEFAULT_CATEGORIES)
        seen = set()
        categories = [x for x in input_categories if not (x in seen or seen.add(x))]
        
        creds = get_secret()
        all_inserted_ids = []
        
        for category in categories:
            print(f"Processing category: {category}")
            payload = {
                "Resources": [
                    "ItemInfo.Title",
                    "ItemInfo.ByLineInfo",
                    "ItemInfo.Features",
                    "ItemInfo.Classifications",
                    "Images.Primary.Large",
                    "Images.Variants.Large",
                    "Offers.Listings.Price",
                    "Offers.Listings.SavingBasis",
                    "Offers.Listings.Availability.Message"
                ],
                "SearchIndex": category,
                "PartnerTag": "dealsnow99-20",
                "PartnerType": "Associates",
                "OfferCount": 1,
                "MinPrice": 10,
                "MinReviewsRating": 4,
                "MinSavingPercent": 14,
                "SortBy": "Relevance"
            }
            
            body = json.dumps(payload).encode('utf-8')
            headers = sign_request(
                method="POST",
                url=ENDPOINT,
                body=body,
                access_key=creds['ACCESS_KEY'],
                secret_key=creds['SECRET_KEY']
            )
            
            try:
                req = urllib.request.Request(
                    ENDPOINT,
                    data=body,
                    headers=headers,
                    method="POST"
                )
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    products = format_products(data)
                    products = products[:20]  # Limit to 3 products per category
                    if products:
                        success, message, ids = insert_products(products, 'deals_master.product')
                        if success:
                            all_inserted_ids.extend(ids)
                            print(f"Inserted {len(ids)} products for {category}")
                        else:
                            print(f"Failed to insert {category}: {message}")

                    else:
                        print(f"No products found for {category}")
                        
            except urllib.error.HTTPError as e:
                error_body = e.read().decode()
                print(f"PAAPI error for {category}: {error_body}")
            except Exception as e:
                print(f"Error processing {category}: {str(e)}")
            
            time.sleep(5)
        
        if success:
            print('invoking embedding')
            payload = {"key": "value"}  # whatever you want to pass
            response=invoke_embedding(payload, "update_product_embedding")
            print('Initiated Embedding',response)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "processed_categories": categories,
                "total_inserted": len(all_inserted_ids),
                "inserted_ids": all_inserted_ids
            })
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
