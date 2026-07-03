# Architecture & Scalability

## Current State

Single monolithic FastAPI application:
- **main.py** (~5200 lines): All routes, business logic
- **models.py** (~700 lines): All ORM models in one file
- **database.py**: Connection pool, session management
- **auth.py**: Authentication, token generation, account provisioning
- **email_service.py**: Email delivery
- **schemas.py**: Pydantic request/response models

**Database:** PostgreSQL (Neon cloud)  
**ORM:** SQLAlchemy v2  
**Deployment:** Uvicorn (ASGI server)

## Scaling Roadmap

### Phase 1: Current (Production-Ready at This Scale)
- ✅ Single backend instance
- ✅ Monolithic main.py
- ✅ PostgreSQL with connection pooling
- ✅ JWT authentication
- ✅ Email delivery (async)
- ✅ Payment gateway (Razorpay)

**Handles:** Up to ~5000 concurrent users, ~50K DAU

### Phase 2: Modularization (When main.py hits 8000+ lines)

Split main.py into modular routes by domain:

```
app/routes/
├── __init__.py
├── auth.py           ← Login, activation, password reset
├── students.py       ← Student CRUD, profile, enrollment
├── applications.py   ← Public intake form + approval
├── invoices.py       ← Invoice creation, payment tracking
├── scheduling.py     ← Batches, sessions, attendance
├── staff.py          ← Staff management
├── curriculum.py     ← Grades, subjects, syllabi
├── admin.py          ← Admin dashboards
└── payments.py       ← Payment webhooks, reconciliation
```

**Benefit:** Easy feature isolation, parallel team development, clearer testing boundaries

### Phase 3: Caching (When response times degrade)

Add Redis for:
- Session storage (JWT tokens)
- Student list caching
- Payment dashboard aggregates (hourly refresh)
- Calendar caching (queries expensive)

```python
@app.get("/students")
async def get_students(db, cache: Redis):
    key = "students:list"
    cached = await cache.get(key)
    if cached:
        return json.loads(cached)
    
    students = db.query(Student).all()
    await cache.setex(key, 3600, json.dumps([...]))  # 1hr TTL
    return students
```

### Phase 4: Background Jobs (When emails/exports are slow)

Add Celery + RabbitMQ for:
- Email delivery (async, retry on fail)
- Invoice PDF generation
- Attendance reports
- Data exports

```python
@app.post("/admin/invoices/{id}/send-email")
async def send_invoice_email(id: int):
    send_email_task.delay(id)  # Returns immediately
    return {"message": "Email queued"}
```

### Phase 5: Microservices (If multiple teams / >10K concurrent users)

Split into separate services:
- **API Service** (core CRUD, auth)
- **Payment Service** (Razorpay integration, reconciliation)
- **Scheduling Service** (calendar, session management)
- **Email Service** (all email delivery)
- **Reporting Service** (analytics, exports)

Each service has:
- Own database (or shared for now)
- Own API (internal + external)
- Own auth (JWT validation)
- Own deployment

### Phase 6: Global Scaling (Multi-region)

- Database replication (Neon supports read replicas)
- CDN for static assets
- Geographically distributed servers
- Rate limiting at API gateway

## Current Bottlenecks & Solutions

| Bottleneck | When | Solution |
|------------|------|----------|
| Slow student list query | >1000 students | Add index on `created_at`, implement pagination, cache |
| Email delays | >100 emails/min | Move to async queue (Celery) |
| Calendar generation | >500 concurrent | Cache template, pagination, background generation |
| Payment reconciliation | Daily batch slow | Async job + timestamp indexing |
| File uploads | If added | Use S3 + signed URLs (not server storage) |

## Database Schema Optimization

### Current Indexes
```sql
CREATE INDEX idx_student_email ON students(email);
CREATE INDEX idx_student_center ON students(center_id);
CREATE INDEX idx_session_date ON class_sessions(date);
CREATE INDEX idx_attendance_session ON attendance(session_id, status);
```

### Future Optimizations
- Partitioning `attendance` table by date (for time-range queries)
- Materialized view for `admin/payment-dashboard` (hourly refresh)
- Read replica for heavy read operations (reporting, exports)

## Infrastructure as Code

### Using Terraform (Recommended for Cloud)

```hcl
# infrastructure/main.tf
resource "aws_ecs_cluster" "vama" {
  name = "vama-backend"
}

resource "aws_db_instance" "postgres" {
  allocated_storage    = 20
  engine              = "postgres"
  instance_class      = "db.t3.micro"
  db_name             = "vama_prod"
  identifier          = "vama-postgres"
  username            = var.db_username
  password            = var.db_password
  skip_final_snapshot = false
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id      = "vama-cache"
  engine          = "redis"
  node_type       = "cache.t3.micro"
  num_cache_nodes = 1
}
```

## Monitoring & Observability

### Logs
- Uvicorn logs → CloudWatch / DataDog
- Database query logs → Analyze slow queries
- Error tracking → Sentry

### Metrics
- Request latency (p50, p95, p99)
- Error rate (5xx, 4xx)
- Database connection pool usage
- Email queue depth

### Alerts
- Error rate > 1% → Slack alert
- Response time p95 > 500ms → Investigate
- Database CPU > 80% → Scale up
- Disk space < 10% → Alert

### APM (Application Performance Monitoring)
- New Relic or Datadog for end-to-end tracing
- Identify bottlenecks: slow queries, slow external calls, etc.

## Cost Estimation (AWS)

### Current Setup (single region, ~5K DAU)
- **RDS PostgreSQL**: t3.micro = $20/month
- **EC2 for backend**: t3.micro = $10/month
- **Load balancer**: ALB = $16/month
- **Data transfer**: ~$5/month
- **Backups**: $5/month
- **Total:** ~$56/month

### Phase 5 (Microservices, multi-region)
- **RDS (multi-AZ)**: m5.large = $300/month
- **ECS services** (5 services × 2 instances): $200/month
- **Load balancers**: $50/month
- **ElastiCache Redis**: $20/month
- **SQS/RabbitMQ**: $30/month
- **Monitoring**: $50/month
- **Backup & DR**: $100/month
- **Total:** ~$750/month

## Decision: Stay Monolithic or Modularize?

**Stay monolithic IF:**
- < 5 teams
- Single region
- < 50K DAU
- Feature velocity important

**Modularize IF:**
- Multiple teams need independence
- Need to scale specific services independently
- High availability required
- Want explicit service boundaries

**Current recommendation:** Stay monolithic + add modular routes (Phase 2) when main.py grows beyond 8000 lines.

## Deployment Architecture

```
┌─────────────────────────────────────────┐
│         CDN / CloudFront                 │
│    (static assets, API caching)          │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│      API Gateway / Load Balancer         │
│     (SSL termination, rate limiting)     │
└──────────────┬──────────────────────────┘
               │
    ┌──────────┼──────────┐
    │          │          │
┌───▼──┐  ┌───▼──┐  ┌───▼──┐
│ App  │  │ App  │  │ App  │  (ECS/Kubernetes)
│ 8000 │  │ 8000 │  │ 8000 │  (3 instances, auto-scaling)
└───┬──┘  └───┬──┘  └───┬──┘
    │         │         │
    └─────────┼─────────┘
              │
        ┌─────▼─────┐
        │ PostgreSQL │  (RDS Multi-AZ)
        │  (Neon)    │
        └────────────┘
        
        ┌──────────┐
        │  Redis   │  (ElastiCache)
        │  Cache   │
        └──────────┘
        
        ┌──────────┐
        │ RabbitMQ │  (SQS / Message Queue)
        │  Jobs    │
        └──────────┘
```

## CI/CD Pipeline

```
Git Commit → GitHub Actions → Build → Test → Deploy to Dev
    ↓
Merge to main → Build → Test → Deploy to Staging
    ↓
Manual approval → Deploy to Production
```

### GitHub Actions Workflow
```yaml
name: Deploy Vama Backend
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: pytest tests/ -v
      - run: python -m black --check .
      - run: python -m mypy .

  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Deploy to Heroku
        run: git push heroku main
```

