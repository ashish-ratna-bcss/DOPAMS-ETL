import React, { useState } from 'react';
import { GitCompare, Search, CheckCircle, XCircle, Layers, Database } from 'lucide-react';
import api from '../../services/api';


export default function CompareDashboard() {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleCompare = (e) => {
    e && e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError(null);

    api.getCompare(query.trim())
      .then((res) => {
        setResult(res);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  };

  const v1 = result?.v1;
  const v2 = result?.v2;

  return (
    <div style={{ maxWidth: '1600px', margin: '0 auto', padding: '24px' }}>
      
      {/* Header card */}
      <div style={{ background: 'var(--bg-secondary)', padding: '24px', borderRadius: 'var(--radius-lg)', border: '1px solid var(--border-color)', marginBottom: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
          <div style={{ background: 'var(--accent-compare)', color: '#ffffff', padding: '10px', borderRadius: '10px' }}>
            <GitCompare size={24} />
          </div>
          <div>
            <h2 style={{ fontSize: '20px', fontWeight: 800, color: 'var(--text-primary)' }}>
              CCTNS V1 ⟷ V2 Cross-Version Comparator
            </h2>
            <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              Verify schema transformations, entity linking, and data integrity side-by-side for any FIR or Crime record.
            </p>
          </div>
        </div>

        {/* Search input form */}
        <form onSubmit={handleCompare} style={{ display: 'flex', gap: '12px', maxWidth: '700px' }}>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Enter FIR Reg Num (e.g. 2024029220208) or FIR No (e.g. 208/2022)..."
            style={{ flex: 1, padding: '12px 16px', fontSize: '14px', background: 'var(--bg-card)' }}
          />
          <button
            type="submit"
            disabled={loading}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '12px 24px',
              borderRadius: 'var(--radius-md)',
              background: 'var(--accent-compare)',
              color: '#ffffff',
              fontSize: '14px',
              fontWeight: 700,
            }}
          >
            <Search size={16} />
            {loading ? 'Searching...' : 'Compare'}
          </button>
        </form>

        {/* Example quick searches */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '12px', fontSize: '12px', color: 'var(--text-muted)' }}>
          <span>Try quick FIR examples:</span>
          <button
            type="button"
            onClick={() => { setQuery('2024029220208'); }}
            style={{ background: 'transparent', color: '#60a5fa', textDecoration: 'underline', padding: 0 }}
          >
            2024029220208
          </button>
          <span>•</span>
          <button
            type="button"
            onClick={() => { setQuery('2022055221025'); }}
            style={{ background: 'transparent', color: '#60a5fa', textDecoration: 'underline', padding: 0 }}
          >
            2022055221025
          </button>
        </div>
      </div>

      {error && (
        <div style={{ padding: '16px', background: 'rgba(239, 68, 68, 0.1)', border: '1px solid var(--danger)', borderRadius: '8px', color: '#fca5a5', marginBottom: '20px' }}>
          {error}
        </div>
      )}

      {/* Comparison Grid Results */}
      {result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          
          {/* Match Overview Bar */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '16px' }}>
            <div style={{ background: 'var(--bg-secondary)', padding: '16px', borderRadius: '10px', border: '1px solid var(--border-color)', display: 'flex', alignItems: 'center', gap: '12px' }}>
              {result.foundInV1 ? <CheckCircle size={24} color="#10b981" /> : <XCircle size={24} color="#ef4444" />}
              <div>
                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Found in CCTNS V1</span>
                <p style={{ fontSize: '14px', fontWeight: 700, color: result.foundInV1 ? '#10b981' : '#ef4444' }}>
                  {result.foundInV1 ? 'Yes (cctns_fir match)' : 'Not Found'}
                </p>
              </div>
            </div>

            <div style={{ background: 'var(--bg-secondary)', padding: '16px', borderRadius: '10px', border: '1px solid var(--border-color)', display: 'flex', alignItems: 'center', gap: '12px' }}>
              {result.foundInV2 ? <CheckCircle size={24} color="#10b981" /> : <XCircle size={24} color="#ef4444" />}
              <div>
                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Found in CCTNS V2</span>
                <p style={{ fontSize: '14px', fontWeight: 700, color: result.foundInV2 ? '#10b981' : '#ef4444' }}>
                  {result.foundInV2 ? 'Yes (crimes match)' : 'Not Found'}
                </p>
              </div>
            </div>

            <div style={{ background: 'var(--bg-secondary)', padding: '16px', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Accused Count</span>
              <p style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginTop: '2px' }}>
                V1: {v1?.accused?.length || 0} vs V2: {v2?.accused?.length || 0}
              </p>
            </div>

            <div style={{ background: 'var(--bg-secondary)', padding: '16px', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Court / Chargesheet</span>
              <p style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginTop: '2px' }}>
                V1 Court: {v1?.court?.length || 0} vs V2 CS: {v2?.chargesheets?.length || 0}
              </p>
            </div>
          </div>

          {/* Dual Column Side-by-Side Comparison */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
            
            {/* LEFT COLUMN: CCTNS V1 */}
            <div style={{ background: 'var(--bg-secondary)', borderRadius: 'var(--radius-lg)', border: '1px solid rgba(59, 130, 246, 0.3)', padding: '20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '16px', color: '#60a5fa', fontWeight: 700, fontSize: '16px' }}>
                <Layers size={18} /> CCTNS V1 (Legacy Data)
              </div>

              {v1 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', fontSize: '13px' }}>
                  <div style={{ background: 'var(--bg-card)', padding: '12px', borderRadius: '8px' }}>
                    <span style={{ color: 'var(--text-muted)', display: 'block', fontSize: '11px' }}>FIR Number / Reg Num</span>
                    <strong>{v1.fir.fir_no}</strong> ({v1.fir.fir_reg_num})
                  </div>
                  <div style={{ background: 'var(--bg-card)', padding: '12px', borderRadius: '8px' }}>
                    <span style={{ color: 'var(--text-muted)', display: 'block', fontSize: '11px' }}>Unit & Police Station</span>
                    <strong>{v1.fir.unit}</strong> — {v1.fir.ps_name}
                  </div>
                  <div style={{ background: 'var(--bg-card)', padding: '12px', borderRadius: '8px' }}>
                    <span style={{ color: 'var(--text-muted)', display: 'block', fontSize: '11px' }}>Section of Law</span>
                    <strong>{v1.fir.section_of_law}</strong>
                  </div>
                  <div style={{ background: 'var(--bg-card)', padding: '12px', borderRadius: '8px' }}>
                    <span style={{ color: 'var(--text-muted)', display: 'block', fontSize: '11px' }}>Registration Date</span>
                    <span>{v1.fir.reg_dt ? new Date(v1.fir.reg_dt).toLocaleString() : '—'}</span>
                  </div>
                  <div style={{ background: 'var(--bg-card)', padding: '12px', borderRadius: '8px' }}>
                    <span style={{ color: 'var(--text-muted)', display: 'block', fontSize: '11px' }}>Accused Dossiers ({v1.accused?.length || 0})</span>
                    <ul style={{ paddingLeft: '20px', marginTop: '6px' }}>
                      {(v1.accused || []).map((a, i) => (
                        <li key={i}>{a.accused_name} ({a.gender}, {a.age} yrs) - Father: {a.father_name || 'N/A'}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : (
                <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
                  Record not present in CCTNS V1 database.
                </div>
              )}
            </div>

            {/* RIGHT COLUMN: CCTNS V2 */}
            <div style={{ background: 'var(--bg-secondary)', borderRadius: 'var(--radius-lg)', border: '1px solid rgba(139, 92, 246, 0.3)', padding: '20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '16px', color: '#a78bfa', fontWeight: 700, fontSize: '16px' }}>
                <Database size={18} /> CCTNS V2 (Current Normalized Data)
              </div>

              {v2 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', fontSize: '13px' }}>
                  <div style={{ background: 'var(--bg-card)', padding: '12px', borderRadius: '8px' }}>
                    <span style={{ color: 'var(--text-muted)', display: 'block', fontSize: '11px' }}>FIR Number / Crime ID</span>
                    <strong>{v2.crime.fir_num}</strong> ({v2.crime.crime_id})
                  </div>
                  <div style={{ background: 'var(--bg-card)', padding: '12px', borderRadius: '8px' }}>
                    <span style={{ color: 'var(--text-muted)', display: 'block', fontSize: '11px' }}>District & Police Station</span>
                    <strong>{v2.crime.district_name}</strong> — {v2.crime.ps_name} ({v2.crime.ps_code})
                  </div>
                  <div style={{ background: 'var(--bg-card)', padding: '12px', borderRadius: '8px' }}>
                    <span style={{ color: 'var(--text-muted)', display: 'block', fontSize: '11px' }}>Acts & Sections</span>
                    <strong>{v2.crime.acts_sections}</strong>
                  </div>
                  <div style={{ background: 'var(--bg-card)', padding: '12px', borderRadius: '8px' }}>
                    <span style={{ color: 'var(--text-muted)', display: 'block', fontSize: '11px' }}>FIR Date</span>
                    <span>{v2.crime.fir_date ? new Date(v2.crime.fir_date).toLocaleString() : '—'}</span>
                  </div>
                  <div style={{ background: 'var(--bg-card)', padding: '12px', borderRadius: '8px' }}>
                    <span style={{ color: 'var(--text-muted)', display: 'block', fontSize: '11px' }}>Joined Persons & Accused ({v2.accused?.length || 0})</span>
                    <ul style={{ paddingLeft: '20px', marginTop: '6px' }}>
                      {(v2.accused || []).map((a, i) => (
                        <li key={i}>{a.full_name} ({a.gender}, {a.age} yrs) - Domicile: {a.domicile_classification || 'N/A'}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : (
                <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
                  Record not present in CCTNS V2 database.
                </div>
              )}
            </div>

          </div>
        </div>
      )}

    </div>
  );
}
