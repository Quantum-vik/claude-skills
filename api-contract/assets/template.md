# [FEATURE NAME]: API contract

Ticket **[TICKET]**. Branch `[BRANCH]`, PR [PR]. This contract describes the code on that branch as of
[YYYY-MM-DD]. Every response below was produced by the code: success and error bodies were captured from
[SANDBOX / LOCAL COPY] (writes rolled back), and the rest was read from the source files named in each section.

[TWO TO FOUR SENTENCES: what the feature does, the main objects it works on, and what happens in the background.]

Related documents (not repeated here): [LINKS TO DESIGN DOCS, FLOW DOCS, ARCHITECTURE NOTES].

---

## 1. Conventions

### 1.1 Base path, headers, authentication

| Item | Value | Source |
|---|---|---|
| Base path | `[/api/…]` | `[urls file]` |
| `Authorization` | `[scheme] <token>` | `[auth class / middleware]` |
| `[TENANT OR ORG HEADER]` | [meaning, or remove the row] | `[middleware]` |
| `Content-Type` | `application/json` for POST / PUT | — |

| Failure | Status | Body | Source |
|---|---|---|---|
| Invalid or expired token | [401] | `[captured body]` | `[file]` |
| Missing permission | [403] | `[captured body]` | `[file]` |

[One line: how permission levels are decided per method, and which views override it.]

### 1.2 Error envelope

| Error kind | Status | Body | Source |
|---|---|---|---|
| Request fails validation | [400] | `[shape]` | `[function]` |
| A domain error | [status] | `[shape]` | `[handler]` |
| Unexpected exception | 500 | `[shape]` | `[handler]` |

[One line on transactions: does a failed request roll back everything it wrote?]

### 1.3 [FEATURE] errors

| Error | Default `msg` | Raised when |
|---|---|---|
| `[ErrorClass or code]` | [verbatim message] | [condition] |

---

## 2. Shared data shapes

### 2.1 [SHAPE NAME]

Used by [endpoints].

```json
{
  "[field]": "[example]"
}
```

| Key | Type | Required | Rules |
|---|---|---|---|
| `[field]` | [type] | [yes / no, default …] | [validation, enum, limits] |

Sources: `[files and functions]`.

---

## 3. Endpoint summary

| # | Method and URL | Purpose | New / existing | Permission |
|---|---|---|---|---|
| 4.1 | `[METHOD /path]` | [purpose] | **New** | [permission] |
| 5.1 | `[METHOD /path]` | [purpose] | Existing; [what changed] | [permission] |
| 6 | [Events / jobs] | [purpose] | [new / existing] | — |

---

## 4. [New / feature] endpoints

### 4.1 `[METHOD /path]`

**Purpose:** [one or two sentences: what the screen gets or does with it].

| Param | In | Type | Required | Meaning |
|---|---|---|---|---|
| `[name]` | [path / query / body] | [type] | [yes / no, default] | [meaning and validation] |

Permission: [permission].

**Request**

```http
[METHOD] [/path?query]
Authorization: [scheme] <token>
```

**Response 200** ([captured / trimmed / from code, not captured])

```json
{}
```

| Response field | Type | Meaning |
|---|---|---|
| `[field]` | [type] | [meaning; null vs omitted] |

**Errors**

| Case | Status | Body (captured) |
|---|---|---|
| [case] | [status] | `[body]` |

**Backend processing:** `[View.method]` → `[Serializer]` → `[service / bll function]` → … [side effects: DB
writes, background jobs, events, external calls; or "Read-only, no writes."]

**Frontend:** [which screen calls it, which fields drive loading / empty / error states, what it caches; or
"Not used by the frontend."]

Sources: `[files and functions]`.

---

## 5. Existing endpoints the feature depends on

Only the parts this feature adds or relies on are described; the rest of each payload is unchanged.

### 5.1 `[METHOD /path]`: [what the feature uses it for]

| Field | Value for this feature | Notes |
|---|---|---|
| `[field]` | [value] | [notes] |

Responses: [statuses and bodies]. Permission: [permission]. Source: `[file]`.

---

## 6. Background jobs and events

| Job / task | Queued by | Queue / priority | Does |
|---|---|---|---|
| `[task]` | [endpoint or trigger] | [queue] | [what it does] |

| Event | Payload | When |
|---|---|---|
| `[event]` | `[payload]` | [when] |

[How the frontend waits for a job: by id, by event, by polling.]

---

## 7. End-to-end flows

### 7.1 Flow 1: [name]

| # | Trigger (FE) | Call and payload | Backend | Response | Next |
|---|---|---|---|---|---|
| 1 | [user action] | [section ref + key payload] | [what happens] | [status] | [next step] |

Outcome: [one line].

---

## 8. Edge cases

| Case | Behaviour | Source |
|---|---|---|
| [case] | [behaviour] | `[function]` |

---

## 9. Frontend / backend contract mismatches

| # | Mismatch | Effect | Status |
|---|---|---|---|
| M1 | [mismatch] | [effect] | [Open / Resolved / FE obligation] |

---

## 10. Unresolved questions

| # | Question |
|---|---|
| Q1 | [decision to make, with the evidence] |

---

## 11. Summary

**Endpoints documented:** [n new, n existing, events].

**Contract mismatches:** [n] (section 9). [One line on which are open.]

**Unresolved questions:** [n] (section 10), mainly [themes].

**Verification:** [which requests and responses were captured live, which inside a rolled-back transaction, which
were read from code; date and environment].
