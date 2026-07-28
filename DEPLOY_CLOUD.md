# Aegis Vercel deployment

Do not connect this branch to production. Create a private Vercel preview from
`upgrade/aegis-production` and keep the production project on `main`.

Before preview testing:

1. Create production-grade PostgreSQL and set `DATABASE_URL`.
2. Run `alembic upgrade head` against that database.
3. Add all protected variables listed in `.env.example`.
4. Use PayPal Sandbox credentials and `PAYPAL_ENVIRONMENT=sandbox`.
5. Preserve the tested `FINCH_*` SMTP values.
6. complete both owner and customer journeys in the private preview.

Switch PayPal to live credentials and deploy production only after the owner has
approved the preview, legal wording, payment provider and email provider. See
`docs/PRODUCTION_RUNBOOK.md` for the full sequence and rollback procedure.
