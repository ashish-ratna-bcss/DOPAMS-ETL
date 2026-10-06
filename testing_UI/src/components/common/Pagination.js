import React from 'react';
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react';

export default function Pagination({ page, totalPages, total, limit, onPageChange, onLimitChange }) {
  if (total === 0) return null;

  const startRecord = (page - 1) * limit + 1;
  const endRecord = Math.min(page * limit, total);

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '12px',
        padding: '12px 16px',
        background: 'var(--bg-secondary)',
        borderTop: '1px solid var(--border-color)',
        borderRadius: '0 0 var(--radius-lg) var(--radius-lg)',
        fontSize: '13px',
      }}
    >
      {/* Records info & Page size selector */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px', color: 'var(--text-secondary)' }}>
        <span>
          Showing <strong style={{ color: 'var(--text-primary)' }}>{startRecord.toLocaleString()}</strong> to{' '}
          <strong style={{ color: 'var(--text-primary)' }}>{endRecord.toLocaleString()}</strong> of{' '}
          <strong style={{ color: 'var(--text-primary)' }}>{total.toLocaleString()}</strong> records
        </span>

        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span>Per page:</span>
          <select
            value={limit}
            onChange={(e) => onLimitChange(Number(e.target.value))}
            style={{ padding: '4px 8px', fontSize: '12px' }}
          >
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>

      {/* Page Navigation Buttons */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <button
          onClick={() => onPageChange(1)}
          disabled={page <= 1}
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            color: page <= 1 ? 'var(--text-muted)' : 'var(--text-primary)',
            padding: '6px 8px',
            borderRadius: '6px',
            cursor: page <= 1 ? 'not-allowed' : 'pointer',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <ChevronsLeft size={16} />
        </button>

        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            color: page <= 1 ? 'var(--text-muted)' : 'var(--text-primary)',
            padding: '6px 8px',
            borderRadius: '6px',
            cursor: page <= 1 ? 'not-allowed' : 'pointer',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <ChevronLeft size={16} />
        </button>

        <span style={{ padding: '4px 12px', color: 'var(--text-primary)', fontWeight: 600 }}>
          Page {page} of {totalPages}
        </span>

        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages}
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            color: page >= totalPages ? 'var(--text-muted)' : 'var(--text-primary)',
            padding: '6px 8px',
            borderRadius: '6px',
            cursor: page >= totalPages ? 'not-allowed' : 'pointer',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <ChevronRight size={16} />
        </button>

        <button
          onClick={() => onPageChange(totalPages)}
          disabled={page >= totalPages}
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            color: page >= totalPages ? 'var(--text-muted)' : 'var(--text-primary)',
            padding: '6px 8px',
            borderRadius: '6px',
            cursor: page >= totalPages ? 'not-allowed' : 'pointer',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <ChevronsRight size={16} />
        </button>
      </div>
    </div>
  );
}
