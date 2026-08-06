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

## 架构

```text
┌─────────────────────────────────────────────────────────────┐
│ Vue 3 + TypeScript + Vite + Element Plus                    │
│ 业务建模 │ 数据映射 │ 图谱浏览/查询 │ 智能体 │ 系统管理       │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP / JSON
┌───────────────────────────▼─────────────────────────────────┐
│ FastAPI + SQLAlchemy + Pydantic                              │
│ 本体 │ 映射 │ DDL │ Oracle Property Graph │ LLM │ 鉴权       │
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

## 快速开始

### 前置条件

- Python 3.11+（建议使用与项目一致的 Python 版本）
- Node.js 20+
- 可访问的 Oracle 数据库；如使用图谱浏览，需要目标库支持 Oracle Property Graph

### 1. 配置后端

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

默认地址为 `http://127.0.0.1:8000`；健康检查接口为 `GET /health`，OpenAPI 文档为 `http://127.0.0.1:8000/api/v1/docs`。

### 2. 启动前端

另开一个终端：

```sh
cd frontend
npm install
npm run dev
```

打开 Vite 输出的本地地址（通常为 `http://localhost:5173`）。

### 3. 推荐使用流程

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
