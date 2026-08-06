import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
    meta: { requiresAuth: false }
  },
  {
    path: '/',
    name: 'Layout',
    component: () => import('../views/Layout.vue'),
    redirect: '/business/domains',
    meta: { requiresAuth: true },
    children: [
      {
        path: 'source',
        redirect: '/source/browse'
      },
      {
        path: 'source/browse',
        name: 'SourceBrowse',
        component: () => import('../views/source/SourceData.vue'),
        meta: { title: '数据浏览管理', icon: 'DataBoard', parentMenu: '源数据管理' }
      },
      {
        path: 'source/annotation',
        name: 'SourceAnnotation',
        component: () => import('../views/source/DataObjectAnnotation.vue'),
        meta: { title: '数据对象标注', icon: 'EditPen', parentMenu: '源数据管理' }
      },
      {
        path: 'business',
        redirect: '/business/domains',
        meta: { title: '业务对象构建', icon: 'Share', isGroup: true },
        children: [
          {
            path: 'domains',
            name: 'BusinessDomains',
            component: () => import('../views/business/BusinessDomainManage.vue'),
            meta: { title: '业务分析域管理', icon: 'CollectionTag', parentMenu: '业务对象构建' }
          },
          {
            path: 'ontology',
            name: 'BusinessOntology',
            component: () => import('../views/ontology/OntologyBuild.vue'),
            meta: { title: '本体关系构建', icon: 'Share', parentMenu: '业务对象构建', buildSection: 'graph' }
          },
          {
            path: 'process',
            name: 'BusinessProcess',
            component: () => import('../views/ontology/OntologyBuild.vue'),
            meta: { title: '业务流程构建', icon: 'Operation', parentMenu: '业务对象构建', buildSection: 'flow' }
          },
          {
            path: 'rules-activities',
            name: 'BusinessRulesActivities',
            component: () => import('../views/business/BusinessRulesActivities.vue'),
            meta: { title: '业务规则活动', icon: 'SetUp', parentMenu: '业务对象构建' }
          }
        ]
      },
      {
        path: 'ontology',
        redirect: '/business/ontology'
      },
      {
        path: 'domains',
        redirect: '/business/domains'
      },
      {
        path: 'process',
        redirect: '/business/process'
      },
      {
        path: 'mapping',
        redirect: '/mapping/operation',
        meta: { title: '数据映射', icon: 'Connection', isGroup: true },
        children: [
          {
            path: 'operation',
            name: 'MappingOperation',
            component: () => import('../views/mapping/DataMappingOperation.vue'),
            meta: { title: '数据映射操作', icon: 'MagicStick', parentMenu: '数据映射' }
          },
          {
            path: 'manage',
            name: 'MappingManage',
            component: () => import('../views/mapping/DataMapping.vue'),
            meta: { title: '数据映射管理', icon: 'Connection', parentMenu: '数据映射' }
          }
        ]
      },
      {
        path: 'ddl',
        name: 'DDL',
        component: () => import('../views/ddl/DDLManage.vue'),
        meta: { title: 'DDL生成与应用', icon: 'DocumentCopy' }
      },
      {
        path: 'browse',
        redirect: '/browse/ontology',
        meta: { title: '本体浏览管理', icon: 'View', isGroup: true },
        children: [
          {
            path: 'ontology',
            name: 'BrowseOntology',
            component: () => import('../views/browse/OntologyBrowse.vue'),
            meta: { title: '本体图谱浏览', icon: 'View', parentMenu: '本体浏览管理' }
          },
          {
            path: 'graph-query',
            name: 'GraphDataQuery',
            component: () => import('../views/browse/GraphDataQuery.vue'),
            meta: { title: '图数据查询', icon: 'Connection', parentMenu: '本体浏览管理' }
          },
        ]
      },
      {
        path: 'agent',
        redirect: '/agent/skills',
        meta: { title: '智能体构建', icon: 'Cpu', isGroup: true },
        children: [
          {
            path: 'skills',
            name: 'AgentSkillBuilder',
            component: () => import('../views/agent/SkillBuilder.vue'),
            meta: { title: '技能构建', icon: 'Tools', parentMenu: '智能体构建' }
          },
          {
            path: 'test',
            name: 'AgentSkillTest',
            component: () => import('../views/agent/AgentTest.vue'),
            meta: { title: '智能体测试', icon: 'Promotion', parentMenu: '智能体构建' }
          }
        ]
      },
      {
        path: 'system',
        redirect: '/system/datasource',
        meta: { title: '系统管理', icon: 'Setting', isGroup: true },
        children: [
          {
            path: 'datasource',
            name: 'SystemDataSource',
            component: () => import('../views/system/DataSource.vue'),
            meta: { title: '数据源管理', icon: 'Coin', parentMenu: '系统管理' }
          },
          {
            path: 'llm',
            name: 'SystemLLM',
            component: () => import('../views/system/SystemManage.vue'),
            meta: { title: '大模型管理', icon: 'Cpu', parentMenu: '系统管理' }
          },
          {
            path: 'users',
            name: 'SystemUsers',
            component: () => import('../views/system/SystemManage.vue'),
            meta: { title: '用户管理', icon: 'UserFilled', parentMenu: '系统管理' }
          },
          {
            path: 'logs',
            name: 'SystemLogs',
            component: () => import('../views/system/SystemManage.vue'),
            meta: { title: '操作日志', icon: 'Tickets', parentMenu: '系统管理' }
          }
        ]
      }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('token')
  if (to.meta.requiresAuth && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    next('/')
  } else {
    next()
  }
})

export default router
