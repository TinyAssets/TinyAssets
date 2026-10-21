"""Tier 1 deterministic structural evaluation -- no LLM required.

Ten checks informed by SCORE framework (arxiv.org/abs/2503.23512) and
TAACO coherence indices.  Catches ~60% of issues without API calls.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# spaCy -- gracefully degrade if model is unavailable
# ---------------------------------------------------------------------------
# Loaded on first use, never at import. This module is reached at import time
# by tinyassets.evaluation, so by outcomes, handoffs and the MCP server
# itself: importing spaCy here put ~2.8 s and its numpy/thinc stack on every
# server start and every process that imports the server (measured
# 2026-10-01), for a model the server never calls.
_UNLOADED = object()
_NLP_CACHE: Any = _UNLOADED


def _nlp() -> Any:
    """The spaCy pipeline, or None when spaCy or its model is unavailable."""
    global _NLP_CACHE
    if _NLP_CACHE is _UNLOADED:
        try:
            import spacy

            try:
                _NLP_CACHE = spacy.load("en_core_web_sm")
            except OSError:
                _NLP_CACHE = None
        except ImportError:
            _NLP_CACHE = None
    return _NLP_CACHE


# ---------------------------------------------------------------------------
# ASP engine -- optional until Phase 4
# ---------------------------------------------------------------------------
try:
    from tinyassets.constraints.asp_engine import ASPEngine  # noqa: F401

    _HAS_ASP = True
except ImportError:
    _HAS_ASP = False


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """Result of a single structural check."""

    name: str
    passed: bool
    score: float  # 0.0-1.0
    details: dict[str, Any] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)
    observation: str = ""
    """Reader-style observation (e.g. 'I noticed X -- is this intentional?').
    Non-empty for checks that found something noteworthy, even if passed."""


@dataclass
class StructuralResult:
    """Aggregated result across all structural checks."""

    checks: list[CheckResult]
    aggregate_score: float
    hard_failure: bool
    violations: list[str] = field(default_factory=list)

    def to_editorial_concerns(self) -> list:
        """Convert structural findings to EditorialConcern objects.

        Hard failures become clearly_wrong=True concerns.
        Observations from non-hard checks become clearly_wrong=False concerns.
        Returns a list of EditorialConcern instances compatible with
        EditorialNotes.
        """
        from tinyassets.evaluation.editorial import EditorialConcern

        concerns: list[EditorialConcern] = []
        for check in self.checks:
            is_hard = check.name in _HARD_FAILURE_CHECKS

            if is_hard and not check.passed:
                # Hard failure: provable error
                for v in check.violations:
                    concerns.append(EditorialConcern(
                        text=v,
                        quoted_passage="",
                        clearly_wrong=True,
                    ))
            elif check.observation:
                # Soft observation: might be intentional
                concerns.append(EditorialConcern(
                    text=check.observation,
                    quoted_passage="",
                    clearly_wrong=False,
                ))
        return concerns


# ---------------------------------------------------------------------------
# Weights for aggregate score
# ---------------------------------------------------------------------------
_CHECK_WEIGHTS: dict[str, float] = {
    "taaco_coherence": 0.11,
    "readability": 0.07,
    "pacing": 0.11,
    "chekhov_tracking": 0.11,
    "timeline_consistency": 0.14,
    "character_voice": 0.07,
    "canon_breach": 0.09,
    "asp_constraint": 0.07,
    "premise_grounding": 0.14,
}

# Checks whose failure sets hard_failure=True (provable errors only).
# canon_breach demoted to observation -- lexical contradiction detection has
# high false-positive rate and the editorial reader handles nuance better.
# name_consistency is now a placeholder -- editorial reader handles name issues.
_HARD_FAILURE_CHECKS = {
    "timeline_consistency", "asp_constraint", "premise_grounding",
}


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _count_syllables(word: str) -> int:
    """Estimate syllable count using a vowel-group heuristic."""
    word = word.lower().strip()
    if not word:
        return 0
    # Remove trailing silent-e
    if word.endswith("e") and len(word) > 2:
        word = word[:-1]
    vowel_groups = re.findall(r"[aeiouy]+", word)
    count = len(vowel_groups)
    return max(count, 1)


def _extract_sentences(text: str) -> list[str]:
    """Split text into sentences using spaCy or regex fallback."""
    nlp = _nlp()
    if nlp is not None:
        doc = nlp(text)
        return [sent.text.strip() for sent in doc.sents if sent.text.strip()]
    # Regex fallback
    raw = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in raw if s.strip()]


def _content_words(text: str) -> set[str]:
    """Return lowercased content words (no stopwords / punctuation)."""
    nlp = _nlp()
    if nlp is not None:
        doc = nlp(text)
        return {
            t.lemma_.lower()
            for t in doc
            if not t.is_stop and not t.is_punct and t.is_alpha
        }
    # Fallback: naive split minus very common words
    _STOP = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "is", "was", "are", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "it", "its", "this", "that",
        "he", "she", "they", "we", "you", "i", "my", "your", "his", "her",
        "their", "our", "not", "no", "so", "if", "then", "than", "as",
    }
    words = re.findall(r"[a-zA-Z]+", text.lower())
    return {w for w in words if w not in _STOP}


def _extract_dialogue(text: str) -> list[str]:
    """Extract dialogue segments enclosed in quotes."""
    # Handles "..." and \u201c...\u201d (smart quotes)
    patterns = [
        r'"([^"]*)"',
        r"\u201c([^\u201d]*)\u201d",
    ]
    segments: list[str] = []
    for pattern in patterns:
        segments.extend(re.findall(pattern, text))
    return segments


def _extract_narration(text: str) -> str:
    """Return text with dialogue removed."""
    text = re.sub(r'"[^"]*"', "", text)
    text = re.sub(r"\u201c[^\u201d]*\u201d", "", text)
    return text


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_taaco_coherence(prose: str) -> CheckResult:
    """TAACO-inspired coherence: sentence & paragraph overlap indices."""
    sentences = _extract_sentences(prose)
    if len(sentences) < 2:
        return CheckResult(
            name="taaco_coherence",
            passed=True,
            score=1.0,
            details={"sentence_count": len(sentences), "note": "too few sentences to measure"},
        )

    # Sentence-level overlap: adjacent sentence content word overlap
    overlaps = []
    for i in range(len(sentences) - 1):
        w1 = _content_words(sentences[i])
        w2 = _content_words(sentences[i + 1])
        union = w1 | w2
        if union:
            overlaps.append(len(w1 & w2) / len(union))
        else:
            overlaps.append(0.0)

    avg_sentence_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0

    # Paragraph-level overlap: split by double newline
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", prose) if p.strip()]
    para_overlaps = []
    for i in range(len(paragraphs) - 1):
        w1 = _content_words(paragraphs[i])
        w2 = _content_words(paragraphs[i + 1])
        union = w1 | w2
        if union:
            para_overlaps.append(len(w1 & w2) / len(union))
        else:
            para_overlaps.append(0.0)
    avg_para_overlap = sum(para_overlaps) / len(para_overlaps) if para_overlaps else 0.0

    # Score: fantasy prose tolerates higher overlap (proper nouns, world terms)
    # Ideal range: 0.05-0.40 for sentences, 0.10-0.50 for paragraphs
    # Below 0.03 sentence overlap = incoherent; above 0.50 = repetitive
    score = avg_sentence_overlap
    # Clamp to 0-1 using a bell curve centered on 0.15
    if score < 0.03:
        normalized = score / 0.03 * 0.3  # very low overlap penalized
    elif score > 0.50:
        normalized = max(0.3, 1.0 - (score - 0.50) / 0.50)  # too repetitive
    else:
        normalized = 0.5 + 0.5 * min(score / 0.20, 1.0)

    passed = 0.03 <= avg_sentence_overlap <= 0.60

    observation = ""
    if not passed:
        if avg_sentence_overlap < 0.03:
            observation = (
                "I noticed the sentences feel disconnected from each other "
                "-- there's very little shared vocabulary between consecutive "
                "sentences. Is this a deliberate fragmented style?"
            )
        else:
            observation = (
                "I noticed a lot of repeated language between sentences "
                "-- the prose feels somewhat repetitive. Is this intentional "
                "for emphasis?"
            )

    return CheckResult(
        name="taaco_coherence",
        passed=passed,
        score=round(min(max(normalized, 0.0), 1.0), 3),
        details={
            "avg_sentence_overlap": round(avg_sentence_overlap, 4),
            "avg_paragraph_overlap": round(avg_para_overlap, 4),
            "sentence_count": len(sentences),
            "paragraph_count": len(paragraphs),
        },
        violations=(
            [f"Sentence overlap {avg_sentence_overlap:.3f} outside acceptable range [0.03, 0.60]"]
            if not passed
            else []
        ),
        observation=observation,
    )


def _check_readability(prose: str) -> CheckResult:
    """Flesch-Kincaid grade level adapted for fantasy genre."""
    words = re.findall(r"[a-zA-Z]+", prose)
    sentences = _extract_sentences(prose)
    if not words or not sentences:
        return CheckResult(
            name="readability",
            passed=True,
            score=1.0,
            details={"note": "insufficient text"},
        )

    total_syllables = sum(_count_syllables(w) for w in words)
    word_count = len(words)
    sentence_count = len(sentences)

    # Flesch-Kincaid Grade Level
    fk_grade = (
        0.39 * (word_count / sentence_count)
        + 11.8 * (total_syllables / word_count)
        - 15.59
    )

    # Fantasy prose acceptable range: grade 6-18
    # Ideal: 9-14 (accessible but not simplistic)
    observation = ""
    if fk_grade < 4:
        score = 0.3
        passed = False
        violations = [f"Readability grade {fk_grade:.1f} too low for fantasy prose (min ~6)"]
        observation = (
            "I noticed the prose reads very simply -- short words, short "
            "sentences. Is this a deliberate stylistic choice for this scene?"
        )
    elif fk_grade > 20:
        score = 0.4
        passed = False
        violations = [f"Readability grade {fk_grade:.1f} too high (max ~18)"]
        observation = (
            "I noticed the prose is very dense -- long sentences with complex "
            "vocabulary. Consider whether the reader needs a breath here."
        )
    elif 8 <= fk_grade <= 15:
        score = 1.0
        passed = True
        violations = []
    elif fk_grade < 8:
        score = 0.6 + 0.4 * ((fk_grade - 4) / 4)
        passed = True
        violations = []
    else:  # 15 < fk_grade <= 20
        score = 0.6 + 0.4 * ((20 - fk_grade) / 5)
        passed = True
        violations = []

    return CheckResult(
        name="readability",
        passed=passed,
        score=round(min(max(score, 0.0), 1.0), 3),
        details={
            "flesch_kincaid_grade": round(fk_grade, 2),
            "word_count": word_count,
            "sentence_count": sentence_count,
            "avg_syllables_per_word": round(total_syllables / word_count, 2),
        },
        violations=violations,
        observation=observation,
    )


def _check_pacing(prose: str, chapter_avg_words: int | None = None) -> CheckResult:
    """Pacing: word count ratio, dialogue-to-narration, paragraph variance."""
    words = re.findall(r"\S+", prose)
    word_count = len(words)
    violations: list[str] = []

    # Word count vs chapter average
    ratio = None
    if chapter_avg_words and chapter_avg_words > 0:
        ratio = word_count / chapter_avg_words
        if ratio > 2.5:
            violations.append(
                f"Scene is {ratio:.1f}x chapter average "
                f"({word_count} vs {chapter_avg_words} words)"
            )
        elif ratio < 0.25:
            violations.append(
                f"Scene is only {ratio:.1f}x chapter average ({word_count} vs {chapter_avg_words})"
            )

    # Dialogue-to-narration ratio
    dialogue_segments = _extract_dialogue(prose)
    dialogue_words = sum(len(seg.split()) for seg in dialogue_segments)
    narration_words = word_count - dialogue_words
    d2n_ratio = dialogue_words / max(narration_words, 1)

    if d2n_ratio > 5.0:
        violations.append(
            f"Dialogue-to-narration ratio {d2n_ratio:.1f} is very high (mostly dialogue)"
        )
    elif d2n_ratio < 0.02 and word_count > 200:
        violations.append("Very little dialogue in a substantial scene")

    # Paragraph length variance (low variance = monotonous)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", prose) if p.strip()]
    para_lengths = [len(p.split()) for p in paragraphs]
    if len(para_lengths) >= 3:
        mean_len = sum(para_lengths) / len(para_lengths)
        variance = sum((pl - mean_len) ** 2 for pl in para_lengths) / len(para_lengths)
        cv = math.sqrt(variance) / max(mean_len, 1)  # coefficient of variation
    else:
        cv = 0.5  # default neutral

    # Score composition
    scores = []
    if ratio is not None:
        if 0.4 <= ratio <= 2.0:
            scores.append(1.0)
        elif ratio < 0.25 or ratio > 2.5:
            scores.append(0.3)
        else:
            scores.append(0.6)
    if 0.1 <= d2n_ratio <= 3.0:
        scores.append(1.0)
    elif d2n_ratio > 5.0 or d2n_ratio < 0.02:
        scores.append(0.4)
    else:
        scores.append(0.7)
    # CV score: ideal 0.3-0.8
    if 0.2 <= cv <= 1.0:
        scores.append(1.0)
    else:
        scores.append(0.5)

    avg_score = sum(scores) / len(scores) if scores else 0.5
    passed = len(violations) == 0

    # Build observation from violations
    observation = ""
    if violations:
        obs_parts = []
        for v in violations:
            if "dialogue-to-narration" in v.lower():
                obs_parts.append(
                    "the dialogue-to-narration balance feels off"
                )
            elif "chapter average" in v.lower() and ratio is not None:
                if ratio > 2.0:
                    obs_parts.append("this scene runs quite long compared to others")
                else:
                    obs_parts.append("this scene feels notably short")
            elif "very little dialogue" in v.lower():
                obs_parts.append(
                    "there's almost no dialogue -- is this scene meant "
                    "to be purely internal?"
                )
        if obs_parts:
            observation = "I noticed " + "; ".join(obs_parts) + " -- is this intentional?"

    return CheckResult(
        name="pacing",
        passed=passed,
        score=round(avg_score, 3),
        details={
            "word_count": word_count,
            "chapter_avg_words": chapter_avg_words,
            "word_count_ratio": round(ratio, 2) if ratio else None,
            "dialogue_to_narration_ratio": round(d2n_ratio, 2),
            "paragraph_count": len(paragraphs),
            "paragraph_length_cv": round(cv, 3),
        },
        violations=violations,
        observation=observation,
    )


def _check_chekhov(state: dict[str, Any]) -> CheckResult:
    """Chekhov's Gun: flag promises active too long without resolution."""
    # Pull promises from orient_result or extracted_promises
    orient = state.get("orient_result", {}) or {}
    active_promises = orient.get("active_promises", [])
    current_scene = state.get("scene_number", 1)
    violations: list[str] = []

    overdue_count = 0
    for promise in active_promises:
        introduced_scene = promise.get("introduced_scene", current_scene)
        scenes_active = current_scene - introduced_scene
        if scenes_active >= 5:
            overdue_count += 1
            violations.append(
                f"Promise '{promise.get('description', 'unknown')}' active for "
                f"{scenes_active} scenes (threshold: 5)"
            )

    total = len(active_promises)
    if total == 0:
        score = 1.0
    else:
        score = 1.0 - (overdue_count / total)

    observation = ""
    if overdue_count > 0:
        observation = (
            f"I noticed {overdue_count} narrative promise(s) have been "
            f"active for 5+ scenes without resolution. Are these being "
            f"deliberately delayed, or were they forgotten?"
        )

    return CheckResult(
        name="chekhov_tracking",
        passed=overdue_count == 0,
        score=round(max(score, 0.0), 3),
        details={
            "active_promises": total,
            "overdue_count": overdue_count,
            "current_scene": current_scene,
        },
        violations=violations,
        observation=observation,
    )


def _check_timeline(state: dict[str, Any]) -> CheckResult:
    """Timeline consistency: character location conflicts, knowledge boundaries."""
    orient = state.get("orient_result", {}) or {}
    warnings = orient.get("warnings", [])

    location_conflicts: list[str] = []
    knowledge_violations: list[str] = []

    for w in warnings:
        wtype = w.get("type", "")
        if wtype == "location_conflict":
            location_conflicts.append(
                f"Character '{w.get('character', '?')}' in two locations: "
                f"{w.get('location_a', '?')} and {w.get('location_b', '?')}"
            )
        elif wtype == "knowledge_boundary":
            knowledge_violations.append(
                f"Character '{w.get('character', '?')}' knows "
                f"'{w.get('fact', '?')}' but shouldn't (access tier violation)"
            )

    all_violations = location_conflicts + knowledge_violations
    # Location conflicts are hard failures
    hard = len(location_conflicts) > 0

    if not all_violations:
        score = 1.0
    else:
        score = max(0.0, 1.0 - 0.3 * len(all_violations))

    return CheckResult(
        name="timeline_consistency",
        passed=len(all_violations) == 0,
        score=round(score, 3),
        details={
            "location_conflicts": len(location_conflicts),
            "knowledge_violations": len(knowledge_violations),
            "is_hard_failure": hard,
        },
        violations=all_violations,
    )


def _check_character_voice(prose: str, state: dict[str, Any]) -> CheckResult:
    """Character voice: POS distribution similarity to voice profiles."""
    nlp = _nlp()
    if nlp is None:
        return CheckResult(
            name="character_voice",
            passed=True,
            score=0.7,
            details={"note": "spaCy not available, check skipped"},
        )

    # Extract voice profiles from retrieved_context
    ctx = state.get("retrieved_context", {}) or {}
    voice_profiles: dict[str, dict[str, float]] = ctx.get("voice_profiles", {})

    if not voice_profiles:
        return CheckResult(
            name="character_voice",
            passed=True,
            score=0.8,
            details={"note": "no voice profiles available for comparison"},
        )

    # Attribute dialogue to speakers (simple heuristic: quote followed by attribution)
    # Pattern: "dialogue" said/asked/replied CharacterName
    attribution_pattern = re.compile(
        r'["\u201c]([^"\u201d]+)["\u201d]\s+'
        r"(?:said|asked|replied|whispered|shouted|murmured|called|answered|exclaimed)"
        r"\s+(\w+)",
        re.IGNORECASE,
    )
    attributions: dict[str, list[str]] = {}
    for match in attribution_pattern.finditer(prose):
        dialogue_text, speaker = match.group(1), match.group(2)
        attributions.setdefault(speaker, []).append(dialogue_text)

    if not attributions:
        return CheckResult(
            name="character_voice",
            passed=True,
            score=0.8,
            details={"note": "no attributed dialogue found"},
        )

    # Compute POS distribution for each speaker's dialogue
    violations: list[str] = []
    similarities: list[float] = []

    for speaker, dialogues in attributions.items():
        combined = " ".join(dialogues)
        doc = nlp(combined)
        total_tokens = sum(1 for t in doc if t.is_alpha)
        if total_tokens < 5:
            continue

        pos_dist = {
            "ADV": sum(1 for t in doc if t.pos_ == "ADV") / total_tokens,
            "ADJ": sum(1 for t in doc if t.pos_ == "ADJ") / total_tokens,
            "PRON": sum(1 for t in doc if t.pos_ == "PRON") / total_tokens,
            "VERB": sum(1 for t in doc if t.pos_ == "VERB") / total_tokens,
            "NOUN": sum(1 for t in doc if t.pos_ == "NOUN") / total_tokens,
        }

        # Compare against profile
        profile = voice_profiles.get(speaker)
        if profile is None:
            continue

        # Cosine similarity on the POS distribution vectors
        keys = set(pos_dist.keys()) | set(profile.keys())
        dot = sum(pos_dist.get(k, 0) * profile.get(k, 0) for k in keys)
        mag_a = math.sqrt(sum(v ** 2 for v in pos_dist.values()))
        mag_b = math.sqrt(sum(v ** 2 for v in profile.values()))
        if mag_a > 0 and mag_b > 0:
            sim = dot / (mag_a * mag_b)
        else:
            sim = 0.0

        similarities.append(sim)
        if sim < 0.7:
            violations.append(
                f"Character '{speaker}' voice drifted from profile "
                f"(POS similarity {sim:.2f}, threshold 0.70)"
            )

    if similarities:
        avg_sim = sum(similarities) / len(similarities)
    else:
        avg_sim = 0.8  # no comparable data

    observation = ""
    if violations:
        drifted = [v.split("'")[1] for v in violations if "'" in v]
        if drifted:
            names = ", ".join(drifted)
            observation = (
                f"I noticed {names}'s dialogue voice feels different from "
                f"their established pattern -- is this a deliberate shift?"
            )

    return CheckResult(
        name="character_voice",
        passed=len(violations) == 0,
        score=round(min(max(avg_sim, 0.0), 1.0), 3),
        details={
            "speakers_analyzed": len(similarities),
            "avg_pos_similarity": round(avg_sim, 3),
        },
        violations=violations,
        observation=observation,
    )


def _facts_contradict(fact_a: str, fact_b: str) -> bool:
    """Detect lexical contradictions between two topically-similar facts.

    Uses two signals:
    1. Negation asymmetry -- one fact negates something the other asserts.
    2. Attribute conflict -- same entity/slot with different values
       (e.g., "blue eyes" vs "brown eyes").
    """
    a_low = fact_a.lower()
    b_low = fact_b.lower()

    # --- Signal 1: negation asymmetry ---
    # Only flag when one fact negates something the other asserts AND they
    # share substantive content, reducing false positives from unrelated facts
    # that happen to contain a negation word.
    _NEGATION_MARKERS = {"not", "no", "never", "neither", "nor", "cannot",
                         "isn't", "wasn't", "aren't", "weren't", "doesn't",
                         "didn't", "won't", "wouldn't", "shouldn't", "couldn't",
                         "none"}
    _STOP_WORDS = {"the", "a", "an", "of", "in", "on", "at", "to", "for",
                   "and", "or", "but", "with", "from", "by", "as", "it",
                   "its", "this", "that", "these", "those"}
    _PREDICATE_VERBS = {"is", "was", "has", "had", "are", "were", "does",
                        "did", "will", "would", "can", "could", "should",
                        "be", "been", "being", "have"}
    a_words = set(re.findall(r"[a-z']+", a_low))
    b_words = set(re.findall(r"[a-z']+", b_low))
    a_neg = bool(a_words & _NEGATION_MARKERS)
    b_neg = bool(b_words & _NEGATION_MARKERS)
    # Require negation asymmetry + enough shared *content* words (excluding
    # stopwords, negation markers, and predicate verbs that indicate grammar
    # rather than topical overlap).  Threshold of 3 prevents false positives
    # from facts that merely share a character name + one common noun.
    noise = _NEGATION_MARKERS | _STOP_WORDS | _PREDICATE_VERBS
    shared_content = (a_words - noise) & (b_words - noise)
    if a_neg != b_neg and len(shared_content) >= 3:
        return True

    # --- Signal 2: attribute value conflict ---
    _ATTR_PATTERN = re.compile(
        r"(\w+)\s+(?:has|had|is|was|with|wields|carries|wears)\s+"
        r"(?:a\s+|an\s+|the\s+)?(\w+(?:\s+\w+)?)"
    )
    a_attrs = _ATTR_PATTERN.findall(a_low)
    b_attrs = _ATTR_PATTERN.findall(b_low)
    for a_subj, a_val in a_attrs:
        # Skip captures where the "value" is actually a negation word
        if a_val.split()[0] in _NEGATION_MARKERS:
            continue
        for b_subj, b_val in b_attrs:
            if b_val.split()[0] in _NEGATION_MARKERS:
                continue
            if a_subj == b_subj and a_val != b_val:
                return True

    return False


def _check_canon_breach(state: dict[str, Any]) -> CheckResult:
    """Canon breach: compare extracted facts against known canon.

    Finds topically overlapping fact pairs (Jaccard > 0.3) then checks
    for lexical contradictions: different attribute values for the same
    entity, negation patterns, or explicit divergence.
    """
    extracted = state.get("extracted_facts", [])
    ctx = state.get("retrieved_context", {}) or {}
    canon_facts = ctx.get("canon_facts", [])

    if not canon_facts:
        return CheckResult(
            name="canon_breach",
            passed=True,
            score=1.0,
            details={"note": "no canon facts available for comparison"},
        )

    violations: list[str] = []

    for ef in extracted:
        if isinstance(ef, dict):
            fact_text = ef.get("text") or ef.get("fact", "")
        else:
            fact_text = str(ef)
        fact_words = _content_words(fact_text)
        if not fact_words:
            continue

        for cf in canon_facts:
            if isinstance(cf, dict):
                canon_text = cf.get("text") or cf.get("fact", "")
            else:
                canon_text = str(cf)
            canon_words = _content_words(canon_text)
            if not canon_words:
                continue

            # Check for entity overlap suggesting the same topic
            overlap = fact_words & canon_words
            union = fact_words | canon_words
            if not union:
                continue
            jaccard = len(overlap) / len(union)

            if jaccard > 0.3 and _facts_contradict(fact_text, canon_text):
                violations.append(
                    f"Extracted fact '{fact_text}' contradicts canon: '{canon_text}'"
                )

    observation = ""
    if violations:
        observation = (
            f"I noticed {len(violations)} potential contradiction(s) with "
            f"established canon. Worth checking whether these are errors or "
            f"intentional reveals."
        )

    return CheckResult(
        name="canon_breach",
        passed=len(violations) == 0,
        score=1.0 if not violations else max(0.0, 1.0 - 0.5 * len(violations)),
        details={
            "extracted_facts_count": len(extracted),
            "canon_facts_count": len(canon_facts),
            "breaches_found": len(violations),
        },
        violations=violations,
        observation=observation,
    )


def _term_in_text(term: str, text: str) -> bool:
    """Check if a proper noun appears in text, using word boundaries for short terms.

    Short terms (under 4 chars) use regex word boundaries to avoid false
    matches like "Val" in "interval".  Longer terms use simple substring
    containment which is faster and sufficient.
    """
    term_lower = term.lower()
    if len(term_lower) < 4:
        return bool(re.search(rf"\b{re.escape(term_lower)}\b", text))
    return term_lower in text


def _extract_premise_terms(premise: str) -> tuple[list[str], list[str]]:
    """Extract protagonist name(s) and world-specific proper nouns from premise.

    Returns (protagonist_names, world_terms).
    Protagonist is heuristically the first capitalized multi-word or single
    proper noun in the premise text.
    """
    if not premise:
        return [], []

    # Find all capitalized proper nouns/phrases (2+ chars, not sentence-start)
    # Split into sentences to skip sentence-initial capitals
    words = premise.split()
    proper_nouns: list[str] = []
    for i, w in enumerate(words):
        # Strip punctuation for matching
        clean = re.sub(r"[^\w]", "", w)
        if not clean or len(clean) < 2:
            continue
        # Skip common words that happen to be capitalized
        if clean.lower() in {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
            "for", "is", "are", "was", "were", "be", "been", "being", "have",
            "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "it", "its", "this",
            "that", "these", "those", "with", "from", "by", "as", "of",
            "not", "no", "nor", "so", "yet", "both", "each", "every",
            "all", "any", "few", "more", "most", "some", "such", "than",
            "too", "very", "just", "about", "above", "after", "before",
            "between", "into", "through", "during", "until", "against",
            "among", "throughout", "despite", "without", "within",
            "along", "across", "behind", "beyond", "under", "over",
            "where", "when", "who", "what", "which", "how", "why",
            "book", "chapter", "scene", "story", "novel", "series",
            "world", "universe", "fantasy", "fiction",
        }:
            continue
        if clean[0].isupper() and not clean.isupper():
            proper_nouns.append(clean)

    # Protagonist heuristic: look for the first proper noun that is
    # followed by a verb-like word (is, was, has, had, studies, explores,
    # navigates, etc.).  Falls back to first proper noun if no verb follows.
    _VERB_HINTS = {
        "is", "was", "has", "had", "does", "did", "will", "would",
        "could", "should", "may", "might", "shall", "can",
    }
    # Clean words for position lookup (strip punctuation to match proper_nouns)
    cleaned_words = [re.sub(r"[^\w]", "", w) for w in words]
    protagonist: list[str] = []
    for noun in proper_nouns:
        # Find this noun's position in the cleaned word list
        try:
            noun_idx = cleaned_words.index(noun)
        except ValueError:
            continue
        if noun_idx + 1 < len(cleaned_words):
            next_word = cleaned_words[noun_idx + 1].lower()
            if next_word in _VERB_HINTS or next_word.endswith(("es", "ed", "ing", "ies")):
                protagonist = [noun]
                break
    if not protagonist and proper_nouns:
        protagonist = [proper_nouns[0]]
    world_terms = proper_nouns

    return protagonist, world_terms


def _check_premise_grounding(state: dict[str, Any]) -> CheckResult:
    """Check that scene prose is grounded in the universe premise.

    Extracts key terms from the premise (soul.md / premise_kernel) and
    verifies:
    1. The protagonist appears in the prose or extracted facts.
    2. At least some world-specific terms appear (zero = hard fail).

    This catches total premise departure and cross-universe contamination
    where the wrong story is being written.
    """
    # Get premise from state, with soul.md disk fallback (Item 4:
    # belt-and-suspenders defense against pipeline not propagating premise)
    premise = ""
    wf_instructions = state.get("workflow_instructions") or {}
    if isinstance(wf_instructions, dict):
        premise = wf_instructions.get("premise", "")
    if not premise:
        premise = state.get("premise_kernel", "")
    if not premise:
        # Fallback: read soul.md from universe directory.
        uni_path = state.get("_universe_path", "")
        if uni_path:
            from pathlib import Path as _P

            from tinyassets.universe_soul import premise_from_soul
            universe_dir = _P(uni_path)
            premise = premise_from_soul(universe_dir).strip()

    if not premise:
        return CheckResult(
            name="premise_grounding",
            passed=True,
            score=1.0,
            details={"note": "No premise available to check against"},
        )

    protagonist_names, world_terms = _extract_premise_terms(premise)

    draft = state.get("draft_output") or {}
    prose = draft.get("prose", "")
    if not prose:
        return CheckResult(
            name="premise_grounding",
            passed=True,
            score=1.0,
            details={"note": "No prose to check"},
        )

    prose_lower = prose.lower()

    # Check against prose and orient POV only.  extracted_facts is stale
    # here (fact extraction runs after structural eval in the commit pipeline).
    orient_result = state.get("orient_result") or {}
    pov_character = (orient_result.get("pov_character") or "").lower()
    combined_text = f"{prose_lower} {pov_character}"

    violations: list[str] = []

    # Check 1: Protagonist presence (observation, not violation —
    # heuristic is best-effort so missing protagonist degrades score
    # but doesn't produce a hard violation)
    protagonist_found = False
    if protagonist_names:
        for name in protagonist_names:
            if _term_in_text(name, combined_text):
                protagonist_found = True
                break

    # Check 2: World term coverage
    terms_found = 0
    terms_missing: list[str] = []
    if world_terms:
        for term in world_terms:
            if _term_in_text(term, combined_text):
                terms_found += 1
            else:
                terms_missing.append(term)

    world_term_ratio = terms_found / len(world_terms) if world_terms else 1.0

    # Zero world terms is a hard failure — total premise departure
    if world_terms and terms_found == 0:
        violations.append(
            f"Zero premise world terms found in scene. "
            f"Missing: {', '.join(terms_missing[:10])}. "
            f"This indicates total premise departure or "
            f"cross-universe contamination."
        )

    # Score: 1.0 if all terms found, scales down proportionally
    score = world_term_ratio
    if protagonist_names and not protagonist_found:
        score *= 0.5

    passed = len(violations) == 0

    observation = ""
    if protagonist_names and not protagonist_found:
        observation = (
            f"Protagonist '{protagonist_names[0]}' from premise not found "
            f"in scene prose — is this an intentional POV shift?"
        )
    elif passed and world_term_ratio < 0.5:
        observation = (
            f"Only {terms_found}/{len(world_terms)} premise terms appear "
            f"in this scene — is the story drifting from the premise?"
        )

    return CheckResult(
        name="premise_grounding",
        passed=passed,
        score=round(score, 3),
        details={
            "protagonist_names": protagonist_names,
            "protagonist_found": protagonist_found,
            "world_terms_total": len(world_terms),
            "world_terms_found": terms_found,
            "world_term_ratio": round(world_term_ratio, 3),
            "missing_terms": terms_missing[:10],
        },
        violations=violations,
        observation=observation,
    )


def _check_asp_constraint(state: dict[str, Any]) -> CheckResult:
    """ASP constraint check: validate via Clingo if available."""
    if not _HAS_ASP:
        return CheckResult(
            name="asp_constraint",
            passed=True,
            score=1.0,
            details={"note": "ASP engine not available (Phase 4)"},
        )

    # When ASP is available, delegate to the constraints module
    extracted = state.get("extracted_facts", [])
    world_rules = state.get("workflow_instructions", {}).get("world_rules", "")

    try:
        engine = ASPEngine()
        result = engine.validate(extracted, world_rules)
        return CheckResult(
            name="asp_constraint",
            passed=result.satisfiable,
            score=1.0 if result.satisfiable else 0.0,
            details={"asp_result": result.details},
            violations=result.violations if hasattr(result, "violations") else [],
        )
    except Exception as exc:
        return CheckResult(
            name="asp_constraint",
            passed=True,
            score=0.5,
            details={"note": f"ASP engine error: {exc}"},
        )


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------

class StructuralEvaluator:
    """Tier 1 deterministic structural evaluator.

    Usage::

        evaluator = StructuralEvaluator()
        result = evaluator.evaluate(scene_state)
        if result.hard_failure:
            verdict = "revert"
    """

    def evaluate(self, scene_state: dict[str, Any]) -> StructuralResult:
        """Run all 10 structural checks against a SceneState.

        Parameters
        ----------
        scene_state : dict
            A SceneState (or dict matching its shape).  Must contain at
            minimum ``draft_output`` with a ``prose`` key.

        Returns
        -------
        StructuralResult
            Aggregated pass/fail, per-check breakdown, and hard_failure flag.
        """
        draft = scene_state.get("draft_output") or {}
        prose = draft.get("prose", "")

        # Guard: empty or near-empty prose is an immediate hard failure.
        # This catches provider exhaustion producing "" instead of prose.
        word_count = len(prose.split()) if prose.strip() else 0
        if word_count < 10:
            return StructuralResult(
                checks=[
                    CheckResult(
                        name="empty_prose",
                        passed=False,
                        score=0.0,
                        violations=[
                            f"Prose is empty or too short ({word_count} words, "
                            f"minimum 10). Provider may have failed."
                        ],
                    ),
                ],
                aggregate_score=0.0,
                hard_failure=True,
                violations=[
                    f"Empty/insufficient prose ({word_count} words)"
                ],
            )

        # Chapter average word count for pacing (if available)
        orient = scene_state.get("orient_result", {}) or {}
        chapter_avg = orient.get("chapter_avg_words")

        # Run all checks
        checks = [
            _check_taaco_coherence(prose),
            _check_readability(prose),
            _check_pacing(prose, chapter_avg_words=chapter_avg),
            _check_chekhov(scene_state),
            _check_timeline(scene_state),
            _check_character_voice(prose, scene_state),
            _check_canon_breach(scene_state),
            _check_premise_grounding(scene_state),
            _check_asp_constraint(scene_state),
        ]

        # Aggregate
        all_violations: list[str] = []
        hard_failure = False
        weighted_sum = 0.0
        weight_total = 0.0

        for check in checks:
            all_violations.extend(check.violations)
            w = _CHECK_WEIGHTS.get(check.name, 0.1)
            weighted_sum += check.score * w
            weight_total += w

            if not check.passed and check.name in _HARD_FAILURE_CHECKS:
                hard_failure = True

        aggregate = weighted_sum / weight_total if weight_total > 0 else 0.0

        return StructuralResult(
            checks=checks,
            aggregate_score=round(aggregate, 3),
            hard_failure=hard_failure,
            violations=all_violations,
        )
