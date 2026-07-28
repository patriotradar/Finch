# Aegis production runbook

## Deployment boundary

- Work and previews use `upgrade/aegis-production`.
- Vercel production remains connected to `main`.
- Rollback baseline: `5163e24706c51d5653c447ba72a6aa53b96a9bb5`.
- Do not merge or deploy until Jamie approves the reviewed preview.

## Required services

Use managed PostgreSQL with point-in-time recovery, a PayPal REST application,
the existing SMTP account, and an HTTPS public URL. Copy `.env.example` into
encrypted provider settings; never commit real values.

## Database migration

```bash
alembic upgrade head
alembic current
```

The expected head is `20260728_06`. Run this against preview first.

## Backup and recovery

Enable daily managed backups and point-in-time recovery. Before launch:

1. Create a provider backup.
2. Restore into a separate recovery database, never over production.
3. Run `alembic current` against the recovery database.
4. Compare workspace, asset, report, suppression and owner-history counts.
5. Sign in to the recovery preview and download a report.
6. Record the recovery time and reviewer.

Do not claim backup verification until a real provider restore passes.

## PayPal

Use sandbox first. Aegis creates an Order for exactly GBP 995.00. It activates
the licence only after a server-side capture returns `COMPLETED` for the Order
and capture with the exact amount and currency.

Required values are `PAYPAL_ENVIRONMENT`, `PAYPAL_CLIENT_ID` and
`PAYPAL_CLIENT_SECRET`. Test successful payment, cancellation, replay,
incorrect-amount rejection and the PayPal receipt before live checkout.

## Email and outreach

- Preserve `FINCH_EMAIL` and `FINCH_EMAIL_PASSWORD`.
- Configure a minimum 24-character `FINCH_OPTOUT_SECRET`.
- Start with no more than 20 eligible corporate prospects daily.
- Every lead requires a public security-interest source and sourced address.
- Replies, opt-outs, hard bounces and global pause stop contact.
- A hard-bounce rate above 3% activates global pause.

## Launch sequence

1. Run the complete automated suite.
2. Review the private preview on mobile and desktop.
3. Complete owner and customer journeys.
4. Confirm legal wording and privacy contact.
5. Confirm PayPal live credentials and SMTP.
6. Complete a real database restore test.
7. Obtain Jamie’s explicit production approval.
8. Merge the reviewed commit to `main`.
9. Deploy and run immediate smoke tests.
10. Keep the rollback commit and prior Vercel deployment available.
