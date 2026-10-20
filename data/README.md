# 运行数据

除本文件外，本目录内容不提交。

在 `backend/` 执行 `uv run python scripts/fetch_data.py` 下载 ChinaTravel Sandbox 并筛选杭州，写入 `hangzhou/`；加 `--city nanjing` 写入 `nanjing/`。数据适用 CC BY 4.0，详见根目录 THIRD_PARTY_NOTICES.md。

可选的 `scripts/tag_pois.py` 使用自己的模型账号生成增强标签。账号数据库、历史、模型响应缓存及生成结果属于运行数据，不能随公开源码分发。可用 `TTA_DATA_DIR` 指定独立持久化目录。
