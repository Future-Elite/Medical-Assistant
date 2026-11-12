import { useState } from "react";
import { useWorkbench } from "../state/workbench";
import { Badge, Empty } from "./ui";
import { Icon } from "./Icon";
import { kindLabels, rankDelta, score } from "../lib/format";
import type { Dict } from "../types";

export function EvidenceDetail({ index }: { index: number | null }) {
  const { pkg, openCitation, citationMessage, saveLiveGuideline, notify } = useWorkbench();
  const [copied, setCopied] = useState(false);

  if (!pkg || index === null || !pkg.ranked_documents?.[index]) {
    return (
      <aside className="detail-panel">
        <div className="detail-head">
          <h3>引文详情</h3>
          <Badge>未选择</Badge>
        </div>
        <div className="detail-body">
          <Empty title="未选择证据" />
        </div>
      </aside>
    );
  }

  const ranked = pkg.ranked_documents[index];
  const doc = ranked.document;
  const citation =
    (pkg.citations || []).find((row: Dict) => row.citation_id === ranked.citation_id) ||
    (pkg.citations || [])[index];

  const copyId = async () => {
    if (!citation) return;
    try {
      await navigator.clipboard.writeText(citation.citation_id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      notify(citation.citation_id, "info");
    }
  };

  const canSaveLive =
    doc.source_id === "guideline-fulltext-live" &&
    doc.metadata?.evidence_type === "clinical_practice_guideline" &&
    ["full_text", "abstract"].includes(doc.metadata?.content_mode);

  return (
    <aside className="detail-panel">
      <div className="detail-head">
        <h3>引文详情</h3>
        <Badge tone="ready">{kindLabels[doc.kind] || doc.kind}</Badge>
      </div>
      <div className="detail-body">
        <h4 className="detail-title">{doc.title}</h4>
        {citation ? (
          <>
            <div className="detail-quote">“{citation.quote}”</div>
            {(citation.key_evidence_sentences || []).length ? (
              <div>
                <div className="section-title">关键证据句</div>
                {(citation.key_evidence_sentences || []).map((sentence: Dict, i: number) => (
                  <div className="key-sentence" key={i}>
                    <div>{sentence.text}</div>
                    <div className="key-sentence-meta">
                      {sentence.locator} · 匹配度 {Number(sentence.score).toFixed(3)}
                      {sentence.matched_terms?.length
                        ? ` · ${sentence.matched_terms.join("、")}`
                        : ""}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="hint">无匹配关键句</div>
            )}
            <ul className="detail-list">
              <li>
                <span>分数构成</span>
                <span>
                  总分 {score(ranked.score)} · base {score(ranked.base_score)} · 词面归一{" "}
                  {score(ranked.lexical_norm_score)} · RRF归一 {score(ranked.fusion_norm_score, 4)} ·
                  词面原分 {score(ranked.lexical_score)} · 语义 {score(ranked.semantic_score)} ·
                  RRF原分 {score(ranked.fusion_score, 4)} · 权威 {score(ranked.authority_score)} ·
                  新鲜 {score(ranked.freshness_score)}
                </span>
              </li>
              <li>
                <span>名次变化</span>
                <span>
                  重排前 {ranked.fusion_rank ? `#${ranked.fusion_rank}` : "—"} → 最终 #{index + 1} ·{" "}
                  {rankDelta(ranked.fusion_rank, index + 1)}
                  {ranked.reranker_score !== null && ranked.reranker_score !== undefined
                    ? ` · 重排分 ${score(ranked.reranker_score)}`
                    : ""}
                </span>
              </li>
              <li>
                <span>检索器 / 重排器</span>
                <span>
                  {ranked.retriever_id || "—"} / {ranked.reranker_id || "—"}
                </span>
              </li>
              <li>
                <span>引用 ID</span>
                <span>
                  <code className="citation-id">{citation.citation_id}</code>{" "}
                  <button type="button" className="btn btn-sm" onClick={copyId}>
                    <Icon name="copy" size={12} />
                    {copied ? "已复制" : "复制"}
                  </button>
                </span>
              </li>
              <li>
                <span>证据状态</span>
                <span>{ranked.evidence_status}</span>
              </li>
              <li>
                <span>来源</span>
                <span>{doc.source_id}</span>
              </li>
              <li>
                <span>指南版本</span>
                <span>{doc.version_id || "不适用"}</span>
              </li>
              <li>
                <span>推荐编号</span>
                <span>{doc.recommendation_id || "未提供"}</span>
              </li>
              <li>
                <span>适用人群</span>
                <span>{doc.population || "未提供"}</span>
              </li>
              <li>
                <span>原文定位</span>
                <span>{citation.locator || "未提供"}</span>
              </li>
              <li>
                <span>标识符</span>
                <span>{doc.pmid ? `PMID ${doc.pmid}` : doc.doi || doc.document_id}</span>
              </li>
              <li>
                <span>排序依据</span>
                <span>{(ranked.rank_explanation || []).join(" · ")}</span>
              </li>
            </ul>
          </>
        ) : (
          <Empty title="无引文记录" />
        )}

        {canSaveLive ? (
          <button
            type="button"
            className="btn btn-block"
            style={{ marginTop: 12 }}
            onClick={() => saveLiveGuideline(doc)}
          >
            <Icon name="file" size={14} />
            保存到本地并解析
          </button>
        ) : null}
        {citation ? (
          <button
            type="button"
            className="btn btn-block"
            style={{ marginTop: 8 }}
            onClick={() => openCitation(citation.citation_id)}
          >
            <Icon name="external" size={14} />
            打开本次检索的引文
          </button>
        ) : null}
        {citationMessage ? <div className="hint" style={{ marginTop: 7 }}>{citationMessage}</div> : null}
      </div>
    </aside>
  );
}
