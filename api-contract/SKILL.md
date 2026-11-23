---
name: api-contract
description: Write an API contract in a fixed house format (one Markdown file - conventions, shared data shapes, endpoint summary, one section per endpoint with real captured requests and responses, dependencies, background jobs and events, end-to-end flows, edge cases, FE/BE mismatches, unresolved questions, verification) from the code itself. Use when asked for an API contract, an API spec for the frontend team, endpoint documentation of a feature, or a document "in the API contract format".
---

# API contract

One feature → one Markdown file that a frontend and a backend team can build against without asking each other.
The format is fixed: the skeleton lives in `assets/template.md`. You write content from the code and from real
responses; you never invent fields, rules or business logic.

Kit (paths relative to this skill's folder, `~/.claude/skills/api-contract/`):

| File | Role |
|---|---|
| `assets/template.md` | Starting document: every section in order, with `[PLACEHOLDER]` text and example rows |
| `reference/sections.md` | What each section must contain, and the rules for its tables |
| `reference/capturing.md` | How to capture real requests and responses safely (read-only live, writes rolled back, redaction) |
| `scripts/check.py` | Lints the finished file: sections present, every summary row has its section, no placeholder left, no secret leaked |

Needs `python3` (3.9+). Capturing needs a running local or sandbox copy of the service, never production.

## Steps

1. **Scope the endpoints.** List every route of the feature from the URL config (method, path, view) and every
   existing endpoint, background job and event it depends on. Show the user the endpoint summary (section 3 of the
   template) and wait for confirmation before writing the rest. Done when the user has confirmed the list.
2. **Gather the facts from code.** For each endpoint read the view, the request serializer / validator, the
   service or business-logic functions it calls, the error classes it can raise, and the frontend code that calls
   it. Every row you write needs a source you opened; anything you could not confirm goes to section 10
   (Unresolved questions), not into the contract as a claim. For many endpoints, split the reading across
   parallel agents by endpoint group, each returning facts plus file references.
3. **Capture real responses** following `reference/capturing.md`: read-only calls against a local or sandbox copy;
   writes inside a transaction that is rolled back; every token read from a file and never printed; secrets in
   bodies redacted. Mark anything you could not capture "(from code, not captured)".
4. **Place the file.** Put it where the repo keeps design docs (for example `docs/<feature>/api-contract.md` or
   `LLD/<feature>/api-contract.md`); with no convention, beside the feature's other docs; ask when neither is
   obvious. Copy `assets/template.md` there.
5. **Write the content** section by section, following `reference/sections.md`. Done when every endpoint in
   section 3 has its own subsection in section 4 or 5, and every placeholder from the template is gone.
6. **Check:** `python3 ~/.claude/skills/api-contract/scripts/check.py <file>`. It exits non-zero on a missing
   section, a summary row without a subsection, an endpoint subsection missing a required part, a leftover
   placeholder, or something that looks like a token or secret. Fix and re-run until it passes.
7. **Deliver** the file path, the number of endpoints, mismatches (M) and unresolved questions (Q), and which
   responses were captured live, rolled back, or taken from code.

## House style

- Describe the code as it is today, including its warts. A behaviour that looks wrong is documented as it is and
  listed in section 9 or 10; the contract never describes the fix as if it existed.
- Tables over prose for anything listed, compared or mapped. Every table that states a fact about the code has a
  `Source` column or a `Sources:` line under it with file paths and function names.
- Captured bodies are real. Trim long ones with `…` and say "(trimmed)". Show stack traces as `"…"`.
- Exact values: status codes, error messages verbatim, field names as `code`, defaults, enums, limits with units.
- Name the request and response fields the frontend needs; never pad a section with fields nobody reads.
- Mismatches (section 9) are FE ↔ BE gaps with their effect; unresolved questions (section 10) are decisions to
  make, security and authorization gaps first. Neither proposes business logic the user has not described.
