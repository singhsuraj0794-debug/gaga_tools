"""Region mapping + "what works / what doesn't" scoring for Meta Mind Map.

Maps TRIBE v2 predictions (fsaverage5, 20,484 cortical vertices) onto
content-relevant functional groups using the Destrieux surface atlas, then
derives a per-moment "works" score and flags.

This module must run under the dedicated TRIBE venv (Python 3.11) — it imports
nilearn/numpy. The Node API invokes it via TRIBE_PYTHON.
"""
from __future__ import annotations

import functools
import math

import numpy as np

# Destrieux parcels grouped into content-relevant dimensions. The grouping is
# adapted from the community TRIBE v2 ad-scorer (anatomical parcels -> function).
REGION_GROUPS: dict[str, list[str]] = {
    "Visual Attention": [
        "S_calcarine", "G_cuneus", "G_occipital_sup",
        "G_oc-temp_med-Lingual", "S_oc-temp_med_and_Lingual",
        "G_occipital_middle", "S_oc_middle_and_Lunatus",
        "S_oc_sup_and_transversal", "Pole_occipital",
        "G_and_S_occipital_inf", "S_occipital_ant",
    ],
    "Emotional Engagement": [
        "G_Ins_lg_and_S_cent_ins", "G_insular_short",
        "S_circular_insula_ant", "S_circular_insula_inf", "S_circular_insula_sup",
        "G_orbital", "S_orbital_lateral", "S_orbital_med-olfact", "S_orbital-H_Shaped",
        "G_and_S_cingul-Ant", "G_and_S_cingul-Mid-Ant", "G_subcallosal",
    ],
    "Reward & Motivation": [
        "G_rectus", "S_suborbital", "G_and_S_frontomargin",
        "G_and_S_transv_frontopol", "G_subcallosal", "G_and_S_cingul-Mid-Post",
    ],
    "Memory Encoding": [
        "G_oc-temp_med-Parahip", "G_oc-temp_lat-fusifor", "S_oc-temp_lat",
        "S_collat_transv_ant", "S_collat_transv_post", "Pole_temporal",
        "G_temporal_inf", "S_temporal_inf",
    ],
    "Language & Message": [
        "G_front_inf-Opercular", "G_front_inf-Triangul", "G_front_inf-Orbital",
        "S_front_inf", "G_pariet_inf-Angular", "G_pariet_inf-Supramar",
        "G_temporal_middle",
    ],
    "Social Connection": [
        "G_front_sup", "S_front_sup", "G_precuneus", "S_subparietal",
        "G_cingul-Post-dorsal", "G_cingul-Post-ventral",
        "S_intrapariet_and_P_trans", "S_interm_prim-Jensen",
    ],
    "Auditory Impact": [
        "G_temp_sup-Lateral", "G_temp_sup-G_T_transv", "G_temp_sup-Plan_tempo",
        "G_temp_sup-Plan_polar", "S_temporal_sup", "S_temporal_transverse",
    ],
}

# Contribution of each dimension to the overall "works" score. Attention +
# emotion + reward dominate short-form engagement; language/social add nuance.
WORKS_WEIGHTS: dict[str, float] = {
    "Visual Attention": 1.4,
    "Emotional Engagement": 1.3,
    "Reward & Motivation": 1.2,
    "Memory Encoding": 1.0,
    "Social Connection": 1.0,
    "Auditory Impact": 0.8,
    "Language & Message": 0.7,
}

# Plain-language explanation of each predicted brain network: what it does, why
# it matters for a hook, and which video features tend to switch it off.
BRAIN_FUNCTIONS: dict[str, dict] = {
    "Visual Attention": {
        "network": "Visual cortex (occipital lobe)",
        "means": "How strongly the brain is processing what's on screen.",
        "hookRole": "The eye must be captured in the first second — motion, contrast and faces drive this.",
        "switchOff": "static, low-contrast or dark frames",
    },
    "Emotional Engagement": {
        "network": "Salience & limbic regions (insula, cingulate)",
        "means": "Whether the content feels like something worth reacting to.",
        "hookRole": "An emotional spike (surprise, humour, tension) makes the brain tag it as relevant.",
        "switchOff": "faceless, flat or silent moments",
    },
    "Reward & Motivation": {
        "network": "Reward valuation (medial orbitofrontal, frontal pole)",
        "means": "Whether the brain values the outcome and wants it.",
        "hookRole": "Promise a payoff early so the brain anticipates a reward.",
        "switchOff": "no clear promise or payoff on screen",
    },
    "Memory Encoding": {
        "network": "Medial temporal / fusiform (memory)",
        "means": "Whether the moment is being encoded and will be recalled.",
        "hookRole": "Distinctive cues (brand, phrase, motif) make the ad memorable.",
        "switchOff": "generic, textless, static frames",
    },
    "Language & Message": {
        "network": "Language network (Broca's area, temporal cortex)",
        "means": "Whether the verbal message is being decoded.",
        "hookRole": "One clear claim the viewer can process instantly.",
        "switchOff": "no on-screen text or speech",
    },
    "Social Connection": {
        "network": "Social brain (TPJ, medial prefrontal, precuneus)",
        "means": "Whether the brain relates to a person in the content.",
        "hookRole": "A human face or reaction pulls social attention and empathy.",
        "switchOff": "no person on screen",
    },
    "Auditory Impact": {
        "network": "Auditory cortex (superior temporal)",
        "means": "How strongly sound is engaging the auditory system.",
        "hookRole": "Music, voice or a sound effect on the beat reinforces the cut.",
        "switchOff": "quiet or absent audio",
    },
}

# Which observations explain a weak signal for each function.
FUNCTION_OBS: dict[str, list[str]] = {
    "Visual Attention": [
        "static shot (almost no movement)", "dark, low-exposure frame",
        "flat, low-contrast image", "fast motion",
    ],
    "Emotional Engagement": [
        "no person on screen", "static shot (almost no movement)", "quiet / no audio",
    ],
    "Reward & Motivation": [
        "no on-screen text / captions", "static shot (almost no movement)",
    ],
    "Memory Encoding": [
        "no on-screen text / captions", "static shot (almost no movement)",
    ],
    "Language & Message": ["no on-screen text / captions"],
    "Social Connection": ["no person on screen"],
    "Auditory Impact": ["quiet / no audio"],
}
OBS_SHORT = {
    "static shot (almost no movement)": "static shot",
    "dark, low-exposure frame": "dark frame",
    "flat, low-contrast image": "low contrast",
    "no person on screen": "no person",
    "quiet / no audio": "silent audio",
    "no on-screen text / captions": "no on-screen text",
    "fast motion": "too-fast motion",
}


@functools.lru_cache(maxsize=1)
def load_atlas():
    """Return (labels, label_map) for the fsaverage5 Destrieux surface atlas."""
    from nilearn import datasets

    atlas = datasets.fetch_atlas_surf_destrieux()
    labels = list(atlas["labels"])
    label_map = np.concatenate(
        [np.asarray(atlas["map_left"]), np.asarray(atlas["map_right"])]
    )
    return labels, label_map


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _group_masks(labels, label_map, groups):
    idx = {name: i for i, name in enumerate(labels)}
    masks = {}
    for cat, names in groups.items():
        mask = np.zeros(label_map.shape[0], dtype=bool)
        for nm in names:
            if nm in idx:
                mask |= label_map == idx[nm]
        masks[cat] = mask
    return masks


# Observation (from _tribe_features) -> (issue template, concrete fix). {t} and
# {b} are filled from the moment. Order = salience priority.
OBS_RULES: dict[str, tuple[str, str]] = {
    "hard cut / scene change": (
        "A hard cut lands around {t}s into a new shot that doesn't hold attention",
        "Make the cut land on something new and rewarding — motion, a face, or an on-screen claim.",
    ),
    "no face on screen": (
        "No face or person is on screen around {t}s",
        "Show a person (face, hands, or a reaction) to anchor social attention.",
    ),
    "dark, low-exposure frame": (
        "The frame is dark around {t}s (brightness {b})",
        "Raise exposure/brightness, or cut to a brighter, higher-contrast shot.",
    ),
    "static shot (almost no movement)": (
        "The shot is almost static around {t}s — nothing for the eye to track",
        "Add camera motion, a reveal, or a quick cut so the eye keeps moving.",
    ),
    "quiet / no audio": (
        "Audio is near-silent around {t}s",
        "Add a voiceover, music bed, or a sound effect on the beat.",
    ),
    "flat, low-contrast image": (
        "The image is flat and low-contrast around {t}s",
        "Increase contrast/saturation so the subject pops off the background.",
    ),
    "very bright / blown-out frame": (
        "The frame is blown out around {t}s",
        "Reduce highlights so the subject reads clearly.",
    ),
    "fast motion": (
        "Motion is very fast around {t}s, which can be hard to parse",
        "Slow the pace or hold a frame so the message registers.",
    ),
    "no on-screen text / captions": (
        "No on-screen text or caption around {t}s — the message is only implied",
        "Add a short on-screen claim/caption so the value is read, not guessed.",
    ),
}
OBS_PRIORITY = list(OBS_RULES.keys())
# Observations that are good news — never turn them into an issue.
POSITIVE_OBS = {"person on screen", "face present"}


def _obs_at(features: dict | None, t: float) -> list[str]:
    if not features:
        return []
    return features.get("observations", {}).get(str(int(round(t))), []) or []


def _moment_at(features: dict | None, t: float) -> dict:
    if not features:
        return {}
    for m in features.get("perSecond", []):
        if m["t"] == int(round(t)):
            return m
    return {}


def _pick_observations(obs: list[str], limit: int = 2) -> list[str]:
    def is_issue(o: str) -> bool:
        return o not in POSITIVE_OBS and not o.startswith("on-screen text:")

    ranked = [o for o in OBS_PRIORITY if o in obs and is_issue(o)]
    ranked += [o for o in obs if o not in ranked and is_issue(o)]
    return ranked[:limit]


def compute_analysis(
    preds: np.ndarray,
    ts_seconds: np.ndarray | None = None,
    features: dict | None = None,
) -> dict:
    """Compute per-timestep dimension scores, a works score, and flags.

    preds : (n_timesteps, n_vertices) raw TRIBE predictions.
    ts_seconds : optional timestamps (seconds) per timestep (defaults to 1 Hz).
    features : optional output of ``_tribe_features.extract_features`` — when
        provided, the critique cites concrete visual/audio observations.
    """
    preds = np.asarray(preds, dtype=np.float64)
    n_t, n_v = preds.shape
    if ts_seconds is None:
        ts_seconds = np.arange(n_t, dtype=float)

    labels, label_map = load_atlas()
    masks = _group_masks(labels, label_map, REGION_GROUPS)

    g_mean = float(preds.mean())
    g_std = float(preds.std())
    if g_std < 1e-8:
        g_std = 1.0

    series: dict[str, list[float]] = {}
    for cat, mask in masks.items():
        if not mask.any():
            series[cat] = [0.5] * n_t
            continue
        raw = preds[:, mask].mean(axis=1)
        series[cat] = _sigmoid((raw - g_mean) / g_std).tolist()

    # Absolute engagement blend (0-1) — used for segment scores so they are
    # comparable across clips. The timeline `worksScore` is min-max shaped so the
    # curve reads as relative engagement within the clip.
    w_sum = sum(WORKS_WEIGHTS.get(c, 1.0) for c in series)
    blended = np.zeros(n_t)
    for cat, vals in series.items():
        blended += WORKS_WEIGHTS.get(cat, 1.0) * np.asarray(vals)
    blended /= max(w_sum, 1e-8)
    works_raw = [float(x) for x in blended]

    lo, hi = float(blended.min()), float(blended.max())
    rng = hi - lo if hi - lo > 1e-8 else 1.0
    works = ((blended - lo) / rng).tolist()
    if n_t <= 1:
        works = [round(float(blended[0]), 4)] if n_t == 1 else []

    wm = float(np.mean(works))
    wsd = float(np.std(works))
    dur = (float(ts_seconds[-1]) + 1.0) if n_t else 0.0

    def _win(a: float, b: float) -> list[int]:
        return [i for i, t in enumerate(ts_seconds) if a <= t < b]

    def _seg_score(idxs: list[int]) -> float | None:
        if not idxs:
            return None
        return float(np.mean([works_raw[i] for i in idxs])) * 100.0

    # Hook / Bridge / Offer windows (vidcognition-style).
    SEGMENTS = [("Hook", 0, 3), ("Bridge", 3, 7), ("Offer", 7, 15)]
    segments = []
    for name, a, b in SEGMENTS:
        end = min(b, round(dur, 1))
        idxs = _win(a, min(b, dur))
        sc = _seg_score(idxs)
        segments.append({
            "name": name,
            "start": a,
            "end": end,
            "score": None if sc is None else round(sc),
            "available": bool(idxs),
        })
    scored = [s for s in segments if s["score"] is not None]
    overall_score = round(float(np.mean([s["score"] for s in scored]))) if scored else 0

    # Drop-off annotations — where engagement falls most between moments.
    dropoffs = []
    for i in range(1, n_t):
        d = works[i - 1] - works[i]
        if d > 0.15:
            dropoffs.append({
                "t": round(float(ts_seconds[i]), 2),
                "drop": round(float(d), 3),
                "severity": "high" if d > 0.35 else "medium",
            })

    peak_idx = [i for i, w in enumerate(works) if w > wm + 0.6 * wsd]
    dip_idx = [i for i, w in enumerate(works) if w < wm - 0.6 * wsd]
    top = sorted(range(n_t), key=lambda i: works[i], reverse=True)[:5]
    weak = sorted(range(n_t), key=lambda i: works[i])[:5]
    dominant = [max(series, key=lambda c: series[c][i]) for i in range(n_t)]

    flags = []
    hook = _seg_score(_win(0, min(3.0, dur))) or 0.0
    cta = _seg_score(_win(max(0.0, dur - 3.0), dur)) or 0.0
    if hook >= 60:
        flags.append({"kind": "hook", "at": 0.0, "label": "Strong opening hook"})
    elif hook and hook < 50:
        flags.append({"kind": "weak_hook", "at": 0.0, "label": "Weak opening hook"})
    if cta >= 60:
        flags.append({"kind": "cta", "at": dur, "label": "Strong closing / CTA"})
    for i in dip_idx:
        flags.append({
            "kind": "drop", "at": float(ts_seconds[i]),
            "label": f"Attention dip at {ts_seconds[i]:.0f}s",
        })

    # ---- Tailored breakdown: what actually happened, at which second ----
    breakdown: list[dict] = []
    seen_t: set[int] = set()
    candidates = [d["t"] for d in dropoffs] + [round(float(ts_seconds[i]), 2) for i in weak[:3]]
    for t in candidates:
        key = int(round(t))
        if key in seen_t:
            continue
        seen_t.add(key)
        obs = _pick_observations(_obs_at(features, t), limit=2)
        mom = _moment_at(features, t)
        drop = next((d["drop"] for d in dropoffs if int(round(d["t"])) == key), None)
        low_dims: list[str] = []
        if 0 <= key < n_t:
            dm = {c: series[c][key] for c in series}
            low_dims = [k for k, _ in sorted(dm.items(), key=lambda kv: kv[1])[:2]]
        why = (
            f"engagement fell {drop:.0%} at this exact moment" if drop
            else f"one of the weakest moments ({works[key]*100:.0f}/100)" if 0 <= key < n_t
            else "low engagement"
        )
        for o in (obs or ["no strong visual or audio cue detected here"]):
            rule = OBS_RULES.get(o)
            what = rule[0].format(t=t, b=f"{mom.get('brightness', 0):.0%}") if rule else o
            fix = rule[1] if rule else "Strengthen the visual/audio cue at this moment."
            breakdown.append({
                "t": t, "what": what, "why": why, "fix": fix,
                "lowDimensions": low_dims,
                "severity": "high" if (drop and drop > 0.35) else "medium",
            })
    breakdown = breakdown[:6]

    # ---- Rule-based strategist critique (features-aware) ----
    FIXES = {
        "Visual Attention": "Open with a stronger visual pattern-interrupt — bold motion, a colour shift, or a face/close-up in the first frame.",
        "Emotional Engagement": "Add an emotional beat: surprise, humour, or a relatable problem the viewer recognises instantly.",
        "Reward & Motivation": "Make the payoff explicit — show the transformation or benefit, not just the product.",
        "Memory Encoding": "Give viewers a distinctive, repeatable cue (brand, phrase, or visual motif) they'll recall later.",
        "Language & Message": "Tighten the claim into one clear sentence; cut anything the viewer has to decode.",
        "Social Connection": "Add a human element — a person, a face, or social proof.",
        "Auditory Impact": "Use sound design or music that lands on the beat of the visual cut.",
    }
    per_segment = []
    for s in segments:
        idxs = _win(s["start"], min(s["end"] or dur, dur))
        if not idxs:
            per_segment.append({
                "segment": s["name"], "score": s["score"], "verdict": "n/a",
                "strengths": [], "weaknesses": [], "issues": [], "fixes": [],
            })
            continue
        dm = {c: float(np.mean([series[c][i] for i in idxs])) for c in series}
        ordered = sorted(dm.items(), key=lambda kv: kv[1], reverse=True)
        strengths = [k for k, _ in ordered[:2]]
        weaknesses = [k for k, _ in ordered[-2:]]

        # Feature-driven issues specific to this window.
        win_obs: list[str] = []
        for i in idxs:
            win_obs.extend(_obs_at(features, ts_seconds[i]))
        picked = _pick_observations(list(dict.fromkeys(win_obs)), limit=3)
        issues: list[str] = []
        fixes: list[str] = []
        for o in picked:
            rule = OBS_RULES.get(o)
            if not rule:
                continue
            t_obs = next(
                (float(ts_seconds[i]) for i in idxs if o in _obs_at(features, ts_seconds[i])),
                float(s["start"]),
            )
            mom = _moment_at(features, t_obs)
            issues.append(rule[0].format(t=t_obs, b=f"{mom.get('brightness', 0):.0%}"))
            fixes.append(rule[1])
        if not issues:  # no feature data — fall back to brain dimensions
            issues = [f"Low {k.lower()} signal across this window" for k in weaknesses]
            fixes = [FIXES[k] for k in weaknesses if k in FIXES]

        sc = s["score"] or 0
        verdict = "strong" if sc >= 70 else "ok" if sc >= 50 else "weak"
        per_segment.append({
            "segment": s["name"], "score": s["score"], "verdict": verdict,
            "strengths": strengths, "weaknesses": weaknesses,
            "issues": issues, "fixes": fixes,
        })
    weakest = min(scored, key=lambda s: s["score"]) if scored else None
    summary = f"Overall {overall_score}/100."
    if breakdown:
        summary += f" Main issue at {breakdown[0]['t']:.0f}s: {breakdown[0]['what'].rstrip('.')}."
    if weakest and weakest["score"] is not None and weakest["score"] < 60:
        summary += f" Weakest window: {weakest['name']} ({weakest['score']}/100) — fix that first."
    elif overall_score >= 70:
        summary += " Strong across the board; safe to test as-is."
    critique = {
        "summary": summary, "segments": per_segment,
        "breakdown": breakdown, "source": "heuristic",
    }

    # ---- Brain-function interpretation (what's engaged / switched off, why) ----
    def _dim_score(dim: str, idxs: list[int]) -> int | None:
        if not idxs:
            return None
        return round(float(np.mean([series[dim][i] for i in idxs])) * 100)

    def _reason(dim: str) -> str:
        if not features:
            return ""
        order = sorted(range(n_t), key=lambda i: series[dim][i])[:2]
        labels: list[str] = []
        times: list[int] = []
        for i in order:
            for o in _obs_at(features, ts_seconds[i]):
                if o in FUNCTION_OBS.get(dim, []) and OBS_SHORT[o] not in labels:
                    labels.append(OBS_SHORT[o])
                    times.append(int(ts_seconds[i]))
        if not labels:
            return ""
        return f"{', '.join(labels)} at {', '.join(str(t) + 's' for t in times)}"

    brain_functions = []
    for dim, meta in BRAIN_FUNCTIONS.items():
        overall_d = _dim_score(dim, list(range(n_t)))
        by_seg = {name: _dim_score(dim, _win(a, min(b, dur))) for name, a, b in SEGMENTS}
        status = "engaged" if (overall_d or 0) >= 55 else "under"
        brain_functions.append({
            "name": dim,
            "network": meta["network"],
            "means": meta["means"],
            "hookRole": meta["hookRole"],
            "switchOff": meta["switchOff"],
            "score": overall_d,
            "status": status,
            "bySegment": by_seg,
            "reason": _reason(dim) if status == "under" else "",
        })

    verdict = "strong" if wm >= 0.6 else "moderate" if wm >= 0.4 else "weak"

    return {
        "nTimesteps": int(n_t),
        "nVertices": int(n_v),
        "timestamps": [round(float(t), 2) for t in ts_seconds],
        "dimensions": {k: [round(float(x), 4) for x in v] for k, v in series.items()},
        "worksScore": [round(float(x), 4) for x in works],
        "worksRaw": [round(float(x), 4) for x in works_raw],
        "overall": round(wm, 4),
        "overallScore": overall_score,
        "verdict": verdict,
        "segments": segments,
        "dropoffs": dropoffs,
        "moments": (features or {}).get("perSecond", []),
        "clipText": (features or {}).get("text", ""),
        "brainFunctions": brain_functions,
        "critique": critique,
        "topMoments": [
            {"t": round(float(ts_seconds[i]), 2), "score": round(float(works[i]), 4),
             "dominant": dominant[i]}
            for i in top
        ],
        "weakMoments": [
            {"t": round(float(ts_seconds[i]), 2), "score": round(float(works[i]), 4),
             "dominant": dominant[i]}
            for i in weak
        ],
        "flags": flags,
        "metadata": {"globalMean": round(g_mean, 4), "globalStd": round(g_std, 4)},
    }
