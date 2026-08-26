import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function Textarea({ variant = 'panel', mono = false, rows = 3, className, ...rest }) {
  return (
    <textarea
      rows={rows}
      className={cx('ms-control', variant === 'dialog' && 'ms-control--dialog', mono && 'ms-control--mono', className)}
      {...rest}
    />
  )
}
