"""
Metabase MCP Server

提供 Metabase MCP 协议支持，让外部 AI 可以访问 Metabase 语义层
"""

from typing import Optional
from pydantic import BaseModel


class QueryResult(BaseModel):
    """查询结果"""
    columns: list[str]
    rows: list[list]
    row_count: int


class MetabaseMCP:
    """
    Metabase MCP Server
    
    协议让外部 AI（Claude、GPT 等）通过 Metabase 的语义层查询数据
    """
    
    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
    
    def list_dashboards(self) -> list[dict]:
        """列出所有 Dashboard"""
        # TODO: 实现
        return []
    
    def list_questions(self) -> list[dict]:
        """列出所有 Questions"""
        # TODO: 实现
        return []
    
    def list_models(self) -> list[dict]:
        """列出所有 Models"""
        # TODO: 实现
        return []
    
    def execute_question(self, question_id: int) -> QueryResult:
        """执行指定的 Question"""
        # TODO: 实现
        return QueryResult(columns=[], rows=[], row_count=0)
    
    def query_native(self, database_id: int, sql: str) -> QueryResult:
        """执行原生 SQL 查询"""
        # TODO: 实现
        return QueryResult(columns=[], rows=[], row_count=0)
