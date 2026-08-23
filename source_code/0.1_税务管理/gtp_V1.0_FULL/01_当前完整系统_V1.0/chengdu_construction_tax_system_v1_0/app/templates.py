"""V0.2: Jinja2 templates 单例。

⚠ Jinja2 3.1.6 + Starlette TemplateResponse 在 ``globals`` 包含 ORM
对象 / ``Decimal`` 时会触发 ``LRUCache: unhashable type: 'dict'``
（cache key 在某些路径下被替换为 globals dict）。

解决：关闭 env cache。性能上损失可忽略（Demo 规模小）。

额外：context_processor 将 current_user / role 加入所有模板全局上下文。
"""
from fastapi import Request
from fastapi.templating import Jinja2Templates


def _global_user(request: Request):
    """从中间件写入的 request.state.current_user 读取当前用户。"""
    user = getattr(request.state, "current_user", None)
    if user is None:
        return {
            "current_user": None,
            "current_role": None,
            "is_manager": False,
            "is_operator": False,
            "csrf_token": request.cookies.get("tax_csrf", ""),
        }
    return {
        "current_user": user,
        "current_role": user.role,
        "is_manager": user.role == "manager",
        "is_operator": user.role == "operator",
        "csrf_token": request.cookies.get("tax_csrf", ""),
    }


templates = Jinja2Templates(
    directory="app/templates",
    context_processors=[_global_user],
)
templates.env.cache = None  # type: ignore[assignment]
