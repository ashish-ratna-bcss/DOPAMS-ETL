import React from 'react';
import { Search, RotateCcw } from 'lucide-react';


export default function SearchFilterBar({
  search,
  onSearchChange,
  district,
  onDistrictChange,
  districtList = [],
  status,
  onStatusChange,
  year,
  onYearChange,
  onReset,
  placeholder = 'Search by FIR No, Police Station, Acts, Accused or keywords...',
}) {
  return (
    <div
      style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border-color)',
        borderRadius: 'var(--radius-lg)',
        padding: '16px',
        display: 'flex',
        flexWrap: 'wrap',
        gap: '12px',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: '20px',
      }}
    >
      {/* Search Input */}
      <div style={{ flex: '1 1 320px', position: 'relative' }}>
        <Search
          size={18}
          color="var(--text-muted)"
          style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)' }}
        />
        <input
          type="text"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={placeholder}
          style={{
            width: '100%',
            paddingLeft: '38px',
            background: 'var(--bg-card)',
            fontSize: '13px',
          }}
        />
      </div>

      {/* Filter Selects */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center' }}>
        {/* District / Unit Filter */}
        {districtList.length > 0 && (
          <select
            value={district}
            onChange={(e) => onDistrictChange(e.target.value)}
            style={{ fontSize: '13px', minWidth: '150px' }}
          >
            <option value="">All Districts / Units</option>
            {districtList.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        )}

        {/* Year Filter */}
        <select
          value={year}
          onChange={(e) => onYearChange(e.target.value)}
          style={{ fontSize: '13px' }}
        >
          <option value="">All Years</option>
          <option value="2026">2026</option>
          <option value="2025">2025</option>
          <option value="2024">2024</option>
          <option value="2023">2023</option>
          <option value="2022">2022</option>
          <option value="2021">2021</option>
          <option value="2020">2020</option>
        </select>

        {/* Status Filter */}
        <select
          value={status}
          onChange={(e) => onStatusChange(e.target.value)}
          style={{ fontSize: '13px' }}
        >
          <option value="">All Statuses</option>
          <option value="Pending Trial">Pending Trial</option>
          <option value="Under Trial">Under Trial</option>
          <option value="UI">Under Investigation (UI)</option>
          <option value="PT">Pending Trial (PT)</option>
          <option value="Disposed">Disposed</option>
          <option value="Chargesheeted">Chargesheeted</option>
        </select>

        {/* Reset Button */}
        <button
          onClick={onReset}
          title="Reset Filters"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '8px 12px',
            borderRadius: 'var(--radius-md)',
            background: 'var(--bg-card)',
            color: 'var(--text-secondary)',
            border: '1px solid var(--border-color)',
            fontSize: '13px',
            fontWeight: 500,
          }}
        >
          <RotateCcw size={14} />
          Reset
        </button>
      </div>
    </div>
  );
}
