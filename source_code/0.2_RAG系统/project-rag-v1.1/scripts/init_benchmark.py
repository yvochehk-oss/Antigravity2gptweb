"""初始化评测题库。运行：python -m scripts.init_benchmark"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.session import get_db
from app.models import BenchmarkQuestion
from app.services.benchmark import init_benchmark_questions
from datetime import datetime, timezone

def main():
    with get_db() as db:
        count = init_benchmark_questions(db, None)
    print(f"Initialized {count} benchmark questions")

if __name__ == "__main__":
    main()
