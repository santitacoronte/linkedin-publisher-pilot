# LinkedIn Publisher — Pilot

A minimal end-to-end demo: click a link → preview a pre-approved LinkedIn post → publish it with one button.

## How it works

```
/          →  landing page with a demo link
/start     →  validates signed token, starts LinkedIn OAuth
/callback  →  exchanges code for access token, fetches profile
/preview   →  shows personalized post preview
/publish   →  POSTs to LinkedIn Posts API
/success   →  confirmation screen
```

## Quick start

### 1. LinkedIn app setup

1. Go to <https://www.linkedin.com/developers/apps> → **Create app**
2. Under **Auth**, add `http://localhost:8000/callback` as an OAuth 2.0 redirect URL
3. Under **Products**, request access to **Share on LinkedIn** (gives `w_member_social`)
4. Copy your **Client ID** and **Client Secret**

### 2. Install & configure

```bash
cd linkedin-publisher-pilot
pip install -r requirements.txt
cp .env.example .env
# Edit .env and fill in LI_CLIENT_ID, LI_CLIENT_SECRET, APP_SECRET
```

### 3. Run

```bash
uvicorn app:app --reload
```

Open <http://localhost:8000> — click the demo link to walk through the full flow.

## Customising the sample post

Edit `SAMPLE_POST` and `SAMPLE_CAMPAIGN` near the top of `app.py`.  
Template variables: `{first_name}`, `{last_name}`, `{campaign_name}`, `{company_name}`, `{post_body}`.

## Notes

- State is stored **in memory** — it resets on every server restart. This is intentional for the pilot.
- The entry token is HMAC-signed with `APP_SECRET` to prevent tampering.
- For production: replace the in-memory `_store` with Redis/a database, and generate entry links per member.
