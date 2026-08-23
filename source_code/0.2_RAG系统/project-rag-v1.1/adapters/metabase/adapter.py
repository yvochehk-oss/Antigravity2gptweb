"""
Metabase Adapter

将 Analytics Contract 配置同步到 Metabase
"""

import os
import yaml
from pathlib import Path
from typing import Optional
import requests


class MetabaseAdapter:
    """
    Metabase 配置适配器
    
    职责：
    1. 读取 Analytics Contract YAML 配置
    2. 通过 Metabase API 同步配置
    3. 不修改 YAML，只同步到 Metabase
    """
    
    def __init__(self, base_url: str, api_key: str):
        """
        初始化 Metabase Adapter
        
        Args:
            base_url: Metabase 服务器地址，如 http://localhost:3000
            api_key: Metabase API Key
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "X-Api-Key": api_key
        })
    
    def _get(self, endpoint: str) -> dict:
        """GET 请求"""
        resp = self._session.get(f"{self._base_url}{endpoint}")
        resp.raise_for_status()
        return resp.json()
    
    def _put(self, endpoint: str, data: dict) -> dict:
        """PUT 请求"""
        resp = self._session.put(
            f"{self._base_url}{endpoint}",
            json=data
        )
        resp.raise_for_status()
        return resp.json()
    
    def _post(self, endpoint: str, data: dict) -> dict:
        """POST 请求"""
        resp = self._session.post(
            f"{self._base_url}{endpoint}",
            json=data
        )
        resp.raise_for_status()
        return resp.json()
    
    def get_database_id(self, database_name: str = "analytics") -> Optional[int]:
        """获取数据库 ID"""
        databases = self._get("/api/database")
        for db in databases:
            if db.get("name") == database_name:
                return db["id"]
        return None
    
    def get_tables(self, database_id: int) -> list[dict]:
        """获取数据库表"""
        resp = self._get(f"/api/database/{database_id}/tables")
        return resp
    
    def get_field_metadata(self, table_id: int) -> list[dict]:
        """获取表字段元数据"""
        resp = self._get(f"/api/table/{table_id}/query_metadata")
        return resp.get("fields", [])
    
    def update_field_metadata(
        self,
        field_id: int,
        display_name: str,
        description: str = "",
        semantic_type: str = ""
    ) -> dict:
        """
        更新字段元数据
        
        Args:
            field_id: 字段 ID
            display_name: 显示名称
            description: 字段说明
            semantic_type: 语义类型（currency、percentage 等）
            
        Returns:
            更新后的字段信息
        """
        data = {
            "display_name": display_name,
            "description": description,
        }
        
        if semantic_type:
            data["semantic_type"] = semantic_type
        
        return self._put(f"/api/field/{field_id}", data)
    
    def sync_metric_display(
        self,
        table_id: int,
        metric_config: dict
    ):
        """
        同步指标配置到 Metabase
        
        Args:
            table_id: 表 ID
            metric_config: 指标配置（来自 YAML）
        """
        metric_id = metric_config["metric_id"]
        name_cn = metric_config.get("name_cn", metric_id)
        definition = metric_config.get("definition", "")
        
        # 获取字段
        fields = self.get_field_metadata(table_id)
        
        for field in fields:
            if field["name"] == metric_id or field["name"].lower() == metric_id.lower():
                self.update_field_metadata(
                    field_id=field["id"],
                    display_name=name_cn,
                    description=definition,
                    semantic_type=self._get_semantic_type(metric_config)
                )
                print(f"Updated field: {metric_id} -> {name_cn}")
                break
    
    def _get_semantic_type(self, metric_config: dict) -> str:
        """根据指标配置推断语义类型"""
        metric_id = metric_config.get("metric_id", "")
        unit = metric_config.get("unit", "")
        
        if "rate" in metric_id or "ratio" in metric_id or unit == "percent":
            return "type/Float"
        if "amount" in metric_id or "cost" in metric_id or unit == "CNY":
            return "type/currency"
        if "margin" in metric_id:
            return "type/Float"
        
        return ""


def load_metric_configs(config_dir: Path) -> list[dict]:
    """加载所有指标配置"""
    metrics_dir = config_dir / "contract" / "metrics"
    configs = []
    
    for yaml_file in metrics_dir.glob("*.yaml"):
        if yaml_file.name.startswith("_"):
            continue
        with open(yaml_file, "r", encoding="utf-8") as f:
            configs.append(yaml.safe_load(f))
    
    return configs


def sync_all_metrics(
    adapter: MetabaseAdapter,
    config_dir: Path,
    database_name: str = "analytics"
):
    """同步所有指标配置到 Metabase"""
    # 获取数据库
    db_id = adapter.get_database_id(database_name)
    if not db_id:
        print(f"Database '{database_name}' not found")
        return
    
    # 获取表
    tables = adapter.get_tables(db_id)
    table_map = {t["name"]: t["id"] for t in tables}
    
    # 加载配置
    configs = load_metric_configs(config_dir)
    
    # 同步到对应的表
    for config in configs:
        source = config.get("source", "")
        # source 格式：analytics_project_profit
        table_id = table_map.get(source)
        
        if table_id:
            adapter.sync_metric_display(table_id, config)
        else:
            print(f"Table not found for metric: {config['metric_id']}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Sync Analytics Contract to Metabase")
    parser.add_argument("--url", default=os.getenv("METABASE_URL", "http://localhost:3000"))
    parser.add_argument("--api-key", default=os.getenv("METABASE_API_KEY", ""))
    parser.add_argument("--config-dir", type=Path, default=Path(__file__).parent.parent.parent / "analytics_contract")
    parser.add_argument("--metric", help="Sync specific metric")
    parser.add_argument("--all", action="store_true", help="Sync all metrics")
    
    args = parser.parse_args()
    
    adapter = MetabaseAdapter(args.url, args.api_key)
    
    if args.all:
        sync_all_metrics(adapter, args.config_dir)
    elif args.metric:
        print(f"Syncing metric: {args.metric}")
    else:
        print("Please specify --all or --metric")
