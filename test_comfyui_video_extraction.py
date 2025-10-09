#!/usr/bin/env python3
"""Test harness for ComfyUI video metadata extraction"""

import json
from pathlib import Path
from pprint import pprint
import sys
import subprocess

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from metascan.extractors.comfyui_video import ComfyUIVideoExtractor


def parse_info_file(info_path: Path) -> dict:
    """Parse the .info file format into expected metadata"""
    expected = {}

    with open(info_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # Parse "label: value" format
            if ': ' in line:
                key, value = line.split(': ', 1)

                # Parse LoRA array format
                if key == 'loras':
                    # Extract from format: [ name1: weight1, name2: weight2 ]
                    value = value.strip('[]')
                    loras = []
                    for lora_str in value.split(','):
                        lora_str = lora_str.strip()
                        if ':' in lora_str:
                            name, weight = lora_str.rsplit(':', 1)
                            loras.append({
                                'lora_name': name.strip(),
                                'lora_weight': float(weight.strip())
                            })
                    expected['loras'] = loras

                # Parse model array format
                elif key == 'model':
                    # Extract from format: [ model1, model2 ]
                    value = value.strip('[]')
                    models = [m.strip() for m in value.split(',')]
                    expected['models'] = models  # Store as list

                elif key == 'positive prompt':
                    expected['prompt'] = value

                elif key == 'negative prompt':
                    expected['negative_prompt'] = value

                elif key == 'Frame Rate':
                    expected['frame_rate'] = float(value) if value != '??' else None

                elif key == 'steps':
                    expected['steps'] = int(value) if value != '??' else None

                elif key == 'CFG':
                    expected['cfg_scale'] = float(value) if value != '??' else None

                elif key == 'Seed':
                    expected['seed'] = int(value) if value != '??' else None

                else:
                    # Store as-is with lowercase key
                    expected[key.lower()] = value

    return expected


def get_raw_video_metadata(video_path: Path) -> dict:
    """Get raw metadata from video using exiftool"""
    try:
        result = subprocess.run(
            ["exiftool", "-Comment", "-json", str(video_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data and len(data) > 0:
                comment = data[0].get("Comment", "")
                if comment:
                    try:
                        comment_data = json.loads(comment)
                        return comment_data
                    except json.JSONDecodeError:
                        return {"raw_comment": comment}
        return {}
    except Exception as e:
        print(f"Failed to extract raw metadata: {e}")
        return {}


def compare_metadata(expected: dict, extracted: dict) -> dict:
    """Compare expected vs extracted metadata"""
    comparison = {
        'matches': {},
        'mismatches': {},
        'missing': {},
        'unexpected': {}
    }

    # Check each expected field
    for key, expected_value in expected.items():
        if key in extracted:
            extracted_value = extracted.get(key)
            if extracted_value == expected_value:
                comparison['matches'][key] = extracted_value
            else:
                comparison['mismatches'][key] = {
                    'expected': expected_value,
                    'extracted': extracted_value
                }
        else:
            comparison['missing'][key] = expected_value

    # Check for unexpected fields
    for key, value in extracted.items():
        if key not in expected and key not in ['source', 'raw_metadata']:
            comparison['unexpected'][key] = value

    return comparison


def main():
    # Paths
    video_path = Path("/Volumes/Backup/linked/video_staging/2025-09-28/AnimateDiff_00039.mp4")
    info_path = Path("/Volumes/Backup/linked/video_staging/2025-09-28/AnimateDiff_00039.info")

    print("=" * 80)
    print("COMFYUI VIDEO METADATA EXTRACTION TEST")
    print("=" * 80)

    # Parse expected metadata
    print("\n1. EXPECTED METADATA (from .info file):")
    print("-" * 40)
    expected = parse_info_file(info_path)
    pprint(expected)

    # Get raw metadata from video
    print("\n2. RAW VIDEO METADATA (from exiftool):")
    print("-" * 40)
    raw_metadata = get_raw_video_metadata(video_path)
    if raw_metadata:
        # Print a truncated version if it's too long
        raw_str = json.dumps(raw_metadata, indent=2)
        if len(raw_str) > 2000:
            print(raw_str[:2000] + "...\n[TRUNCATED]")
        else:
            print(raw_str)
    else:
        print("No raw metadata found")

    # Extract using current extractor
    print("\n3. EXTRACTED METADATA (current extractor):")
    print("-" * 40)
    extractor = ComfyUIVideoExtractor()

    # Check if it can extract
    can_extract = extractor.can_extract(video_path)
    print(f"Can extract: {can_extract}")

    if can_extract:
        extracted = extractor.extract(video_path)
        if extracted:
            # Don't print raw_metadata as it's usually huge
            display_extracted = {k: v for k, v in extracted.items() if k != 'raw_metadata'}
            pprint(display_extracted)

            # Show if raw_metadata exists
            if 'raw_metadata' in extracted:
                print(f"\nraw_metadata keys: {list(extracted['raw_metadata'].keys())}")
        else:
            print("Extraction returned None")
            extracted = {}
    else:
        print("Extractor cannot handle this file")
        extracted = {}

    # Compare results
    print("\n4. COMPARISON:")
    print("-" * 40)
    comparison = compare_metadata(expected, extracted)

    print(f"\n✓ Matches ({len(comparison['matches'])}):")
    for key, value in comparison['matches'].items():
        print(f"  - {key}: {value}")

    print(f"\n✗ Mismatches ({len(comparison['mismatches'])}):")
    for key, values in comparison['mismatches'].items():
        print(f"  - {key}:")
        print(f"    Expected: {values['expected']}")
        print(f"    Extracted: {values['extracted']}")

    print(f"\n⚠ Missing ({len(comparison['missing'])}):")
    for key, value in comparison['missing'].items():
        print(f"  - {key}: {value}")

    print(f"\n? Unexpected ({len(comparison['unexpected'])}):")
    for key, value in comparison['unexpected'].items():
        print(f"  - {key}: {value}")

    # Analyze raw metadata structure if available
    if raw_metadata and 'prompt' in raw_metadata:
        print("\n5. RAW METADATA ANALYSIS:")
        print("-" * 40)
        prompt_data = raw_metadata.get('prompt', {})

        # Parse prompt data if it's a string
        if isinstance(prompt_data, str):
            try:
                prompt_data = json.loads(prompt_data)
            except json.JSONDecodeError:
                print("Failed to parse prompt data as JSON")
                prompt_data = {}

        # Analyze node types
        node_types = {}
        for node_id, node_data in prompt_data.items():
            if isinstance(node_data, dict):
                class_type = node_data.get('class_type', 'Unknown')
                if class_type not in node_types:
                    node_types[class_type] = []
                node_types[class_type].append(node_id)

        print("\nNode types found:")
        for class_type, node_ids in sorted(node_types.items()):
            print(f"  - {class_type}: {node_ids}")

        # Look for specific nodes that might contain our missing metadata
        print("\nSearching for metadata in nodes...")
        for node_id, node_data in prompt_data.items():
            if isinstance(node_data, dict):
                class_type = node_data.get('class_type', '')
                inputs = node_data.get('inputs', {})

                # Look for LoRA nodes
                if 'lora' in class_type.lower():
                    print(f"\n  LoRA Node {node_id} ({class_type}):")
                    print(f"    Inputs: {inputs}")

                # Look for model nodes
                if 'model' in class_type.lower() or 'checkpoint' in class_type.lower():
                    print(f"\n  Model Node {node_id} ({class_type}):")
                    print(f"    Inputs: {inputs}")

                # Look for sampler nodes
                if 'sampler' in class_type.lower() or 'ksampler' in class_type.lower():
                    print(f"\n  Sampler Node {node_id} ({class_type}):")
                    print(f"    Inputs: {inputs}")

                # Look for AnimateDiff nodes
                if 'animatediff' in class_type.lower() or 'animate' in class_type.lower():
                    print(f"\n  AnimateDiff Node {node_id} ({class_type}):")
                    print(f"    Inputs: {inputs}")


if __name__ == "__main__":
    main()