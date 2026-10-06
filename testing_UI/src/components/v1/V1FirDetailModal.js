import React, { useState, useEffect } from 'react';
import { X, FileText, Users, UserCheck, Scale, Code, Loader2, Download, ExternalLink, Eye, FileCheck } from 'lucide-react';
import api from '../../services/api';
import V1AccusedCard from './V1AccusedCard';
import V1AccusedDetailsTable from './V1AccusedDetailsTable';
import V1CourtTable from './V1CourtTable';
import StatusBadge from '../common/StatusBadge';

export default function V1FirDetailModal({ fir_reg_num, isOpen, onClose }) {
  const [activeTab, setActiveTab] = useState('overview');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!isOpen || !fir_reg_num) return;

    setLoading(true);
    setError(null);

    api.getV1FirDetail(fir_reg_num)
      .then((res) => {
        setData(res);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [isOpen, fir_reg_num]);

  if (!isOpen) return null;

  const hasDoc = Boolean(data?.fir?.dms_file_name && data?.fir?.attach_path);
  const pdfUrl = hasDoc
    ? api.getV1MediaPdfUrl(data.fir.attach_path, data.fir.dms_file_name, false)
    : null;
  const pdfDownloadUrl = hasDoc
    ? api.getV1MediaPdfUrl(data.fir.attach_path, data.fir.dms_file_name, true)
    : null;

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
          maxWidth: '1280px',
          height: '92vh',
          borderRadius: 'var(--radius-lg)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: 'var(--shadow-lg)',
          background: 'var(--bg-primary)',
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
            <div style={{ background: 'var(--accent-v1)', color: '#ffffff', padding: '8px', borderRadius: '8px' }}>
              <FileText size={20} />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <h2 style={{ fontSize: '18px', fontWeight: 800, color: 'var(--text-primary)' }}>
                  FIR #{data?.fir?.fir_no || fir_reg_num}
                </h2>
                <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '12px', background: 'var(--bg-card)', padding: '2px 8px', borderRadius: '6px', color: '#93c5fd' }}>
                  Reg Num: {fir_reg_num}
                </span>
                {data?.fir?.fir_status && <StatusBadge status={data.fir.fir_status} />}
              </div>
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                Unit / District: <strong>{data?.fir?.unit || '—'}</strong> • Police Station: <strong>{data?.fir?.ps_name || '—'}</strong> • Registered: {data?.fir?.reg_dt ? new Date(data.fir.reg_dt).toLocaleString() : '—'}
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

        {/* Modal Sub-Tabs */}
        <div style={{ display: 'flex', background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border-color)', padding: '0 24px', gap: '8px', overflowX: 'auto' }}>
          <button
            onClick={() => setActiveTab('overview')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 16px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'overview' ? 'var(--accent-v1)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'overview' ? '2px solid var(--accent-v1)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <FileText size={15} /> Overview & Facts
          </button>

          <button
            onClick={() => setActiveTab('document')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 16px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'document' ? 'var(--accent-v1)' : hasDoc ? '#2563eb' : 'var(--text-secondary)',
              borderBottom: activeTab === 'document' ? '2px solid var(--accent-v1)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <FileCheck size={15} /> Scanned FIR Document {hasDoc ? '(Available)' : '(None)'}
          </button>

          <button
            onClick={() => setActiveTab('accused')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 16px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'accused' ? 'var(--accent-v1)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'accused' ? '2px solid var(--accent-v1)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <Users size={15} /> Accused Dossier & 60 Relatives ({data?.accused?.length || 0})
          </button>

          <button
            onClick={() => setActiveTab('accused_details')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 16px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'accused_details' ? 'var(--accent-v1)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'accused_details' ? '2px solid var(--accent-v1)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <UserCheck size={15} /> Secondary Accused Table ({data?.accused_details?.length || 0})
          </button>

          <button
            onClick={() => setActiveTab('court')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 16px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'court' ? 'var(--accent-v1)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'court' ? '2px solid var(--accent-v1)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <Scale size={15} /> Court & Chargesheet ({data?.court?.length || 0})
          </button>

          <button
            onClick={() => setActiveTab('json')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '12px 16px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'transparent',
              color: activeTab === 'json' ? 'var(--accent-v1)' : 'var(--text-secondary)',
              borderBottom: activeTab === 'json' ? '2px solid var(--accent-v1)' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            <Code size={15} /> Raw JSON
          </button>
        </div>

        {/* Modal Body Content */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '24px', background: 'var(--bg-primary)' }}>
          {loading ? (
            <div style={{ padding: '60px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '12px' }}>
              <Loader2 size={32} color="var(--accent-v1)" className="animate-spin" />
              <span style={{ color: 'var(--text-secondary)' }}>Loading complete FIR dossier from cctns_v1...</span>
            </div>
          ) : error ? (
            <div style={{ padding: '24px', background: 'rgba(239, 68, 68, 0.1)', border: '1px solid var(--danger)', borderRadius: '8px', color: '#fca5a5' }}>
              <strong>Error loading FIR:</strong> {error}
            </div>
          ) : !data ? null : (
            <>
              {/* TAB 1: OVERVIEW */}
              {activeTab === 'overview' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                  {/* Summary Grid */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
                    <div style={{ background: 'var(--bg-secondary)', padding: '16px', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Section of Law</span>
                      <p style={{ fontSize: '15px', fontWeight: 700, color: '#2563eb', marginTop: '4px' }}>
                        {data.fir.section_of_law || '—'}
                      </p>
                    </div>

                    <div style={{ background: 'var(--bg-secondary)', padding: '16px', borderRadius: '10px', border: '1px solid var(--border-color)', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                      <div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Attached Scanned FIR PDF</span>
                          {hasDoc && (
                            <span style={{ fontSize: '11px', background: 'rgba(16, 185, 129, 0.15)', color: '#059669', padding: '2px 8px', borderRadius: '6px', fontWeight: 700 }}>
                              ✓ Stored on Server
                            </span>
                          )}
                        </div>
                        <p style={{ fontSize: '13px', fontWeight: 700, color: 'var(--accent-v1)', marginTop: '4px', wordBreak: 'break-word' }}>
                          {data.fir.dms_file_name || 'No DMS file attached'}
                        </p>
                        {data.fir.attach_path && (
                          <div style={{ marginTop: '6px', fontSize: '11px', color: 'var(--text-muted)', background: 'var(--bg-elevated)', padding: '6px 8px', borderRadius: '6px', wordBreak: 'break-all' }}>
                            <div><strong>Server:</strong> dopams-new (192.168.103.106)</div>
                            <div><strong>Disk Path:</strong> /home/tganb/dopams/media_cctnsv1/{data.fir.attach_path}/{data.fir.dms_file_name}</div>
                          </div>
                        )}
                      </div>
                      {hasDoc && (
                        <div style={{ marginTop: '12px', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                          <button
                            onClick={() => setActiveTab('document')}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '6px',
                              background: 'var(--accent-v1)',
                              color: '#ffffff',
                              padding: '6px 12px',
                              borderRadius: '6px',
                              fontSize: '12px',
                              fontWeight: 600,
                              cursor: 'pointer',
                              border: 'none',
                            }}
                          >
                            <Eye size={14} /> View Document Here
                          </button>
                          <a
                            href={pdfUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '6px',
                              background: 'var(--bg-card)',
                              color: 'var(--text-primary)',
                              padding: '6px 12px',
                              borderRadius: '6px',
                              fontSize: '12px',
                              fontWeight: 600,
                              textDecoration: 'none',
                              border: '1px solid var(--border-color)',
                            }}
                          >
                            <ExternalLink size={14} /> Pop Out
                          </a>
                          <a
                            href={pdfDownloadUrl}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '6px',
                              background: 'var(--bg-card)',
                              color: 'var(--text-primary)',
                              padding: '6px 12px',
                              borderRadius: '6px',
                              fontSize: '12px',
                              fontWeight: 600,
                              textDecoration: 'none',
                              border: '1px solid var(--border-color)',
                            }}
                          >
                            <Download size={14} /> Download
                          </a>
                        </div>
                      )}
                    </div>

                    <div style={{ background: 'var(--bg-secondary)', padding: '16px', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Linked Entities Summary</span>
                      <p style={{ fontSize: '13px', color: 'var(--text-primary)', marginTop: '4px' }}>
                        <strong>{data.accused?.length || 0}</strong> Accused Dossiers • <strong>{data.court?.length || 0}</strong> Court Records
                      </p>
                    </div>
                  </div>

                  {/* Brief Facts / FIR Contents */}
                  <div style={{ background: 'var(--bg-secondary)', padding: '20px', borderRadius: '12px', border: '1px solid var(--border-color)' }}>
                    <h3 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '12px' }}>
                      FIR Contents & Brief Facts
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
                      {data.fir.fir_contents || 'No brief facts content available.'}
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 2: DEDICATED SCANNED FIR DOCUMENT (PDF VIEWER) */}
              {activeTab === 'document' && (
                <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '12px' }}>
                  {hasDoc ? (
                    <>
                      {/* Document Toolbar */}
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          padding: '12px 16px',
                          background: 'var(--bg-secondary)',
                          border: '1px solid var(--border-color)',
                          borderRadius: '8px',
                          flexWrap: 'wrap',
                          gap: '10px',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                          <div style={{ background: 'rgba(37, 99, 235, 0.1)', color: '#2563eb', padding: '6px', borderRadius: '6px' }}>
                            <FileText size={18} />
                          </div>
                          <div>
                            <div style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)' }}>
                              {data.fir.dms_file_name}
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                              Path: {data.fir.attach_path} • Streaming live from Alfresco / dopams-new
                            </div>
                          </div>
                        </div>

                        <div style={{ display: 'flex', gap: '8px' }}>
                          <a
                            href={pdfUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '6px',
                              background: 'var(--bg-card)',
                              color: 'var(--text-primary)',
                              padding: '6px 14px',
                              borderRadius: '6px',
                              fontSize: '12px',
                              fontWeight: 600,
                              textDecoration: 'none',
                              border: '1px solid var(--border-color)',
                            }}
                          >
                            <ExternalLink size={14} /> Open in New Tab
                          </a>
                          <a
                            href={pdfDownloadUrl}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '6px',
                              background: 'var(--accent-v1)',
                              color: '#ffffff',
                              padding: '6px 14px',
                              borderRadius: '6px',
                              fontSize: '12px',
                              fontWeight: 600,
                              textDecoration: 'none',
                            }}
                          >
                            <Download size={14} /> Download PDF
                          </a>
                        </div>
                      </div>

                      {/* Embedded PDF Viewer Frame */}
                      <div
                        style={{
                          flex: 1,
                          minHeight: '620px',
                          height: 'calc(90vh - 240px)',
                          borderRadius: '8px',
                          overflow: 'hidden',
                          border: '1px solid var(--border-color)',
                          background: '#525659',
                          position: 'relative',
                        }}
                      >
                        <object
                          data={pdfUrl}
                          type="application/pdf"
                          width="100%"
                          height="100%"
                          style={{ minHeight: '620px', display: 'block', width: '100%', height: '100%' }}
                        >
                          <iframe
                            src={pdfUrl}
                            title="Scanned FIR PDF Document"
                            style={{
                              width: '100%',
                              height: '100%',
                              minHeight: '620px',
                              border: 'none',
                              display: 'block',
                            }}
                          >
                            <div style={{ padding: '30px', textAlign: 'center', color: '#ffffff', background: '#334155' }}>
                              <p>Unable to display PDF directly in browser.</p>
                              <a
                                href={pdfDownloadUrl}
                                style={{
                                  display: 'inline-block',
                                  marginTop: '10px',
                                  background: 'var(--accent-v1)',
                                  color: '#ffffff',
                                  padding: '8px 16px',
                                  borderRadius: '6px',
                                  textDecoration: 'none',
                                }}
                              >
                                Download Scanned PDF Document
                              </a>
                            </div>
                          </iframe>
                        </object>
                      </div>
                    </>
                  ) : (
                    <div
                      style={{
                        padding: '60px 24px',
                        textAlign: 'center',
                        background: 'var(--bg-secondary)',
                        borderRadius: '12px',
                        border: '1px solid var(--border-color)',
                      }}
                    >
                      <FileText size={48} color="var(--text-muted)" style={{ margin: '0 auto 12px' }} />
                      <h4 style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)' }}>
                        No Scanned Document Attached
                      </h4>
                      <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px', maxWidth: '500px', margin: '8px auto 0' }}>
                        This FIR record in CCTNS V1 does not have an attached DMS scanned PDF file (<code>attach_path</code> / <code>dms_file_name</code> is null).
                      </p>
                    </div>
                  )}
                </div>
              )}

              {/* TAB 3: ACCUSED DOSSIERS (WITH 60 RELATIVES) */}
              {activeTab === 'accused' && (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                    <h3 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)' }}>
                      Accused Dossiers (cctns_accused) — {data.accused?.length || 0} Persons
                    </h3>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                      Each dossier contains flat personal details + 60 interrogation relative fields
                    </span>
                  </div>

                  {data.accused && data.accused.length > 0 ? (
                    data.accused.map((acc, idx) => (
                      <V1AccusedCard key={acc.accused_id || idx} accused={acc} index={idx} />
                    ))
                  ) : (
                    <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
                      No accused dossiers recorded in cctns_accused.
                    </div>
                  )}
                </div>
              )}

              {/* TAB 4: SECONDARY ACCUSED TABLE */}
              {activeTab === 'accused_details' && (
                <div style={{ background: 'var(--bg-secondary)', borderRadius: '12px', border: '1px solid var(--border-color)', padding: '16px' }}>
                  <h3 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '12px' }}>
                    Secondary Accused Table (cctns_accused_details)
                  </h3>
                  <V1AccusedDetailsTable details={data.accused_details} />
                </div>
              )}

              {/* TAB 5: COURT & CHARGESHEET */}
              {activeTab === 'court' && (
                <div style={{ background: 'var(--bg-secondary)', borderRadius: '12px', border: '1px solid var(--border-color)', padding: '16px' }}>
                  <h3 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '12px' }}>
                    Court Disposals & Chargesheet Details (cctns_court)
                  </h3>
                  <V1CourtTable courtRecords={data.court} />
                </div>
              )}

              {/* TAB 6: RAW JSON */}
              {activeTab === 'json' && (
                <div style={{ background: 'var(--bg-elevated)', padding: '16px', borderRadius: '12px', border: '1px solid var(--border-color)' }}>
                  <pre style={{ fontSize: '12px', fontFamily: 'JetBrains Mono, monospace', color: 'var(--accent-v1)', whiteSpace: 'pre-wrap' }}>
                    {JSON.stringify(data, null, 2)}
                  </pre>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
