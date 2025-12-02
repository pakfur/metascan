#!/usr/bin/env python3
"""
Test harness for ComfyUI video metadata extractors in isolation.
Tests both the original and improved extractors against target videos.
"""

import json
from pathlib import Path
from pprint import pprint
import sys
from typing import Dict, Any, List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from metascan.extractors.comfyui_video import ComfyUIVideoExtractor


def parse_info_file(info_path: Path) -> Dict[str, Any]:
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
                    if len(models) == 1:
                        expected['model'] = models[0]
                    else:
                        expected['models'] = models

                elif key == 'positive prompt':
                    expected['prompt'] = value

                elif key == 'negative prompt':
                    expected['negative_prompt'] = value

                elif key == 'Frame Rate':
                    try:
                        expected['frame_rate'] = float(value)
                    except ValueError:
                        pass  # Skip invalid values like "??"

                elif key == 'steps':
                    try:
                        expected['steps'] = int(value)
                    except ValueError:
                        pass  # Skip invalid values like "??"

                elif key == 'CFG':
                    try:
                        expected['cfg_scale'] = float(value)
                    except ValueError:
                        pass

                elif key == 'Seed':
                    try:
                        expected['seed'] = int(value)
                    except ValueError:
                        pass

                else:
                    # Store as-is with lowercase key
                    expected[key.lower()] = value

    return expected


def calculate_accuracy(expected: Dict[str, Any], extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate extraction accuracy metrics"""

    # Define which fields to check
    fields_to_check = [
        'prompt', 'negative_prompt', 'model', 'models',
        'sampler', 'scheduler', 'cfg_scale', 'seed',
        'steps', 'frame_rate', 'loras'
    ]

    metrics = {
        'total_fields': 0,
        'extracted_fields': 0,
        'correct_fields': 0,
        'missing_fields': [],
        'incorrect_fields': [],
        'accuracy': 0.0
    }

    for field in fields_to_check:
        if field in expected:
            metrics['total_fields'] += 1

            if field in extracted:
                metrics['extracted_fields'] += 1

                # Compare values
                expected_val = expected[field]
                extracted_val = extracted[field]

                # Special handling for different types
                if field == 'loras':
                    # Compare LoRA lists
                    if compare_lora_lists(expected_val, extracted_val):
                        metrics['correct_fields'] += 1
                    else:
                        metrics['incorrect_fields'].append(field)
                elif field in ['prompt', 'negative_prompt']:
                    # Strip whitespace for text comparison
                    if str(expected_val).strip() == str(extracted_val).strip():
                        metrics['correct_fields'] += 1
                    else:
                        metrics['incorrect_fields'].append(field)
                else:
                    # Direct comparison for other fields
                    if expected_val == extracted_val:
                        metrics['correct_fields'] += 1
                    else:
                        metrics['incorrect_fields'].append(field)
            else:
                metrics['missing_fields'].append(field)

    # Calculate accuracy percentage
    if metrics['total_fields'] > 0:
        metrics['accuracy'] = (metrics['correct_fields'] / metrics['total_fields']) * 100

    return metrics


def compare_lora_lists(expected: List[Dict], extracted: List[Dict]) -> bool:
    """Compare two lists of LoRA dictionaries"""
    if len(expected) != len(extracted):
        return False

    # Sort both lists by lora_name for comparison
    expected_sorted = sorted(expected, key=lambda x: x.get('lora_name', ''))
    extracted_sorted = sorted(extracted, key=lambda x: x.get('lora_name', ''))

    for exp, ext in zip(expected_sorted, extracted_sorted):
        if exp.get('lora_name') != ext.get('lora_name'):
            return False
        # Allow small float differences for weights
        exp_weight = float(exp.get('lora_weight', 1.0))
        ext_weight = float(ext.get('lora_weight', 1.0))
        if abs(exp_weight - ext_weight) > 0.01:
            return False

    return True


def run_extractor_test(extractor_class, video_path: Path, expected: Dict[str, Any],
                       extractor_name: str) -> Dict[str, Any]:
    """Test a single extractor and return results"""
    print(f"\n{'=' * 60}")
    print(f"Testing: {extractor_name}")
    print('=' * 60)

    extractor = extractor_class()

    # Check if it can extract
    can_extract = extractor.can_extract(video_path)
    print(f"Can extract: {can_extract}")

    if not can_extract:
        print("❌ Extractor cannot handle this file")
        return {
            'name': extractor_name,
            'can_extract': False,
            'metrics': None
        }

    # Extract metadata
    extracted = extractor.extract(video_path)

    if not extracted:
        print("❌ Extraction returned None")
        return {
            'name': extractor_name,
            'can_extract': True,
            'extraction_failed': True,
            'metrics': None
        }

    # Don't print raw_metadata as it's usually huge
    display_extracted = {k: v for k, v in extracted.items() if k != 'raw_metadata'}

    print("\nExtracted metadata:")
    pprint(display_extracted)

    # Calculate accuracy
    metrics = calculate_accuracy(expected, extracted)

    print(f"\n📊 Accuracy: {metrics['accuracy']:.1f}%")
    print(f"   - Total fields: {metrics['total_fields']}")
    print(f"   - Correctly extracted: {metrics['correct_fields']}")
    print(f"   - Missing: {len(metrics['missing_fields'])} {metrics['missing_fields']}")
    print(f"   - Incorrect: {len(metrics['incorrect_fields'])} {metrics['incorrect_fields']}")

    return {
        'name': extractor_name,
        'can_extract': True,
        'extracted': display_extracted,
        'metrics': metrics
    }


def main():
    # Test configuration
    test_cases = [
        {
            'video': "/Volumes/Backup/linked/video_staging/2025-09-28/AnimateDiff_00039.mp4",
            'info': "/Volumes/Backup/linked/video_staging/2025-09-28/AnimateDiff_00039.info"
        },
        {
            'video': "/Volumes/Backup/linked/video_staging/2025-05-26/vid_00012.mp4",
            'info': "/Volumes/Backup/linked/video_staging/2025-05-26/vid_00012.info"
        }
    ]

    for test_case in test_cases:
        video_path = Path(test_case['video'])
        info_path = Path(test_case['info'])

        print("\n" + "=" * 80)
        print(f"TEST VIDEO: {video_path.name}")
        print("=" * 80)

        # Parse expected metadata
        print("\n📋 EXPECTED METADATA (from .info file):")
        print("-" * 40)
        expected = parse_info_file(info_path)
        pprint(expected)

        # Test each extractor
        results = []

        # Test current extractor (which is now the improved one)
        results.append(run_extractor_test(
            ComfyUIVideoExtractor,
            video_path,
            expected,
            "ComfyUIVideoExtractor (Current)"
        ))

        # Summary comparison
        print("\n" + "=" * 80)
        print("SUMMARY COMPARISON")
        print("=" * 80)

        for result in results:
            print(f"\n{result['name']}:")
            if not result['can_extract']:
                print("  ❌ Cannot extract from file")
            elif result.get('extraction_failed'):
                print("  ❌ Extraction failed")
            elif result['metrics']:
                m = result['metrics']
                print(f"  ✅ Accuracy: {m['accuracy']:.1f}%")
                print(f"     - Extracted: {m['extracted_fields']}/{m['total_fields']} fields")
                print(f"     - Correct: {m['correct_fields']}/{m['total_fields']} fields")
                if m['missing_fields']:
                    print(f"     - Missing: {', '.join(m['missing_fields'])}")
                if m['incorrect_fields']:
                    print(f"     - Incorrect: {', '.join(m['incorrect_fields'])}")


if __name__ == "__main__":
    main()