/**
 * Build a list of "mention chips" by scanning free text against the known
 * 6 projects and 26 legal entities (currently the project set is the
 * authoritative target because the entity directory snapshot already lives
 * in the executive store).
 *
 * @param {string} text  Markdown reply from the executive copilot.
 * @param {Array<{id:number, project_code:string, name:string}>} projects
 * @returns {Array<{kind:'project', code:string, label:string, projectId:number}>}
 */
export function detectProjectMentions(text, projects = []) {
  if (!text || typeof text !== 'string' || projects.length === 0) return []
  const seen = new Set()
  const chips = []

  for (const project of projects) {
    const code = project?.project_code
    const name = project?.name
    if (!code || !name) continue
    if (seen.has(code)) continue

    // Match either the project code (e.g. CD-TF-001) or the project name.
    // Names are scanned with the longest-first ordering to avoid prefix
    // collisions like "天府新区金融中心" vs "天府新区".
    const matchesCode = code && text.includes(code)
    const matchesName = name && text.includes(name)

    if (matchesCode || matchesName) {
      seen.add(code)
      chips.push({
        kind: 'project',
        code,
        label: name,
        projectId: project.id,
        shortCode: code
      })
    }
  }
  return chips
}

/**
 * Render text + chips by splitting the original markdown on chip match
 * positions. Each chip becomes a placeholder token (a 4-byte surrogate
 * index prefixed with `\u0001\u0000`).
 *
 * The consuming template iterates the returned segments and renders
 * either a `MentionChip` component or a plain text node.
 *
 * @param {string} text
 * @param {Array<{kind:'project', code:string, label:string, projectId:number, shortCode:string}>} chips
 * @returns {Array<{type:'text'|'chip', value:string, chip?:object}>}
 */
export function buildMentionSegments(text, chips = []) {
  if (!text) return []
  if (chips.length === 0) return [{ type: 'text', value: text }]

  const ordered = [...chips].sort((a, b) => {
    const al = a.label.length
    const bl = b.label.length
    return bl - al
  })

  const ranges = []
  for (const chip of ordered) {
    const targets = [chip.label, chip.code].filter(Boolean)
    for (const target of targets) {
      let idx = text.indexOf(target)
      while (idx !== -1) {
        const overlap = ranges.some(r => !(idx + target.length <= r.start || idx >= r.end))
        if (!overlap) {
          ranges.push({ start: idx, end: idx + target.length, chip })
          break
        }
        idx = text.indexOf(target, idx + 1)
      }
    }
  }

  if (ranges.length === 0) return [{ type: 'text', value: text }]
  ranges.sort((a, b) => a.start - b.start)

  const segments = []
  let cursor = 0
  for (const range of ranges) {
    if (range.start > cursor) {
      segments.push({ type: 'text', value: text.slice(cursor, range.start) })
    }
    segments.push({ type: 'chip', value: text.slice(range.start, range.end), chip: range.chip })
    cursor = range.end
  }
  if (cursor < text.length) {
    segments.push({ type: 'text', value: text.slice(cursor) })
  }
  return segments
}
