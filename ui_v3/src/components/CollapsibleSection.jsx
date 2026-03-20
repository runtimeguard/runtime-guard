import React, { useState } from 'react'

export default function CollapsibleSection({
  icon,
  title,
  badges = [],
  children,
  defaultCollapsed = false,
}) {
  const [open, setOpen] = useState(!defaultCollapsed)

  return (
    <div
      style={{
        background: 'white',
        border: '1px solid #e5e7eb',
        borderRadius: 8,
        marginBottom: 8,
        overflow: 'hidden',
      }}
    >
      <div
        onClick={() => setOpen((v) => !v)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '12px 16px',
          cursor: 'pointer',
          userSelect: 'none',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = '#fafafa'
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = 'white'
        }}
      >
        <div style={{ width: 16, height: 16, color: '#9ca3af', flexShrink: 0 }}>{icon}</div>
        <span style={{ fontSize: 13, fontWeight: 600, color: '#111827', flex: 1 }}>{title}</span>
        <div style={{ display: 'flex', gap: 5 }}>
          {badges.map((badge, idx) => (
            <span
              key={`${badge.label}-${idx}`}
              style={{
                fontSize: 10,
                fontWeight: 600,
                padding: '2px 7px',
                borderRadius: 10,
                background: badge.style === 'red' ? '#fee2e2' : '#f3f4f6',
                color: badge.style === 'red' ? '#dc2626' : '#6b7280',
              }}
            >
              {badge.label}
            </span>
          ))}
        </div>
        <svg
          style={{
            width: 14,
            height: 14,
            color: '#9ca3af',
            flexShrink: 0,
            transform: open ? 'rotate(90deg)' : 'none',
            transition: 'transform 0.2s',
          }}
          viewBox="0 0 10 10"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <path d="M3 2l3 3-3 3" />
        </svg>
      </div>
      {open && <div style={{ borderTop: '1px solid #f3f4f6' }}>{children}</div>}
    </div>
  )
}
