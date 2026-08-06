# 仓库指南

## 项目结构与模块组织
本仓库是制造行业生产缺陷本体构建平台，包含独立的后端和前端应用。

- `backend/` 是 FastAPI 服务，入口文件为 `backend/app/main.py`。
- `backend/app/api/` 按功能划分路由模块，例如本体、映射、DDL、源数据和系统管理。`core/`、`models/`、`schemas/`、`services/` 分别存放配置、持久化模型、接口 Schema 和业务逻辑。
- `backend/ontology_platform.db` 是本地 SQLite 开发数据库。
- `frontend/` 是 Vue 3 + TypeScript + Vite 客户端。
- `frontend/src/views/` 存放页面级 Vue 组件；`api/`、`router/`、`stores/`、`assets/` 存放共享客户端代码和静态资源。
- 根目录中文 Markdown 文件是系统功能需求规范书。

## 构建、测试与开发命令
后端初始化和本地运行：

```sh
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

前端常用命令：

```sh
cd frontend
npm install
npm run dev
npm run build
npm run preview
```

`npm run dev` 启动 Vite 开发服务。`npm run build` 使用 `vue-tsc` 做类型检查并构建生产资源。`npm run preview` 在本地预览构建结果。

## 代码风格与命名规范
Python 代码遵循 Python 3 风格，使用 4 空格缩进。接口模型使用 Pydantic Schema，数据库模型使用 SQLAlchemy，业务逻辑按功能放入 service 模块。FastAPI 路由应按领域分组。

Vue 代码优先使用单文件组件，并在合适场景使用 `<script setup lang="ts">`。页面组件使用 PascalCase 命名，例如 `OntologyBuild.vue`。前端实现应沿用现有 Element Plus、Pinia、Axios 和 Vue Router 写法。

## 测试规范
当前仓库尚未包含项目自有自动化测试。新增后端测试时，放在 `backend/tests/`，文件名示例为 `test_ontology.py`。新增前端测试时，放在对应功能附近或 `frontend/src/__tests__/`，文件名示例为 `OntologyBuild.spec.ts`。提交前端改动前应运行 `npm run build`；引入 Python 测试框架后，应同步补充后端测试命令。

## 经验沉淀与阶段总结
项目中发生重大代码变更，或完成一个重要阶段时，必须在 `experiences/` 目录下按天记录总结，文件名使用 `YYYY-MM-DD.md`，同一天持续追加即可。总结要详细写明背景、改动文件列表、验证结果、遗留问题与下一步建议。后续继续开发
前，先阅读 `experiences/` 中最新的记录，基于最近结论继续推进，避免重复做前面已经完成的排查、试验和工具操作>。如果目录不存在，先创建再记录。


## 提交与 Pull Request 规范
当前仓库还没有提交历史，因此尚未形成本地提交约定。建议使用简洁的祈使句提交信息，例如 `Add ontology browse API` 或 `Fix datasource form validation`。

Pull Request 应包含变更摘要、已执行的测试、影响的后端或前端范围、相关 issue（如有）以及可见 UI 改动的截图。涉及数据库或配置变更时需要明确说明。

## 安全与配置提示
不要提交真实凭据、令牌或生产数据库文件。`backend/app/main.py` 中的默认本地管理员账号仅用于开发环境。
