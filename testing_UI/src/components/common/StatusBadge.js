import React from 'react';

export default function StatusBadge({ status, type = 'status' }) {
  if (!status) return <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>—</span>;

  const s = String(status).toLowerCase();

  let bg = 'rgba(107, 114, 128, 0.2)';
  let text = '#9ca3af';
  let border = 'rgba(107, 114, 128, 0.4)';

  if (s.includes('pending') || s.includes('ui') || s.includes('under')) {
    bg = 'rgba(245, 158, 11, 0.15)';
    text = '#f59e0b';
    border = 'rgba(245, 158, 11, 0.35)';
  } else if (s.includes('dispose') || s.includes('convict') || s.includes('true') || s.includes('yes') || s.includes('arrested')) {
    bg = 'rgba(16, 185, 129, 0.15)';
    text = '#10b981';
    border = 'rgba(16, 185, 129, 0.35)';
  } else if (s.includes('chargesheet') || s.includes('pt') || s.includes('trial')) {
    bg = 'rgba(59, 130, 246, 0.15)';
    text = '#3b82f6';
    border = 'rgba(59, 130, 246, 0.35)';
  } else if (s.includes('abscond') || s.includes('died') || s.includes('false') || s.includes('no')) {
    bg = 'rgba(239, 68, 68, 0.15)';
    text = '#ef4444';
    border = 'rgba(239, 68, 68, 0.35)';
  }

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '3px 8px',
        borderRadius: '6px',
        fontSize: '11px',
        fontWeight: 600,
        backgroundColor: bg,
        color: text,
        border: `1px solid ${border}`,
        whiteSpace: 'nowrap',
      }}
    >
      {status}
    </span>
  );
}
