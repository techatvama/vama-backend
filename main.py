from fastapi import FastAPI
import gspread
import uvicorn
from oauth2client.service_account import ServiceAccountCredentials
from pydantic import BaseModel

app = FastAPI()

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],  # or "*" for quick testing
  allow_credentials=False,
  allow_methods=["*"],
  allow_headers=["*"],
)#code added at 29th july at 4pm

class AddRowRequest(BaseModel):
    timestamp: str
    center:str
    email:str
    first_name:str
    last_name:str
    gender:str
    course:str
    class_frequency:str
    parent_name:str
    complete_address:str
    city:str
    state:str
    state_code:str
    primary_phone:str
    emergency_contact:str
    blood_group:str
    allergies:str
    refferer:str
    acknowledgement:str


# Setup Google Sheets API
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    
    # Open by spreadsheet ID or name
    sheet = client.open_by_key("1gB1Fc_SR6UdpHQErmc6Ci3sfFEd4GvOnLFoPNE2_ok0").sheet1
    return sheet

# Example: Read rows
@app.get("/read-sheet")
def read_sheet():
    sheet = get_sheet()
    data = sheet.get_all_records()
    return {"data": data}

# Example: Add new row
@app.post("/add-row")
def add_row(request: AddRowRequest):
    sheet = get_sheet()
    
    sheet.append_row(list(request.model_dump(mode = "json").values()))
    return {"message": "Row added successfully"}

# Example: Update a specific cell
@app.put("/update-cell")
def update_cell(row: int, col: int, value: str):
    sheet = get_sheet()
    sheet.update_cell(row, col, value)
    return {"message": f"Updated cell {row}, {col} with '{value}'"}

if __name__ == "__main__":
    uvicorn.run(app,host = "0.0.0.0",port=8000)