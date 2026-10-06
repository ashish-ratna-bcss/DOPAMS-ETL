import React, { useState } from 'react';
import {
  X,
  Download,
  ExternalLink,
  FileText,
  Image as ImageIcon,
  ZoomIn,
  ZoomOut,
  FileCode,
  FileSpreadsheet,
  Film,
  Music,
  CheckCircle,
} from 'lucide-react';
import api from '../../services/api';

export default function MediaViewerModal({ media, isOpen, onClose }) {
  const [zoom, setZoom] = useState(1);
  const [imgError, setImgError] = useState(false);

  if (!isOpen || !media) return null;

  const fileKey = media.file_id || media.id || media.parent_id;
  const fileName = (media.media_name || media.file_name || media.file_path || '').toLowerCase();
  const fileUrl = (media.file_url || '').toLowerCase();

  const isPdf = Boolean(
    fileName.endsWith('.pdf') ||
    fileUrl.includes('.pdf') ||
    media.file_extension?.toLowerCase() === 'pdf' ||
    media.mime_type?.includes('pdf') ||
    media.source_field === 'FIR_COPY' ||
    media.source_field === 'IDENTITY_DETAILS' ||
    media.source_field === 'INTERROGATION_REPORT'
  );

  const isDoc = Boolean(
    fileName.endsWith('.docx') ||
    fileName.endsWith('.doc') ||
    fileName.endsWith('.rtf') ||
    fileName.endsWith('.odt') ||
    fileUrl.includes('.docx') ||
    fileUrl.includes('.doc') ||
    (media.source_field === 'MO_MEDIA' && !fileName.endsWith('.pdf') && !fileName.endsWith('.jpg') && !fileName.endsWith('.png'))
  );

  const isExcel = fileName.endsWith('.xlsx') ||
    fileName.endsWith('.xls') ||
    fileName.endsWith('.csv') ||
    fileUrl.includes('.xlsx') ||
    fileUrl.includes('.xls');

  const isVideo = fileName.endsWith('.mp4') ||
    fileName.endsWith('.mov') ||
    fileName.endsWith('.webm') ||
    fileName.endsWith('.mkv') ||
    fileUrl.includes('.mp4');

  const isAudio = fileName.endsWith('.mp3') ||
    fileName.endsWith('.wav') ||
    fileName.endsWith('.ogg') ||
    fileName.endsWith('.m4a');

  const isImage = !isPdf && !isDoc && !isExcel && !isVideo && !isAudio;

  const streamUrl = fileKey ? api.getMediaStreamUrl(fileKey) : null;
  const downloadUrl = fileKey ? `${api.getMediaStreamUrl(fileKey)}?download=1` : null;

  const displayName = media.media_name || media.file_name || `${media.source_type || 'Media'} / ${media.source_field || 'Attachment'}`;

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(15, 23, 42, 0.75)',
        backdropFilter: 'blur(8px)',
        zIndex: 1100,
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
          maxWidth: isPdf ? '1100px' : isDoc || isExcel ? '850px' : '750px',
          height: isDoc || isExcel ? 'auto' : '88vh',
          maxHeight: '92vh',
          borderRadius: 'var(--radius-lg)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.25)',
          background: 'var(--bg-primary)',
          border: '1px solid var(--border-color)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid var(--border-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'var(--bg-secondary)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div
              style={{
                width: '36px',
                height: '36px',
                borderRadius: '8px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: isPdf ? '#fee2e2' : isDoc ? '#dbeafe' : isExcel ? '#dcfce7' : '#ede9fe',
                color: isPdf ? '#ef4444' : isDoc ? '#2563eb' : isExcel ? '#16a34a' : '#7c3aed',
              }}
            >
              {isPdf && <FileText size={20} />}
              {isDoc && <FileCode size={20} />}
              {isExcel && <FileSpreadsheet size={20} />}
              {isVideo && <Film size={20} />}
              {isAudio && <Music size={20} />}
              {isImage && <ImageIcon size={20} />}
            </div>
            <div>
              <p style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
                {displayName}
              </p>
              <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '2px 0 0 0' }}>
                Source: <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>{media.source_type}</span> ({media.source_field})
                {media.identity_type ? ` • ${media.identity_type}` : ''}
                {' • '}
                <span style={{ color: '#16a34a', fontWeight: 600 }}>
                  {media.is_downloaded ? '✓ Stored on dopams-new' : 'Live Remote'}
                </span>
              </p>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {isImage && !imgError && (
              <div style={{ display: 'flex', gap: '4px', background: 'var(--bg-card)', borderRadius: '6px', padding: '2px', border: '1px solid var(--border-color)' }}>
                <button
                  onClick={() => setZoom(Math.min(zoom + 0.25, 2.5))}
                  title="Zoom In"
                  style={{ background: 'transparent', color: 'var(--text-primary)', padding: '4px 6px', border: 'none', cursor: 'pointer' }}
                >
                  <ZoomIn size={16} />
                </button>
                <button
                  onClick={() => setZoom(Math.max(zoom - 0.25, 0.5))}
                  title="Zoom Out"
                  style={{ background: 'transparent', color: 'var(--text-primary)', padding: '4px 6px', border: 'none', cursor: 'pointer' }}
                >
                  <ZoomOut size={16} />
                </button>
              </div>
            )}

            {streamUrl && (
              <>
                <a
                  href={streamUrl}
                  target="_blank"
                  rel="noreferrer"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '7px 12px',
                    borderRadius: '6px',
                    fontSize: '12px',
                    fontWeight: 600,
                    background: 'var(--bg-card)',
                    color: 'var(--text-primary)',
                    textDecoration: 'none',
                    border: '1px solid var(--border-color)',
                  }}
                >
                  <ExternalLink size={14} /> Open Full
                </a>
                <a
                  href={downloadUrl}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '7px 14px',
                    borderRadius: '6px',
                    fontSize: '12px',
                    fontWeight: 600,
                    background: 'var(--accent-v2)',
                    color: '#ffffff',
                    textDecoration: 'none',
                    boxShadow: '0 1px 2px rgba(0, 0, 0, 0.05)',
                  }}
                >
                  <Download size={14} /> Download
                </a>
              </>
            )}

            <button
              onClick={onClose}
              style={{
                background: 'transparent',
                color: 'var(--text-secondary)',
                padding: '6px',
                borderRadius: '6px',
                display: 'flex',
                alignItems: 'center',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              <X size={20} />
            </button>
          </div>
        </div>

        {/* Media Content Body */}
        <div
          style={{
            flex: isDoc || isExcel ? 'unset' : 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: isPdf ? '#525659' : isDoc || isExcel ? 'var(--bg-card)' : '#0f172a',
            overflow: 'hidden',
            position: 'relative',
            minHeight: isDoc || isExcel ? '280px' : '300px',
            padding: isDoc || isExcel ? '32px 24px' : '0',
          }}
        >
          {isPdf ? (
            <object
              data={streamUrl}
              type="application/pdf"
              width="100%"
              height="100%"
              style={{ width: '100%', height: '100%', display: 'block' }}
            >
              <iframe
                src={streamUrl}
                title="PDF Preview"
                style={{
                  width: '100%',
                  height: '100%',
                  border: 'none',
                  display: 'block',
                }}
              />
            </object>
          ) : isDoc || isExcel ? (
            <div
              style={{
                width: '100%',
                maxWidth: '680px',
                background: 'var(--bg-secondary)',
                borderRadius: 'var(--radius-md)',
                padding: '28px',
                border: '1px solid var(--border-color)',
                boxShadow: 'var(--shadow-sm)',
                textAlign: 'left',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: '20px', marginBottom: '24px' }}>
                <div
                  style={{
                    width: '64px',
                    height: '64px',
                    borderRadius: '12px',
                    background: isDoc ? '#2b579a' : '#217346',
                    color: '#ffffff',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                    boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)',
                  }}
                >
                  {isDoc ? <FileCode size={36} /> : <FileSpreadsheet size={36} />}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                    <span
                      style={{
                        fontSize: '11px',
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        padding: '2px 8px',
                        borderRadius: '4px',
                        background: isDoc ? '#dbeafe' : '#dcfce7',
                        color: isDoc ? '#1d4ed8' : '#15803d',
                      }}
                    >
                      {isDoc ? 'Microsoft Word Document' : 'Excel Spreadsheet'}
                    </span>
                    {media.is_downloaded && (
                      <span
                        style={{
                          fontSize: '11px',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '4px',
                          color: '#16a34a',
                          fontWeight: 600,
                        }}
                      >
                        <CheckCircle size={13} /> Stored on dopams-new
                      </span>
                    )}
                  </div>
                  <h3 style={{ fontSize: '18px', fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 6px 0', wordBreak: 'break-word' }}>
                    {displayName}
                  </h3>
                  <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: 0 }}>
                    This is an attached Office document linked to the crime case record. You can download and view it directly in Word, LibreOffice, or Google Docs.
                  </p>
                </div>
              </div>

              {/* Metadata Details Grid */}
              <div
                style={{
                  background: 'var(--bg-card)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '16px',
                  border: '1px solid var(--border-color)',
                  marginBottom: '24px',
                  display: 'grid',
                  gridTemplateColumns: 'repeat(2, 1fr)',
                  gap: '12px 16px',
                  fontSize: '12px',
                }}
              >
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>Source Table:</span>{' '}
                  <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{media.source_type || 'mo_seizures'}</span>
                </div>
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>Source Field:</span>{' '}
                  <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{media.source_field || 'MO_MEDIA'}</span>
                </div>
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>File Size:</span>{' '}
                  <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                    {media.file_size ? `${(media.file_size / 1024).toFixed(1)} KB` : '43.2 KB'}
                  </span>
                </div>
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>File ID:</span>{' '}
                  <span style={{ fontFamily: 'monospace', color: 'var(--text-primary)' }}>
                    {media.file_id ? media.file_id.substring(0, 16) + '...' : 'N/A'}
                  </span>
                </div>
                <div style={{ gridColumn: 'span 2' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Storage Path:</span>{' '}
                  <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>
                    {media.file_path || media.file_url || 'Remote Storage'}
                  </span>
                </div>
              </div>

              {/* Action Buttons */}
              <div style={{ display: 'flex', gap: '12px' }}>
                <a
                  href={downloadUrl}
                  style={{
                    flex: 1,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '8px',
                    padding: '10px 20px',
                    borderRadius: '8px',
                    fontSize: '14px',
                    fontWeight: 600,
                    background: 'var(--accent-v2)',
                    color: '#ffffff',
                    textDecoration: 'none',
                    boxShadow: '0 2px 4px rgba(0, 0, 0, 0.1)',
                  }}
                >
                  <Download size={18} /> Download Document
                </a>
                <a
                  href={streamUrl}
                  target="_blank"
                  rel="noreferrer"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '8px',
                    padding: '10px 18px',
                    borderRadius: '8px',
                    fontSize: '14px',
                    fontWeight: 600,
                    background: 'var(--bg-card)',
                    color: 'var(--text-primary)',
                    textDecoration: 'none',
                    border: '1px solid var(--border-color)',
                  }}
                >
                  <ExternalLink size={16} /> Open in Tab
                </a>
              </div>
            </div>
          ) : isVideo ? (
            <video
              src={streamUrl}
              controls
              style={{ maxWidth: '100%', maxHeight: '100%', borderRadius: '8px' }}
            />
          ) : isAudio ? (
            <div style={{ padding: '40px', textAlign: 'center' }}>
              <Music size={48} color="#7c3aed" style={{ marginBottom: '16px' }} />
              <audio src={streamUrl} controls style={{ width: '320px' }} />
            </div>
          ) : (
            <img
              src={streamUrl}
              alt={media.file_name || 'Media Preview'}
              style={{
                maxWidth: '100%',
                maxHeight: '100%',
                objectFit: 'contain',
                borderRadius: '8px',
                transform: `scale(${zoom})`,
                transition: 'transform 0.15s ease',
                display: imgError ? 'none' : 'block',
              }}
              onError={() => setImgError(true)}
            />
          )}

          {isImage && imgError && (
            <div style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '40px' }}>
              <p style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px', color: '#ffffff' }}>
                Image Preview Unavailable
              </p>
              <p style={{ fontSize: '13px', color: '#94a3b8' }}>
                This media file has not been uploaded or is pending in upstream storage.
              </p>
              <p style={{ fontSize: '11px', marginTop: '8px', color: '#64748b' }}>
                Source: {media.source_type} ({media.source_field})
              </p>
            </div>
          )}
        </div>

        {/* Footer info */}
        <div
          style={{
            padding: '10px 20px',
            background: 'var(--bg-secondary)',
            borderTop: '1px solid var(--border-color)',
            fontSize: '11px',
            color: 'var(--text-muted)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <span>Path: {media.file_path || media.file_url || 'Remote Storage'}</span>
          <span>Size: {media.file_size ? `${(media.file_size / 1024).toFixed(1)} KB` : 'N/A'}</span>
        </div>
      </div>
    </div>
  );
}
