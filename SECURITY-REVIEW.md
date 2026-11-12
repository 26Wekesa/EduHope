# Security Review

**Date:** 2026-11-12

**Scope:** Read-only review of the Django application, routes, upload handling, and deployment configuration.

## Summary

One high-severity access-control finding was identified. No SQL injection vulnerability or unsafe raw SQL construction was found. Upload validation has hardening gaps, but the review did not identify a confirmed unauthenticated exploit path for them in the configured production serving paths.

This was a source-code review, not a penetration test or a verification of every deployed environment.

## Confirmed finding

| # | Severity | File | Lines | Vulnerability | Confidence |
|---|----------|------|-------|---------------|------------|
| 1 | 🟠 HIGH | [eduapp/views.py](eduapp/views.py), [eduapp/urls.py](eduapp/urls.py) | `views.py:579-632`; `urls.py:41-48` | Public approval and deletion handlers lack staff authorization and POST-only restrictions | 10/10 |

### Risk

An unauthenticated remote caller who can identify or guess a record ID can reach approval or deletion actions for mentors, learners, partners, or messages. Since the actions are not restricted to POST, they can be triggered by GET requests and do not receive the intended CSRF protection for state-changing requests.

### Recommended remediation

- Require an authenticated staff user in each state-changing handler.
- Require POST for approval and deletion actions.
- Submit those actions through CSRF-protected forms.

## Upload hardening notes

These are recommendations, not confirmed exploitable findings from this review:

- Recap photos use Django image-field form validation, which checks that the content is a valid image.
- Event reports are checked by filename extension; this does not prove that the file content matches the extension. See [eduapp/forms.py](eduapp/forms.py:23).
- Event image uploads are assigned without the same form-level validation. See [eduapp/views.py](eduapp/views.py:675).
- No upload size limit was found.
- The review found recap uploads restricted to staff and Django's development media serving enabled only when `DEBUG` is true. Production media serving depends on the configured storage/deployment.

Consider enforcing size limits, validating report content in addition to its extension, and validating/re-encoding images before storage. Keep uploaded documents out of executable paths and use production storage/serving configured for untrusted files.

## Other checks

- **SQL injection:** No raw SQL or unsafe ORM query construction was found.
- **Compose credentials:** Weak fallback credentials are documented for local development; they must not be used in a public deployment.

## Remediation status

This report documents findings only. No security code fixes were included in the report-only follow-up.
