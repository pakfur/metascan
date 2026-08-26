import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function SubjectChip({ name, primary = false, title, onClick, className, style }) {
  return (
    <button
      type="button"
      className={cx('ms-subject-chip', primary && 'ms-subject-chip--primary', className)}
      style={style}
      title={title ?? (primary ? 'Primary subject' : 'Click to make primary')}
      onClick={onClick}
    >
      {primary ? <span className="ms-subject-chip__star">{'\u2605'}</span> : null}
      {name}
    </button>
  )
}
