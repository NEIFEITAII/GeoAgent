<template>
  <div class="chart-body">
    <div v-if="items.length" class="pie-wrap">
      <div class="pie" :style="{ background: pieBackground }"></div>
      <ul class="chart-legend">
        <li v-for="(item, i) in items" :key="i" class="legend-item">
          <span class="legend-dot" :style="{ background: color(i) }"></span>
          <span class="legend-name">{{ item.name }}</span>
          <span class="legend-val">{{ item.pct }}% · {{ fmt(item.value) }}</span>
        </li>
      </ul>
    </div>
    <p v-else class="chart-empty">饼图数据为空</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { color, fmt } from '../../chartUtils'

const props = defineProps({
  labels: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
})

const items = computed(() => {
  const s = props.series[0]
  if (!s) return []
  const total = s.values.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0)
  if (total <= 0) return []
  return s.values.map((v, i) => ({
    name: props.labels[i] || `分类${i + 1}`,
    value: v,
    pct: total ? Math.round((v / total) * 1000) / 10 : 0,
  }))
})

const pieBackground = computed(() => {
  const total = items.value.reduce((a, b) => a + b.value, 0)
  if (!total) return ''
  let cursor = 0
  const stops = items.value.map((item, i) => {
    const start = (cursor / total) * 100
    cursor += item.value
    const end = (cursor / total) * 100
    return `${color(i)} ${start}% ${end}%`
  })
  return `conic-gradient(${stops.join(',')})`
})
</script>
