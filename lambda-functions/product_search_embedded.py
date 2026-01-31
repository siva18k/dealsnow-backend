import json
import os
import pg8000
import hashlib
import base64

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
            return {"statusCode": 400, "body": json.dumps({"error": "Missing fields"})}
        conn = pg8000.connect(
            host=os.environ['PG_HOST'],
            database=os.environ['PG_DATABASE'],
            user=os.environ['PG_USER'],
            password=os.environ['PG_PASSWORD'],
            port=5432
        )
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, name, email, password_hash, preferred_categories FROM deals_master.users WHERE email = %s", (email,))
            user_row = cur.fetchone()
            if not user_row:
                return {"statusCode": 401, "body": json.dumps({"error": "Invalid email or password"})}
            user = dict(zip([desc[0] for desc in cur.description], user_row))
            if not verify_password(password, user['password_hash']):
                return {"statusCode": 401, "body": json.dumps({"error": "Invalid email or password"})}
            # Remove password_hash before returning
            user.pop('password_hash', None)
            return {"statusCode": 200, "body": json.dumps({"user": user})}
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
