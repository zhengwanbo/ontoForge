<template>
  <div class="agent-page">
    <div class="hero-panel">
      <div>
        <div class="hero-title">智能体技能构建</div>
        <div class="hero-desc">
          基于分析域、Oracle 源数据库和属性图（Property Graph），构建可复用的数据分析 skill。保存后的 skill 可直接用于图数据分析。
        </div>
      </div>
      <div class="hero-stats">
        <div class="stat-card">
          <div class="stat-value">{{ processes.length }}</div>
          <div class="stat-label">可用流程</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ propertyGraphs.length }}</div>
          <div class="stat-label">属性图对象</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ skills.length }}</div>
          <div class="stat-label">已建技能</div>
        </div>
      </div>
    </div>

    <div class="page-grid">
      <el-card class="builder-card" shadow="never">
        <template #header>
          <div class="card-header">
            <span>技能配置</span>
            <div class="header-actions">
              <el-button size="small" @click="resetForm">新建技能</el-button>
              <el-button size="small" @click="loadAll">刷新</el-button>
            </div>
          </div>
        </template>

        <el-form :model="form" label-width="96px" class="builder-form">
          <el-form-item label="分析域" required>
            <el-select v-model="currentDomainId" placeholder="选择分析域" @change="handleDomainChange">
              <el-option v-for="item in domains" :key="item.domain_id" :label="item.domain_name" :value="item.domain_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="源数据库" required>
            <el-select v-model="form.source_id" placeholder="选择当前分析域的 Oracle 源数据库" filterable @change="handleSourceChange">
              <el-option v-for="item in dataSources" :key="item.source_id" :label="item.source_name" :value="item.source_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="分析流程" required>
            <el-select v-model="form.process_id" placeholder="选择业务流程" filterable>
              <el-option v-for="item in processes" :key="item.process_id" :label="item.process_name" :value="item.process_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="构建模型" required>
            <el-select v-model="form.llm_config_id" placeholder="选择用于技能构建的大模型" filterable>
              <el-option
                v-for="item in llmConfigs"
                :key="item.config_id"
                :label="formatModelOption(item)"
                :value="item.config_id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="属性图对象" required>
            <el-select v-model="form.property_graph_name" placeholder="选择 Oracle Property Graph" filterable :disabled="!form.source_id">
              <el-option
                v-for="item in propertyGraphs"
                :key="item.graph_name"
                :label="item.graph_name"
                :value="item.graph_name"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="技能名称" required>
            <el-input v-model="form.skill_name" placeholder="如：缺陷根因分析技能" />
          </el-form-item>
          <el-form-item label="技能说明">
            <el-input v-model="form.skill_desc" type="textarea" :rows="3" placeholder="描述该 skill 的职责边界、适用对象与分析场景" />
          </el-form-item>
          <el-form-item label="分析目标">
            <el-input v-model="form.analysis_goal" type="textarea" :rows="3" placeholder="明确智能体调用该 skill 时希望得到什么结论" />
          </el-form-item>
          <el-form-item label="执行规则">
            <el-input v-model="form.execution_rules" type="textarea" :rows="4" placeholder="如：先取数，再按流程节点判断，异常时给出风险提示" />
          </el-form-item>
          <el-form-item label="输出要求">
            <el-input v-model="form.output_requirements" type="textarea" :rows="3" placeholder="如：输出摘要、关键指标、异常点、建议动作" />
          </el-form-item>
          <el-form-item label="状态">
            <el-radio-group v-model="form.status">
              <el-radio-button label="ACTIVE">启用</el-radio-button>
              <el-radio-button label="DRAFT">草稿</el-radio-button>
              <el-radio-button label="INACTIVE">停用</el-radio-button>
            </el-radio-group>
          </el-form-item>
        </el-form>

        <div class="form-actions">
          <el-button @click="resetForm">重置</el-button>
          <el-button type="primary" :loading="saving" @click="saveSkill">{{ form.skill_id ? '更新技能' : '保存技能' }}</el-button>
        </div>
      </el-card>

      <div class="preview-column">
        <el-card class="preview-card" shadow="never">
          <template #header><span>构建预览</span></template>
          <div class="preview-section">
            <div class="preview-title">技能摘要</div>
            <div class="preview-summary">{{ previewSummary }}</div>
            <div class="model-chip" v-if="selectedModel">
              构建模型：{{ selectedModel.config_name }} / {{ selectedModel.model_name }}
            </div>
          </div>
          <div class="preview-section">
            <div class="preview-title">属性图上下文</div>
            <div v-if="selectedPropertyGraph" class="entity-box">
              <div class="entity-name">{{ selectedPropertyGraph.graph_name }}</div>
              <div class="entity-desc">Oracle Property Graph · {{ selectedPropertyGraph.owner || selectedDataSource?.schema_name || '当前 Schema' }}</div>
              <div class="tag-row">
                <el-tag size="small" effect="plain">{{ selectedDataSource?.source_name }}</el-tag>
                <el-tag size="small" type="success" effect="plain">PROPERTY GRAPH</el-tag>
              </div>
            </div>
            <el-empty v-else description="选择属性图对象后显示上下文" :image-size="64" />
          </div>
          <div class="preview-section">
            <div class="preview-title">流程步骤预览</div>
            <div v-if="processSteps.length" class="step-list">
              <div v-for="item in processSteps" :key="item.step_no" class="step-item">
                <span class="step-index">{{ item.step_no }}</span>
                <div>
                  <div class="step-name">{{ item.label }}</div>
                  <div class="step-type">{{ item.typeLabel }}</div>
                </div>
              </div>
            </div>
            <el-empty v-else description="选择流程后显示节点顺序" :image-size="64" />
          </div>
          <div class="preview-section">
            <div class="preview-title">Prompt 模板预览</div>
            <el-input :model-value="promptPreview" type="textarea" :rows="12" readonly />
          </div>
        </el-card>

        <el-card class="preview-card" shadow="never">
          <template #header><span>已构建技能</span></template>
            <el-table :data="skills" border stripe size="small" height="360" @row-click="fillForm">
            <el-table-column prop="skill_name" label="技能名称" min-width="180" />
            <el-table-column label="构建模型" min-width="180">
              <template #default="{ row }">{{ row.llm_config_name }} / {{ row.llm_model_name }}</template>
            </el-table-column>
            <el-table-column prop="process_name" label="流程" min-width="140" />
            <el-table-column label="属性图" min-width="160">
              <template #default="{ row }">{{ row.property_graph_name || row.entity_display_name || row.entity_name }}</template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="row.status === 'ACTIVE' ? 'success' : row.status === 'DRAFT' ? 'warning' : 'info'" size="small">
                  {{ row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button type="danger" link @click.stop="removeSkill(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { agentApi, domainApi, processApi, sourceApi, systemApi } from '../../api'
import { useAppStore } from '../../stores/app'

const appStore = useAppStore()

const domains = ref<any[]>([])
const processes = ref<any[]>([])
const dataSources = ref<any[]>([])
const propertyGraphs = ref<any[]>([])
const skills = ref<any[]>([])
const llmConfigs = ref<any[]>([])
const currentDomainId = ref(appStore.currentDomainId || '')
const saving = ref(false)

const createEmptyForm = () => ({
  skill_id: '',
  llm_config_id: '',
  process_id: '',
  source_id: '',
  property_graph_name: '',
  skill_name: '',
  skill_desc: '',
  analysis_goal: '',
  execution_rules: '',
  output_requirements: '',
  status: 'ACTIVE'
})

const form = ref(createEmptyForm())

const selectedProcess = computed(() => processes.value.find(item => item.process_id === form.value.process_id))
const selectedDataSource = computed(() => dataSources.value.find(item => item.source_id === form.value.source_id))
const selectedPropertyGraph = computed(() => propertyGraphs.value.find(item => item.graph_name === form.value.property_graph_name))
const currentDomain = computed(() => domains.value.find(item => item.domain_id === currentDomainId.value))
const selectedModel = computed(() => llmConfigs.value.find(item => item.config_id === form.value.llm_config_id))

const processSteps = computed(() => {
  const parsed = parseProcessJson(selectedProcess.value?.process_json)
  const nodes: any[] = Array.isArray(parsed?.nodes) ? parsed.nodes : []
  const edges: any[] = Array.isArray(parsed?.edges) ? parsed.edges : []
  if (!nodes.length) return []
  const nodeMap = new Map<string, any>(nodes.map((item: any) => [item.id, item]))
  const indegree = new Map<string, number>(nodes.map((item: any) => [item.id, 0]))
  const adjacency = new Map<string, string[]>(nodes.map((item: any) => [item.id, []]))
  edges.forEach((edge: any) => {
    if (nodeMap.has(edge.source) && nodeMap.has(edge.target)) {
      adjacency.get(edge.source)?.push(edge.target)
      indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1)
    }
  })
  const queue = [...indegree.entries()].filter(([, degree]) => degree === 0).map(([id]) => id).sort()
  const ordered: string[] = []
  while (queue.length) {
    const current = queue.shift()!
    ordered.push(current)
    ;(adjacency.get(current) || []).forEach((nextId: string) => {
      const nextDegree = (indegree.get(nextId) || 0) - 1
      indegree.set(nextId, nextDegree)
      if (nextDegree === 0) queue.push(nextId)
    })
  }
  nodes.forEach((node: any) => {
    if (!ordered.includes(node.id)) ordered.push(node.id)
  })
  return ordered.map((id, index) => {
    const node = nodeMap.get(id) || {}
    return {
      step_no: index + 1,
      label: node.label || node.typeName || '未命名节点',
      typeLabel: flowTypeLabel(node.type || 'analysis')
    }
  })
})

const previewSummary = computed(() => {
  if (!selectedProcess.value || !selectedPropertyGraph.value) {
    return '选择分析流程和 Oracle 属性图后，系统会自动生成适合 agent 调用的 skill 模板。'
  }
  return `${form.value.skill_name || '未命名技能'} 将在分析域“${currentDomain.value?.domain_name || ''}”中，围绕属性图“${selectedPropertyGraph.value.graph_name}”，按照“${selectedProcess.value.process_name}”执行图数据分析。`
})

const promptPreview = computed(() => {
  const graphName = selectedPropertyGraph.value?.graph_name || '待选 Property Graph'
  const processName = selectedProcess.value?.process_name || '待选分析流程'
  const processText = processSteps.value.map(item => `${item.step_no}. ${item.label}（${item.typeLabel}）`).join('\n') || '1. 开始准备分析'
  return [
    `你是业务分析智能体中的数据分析技能“${form.value.skill_name || '待命名技能'}”。`,
    `构建模型：${selectedModel.value ? `${selectedModel.value.config_name} / ${selectedModel.value.model_name}` : '未选择'}`,
    `分析域：${currentDomain.value?.domain_name || '未选择'}`,
    `分析目标：${form.value.analysis_goal || `围绕 ${graphName} 完成图数据分析`}`,
    `Oracle 属性图：${graphName}`,
    `源数据库：${selectedDataSource.value?.source_name || '未选择'}`,
    `业务流程：`,
    processText,
    `执行规则：${form.value.execution_rules || '优先按照流程顺序执行，数据不足时标记风险。'}`,
    `输出要求：${form.value.output_requirements || '输出摘要、关键指标、异常点和建议动作。'}`
  ].join('\n')
})

const parseProcessJson = (raw: any) => {
  if (!raw) return {}
  if (typeof raw === 'object') return raw
  try {
    return JSON.parse(raw)
  } catch {
    return {}
  }
}

const flowTypeLabel = (type: string) => ({
  start: '开始',
  dataInput: '数据输入',
  analysis: '分析节点',
  decision: '决策节点',
  action: '操作节点',
  end: '结束'
}[type] || type)

const formatModelOption = (model: any) => `${model.config_name} / ${model.model_name}`

const loadDomains = async () => {
  try {
    const res = await domainApi.list('ACTIVE')
    domains.value = res.data || []
    if (!currentDomainId.value && domains.value.length) {
      const first = domains.value[0]
      currentDomainId.value = first.domain_id
      appStore.setCurrentDomain(first.domain_id, first.domain_name)
    }
  } catch (e) {}
}

const loadModels = async () => {
  try {
    const res = await systemApi.getLLMConfigs()
    llmConfigs.value = (res.data || []).filter((item: any) => item.is_active === 'Y')
    if (!llmConfigs.value.length) {
      form.value.llm_config_id = ''
      return
    }
    const defaultModel = llmConfigs.value.find((item: any) => item.is_default === 'Y') || llmConfigs.value[0]
    if (!form.value.llm_config_id || !llmConfigs.value.some((item: any) => item.config_id === form.value.llm_config_id)) {
      form.value.llm_config_id = defaultModel.config_id
    }
  } catch (e) {
    llmConfigs.value = []
  }
}

const loadDomainResources = async () => {
  if (!currentDomainId.value) {
    processes.value = []
    dataSources.value = []
    propertyGraphs.value = []
    skills.value = []
    return
  }
  try {
    const [processRes, sourceRes, skillRes] = await Promise.all([
      processApi.list(currentDomainId.value),
      sourceApi.listDataSources(currentDomainId.value),
      agentApi.listSkills(currentDomainId.value)
    ])
    processes.value = processRes.data || []
    dataSources.value = (sourceRes.data || []).filter((item: any) => (item.db_type || '').toLowerCase() === 'oracle')
    skills.value = skillRes.data || []
    if (form.value.source_id && !dataSources.value.some((item: any) => item.source_id === form.value.source_id)) {
      form.value.source_id = ''
      form.value.property_graph_name = ''
    }
  } catch (e) {}
}

const loadPropertyGraphs = async () => {
  propertyGraphs.value = []
  if (!currentDomainId.value || !form.value.source_id) return
  try {
    const res = await agentApi.listPropertyGraphs(currentDomainId.value, form.value.source_id)
    propertyGraphs.value = res.data?.graphs || []
    if (form.value.property_graph_name && !propertyGraphs.value.some((item: any) => item.graph_name === form.value.property_graph_name)) {
      form.value.property_graph_name = ''
    }
  } catch (e) {
    form.value.property_graph_name = ''
  }
}

const loadAll = async () => {
  await loadDomains()
  await loadModels()
  await loadDomainResources()
}

const syncSuggestedName = () => {
  if (form.value.skill_id) return
  if (!selectedProcess.value || !selectedPropertyGraph.value) return
  if (form.value.skill_name.trim()) return
  form.value.skill_name = `${selectedPropertyGraph.value.graph_name}${selectedProcess.value.process_name}技能`
}

const handleDomainChange = (val: string) => {
  const domain = domains.value.find(item => item.domain_id === val)
  if (domain) appStore.setCurrentDomain(domain.domain_id, domain.domain_name)
  resetForm()
  loadDomainResources()
}

const handleSourceChange = () => {
  form.value.property_graph_name = ''
  loadPropertyGraphs()
}

const fillForm = (row: any) => {
  form.value = {
    skill_id: row.skill_id,
    llm_config_id: row.llm_config_id || '',
    process_id: row.process_id,
    source_id: row.source_id || '',
    property_graph_name: row.property_graph_name || row.entity_name || '',
    skill_name: row.skill_name || '',
    skill_desc: row.skill_desc || '',
    analysis_goal: row.analysis_goal || '',
    execution_rules: row.execution_rules || '',
    output_requirements: row.output_requirements || '',
    status: row.status || 'ACTIVE'
  }
  loadPropertyGraphs()
}

const resetForm = () => {
  form.value = createEmptyForm()
  const defaultModel = llmConfigs.value.find((item: any) => item.is_default === 'Y') || llmConfigs.value[0]
  form.value.llm_config_id = defaultModel?.config_id || ''
}

const saveSkill = async () => {
  if (!currentDomainId.value) { ElMessage.warning('请先选择分析域'); return }
  if (!form.value.llm_config_id) { ElMessage.warning('请选择用于构建技能的大模型'); return }
  if (!form.value.process_id) { ElMessage.warning('请选择分析流程'); return }
  if (!form.value.source_id) { ElMessage.warning('请选择 Oracle 源数据库'); return }
  if (!form.value.property_graph_name) { ElMessage.warning('请选择 Oracle 属性图对象'); return }
  if (!form.value.skill_name.trim()) { ElMessage.warning('请输入技能名称'); return }
  saving.value = true
  try {
    const payload = {
      llm_config_id: form.value.llm_config_id,
      process_id: form.value.process_id,
      source_id: form.value.source_id,
      property_graph_name: form.value.property_graph_name,
      skill_name: form.value.skill_name,
      skill_desc: form.value.skill_desc,
      analysis_goal: form.value.analysis_goal,
      execution_rules: form.value.execution_rules,
      output_requirements: form.value.output_requirements,
      status: form.value.status
    }
    if (form.value.skill_id) {
      await agentApi.updateSkill(form.value.skill_id, payload)
      ElMessage.success('技能已更新')
    } else {
      await agentApi.createSkill(currentDomainId.value, payload)
      ElMessage.success('技能已创建')
    }
    await loadDomainResources()
    resetForm()
  } catch (e) {} finally {
    saving.value = false
  }
}

const removeSkill = async (row: any) => {
  try {
    await ElMessageBox.confirm(`确定删除技能“${row.skill_name}”吗？`, '确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await agentApi.deleteSkill(row.skill_id)
    if (form.value.skill_id === row.skill_id) resetForm()
    ElMessage.success('技能已删除')
    await loadDomainResources()
  } catch (e) {}
}

watch(() => appStore.currentDomainId, async (val) => {
  if (!val || val === currentDomainId.value) return
  currentDomainId.value = val
  resetForm()
  await loadDomainResources()
})

watch(() => [form.value.process_id, form.value.property_graph_name], syncSuggestedName)

onMounted(async () => {
  await loadAll()
})
</script>

<style scoped>
.agent-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: calc(100vh - 110px);
}
.hero-panel {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px;
  border-radius: 16px;
  background: linear-gradient(135deg, #0f4c81 0%, #1f6aa5 50%, #d5e8f7 100%);
  color: #fff;
}
.hero-title {
  font-size: 24px;
  font-weight: 700;
  margin-bottom: 8px;
}
.hero-desc {
  max-width: 720px;
  line-height: 1.7;
  color: rgba(255, 255, 255, 0.9);
}
.hero-stats {
  display: flex;
  gap: 12px;
  align-items: stretch;
}
.stat-card {
  min-width: 112px;
  padding: 14px 16px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.14);
  backdrop-filter: blur(8px);
  text-align: center;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
}
.stat-label {
  margin-top: 6px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.84);
}
.page-grid {
  display: grid;
  grid-template-columns: minmax(380px, 1fr) minmax(480px, 1.1fr);
  gap: 16px;
  align-items: start;
}
.builder-card,
.preview-card {
  border-radius: 16px;
  border: 1px solid #d7e4f0;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.header-actions,
.form-actions {
  display: flex;
  gap: 8px;
}
.builder-form :deep(.el-select),
.builder-form :deep(.el-input),
.builder-form :deep(.el-textarea) {
  width: 100%;
}
.preview-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.preview-section + .preview-section {
  margin-top: 20px;
}
.preview-title {
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 700;
  color: #1d4f7a;
}
.preview-summary,
.entity-desc {
  color: #5a6778;
  line-height: 1.7;
}
.model-chip {
  display: inline-flex;
  margin-top: 10px;
  padding: 6px 10px;
  border-radius: 999px;
  background: #e7f3fb;
  color: #1f5f8b;
  font-size: 12px;
  font-weight: 600;
}
.entity-box {
  padding: 14px;
  border-radius: 12px;
  background: #f6fbff;
  border: 1px solid #dbeaf6;
}
.entity-name {
  font-size: 16px;
  font-weight: 700;
  color: #23405d;
  margin-bottom: 8px;
}
.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.step-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.step-item {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid #e4edf5;
  background: #fff;
}
.step-index {
  display: inline-flex;
  width: 24px;
  height: 24px;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: #1f6aa5;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
}
.step-name {
  font-weight: 600;
  color: #24384e;
}
.step-type {
  margin-top: 4px;
  font-size: 12px;
  color: #7a8795;
}
@media (max-width: 1200px) {
  .page-grid {
    grid-template-columns: 1fr;
  }
  .hero-panel {
    flex-direction: column;
  }
}
</style>
