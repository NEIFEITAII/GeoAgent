// 会话列表 / 对话头部共用的小工具函数

// 模型 id 较长（如 qwen3.7-max-2026-06-08），展示时截取前缀
export function shortModel(id) {
  if (!id) return ''
  return id.split('-').slice(0, 2).join('-')
}

// 会话更新时间展示：今天显示时分，其余显示日期
export function formatWhen(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const sameDay = (a, b) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (sameDay(d, now)) return hhmm
  const yesterday = new Date(now.getTime() - 86400000)
  if (sameDay(d, yesterday)) return `昨天 ${hhmm}`
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hhmm}`
}
