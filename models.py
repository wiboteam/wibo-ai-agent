# models.py
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String,
    Boolean, DateTime, Text
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# engine SQLite in file memory.db
engine = create_engine("sqlite:///memory.db", echo=False, future=True)

# session factory
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# base per i nostri model
Base = declarative_base()

class Message(Base):
    __tablename__ = "messages"
    id       = Column(Integer, primary_key=True, index=True)
    user     = Column(String, index=True)
    role     = Column(String)      # 'user' o 'assistant'
    content  = Column(Text)
    created  = Column(DateTime, default=datetime.utcnow)

class Event(Base):
    __tablename__ = "events"
    id               = Column(Integer, primary_key=True, index=True)
    user             = Column(String, index=True)
    action           = Column(String)
    datetime_evento  = Column(DateTime)
    sent_before      = Column(Boolean, default=False)
    sent_after       = Column(Boolean, default=False)

# Crea le tabelle se non esistono
Base.metadata.create_all(bind=engine)

