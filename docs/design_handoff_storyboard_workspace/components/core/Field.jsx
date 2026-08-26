import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function Field({ label, htmlFor, hint, error, plainLabel = false, aside, row = false, className, style, children }) {
  const labelEl = label ? (
    <label className={cx('ms-label', plainLabel && 'ms-label--plain')} htmlFor={htmlFor}>
      {label}
    </label>
  ) : null
  return (
    <div className={cx(row ? 'ms-field-row' : 'ms-field', className)} style={style}>
      {labelEl && aside ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {labelEl}
          {aside}
        </div>
      ) : (
        labelEl
      )}
      {children}
      {hint ? <span className="ms-hint">{hint}</span> : null}
      {error ? <span className="ms-error">{error}</span> : null}
    </div>
  )
}
