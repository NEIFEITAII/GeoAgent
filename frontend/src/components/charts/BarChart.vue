<template>
  <div class="chart-body">
    <div v-if="labels.length" class="bar-scroll">
      <div class="bar-plot" :style="{ minWidth: barMinWidth + 'px' }">
        <div v-for="(label, i) in labels" :key="i" class="bar-col">
          <div class="bar-group">
            <div
              v-for="(s, si) in series"
              :key="si"
              class="bar-track"
              :title="`${s.name} · ${label}: ${fmt(s.values[i])}`"
            >
              <div class="bar" :style="barStyle(s.values[i], si)"></div>
            </div>
          </div>
          <div class="bar-label" :title="label">{{ label }}</div>
        </div>
      </div>
    </div>
    <p v-else class="chart-empty">柱状图数据为空</p>
    <div v-if="series.length > 1" class="chart-legend inline">
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

const domain = computed(() => {
  let max = 0
  let min = 0
  for (const s of props.series) {
    for (const v of s.values) {
      if (!Number.isFinite(v)) continue
      if (v > max) max = v
      if (v < min) min = v
    }
  }
  return { max, min }
})

const barMinWidth = computed(() => Math.max(320, props.labels.length * 46))

function barStyle(value, si) {
  const { max, min } = domain.value
  const span = max - min || 1
  // 数值 0 对应的基线距容器底部的百分比；正数向上、负数向下绘制。
  const baselineBottom = (-min / span) * 100
  const h = (Math.abs(value) / span) * 100
  const bottom = value < 0 ? Math.max(0, baselineBottom - h) : baselineBottom
  const style = {
    height: `${Math.max(h, 1)}%`,
    bottom: `${bottom}%`,
    background: color(si),
  }
  if (value < 0) style.opacity = 0.55
  return style
}
</script>
