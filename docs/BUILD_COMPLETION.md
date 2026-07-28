# Aegis build completion record

Date: 29 July 2026  
Branch: `upgrade/aegis-production`  
Production branch: `main` (unchanged)

## Completed in the repository

- Secure owner authentication, expiry, logout, throttling, CSRF protection,
  protected private HTTP/WebSocket access, activity history and Pause Harold.
- Durable customer accounts, email verification, password reset, isolated
  workspaces, five-user limit, 25-asset limit, authority records, cancellation
  and data export.
- PostgreSQL-ready SQLAlchemy models and Alembic migrations through
  `20260728_06`; SQLite remains limited to development and tests.
- Customer-controlled monitoring scope and passive public-information collection
  with conservative limits, stop conditions and request auditing.
- Evidence-led observations, cautious advisory matching, change history, alerts,
  web reports, PDFs and report downloads.
- Private Harold history and daily briefing; browser speech input/output fallback;
  customer Harold remains text-only and does not inspect public-chat domains.
- Fixed £995 / 12-month licence, PayPal Orders checkout, exact server-side capture
  verification, licence activation, invoices and payment event history.
- Sourced corporate leads, eligibility checks, deduplication, permanent
  suppression, one-click opt-out, daily limit, bounce pause and global pause.
- Aegis identity, original brand assets, landing page, legal review copies,
  customer workspace and owner dashboard.
- Professional presentation, faceless seven-minute video script, five- and
  15-minute sales scripts, product sheet, proposal and example monthly report.
- Generic public errors, security headers, no production trace disclosure, no
  fake client data, no simulated public scan and no autonomous target discovery.

## Verification completed

- 65 automated tests pass.
- Python compilation passes.
- Dependency consistency check passes.
- A blank database migrates successfully through Alembic head `20260728_06`.
- Public pages, legal pages, owner login, owner session, owner dashboard and
  customer/public route protections pass local smoke checks.
- Anonymous owner and customer private routes return `401`.
- All 11 presentation slides render without overflow and were visually inspected.
- All six customer-facing PDFs render successfully and were visually inspected.
- Git diff whitespace validation passes.

## External launch gates

These cannot be truthfully completed in source code:

1. Record the current live Vercel deployment ID and test rollback.
2. Provision managed production PostgreSQL.
3. Run migrations against preview PostgreSQL.
4. Enable managed backups and complete a real restore test.
5. Add protected owner, session, opt-out, SMTP and public-URL values.
6. Add PayPal Sandbox credentials and complete a real sandbox purchase.
7. Confirm PayPal business receipt details and then add live credentials.
8. Test the preserved SMTP mailbox and reply flow from private preview.
9. Insert final privacy contact details and obtain appropriate legal review.
10. Complete owner and customer journeys on mobile and desktop private preview.
11. Obtain explicit owner approval before merging or deploying production.

Until every external gate passes, Aegis is build-complete but not production
launch-approved.
