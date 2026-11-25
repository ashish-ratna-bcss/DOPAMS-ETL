import React, { useState, useEffect, useCallback } from 'react';
import { Database, Users, Package, Brain, Image as ImageIcon, ExternalLink } from 'lucide-react';
import api from '../../services/api';
import StatCard from '../common/StatCard';
import SearchFilterBar from '../common/SearchFilterBar';
import DataTable from '../common/DataTable';
import Pagination from '../common/Pagination';
import StatusBadge from '../common/StatusBadge';
import V2CrimeDetailDrawer from './V2CrimeDetailDrawer';

export default function V2Dashboard({ health }) {
  const [crimes, setCrimes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(25);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);

  // Filters
  const [search, setSearch] = useState('');
  const [district, setDistrict] = useState('');
  const [year, setYear] = useState('');
  const [status, setStatus] = useState('');
  const [hierarchyList, setHierarchyList] = useState([]);

  // Selected Crime for Drawer
  const [selectedCrimeId, setSelectedCrimeId] = useState(null);

  // Load hierarchy
  useEffect(() => {
    api.getV2Hierarchy()
      .then((data) => {
        const uniqueDists = [...new Set((data || []).map(h => h.district_name).filter(Boolean))];
        setHierarchyList(uniqueDists);
      })
      .catch(console.error);
  }, []);

  // Fetch Crimes
  const fetchCrimes = useCallback(() => {
    setLoading(true);
    api.getV2Crimes({ page, limit, search, status, year })
      .then((res) => {
        setCrimes(res.data || []);
        setTotal(res.total || 0);
        setTotalPages(res.totalPages || 1);
        setLoading(false);
      })
      .catch((err) => {
        console.error('Failed to fetch V2 crimes:', err);
        setLoading(false);
      });
  }, [page, limit, search, status, year]);

  useEffect(() => {
    fetchCrimes();
  }, [fetchCrimes]);

  const handleSearchSubmit = (val) => {
    setSearch(val);
    setPage(1);
    api.getV2Crimes({ page: 1, limit, search: val, status, year })
      .then((res) => {
        setCrimes(res.data || []);
        setTotal(res.total || 0);
        setTotalPages(res.totalPages || 1);
      });
  };

  const handleReset = () => {
    setSearch('');
    setDistrict('');
    setYear('');
    setStatus('');
    setPage(1);
  };

  // Columns definition for DataTable
  const columns = [
    {
      header: 'Crime ID / Reg Num',
      accessor: 'crime_id',
      render: (row) => (
        <div>
          <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 600, color: '#a78bfa' }}>
            {row.fir_reg_num || row.crime_id.substring(0, 14) + '...'}
          </span>
          <span style={{ display: 'block', fontSize: '11px', fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-muted)' }}>
            FIR: {row.fir_num || '—'}
          </span>
        </div>
      ),
    },
    {
      header: 'Police Station / District',
      accessor: 'ps_name',
      render: (row) => (
        <div>
          <strong style={{ color: 'var(--text-primary)' }}>{row.ps_name || row.ps_code}</strong>
          <span style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)' }}>
            Dist: {row.district_name || '—'} {row.circle_name ? `• ${row.circle_name}` : ''}
          </span>
        </div>
      ),
    },
    {
      header: 'Acts & Sections',
      accessor: 'acts_sections',
      render: (row) => (
        <span style={{ fontSize: '12px', maxWidth: '200px', display: 'inline-block' }}>
          {row.acts_sections || '—'}
        </span>
      ),
    },
    {
      header: 'FIR Date',
      accessor: 'fir_date',
      render: (row) => (
        <span style={{ fontSize: '12px' }}>
          {row.fir_date ? new Date(row.fir_date).toLocaleDateString() : '—'}
        </span>
      ),
    },
    {
      header: 'Case Status',
      accessor: 'case_status',
      render: (row) => <StatusBadge status={row.case_status || 'UI'} />,
    },
    {
      header: 'Linked Entities',
      render: (row) => (
        <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '11px', background: 'rgba(139, 92, 246, 0.15)', color: '#a78bfa', padding: '2px 6px', borderRadius: '4px' }}>
            {row.accused_count} Accused
          </span>
          <span style={{ fontSize: '11px', background: 'rgba(245, 158, 11, 0.15)', color: '#fbbf24', padding: '2px 6px', borderRadius: '4px' }}>
            {row.seizures_count} Seizures
          </span>
          <span style={{ fontSize: '11px', background: 'rgba(16, 185, 129, 0.15)', color: '#34d399', padding: '2px 6px', borderRadius: '4px' }}>
            {row.chargesheets_count} CS
          </span>
          <span style={{ fontSize: '11px', background: 'rgba(6, 182, 212, 0.15)', color: '#22d3ee', padding: '2px 6px', borderRadius: '4px' }}>
            {row.media_count} Files
          </span>
        </div>
      ),
    },
    {
      header: 'Actions',
      render: (row) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            setSelectedCrimeId(row.crime_id);
          }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            color: 'var(--accent-v2)',
            padding: '6px 10px',
            borderRadius: '6px',
            fontSize: '12px',
            fontWeight: 600,
          }}
        >
          <ExternalLink size={13} /> Inspect All
        </button>
      ),
    },
  ];

  return (
    <div style={{ maxWidth: '1600px', margin: '0 auto', padding: '24px' }}>
      
      {/* KPI Stats Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px', marginBottom: '24px' }}>
        <StatCard
          title="Total Crimes (V2)"
          value={health?.v2?.counts?.crimes || total}
          subtitle="Parent Table (crimes)"
          icon={Database}
          color="purple"
        />
        <StatCard
          title="Persons & Accused"
          value={health?.v2?.counts?.persons}
          subtitle="persons & accused tables"
          icon={Users}
          color="blue"
        />
        <StatCard
          title="Interrogation Reports"
          value={health?.v2?.counts?.interrogation_reports}
          subtitle="interrogation_reports table"
          icon={Brain}
          color="emerald"
        />
        <StatCard
          title="MO Seizures"
          value={health?.v2?.counts?.mo_seizures}
          subtitle="mo_seizures table"
          icon={Package}
          color="amber"
        />
        <StatCard
          title="Media & Files"
          value={health?.v2?.counts?.media_files}
          subtitle="file_media_bookkeeping"
          icon={ImageIcon}
          color="cyan"
        />
      </div>

      {/* Search & Filters */}
      <SearchFilterBar
        search={search}
        onSearchChange={handleSearchSubmit}
        district={district}
        onDistrictChange={(d) => { setDistrict(d); setPage(1); }}
        districtList={hierarchyList}
        year={year}
        onYearChange={(y) => { setYear(y); setPage(1); }}
        status={status}
        onStatusChange={(s) => { setStatus(s); setPage(1); }}
        onReset={handleReset}
        placeholder="Search V2 Crimes by FIR No, Crime ID, PS, Acts, facts..."
      />

      {/* Main Data Table */}
      <div style={{ boxShadow: 'var(--shadow-md)', borderRadius: 'var(--radius-lg)' }}>
        <DataTable
          columns={columns}
          data={crimes}
          isLoading={loading}
          onRowClick={(row) => setSelectedCrimeId(row.crime_id)}
          emptyMessage="No CCTNS V2 records found matching your filters"
        />

        <Pagination
          page={page}
          totalPages={totalPages}
          total={total}
          limit={limit}
          onPageChange={setPage}
          onLimitChange={(l) => { setLimit(l); setPage(1); }}
        />
      </div>

      {/* Full Crime Multi-Entity Drawer */}
      {selectedCrimeId && (
        <V2CrimeDetailDrawer
          crime_id={selectedCrimeId}
          isOpen={!!selectedCrimeId}
          onClose={() => setSelectedCrimeId(null)}
        />
      )}

    </div>
  );
}
