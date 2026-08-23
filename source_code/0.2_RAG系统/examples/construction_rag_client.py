"""Optimized RAG client example with retry and error handling."""
import httpx
import time
from typing import Optional


class RAGClient:
    """Optimized client for ProjectRAG V0.2 Optimized."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8800",
        timeout: float = 30.0,
        max_retries: int = 3
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make HTTP request with retry."""
        kwargs.setdefault("timeout", self.timeout)

        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client() as client:
                    response = client.request(method, f"{self.base_url}{path}", **kwargs)
                    response.raise_for_status()
                    return response.json()
            except httpx.TimeoutException:
                if attempt == self.max_retries:
                    raise
                time.sleep(2 ** attempt)
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    raise
                if attempt == self.max_retries:
                    raise
                time.sleep(2 ** attempt)

        raise RuntimeError("Max retries exceeded")

    def health(self) -> dict:
        """Check service health."""
        return self._request("GET", "/api/v1/health")

    def list_projects(self) -> list[dict]:
        """List all projects."""
        return self._request("GET", "/api/v1/projects")

    def create_project(self, project_code: str, name: str, **kwargs) -> dict:
        """Create a new project."""
        return self._request(
            "POST", "/api/v1/projects",
            json={"project_code": project_code, "name": name, **kwargs}
        )

    def sync_project(
        self,
        project_code: str,
        name: str,
        external_project_id: str,
        status: str = "ACTIVE"
    ) -> dict:
        """Sync project from external system (idempotent)."""
        return self._request(
            "POST", "/api/v1/projects/sync",
            json={
                "project_code": project_code,
                "name": name,
                "external_system": "construction-tax",
                "external_project_id": str(external_project_id),
                "status": status,
            }
        )

    def get_project(
        self,
        project_id: Optional[int] = None,
        project_code: Optional[str] = None,
        page: int = 1,
        page_size: int = 50
    ) -> dict:
        """Get project details with paginated documents."""
        params = f"?page={page}&page_size={page_size}"
        if project_id:
            return self._request("GET", f"/api/v1/projects/{project_id}{params}")
        if project_code:
            return self._request("GET", f"/api/v1/projects/by-code/{project_code}{params}")
        raise ValueError("project_id or project_code required")

    def retrieve(
        self,
        project_code: str,
        query: str,
        filters: dict = None,
        top_k: int = 10,
        rerank: bool = True
    ) -> dict:
        """Retrieve relevant document chunks."""
        return self._request(
            "POST", "/api/v1/retrieve",
            json={
                "project_code": project_code,
                "query": query,
                "filters": filters or {},
                "top_k": top_k,
                "rerank": rerank,
            }
        )

    def query(
        self,
        project_code: str,
        query: str,
        filters: dict = None,
        top_k: int = 10,
        answer: bool = True
    ) -> dict:
        """Retrieve and generate answer."""
        return self._request(
            "POST", "/api/v1/query",
            json={
                "project_code": project_code,
                "query": query,
                "filters": filters or {},
                "top_k": top_k,
                "answer": answer,
            }
        )

    def upload_document(
        self,
        project_id: int,
        file_path: str,
        document_type: str = "",
        entity_code: str = "",
        business_category: str = "",
        **kwargs
    ) -> dict:
        """Upload a document."""
        with open(file_path, "rb") as f:
            files = {"file": (file_path.split("/")[-1], f)}
            data = {
                "project_id": str(project_id),
                "document_type": document_type,
                "entity_code": entity_code,
                "business_category": business_category,
                **kwargs
            }
            return self._request("POST", "/api/v1/documents/upload", data=data, files=files)

    def import_folder(
        self,
        project_id: int,
        folder_path: str,
        recursive: bool = True,
        auto_parse: bool = True
    ) -> dict:
        """Import all files from a folder."""
        return self._request(
            "POST", "/api/v1/documents/import-folder",
            json={
                "project_id": project_id,
                "path": folder_path,
                "recursive": recursive,
                "auto_parse": auto_parse,
            }
        )

    def get_job(self, job_id: int) -> dict:
        """Get job status."""
        return self._request("GET", f"/api/v1/jobs/{job_id}")

    def list_jobs(
        self,
        status: str = None,
        page: int = 1,
        page_size: int = 50
    ) -> dict:
        """List jobs with pagination."""
        params = f"?page={page}&page_size={page_size}"
        if status:
            params += f"&status={status}"
        return self._request("GET", f"/api/v1/jobs{params}")

    def audit_project(self, project_id: int) -> dict:
        """Get project audit report."""
        return self._request("GET", f"/api/v1/projects/{project_id}/audit")

    def get_stats(self, project_id: int = None) -> dict:
        """Get query statistics."""
        path = "/api/v1/stats"
        if project_id:
            path += f"?project_id={project_id}"
        return self._request("GET", path)

    def delete_document(self, document_id: int) -> dict:
        """Delete a document and its chunks."""
        return self._request("DELETE", f"/api/v1/documents/{document_id}")


if __name__ == "__main__":
    # Example usage
    rag = RAGClient()

    # Check health
    health = rag.health()
    print(f"Service: {health['status']}, Version: {health['version']}")

    # Sync project
    project = rag.sync_project(
        project_code="YB001",
        name="宜宾XX项目",
        external_project_id=18
    )
    print(f"Project: {project['project_code']} (ID: {project['id']})")

    # Retrieve documents
    results = rag.retrieve(
        project_code="YB001",
        query="设备台班和结算依据",
        filters={"entity_code": ["D"], "business_category": ["equipment"]}
    )
    print(f"Found {len(results['results'])} results")

    # Get stats
    stats = rag.get_stats()
    print(f"Total queries: {stats['total_queries']}")
