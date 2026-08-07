<template>
  <div class="instance-page">
    <section class="page-header">
      <div><div class="eyebrow">PROPERTY GRAPH INSTANCES</div><h2>实例数据展示</h2><p>当前业务分析域：{{ appStore.currentDomainName || '未选择' }}。选择本体节点并按属性条件查询，结果以实例子图展示。</p></div>
    </section>
    <el-alert v-if="!currentDomainId" type="warning" :closable="false" show-icon title="请先在页面顶部选择业务分析域" />
    <el-card v-else shadow="never" class="query-card">
      <div class="query-grid">
        <el-select v-model="sourceId" placeholder="选择当前业务域数据源" @change="handleSourceChange"><el-option v-for="source in sources" :key="source.source_id" :label="`${source.source_name} / ${source.schema_name || source.username}`" :value="source.source_id" /></el-select>
        <el-select v-model="graphName" placeholder="选择 Property Graph" :disabled="!sourceId" @change="loadTopology"><el-option v-for="graph in graphs" :key="graph.graph_name" :label="graph.graph_name" :value="graph.graph_name" /></el-select>
        <el-select v-model="nodeId" placeholder="选择本体节点" :disabled="!graphName" @change="resetCondition"><el-option v-for="node in topologyNodes" :key="node.id" :label="node.displayName || node.name" :value="node.id" /></el-select>
        <el-select v-model="propertyName" placeholder="选择条件属性（可选）" :disabled="!selectedNode"><el-option v-for="property in selectedNode?.properties || []" :key="property.property_name" :label="property.property_display_name || property.property_name" :value="property.property_name" /></el-select>
        <el-select v-model="operator" :disabled="!propertyName" class="operator"><el-option label="包含" value="contains" /><el-option label="等于" value="equals" /><el-option label="大于" value="greater_than" /><el-option label="小于" value="less_than" /></el-select>
        <el-input v-model="conditionValue" :disabled="!propertyName" placeholder="输入查询条件" @keyup.enter="queryInstances" />
        <el-input-number v-model="rowLimit" :min="1" :max="100" controls-position="right" />
        <el-button type="primary" :disabled="!nodeId" :loading="loading" @click="queryInstances">查询实例</el-button>
      </div>
      <div class="hint">不填条件属性时，返回所选节点前 {{ rowLimit }} 条实例；已命中的实例会展示其一跳关联实例。</div>
    </el-card>
    <el-alert v-if="result" :type="result.rows?.length ? 'success' : 'info'" :closable="false" show-icon class="result-summary" :title="`查询到 ${result.rows?.length || 0} 个 ${selectedNode?.displayName || '节点'} 实例，以及 ${(result.edges || []).length} 条一跳关系`" />
    <div v-if="result" class="result-grid">
      <el-card shadow="never" class="graph-card"><template #header><div class="graph-card-header"><span>{{ lineageActive ? '上下游全链路实例图' : '实例子图' }}</span><span class="graph-count">展示 {{ visibleGraphNodes.length }} / {{ result.nodes?.length || 0 }} 个实例</span></div></template>
        <div v-if="entityTypes.length" class="entity-filter">
          <span class="filter-label">展示实体</span>
          <el-checkbox-group v-model="visibleEntityTypes" class="entity-checkboxes">
            <el-checkbox-button v-for="entity in entityTypes" :key="entity.key" :value="entity.key">{{ entity.label }}（{{ entity.count }}）</el-checkbox-button>
          </el-checkbox-group>
        </div>
        <div v-if="result.nodes?.length" ref="graphChartRef" class="graph-canvas"></div><el-empty v-else description="没有匹配的实例数据" />
      </el-card>
      <el-card shadow="never" class="table-card"><template #header><span>命中实例数据（点击记录查看全链路）</span></template><el-table :data="result.rows || []" border stripe size="small" max-height="560" highlight-current-row @row-click="showLineage"><el-table-column v-for="column in resultColumns" :key="column" :prop="column" :label="column" min-width="140" show-overflow-tooltip /></el-table></el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { graphApi, sourceApi } from '../../api'
import { useAppStore } from '../../stores/app'
const appStore = useAppStore(); const currentDomainId = computed(() => appStore.currentDomainId)
const sources = ref<any[]>([]); const graphs = ref<any[]>([]); const topologyNodes = ref<any[]>([]); const topologyEdges = ref<any[]>([]); const sourceId = ref(''); const graphName = ref(''); const nodeId = ref(''); const propertyName = ref(''); const operator = ref('contains'); const conditionValue = ref(''); const rowLimit = ref(30); const result = ref<any>(null); const loading = ref(false); const lineageActive = ref(false)
const graphChartRef = ref<HTMLElement | null>(null); const visibleEntityTypes = ref<string[]>([]); let graphChart: echarts.ECharts | null = null
const selectedNode = computed(() => topologyNodes.value.find(node => node.id === nodeId.value)); const resultColumns = computed(() => Object.keys(result.value?.rows?.[0] || {}))
const trim = (value: any, size: number) => { const text = String(value ?? ''); return text.length > size ? `${text.slice(0, size)}…` : text }
const entityPalette = ['#2563eb', '#0891b2', '#059669', '#7c3aed', '#ea580c', '#db2777', '#4f46e5', '#65a30d']
const entityTypes = computed(() => {
  const counters = new Map<string, { key: string, label: string, count: number }>()
  ;(result.value?.nodes || []).forEach((node: any) => { const key = String(node.node_id || node.label || 'unknown'); const current = counters.get(key) || { key, label: node.label || '未命名实体', count: 0 }; current.count += 1; counters.set(key, current) })
  return Array.from(counters.values())
})
const visibleGraphNodes = computed(() => (result.value?.nodes || []).filter((node: any) => visibleEntityTypes.value.includes(String(node.node_id || node.label || 'unknown'))))
const resetEntityFilter = () => { visibleEntityTypes.value = entityTypes.value.map(item => item.key) }
const renderGraph = async () => {
  await nextTick()
  if (!graphChartRef.value) return
  if (!graphChart) graphChart = echarts.init(graphChartRef.value)
  const visibleIds = new Set(visibleGraphNodes.value.map((node: any) => node.id))
  const categories = entityTypes.value.map((entity, index) => ({ name: entity.label, itemStyle: { color: entityPalette[index % entityPalette.length] } }))
  const categoryIndex = new Map(entityTypes.value.map((entity, index) => [entity.key, index]))
  const nodes = visibleGraphNodes.value.map((node: any) => ({
    id: node.id,
    name: `${node.label || '实体'}\n${trim(node.instance_label, 16)}`,
    category: categoryIndex.get(String(node.node_id || node.label || 'unknown')) || 0,
    symbol: 'circle',
    symbolSize: node.selected ? 72 : 60,
    value: node.instance_label,
    itemStyle: node.selected ? { borderColor: '#1d4ed8', borderWidth: 3, shadowBlur: 12, shadowColor: 'rgba(37, 99, 235, .32)' } : { borderColor: '#fff', borderWidth: 2, shadowBlur: 8, shadowColor: 'rgba(15, 23, 42, .18)' }
  }))
  const links = (result.value?.edges || []).filter((edge: any) => visibleIds.has(edge.source) && visibleIds.has(edge.target)).map((edge: any) => ({ source: edge.source, target: edge.target, value: edge.label || '关联' }))
  graphChart.setOption({
    animationDurationUpdate: 450,
    tooltip: { trigger: 'item', formatter: (params: any) => params.dataType === 'edge' ? `关系：${params.data.value}` : `<b>${params.data.name.replace('\n', '</b><br/>')}` },
    series: [{
      type: 'graph', layout: 'force', roam: true, draggable: true, data: nodes, links, categories,
      edgeSymbol: ['none', 'arrow'], edgeSymbolSize: [0, 9],
      label: { show: true, position: 'inside', color: '#fff', fontSize: 10, lineHeight: 14, overflow: 'truncate', width: 54 },
      edgeLabel: { show: true, formatter: (params: any) => params.data.value, color: '#475569', fontSize: 10, backgroundColor: 'rgba(255,255,255,.88)', padding: [3, 5], borderRadius: 4 },
      lineStyle: { color: '#94a3b8', width: 1.5, curveness: .12, opacity: .86 },
      emphasis: { focus: 'adjacency', lineStyle: { width: 3, color: '#2563eb' } },
      force: { repulsion: 850, edgeLength: [110, 220], gravity: .09, layoutAnimation: true }
    }]
  }, true)
}
const resizeGraph = () => graphChart?.resize()
const setGraphResult = (data: any, isLineage: boolean) => { result.value = applyChineseLabels(data || {}); lineageActive.value = isLineage; resetEntityFilter(); void renderGraph() }
const resetCondition = () => { propertyName.value = ''; conditionValue.value = ''; result.value = null; lineageActive.value = false }
const loadSources = async () => { if (!currentDomainId.value) { sources.value=[]; return }; const res = await sourceApi.listDataSources(currentDomainId.value); sources.value=(res.data||[]).filter((item:any)=>(item.db_type||'').toLowerCase()==='oracle'); sourceId.value=sources.value.find((item:any)=>item.is_default==='Y')?.source_id || sources.value[0]?.source_id || ''; graphName.value=''; await loadTopology() }
const handleSourceChange = async () => { graphName.value=''; await loadTopology() }
const loadTopology = async () => { graphs.value=[]; topologyNodes.value=[]; topologyEdges.value=[]; nodeId.value=''; result.value=null; if (!sourceId.value || !currentDomainId.value) return; const res:any=await graphApi.getOntologyBrowseGraph(sourceId.value, graphName.value || undefined, currentDomainId.value); graphs.value=res.data?.graphs||[]; graphName.value=res.data?.graph_name||''; topologyNodes.value=res.data?.nodes||[]; topologyEdges.value=res.data?.edges||[] }
const applyChineseLabels = (data:any) => { data.nodes=(data.nodes||[]).map((node:any)=>({...node,label:topologyNodes.value.find((item:any)=>item.id===node.node_id)?.displayName||node.label})); data.edges=(data.edges||[]).map((edge:any)=>({...edge,label:topologyEdges.value.find((item:any)=>item.id===edge.edge_id)?.name||edge.label})); return data }
const queryInstances = async () => { loading.value=true; try { const res:any=await graphApi.queryOntologyGraphInstances({ domain_id: currentDomainId.value, source_id: sourceId.value, graph_name: graphName.value, node_id: nodeId.value, property_name: propertyName.value || null, operator: operator.value, value: conditionValue.value || null, row_limit: rowLimit.value }); setGraphResult(res.data, false) } finally { loading.value=false } }
const showLineage = async (row:any) => { const key=selectedNode.value?.properties?.find((item:any)=>item.is_primary_key==='Y')?.property_name; if (!key || row[key] == null) return; loading.value=true; try { const res:any=await graphApi.queryOntologyGraphInstanceLineage({domain_id:currentDomainId.value,source_id:sourceId.value,graph_name:graphName.value,node_id:nodeId.value,instance_key:String(row[key]),max_depth:12}); const lineage=res.data||{}; lineage.rows=result.value?.rows||[]; setGraphResult(lineage, true) } finally { loading.value=false } }
watch(visibleEntityTypes, () => { void renderGraph() }, { deep: true }); watch(() => appStore.currentDomainId, loadSources); onMounted(() => { window.addEventListener('resize', resizeGraph); loadSources() }); onBeforeUnmount(() => { window.removeEventListener('resize', resizeGraph); graphChart?.dispose(); graphChart = null })
</script>

<style scoped>
.instance-page{min-height:calc(100vh - 86px);padding:8px 0 20px}.page-header{margin:8px 0 16px}.eyebrow{color:#2563eb;font-size:11px;font-weight:800;letter-spacing:.14em}.page-header h2{margin:4px 0;color:#0f172a;font-size:25px}.page-header p,.hint{margin:0;color:#64748b;font-size:13px}.query-card,.graph-card,.table-card{border-color:#e4eaf2}.query-grid{display:grid;grid-template-columns:1.35fr 1.2fr 1.2fr 1.1fr .7fr 1fr 105px auto;gap:10px}.hint{margin-top:12px}.result-summary{margin:16px 0}.result-grid{display:grid;grid-template-columns:1.1fr 1fr;gap:16px}.graph-card-header{display:flex;align-items:center;justify-content:space-between;gap:12px}.graph-count{color:#64748b;font-size:12px;font-weight:400}.entity-filter{display:flex;align-items:flex-start;gap:10px;padding:10px 14px 4px}.filter-label{flex:none;padding-top:7px;color:#64748b;font-size:12px}.entity-checkboxes{display:flex;flex-wrap:wrap;gap:6px}.entity-checkboxes :deep(.el-checkbox-button__inner){border-left:1px solid var(--el-checkbox-button-checked-bg-color,#409eff);border-radius:6px!important;padding:6px 8px;font-size:12px}.graph-canvas{height:590px;overflow:hidden;background:radial-gradient(circle at 1px 1px,#d6e0eb 1px,transparent 1.2px);background-size:22px 22px}.table-card{min-width:0}@media(max-width:1280px){.query-grid,.result-grid{grid-template-columns:1fr}.graph-canvas{height:460px}}
</style>
