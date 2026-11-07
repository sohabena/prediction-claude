import json
import shutil
from pathlib import Path

candidates_dir = Path("models/specialists/candidates")
specialists_dir = Path("models/specialists")
metadata_file = specialists_dir / "metadata.json"

metadata = {}
if metadata_file.exists():
    with open(metadata_file, "r", encoding="utf-8") as f:
        metadata = json.load(f)

promoted_keys = ["wbbl_women", "bbl_men", "odi_men", "t20i_men"]

for key in promoted_keys:
    cand_zip = candidates_dir / f"{key}_v1.zip"
    cand_norm = candidates_dir / f"{key}_v1.vecnorm.pkl"
    cand_manifest = candidates_dir / f"{key}_train_manifest.json"
    
    if not cand_zip.exists() or not cand_manifest.exists():
        print(f"Skipping {key}: candidate files missing")
        continue

    dest_zip = specialists_dir / f"{key}_v1.zip"
    dest_norm = specialists_dir / f"{key}_v1.vecnorm.pkl"

    shutil.copyfile(cand_zip, dest_zip)
    if cand_norm.exists():
        shutil.copyfile(cand_norm, dest_norm)

    with open(cand_manifest, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    metrics = manifest.get("holdout_metrics", {})
    entry = metadata.get(key, {})
    entry.update({
        "key": key,
        "path": str(dest_zip),
        "format": metrics.get("format_name", key),
        "matches_trained_on": manifest.get("split_sizes", {}).get("train", 20),
        "metrics": metrics,
        "active": True,
        "promoted_at": manifest.get("created_at"),
    })
    metadata[key] = entry
    print(f"Successfully promoted {key} (Holdout P&L: ${metrics.get('total_pnl', 0):,.2f}, Sharpe: {metrics.get('sharpe_ratio', 0):.2f})")

with open(metadata_file, "w", encoding="utf-8") as f:
    json.dump(metadata, f, indent=2)

print("\nUpdated models/specialists/metadata.json successfully!")
