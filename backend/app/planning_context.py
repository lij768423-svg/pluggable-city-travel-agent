"""当前调用的有限景点池与固定景点目录；ContextVar 避免并发网页请求受 MCP 影响。

ThreadPoolExecutor.submit 不复制 ContextVar，后台线程必须显式安装任务自己的目录。
"""
from contextvars import ContextVar

allowed_pois: ContextVar[frozenset[int] | None] = ContextVar("allowed_pois", default=None)
# app.poi_catalog.PlanningCatalog；None 表示没有任务上下文，读进程当前发布版
poi_catalog: ContextVar = ContextVar("poi_catalog", default=None)
