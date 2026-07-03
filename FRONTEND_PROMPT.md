# Frontend Developer Prompt

**Task:** Google Sheets Integration (Exact Match to User Code)

The backend matches the "Older Version" logic.

## 1. Read Students
*   **Endpoint:** `GET /read-sheet`
*   **Response:**
    ```json
    {
      "data": [
        { "First Name": "John", "Timestamp": "..." },
        ...
      ]
    }
    ```
    *   **Frontend Logic:** Use `res.data.data` to access the array.
    *   **Keys:** The keys will be the **Original Google Sheet Headers** (e.g., "First Name", "Timestamp"). Do NOT expect snake_case keys in the read response if your sheet has Title Case headers.

## 2. Add Student
*   **Endpoint:** `POST /add-row`
*   **Payload:** strict snake_case (This matches your AddStudentDialog):
    ```json
    {
      "timestamp": "...",
      "center": "...",
      "email": "...",
      "first_name": "...",
      # ... other snake_case fields
    }
    ```
