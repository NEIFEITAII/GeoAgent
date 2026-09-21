<template>
  <section class="template-workflow">
    <h2>固定模板问数</h2>
    <p>步骤一：上传Word模板。系统会识别正文、表格和需要填写的数据，并与标准问题库匹配。</p>
    <p><a href="/api/report-templates/sample">下载标准问题库示例模板</a>（首次演示请使用这份模板）</p>
    <input type="file" accept=".docx" :disabled="busy || chat.streaming" @change="upload" />
    <p v-if="busy">正在解析模板…</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="result">
      <p>模板解析完成：识别到{{ result.template.sections.length }}个章节、{{ result.template.blocks.length }}处正文或表格、{{ result.template.slots.length }}个逻辑槽位、{{ result.atomic_items.length }}个原子查询项，并整理出{{ result.questions.length }}组问题。</p>
      <p>图表锚点：{{ result.template.chart_anchors.length }}个。每个原子查询项都保存了来源和回填位置。</p>
      <p>本次业务期为{{ result.template.report_year }}年，解析时间为{{ formatTime(result.template.parsed_at) }}。</p>
      <button @click="download">下载模板解析结果和问数清单</button>
      <details><summary>查看模板解析结果（JSON）</summary><pre>{{ JSON.stringify(result.template, null, 2) }}</pre></details>
      <p v-for="warning in result.template.warnings" :key="warning">{{ warning }}</p>
      <h3>步骤二：确认问题并逐项查询</h3>
      <p>下面的问题由模板自动整理。请检查原文、提取问题和标准问题，确认后逐项发送到下方智能体对话框。</p>
      <p v-if="!result.questions.length">没有识别到待填项，请确认上传的是带占位符或空表格的模板，而非已生成的报告。</p>
      <ol>
        <li v-for="q in result.questions" :key="q.id">
          <label :for="q.id">{{ q.id }} · 来源 {{ q.source_block }}</label>
          <div class="source-compare">
            <div><strong>模板原文</strong><p>{{ q.source_text }}</p></div>
            <div><strong>提取的问题（可以修改）</strong><textarea :id="q.id" v-model="q.question" :disabled="q.confirmed" rows="4" /></div>
          </div>
          <p class="requirements">统计要求：{{ q.requirements }}</p>
          <label class="library-select">
            对应标准问题
            <select v-model="q.selectedLibraryId" :disabled="q.executing || q.execution">
              <option value="">请选择</option>
              <option v-for="candidate in candidates(q)" :key="candidate.id" :value="candidate.id">
                {{ candidate.id }} · {{ candidate.question }}（匹配度 {{ Math.round(candidate.confidence * 100) }}%）
              </option>
            </select>
          </label>
          <p v-if="!candidates(q).length" class="warning">问题库暂无可靠候选，请先人工补充对应关系。</p>
          <details class="atomic-items">
            <summary>查看本组{{ q.atomic_item_ids.length }}个原子查询项</summary>
            <ul>
              <li v-for="item in itemsForQuestion(q)" :key="item.id">
                <strong>{{ item.id }} · {{ item.metric }}</strong>
                <span>类型：{{ item.expected_type }}；单位：{{ item.unit || '待确认' }}</span>
                <span>回填：{{ item.binding.target_type }} · {{ item.binding.slot_id }}</span>
              </li>
            </ul>
          </details>
          <label><input v-model="q.confirmed" type="checkbox" :disabled="q.executing || q.execution" />我已核对原文、问题和统计口径</label>
          <button :disabled="!canAsk(q)" @click="askAgent(q)">{{ questionButtonText(q) }}</button>
          <div v-if="q.execution" class="query-result">
            <strong>查询结果：{{ q.execution.result.row_count }}行</strong>
            <table>
              <thead><tr><th v-for="column in q.execution.result.columns" :key="column">{{ columnLabel(column) }}</th></tr></thead>
              <tbody>
                <tr v-for="(row, index) in q.execution.result.rows.slice(0, 10)" :key="index">
                  <td v-for="column in q.execution.result.columns" :key="column">{{ row[column] }}</td>
                </tr>
              </tbody>
            </table>
            <p v-if="q.execution.result.row_count > 10">这里只预览前10行，完整结果已经保存用于回填。</p>
          </div>
        </li>
      </ol>
      <h3>步骤三：生成报告</h3>
      <p>每个问题都在智能体对话中回答并保存结果后，系统使用同一批结果回填正文和表格，并生成饼图、柱状图及Word/PDF文件。生成过程和下载文件也会显示在下方智能体对话框。</p>
      <button :disabled="!allExecuted || generating || generated" @click="generate">{{ generationButtonText }}</button>
    </template>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { chat } from '../stores/chat'

const result = ref(null)
const busy = ref(false)
const error = ref('')
const generating = ref(false)
const generated = ref(null)

const allExecuted = computed(() => (
  Boolean(result.value?.questions.length)
  && result.value.questions.every(question => Boolean(question.execution))
))
const generationButtonText = computed(() => {
  if (generated.value) return '报告已发送到智能体'
  if (generating.value) return '智能体正在生成…'
  return '发送到智能体并生成Word和PDF'
})

async function upload(event) {
  const file = event.target.files?.[0]
  if (!file) return
  result.value = null
  generated.value = null
  error.value = ''
  if (!file.name.toLowerCase().endsWith('.docx') || file.size > 10 * 1024 * 1024) {
    error.value = '请选择不超过10MB的DOCX模板。'
    return
  }
  busy.value = true
  try {
    const response = await fetch(`/api/report-templates/parse?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '解析失败')
    data.questions = data.questions.map(question => ({
      ...question,
      confirmed: false,
      executing: false,
      execution: null,
      selectedLibraryId: question.question_library_match?.candidates?.[0]?.id || '',
    }))
    result.value = data
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function download() {
  const url = URL.createObjectURL(new Blob([JSON.stringify(result.value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = '模板解析与问数.json'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function candidates(question) {
  return question.question_library_match?.candidates || []
}

function canAsk(question) {
  return question.confirmed && question.selectedLibraryId && !question.executing
    && !question.execution && !chat.streaming
}

function questionButtonText(question) {
  if (question.execution) return '问数完成'
  if (question.executing) return '智能体查询中…'
  return '发送到智能体'
}

async function askAgent(question) {
  if (!canAsk(question)) return
  question.executing = true
  error.value = ''
  try {
    const response = await fetch(
      `/api/report-templates/${result.value.workflow_id}/questions/${question.id}/execute`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          library_id: question.selectedLibraryId,
          question: question.question,
          conversation_id: chat.currentId,
        }),
      },
    )
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '查询失败')
    question.execution = data
    await chat.refreshMessages()
  } catch (e) {
    error.value = e.message
  } finally {
    question.executing = false
  }
}

async function generate() {
  if (!allExecuted.value) return
  generating.value = true
  generated.value = null
  error.value = ''
  try {
    const response = await fetch(`/api/report-templates/${result.value.workflow_id}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: chat.currentId,
        request_text: '请根据以上问数结果生成Word和PDF快报。',
      }),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '报告生成失败')
    generated.value = data
    await chat.refreshMessages()
  } catch (e) {
    error.value = e.message
  } finally {
    generating.value = false
  }
}

function itemsForQuestion(question) {
  const ids = new Set(question.atomic_item_ids || [])
  return result.value.atomic_items.filter(item => ids.has(item.id))
}

function formatTime(value) {
  if (!value) return '未知'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function columnLabel(value) {
  return {
    n: '图斑数量',
    area_mu: '面积（亩）',
    TBLX: '图斑类型',
    region: '县（市、区）',
  }[value] || value
}
</script>

<style scoped>
.template-workflow { padding: 16px 24px; max-height: 55vh; overflow: auto; border-bottom: 1px solid #ddd; }
h2 { font-size: 18px; }
h3 { font-size: 16px; }
p, li { font-size: 14px; line-height: 1.7; }
textarea { display: block; width: 100%; margin: 8px 0; padding: 8px; font: inherit; }
.source-compare { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 10px 0; }
.source-compare > div { padding: 10px; background: #f7f8fa; border: 1px solid #ddd; border-radius: 6px; }
.source-compare p { margin: 8px 0 0; white-space: pre-wrap; }
.requirements { color: #555; }
.library-select { display: block; margin: 8px 0; }
.library-select select { display: block; width: 100%; margin-top: 6px; padding: 7px; }
.atomic-items { margin: 8px 0; }
.atomic-items ul { padding-left: 20px; }
.atomic-items li { margin: 6px 0; }
.atomic-items span { display: block; color: #666; font-size: 13px; }
button { padding: 6px 12px; margin: 4px 0; cursor: pointer; }
button:disabled { cursor: default; opacity: .5; }
.query-result { margin: 10px 0; overflow-x: auto; }
.query-result table { width: 100%; border-collapse: collapse; margin-top: 8px; }
.query-result th, .query-result td { border: 1px solid #d8dde5; padding: 6px 8px; text-align: center; white-space: nowrap; }
.query-result th { background: #eef4fa; }
.warning { color: #9a5a00; }
.generated-files { margin: 10px 0; padding: 10px; background: #f3f8f3; border: 1px solid #cfe3cf; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 200px; overflow: auto; }
@media (max-width: 800px) { .source-compare { grid-template-columns: 1fr; } }
</style>
