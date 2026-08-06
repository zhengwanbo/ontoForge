# ontoForge

**面向制造缺陷分析的本体构建与知识图谱平台。**

ontoForge 将制造现场的源数据、业务语义、本体模型和 Oracle Property Graph 串联起来，帮助团队从数据表出发，完成业务对象建模、字段映射、图谱部署、图数据查询以及面向场景的智能体技能配置。

> 当前实现面向 Oracle 数据库与制造缺陷追溯场景；其中 Property Graph 浏览依赖目标 Oracle 环境已部署相应图谱并具有数据字典读取权限。

## 核心能力

- **源数据与数据源管理**：维护 Oracle 数据源，浏览 Schema、表和字段，并支持数据对象标注。
- **业务对象构建**：管理业务分析域，构建实体、属性、关系、业务流程、活动与业务规则。
- **智能化本体引导**：可基于文档、DDL 和规则数据解析/生成本体构建引导，并支持自然语言调整。
- **数据映射**：配置实体、属性与关系映射；提供自动映射、批量应用、边 SQL 预览、确认与任务追踪。
- **DDL 生成与部署**：生成并执行 Oracle DDL，将本体映射为节点、边和 Property Graph；对未确认映射采取安全降级，降低部署失败风险。
- **图谱浏览与查询**：直接读取 Oracle Property Graph 元数据展示拓扑，支持图数据查询。
- **智能体构建**：围绕业务域、数据源和 Property Graph 创建、管理及测试智能体技能。
- **平台管理**：提供登录鉴权、用户管理、LLM 配置、数据源连通性测试与操作日志。

## 前后端技术架构

### 前端：交互与可视化层

`frontend/` 是基于 Vue 3 的单页应用，负责业务建模、映射编辑、图谱可视化和系统配置等交互。

| 模块 | 实现与职责 |
| --- | --- |
| 页面与组件 | Vue 3 SFC + TypeScript；`src/views/` 按源数据、业务建模、映射、DDL、图谱浏览、智能体和系统管理分组 |
| 路由与权限 | Vue Router；路由守卫根据浏览器中的 JWT 判断登录态 |
| 状态与界面 | Pinia 管理共享状态，Element Plus 提供管理端 UI 组件 |
| 图与编辑器 | Vue Flow 展示/编辑图谱关系，CodeMirror 用于 SQL 编辑与预览 |
| 服务访问 | Axios 统一封装；浏览器请求使用相对路径 `/api/v1`，自动附带 `Authorization: Bearer <JWT>` |

### 后端：领域服务与接口层

`backend/` 是 FastAPI 服务，向前端提供 REST API，并编排本体、映射、DDL、图查询和智能体等领域能力。

| 模块 | 实现与职责 |
| --- | --- |
| API | `app/api/` 按业务域拆分路由，统一挂载在 `/api/v1` |
| 服务 | `app/services/` 实现本体生成、数据映射、DDL 生成/执行、源数据访问、LLM 调用和智能体技能逻辑 |
| 数据模型 | SQLAlchemy 管理平台元数据；Pydantic 负责请求、响应与校验模型 |
| 安全与运行 | JWT 鉴权、bcrypt 密码哈希、CORS、请求日志；Uvicorn 作为 ASGI 服务进程 |

### 数据与集成层

- **Oracle 平台库**：保存业务域、本体、映射、DDL 日志、用户和系统配置等平台元数据。
- **Oracle 源数据与 Property Graph**：读取制造源表；根据已确认映射生成节点/边并部署 Property Graph；图谱浏览从 Oracle `ALL_PG_*` 数据字典视图读取拓扑。
- **LLM 服务**：由系统管理中的 LLM 配置接入，用于对象标注、本体引导和映射辅助；密钥应仅保存在数据库或受控环境配置中。

### 运行时调用链

```text
┌─────────────────────────────────────────────────────────────┐
│ 浏览器：Vue 3 + TypeScript + Vite + Element Plus             │
│ 业务建模 │ 数据映射 │ 图谱浏览/查询 │ 智能体 │ 系统管理       │
└───────────────────────────┬─────────────────────────────────┘
                            │ /api/v1 · HTTP / JSON · JWT
┌───────────────────────────▼─────────────────────────────────┐
│ FastAPI：路由 │ 服务 │ SQLAlchemy │ Pydantic │ 鉴权           │
│ 本体 │ 映射 │ DDL │ Oracle Property Graph │ LLM             │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│ Oracle Database                                               │
│ 平台元数据 │ 源数据 │ 节点/边表 │ Oracle Property Graph      │
└─────────────────────────────────────────────────────────────┘
```

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Vite、Element Plus、Pinia、Vue Router、Vue Flow、CodeMirror |
| 后端 | Python、FastAPI、SQLAlchemy、Pydantic、Uvicorn |
| 数据库 | Oracle Database、python-oracledb、Oracle Property Graph |
| AI 集成 | OpenAI Python SDK（通过平台 LLM 配置使用） |

## 本地运行：前后端联调

### 前置条件

- Python 3.11+（建议使用与项目一致的 Python 版本）
- Node.js 20+
- 可访问的 Oracle 数据库；如使用图谱浏览，需要目标库支持 Oracle Property Graph

### 1. 准备 Oracle 数据库

请先准备一个可访问的 Oracle 服务，并确保运行平台的账号具有创建和读取平台元数据所需的权限。图谱浏览还需要当前数据源账号能够读取 `ALL_PG_*` Property Graph 数据字典视图。

> 不建议使用 DBA 或生产超级管理员账号作为日常应用连接账号。

### 2. 配置并启动后端

```sh
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

在 `backend/.env` 中配置本地环境（该文件不会提交）：

```dotenv
DATABASE_URL=oracle+oracledb://<username>:<password>@<host>:1521/<service_name>
JWT_SECRET_KEY=<replace-with-a-strong-random-secret>
CORS_ORIGINS=["http://localhost:5173"]
```

启动 API：

```sh
uvicorn app.main:app --reload
```

后端启动后会监听 `http://127.0.0.1:8000`：

- 健康检查：`GET http://127.0.0.1:8000/health`
- OpenAPI 文档：`http://127.0.0.1:8000/api/v1/docs`
- API 基础路径：`http://127.0.0.1:8000/api/v1`

### 3. 配置并启动前端

另开一个终端：

```sh
cd frontend
npm install
npm run dev
```

前端默认运行在 `http://localhost:5173`。Vite 已配置开发代理：

```text
浏览器 http://localhost:5173/api/v1/*
  └─ Vite 开发服务器代理
       └─ http://localhost:8000/api/v1/*
```

因此，本地开发时无需在前端另行设置 API 地址；请保持后端运行在 `8000` 端口。若修改后端端口，则同步修改 `frontend/vite.config.ts` 中 `/api` 的代理目标，并将新前端地址加入后端 `CORS_ORIGINS`。

### 4. 联调检查

1. 在浏览器打开 `http://localhost:5173`，应进入登录页。
2. 使用本地初始化账号登录后，浏览系统管理、业务对象构建等页面。
3. 打开浏览器开发者工具的 Network 面板，确认接口请求为 `/api/v1/...` 且返回 2xx。
4. 如访问失败，先检查 `http://127.0.0.1:8000/health`，再检查 Oracle 连接串、用户权限和前端代理端口。

### 5. 推荐使用流程

1. 在“系统管理”配置 Oracle 数据源和 LLM 服务。
2. 在“源数据管理”查看和标注数据对象。
3. 创建业务分析域，建立实体、属性、关系、流程和规则，或使用引导生成能力辅助建模。
4. 在“数据映射”确认实体、属性与关系的来源及 Join 条件。
5. 在“DDL 生成与应用”预览、生成并执行节点、边和 Property Graph DDL。
6. 在“本体图谱浏览”和“图数据查询”中验证已部署图谱；按需为业务域配置智能体技能。

## 开发与验证

前端构建（同时执行 TypeScript 类型检查）：

```sh
cd frontend
npm run build
```

后端测试示例：

```sh
cd backend
python -m unittest tests.test_auth tests.test_property_graph_topology
```

当前后端测试位于 `backend/tests/`。请在修改前端后执行 `npm run build`，在修改后端核心逻辑后补充或运行相应测试。

## 目录结构

```text
.
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI 路由
│   │   ├── core/         # 配置、数据库、鉴权与日志
│   │   ├── models/       # SQLAlchemy 模型
│   │   ├── schemas/      # Pydantic Schema
│   │   └── services/     # 本体、映射、DDL、源数据与智能体服务
│   ├── scripts/          # 运维与元数据同步脚本
│   └── tests/            # 后端测试
├── frontend/
│   └── src/
│       ├── api/          # 接口调用
│       ├── router/       # 前端路由
│       ├── stores/       # Pinia 状态
│       └── views/        # 页面组件
├── docs/                 # 设计与使用文档
└── experiences/          # 阶段记录
```

## 安全提示

- 不要提交 `.env`、数据库文件、访问令牌或生产凭据。
- 请在部署前替换 JWT 密钥，并为 Oracle 连接使用权限最小化的专用账号。
- 开发环境会初始化本地管理员账号；首次登录后应立即修改密码，生产环境请使用受控的账号初始化与密码策略。
- DDL 执行会创建或重建目标库对象，请先在非生产环境验证映射和生成结果。

## 许可与贡献

当前仓库尚未声明开源许可证。提交贡献前，请先与项目维护者确认使用和分发方式。
