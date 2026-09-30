# Deploying the web app

## Run it locally first

```bash
pip install -r redirect_mapper/requirements-app.txt
python3 -m streamlit run redirect_mapper/app.py
```

It opens at `http://localhost:8501`. That's the whole app — the same thing your team
will see, just not reachable by anyone else.

## Hosting on A2 / hosting.com

**Shared or cPanel hosting will not work.** cPanel's "Setup Python App" runs WSGI
applications through Phusion Passenger. Streamlit is not a WSGI application — it's a
Tornado server that holds a persistent process and talks to the browser over WebSockets.
There is no Passenger entry point to give it, and no way to keep the process alive
between requests. On a shared plan, use Streamlit Community Cloud (below) instead.

**A VPS or dedicated server works.** You need root SSH. Two pieces: a systemd unit to
keep the app running, and nginx in front of it for TLS and WebSocket passthrough.

### 1. Install

```bash
sudo apt install -y python3-venv nginx        # or dnf, on CentOS-family images
sudo mkdir -p /opt/redirect-mapper
sudo chown "$USER" /opt/redirect-mapper
git clone -b claude/podcast-url-redirect-mapper-tr5htp \
  https://github.com/partnersmg/Claude.git /opt/redirect-mapper
cd /opt/redirect-mapper
python3 -m venv .venv
.venv/bin/pip install -r redirect_mapper/requirements-app.txt
```

### 2. Keep it running

`/etc/systemd/system/redirect-mapper.service`:

```ini
[Unit]
Description=Redirect Mapper
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/redirect-mapper
ExecStart=/opt/redirect-mapper/.venv/bin/streamlit run redirect_mapper/app.py \
  --server.port 8501 --server.address 127.0.0.1 \
  --server.headless true --browser.gatherUsageStats false
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now redirect-mapper
sudo systemctl status redirect-mapper
```

### 3. Put nginx in front

The `Upgrade` and `Connection` headers are the part people forget. Without them the page
loads and then hangs forever, because Streamlit's WebSocket never connects.

```nginx
server {
    listen 80;
    server_name redirects.example.com;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 600s;   # title fetching can run for minutes
    }
}
```

Then `sudo certbot --nginx -d redirects.example.com` for TLS.

### 4. Put a password on it

The app scrapes whatever URLs it's given. Do not leave it open to the internet. The
quickest gate is HTTP basic auth at the nginx level:

```bash
sudo apt install -y apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd yourteam
```

Add inside the `location /` block:

```nginx
auth_basic "Redirect Mapper";
auth_basic_user_file /etc/nginx/.htpasswd;
```

## Streamlit Community Cloud (the shared-hosting answer)

Free, and it deploys from this repository directly. Nothing to install or administer.

1. Go to <https://share.streamlit.io> and sign in with the GitHub account that can see
   `partnersmg/Claude`.
2. Authorize Streamlit for the `partnersmg` organization — a private repo won't appear
   in the list until you do. An org owner may have to approve this.
3. **Create app** → **Deploy a public app from GitHub**, and fill in:
   - Repository: `partnersmg/Claude`
   - Branch: `claude/podcast-url-redirect-mapper-tr5htp` (or `main` once merged)
   - Main file path: `redirect_mapper/app.py`
4. Deploy. The first build takes a few minutes while it installs the dependencies from
   the root `requirements.txt`.
5. **Restrict who can open it.** In the app's settings, under sharing, set the app to
   private and add your team's email addresses. Do this before circulating the link —
   the app fetches whatever URLs it's handed, so an open one is a small open proxy.

Things to know about the free tier:

- Apps sleep after a period of inactivity and take around 30 seconds to wake on the
  next visit. The first person in each morning waits; nobody else does.
- Resources are modest. A few hundred URLs with title fetching is well within it; tens
  of thousands may not be.
- Pushing to the deployed branch redeploys automatically.

If you outgrow it, Render or Fly.io run the same repo for a few dollars a month with a
custom domain and no sleeping, still without server administration.

## Operational notes

- Sitemaps are cached for 15 minutes per URL, so re-running with different section
  filters doesn't re-fetch them.
- Title fetching is capped by the concurrency slider (default 16). Be careful pointing
  this at a client's live production site — 16 concurrent requests for several hundred
  pages looks like a small load test to some hosts.
- Nothing is persisted. Results live in the browser session only and disappear on
  refresh, which also means no cleanup and no database.
