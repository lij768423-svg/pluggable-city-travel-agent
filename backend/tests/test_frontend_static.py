"""前端静态文件：压缩传输、带哈希资源长期缓存，入口页不长期缓存。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import frontend_app


def test_frontend_assets_are_compressed_and_cached(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log('杭州行记');\n" * 400)
    (tmp_path / "index.html").write_text("<!doctype html><title>杭州行记</title>" + " " * 2000)
    app = FastAPI()
    app.mount("/", frontend_app(tmp_path), name="frontend")
    client = TestClient(app)
    asset = client.get("/assets/index-abc123.js", headers={"Accept-Encoding": "gzip"})
    assert asset.status_code == 200 and asset.headers["content-encoding"] == "gzip"
    assert "immutable" in asset.headers["cache-control"] and "杭州行记" in asset.text
    page = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert page.status_code == 200 and page.headers["content-encoding"] == "gzip"
    assert "immutable" not in page.headers.get("cache-control", "")
