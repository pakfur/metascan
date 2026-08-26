import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function JobBadge({ state, value, max, error, layout = 'overlay', className, style }) {
  if (!state) return null
  const base = layout === 'fill' ? 'ms-job-fill' : 'ms-job-overlay'
  const failed = state === 'failed'
  let body = null
  if (state === 'queued') body = layout === 'fill' ? '\u23F3' : '\u23F3 queued'
  else if (state === 'running')
    body = (
      <>
        <span className="pi pi-spin pi-spinner ms-spin" aria-hidden="true" />
        {value != null && max != null ? <span>{value + '/' + max}</span> : null}
      </>
    )
  else if (failed) body = '\u26A0'
  return (
    <span
      className={cx(base, failed && base + '--failed', className)}
      style={style}
      title={failed ? (error ?? 'Generation failed') : state === 'queued' ? 'Queued' : 'Generating'}
    >
      {body}
    </span>
  )
}
