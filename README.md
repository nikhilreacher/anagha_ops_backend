# anagha_ops_backend

FastAPI backend for Anagha Ops, using SQLAlchemy models and startup migrations.

## Requirements

- Python 3.x
- `pip`

## Install

```bash
pip install -r requirements.txt
```

## Database configuration

Set `DATABASE_URL` before starting the app.

Example:

```bash
set DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@db.ncqspuxvxkqvuhbdazba.supabase.co:5432/postgres
```

If `DATABASE_URL` is not set, the app falls back to the Supabase connection string placeholder in `database.py`.

## SMS configuration for new credit alerts

To send an SMS when a new credit entry is added from Dispatch using your company phone SMS gateway app, configure these environment variables:

```bash
set SMS_GATEWAY_URL=http://YOUR_PHONE_OR_GATEWAY_IP:PORT/send
set SMS_GATEWAY_API_KEY=optional_api_key
set SMS_GATEWAY_DEVICE_ID=optional_device_id
```

Notes:

- The backend sends a `POST` request with JSON like:
```json
{
  "phone": "+919876543210",
  "message": "Anagha Ops: Credit added..."
}
```
- If provided, `api_key` and `device_id` are also included in the JSON payload.
- The shop phone number should be stored in the database in a valid format. The backend also normalizes 10-digit Indian mobile numbers to `+91`.
- If SMS is not configured, credit creation still works and the app will simply skip sending the message.

## Run locally

```bash
uvicorn main:app --reload
```

The API starts on `http://127.0.0.1:8000/` by default.

## Importing Mainline or ICD data

Use `import_shops.py` to load separate shop masters and ledger data for each business.

Example ICD import:

```bash
python import_shops.py --file "C:\path\to\icd-data.xlsx" --business-type icd --mode both --shops-sheet Shops --ledger-sheet Sheet1 --create-missing-shops
```

Example mainline ledger-only import:

```bash
python import_shops.py --file "C:\path\to\mainline-ledger.xlsx" --business-type mainline --mode ledger --ledger-sheet Sheet1
```

Notes:

- Supported `--business-type` values are `mainline` and `icd`.
- `--mode shops` imports only shop master rows.
- `--mode ledger` imports only ledger rows.
- `--mode both` runs both imports in one pass.
- When `--create-missing-shops` is provided, ledger rows can create shop master entries automatically for that business bucket.

## Available route groups

- `/shops`
- `/dispatch`
- `/payments`
- `/admin`
- `/routes`
- `/auth`
- `/dashboard`

## Notes

- The app creates and updates the database schema on startup.
- Local export artifacts are intentionally not tracked in git:
  - `datastore/`
  - `export_datastore_csvs.py`
