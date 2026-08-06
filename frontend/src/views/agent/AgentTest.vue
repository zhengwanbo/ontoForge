<template>
  <div class="agent-test-page">
    <div class="test-banner">
      <div>
        <div class="banner-title">智能体测试</div>
        <div class="banner-desc">
          选择已构建的 skill、目标本体对象数据库和 graph 表，验证 agent 是否能按既定业务流程完成数据准备、分析判断和输出动作。
        </div>
      </div>
      <el-alert
        title="当前测试为流程驱动验证"
        description="系统会结合 skill 模板、流程节点、本体对象和 graph 表结构，生成执行轨迹、字段匹配和输出建议。"
        type="info"
        :closable="false"
        show-icon
      />
    </div>

    <div class="layout-grid">
      <el-card class="test-card" shadow="never">
        <template #header><span>测试配置</span></template>

        <el-form :model="form" label-width="96px">
          <el-form-item label="分析域" required>
            <el-select v-model="currentDomainId" placeholder="选择分析域" @change="handleDomainChange">
              <el-option v-for="item in domains" :key="item.domain_id" :label="item.domain_name" :value="item.domain_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="选择技能" required>
            <el-select v-model="form.skill_id" placeholder="选择已构建 skill" filterable @change="handleSkillChange">
              <el-option v-for="item in skills" :key="item.skill_id" :label="item.skill_name" :value="item.skill_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="测试模型" required>
            <el-select v-model="form.llm_config_id" placeholder="选择测试执行大模型" filterable>
              <el-option
                v-for="item in llmConfigs"
                :key="item.config_id"
                :label="formatModelOption(item)"
                :value="item.config_id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="对象数据库" required>
            <el-select v-model="form.source_id" placeholder="选择本体对象数据库" filterable @change="handleSourceChange">
              <el-option v-for="item in dataSources" :key="item.source_id" :label="item.source_name" :value="item.source_id" />
            </el-select>
          </el-form-item>
          <el-form-item label="Schema">
            <el-select v-model="form.schema" placeholder="选择 Schema" filterable clearable @change="loadTables">
              <el-option v-for="item in schemas" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="Graph 表" required>
            <el-select v-model="form.graph_table" placeholder="选择 graph 表" filterable>
              <el-option v-for="item in graphTables" :key="item.table_name" :label="formatTableLabel(item)" :value="item.table_name" />
            </el-select>
          </el-form-item>
          <el-form-item label="测试问题">
            <el-input
              v-model="form.test_question"
              type="textarea"
              :rows="3"
              placeholder="如：请判断该批次缺陷是否存在工艺异常，并给出优先排查方向"
            />
          </el-form-item>
          <el-form-item label="额外输入">
            <el-input
              v-model="form.input_payload"
              type="textarea"
              :rows="4"
              placeholder="可输入 JSON 或补充业务上下文，例如批次号、时间窗口、人工备注等"
            />
          </el-form-item>
          <el-form-item label="采样行数">
            <el-input-number v-model="form.sample_limit" :min="1" :max="10" />
          </el-form-item>
        </el-form>

        <div class="test-actions">
          <el-button @click="loadAll">刷新</el-button>
          <el-button type="primary" :loading="testing" @click="runTest">执行测试</el-button>
        </div>
      </el-card>

      <div class="result-column">
        <el-card class="test-card" shadow="never">
          <template #header><span>技能信息</span></template>
          <div v-if="selectedSkill" class="skill-profile">
            <div class="skill-name">{{ selectedSkill.skill_name }}</div>
            <div class="skill-meta">
              <el-tag size="small" type="success">{{ selectedSkill.process_name }}</el-tag>
              <el-tag size="small" effect="plain">{{ selectedSkill.entity_display_name || selectedSkill.entity_name }}</el-tag>
              <el-tag size="small" :type="selectedSkill.status === 'ACTIVE' ? 'success' : selectedSkill.status === 'DRAFT' ? 'warning' : 'info'">
                {{ selectedSkill.status }}
              </el-tag>
            </div>
            <div class="skill-desc">{{ selectedSkill.skill_desc || '当前技能尚未补充说明。' }}</div>
            <div class="skill-section">
              <div class="section-title">构建模型</div>
              <div>{{ selectedSkill.llm_config_name }} / {{ selectedSkill.llm_model_name }}</div>
            </div>
            <div class="skill-section">
              <div class="section-title">分析目标</div>
              <div>{{ selectedSkill.analysis_goal || '未设置' }}</div>
            </div>
            <div class="skill-section">
              <div class="section-title">Prompt 模板</div>
              <el-input :model-value="selectedSkill.prompt_template" type="textarea" :rows="10" readonly />
            </div>
          </div>
          <el-empty v-else description="选择 skill 后显示详情" :image-size="72" />
        </el-card>

        <el-card class="test-card" shadow="never">
          <template #header><span>测试结果</span></template>
          <div v-if="result">
            <el-alert
              v-for="item in result.warnings || []"
              :key="item"
              :title="item"
              type="warning"
              :closable="false"
              show-icon
              class="warning-item"
            />

            <div class="result-block">
              <div class="section-title">执行摘要</div>
              <div class="summary-box">{{ result.expected_output?.summary }}</div>
              <div class="execution-model" v-if="result.execution_model">
                测试模型：{{ result.execution_model.llm_config_name }} / {{ result.execution_model.llm_model_name }}
              </div>
              <div class="focus-list">
                <el-tag v-for="item in result.expected_output?.focus_points || []" :key="item" size="small">{{ item }}</el-tag>
              </div>
            </div>

            <div class="result-block">
              <div class="section-title">大模型执行结果</div>
              <div class="llm-output-box">{{ result.agent_output }}</div>
            </div>

            <div class="result-block">
              <div class="section-title">流程执行轨迹</div>
              <div class="trace-list">
                <div v-for="item in result.process_trace || []" :key="item.step_no" class="trace-item">
                  <div class="trace-step">{{ item.step_no }}</div>
                  <div class="trace-body">
                    <div class="trace-name">{{ item.step_name }} <span>{{ item.step_type }}</span></div>
                    <div class="trace-action">{{ item.action }}</div>
                  </div>
                </div>
              </div>
            </div>

            <div class="result-block">
              <div class="section-title">字段匹配</div>
              <el-table :data="result.matched_columns || []" border stripe size="small" max-height="220">
                <el-table-column prop="property_display_name" label="本体属性" min-width="120">
                  <template #default="{ row }">{{ row.property_display_name || row.property_name }}</template>
                </el-table-column>
                <el-table-column prop="column_name" label="表字段" min-width="120" />
                <el-table-column prop="data_type" label="类型" min-width="120" />
                <el-table-column prop="column_comment" label="字段说明" min-width="180" />
              </el-table>
            </div>

            <div class="result-block">
              <div class="section-title">建议 SQL</div>
              <pre class="code-box">{{ result.suggested_sql }}</pre>
            </div>

            <div class="result-block">
              <div class="section-title">测试 Prompt</div>
              <el-input :model-value="result.prompt_preview" type="textarea" :rows="12" readonly />
            </div>

            <div class="result-block">
              <div class="section-title">Graph 表样例</div>
              <el-table :data="result.table_preview?.sample_rows || []" border stripe size="small" max-height="240">
                <el-table-column
                  v-for="item in (result.table_preview?.columns || []).slice(0, 8)"
                  :key="item.column_name"
                  :prop="item.column_name"
                  :label="item.column_name"
                  min-width="120"
                />
              </el-table>
            </div>
          </div>
          <el-empty v-else description="执行测试后显示流程回放与结果建议" :image-size="76" />
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { agentApi, domainApi, sourceApi, systemApi } from '../../api'
import { useAppStore } from '../../stores/app'

const appStore = useAppStore()

const domains = ref<any[]>([])
const skills = ref<any[]>([])
const dataSources = ref<any[]>([])
const llmConfigs = ref<any[]>([])
const schemas = ref<string[]>([])
const graphTables = ref<any[]>([])
const result = ref<any>(null)
const testing = ref(false)
const currentDomainId = ref(appStore.currentDomainId || '')

const form = ref({
  skill_id: '',
  llm_config_id: '',
  source_id: '',
  schema: '',
  graph_table: '',
  test_question: '',
  input_payload: '',
  sample_limit: 5
})

const selectedSkill = computed(() => skills.value.find(item => item.skill_id === form.value.skill_id))
const defaultTestModel = computed(() => llmConfigs.value.find(item => item.is_default === 'Y') || llmConfigs.value[0])

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

const loadDomainResources = async () => {
  if (!currentDomainId.value) {
    skills.value = []
    dataSources.value = []
    return
  }
  try {
    const [skillRes, sourceRes] = await Promise.all([
      agentApi.listSkills(currentDomainId.value),
      sourceApi.listDataSources(currentDomainId.value)
    ])
    skills.value = skillRes.data || []
    dataSources.value = sourceRes.data || []
  } catch (e) {}
}

const loadLLMConfigs = async () => {
  try {
    const res = await systemApi.getLLMConfigs()
    llmConfigs.value = (res.data || []).filter((item: any) => item.is_active === 'Y')
    if (!form.value.llm_config_id) {
      form.value.llm_config_id = defaultTestModel.value?.config_id || ''
    }
  } catch (e) {
    llmConfigs.value = []
  }
}

const loadSchemas = async () => {
  if (!form.value.source_id) {
    schemas.value = []
    return
  }
  try {
    const res = await sourceApi.getSchemas(form.value.source_id)
    schemas.value = res.data?.schemas || []
    if (!form.value.schema) {
      form.value.schema = res.data?.default_schema || ''
    }
  } catch (e) {}
}

const loadTables = async () => {
  if (!form.value.source_id) {
    graphTables.value = []
    return
  }
  try {
    const res = await sourceApi.getRemoteTables(form.value.source_id, {
      schema: form.value.schema || undefined
    })
    graphTables.value = res.data?.tables || []
    if (form.value.graph_table && !graphTables.value.some((item: any) => item.table_name === form.value.graph_table)) {
      form.value.graph_table = ''
    }
  } catch (e) {}
}

const loadAll = async () => {
  await loadDomains()
  await loadLLMConfigs()
  await loadDomainResources()
  if (form.value.source_id) {
    await loadSchemas()
    await loadTables()
  }
}

const handleDomainChange = async (val: string) => {
  const domain = domains.value.find(item => item.domain_id === val)
  if (domain) appStore.setCurrentDomain(domain.domain_id, domain.domain_name)
  form.value.skill_id = ''
  result.value = null
  await loadDomainResources()
}

const handleSkillChange = async (val: string) => {
  if (!val) return
  try {
    const res = await agentApi.getSkill(val)
    const detail = res.data
    const index = skills.value.findIndex(item => item.skill_id === val)
    if (index >= 0) skills.value[index] = detail
    if (detail?.llm_config_id && llmConfigs.value.some((item: any) => item.config_id === detail.llm_config_id)) {
      form.value.llm_config_id = detail.llm_config_id
    } else if (!form.value.llm_config_id) {
      form.value.llm_config_id = defaultTestModel.value?.config_id || ''
    }
  } catch (e) {}
}

const handleSourceChange = async () => {
  form.value.schema = ''
  form.value.graph_table = ''
  graphTables.value = []
  await loadSchemas()
  await loadTables()
}

const runTest = async () => {
  if (!form.value.skill_id) { ElMessage.warning('请选择技能'); return }
  if (!form.value.llm_config_id) { ElMessage.warning('请选择测试模型'); return }
  if (!form.value.source_id) { ElMessage.warning('请选择对象数据库'); return }
  if (!form.value.graph_table) { ElMessage.warning('请选择 graph 表'); return }
  testing.value = true
  result.value = null
  try {
    const res = await agentApi.testSkill(form.value.skill_id, {
      llm_config_id: form.value.llm_config_id,
      source_id: form.value.source_id,
      schema: form.value.schema || null,
      graph_table: form.value.graph_table,
      test_question: form.value.test_question,
      input_payload: form.value.input_payload,
      sample_limit: form.value.sample_limit
    })
    result.value = res.data
    ElMessage.success('测试完成')
  } catch (e) {} finally {
    testing.value = false
  }
}

const formatTableLabel = (item: any) => {
  return item.comments ? `${item.table_name} (${item.comments})` : item.table_name
}

const formatModelOption = (item: any) => `${item.config_name} / ${item.model_name}`

watch(() => appStore.currentDomainId, async (val) => {
  if (!val || val === currentDomainId.value) return
  currentDomainId.value = val
  form.value.skill_id = ''
  form.value.llm_config_id = defaultTestModel.value?.config_id || ''
  result.value = null
  await loadDomainResources()
})

onMounted(async () => {
  await loadAll()
})
</script>

<style scoped>
.agent-test-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: calc(100vh - 110px);
}
.test-banner {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
  padding: 18px 22px;
  border-radius: 16px;
  background:
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.3), transparent 42%),
    linear-gradient(135deg, #17324d 0%, #295c82 55%, #dfeef8 100%);
  color: #fff;
}
.banner-title {
  font-size: 24px;
  font-weight: 700;
  margin-bottom: 8px;
}
.banner-desc {
  max-width: 720px;
  line-height: 1.7;
  color: rgba(255, 255, 255, 0.9);
}
.layout-grid {
  display: grid;
  grid-template-columns: minmax(360px, 0.9fr) minmax(500px, 1.1fr);
  gap: 16px;
  align-items: start;
}
.test-card {
  border-radius: 16px;
  border: 1px solid #d9e4ee;
}
.test-actions {
  display: flex;
  gap: 8px;
}
.result-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.skill-profile {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.skill-name {
  font-size: 18px;
  font-weight: 700;
  color: #1f3f5d;
}
.skill-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.skill-desc {
  color: #5d6a78;
  line-height: 1.7;
}
.skill-section,
.result-block {
  margin-top: 12px;
}
.section-title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 700;
  color: #25527c;
}
.summary-box {
  padding: 12px 14px;
  border-radius: 12px;
  background: #f4f9fd;
  border: 1px solid #d9ebf7;
  color: #435364;
  line-height: 1.7;
}
.focus-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.execution-model {
  margin-top: 10px;
  color: #1f5f8b;
  font-size: 12px;
  font-weight: 600;
}
.llm-output-box {
  padding: 14px 16px;
  border-radius: 12px;
  background: #fffdf6;
  border: 1px solid #efe1a8;
  color: #473f21;
  white-space: pre-wrap;
  line-height: 1.8;
}
.trace-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.trace-item {
  display: flex;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #e3edf5;
}
.trace-step {
  display: inline-flex;
  width: 26px;
  height: 26px;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: #295c82;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}
.trace-name {
  font-weight: 700;
  color: #22384d;
}
.trace-name span {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: #6f8090;
}
.trace-action {
  margin-top: 6px;
  color: #526272;
  line-height: 1.7;
}
.code-box {
  margin: 0;
  padding: 14px;
  border-radius: 12px;
  background: #0f1b28;
  color: #d9e8f6;
  white-space: pre-wrap;
  word-break: break-word;
}
.warning-item + .warning-item {
  margin-top: 10px;
}
@media (max-width: 1200px) {
  .layout-grid,
  .test-banner {
    grid-template-columns: 1fr;
  }
}
</style>
