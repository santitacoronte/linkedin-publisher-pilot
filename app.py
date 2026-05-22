"""
LinkedIn Publisher Pilot
A minimal demo app: click a link → preview a LinkedIn post → publish it.
"""

import os
import uuid
import hmac
import hashlib
import json
import urllib.parse
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ── Config ──────────────────────────────────────────────────────────────────
LI_CLIENT_ID     = os.getenv("LI_CLIENT_ID", "")
LI_CLIENT_SECRET = os.getenv("LI_CLIENT_SECRET", "")
LI_REDIRECT_URI  = os.getenv("LI_REDIRECT_URI", "http://localhost:8000/callback")
APP_SECRET       = os.getenv("APP_SECRET", "demo-secret-change-me")

LI_AUTH_URL      = "https://www.linkedin.com/oauth/v2/authorization"
LI_TOKEN_URL     = "https://www.linkedin.com/oauth/v2/accessToken"
LI_ME_URL         = "https://api.linkedin.com/v2/me"
LI_INTROSPECT_URL = "https://www.linkedin.com/oauth/v2/introspectToken"
LI_POSTS_URL      = "https://api.linkedin.com/rest/posts"

# ── In-memory state store (pilot only — not for production) ─────────────────
# key: state UUID  →  value: dict with campaign + tokens
_store: dict[str, dict] = {}

# ── Sample campaign (hardcoded for the demo) ─────────────────────────────────
SAMPLE_POST = """\
Excited to share that I just completed the {campaign_name} with {company_name}! 🚀

{post_body}

#LinkedIn #Community #Growth\
"""

SAMPLE_CAMPAIGN = {
    "id":            "demo-campaign-001",
    "campaign_name": "AI Innovation Sprint",
    "company_name":  "Acme Corp",
    "post_body": (
        "This program pushed me to think differently about how we use AI in day-to-day work. "
        "The sessions were packed with practical insights and the community we built is incredible. "
        "If you're curious about what's next in enterprise AI, let's connect!"
    ),
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sign_token(data: dict) -> str:
    payload = json.dumps(data, sort_keys=True)
    sig = hmac.new(APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    encoded = urllib.parse.quote(payload)
    return f"{encoded}.{sig}"


def _verify_token(token: str) -> dict:
    try:
        encoded, sig = token.rsplit(".", 1)
        payload = urllib.parse.unquote(encoded)
        expected = hmac.new(APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("bad signature")
        return json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or tampered token.")


def _interpolate(template: str, campaign: dict, member: dict) -> str:
    ctx = {**campaign, **member}
    for key, val in ctx.items():
        template = template.replace(f"{{{key}}}", str(val))
    return template


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    token = _sign_token({"campaign_id": SAMPLE_CAMPAIGN["id"], "member_id": "demo-member"})
    url   = f"/start?token={urllib.parse.quote(token)}"
    return HTMLResponse(f"""
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>LinkedIn Publisher — Demo</title>
<style>
  body{{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;
        min-height:100vh;margin:0;background:#f3f4f6}}
  .card{{background:#fff;border-radius:12px;padding:40px 48px;max-width:480px;
         text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.1)}}
  h1{{margin:0 0 8px;font-size:1.6rem;color:#0a66c2}}
  p{{color:#555;line-height:1.6;margin:0 0 28px}}
  a.btn{{display:inline-block;background:#0a66c2;color:#fff;border-radius:6px;
         padding:14px 28px;text-decoration:none;font-weight:600;font-size:1rem}}
  a.btn:hover{{background:#004182}}
</style>
</head>
<body>
<div class="card">
  <h1>LinkedIn Publisher</h1>
  <p>This demo lets you preview and publish a pre-approved LinkedIn post in one click.</p>
  <a class="btn" href="{url}">Try the demo →</a>
</div>
</body>
</html>
""")


@app.get("/start")
async def start(token: str):
    """Entry point — validate token, create OAuth state, redirect to LinkedIn."""
    data = _verify_token(token)
    state = str(uuid.uuid4())
    _store[state] = {
        "campaign_id": data["campaign_id"],
        "member_id":   data.get("member_id", "unknown"),
        "token_data":  data,
        "expires":     (datetime.utcnow() + timedelta(minutes=10)).isoformat(),
    }

    params = {
        "response_type": "code",
        "client_id":     LI_CLIENT_ID,
        "redirect_uri":  LI_REDIRECT_URI,
        "state":         state,
        "scope":         "r_liteprofile w_member_social",
    }
    url = f"{LI_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url)


@app.get("/callback")
async def callback(code: str = None, state: str = None, error: str = None):
    if error:
        return HTMLResponse(f"<h2>LinkedIn denied access: {error}</h2>", status_code=400)
    if not code or not state or state not in _store:
        raise HTTPException(status_code=400, detail="Invalid callback parameters.")

    session = _store[state]

    # Exchange authorization code for access token
    async with httpx.AsyncClient() as client:
        resp = await client.post(LI_TOKEN_URL, data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  LI_REDIRECT_URI,
            "client_id":     LI_CLIENT_ID,
            "client_secret": LI_CLIENT_SECRET,
        })

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {resp.text}")

    token_data = resp.json()
    access_token = token_data["access_token"]

    # Fetch member profile (name + ID)
    # Try /v2/me first; fall back to token introspection if scope doesn't allow it.
    def _localised(obj: dict) -> str:
        loc = obj.get("localized", {})
        return next(iter(loc.values()), "") if loc else ""

    member = {"first_name": "there", "last_name": "", "li_sub": ""}

    async with httpx.AsyncClient() as client:
        profile_resp = await client.get(
            LI_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if profile_resp.status_code == 200:
        profile = profile_resp.json()
        member = {
            "first_name": _localised(profile.get("firstName", {})) or "there",
            "last_name":  _localised(profile.get("lastName", {})),
            "li_sub":     profile.get("id", ""),
        }
    else:
        # /v2/me requires r_liteprofile; fall back to token introspection to get the member ID.
        async with httpx.AsyncClient() as client:
            intro_resp = await client.post(
                LI_INTROSPECT_URL,
                data={
                    "client_id":     LI_CLIENT_ID,
                    "client_secret": LI_CLIENT_SECRET,
                    "token":         access_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if intro_resp.status_code == 200:
            intro = intro_resp.json()
            member["li_sub"] = intro.get("sub", "")
            if not member["li_sub"]:
                # sub absent — surface the full introspection body for diagnosis
                raise HTTPException(
                    status_code=502,
                    detail=f"Token introspection succeeded but returned no sub. Body: {intro_resp.text}",
                )
        else:
            raise HTTPException(
                status_code=502,
                detail=f"Profile {profile_resp.status_code}: {profile_resp.text[:200]} | Introspect {intro_resp.status_code}: {intro_resp.text[:200]}",
            )

    # Store access token and member info keyed by a new publish token
    publish_state = str(uuid.uuid4())
    _store[publish_state] = {
        **session,
        "access_token": access_token,
        "member":       member,
    }
    del _store[state]  # Clean up OAuth state

    return RedirectResponse(f"/preview?ps={publish_state}")


@app.get("/preview", response_class=HTMLResponse)
async def preview(ps: str, request: Request):
    if ps not in _store:
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    session  = _store[ps]
    campaign = SAMPLE_CAMPAIGN
    member   = session["member"]
    post_text = _interpolate(SAMPLE_POST, campaign, member)

    return templates.TemplateResponse("preview.html", {
        "request":   request,
        "post_text": post_text,
        "member":    member,
        "campaign":  campaign,
        "ps":        ps,
    })


@app.post("/publish")
async def publish(request: Request):
    form = await request.form()
    ps   = form.get("ps")

    if not ps or ps not in _store:
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    session      = _store[ps]
    access_token = session["access_token"]
    campaign     = SAMPLE_CAMPAIGN
    member       = session["member"]
    post_text    = _interpolate(SAMPLE_POST, campaign, member)

    payload = {
        "author":     f"urn:li:person:{member['li_sub']}",
        "commentary": post_text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities":   [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            LI_POSTS_URL,
            json=payload,
            headers={
                "Authorization":    f"Bearer {access_token}",
                "Content-Type":     "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
                "LinkedIn-Version": "202401",
            },
        )

    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"LinkedIn API error: {resp.text}")

    post_id = resp.headers.get("x-restli-id", "")
    del _store[ps]

    return RedirectResponse(f"/success?post_id={urllib.parse.quote(post_id)}", status_code=303)


@app.get("/success", response_class=HTMLResponse)
async def success(post_id: str = ""):
    li_url = f"https://www.linkedin.com/feed/update/{post_id}" if post_id else "https://www.linkedin.com/feed/"
    return HTMLResponse(f"""
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Post Published!</title>
<style>
  body{{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;
        min-height:100vh;margin:0;background:#f0fdf4}}
  .card{{background:#fff;border-radius:12px;padding:40px 48px;max-width:480px;
         text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.08)}}
  .icon{{font-size:3rem;margin-bottom:12px}}
  h1{{margin:0 0 8px;font-size:1.6rem;color:#166534}}
  p{{color:#555;margin:0 0 28px}}
  a.btn{{display:inline-block;background:#0a66c2;color:#fff;border-radius:6px;
         padding:12px 24px;text-decoration:none;font-weight:600}}
</style>
</head>
<body>
<div class="card">
  <div class="icon">🎉</div>
  <h1>Post published!</h1>
  <p>Your LinkedIn post is now live. Thanks for sharing!</p>
  <a class="btn" href="{li_url}" target="_blank" rel="noopener">View on LinkedIn</a>
</div>
</body>
</html>
""")
