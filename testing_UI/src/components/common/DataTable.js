import React from 'react';
import { Loader2, Database } from 'lucide-react';

export default function DataTable({
  columns,
  data,
  isLoading,
  onRowClick,
  emptyMessage = 'No records found matching your filters',
}) {
  if (isLoading) {
    return (
      <div
        style={{
          padding: '60px 20px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '12px',
          background: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--border-color)',
        }}
      >
        <Loader2 size={32} color="var(--accent-v1)" className="animate-spin" />
        <span style={{ color: 'var(--text-secondary)', fontSize: '14px', fontWeight: 500 }}>
          Fetching records from PostgreSQL...
        </span>
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div
        style={{
          padding: '60px 20px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '12px',
          background: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--border-color)',
        }}
      >
        <Database size={36} color="var(--text-muted)" />
        <span style={{ color: 'var(--text-secondary)', fontSize: '14px', fontWeight: 500 }}>
          {emptyMessage}
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        overflowX: 'auto',
        background: 'var(--bg-secondary)',
        borderRadius: 'var(--radius-lg) var(--radius-lg) 0 0',
        border: '1px solid var(--border-color)',
        borderBottom: 'none',
      }}
    >
      <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13px' }}>
        <thead>
          <tr style={{ background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border-color)' }}>
            {columns.map((col, idx) => (
              <th
                key={idx}
                style={{
                  padding: '12px 16px',
                  fontWeight: 600,
                  color: 'var(--text-secondary)',
                  textTransform: 'uppercase',
                  fontSize: '11px',
                  letterSpacing: '0.05em',
                  width: col.width || 'auto',
                }}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, rowIdx) => (
            <tr
              key={rowIdx}
              onClick={() => onRowClick && onRowClick(row)}
              style={{
                borderBottom: '1px solid var(--border-subtle)',
                cursor: onRowClick ? 'pointer' : 'default',
                transition: 'background-color 0.15s ease',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--bg-card-hover)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent';
              }}
            >
              {columns.map((col, colIdx) => (
                <td key={colIdx} style={{ padding: '12px 16px', color: 'var(--text-primary)' }}>
                  {col.render ? col.render(row) : (row[col.accessor] ?? '—')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
