<template>
  <div class="graph-query-page">
    <section class="page-header">
      <div><div class="eyebrow">ORACLE PROPERTY GRAPH</div><h2>图数据查询</h2><p>在选定的目标 Oracle 数据库中执行只读 Graph SQL，并将包含 SOURCE_ID、TARGET_ID 的结果渲染为关系图。</p></div>
    </section>

    <el-card shadow="never" class="query-card">
      <div class="query-form">
        <el-select v-model="domainId" placeholder="选择业务分析域" @change="handleDomainChange"><el-option v-for="domain in domains" :key="domain.domain_id" :label="domain.domain_name" :value="domain.domain_id" /></el-select>
        <el-select v-model="sourceId" placeholder="选择目标 Oracle 数据库" filterable :disabled="!domainId" @change="handleSourceChange"><el-option v-for="source in sources" :key="source.source_id" :label="`${source.source_name} / ${source.schema_name || source.username}`" :value="source.source_id" /></el-select>
        <el-select v-model="schema" placeholder="Schema（可选）" filterable :disabled="!sourceId" @change="loadGraphRecommendations"><el-option v-for="item in schemas" :key="item" :label="item" :value="item" /></el-select>
        <el-select v-model="graphName" placeholder="选择 Oracle 属性图" filterable :disabled="!sourceId || !graphOptions.length" @change="loadGraphRecommendations"><el-option v-for="item in graphOptions" :key="item.graph_name" :label="item.graph_name" :value="item.graph_name" /></el-select>
        <el-input-number v-model="rowLimit" :min="1" :max="1000" controls-position="right" />
        <el-button type="primary" :loading="executing" :disabled="!canExecute" @click="executeQuery">执行 Graph SQL</el-button>
      </div>
      <div class="form-hint">只允许包含 <code>GRAPH_TABLE</code> 的只读 <code>SELECT / WITH</code> 查询；结果列建议命名为 <code>SOURCE_ID</code>、<code>TARGET_ID</code>、<code>RELATION_NAME</code>。</div>
      <div v-if="recommendations.length" class="recommendation-area">
        <div class="recommendation-heading"><span>常用业务图查询</span><el-tag size="small" effect="plain">{{ graphName || '当前属性图' }}</el-tag></div>
        <div class="recommendation-list">
          <button v-for="item in recommendations" :key="item.id" type="button" class="recommendation-item" :class="{ selected: selectedRecommendationId === item.id }" @click="selectRecommendation(item)">
            <strong>{{ item.title }}</strong><span>{{ item.description }}</span>
          </button>
        </div>
      </div>
      <el-input v-model="graphSql" type="textarea" :rows="10" resize="vertical" class="sql-editor" />
    </el-card>

    <el-alert v-if="result && !graphData.edges.length" type="info" :closable="false" show-icon class="result-alert"><template #title>查询返回 {{ result.rows?.length || 0 }} 行，但未找到 SOURCE_ID / TARGET_ID；下方保留表格结果。为渲染关系图，请在 SQL 中为两端顶点 ID 使用这两个别名。</template></el-alert>

    <div v-if="result" class="result-layout">
      <el-card shadow="never" class="graph-result-card">
        <template #header><div class="card-header"><span>查询图形结果</span><el-tag>{{ graphData.nodes.length }} 个节点 / {{ graphData.edges.length }} 条边</el-tag></div></template>
        <div v-if="graphData.nodes.length" ref="graphChartRef" class="graph-canvas"></div>
        <el-empty v-else description="查询结果中没有可绘制的关系数据" />
        <div v-if="selectedQueryNode" class="node-detail">
          <div class="node-detail-heading"><span class="node-detail-dot" :style="{ background: graphNodeColor(selectedQueryNode.id) }"></span><strong>{{ selectedQueryNode.label }}</strong><el-tag size="small" effect="plain">{{ graphNodeType(selectedQueryNode.id) }}</el-tag></div>
          <el-descriptions :column="2" border size="small" class="node-detail-properties">
            <el-descriptions-item v-for="item in selectedNodeProperties" :key="item.key" :label="item.key">{{ item.value }}</el-descriptions-item>
            <el-descriptions-item label="关联关系">{{ selectedNodeRelationNames || '无' }}</el-descriptions-item>
          </el-descriptions>
        </div>
        <div v-else-if="graphData.nodes.length" class="node-detail-placeholder">点击图中的实例节点，可查看其主要属性和关联关系。</div>
      </el-card>
      <el-card shadow="never" class="table-result-card">
        <template #header><div class="card-header"><span>原始查询结果</span><span class="result-meta">{{ result.source_name }} / {{ result.schema }} / {{ result.rows?.length || 0 }} 行</span></div></template>
        <el-table :data="result.rows || []" border stripe size="small" max-height="560"><el-table-column v-for="column in result.columns || []" :key="column" :prop="column" :label="column" min-width="150" show-overflow-tooltip /></el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { domainApi, sourceApi } from '../../api'
import { useAppStore } from '../../stores/app'

const appStore = useAppStore()
const domainId = ref(appStore.currentDomainId || '')
const sourceId = ref('')
const schema = ref('')
const graphName = ref('')
const graphOptions = ref<any[]>([])
const recommendations = ref<any[]>([])
const selectedRecommendationId = ref('')
const rowLimit = ref(200)
const domains = ref<any[]>([])
const sources = ref<any[]>([])
const schemas = ref<string[]>([])
const result = ref<any>(null)
const executing = ref(false)
const graphChartRef = ref<HTMLElement | null>(null)
const selectedQueryNode = ref<any>(null)
let graphChart: echarts.ECharts | null = null
const graphSql = ref(`WITH trace_path AS (
  SELECT * FROM GRAPH_TABLE(
    PG_JDXQ_SUPPLY_TRACE
    MATCH (b IS BOTTLECODE)-[e1 IS GRAPH_LABEL]->(p IS PACKCODE)-[e2 IS GRAPH_LABEL]->(c IS CASECODE)-[e3 IS GRAPH_LABEL]->(pal IS PALLETCODE)-[e4 IS GRAPH_LABEL]->(s IS STACKCODE)
    COLUMNS (
      b.BOTTLE_ID AS bottle_id, b.BOTTLE_CODE AS bottle_code,
      p.PACK_ID AS pack_id, p.PACK_CODE AS pack_code,
      c.CASE_ID AS case_id, c.CASE_CODE AS case_code,
      pal.PALLET_ID AS pallet_id, pal.PALLET_CODE AS pallet_code,
      s.STACK_ID AS stack_id, s.STACK_CODE AS stack_code,
      e1.RELATION_NAME AS bottle_pack_relation, e2.RELATION_NAME AS pack_case_relation,
      e3.RELATION_NAME AS case_pallet_relation, e4.RELATION_NAME AS pallet_stack_relation
    )
  )
)
SELECT 'BOTTLE:' || bottle_id AS source_id, bottle_code AS source_label, 'PACK:' || pack_id AS target_id, pack_code AS target_label, bottle_pack_relation AS relation_name FROM trace_path WHERE bottle_code = 'BOT-202608-000001'
UNION ALL
SELECT 'PACK:' || pack_id, pack_code, 'CASE:' || case_id, case_code, pack_case_relation FROM trace_path WHERE bottle_code = 'BOT-202608-000001'
UNION ALL
SELECT 'CASE:' || case_id, case_code, 'PALLET:' || pallet_id, pallet_code, case_pallet_relation FROM trace_path WHERE bottle_code = 'BOT-202608-000001'
UNION ALL
SELECT 'PALLET:' || pallet_id, pallet_code, 'STACK:' || stack_id, stack_code, pallet_stack_relation FROM trace_path WHERE bottle_code = 'BOT-202608-000001'`)

const canExecute = computed(() => Boolean(domainId.value && sourceId.value && graphSql.value.trim()))
const rowValue = (row: any, names: string[]) => { const key = Object.keys(row || {}).find(item => names.includes(item.toUpperCase())); return key ? String(row[key] ?? '') : '' }
const graphData = computed(() => {
  const nodeMap = new Map<string, any>(); const edges: any[] = []
  ;(result.value?.rows || []).forEach((row: any, index: number) => {
    const source = rowValue(row, ['SOURCE_ID', 'SOURCE', 'FROM_ID']); const target = rowValue(row, ['TARGET_ID', 'TARGET', 'TO_ID'])
    if (!source || !target) return
    const sourceLabel = rowValue(row, ['SOURCE_LABEL', 'SOURCE_NAME', 'FROM_LABEL']) || source
    const targetLabel = rowValue(row, ['TARGET_LABEL', 'TARGET_NAME', 'TO_LABEL']) || target
    const readProperties = (side: 'source' | 'target', id: string, label: string) => {
      const prefixes = side === 'source' ? ['SOURCE_', 'FROM_'] : ['TARGET_', 'TO_']
      const properties: Record<string, string> = { 实例标识: id, 实例名称: label, 实体类型: graphNodeType(id) }
      Object.entries(row).forEach(([key, value]) => {
        const upperKey = key.toUpperCase()
        if (prefixes.some(prefix => upperKey.startsWith(prefix)) && !/(?:_ID|_LABEL|_NAME)$/.test(upperKey) && value != null && value !== '') properties[key] = String(value)
      })
      return properties
    }
    nodeMap.set(source, { id: source, label: sourceLabel, properties: readProperties('source', source, sourceLabel) }); nodeMap.set(target, { id: target, label: targetLabel, properties: readProperties('target', target, targetLabel) })
    edges.push({ id: `${source}-${target}-${index}`, source, target, label: rowValue(row, ['RELATION_NAME', 'EDGE_LABEL', 'LABEL', 'RELATION']) || '关联' })
  })
  return { nodes: Array.from(nodeMap.values()).slice(0, 80), edges: edges.slice(0, 160) }
})
const graphPalette = ['#2563eb', '#0891b2', '#059669', '#7c3aed', '#ea580c', '#db2777', '#65a30d']
const graphNodeType = (id: string) => String(id || '').split(':', 1)[0] || '实体'
const graphNodeColor = (id: string) => graphPalette[Math.abs(Array.from(graphNodeType(id)).reduce((value, char) => value + char.charCodeAt(0), 0)) % graphPalette.length]
const selectedNodeProperties = computed(() => Object.entries(selectedQueryNode.value?.properties || {}).slice(0, 8).map(([key, value]) => ({ key, value })))
const selectedNodeRelationNames = computed(() => Array.from(new Set(graphData.value.edges.filter((edge: any) => edge.source === selectedQueryNode.value?.id || edge.target === selectedQueryNode.value?.id).map((edge: any) => edge.label))).join('、'))
const renderGraph = async () => {
  await nextTick()
  if (!graphChartRef.value || !graphData.value.nodes.length) { graphChart?.clear(); return }
  if (!graphChart) {
    graphChart = echarts.init(graphChartRef.value)
    graphChart.on('click', (params: any) => { if (params.dataType === 'node') selectedQueryNode.value = graphData.value.nodes.find((node: any) => node.id === params.data.id) || null })
  }
  const visibleIds = new Set(graphData.value.nodes.map((node: any) => node.id))
  const typeNames = Array.from(new Set(graphData.value.nodes.map((node: any) => graphNodeType(node.id))))
  const typeIndex = new Map(typeNames.map((name, index) => [name, index]))
  graphChart.setOption({
    animationDurationUpdate: 450,
    tooltip: { trigger: 'item', formatter: (params: any) => params.dataType === 'edge' ? `关系：${params.data.value}` : `<b>${params.data.label}</b><br/>${params.data.type}` },
    series: [{
      type: 'graph', layout: 'force', roam: true, draggable: true,
      categories: typeNames.map((name, index) => ({ name, itemStyle: { color: graphPalette[index % graphPalette.length] } })),
      data: graphData.value.nodes.map((node: any) => ({ id: node.id, name: node.label, label: node.label, type: graphNodeType(node.id), category: typeIndex.get(graphNodeType(node.id)) || 0, symbol: 'circle', symbolSize: 62, itemStyle: { color: graphNodeColor(node.id), borderColor: '#fff', borderWidth: 2, shadowBlur: 8, shadowColor: 'rgba(15, 23, 42, .18)' } })),
      links: graphData.value.edges.filter((edge: any) => visibleIds.has(edge.source) && visibleIds.has(edge.target)).map((edge: any) => ({ source: edge.source, target: edge.target, value: edge.label })),
      edgeSymbol: ['none', 'arrow'], edgeSymbolSize: [0, 9],
      label: { show: true, position: 'inside', color: '#fff', fontSize: 10, fontWeight: 650, width: 54, overflow: 'truncate' },
      edgeLabel: { show: true, formatter: (params: any) => params.data.value, color: '#475569', fontSize: 10, backgroundColor: 'rgba(255,255,255,.9)', padding: [3, 5], borderRadius: 4 },
      lineStyle: { color: '#94a3b8', width: 1.6, curveness: .12, opacity: .88 },
      emphasis: { focus: 'adjacency', lineStyle: { color: '#2563eb', width: 3 } },
      force: { repulsion: 850, edgeLength: [115, 220], gravity: .1, layoutAnimation: true }
    }]
  }, true)
}
const resizeGraph = () => graphChart?.resize()
const loadDomains = async () => { try { const res = await domainApi.list('ACTIVE'); domains.value = res.data || [] } catch (_) {} }
const loadSources = async () => { if (!domainId.value) return; try { const res = await sourceApi.listDataSources(domainId.value); sources.value = (res.data || []).filter((item: any) => (item.db_type || '').toLowerCase() === 'oracle'); sourceId.value = sources.value.find((item: any) => item.is_default === 'Y')?.source_id || sources.value[0]?.source_id || ''; await loadSchemas() } catch (_) { sources.value = [] } }
const selectRecommendation = (item: any, clearResult = true) => { graphSql.value = item.sql || ''; selectedRecommendationId.value = item.id || ''; if (item.graph_name) graphName.value = item.graph_name; if (clearResult) result.value = null }
const loadGraphRecommendations = async () => {
  if (!domainId.value || !sourceId.value) { graphOptions.value = []; recommendations.value = []; graphName.value = ''; return }
  try {
    const res: any = await sourceApi.getGraphQueryRecommendations(domainId.value, sourceId.value, { schema: schema.value || undefined, graph_name: graphName.value || undefined })
    graphOptions.value = res.data?.graphs || []
    graphName.value = res.data?.graph_name || ''
    recommendations.value = res.data?.recommendations || []
    if (recommendations.value.length) selectRecommendation(recommendations.value[0], false)
  } catch (_) { graphOptions.value = []; recommendations.value = []; graphName.value = '' }
}
const loadSchemas = async () => { if (!sourceId.value) { schemas.value = []; await loadGraphRecommendations(); return }; try { const res = await sourceApi.getSchemas(sourceId.value); schemas.value = res.data?.schemas || []; schema.value = res.data?.default_schema || schemas.value[0] || '' } catch (_) { schemas.value = [] } finally { await loadGraphRecommendations() } }
const handleDomainChange = async () => { const domain = domains.value.find(item => item.domain_id === domainId.value); appStore.setCurrentDomain(domainId.value, domain?.domain_name || ''); result.value = null; sourceId.value = ''; schema.value = ''; graphName.value = ''; await loadSources() }
const handleSourceChange = async () => { result.value = null; graphName.value = ''; await loadSchemas() }
const executeQuery = async () => { if (!canExecute.value) return; executing.value = true; try { const res = await sourceApi.executeGraphQuery({ domain_id: domainId.value, source_id: sourceId.value, schema: schema.value || undefined, graph_sql: graphSql.value, row_limit: rowLimit.value }); selectedQueryNode.value = null; result.value = res.data; void renderGraph(); ElMessage.success(`查询完成，返回 ${res.data?.rows?.length || 0} 行`) } catch (_) {} finally { executing.value = false } }
watch(() => appStore.currentDomainId, async (currentDomainId) => {
  if (currentDomainId === domainId.value) return
  domainId.value = currentDomainId || ''
  result.value = null
  sourceId.value = ''
  schema.value = ''
  graphName.value = ''
  await loadSources()
})
watch(result, value => { if (!value) { selectedQueryNode.value = null; graphChart?.dispose(); graphChart = null } })
onMounted(async () => { window.addEventListener('resize', resizeGraph); await loadDomains(); if (domainId.value) await loadSources() })
onBeforeUnmount(() => { window.removeEventListener('resize', resizeGraph); graphChart?.dispose(); graphChart = null })
</script>

<style scoped>
.graph-query-page { min-height: calc(100vh - 86px); padding: 8px 0 20px; }.page-header { margin: 8px 0 16px; }.eyebrow { color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .14em; }.page-header h2 { margin: 4px 0; color: #0f172a; font-size: 25px; }.page-header p { margin: 0; color: #64748b; font-size: 13px; }.query-card, .graph-result-card, .table-result-card { border-color: #e4eaf2; }.query-form { display: grid; grid-template-columns: 1.1fr 1.25fr 1fr 1fr 105px auto; gap: 10px; }.form-hint { margin: 10px 0; color: #64748b; font-size: 12px; }.form-hint code { color: #2563eb; }.recommendation-area { margin: 14px 0; padding: 12px; border: 1px solid #dbeafe; border-radius: 10px; background: #f8fbff; }.recommendation-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 9px; color: #1e3a5f; font-size: 13px; font-weight: 700; }.recommendation-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }.recommendation-item { min-height: 68px; padding: 9px 10px; text-align: left; border: 1px solid #dbe4f0; border-radius: 8px; color: #334155; background: #fff; cursor: pointer; }.recommendation-item:hover, .recommendation-item.selected { border-color: #3b82f6; background: #eff6ff; }.recommendation-item strong, .recommendation-item span { display: block; }.recommendation-item strong { margin-bottom: 4px; color: #1e3a5f; font-size: 12px; }.recommendation-item span { color: #64748b; font-size: 11px; line-height: 1.4; }.sql-editor :deep(textarea) { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 13px; }.result-alert { margin: 16px 0; }.result-layout { display: grid; grid-template-columns: 1.05fr 1fr; gap: 16px; }.card-header { display: flex; justify-content: space-between; align-items: center; gap: 10px; }.result-meta { color: #64748b; font-size: 12px; }.graph-canvas { height: 590px; overflow: hidden; background: radial-gradient(circle at 1px 1px, #d6e0eb 1px, transparent 1.2px); background-size: 22px 22px; }.node-detail, .node-detail-placeholder { margin: 12px 14px 14px; padding: 12px; border: 1px solid #e2e8f0; border-radius: 10px; background: #f8fafc; }.node-detail-heading { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; color: #0f172a; font-size: 13px; }.node-detail-dot { width: 10px; height: 10px; border-radius: 50%; }.node-detail-properties { background: #fff; }.node-detail-placeholder { color: #64748b; font-size: 12px; }.table-result-card { min-width: 0; } @media (max-width: 1200px) { .query-form, .result-layout, .recommendation-list { grid-template-columns: 1fr; }.graph-canvas { height: 460px; } }
</style>
