import React, { useState } from 'react'

export function PencilIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M9.5 2.5l2 2L4 12H2v-2L9.5 2.5z" />
    </svg>
  )
}

export function RemoveIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
      <line x1="3" y1="3" x2="11" y2="11" />
      <line x1="11" y1="3" x2="3" y2="11" />
    </svg>
  )
}

export default function IconBtn({ icon, variant = 'default', title = '', onClick }) {
  const [hovered, setHovered] = useState(false)
  const hoverStyles = {
    default: { background: '#f3f4f6', color: '#374151' },
    danger: { background: '#fee2e2', color: '#dc2626' },
  }

  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        width: 22,
        height: 22,
        borderRadius: 4,
        border: 'none',
        background: hovered ? hoverStyles[variant].background : 'transparent',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: hovered ? hoverStyles[variant].color : '#9ca3af',
        transition: 'all 0.15s',
        padding: 0,
      }}
    >
      {icon}
    </button>
  )
}
