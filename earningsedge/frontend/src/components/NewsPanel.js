import React from 'react';

function normalizeSentiment(s) {
  const v = String(s || 'neutral').toLowerCase();
  if (v.includes('bull')) return 'bullish';
  if (v.includes('bear')) return 'bearish';
  return 'neutral';
}

function hostFromUrl(u) {
  try {
    const { hostname } = new URL(u);
    return hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

export default function NewsPanel({ news, overall, overallRationale }) {
  const isEmpty = !news || news.length === 0;
  const overallNorm = normalizeSentiment(overall);

  return (
    <div className="card">
      <h3 className="card-title">News & Sentiment</h3>
      {isEmpty ? (
        <div className="skeleton skel-line" style={{ width: 110, height: 22, marginBottom: 10 }} />
      ) : (
        <div className="news-overall-block">
          <span className={`sent-badge sent-overall ${overallNorm}`}>
            Overall: {overallNorm}
          </span>
          {overallRationale ? (
            <p className="news-overall-rationale">{overallRationale}</p>
          ) : null}
        </div>
      )}
      <div className="news-list">
        {isEmpty
          ? [0, 1, 2, 3].map((i) => (
              <div key={i} className="news-item skeleton-row">
                <div className="skeleton skel-line" style={{ flex: 1, height: 14 }} />
                <div className="skeleton skel-line" style={{ width: 60, height: 18 }} />
              </div>
            ))
          : news.map((item, i) => {
              const cls = normalizeSentiment(item.sentiment);
              const src = (item.source || '').trim();
              const linkHost = hostFromUrl(item.url);
              const sourceLine = [src || linkHost, item.published_at].filter(Boolean).join(' · ');
              return (
                <div key={i} className="news-item">
                  <div className="news-item-body">
                    <a
                      className="headline"
                      href={item.url}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      {item.headline || '(no headline)'}
                    </a>
                    {sourceLine ? <div className="news-meta">{sourceLine}</div> : null}
                    {item.sentiment_reason ? (
                      <div className="news-rationale" title={item.sentiment_reason}>
                        {item.sentiment_reason}
                      </div>
                    ) : null}
                  </div>
                  <div className="news-item-aside">
                    <span className={`sent-badge ${cls}`}>{cls}</span>
                    {item.sentiment_confidence != null ? (
                      <span className="news-confidence">{Math.round(item.sentiment_confidence * 100)}%</span>
                    ) : null}
                  </div>
                </div>
              );
            })}
      </div>
    </div>
  );
}
