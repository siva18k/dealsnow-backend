# CORS Fix: products_management from dealsnowdaily.com

## The error

```
Access to fetch at 'https://lfz87q0k7f.execute-api.us-east-2.amazonaws.com/initial/products_management?...'
from origin 'https://dealsnowdaily.com' has been blocked by CORS policy:
No 'Access-Control-Allow-Origin' header is present on the requested resource.
```

**What it means:** The browser blocks the response because the API did not send a CORS header that allows `https://dealsnowdaily.com` to read it. Browsers enforce this for cross-origin requests.

---

## Why it happens

1. **OPTIONS preflight** – For cross-origin requests, the browser first sends an **OPTIONS** request. If the API does not respond to OPTIONS with CORS headers, the browser blocks the actual GET/POST and you see “No 'Access-Control-Allow-Origin' header”.
2. **Error responses** – If the Lambda fails (timeout, crash, 5xx) or API Gateway returns 4xx/5xx, the response might be a **Gateway Response**. Those often don’t include CORS headers unless you configure them.
3. **Wrong API/stage** – The resource might be on an API or stage that was never configured for CORS.

---

## Fix in API Gateway (recommended)

Do this in the **API Gateway** console for the API that has ID `lfz87q0k7f` (stage `initial`).

### 1. Enable CORS on the resource

1. Open **API Gateway** → select the API (e.g. “DealsNow Main API” or the one with ID `lfz87q0k7f`).
2. Go to **Resources** → select `/products_management`.
3. **Actions** → **Enable CORS**.
4. Set:
   - **Access-Control-Allow-Origin:** `https://dealsnowdaily.com` (or `*` for any origin)
   - **Access-Control-Allow-Headers:** `Content-Type,Authorization,X-Api-Key,X-Country-Code`
   - **Access-Control-Allow-Methods:** `GET,POST,OPTIONS`
5. Confirm so that API Gateway creates/updates the **OPTIONS** method and adds the headers to the method responses.
6. **Deploy API** to the `initial` stage.

### 2. Add CORS to Gateway Responses (for 4xx/5xx)

So that **error** responses (4xx/5xx) also send CORS headers:

1. In the same API, go to **Gateway Responses** (left sidebar).
2. Open **DEFAULT_4XX**:
   - Add header: `Access-Control-Allow-Origin` = `https://dealsnowdaily.com` (or `*`).
   - Save.
3. Open **DEFAULT_5XX**:
   - Add the same header.
   - Save.
4. **Deploy API** again to the `initial` stage.

After this, both success and error responses from this API (including `products_management`) will include the CORS header.

---

## Verify

1. **OPTIONS**  
   From a terminal:
   ```bash
   curl -i -X OPTIONS "https://lfz87q0k7f.execute-api.us-east-2.amazonaws.com/initial/products_management" \
     -H "Origin: https://dealsnowdaily.com" \
     -H "Access-Control-Request-Method: GET"
   ```
   Response should include:  
   `Access-Control-Allow-Origin: https://dealsnowdaily.com` (or `*`).

2. **Browser**  
   Reload https://dealsnowdaily.com and trigger the request again; the CORS error should be gone (or replaced by a non-CORS error if something else fails).

---

## Lambda (already correct)

`dealsnow-backend/lambda-functions/product_management.py` already returns CORS on every path:

```python
'headers': {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*'
}
```

So the problem is **API Gateway** (OPTIONS and/or Gateway Responses), not the Lambda response format. Applying the steps above should resolve the CORS error for `products_management` from `dealsnowdaily.com`.
