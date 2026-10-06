import React, { useState, useEffect, useCallback } from 'react';
import { FileText, Users, Scale, ExternalLink } from 'lucide-react';
import api from '../../services/api';
import StatCard from '../common/StatCard';
import SearchFilterBar from '../common/SearchFilterBar';
import DataTable from '../common/DataTable';
import Pagination from '../common/Pagination';
import StatusBadge from '../common/StatusBadge';
import V1FirDetailModal from './V1FirDetailModal';

export default function V1Dashboard({ health }) {
  const [firs, setFirs] = useState([]);
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
  const [districtList, setDistrictList] = useState([]);

  // Selected FIR for Modal
  const [selectedFirRegNum, setSelectedFirRegNum] = useState(null);

  // Load districts
  useEffect(() => {
    api.getV1Districts()
      .then((data) => setDistrictList(Object.keys(data || {})))
      .catch(console.error);
  }, []);

  // Fetch FIRs
  const fetchFirs = useCallback(() => {
    setLoading(true);
    api.getV1Firs({ page, limit, search, district, year, status })
      .then((res) => {
        setFirs(res.data || []);
        setTotal(res.total || 0);
        setTotalPages(res.totalPages || 1);
        setLoading(false);
      })
      .catch((err) => {
        console.error('Failed to fetch V1 FIRs:', err);
        setLoading(false);
      });
  }, [page, limit, search, district, year, status]);

  useEffect(() => {
    fetchFirs();
  }, [fetchFirs]);

  const handleSearchSubmit = (val) => {
    setSearch(val);
    setPage(1);
    api.getV1Firs({ page: 1, limit, search: val, district, year, status })
      .then((res) => {
        setFirs(res.data || []);
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
      header: 'FIR Reg Num',
      accessor: 'fir_reg_num',
      render: (row) => (
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 600, color: '#60a5fa' }}>
          {row.fir_reg_num}
        </span>
      ),
    },
    {
      header: 'FIR No / Year',
      accessor: 'fir_no',
      render: (row) => (
        <div>
          <strong style={{ color: 'var(--text-primary)' }}>{row.fir_no || '—'}</strong>
          <span style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)' }}>
            Year: {row.reg_year || '—'}
          </span>
        </div>
      ),
    },
    {
      header: 'District / Unit',
      accessor: 'unit',
      render: (row) => (
        <div>
          <span style={{ fontWeight: 600 }}>{row.unit || '—'}</span>
          <span style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)' }}>
            PS: {row.ps_name || '—'}
          </span>
        </div>
      ),
    },
    {
      header: 'Section of Law',
      accessor: 'section_of_law',
      render: (row) => (
        <span style={{ fontSize: '12px', maxWidth: '180px', display: 'inline-block' }}>
          {row.section_of_law || '—'}
        </span>
      ),
    },
    {
      header: 'Reg Date',
      accessor: 'reg_dt',
      render: (row) => (
        <span style={{ fontSize: '12px' }}>
          {row.reg_dt ? new Date(row.reg_dt).toLocaleDateString() : '—'}
        </span>
      ),
    },
    {
      header: 'Case Status',
      accessor: 'fir_status',
      render: (row) => <StatusBadge status={row.fir_status || 'Pending'} />,
    },
    {
      header: 'Linked Records',
      render: (row) => (
        <div style={{ display: 'flex', gap: '6px' }}>
          <span style={{ fontSize: '11px', background: 'rgba(59, 130, 246, 0.15)', color: '#60a5fa', padding: '2px 6px', borderRadius: '4px' }}>
            {row.accused_count} Accused
          </span>
          <span style={{ fontSize: '11px', background: 'rgba(139, 92, 246, 0.15)', color: '#a78bfa', padding: '2px 6px', borderRadius: '4px' }}>
            {row.court_records_count} Court
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
            setSelectedFirRegNum(row.fir_reg_num);
          }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            color: 'var(--accent-v1)',
            padding: '6px 10px',
            borderRadius: '6px',
            fontSize: '12px',
            fontWeight: 600,
          }}
        >
          <ExternalLink size={13} /> View Dossier
        </button>
      ),
    },
  ];

  return (
    <div style={{ maxWidth: '1600px', margin: '0 auto', padding: '24px' }}>
      
      {/* KPI Stats Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '16px', marginBottom: '24px' }}>
        <StatCard
          title="Total CCTNS V1 FIRs"
          value={health?.v1?.counts?.firs || total}
          subtitle="Parent Table (cctns_fir)"
          icon={FileText}
          color="blue"
        />
        <StatCard
          title="Accused Dossiers"
          value={health?.v1?.counts?.accused}
          subtitle="Wide table + 60 relative fields"
          icon={Users}
          color="purple"
        />
        <StatCard
          title="Secondary Accused"
          value={health?.v1?.counts?.accused_details}
          subtitle="cctns_accused_details"
          icon={Users}
          color="cyan"
        />
        <StatCard
          title="Court Disposals"
          value={health?.v1?.counts?.court}
          subtitle="cctns_court records"
          icon={Scale}
          color="emerald"
        />
      </div>

      {/* Search & Filters */}
      <SearchFilterBar
        search={search}
        onSearchChange={handleSearchSubmit}
        district={district}
        onDistrictChange={(d) => { setDistrict(d); setPage(1); }}
        districtList={districtList}
        year={year}
        onYearChange={(y) => { setYear(y); setPage(1); }}
        status={status}
        onStatusChange={(s) => { setStatus(s); setPage(1); }}
        onReset={handleReset}
        placeholder="Search V1 FIRs by FIR No, PS, Law section, facts..."
      />

      {/* Main Data Table */}
      <div style={{ boxShadow: 'var(--shadow-md)', borderRadius: 'var(--radius-lg)' }}>
        <DataTable
          columns={columns}
          data={firs}
          isLoading={loading}
          onRowClick={(row) => setSelectedFirRegNum(row.fir_reg_num)}
          emptyMessage="No CCTNS V1 records found matching your filters"
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

      {/* Full Dossier Modal */}
      {selectedFirRegNum && (
        <V1FirDetailModal
          fir_reg_num={selectedFirRegNum}
          isOpen={!!selectedFirRegNum}
          onClose={() => setSelectedFirRegNum(null)}
        />
      )}

    </div>
  );
}
