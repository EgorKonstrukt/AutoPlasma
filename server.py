from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import uvicorn
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AutoPlasmaServer")

DATABASE_URL = "sqlite:///./autoplasma.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class DBPowder(Base):
    __tablename__ = "powders"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    density = Column(Float)
    flow_factor = Column(Float)
    target_gpm = Column(Float)
    inventory = relationship("DBInventory", back_populates="powder", uselist=False)
    logs = relationship("DBUsageLog", back_populates="powder")

class DBInventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True, index=True)
    powder_id = Column(Integer, ForeignKey("powders.id"), unique=True)
    quantity_grams = Column(Float, default=0.0)
    powder = relationship("DBPowder", back_populates="inventory")

class DBUsageLog(Base):
    __tablename__ = "usage_log"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    powder_id = Column(Integer, ForeignKey("powders.id"))
    consumed_grams = Column(Float)
    operator = Column(String, default="System")
    duration_sec = Column(Float)
    powder = relationship("DBPowder", back_populates="logs")

Base.metadata.create_all(bind=engine)

class PowderSchema(BaseModel):
    id: Optional[int] = None
    name: str
    density: float
    flow_factor: float
    target_gpm: float
    class Config: from_attributes = True

class InventorySchema(BaseModel):
    id: int
    powder_id: int
    powder_name: str
    quantity_grams: float
    class Config: from_attributes = True

class UsageLogSchema(BaseModel):
    id: int
    timestamp: datetime
    powder_name: str
    consumed_grams: float
    operator: str
    duration_sec: float
    class Config: from_attributes = True

class UsageRecord(BaseModel):
    powder_name: str
    consumed_grams: float
    duration_sec: float
    operator: str = "Operator"

class StockOperation(BaseModel):
    powder_name: str
    quantity_change: float
    operator: str
    comment: str = ""

app = FastAPI(title="AutoPlasma Server")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/inventory/adjust/", response_model=dict)
def adjust_stock(op: StockOperation, db: Session = Depends(get_db)):
    logger.info(f"Adjusting stock for {op.powder_name} by {op.quantity_change}")
    powder = db.query(DBPowder).filter(DBPowder.name == op.powder_name).first()
    if not powder:
        raise HTTPException(status_code=404, detail="Powder not found")

    inv = db.query(DBInventory).filter(DBInventory.powder_id == powder.id).first()
    if not inv:
        inv = DBInventory(powder_id=powder.id, quantity_grams=0.0)
        db.add(inv)

    new_quantity = inv.quantity_grams + op.quantity_change
    if new_quantity < 0:
        raise HTTPException(status_code=400, detail="Resulting stock cannot be negative")

    inv.quantity_grams = new_quantity
    log_entry = DBUsageLog(
        powder_id=powder.id,
        consumed_grams=-op.quantity_change,
        duration_sec=0.0,
        operator=op.operator
    )
    db.add(log_entry)
    db.commit()
    db.refresh(inv)
    return {"status": "success", "new_quantity": inv.quantity_grams, "powder_name": op.powder_name}

@app.delete("/powders/{name}")
def delete_powder(name: str, db: Session = Depends(get_db)):
    powder = db.query(DBPowder).filter(DBPowder.name == name).first()
    if not powder:
        raise HTTPException(status_code=404, detail="Not found")
    inv = db.query(DBInventory).filter(DBInventory.powder_id == powder.id).first()
    db.delete(powder)
    if inv: db.delete(inv)
    db.commit()
    logger.info(f"Deleted powder: {name}")
    return {"status": "deleted"}

@app.post("/powders/", response_model=PowderSchema)
def create_powder(powder: PowderSchema, db: Session = Depends(get_db)):
    db_item = DBPowder(**powder.dict())
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    inv_item = DBInventory(powder_id=db_item.id, quantity_grams=5000.0)
    db.add(inv_item)
    db.commit()
    logger.info(f"Created powder: {powder.name}")
    return db_item

@app.get("/powders/", response_model=List[PowderSchema])
def read_powders(db: Session = Depends(get_db)):
    return db.query(DBPowder).all()

@app.get("/inventory/", response_model=List[InventorySchema])
def get_inventory(db: Session = Depends(get_db)):
    items = db.query(DBInventory).join(DBPowder).all()
    return [{"id": i.id, "powder_id": i.powder_id, "powder_name": i.powder.name, "quantity_grams": i.quantity_grams} for i in items]

@app.post("/log_usage/", response_model=dict)
def log_usage(record: UsageRecord, db: Session = Depends(get_db)):
    logger.info(f"Logging usage: {record.powder_name}, {record.consumed_grams}g, Op: {record.operator}")
    powder = db.query(DBPowder).filter(DBPowder.name == record.powder_name).first()
    if not powder: raise HTTPException(status_code=404, detail="Powder not found")

    inv = db.query(DBInventory).filter(DBInventory.powder_id == powder.id).first()
    if not inv: raise HTTPException(status_code=404, detail="Inventory not found")

    if inv.quantity_grams < record.consumed_grams:
        raise HTTPException(status_code=400, detail="Insufficient material on stock")

    inv.quantity_grams -= record.consumed_grams
    log_entry = DBUsageLog(
        powder_id=powder.id,
        consumed_grams=record.consumed_grams,
        duration_sec=record.duration_sec,
        operator=record.operator
    )
    db.add(log_entry)
    db.commit()
    return {"status": "success", "remaining": inv.quantity_grams}

@app.get("/stats/summary/")
def get_summary(db: Session = Depends(get_db)):
    total_used = db.query(DBUsageLog).all()
    summary = {}
    for log in total_used:
        name = log.powder.name
        if name not in summary: summary[name] = 0.0
        summary[name] += log.consumed_grams
    return summary

@app.get("/logs/", response_model=List[UsageLogSchema])
def get_logs(limit: int = 50, db: Session = Depends(get_db)):
    logs = db.query(DBUsageLog).join(DBPowder).order_by(DBUsageLog.timestamp.desc()).limit(limit).all()
    return [{"id": l.id, "timestamp": l.timestamp, "powder_name": l.powder.name,
             "consumed_grams": l.consumed_grams, "operator": l.operator, "duration_sec": l.duration_sec} for l in logs]

if __name__ == "__main__":
    print("Starting AutoPlasma Server on port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)