import axios from 'axios'
import { ElMessage } from 'element-plus'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// Request interceptor - add JWT token
api.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Response interceptor
api.interceptors.response.use(
  response => {
    if (response.config.responseType === 'blob') return response
    const res = response.data
    if (res.code !== 200) {
      ElMessage.error(res.message || '请求失败')
      return Promise.reject(new Error(res.message))
    }
    return res
  },
  error => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    } else {
      const detail = error.response?.data?.detail
      ElMessage.error(
        typeof detail === 'string'
          ? detail
          : (detail?.message || error.message || '网络错误')
      )
    }
    return Promise.reject(error)
  }
)

// ====== Auth API ======
export const authApi = {
  login: (data: { username: string; password: string }) => api.post('/system/auth/login', data),
  changePassword: (data: any) => api.put('/system/auth/password', data)
}

// ====== Source Data API ======
export const sourceApi = {
  listDataSources: (domainId?: string) => api.get('/source/datasources', { params: { domain_id: domainId } }),
  getSchemas: (sourceId: string) => api.get(`/source/datasources/${sourceId}/schemas`),
  getRemoteTables: (sourceId: string, params?: { schema?: string; prefix?: string; search?: string }) =>
    api.get(`/source/datasources/${sourceId}/tables`, { params }),
  getRemoteTableDetail: (
    sourceId: string,
    tableName: string,
    params?: { schema?: string; sample_limit?: number }
  ) => api.get(`/source/datasources/${sourceId}/tables/${encodeURIComponent(tableName)}/detail`, { params }),
  generateObjectComments: (
    sourceId: string,
    tableName: string,
    data: {
      schema?: string
      sample_limit?: number
      primary_model_config_id?: string
      verifier_model_config_id?: string
    }
  ) => api.post(`/source/datasources/${sourceId}/tables/${encodeURIComponent(tableName)}/annotation/generate`, data),
  saveObjectComments: (
    sourceId: string,
    tableName: string,
    data: { schema?: string; table_comment?: string | null; column_comments: Array<{ column_name: string; comments: string }> }
  ) => api.post(`/source/datasources/${sourceId}/tables/${encodeURIComponent(tableName)}/annotation/save`, data),
  getGraphQueryRecommendations: (domainId: string, sourceId: string, params?: { schema?: string; graph_name?: string }) =>
    api.get('/source/graph-query/recommendations', { params: { domain_id: domainId, source_id: sourceId, ...params } }),
  generateGraphQueryRecommendations: (domainId: string, sourceId: string, params?: { schema?: string; graph_name?: string }) =>
    api.post('/source/graph-query/recommendations/generate', null, { params: { domain_id: domainId, source_id: sourceId, ...params }, timeout: 200000 }),
  executeGraphQuery: (data: { domain_id: string; source_id: string; schema?: string; graph_sql: string; row_limit: number }) =>
    api.post('/source/graph-query', data, { timeout: 120000 }),
}

export const datasourceApi = {
  list: (businessDomainId?: string) => api.get('/system/datasources', { params: { business_domain_id: businessDomainId } }),
  create: (data: any) => api.post('/system/datasources', data),
  update: (id: string, data: any) => api.put(`/system/datasources/${id}`, data),
  remove: (id: string) => api.delete(`/system/datasources/${id}`),
  test: (id: string) => api.post(`/system/datasources/${id}/test`),
  listTables: (id: string, params?: { schema?: string; search?: string }) => api.get(`/system/datasources/${id}/tables`, { params }),
  getTableColumns: (id: string, tableName: string, params?: { schema?: string }) =>
    api.get(`/system/datasources/${id}/tables/${encodeURIComponent(tableName)}/columns`, { params })
}

// ====== Domain API ======
export const domainApi = {
  list: (status?: string) => api.get('/domains', { params: { status } }),
  create: (data: any) => api.post('/domains', data),
  get: (id: string) => api.get(`/domains/${id}`),
  update: (id: string, data: any) => api.put(`/domains/${id}`, data),
  delete: (id: string) => api.delete(`/domains/${id}`)
}

export const dashboardApi = {
  getOverview: (domainId: string) => api.get(`/dashboard/domains/${domainId}/overview`),
}

// ====== Business Type Semantic API ======
export const businessTypeApi = {
  list: () => api.get('/business-types'),
  get: (code: string) => api.get(`/business-types/${encodeURIComponent(code)}`),
  create: (data: any) => api.post('/business-types', data),
  update: (code: string, data: any) => api.put(`/business-types/${encodeURIComponent(code)}`, data),
  delete: (code: string) => api.delete(`/business-types/${encodeURIComponent(code)}`)
}

// ====== Ontology Entity API ======
export const entityApi = {
  list: (domainId: string) => api.get(`/domains/${domainId}/entities`),
  create: (domainId: string, data: any) => api.post(`/domains/${domainId}/entities`, data),
  update: (id: string, data: any) => api.put(`/entities/${id}`, data),
  delete: (id: string) => api.delete(`/entities/${id}`),
  updatePosition: (id: string, position: any) => api.put(`/entities/${id}/position`, position)
}

// ====== Property API ======
export const propertyApi = {
  list: (entityId: string) => api.get(`/entities/${entityId}/properties`),
  create: (entityId: string, data: any) => api.post(`/entities/${entityId}/properties`, data),
  update: (id: string, data: any) => api.put(`/properties/${id}`, data),
  delete: (id: string) => api.delete(`/properties/${id}`)
}

// ====== Relation API ======
export const relationApi = {
  list: (domainId: string) => api.get(`/domains/${domainId}/relations`),
  create: (domainId: string, data: any) => api.post(`/domains/${domainId}/relations`, data),
  update: (id: string, data: any) => api.put(`/relations/${id}`, data),
  delete: (id: string) => api.delete(`/relations/${id}`)
}

// ====== Graph API ======
export const graphApi = {
  getOntologyGraph: (domainId: string) => api.get(`/domains/${domainId}/graph`),
  getOntologyBrowseGraph: (sourceId: string, graphName: string | undefined, domainId: string) =>
    api.get('/ontology/graph', { params: { source_id: sourceId, graph_name: graphName || undefined, domain_id: domainId } }),
  queryOntologyGraphInstances: (data: any) => api.post('/ontology/graph/instances', data),
  queryOntologyGraphInstanceLineage: (data: any) => api.post('/ontology/graph/instances/lineage', data),
  clearOntologyData: (domainId: string) => api.delete(`/domains/${domainId}/ontology-data`),
  generateOntologyGuide: (domainId: string, data: any) => api.post(`/domains/${domainId}/guide/generate`, data, {
    timeout: 900000
  }),
  listOntologyGuideBlueprints: (domainId: string) => api.get(`/domains/${domainId}/guide/blueprints`),
  applyOntologyGuide: (domainId: string, data: any) => api.post(`/domains/${domainId}/guide/apply`, data, {
    timeout: 300000
  }),
  naturalAdjustOntology: (domainId: string, data: any) => api.post(`/domains/${domainId}/guide/natural-adjust`, data, {
    timeout: 180000
  }),
  applyNaturalAdjustOntology: (domainId: string, data: any) => api.post(`/domains/${domainId}/guide/natural-adjust/apply`, data, {
    timeout: 180000
  }),
  parseOntologyGuideDocument: (domainId: string, file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post(`/domains/${domainId}/guide/parse-document`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  },
  parseOntologyGuideDDL: (domainId: string, file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post(`/domains/${domainId}/guide/parse-ddl`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  },
  parseOntologyGuideRuleData: (domainId: string, file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post(`/domains/${domainId}/guide/parse-rule-data`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  },
}

// ====== Mapping API ======
export const mappingApi = {
  getLatestBlueprint: (domainId: string) => api.get(`/mapping/domains/${domainId}/blueprint/latest`, {
    timeout: 300000
  }),
  getLatestDataSupportBlueprint: (domainId: string) => api.get(`/mapping/domains/${domainId}/blueprint/data-support`),
  checkDdlReadiness: (domainId: string) => api.post(`/mapping/domains/${domainId}/ddl-readiness-check`),
  getEntityMapping: (entityId: string) => api.get(`/mapping/entities/${entityId}/entity-mapping`),
  updateEntityMapping: (entityId: string, data: any) => api.put(`/mapping/entities/${entityId}/entity-mapping`, data),
  getEntitySemanticView: (entityId: string) => api.get(`/mapping/entities/${entityId}/semantic-view`),
  updateEntitySemanticView: (entityId: string, data: any) => api.put(`/mapping/entities/${entityId}/semantic-view`, data),
  suggestEntitySemanticView: (entityId: string, data: any) => api.post(`/mapping/entities/${entityId}/semantic-view/suggest`, data, { timeout: 300000 }),
  getPropertyMappings: (entityId: string) => api.get(`/mapping/entities/${entityId}/mappings`),
  updatePropertyMapping: (propertyId: string, data: any) => api.put(`/mapping/properties/${propertyId}/mapping`, data),
  getRelationMapping: (relationId: string) => api.get(`/mapping/relations/${relationId}/mapping`),
  analyzeRelationJoin: (relationId: string, data: any) => api.post(`/mapping/relations/${relationId}/join-analysis`, data),
  createRelationMapping: (relationId: string, data: any) => api.post(`/mapping/relations/${relationId}/mapping`, data),
  updateRelationMapping: (relationId: string, data: any) => api.put(`/mapping/relations/${relationId}/mapping`, data),
  previewRelationEdgeSql: (data: any) => api.post('/mapping/relations/edge-sql/preview', data),
  autoMapping: (entityId: string, data: any) => api.post(`/mapping/entities/${entityId}/auto-mapping`, data),
  bulkAutoMapping: (domainId: string, data: any) => api.post(`/mapping/domains/${domainId}/bulk-auto-mapping`, data, {
    timeout: 900000
  }),
  listTasks: (domainId: string) => api.get(`/mapping/domains/${domainId}/tasks`),
  clearTasks: (domainId: string) => api.delete(`/mapping/domains/${domainId}/tasks`),
  getTask: (taskId: string) => api.get(`/mapping/tasks/${taskId}`),
  bulkApplyMappings: (domainId: string, data: any) => api.post(`/mapping/domains/${domainId}/bulk-apply-mappings`, data, {
    timeout: 300000
  }),
  confirmMappings: (entityId: string, data: any) => api.post(`/mapping/entities/${entityId}/mappings/confirm`, data)
}

// ====== DDL API ======
export const ddlApi = {
  getContext: (domainId: string) => api.get(`/ddl/domains/${domainId}/context`),
  generate: (domainId: string) => api.post(`/ddl/domains/${domainId}/generate`, {}, {
    timeout: 300000
  }),
  execute: (domainId: string, data: any) => api.post(`/ddl/domains/${domainId}/execute`, data),
  getLogs: (domainId?: string) => api.get('/ddl/logs', { params: { domain_id: domainId } }),
  getExecutionStatus: (logId: string) => api.get(`/ddl/logs/${logId}/status`),
  getLogDetails: (logId: string) => api.get(`/ddl/logs/${logId}/details`)
}

// ====== Browse API ======
export const browseApi = {
  updateComments: (tableName: string, comments: string) => api.put(`/ontology/tables/${tableName}/comments`, { comments }),
  addColumn: (tableName: string, data: any) => api.post(`/ontology/tables/${tableName}/columns`, data),
  addRelation: (data: any) => api.post('/ontology/ontology/relations', data)
}

// ====== Agent Skill API ======
export const agentApi = {
  listSkills: (domainId?: string) => api.get('/agent/skills', { params: { domain_id: domainId } }),
  listManagedSkills: (domainId: string) => api.get('/agent/managed-skills', { params: { domain_id: domainId } }),
  uploadManagedSkill: (domainId: string, file: File) => {
    const formData = new FormData()
    formData.append('domain_id', domainId)
    formData.append('file', file)
    return api.post('/agent/managed-skills/upload', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  deleteManagedSkill: (managedSkillId: string, domainId: string) => api.delete(`/agent/managed-skills/${managedSkillId}`, { params: { domain_id: domainId } }),
  listManagedSkillTestSessions: () => api.get('/agent/managed-skill-test-sessions'),
  getManagedSkillTestSession: (sessionId: string) => api.get(`/agent/managed-skill-test-sessions/${sessionId}`),
  streamManagedSkillTestEvents: async (
    sessionId: string,
    options: {
      turnNo?: number
      delayMs?: number
      signal?: AbortSignal
      onEvent: (eventName: string, payload: any) => void
    }
  ) => {
    const params = new URLSearchParams()
    if (options.turnNo != null) params.set('turn_no', String(options.turnNo))
    if (options.delayMs != null) params.set('delay_ms', String(options.delayMs))
    const token = localStorage.getItem('token')
    const response = await fetch(`/api/v1/agent/managed-skill-test-sessions/${sessionId}/events?${params.toString()}`, {
      method: 'GET',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: options.signal
    })
    if (!response.ok) {
      let message = '事件流读取失败'
      try {
        const data = await response.json()
        message = data?.detail || data?.message || message
      } catch (_error) {}
      throw new Error(message)
    }
    const reader = response.body?.getReader()
    const decoder = new TextDecoder('utf-8')
    if (!reader) return
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const block of parts) {
        const lines = block.split('\n')
        let eventName = 'message'
        const dataLines: string[] = []
        for (const line of lines) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim()
          if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
        }
        if (!dataLines.length) continue
        try {
          options.onEvent(eventName, JSON.parse(dataLines.join('\n')))
        } catch (_error) {
          options.onEvent(eventName, dataLines.join('\n'))
        }
      }
    }
  },
  streamManagedSkillTestTurn: async (
    managedSkillId: string,
    data: any,
    options: {
      signal?: AbortSignal
      onEvent: (eventName: string, payload: any) => void
    }
  ) => {
    const token = localStorage.getItem('token')
    const response = await fetch(`/api/v1/agent/managed-skills/${managedSkillId}/test/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {})
      },
      body: JSON.stringify(data),
      signal: options.signal
    })
    if (!response.ok) {
      let message = '流式测试失败'
      try {
        const data = await response.json()
        message = data?.detail || data?.message || message
      } catch (_error) {}
      throw new Error(message)
    }
    const reader = response.body?.getReader()
    const decoder = new TextDecoder('utf-8')
    if (!reader) return
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const block of parts) {
        const lines = block.split('\n')
        let eventName = 'message'
        const dataLines: string[] = []
        for (const line of lines) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim()
          if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
        }
        if (!dataLines.length) continue
        try {
          options.onEvent(eventName, JSON.parse(dataLines.join('\n')))
        } catch (_error) {
          options.onEvent(eventName, dataLines.join('\n'))
        }
      }
    }
  },
  startManagedSkillTestSession: (managedSkillId: string, data: any) => api.post(`/agent/managed-skills/${managedSkillId}/test-session/start`, data),
  testManagedSkill: (managedSkillId: string, data: any) => api.post(`/agent/managed-skills/${managedSkillId}/test`, data),
  listPropertyGraphs: (domainId: string, sourceId: string, schema?: string) =>
    api.get(`/agent/domains/${domainId}/property-graphs`, { params: { source_id: sourceId, schema } }),
  getAnalysisSemantics: (domainId: string) => api.get(`/agent/domains/${domainId}/analysis-semantics`),
  getSkill: (skillId: string) => api.get(`/agent/skills/${skillId}`),
  createSkill: (domainId: string, data: any) => api.post(`/agent/domains/${domainId}/skills`, data),
  updateSkill: (skillId: string, data: any) => api.put(`/agent/skills/${skillId}`, data),
  deleteSkill: (skillId: string) => api.delete(`/agent/skills/${skillId}`),
  downloadSkillPackage: (skillId: string) => api.post(`/agent/skills/${skillId}/package`, {}, { responseType: 'blob', timeout: 300000 }),
  testSkill: (skillId: string, data: any) => api.post(`/agent/skills/${skillId}/test`, data)
}

// ====== Process API ======
export const processApi = {
  list: (domainId: string) => api.get(`/processes/domains/${domainId}/processes`),
  create: (domainId: string, data: any) => api.post(`/processes/domains/${domainId}/processes`, data),
  generateGuide: (domainId: string, data: any) => api.post(`/processes/domains/${domainId}/guide/generate`, data, {
    timeout: 180000
  }),
  update: (id: string, data: any) => api.put(`/processes/${id}`, data),
  delete: (id: string) => api.delete(`/processes/${id}`)
}

// ====== Business Rules & Activities API ======
export const businessRuleApi = {
  catalog: (domainId: string) => api.get(`/business-rules/domains/${domainId}/catalog`),
  listMetrics: (domainId: string) => api.get(`/business-rules/domains/${domainId}/metrics`),
  createMetric: (domainId: string, data: any) => api.post(`/business-rules/domains/${domainId}/metrics`, data),
  updateMetric: (id: string, data: any) => api.put(`/business-rules/metrics/${id}`, data),
  deleteMetric: (id: string) => api.delete(`/business-rules/metrics/${id}`),
  listActivities: (domainId: string) => api.get(`/business-rules/domains/${domainId}/activities`),
  createActivity: (domainId: string, data: any) => api.post(`/business-rules/domains/${domainId}/activities`, data),
  updateActivity: (id: string, data: any) => api.put(`/business-rules/activities/${id}`, data),
  deleteActivity: (id: string) => api.delete(`/business-rules/activities/${id}`),
  listRules: (domainId: string) => api.get(`/business-rules/domains/${domainId}/rules`),
  createRule: (domainId: string, data: any) => api.post(`/business-rules/domains/${domainId}/rules`, data),
  updateRule: (id: string, data: any) => api.put(`/business-rules/rules/${id}`, data),
  deleteRule: (id: string) => api.delete(`/business-rules/rules/${id}`)
}

// ====== System API ======
export const systemApi = {
  getSystemInfo: () => api.get('/system/info'),
  getUsers: () => api.get('/system/users'),
  createUser: (data: any) => api.post('/system/users', data),
  updateUser: (id: string, data: any) => api.put(`/system/users/${id}`, data),
  deleteUser: (id: string) => api.delete(`/system/users/${id}`),
  getLLMConfigs: () => api.get('/system/llm-configs'),
  createLLMConfig: (data: any) => api.post('/system/llm-configs', data),
  updateLLMConfig: (id: string, data: any) => api.put(`/system/llm-configs/${id}`, data),
  deleteLLMConfig: (id: string) => api.delete(`/system/llm-configs/${id}`),
  testLLMConfig: (id: string) => api.post(`/system/llm-configs/${id}/test`),
  getOperationLogs: () => api.get('/system/operation-logs')
}

export default api
