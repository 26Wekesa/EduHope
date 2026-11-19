# Security Review

**Date:** 2026-11-12

**Scope:** Read-only review of the Django application, routes, upload handling, and deployment configuration.

## Summary

The review identified one high-severity access-control issue and upload-validation gaps. The original access-control issue was confirmed with an isolated Django client request against an in-memory database. Remediation is now implemented locally and covered by tests. No SQL injection vulnerability or unsafe raw SQL construction was found.

This was a source-code review, not a penetration test or a verification of every deployed environment.

## Confirmed finding

| # | Severity | File | Lines | Vulnerability | Confidence |
|---|----------|------|-------|---------------|------------|
| 1 | 🟠 HIGH | [eduapp/views.py](eduapp/views.py), [eduapp/urls.py](eduapp/urls.py) | `views.py:579-632`; `urls.py:41-48` | Public approval and deletion handlers lack staff authorization and POST-only restrictions | 10/10 |

### Risk

An unauthenticated remote caller who can identify or guess a record ID can reach approval or deletion actions for mentors, learners, partners, or messages. Since the actions are not restricted to POST, they can be triggered by GET requests and do not receive the intended CSRF protection for state-changing requests.

### Remediation applied

- All affected admin actions now require an authenticated staff user and POST.
- Dashboard action controls now use CSRF-protected POST forms.
- Event deletion received the same protections.
- Tests verify anonymous and non-staff denial, GET non-mutation, successful staff POST, and CSRF enforcement.

## Upload hardening notes

Upload validation has also been tightened:

- Recap photos and event images are decoded/validated as images before saving.
- Reports must pass format-specific checks in addition to the extension check: PDF header/xref/EOF structure, legacy DOC compound-file signature, or DOCX ZIP/XML structure.
- DOCX ZIP entry count, total uncompressed size, XML size, and per-entry compression ratio are bounded.
- Event images, recap photos, and reports are limited to 5 MB per file.
- Tests cover malformed image/report payloads, oversized files, and oversized DOCX XML.

These checks are file-format validation, not antivirus or malware scanning. Production storage and media-serving behavior still depends on the deployment configuration.

## Other checks

- **SQL injection:** No raw SQL or unsafe ORM query construction was found.
- **Compose credentials:** Weak fallback credentials are documented for local development; they must not be used in a public deployment.

## Remediation status

The fixes are implemented and covered by the test suite. All 40 tests pass locally, and Django system and migration checks pass. Verify the hosted environment after deployment to confirm it is running this revision.
