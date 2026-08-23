import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker,DeclarativeBase
URL=os.getenv("DATABASE_URL","sqlite:///./data/demo.db")
engine=create_engine(URL,connect_args={"check_same_thread":False} if URL.startswith("sqlite") else {})
SessionLocal=sessionmaker(bind=engine,expire_on_commit=False)
class Base(DeclarativeBase): pass
