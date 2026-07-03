# Quick Reference Card

## 🚀 Start Backend

```bash
# Local development
cp .env.example .env
uvicorn main:app --reload --port 8000

# Or with Docker
docker-compose up -d
```

API docs: http://127.0.0.1:8000/docs

## 📚 Key Documentation

| Need | Read |
|------|------|
| Getting started? | README.md (5 min) |
| API reference? | API.md (10 min) |
| How to deploy? | DEPLOYMENT.md (10 min) |
| Scaling plan? | ARCHITECTURE.md (15 min) |
| What changed? | SUMMARY.md (10 min) |
| Project layout? | PROJECT_STRUCTURE.md (5 min) |

## 🔐 Secrets Setup

```bash
# Never commit .env
cp .env.example .env

# Fill in your secrets:
DATABASE_URL=postgresql://user:pass@host/db
JWT_SECRET=your-secret-key-here
SMTP_PASSWORD=app-specific-password
RAZORPAY_KEY_SECRET=your-razorpay-secret
```

## 📦 Dependencies

```bash
pip install -r requirements.txt
```

## 🧪 Test It

```bash
# Manual: Hit /docs in browser
curl http://127.0.0.1:8000/docs

# Automate: Run tests (when you add them)
pytest tests/ -v
```

## 🌐 Frontend Integration

Frontend expects:
- Base URL: `http://127.0.0.1:8000` (dev)
- Auth: `Authorization: Bearer <jwt_token>`
- Set `VITE_API_URL` in frontend `.env`

## 📊 150 Routes at a Glance

- **Public:** `/public/student-applications`, `/centers`, `/pay/{id}`
- **Auth:** `/student/login`, `/teacher/login`, `/activate`, `/forgot-password`
- **Student:** `/students/{id}`, `/student/{id}/sessions`, `/student/{id}/payments`
- **Teacher:** `/teacher/{id}/students`, `/teacher/{id}/sessions`
- **Admin:** `/admin/students-overview`, `/admin/invoices`, `/admin/student-applications/{id}/approve`
- **Billing:** `/admin/payment-dashboard`, `/admin/packages`, `/admin/subscriptions`
- **Scheduling:** `/batches`, `/sessions`, `/class-sessions`, `/scheduling/calendar`
- **Curriculum:** `/admin/grades`, `/admin/subjects`, `/admin/syllabi`

## ✅ Production Checklist

- [ ] Set all secrets in `.env`
- [ ] Test with `docker-compose up -d`
- [ ] Run API docs at `/docs`, test endpoints
- [ ] Setup database backup
- [ ] Setup monitoring (Sentry)
- [ ] Setup logging (CloudWatch/ELK)
- [ ] Deploy (see DEPLOYMENT.md)
- [ ] Monitor error rate & latency

## 🆘 Troubleshooting

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| Database connection fails | Check DATABASE_URL in .env |
| Port 8000 in use | Change port: `uvicorn main:app --port 9000` |
| Docker issues | Run `docker-compose down && docker-compose up --build` |

## 📞 Quick Links

- API docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/
- GitHub: [your-repo]
- Issues: [your-issues-board]

---

**⏱️ From zero to running: 5 minutes**
