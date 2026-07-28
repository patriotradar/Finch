# Aegis release guide

This file intentionally contains no one-click production instructions. Aegis
handles customer accounts, authorised asset scope, outreach and payments, so the
controlled release process is mandatory.

## Branch and rollback

- Development: `upgrade/aegis-production`
- Production: `main`
- Record the current production deployment URL and Vercel deployment ID before
  changing production.
- Keep that deployment available for immediate rollback.

## Release order

1. Verify a private Vercel preview.
2. Configure PostgreSQL, migrations and tested backups.
3. Test owner login, customer isolation, monitoring, reports, SMTP, suppression
   and PayPal Sandbox.
4. Complete accessibility, mobile and security checks.
5. Confirm privacy contact details, legal wording, cancellation and refund terms.
6. Obtain explicit owner approval.
7. Save the approved version, deploy production and run smoke tests.

Never claim exploitation, a guaranteed security outcome or guaranteed sales.
Monitoring remains passive, public-information-only and limited to assets approved
by the customer.

The detailed environment, migration, backup, preview, smoke-test and rollback
instructions are in `docs/PRODUCTION_RUNBOOK.md`.
