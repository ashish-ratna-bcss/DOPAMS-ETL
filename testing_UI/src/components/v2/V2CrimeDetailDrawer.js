import React, { useState, useEffect } from 'react';
import { X, Database, Users, ShieldAlert, Brain, Scale, Package, Image as ImageIcon, Code, Loader2 } from 'lucide-react';
import api from '../../services/api';
import V2PersonsAccused from './V2PersonsAccused';
import V2ArrestsTable from './V2ArrestsTable';
import V2InterrogationView from './V2InterrogationView';
import V2ChargesheetsView from './V2ChargesheetsView';
import V2SeizuresProperties from './V2SeizuresProperties';
import V2MediaGallery from './V2MediaGallery';
import MediaViewerModal from '../common/MediaViewerModal';
import StatusBadge from '../common/StatusBadge';

export default function V2CrimeDetailDrawer({ crime_id, isOpen, onClose }) {
  const [activeTab, setActiveTab] = useState('overview');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Active media for Lightbox / PDF Viewer
  const [selectedMedia, setSelectedMedia] = useState(null);

  useEffect(() => {
    if (!isOpen || !crime_id) return;

    setLoading(true);
    setError(null);

    api.getV2CrimeDetail(crime_id)
      .then((res) => {
        setData(res);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [isOpen, crime_id]);

  if (!isOpen) return null;

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        backdropFilter: 'blur(8px)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px',
      }}
      onClick={onClose}
    >
      <div
        className="glass-panel"
        style={{
          width: '100%',
          maxWidth: '1300px',
          height: '92vh',
          borderRadius: 'var(--radius-lg)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: 'var(--shadow-lg)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div
          style={{
            padding: '16px 24px',
            borderBottom: '1px solid var(--border-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'var(--bg-secondary)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{ background: 'var(--accent-v2)', color: '#ffffff', padding: '8px', borderRadius: '8px' }}>
              <Database size={20} />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <h2 style={{ fontSize: '18px', fontWeight: 800, color: 'var(--text-primary)' }}>
                  Crime #{data?.crime?.fir_num || data?.crime?.fir_reg_num || crime_id}
                </h2>
                <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '12px', background: 'var(--bg-card)', padding: '2px 8px', borderRadius: '6px', color: '#c4b5fd' }}>
                  ID: {crime_id}
                </span>
                {data?.crime?.case_status && <StatusBadge status={data.crime.case_status} />}
              </div>
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                PS: <strong>{data?.crime?.ps_name || data?.crime?.ps_code}</strong> • District: <strong>{data?.crime?.district_name || '—'}</strong> • Registered: {data?.crime?.fir_date ? new Date(data.crime.fir_date).toLocaleString() : '—'}
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            style={{
              background: 'var(--bg-card)',
              color: 'var(--text-secondary)',
              padding: '6px',
              borderRadius: '8px',
              display: 'flex',
              alignItems: 'center',
            }}
          >
            <X size={20} />
          </button>
        </div>

        {/* Navigation Sub-Tabs */}
        <div style={{ display: 'flex', background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border-color)', padding: '0 24px', gap: '4px', overflowX: 'auto' }}>
          <button
            onClick={() => setActiveTab('overview')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 14px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'overview' ? 'var(--accent-v2)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'overview' ? '2px solid var(--accent-v2)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <Database size={15} /> Overview & Facts
          </button>

          <button
            onClick={() => setActiveTab('accused')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 14px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'accused' ? 'var(--accent-v2)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'accused' ? '2px solid var(--accent-v2)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <Users size={15} /> Accused & Persons ({data?.accused?.length || 0})
          </button>

          <button
            onClick={() => setActiveTab('arrests')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 14px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'arrests' ? 'var(--accent-v2)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'arrests' ? '2px solid var(--accent-v2)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <ShieldAlert size={15} /> Arrests & 41A ({data?.arrests?.length || 0})
          </button>

          <button
            onClick={() => setActiveTab('ir')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 14px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'ir' ? 'var(--accent-v2)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'ir' ? '2px solid var(--accent-v2)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <Brain size={15} /> Interrogation ({data?.interrogation_reports?.length || 0})
          </button>

          <button
            onClick={() => setActiveTab('chargesheets')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 14px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'chargesheets' ? 'var(--accent-v2)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'chargesheets' ? '2px solid var(--accent-v2)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <Scale size={15} /> Chargesheets ({data?.chargesheets?.length || 0})
          </button>

          <button
            onClick={() => setActiveTab('seizures')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 14px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'seizures' ? 'var(--accent-v2)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'seizures' ? '2px solid var(--accent-v2)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <Package size={15} /> Seizures & Properties ({data?.mo_seizures?.length || 0})
          </button>

          <button
            onClick={() => setActiveTab('media')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 14px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'media' ? 'var(--accent-v2)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'media' ? '2px solid var(--accent-v2)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <ImageIcon size={15} /> Media & Files ({data?.media?.length || 0})
          </button>

          <button
            onClick={() => setActiveTab('json')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 14px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'json' ? 'var(--accent-v2)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'json' ? '2px solid var(--accent-v2)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <Code size={15} /> JSON Tree
          </button>
        </div>

        {/* Modal Body Content */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '24px', background: 'var(--bg-primary)' }}>
          {loading ? (
            <div style={{ padding: '60px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '12px' }}>
              <Loader2 size={32} color="var(--accent-v2)" className="animate-spin" />
              <span style={{ color: 'var(--text-secondary)' }}>Loading complete CCTNS V2 composite tree...</span>
            </div>
          ) : error ? (
            <div style={{ padding: '24px', background: 'rgba(239, 68, 68, 0.1)', border: '1px solid var(--danger)', borderRadius: '8px', color: '#fca5a5' }}>
              <strong>Error loading Crime details:</strong> {error}
            </div>
          ) : !data ? null : (
            <>
              {/* TAB 1: OVERVIEW */}
              {activeTab === 'overview' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                  
                  {/* Summary Grid */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '14px' }}>
                    <div style={{ background: 'var(--bg-secondary)', padding: '14px', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Acts & Sections</span>
                      <p style={{ fontSize: '14px', fontWeight: 700, color: '#a78bfa', marginTop: '4px' }}>
                        {data.crime.acts_sections || '—'}
                      </p>
                    </div>

                    <div style={{ background: 'var(--bg-secondary)', padding: '14px', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Major / Minor Head</span>
                      <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', marginTop: '4px' }}>
                        {data.crime.major_head || '—'} / {data.crime.minor_head || '—'}
                      </p>
                    </div>

                    <div style={{ background: 'var(--bg-secondary)', padding: '14px', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Investigating Officer (IO)</span>
                      <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', marginTop: '4px' }}>
                        {data.crime.io_name || '—'} ({data.crime.io_rank || 'IO'})
                      </p>
                    </div>

                    <div style={{ background: 'var(--bg-secondary)', padding: '14px', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Hierarchy Mapping</span>
                      <p style={{ fontSize: '12px', color: 'var(--text-primary)', marginTop: '4px' }}>
                        Circle: <strong>{data.crime.circle_name || '—'}</strong> • SDPO: <strong>{data.crime.sdpo_name || '—'}</strong>
                      </p>
                    </div>
                  </div>

                  {/* Brief Facts */}
                  <div style={{ background: 'var(--bg-secondary)', padding: '20px', borderRadius: '12px', border: '1px solid var(--border-color)' }}>
                    <h3 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '12px' }}>
                      Brief Facts & FIR Narrative
                    </h3>
                    <div
                      style={{
                        background: 'var(--bg-elevated)',
                        padding: '16px',
                        borderRadius: '8px',
                        fontSize: '13px',
                        lineHeight: 1.7,
                        color: 'var(--text-primary)',
                        whiteSpace: 'pre-wrap',
                        maxHeight: '400px',
                        overflowY: 'auto',
                        border: '1px solid var(--border-subtle)',
                      }}
                    >
                      {data.crime.brief_facts || 'No brief facts narrative available.'}
                    </div>
                  </div>

                </div>
              )}

              {/* TAB 2: ACCUSED & PERSONS */}
              {activeTab === 'accused' && (
                <V2PersonsAccused accusedList={data.accused} onOpenMedia={setSelectedMedia} />
              )}

              {/* TAB 3: ARRESTS */}
              {activeTab === 'arrests' && (
                <V2ArrestsTable arrests={data.arrests} />
              )}

              {/* TAB 4: INTERROGATION REPORTS */}
              {activeTab === 'ir' && (
                <V2InterrogationView irList={data.interrogation_reports} />
              )}

              {/* TAB 5: CHARGESHEETS */}
              {activeTab === 'chargesheets' && (
                <V2ChargesheetsView chargesheets={data.chargesheets} updates={data.charge_sheet_updates} onOpenMedia={setSelectedMedia} />
              )}

              {/* TAB 6: SEIZURES & PROPERTIES */}
              {activeTab === 'seizures' && (
                <V2SeizuresProperties seizures={data.mo_seizures} fslList={data.fsl_case_property} properties={data.properties} onOpenMedia={setSelectedMedia} />
              )}

              {/* TAB 7: MEDIA GALLERY */}
              {activeTab === 'media' && (
                <V2MediaGallery mediaList={data.media} onOpenMedia={setSelectedMedia} />
              )}

              {/* TAB 8: RAW JSON */}
              {activeTab === 'json' && (
                <div style={{ background: 'var(--bg-elevated)', padding: '16px', borderRadius: '12px', border: '1px solid var(--border-color)' }}>
                  <pre style={{ fontSize: '12px', fontFamily: 'JetBrains Mono, monospace', color: 'var(--accent-v2)', whiteSpace: 'pre-wrap' }}>
                    {JSON.stringify(data, null, 2)}
                  </pre>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Media Lightbox / PDF Modal */}
      {selectedMedia && (
        <MediaViewerModal
          media={selectedMedia}
          isOpen={!!selectedMedia}
          onClose={() => setSelectedMedia(null)}
        />
      )}
    </div>
  );
}
