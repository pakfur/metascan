import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

const VARIANTS = ['primary', 'secondary', 'danger', 'dangerSolid', 'quiet', 'dashed', 'link']

export function Button({
  children,
  label,
  variant = 'secondary',
  size = 'md',
  icon,
  iconRight,
  active = false,
  disabled = false,
  type = 'button',
  className,
  style,
  onClick,
  title,
  ...rest
}) {
  const v = VARIANTS.includes(variant) ? variant : 'secondary'
  const isLink = v === 'link'
  return (
    <button
      type={type}
      disabled={disabled}
      title={title}
      onClick={onClick}
      style={style}
      className={cx('ms-btn', 'ms-btn--' + v, !isLink && 'ms-btn--' + size, active && 'ms-btn__toggle-on', className)}
      {...rest}
    >
      {icon ? <span className={cx('pi', icon)} aria-hidden="true" /> : null}
      {label ?? children}
      {iconRight ? <span className={cx('pi', iconRight)} aria-hidden="true" /> : null}
    </button>
  )
}
