"""标准 Streamable HTTP 适配；身份来自专用凭据，不来自工具参数。"""
from __future__ import annotations

import hmac
import re
from typing import Any
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse

from .mcp_domain import MCPError
from .mcp_service import MCPService
from .schemas import ExternalSelection, MealEditPreviewRequest

connection: ContextVar[tuple[str, str] | None] = ContextVar("mcp_connection", default=None)

principal: ContextVar[str | None] = ContextVar("mcp_principal", default=None)


def authenticated_owner() -> str:
    owner = principal.get()
    if owner is None:
        raise MCPError("unauthorized", "MCP 身份无效")
    return owner


def create_mcp_app(service: MCPService, owner: str, credential: str, *, port: int, login=None):
    if not owner.startswith("user:") or len(credential) < 24:
        raise ValueError("需要有效的专用身份和至少24字符的私有凭据")
    mcp = FastMCP("Hangzhou Travel", instructions=(
        "首次使用先connect_account获取登录授权链接，请用户在浏览器登录并授权，完成后再次connect_account确认。已绑定无需重复登录。账号密码只能在网站输入。"
        "使用当前聊天AI理解需求并挑选候选ID；服务不调用语言模型。先prepare_trip，向用户补齐缺项，"
        "再generate_trip并get_job查询。保留必去和预算等硬条件；needs_changes必须询问用户。"
        "ready才是正式结果。draft为测试草稿，必须展示待核实提示，不能称完整可执行。data_unavailable不得描述为成功。换餐先展示预览，"
        "取得用户明确确认再confirm_meal_change，不因工具可调用就自行确认。"
    ), stateless_http=True, json_response=True, max_request_body_size=65536,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[f"127.0.0.1:{port}", f"localhost:{port}"],
            allowed_origins=[f"http://127.0.0.1:{port}", f"http://localhost:{port}"]))

    def invoke(fn, *args, **kwargs):
        try:
            if principal.get() is None and login and connection.get():
                return login.status(*connection.get())
            return fn(authenticated_owner(), *args, **kwargs)
        except MCPError as error:
            return error.payload
        except Exception:
            return {"status": "error", "code": "operation_failed", "message": "操作未完成；请检查字段或任务状态"}

    if login:
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
        def connect_account() -> dict[str, Any]:
            """首次配置时获取网页登录授权链接；完成后再调用确认。已授权会直接返回当前账号，无需重新登录。"""
            identity = connection.get()
            if identity is None:
                return {"status": "legacy", "message": "旧运维连接，请使用已配置的 SSH stdio 连接完成本人授权。"}
            return login.status(*identity)

        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
        def disconnect_account() -> dict[str, Any]:
            """用户要求退出/解绑 MCP 时撤销当前连接授权；下次必须重新登录授权。不会删除行程。"""
            identity = connection.get()
            if identity:
                login.revoke(identity[0])
            return {"status": "disconnected"}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
    def prepare_trip(profile: dict, request_id: str, job_id: str | None = None,
                     origin_query: str | None = None) -> dict[str, Any]:
        """准备画像和有限候选，不生成行程。days/budget/start_date须用户确认；hotel坐标为BD-09。
        profile.city为城市键（如hangzhou），只能是已启用城市，默认hangzhou；一次行程只在一个城市内。
        起点不明确时用origin_query返回当前城市的起点选项，用户选择后以同job_id重新prepare；不可猜坐标。
        request_id为8–100字符，重试复用；改变内容须新request_id。缺字段会给profile_schema。
        """
        return invoke(service.prepare, profile, request_id, job_id, origin_query)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
    def generate_trip(job_id: str, preparation_revision: int, candidate_set_id: str,
                      selections: list[ExternalSelection], request_id: str) -> dict[str, Any]:
        """将宿主选择的景点交给确定性排程，立即返回job_id。只选本次候选ID，必须包含必去。
        不填写时间；priority越大越优先。之后用get_job查询；断开连接不取消、不重复生成。
        """
        return invoke(service.generate, job_id, preparation_revision, candidate_set_id,
                      [s.model_dump() for s in selections], request_id)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    def get_job(job_id: str) -> dict[str, Any]:
        """只读进度/缺口/冲突/最终结果，不触发模型或高德。后台仍运行时不要再generate。"""
        return invoke(service.get_job, job_id)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    def get_trip(trip_id: str, expected_revision: int | None = None) -> dict[str, Any]:
        """读取本人plan_id或history_id；历史会话过期只读，不自动重新规划。"""
        return invoke(service.get_trip, trip_id, expected_revision)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
    def preview_meal_change(trip_id: str, base_revision: int, edit: MealEditPreviewRequest,
                            request_id: str) -> dict[str, Any]:
        """预览替换当前餐次的备选餐厅。edit.revision须等于base_revision；不写历史。
        展示变化和冲突，取得用户明确确认后才可调用confirm_meal_change。
        """
        if edit.revision != base_revision:
            return {"status": "error", "code": "stale_revision", "message": "预览版本须与base_revision一致"}
        return invoke(service.preview, trip_id, base_revision, edit.model_dump(exclude={"revision"}), request_id)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
    def confirm_meal_change(trip_id: str, base_revision: int, preview_id: str, request_id: str) -> dict[str, Any]:
        """仅在用户明确确认预览后提交一次，更新网页同一行程。过期/旧版本必须重新预览。
        丢失响应应使用相同request_id重试，不能用新ID重放旧preview。
        """
        return invoke(service.confirm, trip_id, base_revision, preview_id, request_id)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
    def cancel_job(job_id: str) -> dict[str, Any]:
        """取消本人未完成任务，阻止后续请求和保存；不删除已有历史。"""
        return invoke(service.cancel, job_id)

    app = mcp.streamable_http_app()

    class PrivateMCP:
        router = app.router

        async def __call__(self, scope, receive, send):
            if scope["type"] == "lifespan":
                return await app(scope, receive, send)
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            auth = headers.get(b"authorization", b"")
            identity = None
            request_owner = owner
            if login and hmac.compare_digest(headers.get(b"x-travel-transport", b""), credential.encode()):
                raw = auth.removeprefix(b"Bearer ").decode("ascii", errors="ignore")
                label = headers.get(b"x-travel-client", b"travel-mcp").decode("ascii", errors="ignore")[:64]
                if not auth.startswith(b"Bearer ") or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", raw):
                    return await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                identity = (raw, label)
                user = login.user(raw)
                request_owner = f"user:{user['id']}" if user else None
            elif login is not None or not hmac.compare_digest(auth, f"Bearer {credential}".encode()):
                return await JSONResponse({"error": "unauthorized"}, status_code=401,
                                          headers={"Cache-Control": "no-store"})(scope, receive, send)
            token = principal.set(request_owner)
            context_token = connection.set(identity)
            try:
                await app(scope, receive, send)
            finally:
                connection.reset(context_token)
                principal.reset(token)

    return PrivateMCP()
