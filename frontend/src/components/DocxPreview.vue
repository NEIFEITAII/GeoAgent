<template>
  <div class="docx-preview">
    <div ref="box" class="docx-box"></div>
    <p v-if="error" class="docx-error">预览失败：{{ error }}</p>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { renderAsync } from 'docx-preview'

const props = defineProps({
  src: { type: String, required: true },
})

const box = ref(null)
const error = ref('')

onMounted(async () => {
  try {
    const resp = await fetch(props.src)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.arrayBuffer()
    await renderAsync(data, box.value, undefined, {
      className: 'docx',
      inWrapper: true,
      ignoreWidth: false,
      ignoreHeight: false,
    })
  } catch (e) {
    error.value = e.message || String(e)
  }
})

onBeforeUnmount(() => {
  if (box.value) box.value.innerHTML = ''
})
</script>
