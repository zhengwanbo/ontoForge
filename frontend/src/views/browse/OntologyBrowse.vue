<template>
  <div class="browse-page">
    <section class="browse-header">
      <div>
        <div class="eyebrow">ONTOLOGY GRAPH</div>
        <h2>本体图谱浏览</h2>
        <p>图中的顶点和关系直接读取自所选 Oracle 数据源的 Property Graph 视图。</p>
      </div>
      <div class="header-actions">
        <el-select v-model="selectedSourceId" placeholder="选择 Oracle 数据源" filterable @change="handleSourceChange">
          <el-option v-for="source in sourceDataSources" :key="source.source_id" :label="`${source.source_name} / ${source.schema_name || source.username}`" :value="source.source_id" />
        </el-select>
        <el-select v-model="selectedGraphName" placeholder="选择 Property Graph" :disabled="!selectedSourceId || !availableGraphs.length" filterable @change="loadGraphData">
          <el-option v-for="graph in availableGraphs" :key="graph.graph_name" :label="graph.graph_name" :value="graph.graph_name" />
        </el-select>
        <el-button :icon="Refresh" :disabled="!selectedSourceId" @click="loadGraphData">刷新</el-button>
      </div>
    </section>

    <el-alert
      v-if="graphDeploymentWarning"
      type="warning"
      :closable="false"
      show-icon
      class="deployment-warning"
      :title="graphDeploymentWarning"
    />

    <main class="browse-layout">
      <section class="graph-card">
        <div class="graph-toolbar">
          <div class="graph-summary">
            <span><b>{{ graphNodes.length }}</b> 个本体对象</span>
            <i></i>
            <span><b>{{ graphEdges.length }}</b> 条关系</span>
          </div>
          <div class="graph-tools">
            <el-tooltip content="缩小"><el-button circle size="small" :icon="Minus" @click="adjustZoom(-0.15)" /></el-tooltip>
            <span class="zoom-label">{{ Math.round(viewport.zoom * 100) }}%</span>
            <el-tooltip content="放大"><el-button circle size="small" :icon="Plus" @click="adjustZoom(0.15)" /></el-tooltip>
            <el-button size="small" :icon="FullScreen" @click="fitView">适应画布</el-button>
            <el-button size="small" @click="resetLayout">自动布局</el-button>
          </div>
        </div>

        <div ref="graphCanvasRef" class="graph-canvas" :class="{ 'is-panning': interaction.mode === 'canvas' }">
          <svg
            v-if="graphNodes.length"
            class="ontology-svg"
            :viewBox="`0 0 ${canvasSize.width} ${canvasSize.height}`"
            @pointerdown="startCanvasPan"
            @pointermove="onPointerMove"
            @pointerup="endPointerInteraction"
            @pointerleave="endPointerInteraction"
            @wheel.prevent="onWheel"
          >
            <defs>
              <pattern id="ontology-grid" width="32" height="32" patternUnits="userSpaceOnUse">
                <circle cx="1.5" cy="1.5" r="1.2" fill="#cbd5e1" opacity=".7" />
              </pattern>
              <marker id="browse-arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">
                <path d="M0,0 L10,5 L0,10 z" fill="#94a3b8" />
              </marker>
              <filter id="node-shadow" x="-20%" y="-20%" width="140%" height="150%">
                <feDropShadow dx="0" dy="5" stdDeviation="5" flood-color="#0f172a" flood-opacity=".16" />
              </filter>
            </defs>
            <rect width="100%" height="100%" fill="url(#ontology-grid)" />
            <g :transform="`translate(${viewport.x} ${viewport.y}) scale(${viewport.zoom})`">
              <g v-for="edge in graphEdges" :key="edge.id" class="graph-edge">
                <path :d="getEdgePath(edge)" fill="none" stroke="#94a3b8" stroke-width="2" marker-end="url(#browse-arrow)" />
                <g :transform="`translate(${getEdgeLabelPoint(edge).x}, ${getEdgeLabelPoint(edge).y})`">
                  <rect :width="edgeLabelWidth(edge)" height="26" :x="-edgeLabelWidth(edge) / 2" y="-13" rx="13" fill="#fff" stroke="#dbe4ef" />
                  <text text-anchor="middle" y="4" class="edge-label">{{ edge.name || '关联' }}</text>
                </g>
              </g>

              <g
                v-for="node in graphNodes"
                :key="node.id"
                class="graph-node"
                :class="{ selected: selectedNode?.id === node.id }"
                :transform="`translate(${node.position.x}, ${node.position.y})`"
                @pointerdown.stop="startNodeDrag($event, node)"
                @click.stop="selectNode(node)"
              >
                <rect class="node-shell" :width="nodeSize.width" :height="nodeSize.height" rx="16" :fill="nodeFill(node)" filter="url(#node-shadow)" />
                <rect class="node-accent" width="6" :height="nodeSize.height - 20" x="10" y="10" rx="3" :fill="node.color || (node.buildType === 'VIEW' ? '#0ea5e9' : '#10b981')" />
                <text x="29" y="32" class="node-title">{{ truncate(node.displayName || node.name, 16) }}</text>
                <text x="29" y="54" class="node-name">{{ truncate(node.name, 24) }}</text>
                <line x1="28" x2="172" y1="68" y2="68" stroke="#e2e8f0" />
                <text x="29" y="90" class="node-meta">{{ node.buildType === 'VIEW' ? 'VIEW' : 'TABLE' }} · {{ node.properties?.length || 0 }} 属性</text>
                <circle cx="173" cy="85" r="5" :fill="node.status === 'DEPLOYED' ? '#22c55e' : '#f59e0b'" />
              </g>
            </g>
          </svg>
          <el-empty v-else description="请选择包含 Property Graph 的 Oracle 数据源" :image-size="96" />

          <div v-if="graphNodes.length" class="canvas-tip">拖动节点 · 滚轮缩放 · 空白处平移</div>
          <div v-if="graphNodes.length" class="legend">
            <span><i class="legend-dot table"></i>管理表</span>
            <span><i class="legend-dot view"></i>管理视图</span>
            <span><i class="legend-line"></i>有向本体关系</span>
          </div>
        </div>
      </section>

      <aside class="detail-panel">
        <template v-if="selectedNode">
          <div class="detail-heading">
            <span class="detail-color" :style="{ background: selectedNode.color || (selectedNode.buildType === 'VIEW' ? '#0ea5e9' : '#10b981') }"></span>
            <div><h3>{{ selectedNode.displayName || selectedNode.name }}</h3><code>{{ selectedNode.name }}</code></div>
          </div>
          <div class="detail-tags">
            <el-tag effect="plain">{{ selectedNode.buildType }}</el-tag>
            <el-tag :type="selectedNode.status === 'DEPLOYED' ? 'success' : 'warning'">{{ selectedNode.status }}</el-tag>
          </div>
          <dl class="detail-info">
            <div><dt>对象表</dt><dd><code>{{ selectedNode.tableName || '待生成' }}</code></dd></div>
            <div><dt>关系数量</dt><dd>{{ connectedRelationCount }}</dd></div>
            <div class="full"><dt>业务描述</dt><dd>{{ selectedNode.desc || '暂无描述' }}</dd></div>
          </dl>
          <div class="property-header"><h4>目标数据库中的实际属性</h4></div>
          <el-table :data="selectedNode.properties" size="small" max-height="360" class="property-table">
            <el-table-column prop="property_display_name" label="属性">
              <template #default="{ row }"><div>{{ row.property_display_name || row.property_name }}</div><code>{{ row.property_name }}</code></template>
            </el-table-column>
            <el-table-column prop="data_type" label="类型" width="86" />
            <el-table-column label="标记" width="65"><template #default="{ row }"><el-tag v-if="row.is_primary_key === 'Y'" type="danger" size="small">PK</el-tag></template></el-table-column>
          </el-table>
        </template>
        <el-empty v-else description="选择图中的节点，查看对象详情" :image-size="78" />
      </aside>
    </main>

  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { FullScreen, Minus, Plus, Refresh } from '@element-plus/icons-vue'
import { graphApi, sourceApi } from '../../api'

const selectedSourceId = ref('')
const selectedGraphName = ref('')
const sourceDataSources = ref<any[]>([])
const availableGraphs = ref<any[]>([])
const graphNodes = ref<any[]>([])
const graphEdges = ref<any[]>([])
const selectedNode = ref<any>(null)
const graphDeploymentWarning = ref('')
const graphCanvasRef = ref<HTMLElement | null>(null)
const nodeSize = { width: 196, height: 104 }
const canvasSize = { width: 1500, height: 860 }
const viewport = reactive({ x: 42, y: 42, zoom: 1 })
const interaction = reactive({ mode: '' as 'node' | 'canvas' | '', node: null as any, startX: 0, startY: 0, originX: 0, originY: 0, moved: false })

const connectedRelationCount = computed(() => graphEdges.value.filter(edge => edge.source === selectedNode.value?.id || edge.target === selectedNode.value?.id).length)
const positionStorageKey = () => `ontology-browse-positions:${selectedSourceId.value}:${selectedGraphName.value}`

const truncate = (value: string, length: number) => value && value.length > length ? `${value.slice(0, length)}…` : value
const nodeFill = (node: any) => node.buildType === 'VIEW' ? '#f0f9ff' : '#f0fdf4'
const edgeLabelWidth = (edge: any) => Math.max(54, Math.min(136, String(edge.name || '关联').length * 15 + 28))

const loadSourceDataSources = async () => {
  try {
    const res = await sourceApi.listDataSources()
    sourceDataSources.value = (res.data || []).filter((source: any) => (source.db_type || '').toLowerCase() === 'oracle')
    if (!sourceDataSources.value.some(source => source.source_id === selectedSourceId.value)) {
      selectedSourceId.value = sourceDataSources.value.find(source => source.is_default === 'Y')?.source_id || ''
    }
  } catch (_) { sourceDataSources.value = []; selectedSourceId.value = '' }
}

const buildAutoLayout = (nodes: any[], edges: any[]) => {
  const ids = new Set(nodes.map(node => node.id))
  const indegree = new Map(nodes.map(node => [node.id, 0]))
  const next = new Map(nodes.map(node => [node.id, [] as string[]]))
  edges.forEach(edge => { if (ids.has(edge.source) && ids.has(edge.target)) { indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1); next.get(edge.source)?.push(edge.target) } })
  const levels = new Map<string, number>()
  const queue = nodes.filter(node => !indegree.get(node.id)).map(node => node.id)
  queue.forEach(id => levels.set(id, 0))
  while (queue.length) {
    const id = queue.shift()!
    next.get(id)?.forEach(target => { indegree.set(target, (indegree.get(target) || 1) - 1); levels.set(target, Math.max(levels.get(target) || 0, (levels.get(id) || 0) + 1)); if (indegree.get(target) === 0) queue.push(target) })
  }
  let fallbackLevel = Math.max(0, ...Array.from(levels.values()))
  nodes.forEach(node => { if (!levels.has(node.id)) levels.set(node.id, fallbackLevel++) })
  const columns = new Map<number, any[]>()
  nodes.forEach(node => { const level = levels.get(node.id) || 0; columns.set(level, [...(columns.get(level) || []), node]) })
  columns.forEach((items, level) => items.forEach((node, index) => { node.position = { x: 110 + level * 290, y: 110 + index * 165 } }))
}

const restorePositions = (nodes: any[]) => {
  try {
    const saved = JSON.parse(localStorage.getItem(positionStorageKey()) || '{}')
    if (Object.keys(saved).length) { nodes.forEach(node => { if (saved[node.id]) node.position = saved[node.id] }); return true }
  } catch (_) {}
  return false
}
const persistPositions = () => { try { localStorage.setItem(positionStorageKey(), JSON.stringify(Object.fromEntries(graphNodes.value.map(node => [node.id, node.position])))) } catch (_) {} }

const loadGraphData = async () => {
  if (!selectedSourceId.value) { graphNodes.value = []; graphEdges.value = []; availableGraphs.value = []; return }
  try {
    const res = await graphApi.getOntologyBrowseGraph(selectedSourceId.value, selectedGraphName.value)
    availableGraphs.value = res.data?.graphs || []
    if (!availableGraphs.value.some((graph: any) => graph.graph_name === selectedGraphName.value)) {
      selectedGraphName.value = res.data?.graph_name || ''
    }
    graphDeploymentWarning.value = ''
    const nodes = (res.data?.nodes || []).map((node: any) => ({ ...node, position: { ...(node.position || {}) } }))
    const edges = res.data?.edges || []
    if (!restorePositions(nodes)) buildAutoLayout(nodes, edges)
    graphNodes.value = nodes
    graphEdges.value = edges
    selectedNode.value = nodes.find((node: any) => node.id === selectedNode.value?.id) || null
    requestAnimationFrame(fitView)
  } catch (_) { graphDeploymentWarning.value = '' }
}

const getCanvasPoint = (event: PointerEvent | WheelEvent) => {
  const rect = graphCanvasRef.value?.getBoundingClientRect()
  if (!rect) return null
  return { x: (event.clientX - rect.left) * canvasSize.width / rect.width, y: (event.clientY - rect.top) * canvasSize.height / rect.height }
}
const getNodeCenter = (nodeId: string) => { const node = graphNodes.value.find(item => item.id === nodeId); return { x: (node?.position?.x || 0) + nodeSize.width / 2, y: (node?.position?.y || 0) + nodeSize.height / 2 } }
const boundaryPoint = (from: any, to: any, gap = 0) => { const hw = nodeSize.width / 2 + gap; const hh = nodeSize.height / 2 + gap; const dx = to.x - from.x; const dy = to.y - from.y; const scale = 1 / Math.max(Math.abs(dx || 1) / hw, Math.abs(dy || 1) / hh); return { x: from.x + dx * scale, y: from.y + dy * scale } }
const getEdgeGeometry = (edge: any) => { const source = getNodeCenter(edge.source); const target = getNodeCenter(edge.target); return { start: boundaryPoint(source, target, 3), end: boundaryPoint(target, source, 12), source, target } }
const getEdgePath = (edge: any) => { const { start, end } = getEdgeGeometry(edge); const dx = Math.max(90, Math.abs(end.x - start.x) * .45); return `M ${start.x} ${start.y} C ${start.x + dx} ${start.y}, ${end.x - dx} ${end.y}, ${end.x} ${end.y}` }
const getEdgeLabelPoint = (edge: any) => { const { start, end } = getEdgeGeometry(edge); return { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 - 16 } }

const selectNode = (node: any) => { if (!interaction.moved) selectedNode.value = node }
const startNodeDrag = (event: PointerEvent, node: any) => { const point = getCanvasPoint(event); if (!point) return; interaction.mode = 'node'; interaction.node = node; interaction.startX = (point.x - viewport.x) / viewport.zoom - node.position.x; interaction.startY = (point.y - viewport.y) / viewport.zoom - node.position.y; interaction.moved = false; ;(event.currentTarget as Element).setPointerCapture?.(event.pointerId) }
const startCanvasPan = (event: PointerEvent) => { if (event.button !== 0) return; interaction.mode = 'canvas'; interaction.startX = event.clientX; interaction.startY = event.clientY; interaction.originX = viewport.x; interaction.originY = viewport.y; interaction.moved = false }
const onPointerMove = (event: PointerEvent) => {
  if (!interaction.mode) return
  if (interaction.mode === 'node' && interaction.node) { const point = getCanvasPoint(event); if (!point) return; const position = { x: Math.max(20, (point.x - viewport.x) / viewport.zoom - interaction.startX), y: Math.max(20, (point.y - viewport.y) / viewport.zoom - interaction.startY) }; interaction.moved = Math.abs(position.x - interaction.node.position.x) > 1 || Math.abs(position.y - interaction.node.position.y) > 1; interaction.node.position = position }
  if (interaction.mode === 'canvas') { const rect = graphCanvasRef.value?.getBoundingClientRect(); if (!rect) return; viewport.x = interaction.originX + (event.clientX - interaction.startX) * canvasSize.width / rect.width; viewport.y = interaction.originY + (event.clientY - interaction.startY) * canvasSize.height / rect.height; interaction.moved = true }
}
const endPointerInteraction = () => { if (interaction.mode === 'node' && interaction.moved) persistPositions(); interaction.mode = ''; interaction.node = null }
const adjustZoom = (delta: number) => { viewport.zoom = Math.min(1.8, Math.max(.35, Number((viewport.zoom + delta).toFixed(2)))) }
const onWheel = (event: WheelEvent) => adjustZoom(event.deltaY > 0 ? -.1 : .1)
const fitView = () => { if (!graphNodes.value.length) return; const xs = graphNodes.value.map(node => node.position.x); const ys = graphNodes.value.map(node => node.position.y); const minX = Math.min(...xs); const minY = Math.min(...ys); const maxX = Math.max(...xs) + nodeSize.width; const maxY = Math.max(...ys) + nodeSize.height; const zoom = Math.min(1.25, Math.max(.45, Math.min((canvasSize.width - 150) / Math.max(1, maxX - minX), (canvasSize.height - 130) / Math.max(1, maxY - minY)))); viewport.zoom = Number(zoom.toFixed(2)); viewport.x = (canvasSize.width - (maxX - minX) * zoom) / 2 - minX * zoom; viewport.y = (canvasSize.height - (maxY - minY) * zoom) / 2 - minY * zoom }
const resetLayout = () => { buildAutoLayout(graphNodes.value, graphEdges.value); persistPositions(); fitView() }
const handleSourceChange = () => { selectedNode.value = null; selectedGraphName.value = ''; availableGraphs.value = []; loadGraphData() }

onMounted(async () => { await loadSourceDataSources(); if (selectedSourceId.value) await loadGraphData() })
onBeforeUnmount(() => { interaction.mode = '' })
</script>

<style scoped>
.browse-page { min-height: calc(100vh - 86px); padding: 4px 0 18px; color: #1e293b; }
.browse-header { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; padding: 12px 4px 18px; }
.eyebrow { color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .14em; }
.browse-header h2 { margin: 5px 0 4px; font-size: 25px; letter-spacing: -.02em; }.browse-header p { margin: 0; color: #64748b; font-size: 13px; }
.header-actions { display: flex; gap: 10px; align-items: center; }.header-actions .el-select { width: 230px; }.header-actions .el-select + .el-select { width: 270px; }
.deployment-warning { margin: 0 4px 16px; }
.browse-layout { display: grid; grid-template-columns: minmax(0, 1fr) 350px; gap: 16px; min-height: calc(100vh - 175px); }
.graph-card, .detail-panel { background: #fff; border: 1px solid #e5eaf1; border-radius: 16px; box-shadow: 0 8px 28px rgba(15, 23, 42, .05); overflow: hidden; }
.graph-card { display: flex; flex-direction: column; min-height: 590px; }.graph-toolbar { display: flex; justify-content: space-between; align-items: center; min-height: 54px; padding: 0 16px; border-bottom: 1px solid #edf1f6; }
.graph-summary { display: flex; align-items: center; gap: 9px; color: #64748b; font-size: 13px; }.graph-summary b { color: #0f172a; }.graph-summary i { width: 1px; height: 13px; background: #d6dee9; }.graph-tools { display: flex; align-items: center; gap: 6px; }.zoom-label { width: 42px; text-align: center; color: #64748b; font-size: 12px; }
.graph-canvas { position: relative; flex: 1; min-height: 530px; overflow: hidden; background: linear-gradient(145deg, #fbfdff, #f5f9ff); cursor: grab; touch-action: none; }.graph-canvas.is-panning { cursor: grabbing; }.ontology-svg { width: 100%; height: 100%; display: block; }.graph-node { cursor: grab; user-select: none; }.graph-node:active { cursor: grabbing; }.node-shell { stroke: #dbe4ef; stroke-width: 1; }.graph-node.selected .node-shell { stroke: #2563eb; stroke-width: 2.5; }.node-accent { pointer-events: none; }.node-title { fill: #0f172a; font-size: 15px; font-weight: 700; }.node-name { fill: #64748b; font-size: 11px; }.node-meta { fill: #64748b; font-size: 10px; }.edge-label { fill: #475569; font-size: 11px; font-weight: 600; pointer-events: none; }
.canvas-tip { position: absolute; left: 16px; bottom: 14px; padding: 7px 10px; border: 1px solid #e2e8f0; border-radius: 8px; background: rgba(255,255,255,.92); color: #64748b; font-size: 11px; }.legend { position: absolute; right: 16px; bottom: 14px; display: flex; gap: 12px; padding: 7px 10px; border: 1px solid #e2e8f0; border-radius: 8px; background: rgba(255,255,255,.92); color: #64748b; font-size: 11px; }.legend span { display: flex; align-items: center; gap: 5px; }.legend-dot { width: 8px; height: 8px; border-radius: 50%; }.legend-dot.table { background: #10b981; }.legend-dot.view { background: #0ea5e9; }.legend-line { width: 18px; height: 2px; background: #94a3b8; position: relative; }.legend-line::after { content: ''; position: absolute; right: -1px; top: -3px; border: 4px solid transparent; border-left-color: #94a3b8; }
.detail-panel { padding: 20px; overflow: auto; }.detail-heading { display: flex; align-items: center; gap: 10px; }.detail-color { width: 12px; height: 42px; border-radius: 8px; }.detail-heading h3 { margin: 0 0 3px; font-size: 18px; }.detail-heading code, .property-table code { color: #64748b; font-size: 11px; }.detail-tags { display: flex; gap: 7px; margin: 16px 0; }.detail-info { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 0 0 20px; }.detail-info div { padding: 10px; background: #f8fafc; border-radius: 9px; }.detail-info .full { grid-column: 1 / -1; }.detail-info dt { color: #94a3b8; font-size: 11px; }.detail-info dd { margin: 4px 0 0; color: #334155; font-size: 12px; line-height: 1.55; word-break: break-word; }.property-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }.property-header h4 { margin: 0; font-size: 15px; }.property-table { border-top: 1px solid #edf1f6; }
@media (max-width: 1100px) { .browse-layout { grid-template-columns: 1fr; }.detail-panel { min-height: 260px; }.browse-header { align-items: flex-start; flex-direction: column; }.header-actions { width: 100%; }.header-actions .el-select { flex: 1; } }
</style>
