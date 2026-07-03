from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime

# Staff Schemas
class StaffBase(BaseModel):
    name: str
    role: str
    phone: str
    email: EmailStr
    calendar: bool = True
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    takesClasses: Optional[bool] = True

class StaffCreate(StaffBase):
    pass

class StaffResponse(StaffBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

class StaffUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    calendar: Optional[bool] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    takesClasses: Optional[bool] = None


# Student Schemas
class StudentBase(BaseModel):
    First_Name: str
    Last_Name: str
    Email: EmailStr
    Primary_Phone_Number: str
    Date_of_Birth: Optional[str] = None
    Gender: Optional[str] = None
    Address: Optional[str] = None
    Desired_Course: Optional[str] = None
    Nearest_Vama_Center: Optional[str] = None
    Preferred_Mode_of_Contact: Optional[str] = None

class StudentCreate(StudentBase):
    pass

class StudentResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    primary_phone_number: str
    date_of_birth: Optional[str]
    gender: Optional[str]
    address: Optional[str]
    desired_course: Optional[str]
    nearest_vama_center: Optional[str]
    preferred_mode_of_contact: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True

class StudentUpdate(BaseModel):
    First_Name: Optional[str] = None
    Last_Name: Optional[str] = None
    Email: Optional[EmailStr] = None
    Primary_Phone_Number: Optional[str] = None
    Date_of_Birth: Optional[str] = None
    Gender: Optional[str] = None
    Address: Optional[str] = None
    Desired_Course: Optional[str] = None
    Nearest_Vama_Center: Optional[str] = None
    Preferred_Mode_of_Contact: Optional[str] = None


# For /read-sheet endpoint compatibility
class StudentDashboard(BaseModel):
    """Timestamp": "Joined On",
    "Email": "Email",
    "First Name": "First Name",
    "Last Name": "Last Name",
    "Desired Course": "Course",
    "Primary Phone Number": "Phone",
    "Select your nearest Vama Center ": "Center"
    """
    pass

# For /add-row full form compatibility
# For /add-row full form compatibility
class StudentSheetCreate(BaseModel):
    Email: str = Field(..., alias="email")
    First_Name: str = Field(..., alias="firstName")
    Last_Name: str = Field(..., alias="lastName")
    Primary_Phone_Number: str = Field(..., alias="phone")
    Date_of_Birth: Optional[str] = Field("", alias="dob")
    Gender: Optional[str] = Field("", alias="gender")
    Address: Optional[str] = Field("", alias="address")
    Desired_Course: Optional[str] = Field("", alias="course")
    Nearest_Vama_Center: Optional[str] = Field("", alias="center")
    Preferred_Mode_of_Contact: Optional[str] = Field("", alias="contactMode")
    
    # Extra fields from the full form shown in screenshot/original code
    Class_Frequency: Optional[str] = Field("", alias="frequency")
    Parent_Guardian_Name: Optional[str] = Field("", alias="parentName")
    Complete_Address: Optional[str] = Field("", alias="completeAddress")
    City: Optional[str] = Field("", alias="city")
    State: Optional[str] = Field("", alias="state")
    State_Code: Optional[str] = Field("", alias="stateCode")
    Postal_Code: Optional[str] = Field("", alias="postalCode")
    Emergency_Contact_Number: Optional[str] = Field("", alias="emergencyContact")
    Blood_Group: Optional[str] = Field("", alias="bloodGroup")
    Allergies: Optional[str] = Field("", alias="allergies")
    How_did_you_hear_about_us: Optional[str] = Field("", alias="referral")
    Acknowledgement: Optional[str] = Field("", alias="acknowledgement")

    class Config:
        extra = "allow"  # Allow any other fields sent by frontend
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "firstName": "John",
                "lastName": "Doe",
                "email": "john@example.com",
                "phone": "1234567890",
                "course": "Guitar",
                "center": "Downtown"
            }
        }
