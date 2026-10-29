# 行记 · 可插拔城市旅行规划 Agent（Web + MCP）

**Xingji — a pluggable-city travel planning agent, served as a Web app and an MCP server.**

一套规划代码对应多座城市。新城市只要一条命令就能接入：`onboard_city.py --name 苏州`。不需要改代码。同一套规划能力有两个入口：

- **Web 端**：问卷、地图和行程页，由服务端模型理解需求并选点。
- **MCP 服务**：Claude、Codex 等聊天 AI 通过 MCP 工具调用。由聊天 AI 自己选点，服务端不调用语言模型。

目前已接入 ChinaTravel 数据集覆盖的全部 10 座城市：北京、上海、广州、深圳、成都、重庆、武汉、杭州、南京、苏州。本项目是本科毕业设计：**基于大模型 Agent 的个性化旅游行程规划系统设计与实现**。

这是研究与原型项目。通过程序校验，只代表满足系统已知的约束，不代表在真实世界中一定可行；营业时间、预约、交通、价格和饮食限制仍需自行确认。

## 为什么叫“可插拔”

```text
backend/app/assets/cities.json        城市注册表：编码、边界、默认起点、酒店区、夜游街、本地菜……
backend/app/assets/cities/<key>/      每城资产：景点审阅、停留参考、片区名
data/<key>/                           每城数据：景点/餐厅/住宿表、模型标签、交通标定（不入库）
```

- **只按配置运行**：规划、调度、校验、导航和前端都只读当前城市的配置，代码里没有写死某座城市。
- **城市隔离**：每份行程都绑定自己的城市和景点目录版本；景点编号只在本城目录内有效，不会串到别的城市。
- **启用哪些城市**：由环境变量 `TTA_CITIES` 决定（如 `hangzhou,nanjing,suzhou`）。不在列表里的城市，历史行程仍可查看，但不能新建。

### 一条命令接入新城市

```bash
cd backend
uv run python scripts/onboard_city.py --name 武汉 --dry-run   # 先看步骤与预计外部调用次数
uv run python scripts/onboard_city.py --name 武汉             # 依次跑完，可断点续跑
uv run python scripts/check_city.py --city wuhan             # 单独做完整性检查
```

| 步骤 | 自动完成 |
| --- | --- |
| config | 用高德行政区接口取编码、省份、中心和边界，起草城市配置 |
| data | 下载 ChinaTravel Sandbox 中该城的景点、餐厅、住宿 |
| assets | 用模型生成景点标签和停留时长参考 |
| derive | 推导默认起点、酒店区、夜游街和本地菜 |
| calibration | 分层采样实测路线，拟合该城的车程和车费 |
| areas | 片区自动聚类，由模型起草片区名 |
| review | 规则体检，能确定的直接修正，其余列入待核清单 |
| check | 检查配置、数据和资产是否齐全一致 |
| eval | 跑 20 个画像（起点按住宿分布自动选），过门槛才建议启用 |

规则体检会自动做这几件事：
- 坐标越界的地点和限时活动暂停推荐；
- 明显偏短的游玩时长按模型估计调长，只加不减；
- 大型主题乐园按整日游玩、园内用餐处理。

0 元门票的乐园、校园、疑似重复的条目只列入待核清单，不冒充人工核验。数据集覆盖北京、上海、南京、苏州、杭州、深圳、成都、武汉、广州、重庆 10 座城市；其他城市要用 `--key` 指定英文键，并自备同格式数据。

接一座城市大约要调用模型 130 次、高德 2300 次（以苏州为例）。

## 两个入口

### Web 端

- **问卷**：四步填写偏好，包括天数、同行人、起点、兴趣、节奏、餐饮、预算和特别要求；先选城市，每座城市有自己的品牌和默认起点。
- **说一句话也行**：可以不填问卷，直接说需求（如“周末带爸妈在杭州玩两天，别太累”）。规划前先列出理解的每一项，标明来源（你说的、你填的、推断、默认）；可以在表里改好后一次提交，也可以在「跟 AI 说」里用对话改。闭馆、必去相距太远排不进同一天等问题会先提示，并给出一键修改。
- **贴近实际的排法**：起点可以不定（从第一站开始，结束后不算回程）；同一景区里的多个条目（如故宫各殿、灵隐寺与飞来峰）合成一次游览，按景区的游览时长排；苏堤、外滩这类长堤长街从一头进、另一头出，下一段路从出口算。
- **规划过程**：可以看到实际执行的规划步骤和模型输出；模型调用次数、缓存命中和 token 用量分别统计。
- **行程查看**：真实道路路径、地图定位、地点详情、餐厅备选；换餐要先预览，确认后才生效，也可以撤销。
- **账号**：登录后按账号保存行程历史；公网部署可开启邀请码注册、每日次数和限流。

### MCP 服务

基于官方 Python SDK（`mcp` 1.x），使用标准 Streamable HTTP（`/mcp`）。共 9 个工具：

| 工具 | 作用 |
| --- | --- |
| `connect_account` / `disconnect_account` | 在浏览器里登录网站，授权或解绑；账号密码只在网站上输入 |
| `prepare_trip` | 校验需求并返回候选地点池（候选 ID） |
| `generate_trip` / `get_job` / `cancel_job` | 用聊天 AI 选定的候选生成行程，查询或取消任务 |
| `get_trip` | 读取行程（与网站同一账号、同一份数据） |
| `preview_meal_change` / `confirm_meal_change` | 先预览换餐，用户明确确认后才写入 |

- **分工**：聊天 AI 负责理解需求、挑选候选；服务端只负责排程、餐饮和校验，不调用语言模型。
- **结果状态**：`ready` 才是正式结果；`draft` 是待核实草稿，`data_unavailable` 不能当作成功。
- **限额**：每次规划有高德请求上限，同一需求重复提交不会重复执行。

## 架构

```text
        Web（Vue 3 / TypeScript）            MCP 客户端（聊天 AI 选点）
                 │ JSON + NDJSON                     │ Streamable HTTP
                 └──────── FastAPI 同一进程（端口分离）┘
                                   │
   城市注册表 → 需求理解与确认 → 候选检索 → 选点（服务端模型 / 外部 AI）
                                   ↓
            片区分天 → OR-Tools VRPTW 日内排序 → 餐饮与晚间活动
                                   ↓
                     规则校验 → 有界修复 → 行程 / 地图 / 账号历史
```

主要目录：

| 目录 | 内容 |
| --- | --- |
| `backend/app/nodes/` | 规划节点 |
| `backend/app/cities.py` | 城市配置 |
| `backend/app/mcp_*.py` | MCP 服务 |
| `backend/scripts/` | 数据与接入脚本 |
| `backend/tests/` | 回归测试 |
| `backend/eval/` | 评测工具 |
| `frontend/src/` | 界面 |

## 本地启动

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/)、Node.js 22 和 npm。模型和高德需要使用你自己的服务账号；没有 API key 也能运行测试，但在线生成行程需要配置一个可用的模型。

```bash
git clone https://github.com/lij768423-svg/pluggable-city-travel-agent.git
cd pluggable-city-travel-agent/backend
cp .env.example .env
uv sync --frozen
for city in beijing shanghai guangzhou shenzhen chengdu chongqing wuhan hangzhou nanjing suzhou; do uv run python scripts/fetch_data.py --city $city; done
```

国内网络可以在命令后加 `--mirror https://hf-mirror.com`。

编辑 `backend/.env`，至少设置 `LLM_API_KEY`：
- 默认模型是 `MiniMax-M3.1-Flash-Preview`，配合 `LLM_THINKING=adaptive`；这个模型标识能否使用，取决于你的服务商账号。
- 使用其他 OpenAI 兼容服务时，同时设置 `LLM_BASE_URL` 和 `LLM_MODEL`，并按服务商的支持情况把 `LLM_THINKING` 设为空或 `disabled`。

| 配置 | 用途 |
| --- | --- |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | 服务端模型接口（仅 Web 端选点使用） |
| `AMAP_WEB_KEY` | 服务端路线与在线地点查询；未配置时使用估算 |
| `AMAP_JS_KEY` / `AMAP_JS_SECURITY` | 高德网页地图及服务端安全代理 |
| `TTA_CITIES` | 启用的城市，逗号分隔，默认 `hangzhou` |
| `TTA_DATA_DIR` | 可选运行数据目录，默认仓库的 `data/` |

只有高德网页 JS key 会按地图 SDK 的要求提供给浏览器，请给它配置域名限制；模型 key、Web 服务 key 和安全密钥只在后端使用。

**启动 Web 端：**

```bash
cd ../frontend && npm ci && npm run build
cd ../backend
TTA_CITIES=beijing,shanghai,guangzhou,shenzhen,chengdu,chongqing,wuhan,hangzhou,nanjing,suzhou uv run uvicorn app.api:app --host 127.0.0.1 --port 18600
```

打开 `http://127.0.0.1:18600/`。开发前端时，在 `frontend/` 下执行 `npm run dev`。

**同时启动 Web 端和 MCP：** 两个入口在同一个进程里，用不同端口，都只监听本机。

```bash
# backend/.env 中配置 TTA_MCP_TOKEN（至少 24 位随机值）、TTA_MCP_USERNAME（已存在的网站账号）、TTA_MCP_SITE_URL
TTA_CITIES=beijing,shanghai,guangzhou,shenzhen,chengdu,chongqing,wuhan,hangzhou,nanjing,suzhou uv run python -m app.mcp_server --web-port 18600 --mcp-port 18602
```

MCP 客户端连接 `http://127.0.0.1:18602/mcp`，鉴权使用 Bearer 凭据或浏览器登录授权；Web 端口上的 `/mcp` 一律返回 404。远程客户端可以用 `scripts/mcp_stdio.py`，经 SSH 以 stdio 方式转接，凭据只留在服务器上。

## 数据与许可

- **基础数据**：景点、餐厅、住宿表由脚本从 ChinaTravel Sandbox（CC BY 4.0）下载后按城市筛选，不随仓库发布。它们是历史快照。
- **模型生成的数据**：标签、停留参考、交通标定由接入脚本用你自己的账号生成。
- **在线数据**：在线餐饮和路线来自外部服务返回，不保证所有字段实时、完整。
- **攻略**：公开版攻略只有 **11 条 Wikivoyage 改编的结构化样本**（CC BY-SA 4.0）。

原创代码及文档使用 [MIT](LICENSE) 许可。攻略、照片和基础数据适用各自的许可证，详见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 验证

```bash
cd backend && uv run pytest -q
cd ../frontend && npm run build
```

回归测试使用模拟的模型和路由，不需要真实的模型或高德凭据。缺少模型标签或交通标定的城市，相关检查会自动跳过。测试通过不能代替真实路线核验或用户评估。`PUBLIC_RELEASE.json` 记录了导出来源版本和文件 SHA-256。

## 部署边界

默认配置面向本机开发。部署到公网时：
- 使用 HTTPS；
- 配置 `TTA_PUBLIC_HOSTS`（逗号分隔的实际域名）；
- 按需启用 `TTA_REQUIRE_LOGIN=1`、`TTA_RATE_LIMIT=1`、每日次数上限和 `TTA_INVITE_CODES`；
- 账号与历史存在运行数据目录里，请使用持久化卷并做好备份。

默认部署不是多副本服务：部分限流计数和编辑会话保存在进程内存中。MCP 入口应只在私网或可信反代后开放。

欢迎通过 Issue 报告可复现的路线问题，也可以按 [贡献说明](CONTRIBUTING.md) 提交改进，新城市的接入经验也欢迎。

## 友情链接

- [LINUX DO](https://linux.do)：新的理想型社区。
