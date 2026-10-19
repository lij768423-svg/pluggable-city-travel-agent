"""客户端连接测试专用实例：合成身份、模拟路线、无真实模型/高德。"""
from pathlib import Path
import asyncio
import uvicorn
from app import config
from app.api import app
from app.mcp_domain import run_selected
from app.mcp_service import MCPService
from app.mcp_server import PortDispatcher, bind
from app.mcp_transport import create_mcp_app
from app.travel import Travel
from app.restaurants import RestaurantCatalog
import app.mcp_service as module

RUN = Path(__file__).resolve().parents[1] / 'eval/runs/mcp-20261018'
config.AMAP_WEB_KEY = ''
config.ONLINE_RESTAURANTS = False

def offline_run(profile, candidates, selections, report, **kwargs):
    return run_selected(profile, candidates, selections, report, route=Travel('estimate'),
                        catalog=RestaurantCatalog(online=False), use_solver=False, **kwargs)
module.run_selected = offline_run
service = MCPService(ledger_path=RUN / 'client-fixture.sqlite3')
mcp = create_mcp_app(service, 'user:client-fixture', 'test-only-credential-not-a-real-secret', port=18685)
application = PortDispatcher(app, mcp, 18684, 18685, close=service.close)
sockets = []
try:
    sockets = [bind(18684), bind(18685)]
    asyncio.run(uvicorn.Server(uvicorn.Config(application, access_log=False, log_level='warning')).serve(sockets=sockets))
finally:
    for s in sockets: s.close()
    service.close()
