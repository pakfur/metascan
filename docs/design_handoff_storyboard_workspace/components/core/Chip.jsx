import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function Chip({ children, tone = 'neutral', icon, onDismiss, title, className, style, ...rest }) {
  return (
    <span
      title={title}
      style={style}
      className={cx('ms-chip', tone !== 'neutral' && 'ms-chip--' + tone, className)}
      {...rest}
    >
      {icon ? <span className={cx('pi', icon)} aria-hidden="true" /> : null}
      <span className="ms-chip__text">{children}</span>
      {onDismiss ? (
        <button type="button" className="ms-chip__dismiss" aria-label="Dismiss" onClick={onDismiss}>
          ×
        </button>
      ) : null}
    </span>
  )
}
