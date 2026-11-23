# Capturing real requests and responses

A contract quotes what the code really returns. Capture against a local or sandbox copy of the service, never
production, and never against data that belongs to someone else.

## Before the first call

| Check | Why |
|---|---|
| The target is a local / sandbox instance, and its database is a copy | Captures must not change real data |
| Every tenant / org / account you send routes to that copy | Multi-tenant services may route some tenants to real databases |
| You know where the token comes from (a file, a mint script) | You read it inside the command; you never print it |
| You know whether background jobs are queued before or after the transaction commits | Decides whether a rolled-back write queues nothing |

## Read-only calls: call them live

```sh
curl -s "http://127.0.0.1:<port>/<path>?<query>" \
  -H "Authorization: <scheme> $(cat <token-file>)" \
  -H "<tenant header>: <sandbox tenant>" | python3 -m json.tool
```

- Pass the token with `$(cat …)`; never `echo` it, never paste it into the document or the chat.
- Capture the error cases too: missing parameter, wrong type, unknown id, missing permission (a user without the
  permission, or a feature turned off).
- A read that reaches a third-party API with configured credentials is fine when that is what the UI does; note
  it in **Backend processing**.

## Writes: run them in a rolled-back transaction

Do not send writes to the running server. Call the view in-process inside a transaction and roll it back, so the
response is real and nothing is saved. Django example (adapt for other frameworks):

```python
from django.db import transaction
from rest_framework.test import APIClient

client = APIClient()
client.credentials(HTTP_AUTHORIZATION="<scheme> " + open("<token-file>").read().strip())
with transaction.atomic():
    response = client.put("/<path>", {"<field>": "<value>"}, format="json", HTTP_X_TENANT="<sandbox tenant>")
    print(response.status_code, response.json())
    transaction.set_rollback(True)
```

- Confirm in code that background jobs are dispatched on commit (`transaction.on_commit`, an outbox table, …);
  then a rollback queues nothing. If a write calls an external system synchronously, do not capture it: document
  it from code and mark it `(from code, not captured)`.
- Search indexes, caches and files written outside the database are not rolled back. Skip those writes or point
  them at disposable local services.
- Say in the **Verification** paragraph which calls were rolled back.

## Redaction

| Replace with `"…"` | Keep |
|---|---|
| Tokens, API keys, client secrets, passwords, connection strings | Ids (UUIDs, numeric ids) |
| Stack traces | Names of templates, fields, enums, states |
| Personal emails, phone numbers, real people's names | Status codes and verbatim error messages |

Run `scripts/check.py` before delivering: it flags strings that look like JWTs, bearer tokens or `secret` /
`password` / `api_key` values.
