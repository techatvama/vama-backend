# Vama Academy API Documentation

## Base URL

- **Development:** `http://127.0.0.1:8000`
- **Production:** `https://api.vama.example.com`

## Authentication

Most endpoints require a JWT token in the `Authorization` header:

```
Authorization: Bearer <jwt_token>
```

Tokens are issued by login endpoints (`/student/login`, `/teacher/login`, `/admin/login`).

## Public Endpoints (No Auth Required)

### Student Intake Form
```
POST /public/student-applications
Content-Type: application/json

{
  "first_name": "John",
  "last_name": "Doe",
  "email": "john@example.com",
  "primary_phone_number": "+91-9876543210",
  "guardian_email": "parent@example.com",
  "gender": "Male",
  "date_of_birth": "2010-05-15",
  "address": "123 Main St",
  "city": "Bangalore",
  "state": "Karnataka",
  "desired_course": "Guitar",
  "class_frequency": "Weekly",
  "nearest_vama_center": "Vama - Gunjur",
  "blood_group": "O+",
  "allergies": "Peanuts",
  "referrer": "Google Search"
}

Response: { "id": 42, "message": "Application submitted" }
```

### Payment Page
```
GET /pay/{invoice_id}
→ HTML page for public payment via Razorpay
```

### List Centers
```
GET /centers

Response:
[
  { "id": 1, "name": "Vama - Gunjur", "address": "...", "phone": "...", "email": "..." },
  { "id": 2, "name": "Vama - Varthur", ... }
]
```

## Authentication Endpoints

### Student Login
```
POST /student/login
Content-Type: application/json

{
  "email": "student@example.com",
  "password": "password123"
}

Response:
{
  "student": { "id": 1, "first_name": "John", "email": "student@example.com", ... },
  "access_token": "eyJhbGc...",
  "token_type": "bearer"
}
```

### Teacher Login
```
POST /teacher/login
{
  "email": "teacher@example.com",
  "password": "password123"
}
```

### Account Activation
```
GET /activate?token=<activation_token>
→ Redirects to password reset page after email verification
```

### Forgot Password
```
POST /forgot-password
{
  "email": "student@example.com"
}
→ Sends password reset email
```

## Student Portal Endpoints

### Get Student Profile
```
GET /students/{student_id}
Authorization: Bearer <token>

Response:
{
  "id": 1,
  "first_name": "John",
  "last_name": "Doe",
  "email": "john@example.com",
  "primary_phone_number": "+91-9876543210",
  "current_grade": "Debut",
  "desired_course": "Guitar",
  "nearest_vama_center": "Vama - Gunjur",
  ...
}
```

### Update Student Profile
```
PUT /students/{student_id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "first_name": "Johnny",
  "current_grade": "Grade 1",
  "parent_name": "Jane Doe",
  "city": "Bangalore",
  "allergies": "None"
}

Response: { "message": "Student updated", "id": 1 }
```

### Get Upcoming Sessions
```
GET /student/{student_id}/sessions
Authorization: Bearer <token>
?start=2026-06-01&end=2026-06-30

Response:
[
  {
    "id": 101,
    "date": "2026-06-17",
    "start_time": "16:00",
    "end_time": "17:00",
    "batch": { "id": 5, "subject": "Guitar", "teacher": "Raj Kumar" },
    "status": "scheduled"
  }
]
```

### Reschedule Session
```
POST /student/{student_id}/reschedule
Authorization: Bearer <token>
Content-Type: application/json

{
  "session_id": 101,
  "new_date": "2026-06-20"
}

Response: { "message": "Session rescheduled" }
```

### Get Payment History
```
GET /student/{student_id}/payments
Authorization: Bearer <token>

Response:
{
  "active_package": { "id": 1, "plan_name": "Monthly 4 classes", "amount": 4500, ... },
  "invoices": [
    {
      "id": 1,
      "invoice_number": "INV-001",
      "amount": 4500,
      "paid_amount": 4500,
      "status": "paid",
      "payment_type": "Package",
      "issue_date": "2026-06-01",
      "paid_date": "2026-06-05"
    }
  ],
  "payments": [
    {
      "id": 1,
      "invoice_number": "INV-001",
      "amount": 4500,
      "method": "razorpay",
      "paid_date": "2026-06-05"
    }
  ]
}
```

## Teacher Portal Endpoints

### Get My Students
```
GET /teacher/{teacher_id}/students
Authorization: Bearer <token>

Response:
[
  {
    "id": 1,
    "first_name": "John",
    "last_name": "Doe",
    "email": "john@example.com",
    "instrument": "Guitar",
    "current_grade": "Grade 2"
  }
]
```

### Get My Sessions
```
GET /teacher/{teacher_id}/sessions
Authorization: Bearer <token>
?start=2026-06-01&end=2026-06-30

Response:
[
  {
    "id": 101,
    "date": "2026-06-17",
    "start_time": "16:00",
    "batch": { "id": 5, "subject": "Guitar" },
    "students": [
      { "id": 1, "name": "John Doe", "attendance": "present" },
      { "id": 2, "name": "Jane Smith", "attendance": null }
    ]
  }
]
```

### Mark Attendance
```
PUT /sessions/{session_id}/attendance/{student_id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "status": "present",  // or "absent", "late"
  "notes": "Good performance"
}

Response: { "message": "Attendance updated" }
```

## Admin Endpoints

### Get Students Overview
```
GET /admin/students-overview
Authorization: Bearer <token>

Response:
[
  {
    "id": 1,
    "first_name": "John",
    "email": "john@example.com",
    "primary_phone_number": "+91-9876543210",
    "current_grade": "Debut",
    "desired_course": "Guitar",
    "progress_pct": 45,
    "progress_done": 9,
    "progress_total": 20,
    "tracks": [
      { "id": 1, "instrument": "Guitar", "teacher_id": 1, "teacher_name": "Raj Kumar" }
    ]
  }
]
```

### Get Student Complete Profile
```
GET /admin/student/{student_id}/complete-profile
Authorization: Bearer <token>

Response:
{
  "id": 1,
  "first_name": "John",
  "email": "john@example.com",
  "gender": "Male",
  "date_of_birth": "2010-05-15",
  "parent_name": "Jane Doe",
  "city": "Bangalore",
  "address": "123 Main St",
  "phone": "+91-9876543210",
  "enrollments": [
    {
      "id": 1,
      "subject": "Guitar",
      "teacher": "Raj Kumar",
      "total_classes": 20,
      "attended": 18,
      "attendance_rate": 90
    }
  ],
  "financial": {
    "total_fees": 9000,
    "fees_paid": 4500,
    "outstanding": 4500,
    "payment_history": [...]
  },
  "performance": {
    "attendance_percentage": 90,
    "total_classes": 20,
    "progress_items_total": 20,
    "progress_items_done": 9
  },
  "upcoming_classes": [...]
}
```

### List Student Applications
```
GET /admin/student-applications?status=pending
Authorization: Bearer <token>

Response:
[
  {
    "id": 42,
    "first_name": "John",
    "email": "john@example.com",
    "status": "pending",
    "desired_course": "Guitar",
    "nearest_vama_center": "Vama - Gunjur",
    "created_at": "2026-06-15T10:30:00Z"
  }
]
```

### Approve Student Application
```
POST /admin/student-applications/{application_id}/approve
Authorization: Bearer <token>
Content-Type: application/json

{
  "current_grade": "Debut",
  "syllabus_type": "Trinity"
}

Response: { "id": 1, "first_name": "John", "last_name": "Doe" }
→ Creates real Student record & provisions activation email
```

### Reject Student Application
```
POST /admin/student-applications/{application_id}/reject
Authorization: Bearer <token>
Content-Type: application/json

{
  "reason": "Duplicate application"
}

Response: { "message": "Application rejected" }
```

### Create Invoice
```
POST /admin/invoices
Authorization: Bearer <token>
Content-Type: application/json

{
  "student_id": 1,
  "total_amount": 4500,
  "tax_amount": 810,
  "invoice_type": "monthly_fee",
  "description": "June classes"
}

Response:
{
  "id": 1,
  "invoice_number": "INV-001",
  "student_id": 1,
  "total_amount": 4500,
  "tax_amount": 810,
  "paid_amount": 0,
  "status": "pending"
}
```

### Get Payment Dashboard
```
GET /admin/payment-dashboard?period=month
Authorization: Bearer <token>

Response:
{
  "total_revenue": 50000,
  "paid_invoices": 25000,
  "pending_invoices": 25000,
  "unpaid_rate": 50,
  "invoices_by_status": {
    "paid": 10,
    "pending": 5,
    "overdue": 2
  }
}
```

## Error Responses

All errors return JSON with `detail` field:

```json
{
  "detail": "Invalid email or password"
}
```

### Common Status Codes

| Code | Meaning |
|------|---------|
| 200 | OK |
| 201 | Created |
| 400 | Bad Request (validation error) |
| 401 | Unauthorized (missing/invalid token) |
| 403 | Forbidden (insufficient permissions) |
| 404 | Not Found |
| 500 | Internal Server Error |

## Rate Limiting

Currently no rate limiting implemented. Future: 100 req/min per IP.

## Versioning

API is v1. Future versions via `/v2/` prefix if needed.

## Webhooks

- Razorpay payment webhooks handled at `POST /razorpay/webhooks`
- Email delivery status callbacks (future)

## Testing

Try the interactive API docs at `/docs` (Swagger UI) or `/redoc` (ReDoc).
