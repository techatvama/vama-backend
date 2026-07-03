# Frontend Implementation Instructions

## 1. Edit Functionality (Backend Ready ✅)

I have enabled the backend to support editing student details. Use the following API endpoint to update student information.

### Endpoint
`PUT /students/{id}`

### Request Schema
Send a JSON body with the fields you want to update.

**Example Request:**
```javascript
// URL: http://localhost:8000/students/123
// Method: PUT
// Headers: { "Content-Type": "application/json" }
{
  "First_Name": "John",
  "Last_Name": "Doe",
  "Primary_Phone_Number": "9876543210",
  "Desired_Course": "Piano",
  "Nearest_Vama_Center": "Downtown"
  // ... any other fields from StudentUpdate schema
}
```

### Implementation Guide
1.  Add an **"Edit" icon/button** to each row.
2.  On click, open a **Modal** pre-filled with that student's data.
3.  On "Save", send the `PUT` request to `/students/` + `student.id`.
4.  On success, refresh the table data.

---

## 2. Pagination & Premium UI (Frontend Task)

Please implement premium pagination and styling for the student table.

### Pagination Features
1.  **Rows per page**: Add a dropdown to select `10`, `25`, `50`, `100` items.
2.  **Navigation**: Add `Previous`, `Next`, and Page Number buttons (e.g., `1`, `2`, `3`).
3.  **State**: Functionality should be client-side (filter the data array returned from `/read-sheet`).

### Premium Styling Requirements
1.  **Buttons**: Use a sophisticated color palette (e.g., Deep Purple `#5B4DFF` or Emerald Green `#10B981`) with hover effects.
    *   *Normal*: Solid color, rounded corners (8px).
    *   *Hover*: Slight opacity drop or shadow elevation.
    *   *Active*: Darker shade.
2.  **Table**: 
    *   Add **zebra striping** (alternating light gray background).
    *   Add **hover row effect** (highlight row on mouse over).
    *   Use a modern font like `Inter` or `Poppins`.
3.  **Dropdown**: consistent styling with the buttons, minimal borders.

### Example Code Snippet (React/Tailwind concept)

```jsx
// Pagination Control
<div className="flex justify-between items-center mt-4">
  <select 
    value={itemsPerPage} 
    onChange={(e) => setItemsPerPage(Number(e.target.value))}
    className="border rounded-lg px-3 py-2 bg-white shadow-sm focus:ring-2 focus:ring-purple-500"
  >
    <option value={10}>10 per page</option>
    <option value={25}>25 per page</option>
    <option value={50}>50 per page</option>
  </select>

  <div className="flex gap-2">
    <button 
      onClick={prevPage}
      className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition duration-200 shadow-md"
    >
      Previous
    </button>
    {/* Page Numbers... */}
    <button 
      onClick={nextPage}
      className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition duration-200 shadow-md"
    >
      Next
    </button>
  </div>
</div>
```
