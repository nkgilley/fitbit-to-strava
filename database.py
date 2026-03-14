import os
import json
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, Float, Text, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

# Default to local SQLite if no DATABASE_URL is provided
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data.db")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Token(Base):
    __tablename__ = "tokens"
    service = Column(String(50), primary_key=True) # 'strava' or 'fitbit'
    access_token = Column(Text)
    refresh_token = Column(Text)
    expires_at = Column(Integer)
    other_data = Column(JSON) # Store the full response just in case

class SyncedActivity(Base):
    __tablename__ = "synced_activities"
    old_id = Column(String(100), primary_key=True)
    new_id = Column(String(100))
    name = Column(String(255))
    date = Column(String(100))
    status = Column(String(50), default="pending_cleanup") # 'pending_cleanup', 'completed'
    distance_mi = Column(Float)
    duration_min = Column(Float)
    elevation_gain_ft = Column(Integer)
    synced_at = Column(DateTime, default=datetime.utcnow)

class SkippedActivity(Base):
    __tablename__ = "skipped_activities"
    id = Column(String(100), primary_key=True)
    name = Column(String(255))
    date = Column(String(100))
    reason = Column(String(255))

class FixableActivity(Base):
    __tablename__ = "fixable_activities"
    id = Column(String(100), primary_key=True)
    name = Column(String(255))
    date = Column(String(100))
    hr_data = Column(JSON) # Cache Fitbit heart rate data
    activity_data = Column(JSON) # Cache Strava activity metadata
    streams_data = Column(JSON) # Cache Strava sensor streams

class ScanResult(Base):
    __tablename__ = "scan_results"
    id = Column(Integer, primary_key=True, default=1)
    count = Column(Integer, default=0)
    fixable_count = Column(Integer, default=0)
    last_scan = Column(String(100), default="Never")

# Create tables
def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
