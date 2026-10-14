# Employee Attendance & Analytics API

FastAPI and MongoDB service for employee attendance tracking, regularization, and department analytics. Fully compliant with OpenAPI 3.0 contract and business rules R1–R10.

---

## Getting Started

### 1. Prerequisites
- Python 3.11+
- MongoDB 6.0+ (Local Docker or MongoDB Atlas)

### 2. Installation & Setup
```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate       # On Windows: .\.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables (or copy .env.example)
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB="attendance_db"
```

### 3. Run Application
```bash
uvicorn app.main:app --port 8000
```
Interactive API documentation is available at `http://127.0.0.1:8000/docs`.

---

## Architectural Highlights

- **Database-Side Aggregation & Window Functions:** 
  All analytics endpoints (`/monthly`, `/summary`, `/late`, `/trend`) execute natively inside MongoDB using `$lookup`, `$densify`, and `$setWindowFields` (`$rank`, `$avg`), with zero client-side list loops.
- **Index Optimization (`IXSCAN`):**
  Startup lifespan idempotently provisions compound and unique indexes on `employees` and `attendance_logs`. Every query and aggregation plan runs via index scans (`IXSCAN`) with zero collection scans (`COLLSCAN`), as verified by `GET /admin/explain/{endpoint}`.
- **Fail-Fast Lifespan:**
  Startup halts immediately if required database indexes cannot be created, preventing the service from silently running unindexed.
- **Strict Typing & Concurrency:**
  Strict Pydantic models (`StrictInt`, regex constraints) enforce integer epoch milliseconds and reject loose type coercion. Concurrency is guarded via atomic operations and optimistic concurrency locking.

---

## Documentation & Audits

- **`REVIEW.md`**: Comprehensive defect audit of the starter code, root-cause fixes, and complete test matrices across all endpoints.
- **`DECISIONS.md`**: Architectural reasoning covering index design, punch-in race resolution, cutoff tie-breaking, zero-log headcount accounting, and 100x scaling strategy.
- **`DATA_MODEL.md`** & **`openapi.yaml`**: Stored schema definitions and API contract.
