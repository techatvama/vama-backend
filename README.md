# Vama Academy Backend

Production-grade FastAPI backend for Vama Academy music education platform. Manages students, teachers, scheduling, payments, curriculum, and student intake.

## Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL (Neon cloud or local)
- pip

### Installation

```bash
# 1. Clone and enter directory
cd vama-backend

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your DATABASE_URL and other secrets

# 5. Start dev server
uvicorn main:app --reload --port 8000
```

Server: http://127.0.0.1:8000  
API Docs: http://127.0.0.1:8000/docs

## Architecture

```
vama-backend/
├── main.py              ← FastAPI app & routes (5200+ lines)
├── models.py            ← SQLAlchemy ORM models
├── database.py          ← DB connection management
├── auth.py              ← Authentication & account provisioning
├── schemas.py           ← Pydantic request/response models
├── email_service.py     ← Email delivery
├── invoice_pdf.py       ← PDF generation
├── requirements.txt     ← Python dependencies
├── .env.example         ← Environment template
└── migrations/          ← SQL scripts (manual)
```

## Key Endpoints

### Public (No Auth)
- `POST /public/student-applications` — Submit enrollment form
- `GET /centers` — List active centers

### Student Portal
- `POST /student/login` — Login
- `GET /student/{id}/sessions` — Upcoming classes
- `GET /student/{id}/payments` — Payment history

### Teacher Portal
- `POST /teacher/login` — Login
- `GET /teacher/{id}/students` — My students
- `GET /teacher/{id}/sessions` — My classes

### Admin (Requires Auth)
- `GET /admin/students-overview` — Student list + stats
- `GET /admin/student/{id}/complete-profile` — Full profile
- `POST /admin/student-applications/{id}/approve` — Onboard lead
- `GET /admin/invoices` — Invoice management
- `GET /admin/payment-dashboard` — Payment stats

## Database

**Engine:** PostgreSQL (Neon)  
**ORM:** SQLAlchemy v2  
**Init:** Automatic via `Base.metadata.create_all()` on startup

### Key Tables
- `students` — Student profiles
- `student_applications` — Intake form submissions
- `staff` — Teachers & admins
- `batches` — Class series
- `class_sessions` — Individual classes
- `invoices` — Billing
- `student_packages` — Fee packages
- `attendance` — Attendance tracking

## Environment

```env
DATABASE_URL=postgresql://user:pass@host/db
JWT_SECRET=your-secret-key
SMTP_HOST=smtp.gmail.com
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=app-password
RAZORPAY_KEY_ID=key_live_xxxxx
RAZORPAY_KEY_SECRET=secret_xxxxx
FRONTEND_URL=http://localhost:5173
```

## Deployment

**Local Dev:**
```bash
uvicorn main:app --reload --port 8000
```

**Production:**
```bash
# Docker
docker build -t vama-backend .
docker run -e DATABASE_URL=... -p 8000:8000 vama-backend

# PM2
pm2 start main.py --name vama-backend
```

## Integration with Frontend

Frontend (`vama-frontend/`) expects:
- Base URL: `http://127.0.0.1:8000` (dev)
- JWT token in `Authorization: Bearer <token>` header
- JSON request/response

See `vama-frontend/.env` → `VITE_API_URL`

## Common Tasks

**Create student:** 
```bash
curl -X POST http://127.0.0.1:8000/students \
  -H "Content-Type: application/json" \
  -d '{"first_name":"John","last_name":"Doe","email":"john@example.com","primary_phone_number":"+91-9876543210","desired_course":"Guitar"}'
```

**List students:**
```bash
curl http://127.0.0.1:8000/students | jq
```

**Create invoice:**
```bash
curl -X POST http://127.0.0.1:8000/admin/invoices \
  -H "Content-Type: application/json" \
  -d '{"student_id":1,"total_amount":4500}'
```

## Removed Components

- ❌ Google Sheets integration (legacy, replaced by PostgreSQL)
- ❌ Stale frontend/backend copy (unified on single vama-backend/)

## Notes

- **Monolithic main.py**: All routes in one file for now; future refactor into modular `app/routes/` by domain
- **Unified ORM file**: Single `models.py` for all tables; easier to navigate
- **JWT Auth**: Token-based, validated on protected endpoints
- **Email-first onboarding**: Pending-activation accounts until email verified
- **Intake separation**: `StudentApplication` table keeps `Student` schema stable

## License

Proprietary — Vama Academy
