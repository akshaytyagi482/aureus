# Deploying AureusRecon on Render + PostgreSQL

About 20 minutes. You need a GitHub login and a Render account. `render.yaml` creates the web app, the daily job and a managed PostgreSQL database in one step.

## Why Render and not Vercel

- **Upload size:** Vercel runs Python as serverless functions with a 4.5 MB request-body limit. A year of Tally Day Book XML is easily bigger, so imports would fail.
- **Long requests:** reconciliation and Zoho syncs can run past serverless time limits.
- **Simplicity:** Render runs the app as a normal always-on server, which is how it was built, and `render.yaml` in this repo sets it up in one step.

## 1. Database: PostgreSQL

**Default.** The blueprint creates a Render PostgreSQL 16 database (`aureusrecon-db`) in Singapore, Render's closest region to India. Both the web app and the daily job get its connection string automatically, and only services in your Render account can reach it.

**Keeping data inside India.** Render has no India region. If a client requires data in India, use a Postgres hosted in Mumbai instead:
- **AWS RDS:** `ap-south-1`.
- **Supabase:** South Asia (Mumbai) region.

To switch, delete the `databases:` block in `render.yaml` and set `DATABASE_URL` yourself on both services. Any standard `postgresql://user:pass@host:5432/db` string works; add `?sslmode=require` for hosted databases.

**Schema.** The app creates and upgrades its own tables on start-up (numbered migrations in `aureus/db.py`), so there is nothing to run by hand.

## 2. App: Render

1. **Create the service.** At [dashboard.render.com](https://dashboard.render.com), choose *New → Blueprint*, connect GitHub and pick the `aureus` repo and branch.
2. **Fill in the secrets** Render asks for:
   - `AUREUS_MASTER_KEY`: generate one with
     `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`.
     **Store a copy in a password manager.** If it is lost, every encrypted record becomes unreadable.
   - `APP_BASE_URL` and the `SMTP_*` / `MAIL_FROM` email settings (section 3).
   - `ANTHROPIC_API_KEY` (optional), and the `ZOHO_*` values (optional; leave blank for now).
3. **Deploy.** Render builds and starts the app, and checks `/api/health`. If the key or database is wrong, the deploy fails straight away and the log says why.
4. **Custom domain (optional).** Under *Settings → Custom domains*, Render provides HTTPS automatically. If you use Zoho, set `ZOHO_REDIRECT_URI` to `https://<domain>/api/integrations/zoho/callback`.

## 3. Email and the daily job

- **Email** is used for password resets and alerts. Any SMTP provider works (Resend, SendGrid, or Amazon SES in Mumbai). Fill `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` and `MAIL_FROM`, and set `APP_BASE_URL` to your public URL. In production, password reset refuses to run until email is configured.
- **The daily job** (`aureusrecon-daily`, 08:00 IST) does three things: re-syncs Zoho Books, emails a GSTR-3B deadline alert when credit is still at risk, and warns 14 days before a data consent expires. Each alert goes out at most once per business per period. Render cron jobs are billed separately, at a small monthly minimum.

## 4. First use

- **Demo data.** Don't run the demo seed in production. Instead, sign up and use **Try with demo data** inside a test organisation.
- **Real clients.** Give each client their own organisation. A CA signs up with the *Chartered Accountant* role and adds clients from the console.

## Costs (check current prices)

| Item | Pilot | Paying clients |
|---|---|---|
| Render web service | Free (sleeps when idle) or Starter | Starter / Standard |
| PostgreSQL (Render) | Smallest paid instance (free ones expire) | Larger instance, point-in-time recovery |
| Anthropic API | Only if Notice Copilot polish is on | Per use |
