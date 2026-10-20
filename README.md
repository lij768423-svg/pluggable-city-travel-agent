# 行记 · City Travel Agent

面向杭州、南京 1–5 天旅行的个性化行程规划系统，城市按配置接入（`backend/app/assets/cities.json`）。本科毕业设计项目：**基于大模型 Agent 的个性化旅游行程规划系统设计与实现**。

大模型负责理解需求和挑选地点，调度算法安排时间与顺序，规则校验器检查预算、交通、用餐和游玩约束。用户可以在问卷中选择偏好，查看真实规划步骤，比较备选餐厅，并确认换餐后的单日重规划。

这是研究与原型项目。通过程序校验代表满足系统已知约束，不等于真实世界中一定可行；营业、预约、交通、价格和饮食限制仍需确认。

## 功能

- 四步偏好问卷：天数、同行人、起点、兴趣、节奏、餐饮、预算及特别要求。
- 景点使用本地基础库和人工修订；餐饮综合本地数据与可选的高德在线查询，展示来源及候选降级提示。
- 按天调度，检查跨区域交通、停留时间、午晚餐、晚间活动及步行负担；有界修复不满足约束的安排。
- 浅色桌面与手机界面，真实道路路径、地图定位、地点详情、来源评分与图片注册表。
- 可打断的地图移动、实际阶段计数、可展开的模型输出；调用次数、缓存命中和 token 使用分别统计。
- 登录、按账号保存的行程历史、餐厅替换确认和撤销。
- 规则与 LLM 选点对照、攻略辅助选点和可选 Jev 实验接口。

## 架构

```text
Vue 3 / TypeScript / Vite
             │ JSON + NDJSON stream
FastAPI → LangGraph
             │
需求解析 → 候选检索 → 选点 → 聚类与 OR-Tools 调度
                               ↓
                         餐饮与晚间活动
                               ↓
                       规则校验 → 有界修复
                               ↓
                       行程 / 地图 / 账号历史
```

主要目录：`backend/app/nodes/` 为规划节点，`backend/app/llm.py` 为统一模型入口，`backend/tests/` 为回归测试，`backend/eval/` 为评测工具，`frontend/src/` 为界面。

## 本地启动

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/)、Node.js 22 和 npm。模型、高德和 Jev 使用自己的服务账号；无 API key 也可运行测试，在线生成需要配置可用模型。

```bash
git clone https://github.com/lij768423-svg/thesis-travel-agent.git
cd thesis-travel-agent

cd backend
cp .env.example .env
uv sync --frozen
uv run python scripts/fetch_data.py
uv run python scripts/fetch_data.py --city nanjing
```

编辑 `backend/.env`，至少设置 `LLM_API_KEY`。默认使用 `MiniMax-M3.1-Flash-Preview` 与 `LLM_THINKING=adaptive`；该模型标识是否可用取决于服务商账号。使用其他 OpenAI 兼容服务时，同时设置 `LLM_BASE_URL` 和 `LLM_MODEL`，并根据服务商支持情况将 `LLM_THINKING` 设为空或 `disabled`。

| 配置 | 用途 |
| --- | --- |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | 服务端模型接口 |
| `LLM_THINKING` | MiniMax 思考配置；其他服务需核对兼容性 |
| `AMAP_WEB_KEY` | 服务端路线与在线地点查询；未配置时使用估算 |
| `AMAP_JS_KEY` / `AMAP_JS_SECURITY` | 高德网页地图及服务端安全代理 |
| `TYPESAFE_API_KEY` | 可选 Jev 快速选点实验；默认无需配置 |
| `TTA_DATA_DIR` | 可选运行数据目录，默认仓库的 `data/` |

只有高德网页 JS key 会按地图 SDK 要求提供给浏览器，应配置域名限制；模型 key、Web 服务 key 和安全密钥只在后端使用。不要把 `.env`、账号数据库或模型缓存提交到 Git。

接着构建界面并启动服务：

```bash
cd ../frontend
npm ci
npm run build
cd ../backend
uv run uvicorn app.api:app --host 127.0.0.1 --port 18600
```

打开 `http://127.0.0.1:18600/`。开发前端可在另一个终端进入 `frontend/` 执行 `npm run dev`；Vite 代理 `/api` 到后端。

可选的 `uv run python scripts/tag_pois.py` 会调用模型生成景点增强标签并缓存，可能产生费用。基础数据下载不调用模型。详情图库注册表随源码为空；项目提供管理代码，但不提供每家餐厅的图片包。

## 数据与许可

基础景点、餐厅、住宿表由脚本从 ChinaTravel Sandbox 下载后按城市（杭州、南京）筛选，并不随仓库发布。它们是历史数据快照。在线餐饮和路线是外部服务返回，也不保证所有字段实时或完整。

公开版攻略包含 **11 条 Wikivoyage 改编的结构化样本**，保留来源、日期、地点映射与 CC BY-SA 4.0 声明。内部研究使用的其他来源不在公开分发中；这里没有小红书抓取数据，也没有训练好的微调模型。攻略辅助选点，时间表仍由调度器计算。

原始代码及本文档使用 [MIT](LICENSE)。攻略、照片及基础数据适用各自许可证，不因代码开源变为 MIT；详见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 验证与评测

完成基础数据下载后：

```bash
cd backend
uv run pytest -q
cd ../frontend
npm run build
```

回归测试使用模拟模型和路由，不需要真实模型或高德凭据。测试通过不能代替用户盲评或真实路线核验。`backend/eval/` 保留用例与评测工具；严格离线评测需自行准备对应模型响应快照。未提供历史评测数值作为当前版本效果结论。

`PUBLIC_RELEASE.json` 记录导出来源版本、公开版数据裁剪和文件 SHA-256，描述首次发布快照；后续贡献以 Git 历史为准。

## 部署边界

默认配置面向本机开发。向公网部署时应使用 HTTPS，配置 `TTA_PUBLIC_HOSTS`（逗号分隔的实际域名）以隐藏本地停留时长核对页面/API，按需要启用 `TTA_REQUIRE_LOGIN=1`、`TTA_RATE_LIMIT=1`、`TTA_PLAN_LIMIT_USER`、`TTA_PLAN_LIMIT_IP` 和 `TTA_INVITE_CODES`。数值配额默认 0 表示不限。

账号与历史存储在运行数据目录，请使用持久化卷并做好独立备份。代理转发的客户端地址必须来自可信入口。默认部署不是多副本服务：部分限流计数和规划编辑会话存于进程内存；多进程、多实例和重启后的编辑恢复需要额外设计。

欢迎通过 Issue 报告可复现的路线问题，或按 [贡献说明](CONTRIBUTING.md) 提交改进。
