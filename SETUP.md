# Setup Instructions for Neon DB Integration

## Step 1: Get Neon Database URL
1. Go to [console.neon.tech](https://console.neon.tech)
2. Create a new project (if you haven't already)
3. Copy the connection string
   - It looks like: `postgresql://user:password@ep-xxx-xxx.region.aws.neon.tech/dbname?sslmode=require`

## Step 2: Configure Environment
1. Create `.env` file in the `backend` folder:
```bash
cd backend
cp .env.example .env
```

2. Edit `.env` and replace with your actual Neon connection string:
```env
DATABASE_URL=your_actual_connection_string_here
```

## Step 3: Install Dependencies
```bash
cd backend
pip install -r requirements.txt
```

## Step 4: Run the Application
```bash
# From the backend directory
uvicorn main:app --reload --port 8000
```

On first run, the tables will be automatically created in your Neon database.

## Step 5: Verify
1. Check the terminal output for "✅ Database tables created successfully"
2. Visit `http://localhost:8000/db-info` to see database stats
3. Try adding a staff member from the frontend

## Troubleshooting
- **Connection Error**: Double-check your DATABASE_URL in `.env`
- **Module Not Found**: Run `pip install -r requirements.txt`
- **Port Already in Use**: Change port with `--port 8001`
