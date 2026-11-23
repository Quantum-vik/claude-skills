#!/usr/bin/env python3
"""Lint an API contract written from assets/template.md.

usage: check.py <contract.md>
Exits 1 when a required section is missing, a section-3 row has no subsection, an endpoint subsection lacks a
required part, a template placeholder is left, or something looks like a secret. Prints warnings otherwise.
"""
import re
import sys

SECTIONS = {
    1: "Conventions", 2: "Shared data shapes", 3: "Endpoint summary", 5: "Existing endpoints",
    6: "Background", 7: "End-to-end flows", 8: "Edge cases", 9: "mismatches", 10: "Unresolved questions",
    11: "Summary",
}  # section 4's title is the feature's own; only its number is required
ENDPOINT_PARTS = ("**Purpose:**", "**Request", "**Response", "**Errors**", "**Backend processing:**",
                  "**Frontend:**", "Sources:")
PLACEHOLDER = re.compile(r"\[[^\]\n\"']{2,80}\](?![(:])")  # [text] that is not a link; code is stripped first
SECRETS = (
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "JWT-looking string"),
    (re.compile(r"(?i)\b(?:bearer|jwt|token)\s+[A-Za-z0-9._-]{24,}"), "token after an auth scheme"),
    (re.compile(r"(?i)\"?(?:secret|password|passwd|api[_-]?key|client_secret)\"?\s*[:=]\s*\"(?!…)[^\"\s]{6,}\""),
     "secret / password / api key value"),
)


def main(path):
    text = open(path, encoding="utf-8").read()
    errors, warnings = [], []

    headings = {int(m.group(1)): m.group(2) for m in re.finditer(r"^## (\d+)\. (.+)$", text, re.M)}
    if 4 not in headings:
        errors.append("missing section '## 4. …' (feature endpoints)")
    for number, word in SECTIONS.items():
        if number not in headings or word.lower() not in headings[number].lower():
            errors.append(f"missing section '## {number}. …{word}…'")

    subsections = {m.group(1): m.start() for m in re.finditer(r"^### ([45]\.\d+)\b", text, re.M)}
    summary = re.search(r"^## 3\..*?(?=^## 4\.)", text, re.M | re.S)
    for ref in re.findall(r"^\|\s*([45]\.\d+)\s*\|", summary.group(0) if summary else "", re.M):
        if ref not in subsections:
            errors.append(f"section 3 lists {ref} but there is no '### {ref}' heading")

    starts = sorted(subsections.items(), key=lambda item: item[1])
    for index, (ref, start) in enumerate(starts):
        if not ref.startswith("4."):
            continue
        end = starts[index + 1][1] if index + 1 < len(starts) else text.find("\n## 5.", start)
        body = text[start:end if end > 0 else len(text)]
        missing = [part for part in ENDPOINT_PARTS if part not in body]
        if missing:
            errors.append(f"### {ref} is missing: {', '.join(missing)}")
        if "captured" not in body and "from code" not in body:
            warnings.append(f"### {ref} does not say whether its response was captured or read from code")

    in_code = False
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
        prose = "" if in_code else re.sub(r"`[^`]*`", "", line)
        for match in PLACEHOLDER.finditer(prose):
            errors.append(f"line {line_no}: placeholder left: {match.group(0)}")
        for pattern, label in SECRETS:
            if pattern.search(line):
                errors.append(f"line {line_no}: looks like a {label}; redact it")

    if not re.search(r"\*\*Verification:\*\*", text):
        errors.append("section 11 has no **Verification:** paragraph")
    if len(re.findall(r"^\| M\d+ \|", text, re.M)) == 0:
        warnings.append("no mismatches (M1…) listed in section 9; write 'None found' if that is true")

    for message in warnings:
        print(f"warning: {message}")
    for message in errors:
        print(f"error: {message}")
    print(f"{path}: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
