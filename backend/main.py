import secrets
import uuid
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from database import get_db, engine, Base
from models import Product, Order, OrderItem
from schemas import (
    ProductCreate, ProductResponse, OrderCreate, OrderResponse, AdminLogin, Token
)

SECRET_KEY = os.getenv("JWT_SECRET", "cmd_super_secret_jwt_key_998877665544332211")
ALGORITHM = "HS256"
ADMIN_PASS = os.getenv("ADMIN_PASSWORD", "CMD")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="CMD Fragrances Enterprise API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Security Headers & CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' https:;"
    return response

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Auth Helper
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/admin/login", auto_error=False)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=12)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def verify_admin_token(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("admin_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized access")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Forbidden")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Endpoints
@app.post("/api/v1/admin/login", response_model=Token)
@limiter.limit("5/minute")
async def login(request: Request, payload: AdminLogin, response: Response):
    if not secrets.compare_digest(payload.password, ADMIN_PASS):
        raise HTTPException(status_code=400, detail="Incorrect password")
    token = create_access_token({"role": "admin"})
    response.set_cookie(
        key="admin_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False # Set True in SSL Production
    )
    return {"access_token": token, "token_type": "bearer"}

@app.get("/api/v1/products", response_model=List[ProductResponse])
async def list_products(db: AsyncSession = Depends(get_db)):
    stmt = select(Product).order_by(Product.id.desc())
    result = await db.execute(stmt)
    return result.scalars().all()

@app.post("/api/v1/products", response_model=ProductResponse)
async def create_product(payload: ProductCreate, db: AsyncSession = Depends(get_db), _=Depends(verify_admin_token)):
    product = Product(**payload.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product

@app.put("/api/v1/products/{product_id}", response_model=ProductResponse)
async def update_product(product_id: int, payload: ProductCreate, db: AsyncSession = Depends(get_db), _=Depends(verify_admin_token)):
    stmt = select(Product).where(Product.id == product_id)
    res = await db.execute(stmt)
    product = res.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    for k, v in payload.model_dump().items():
        setattr(product, k, v)
    await db.commit()
    await db.refresh(product)
    return product

@app.delete("/api/v1/products/{product_id}")
async def delete_product(product_id: int, db: AsyncSession = Depends(get_db), _=Depends(verify_admin_token)):
    stmt = select(Product).where(Product.id == product_id)
    res = await db.execute(stmt)
    product = res.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    await db.delete(product)
    await db.commit()
    return {"status": "success"}

@app.post("/api/v1/orders", response_model=dict)
@limiter.limit("10/minute")
async def create_order(request: Request, payload: OrderCreate, db: AsyncSession = Depends(get_db)):
    raw_subtotal = 0.0
    order_items_to_create = []
    total_qty = sum(item.quantity for item in payload.items)

    for item in payload.items:
        stmt = select(Product).where(Product.id == item.product_id)
        res = await db.execute(stmt)
        p = res.scalar_one_or_none()
        if not p or p.stock < item.quantity:
            raise HTTPException(status_code=400, detail=f"Insufficient stock for product ID {item.product_id}")
        
        p.stock -= item.quantity
        item_total = p.price * item.quantity
        raw_subtotal += item_total
        order_items_to_create.append((p.id, item.quantity, p.price))

    discount = raw_subtotal * 0.20 if total_qty >= 3 else (raw_subtotal * 0.15 if total_qty == 2 else 0.0)
    subtotal = raw_subtotal - discount
    final_total = round(subtotal + (subtotal * 0.14))

    tracking_code = f"#CMD-{uuid.uuid4().hex[:6].upper()}"
    new_order = Order(
        tracking_code=tracking_code,
        full_name=payload.full_name,
        phone=payload.phone,
        alt_phone=payload.alt_phone,
        governorate=payload.governorate,
        address=payload.address,
        total_amount=final_total,
        status="Under Review"
    )
    db.add(new_order)
    await db.flush()

    for p_id, qty, u_price in order_items_to_create:
        db.add(OrderItem(order_id=new_order.id, product_id=p_id, quantity=qty, unit_price=u_price))

    await db.commit()
    return {"status": "success", "tracking_code": tracking_code, "total": final_total}

@app.get("/api/v1/orders", response_model=List[dict])
async def list_orders(db: AsyncSession = Depends(get_db), _=Depends(verify_admin_token)):
    stmt = select(Order).order_by(Order.id.desc())
    res = await db.execute(stmt)
    orders = res.scalars().all()
    
    output = []
    for o in orders:
        items_stmt = select(OrderItem, Product.name).join(Product, OrderItem.product_id == Product.id).where(OrderItem.order_id == o.id)
        i_res = await db.execute(items_stmt)
        items = [{"name": name, "quantity": item.quantity, "price": item.unit_price} for item, name in i_res.all()]
        output.append({
            "id": o.id,
            "tracking_code": o.tracking_code,
            "full_name": o.full_name,
            "phone": o.phone,
            "governorate": o.governorate,
            "address": o.address,
            "total_amount": o.total_amount,
            "status": o.status,
            "created_at": o.created_at.strftime("%Y-%m-%d %H:%M"),
            "items": items
        })
    return output

@app.patch("/api/v1/orders/{order_id}/status")
async def update_order_status(order_id: int, payload: dict, db: AsyncSession = Depends(get_db), _=Depends(verify_admin_token)):
    stmt = select(Order).where(Order.id == order_id)
    res = await db.execute(stmt)
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = payload.get("status", order.status)
    await db.commit()
    return {"status": "success", "new_status": order.status}

@app.get("/api/v1/orders/track/{tracking_code}")
async def track_order(tracking_code: str, db: AsyncSession = Depends(get_db)):
    clean_code = tracking_code.strip().upper()
    if not clean_code.startswith("#CMD-"):
        clean_code = f"#CMD-{clean_code}"
    stmt = select(Order).where(Order.tracking_code == clean_code)
    res = await db.execute(stmt)
    o = res.scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "tracking_code": o.tracking_code,
        "full_name": o.full_name,
        "status": o.status,
        "total": o.total_amount,
        "date": o.created_at.strftime("%Y-%m-%d")
    }
