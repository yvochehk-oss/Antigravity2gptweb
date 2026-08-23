"""V0.2: Jinja2 templates 单例。

⚠ Jinja2 3.1.6 + Starlette TemplateResponse 在 ``globals`` 包含 ORM
对象 / ``Decimal`` 时会触发 ``LRUCache: unhashable type: 'dict'``
（cache key 在某些路径下被替换为 globals dict）。

解决：关闭 env cache。性能上损失可忽略（Demo 规模小）。
"""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None  # type: ignore[assignment]