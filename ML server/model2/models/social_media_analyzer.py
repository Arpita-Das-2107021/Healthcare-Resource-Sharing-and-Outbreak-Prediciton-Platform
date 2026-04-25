"""
social_media_analyzer.py — Hashtag-Based Outbreak Confirmation
==============================================================
Reads social_media_hashtags.csv (real or simulated hashtag data),
normalizes counts per upazila, and returns a confirmation score
(0.0 to 1.0) per upazila that is combined with the ST-GNN prediction.

Final confidence = 0.7 * model_prob + 0.3 * social_score

RUN: python models/social_media_analyzer.py
"""

import csv, os
from datetime import datetime, timedelta
from collections import defaultdict
import random

random.seed(42)

BASE     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "../data")

UPAZILAS = [
    "Dhaka", "Chittagong", "Rajshahi", "Khulna",
    "Barisal", "Sylhet", "Rangpur", "Mymensingh"
]

# ── Generate sample hashtag CSV if not exists ─────────────────────────────────
def generate_sample_hashtags(num_days=90):
    """
    Generates simulated hashtag data in wide format (one column per disease signal).
    Columns: date, upazila, hashtag_dengue, hashtag_diarrhea, hashtag_fever,
             hashtag_respiratory, hashtag_allergy
    In real use: replace this CSV with actual scraped Twitter/Facebook data.
    """
    path = os.path.join(DATA_DIR, "social_media_hashtags.csv")
    if os.path.exists(path):
        return path

    START = datetime(2024, 1, 1)

    SPIKES = {
        "Dhaka":      (15, 45, "hashtag_dengue"),
        "Chittagong": (15, 45, "hashtag_dengue"),
        "Barisal":    (30, 60, "hashtag_diarrhea"),
        "Khulna":     (30, 60, "hashtag_diarrhea"),
        "Sylhet":     (10, 35, "hashtag_respiratory"),
        "Rangpur":    (10, 35, "hashtag_respiratory"),
    }

    rows = []
    for day in range(num_days):
        date = (START + timedelta(days=day)).strftime("%Y-%m-%d")
        for upazila in UPAZILAS:
            row = {"date": date, "upazila": upazila,
                   "hashtag_dengue": random.randint(0, 5),
                   "hashtag_diarrhea": random.randint(0, 5),
                   "hashtag_fever": random.randint(10, 20),
                   "hashtag_respiratory": random.randint(0, 5),
                   "hashtag_allergy": random.randint(0, 3)}

            spike = SPIKES.get(upazila)
            if spike:
                s_start, s_end, col = spike
                if s_start <= day <= s_end:
                    peak     = (s_start + s_end) // 2
                    strength = max(0, 1 - abs(day - peak) / ((s_end - s_start) / 2))
                    row[col] += int(random.randint(10, 30) * strength)

            rows.append(row)

    os.makedirs(DATA_DIR, exist_ok=True)
    fieldnames = ["date", "upazila", "hashtag_dengue", "hashtag_diarrhea",
                  "hashtag_fever", "hashtag_respiratory", "hashtag_allergy"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(rows)

    print(f"  Generated social_media_hashtags.csv — {len(rows):,} records")
    return path

# ── Load and analyze hashtags ─────────────────────────────────────────────────
def load_hashtag_scores(last_n_days=7, target_dates=None):
    """
    Returns {upazila: {disease: score}} for the specified date window.

    target_dates : set/list of 'YYYY-MM-DD' strings — use these exact dates.
                   When provided, last_n_days is ignored.
    last_n_days  : fallback — take the last N dates in the CSV.
    Score = normalized hashtag count (0.0 to 1.0).
    """
    path = os.path.join(DATA_DIR, "social_media_hashtags.csv")
    if not os.path.exists(path):
        generate_sample_hashtags()
    if not os.path.exists(path):
        return {}

    records = []
    with open(path) as f:
        for row in csv.DictReader(f):
            records.append(row)

    if not records:
        return {}

    if target_dates is not None:
        recent_dates = set(target_dates)
    else:
        all_dates = sorted(set(r["date"] for r in records))
        recent_dates = set(all_dates[-last_n_days:])

    counts = defaultdict(lambda: defaultdict(int))
    for r in records:
        if r["date"] not in recent_dates:
            continue
        upazila = r["upazila"]
        counts[upazila]["Fever/Flu"]     += int(r.get("hashtag_dengue", 0)) + int(r.get("hashtag_fever", 0))
        counts[upazila]["Diarrhea"]      += int(r.get("hashtag_diarrhea", 0))
        counts[upazila]["Respiratory"]   += int(r.get("hashtag_respiratory", 0))
        counts[upazila]["Allergy/Fever"] += int(r.get("hashtag_allergy", 0))

    scores = {}
    for upazila, disease_counts in counts.items():
        max_count = max(disease_counts.values()) if disease_counts else 1
        scores[upazila] = {
            disease: min(1.0, count/max(max_count, 1))
            for disease, count in disease_counts.items()
        }

    return scores

# ── Combine ST-GNN + Social Media ─────────────────────────────────────────────
def get_combined_confidence(pharmacy_prob, upazila, likely_disease,
                             social_scores,
                             model_weight=0.7, social_weight=0.3):
    """
    Combines ST-GNN model probability with social media confirmation.
    final_confidence = model_weight * model_prob + social_weight * social_score
    """
    social_score = 0.0
    if upazila in social_scores and likely_disease in social_scores[upazila]:
        social_score = social_scores[upazila][likely_disease]

    final = model_weight * pharmacy_prob + social_weight * social_score

    if social_score > 0.5:
        confirmation = "CONFIRMED by social media"
    elif social_score > 0.2:
        confirmation = "Partial social signal"
    else:
        confirmation = "No social signal"

    return round(final, 4), round(social_score, 4), confirmation

# ── Print social media summary ────────────────────────────────────────────────
def print_social_summary(social_scores):
    print("\n" + "="*65)
    print("  Social Media Hashtag Analysis — Last 7 days")
    print("="*65)
    print(f"  {'Upazila':<22} {'Disease':<20} {'Social Score':>12}")
    print("-"*65)
    for upazila in UPAZILAS:
        if upazila not in social_scores:
            print(f"  {upazila:<22} {'No signal':<20} {'0.000':>12}")
            continue
        for disease, score in sorted(social_scores[upazila].items(),
                                      key=lambda x:x[1], reverse=True)[:2]:
            print(f"  {upazila:<22} {disease:<20} {score:>12.3f}")
    print("-"*65)

# ── Main (standalone test) ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n"+"="*65)
    print("  Social Media Analyzer — Outbreak Confirmation")
    print("="*65)

    print("\n  Generating/loading hashtag data...")
    generate_sample_hashtags()

    print("\n  Analyzing last 7 days of hashtags...")
    scores = load_hashtag_scores(last_n_days=7)
    print_social_summary(scores)

    print("\n  Demo — Combined Confidence (ST-GNN + Social Media):")
    print("-"*65)
    print(f"  {'Upazila':<22} {'Model%':>7}  {'Social':>7}  {'Final%':>7}  Confirmation")
    print("-"*65)

    demo = [
        ("Dhaka",      0.87, "Fever/Flu"),
        ("Chittagong", 0.72, "Fever/Flu"),
        ("Barisal",    0.65, "Diarrhea"),
        ("Khulna",     0.41, "Diarrhea"),
        ("Sylhet",     0.30, "Respiratory"),
        ("Rangpur",    0.28, "Respiratory"),
    ]
    for upazila, prob, disease in demo:
        final, sscore, conf = get_combined_confidence(prob, upazila, disease, scores)
        print(f"  {upazila:<22} {prob*100:>6.1f}%  {sscore:>7.3f}  "
              f"{final*100:>6.1f}%  {conf}")
    print("-"*65)
    print("\n  Social media analyzer ready")
    print("  Replace social_media_hashtags.csv with real scraped data")
    print("="*65)
