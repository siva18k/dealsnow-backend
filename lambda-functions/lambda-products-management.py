import json
import os
import pg8000
import hashlib
import base64
import boto3

CORS_HEADERS = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-Api-Key,X-Country-Code',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
}

def _get_db_connection():
    """Connect via AWS Secrets Manager (Aurora) or PG_* / DB_* env. Port 5432 for Aurora PostgreSQL."""
    secret_name = os.environ.get('DB_SECRET_NAME') or os.environ.get('DB_SECRET_ARN')
    if secret_name:
        try:
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
        except Exception as e:
            print(f"Secrets Manager connection failed: {e}")
            return None
    host = os.environ.get('PG_HOST') or os.environ.get('DB_HOST')
    if host:
        return pg8000.connect(
            host=host,
            database=os.environ.get('PG_DATABASE') or os.environ.get('DB_NAME') or 'postgres',
            user=os.environ.get('PG_USER') or os.environ.get('DB_USER'),
            password=os.environ.get('PG_PASSWORD') or os.environ.get('DB_PASSWORD'),
            port=int(os.environ.get('PG_PORT') or os.environ.get('DB_PORT') or 5432)
        )
    return None

def verify_password(password, stored_hash):
    decoded = base64.b64decode(stored_hash.encode('utf-8'))
    salt = decoded[:16]
    key = decoded[16:]
    new_key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
    return new_key == key

def lambda_handler(event, context):
    try:
        if 'body' in event:
            body = json.loads(event['body'])
        else:
            body = event
        email = body.get('email')
        password = body.get('password')
        if not email or not password:
            return {"statusCode": 400, "body": json.dumps({"error": "Missing fields"}), "headers": CORS_HEADERS}
        # Aurora PostgreSQL: prefer DB_SECRET_NAME (Secrets Manager), else PG_* / DB_* env
        conn = _get_db_connection()
        if not conn:
            return {"statusCode": 500, "body": json.dumps({"error": "Database connection failed"}), "headers": CORS_HEADERS}
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, name, email, password_hash, preferred_categories FROM deals_master.users WHERE email = %s", (email,))
            user_row = cur.fetchone()
            if not user_row:
                return {"statusCode": 401, "body": json.dumps({"error": "Invalid email or password"}), "headers": CORS_HEADERS}
            user = dict(zip([desc[0] for desc in cur.description], user_row))
            if not verify_password(password, user['password_hash']):
                return {"statusCode": 401, "body": json.dumps({"error": "Invalid email or password"}), "headers": CORS_HEADERS}
            # Remove password_hash before returning
            user.pop('password_hash', None)
            return {"statusCode": 200, "body": json.dumps({"user": user}), "headers": CORS_HEADERS}
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)}), "headers": CORS_HEADERS}
