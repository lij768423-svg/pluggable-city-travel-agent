# 运行数据

除本文件外，本目录内容不提交。

在 `backend/` 执行 `uv run python scripts/fetch_data.py --city <key>` 下载 ChinaTravel Sandbox 并按城市筛选，写入 `<key>/`（已接入的 10 座城市：beijing、shanghai、guangzhou、shenzhen、chengdu、chongqing、wuhan、hangzhou、nanjing、suzhou）。接入新城市用 `scripts/onboard_city.py --name <城市名>`，会一并生成该城的模型标签、停留参考和交通标定。数据适用 CC BY 4.0，详见根目录 THIRD_PARTY_NOTICES.md。

可选的 `scripts/tag_pois.py` 使用自己的模型账号生成增强标签。账号数据库、历史、模型响应缓存及生成结果属于运行数据，不能随公开源码分发。可用 `TTA_DATA_DIR` 指定独立持久化目录。
