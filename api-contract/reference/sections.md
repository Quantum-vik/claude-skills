# Sections

The order and numbering are fixed (1–11). Subsection numbers in section 3 (`4.1`, `5.2`, …) must match the
headings in sections 4 and 5; `scripts/check.py` enforces it.

| § | Section | Must contain | Leave out |
|---|---|---|---|
| — | Header | Ticket, branch, PR, date, where responses were captured, a 2–4 sentence description of the feature, related documents | Marketing prose, history of the project |
| 1 | Conventions | 1.1 base path, headers, auth failures with real bodies; 1.2 the error envelope(s) actually returned, with status per kind and whether a failed request rolls back; 1.3 the feature's own errors with verbatim messages | Conventions the service does not follow (say so in section 10 instead) |
| 2 | Shared data shapes | Every object used by more than one endpoint: a JSON example, a Key / Type / Required / Rules table, `Sources:` | Shapes used once (describe them in their endpoint) |
| 3 | Endpoint summary | One row per endpoint, existing dependency and event group; `#` points at its subsection | Endpoints the feature does not touch |
| 4 | Feature endpoints | Per endpoint, in this order: **Purpose**, params table, permission, **Request**, **Response 200**, response field table, **Errors**, **Backend processing**, **Frontend**, **Sources** | Fields nobody reads; speculative behaviour |
| 5 | Existing endpoints relied on | Only the keys the feature adds or reads, their rules, responses and permission | The rest of the existing payload |
| 6 | Background jobs and events | Job table (task, queued by, queue, does), event table (event, payload, when), how the frontend waits | Jobs unrelated to the feature |
| 7 | End-to-end flows | One table per user flow: trigger, call + payload, backend, response, next | UI pixel details (link the UI doc) |
| 8 | Edge cases | Case / Behaviour / Source, one row each | Cases you did not confirm in code |
| 9 | FE / BE mismatches | `M1…`: mismatch, effect, status (Open / Resolved / FE obligation) | Pure backend bugs with no FE effect (section 10) |
| 10 | Unresolved questions | `Q1…`: decisions to make, security and authorization first, each with its evidence | Proposed business logic |
| 11 | Summary | Counts of endpoints, M and Q, and the **Verification** paragraph | New facts |

## Rules for tables

- `thead` row on every table; one fact per row.
- A table about the code has a `Source` column, or a `Sources:` line right under it, naming files and functions
  (`file:line` when one line matters).
- Error rows quote the message verbatim and give the status the client really receives (for example a "not found"
  that the service answers with 400 is written as 400).
- Types: `string (UUID)`, `integer`, `boolean`, `string or null`, `array of <shape>`, enums as `` `a` / `b` ``.
- Say for every response field whether it can be `null` or is omitted, when the code does either.

## Rules for bodies

- Captured, not imagined. Mark the source of each block: `(captured)`, `(captured, trimmed)`,
  `(rolled back)`, or `(from code, not captured)`.
- Trim long lists with `"…"` and keep at least one complete element.
- Stack traces, tokens, passwords, keys, connection strings and personal emails become `"…"`.
- Use realistic ids and names from the capture, not `foo` / `bar`.

## Writing the Backend processing line

Follow the real call chain with arrows: `View.method` → `Serializer` → orchestrator / service → data access.
Then the side effects in order: database writes, background jobs queued (and whether they are queued only after
commit), events sent, external calls, caches. End with "Read-only, no writes." when that is true.

## Writing the Frontend line

Which screen and action call it; which response fields drive loading, empty, error and partial states; which
errors are shown and which are handled silently; what it caches or invalidates; or "Not used by the frontend."
Say what the frontend does today, not what it should do (that goes to section 9).
