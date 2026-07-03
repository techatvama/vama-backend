# Deployment Guide

## Development (Local)

```bash
# Setup
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Configure .env with local DATABASE_URL
# Run
uvicorn main:app --reload --port 8000
```

## Docker Development

```bash
# Build & run with database
docker-compose up -d

# View logs
docker-compose logs -f backend

# Stop
docker-compose down
```

## Production Deployment

### Option 1: Heroku

```bash
# Create Procfile
echo "web: uvicorn main:app --host 0.0.0.0 --port $PORT" > Procfile

# Deploy
heroku create vama-backend
heroku config:set DATABASE_URL=postgresql://...
git push heroku main
```

### Option 2: AWS EC2 + SystemD

```bash
# SSH into server
ssh ubuntu@your-ec2-ip

# Setup
cd /opt
git clone https://github.com/vama/vama-backend.git
cd vama-backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with production secrets

# Create systemd service
sudo nano /etc/systemd/system/vama-backend.service
```

**Service file:**
```ini
[Unit]
Description=Vama Academy Backend
After=network.target

[Service]
Type=notify
User=ubuntu
WorkingDirectory=/opt/vama-backend
ExecStart=/opt/vama-backend/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# Enable & start
sudo systemctl enable vama-backend
sudo systemctl start vama-backend
sudo systemctl status vama-backend
```

### Option 3: DigitalOcean App Platform

```bash
# Connect repo to DigitalOcean
# Create app.yaml
```

**app.yaml:**
```yaml
name: vama-backend
services:
- name: backend
  github:
    repo: vama/vama-backend
    branch: main
  build_command: pip install -r requirements.txt
  run_command: uvicorn main:app --host 0.0.0.0 --port 8000
  envs:
  - key: DATABASE_URL
    scope: RUN_AND_BUILD_TIME
    value: ${db.connection_string}
databases:
- name: db
  engine: PG
  version: "16"
```

## Monitoring

```bash
# View logs
pm2 logs vama-backend

# Monitor memory/CPU
pm2 monit

# Setup Sentry for error tracking
pip install sentry-sdk
# Add to main.py:
# import sentry_sdk
# sentry_sdk.init("https://xxxxx@o.ingest.sentry.io/xxxxxx")
```

## Database Migrations

For major schema changes:

```bash
# Generate migration
alembic revision --autogenerate -m "Add new column"

# Review alembic/versions/xxxxx_add_new_column.py
# Apply to dev database first

# Apply to production
alembic upgrade head
```

Currently using auto-create, but for production recommend Alembic.

## Environment Variables (Production)

All must be set before deployment:

```
DATABASE_URL=postgresql://prod_user:SECURE_PASSWORD@prod-db.example.com/vama_prod
JWT_SECRET=VERY_LONG_RANDOM_STRING_MIN_32_CHARS
SMTP_PASSWORD=APP_SPECIFIC_PASSWORD (not regular password)
RAZORPAY_KEY_SECRET=rzp_live_secret_key
ENVIRONMENT=production
```

## SSL/TLS

Use Nginx reverse proxy or cloud provider's SSL:

**Nginx config:**
```nginx
server {
    listen 443 ssl http2;
    server_name api.vama.example.com;
    
    ssl_certificate /etc/ssl/certs/cert.pem;
    ssl_certificate_key /etc/ssl/private/key.pem;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Maintenance

- **Backup database**: Daily snapshots via cloud provider
- **Log rotation**: Configure logrotate for uvicorn logs
- **Health checks**: Setup uptime monitoring (UptimeRobot, etc.)
- **Security updates**: Keep Python + dependencies up-to-date

## Rollback

```bash
# If new version breaks
git revert <commit-hash>
git push
# Redeploy (CI/CD will handle or manual restart)
```
