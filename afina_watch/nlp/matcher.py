from __future__ import annotations

import logging

from afina_watch.config import MatchingCfg, RuleCfg, WatchConfig
from afina_watch.models import MatchHit, MediaDigest, NormalizedItem
from afina_watch.nlp.embeddings import Embedder
from afina_watch.nlp.keywords import keyword_hits
from afina_watch.nlp.llm import LlmJudge

log = logging.getLogger(__name__)


def _source_allowed(item: NormalizedItem, rule: RuleCfg) -> bool:
    allowed = (rule.sources or {}).get(item.platform.value) or []
    if not allowed or "*" in allowed:
        return True
    return item.source_id in allowed or (item.source_title or "") in allowed


class Matcher:
    def __init__(self, cfg: WatchConfig):
        self.cfg = cfg
        self.embedder = Embedder(cfg.llm)
        self.judge = LlmJudge(cfg.llm)

    def match(self, item: NormalizedItem, digest: MediaDigest) -> list[MatchHit]:
        hits: list[MatchHit] = []
        blob = digest.search_blob or item.text
        mcfg: MatchingCfg = self.cfg.matching

        for rule in self.cfg.rules:
            if not rule.enabled or not _source_allowed(item, rule):
                continue
            kw = keyword_hits(blob, rule.keywords, mcfg.keyword_casefold)
            sem = None
            if rule.phrases and blob.strip():
                try:
                    sem = self.embedder.best_phrase_score(blob, rule.phrases)
                except Exception:
                    log.exception("embed failed rule=%s", rule.id)

            threshold = rule.semantic_threshold or mcfg.default_semantic_threshold
            cheap_hit = bool(kw) or (sem is not None and sem >= threshold)
            need_llm = rule.always_llm or (
                sem is not None and sem >= mcfg.run_llm_if_semantic_above and not cheap_hit
            ) or (cheap_hit and rule.phrases)

            llm_match = llm_score = llm_why = tags = None
            if need_llm and (blob.strip() or item.media):
                try:
                    verdict = self.judge.judge(item, digest, rule)
                    llm_match = verdict["match"]
                    llm_score = verdict["score"]
                    llm_why = verdict["why"]
                    tags = verdict["tags"]
                except Exception:
                    log.exception("llm judge failed rule=%s", rule.id)

            matched = cheap_hit or (llm_match is True)
            if rule.always_llm and llm_match is False:
                matched = False
            if not matched:
                continue
            hits.append(
                MatchHit(
                    rule_id=rule.id,
                    keyword_hits=kw,
                    semantic_score=sem,
                    llm_match=llm_match,
                    llm_score=llm_score,
                    llm_why=llm_why,
                    tags=tags or [],
                )
            )
        return hits
