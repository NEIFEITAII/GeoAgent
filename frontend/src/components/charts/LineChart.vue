<template>
  <div class="chart-body">
    <svg v-if="labels.length" :viewBox="viewBox" class="line-svg">
      <g v-for="(tick, i) in yTicks" :key="'g' + i">
        <line
          class="grid-line"
          :x1="margin.left"
          :y1="tick.y"
          :x2="width - margin.right"
          :y2="tick.y"
        />
        <text class="axis-text y" :x="margin.left - 8" :y="tick.y + 4" text-anchor="end">
          {{ tick.text }}
        </text>
      </g>
      <polyline
        v-for="(s, si) in series"
        :key="'p' + si"
        class="line-path"
        :points="linePoints(s)"
        :stroke="color(si)"
      />
      <g v-for="(s, si) in series" :key="'c' + si">
        <circle
          v-for="(pt, i) in linePointList(s)"
          :key="i"
          :cx="pt.x"
          :cy="pt.y"
          r="2.6"
          :fill="color(si)"
        >
          <title>{{ s.name }} · {{ labels[i] }}: {{ fmt(s.values[i]) }}</title>
        </circle>
      </g>
      <text
        v-for="(label, i) in xLabels"
        :key="'x' + i"
        class="axis-text x"
        :x="label.x"
        :y="height - 14"
        text-anchor="middle"
      >
        {{ label.text }}
      </text>
    </svg>
    <p v-else class="chart-empty">折线图数据为空</p>
    <div v-if="series.length" class="chart-legend inline">
      <span v-for="(s, si) in series" :key="si" class="legend-item">
        <span class="legend-dot" :style="{ background: color(si) }"></span>
        <span class="legend-name">{{ s.name }}</span>
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { color, fmt } from '../../chartUtils'

const props = defineProps({
  labels: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
})

const width = 620
const height = 300
const margin = { left: 64, right: 18, top: 16, bottom: 44 }
const viewBox = computed(() => `0 0 ${width} ${height}`)

function range() {
  let max = 0
  let min = 0
  for (const s of props.series) {
    for (const v of s.values) {
      if (!Number.isFinite(v)) continue
      if (v > max) max = v
      if (v < min) min = v
    }
  }
  if (max === min) {
    max = Math.max(max, 1)
    min = Math.min(min, 0)
  }
  return { max, min }
}

const yTicks = computed(() => {
  const { max, min } = range()
  const plotH = height - margin.top - margin.bottom
  return [0, 1, 2, 3, 4].map((k) => {
    const v = min + ((max - min) * k) / 4
    return {
      y: margin.top + plotH - ((v - min) / (max - min || 1)) * plotH,
      text: fmt(v),
    }
  })
})

function seriesPoints(values) {
  const n = props.labels.length
  if (!n) return []
  const { max, min } = range()
  const plotW = width - margin.left - margin.right
  const plotH = height - margin.top - margin.bottom
  return values.map((v, i) => {
    const x = n === 1 ? margin.left + plotW / 2 : margin.left + (i * plotW) / (n - 1)
    const y = margin.top + plotH - ((v - min) / (max - min || 1)) * plotH
    return { x: Number(x.toFixed(2)), y: Number(y.toFixed(2)) }
  })
}

function linePointList(s) {
  return seriesPoints(s.values)
}

function linePoints(s) {
  return linePointList(s).map((p) => `${p.x},${p.y}`).join(' ')
}

const xLabels = computed(() => {
  const n = props.labels.length
  const step = Math.max(1, Math.ceil(n / 10))
  const plotW = width - margin.left - margin.right
  return props.labels
    .map((text, i) => {
      const x =
        n === 1 ? margin.left + plotW / 2 : margin.left + (i * plotW) / (n - 1)
      return { text, x: Number(x.toFixed(2)) }
    })
    .filter((_, i) => i % step === 0)
})
</script>
