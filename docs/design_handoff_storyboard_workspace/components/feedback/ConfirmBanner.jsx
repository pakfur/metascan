import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function ConfirmBanner({ children, message, size = 'sm', actions, className, style }) {
  return (
    <p className={cx('ms-confirm', size === 'lg' && 'ms-confirm--lg', className)} style={style}>
      {message ?? children}
      {actions ? <span className="ms-confirm__actions">{actions}</span> : null}
    </p>
  )
}
