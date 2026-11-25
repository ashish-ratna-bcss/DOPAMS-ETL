import React from 'react';
import { FileText, Image as ImageIcon, ExternalLink, CheckCircle, AlertCircle } from 'lucide-react';

export default function V2MediaGallery({ mediaList, onOpenMedia }) {
  if (!mediaList || mediaList.length === 0) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        No media files or documents attached in file_media_bookkeeping for this crime.
      </div>
    );
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '16px' }}>
      {mediaList.map((media, idx) => {
        const isPdf = media.file_extension?.toLowerCase() === 'pdf' || media.mime_type?.includes('pdf') || media.file_name?.endsWith('.pdf');


        return (
          <div
            key={media.id || idx}
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-color)',
              borderRadius: 'var(--radius-lg)',
              padding: '16px',
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'space-between',
              boxShadow: 'var(--shadow-sm)',
            }}
          >
            {/* Top icon and title */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
                <div style={{
                  background: isPdf ? 'rgba(59, 130, 246, 0.15)' : 'rgba(139, 92, 246, 0.15)',
                  color: isPdf ? '#60a5fa' : '#a78bfa',
                  padding: '10px',
                  borderRadius: '10px',
                  display: 'flex',
                  alignItems: 'center',
                }}>
                  {isPdf ? <FileText size={22} /> : <ImageIcon size={22} />}
                </div>

                <span style={{
                  fontSize: '11px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  color: media.is_downloaded ? '#34d399' : '#fbbf24',
                  background: media.is_downloaded ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)',
                  padding: '2px 8px',
                  borderRadius: '6px',
                }}>
                  {media.is_downloaded ? <CheckCircle size={12} /> : <AlertCircle size={12} />}
                  {media.is_downloaded ? 'On Disk' : 'Remote / Pending'}
                </span>
              </div>

              <h4 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', wordBreak: 'break-word' }}>
                {media.file_name || `${media.source_type} / ${media.source_field}`}
              </h4>

              <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                Entity: <strong>{media.source_type}</strong> • Field: <strong>{media.source_field}</strong>
              </p>

              {media.file_id && (
                <p style={{ fontSize: '10px', fontFamily: 'JetBrains Mono, monospace', color: '#93c5fd', marginTop: '2px' }}>
                  File ID: {media.file_id}
                </p>
              )}
            </div>

            {/* Bottom Actions & Size */}
            <div style={{ marginTop: '16px', borderTop: '1px solid var(--border-subtle)', paddingTop: '12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                {media.file_size ? `${(media.file_size / 1024).toFixed(1)} KB` : (media.file_extension ? `.${media.file_extension}` : 'File')}
              </span>

              <div style={{ display: 'flex', gap: '6px' }}>
                <button
                  onClick={() => onOpenMedia && onOpenMedia(media)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    padding: '5px 10px',
                    borderRadius: '6px',
                    fontSize: '11px',
                    fontWeight: 600,
                    background: 'var(--accent-v2)',
                    color: '#ffffff',
                  }}
                >
                  <ExternalLink size={12} /> Preview
                </button>
              </div>
            </div>

          </div>
        );
      })}
    </div>
  );
}
