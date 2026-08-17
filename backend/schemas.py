from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import datetime

class ProductBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    brand: str = Field(..., min_length=1, max_length=100)
    concentration: str = Field(..., min_length=1, max_length=100)
    price: float = Field(..., gt=0)
    stock: int = Field(..., ge=0)
    description: Optional[str] = None
    notes: Optional[str] = None
    images: List[str] = Field(default_factory=list)

class ProductCreate(ProductBase):
    pass

class ProductResponse(ProductBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class OrderItemSchema(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0)

class OrderCreate(BaseModel):
    full_name: str = Field(..., min_length=3, max_length=255)
    phone: str = Field(..., pattern=r"^01[0-2,5]{1}[0-9]{8}$")
    alt_phone: Optional[str] = Field(None, pattern=r"^$|^01[0-2,5]{1}[0-9]{8}$")
    governorate: str = Field(..., min_length=2, max_length=100)
    address: str = Field(..., min_length=5)
    items: List[OrderItemSchema] = Field(..., min_items=1)

class OrderResponse(BaseModel):
    id: int
    tracking_code: str
    full_name: str
    phone: str
    alt_phone: Optional[str]
    governorate: str
    address: str
    total_amount: float
    status: str
    created_at: datetime
    items: List[dict]
    model_config = ConfigDict(from_attributes=True)

class AdminLogin(BaseModel):
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
