#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Query Variant Generator for Multi-API Performance Testing

Reads 254 base queries from sealqa_seal_hard.csv and generates 10,000 unique
query variants using synonym substitution, rephrasing, and format variations.

Ensures all queries meet API constraints:
- Brave: max 400 chars, max 50 words
- Octen: max 490 chars
"""

import csv
import random
import re
from typing import List, Set
from pathlib import Path


# Synonym mappings for common terms
SYNONYMS = {
    "who": ["which person", "what person"],
    "what": ["which", "what is"],
    "where": ["in which place", "at what location"],
    "when": ["at what time", "during which period"],
    "how many": ["what number of", "how much", "what quantity of"],
    "how": ["in what way", "by what method"],
    "current": ["present", "latest", "most recent"],
    "latest": ["most recent", "newest", "current"],
    "oldest": ["most ancient", "earliest"],
    "largest": ["biggest", "greatest"],
    "smallest": ["tiniest", "least"],
    "best": ["top", "finest", "greatest"],
    "worst": ["poorest", "least good"],
    "first": ["initial", "earliest"],
    "last": ["final", "most recent"],
    "most recent": ["latest", "newest", "current"],
    "top": ["best", "leading", "highest"],
    "highest": ["top", "greatest", "maximum"],
    "lowest": ["minimum", "bottom", "least"],
}

# Question format variations
QUESTION_STARTERS = [
    ("Who ", ["Which person ", "What person "]),
    ("What is ", ["Can you tell me what is ", "Please explain what is "]),
    ("What was ", ["Can you tell me what was ", "Tell me what was "]),
    ("How many ", ["What is the number of ", "What number of ", "Can you count how many "]),
    ("Which ", ["What ", "Which one of "]),
    ("Where ", ["In which place ", "At what location "]),
    ("When ", ["At what time ", "During which period "]),
]

# Filler word variations (for adding/removing)
FILLER_ADDITIONS = [
    " exactly",
    " precisely",
    " currently",
    " actually",
    " really",
]

# Article variations
ARTICLE_PATTERNS = [
    (r"\bthe\b", ""),  # Remove "the"
    (r"\ba\b", ""),    # Remove "a"
    (r"\ban\b", ""),   # Remove "an"
]


def load_base_queries(csv_path: str) -> List[str]:
    """Load base queries from CSV file, handling potential parsing issues."""
    queries = []

    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        # Read the CSV - handle potential unescaped commas by using proper CSV reader
        reader = csv.DictReader(f)
        for row in reader:
            if 'Query' in row and row['Query']:
                query = row['Query'].strip()
                if query:
                    queries.append(query)

    print(f"Loaded {len(queries)} base queries from {csv_path}")
    return queries


def validate_constraints(query: str) -> bool:
    """
    Validate query against API constraints:
    - Brave: max 400 chars, max 50 words
    - Octen: max 490 chars (we use the stricter Brave limit)
    """
    if len(query) > 400:
        return False

    word_count = len(query.split())
    if word_count > 50:
        return False

    return True


def truncate_to_constraints(query: str) -> str:
    """Truncate query to meet constraints if needed."""
    # First try word-based truncation to stay under 50 words
    words = query.split()
    if len(words) > 50:
        query = ' '.join(words[:50])

    # Then character-based truncation to stay under 400 chars
    if len(query) > 400:
        query = query[:397] + "..."

    return query


def apply_synonym_substitution(query: str, synonyms_dict: dict) -> List[str]:
    """Generate variants by substituting synonyms for key terms."""
    variants = []

    for original, replacements in synonyms_dict.items():
        pattern = re.compile(r'\b' + re.escape(original) + r'\b', re.IGNORECASE)
        if pattern.search(query):
            for replacement in replacements:
                # Match case of original
                def replace_func(match):
                    orig_text = match.group(0)
                    if orig_text[0].isupper():
                        return replacement.capitalize()
                    return replacement

                variant = pattern.sub(replace_func, query, count=1)
                if variant != query and validate_constraints(variant):
                    variants.append(variant)

    return variants


def apply_question_format_variations(query: str) -> List[str]:
    """Generate variants by changing question format."""
    variants = []

    for original_starter, replacements in QUESTION_STARTERS:
        if query.startswith(original_starter):
            for replacement in replacements:
                variant = replacement + query[len(original_starter):]
                if validate_constraints(variant):
                    variants.append(variant)

    return variants


def apply_filler_additions(query: str) -> List[str]:
    """Generate variants by adding filler words."""
    variants = []

    # Add fillers after the first clause or at the end
    for filler in FILLER_ADDITIONS:
        # Try adding after question word
        words = query.split(maxsplit=3)
        if len(words) >= 3:
            variant = f"{words[0]} {words[1]}{filler} {' '.join(words[2:])}"
            if validate_constraints(variant):
                variants.append(variant)

        # Try adding at the end
        if query.endswith('?'):
            variant = query[:-1] + filler + '?'
        else:
            variant = query + filler

        if validate_constraints(variant):
            variants.append(variant)

    return variants


def apply_article_variations(query: str) -> List[str]:
    """Generate variants by removing articles."""
    variants = []

    for pattern, replacement in ARTICLE_PATTERNS:
        variant = re.sub(pattern, replacement, query)
        # Clean up double spaces
        variant = re.sub(r'\s+', ' ', variant).strip()
        if variant != query and validate_constraints(variant):
            variants.append(variant)

    return variants


def apply_minor_rephrasings(query: str) -> List[str]:
    """Generate variants with minor rephrasing."""
    variants = []

    # Convert questions to declarative and vice versa
    if query.endswith('?'):
        # Remove question mark
        variant = query[:-1] + '.'
        if validate_constraints(variant):
            variants.append(variant)
    else:
        # Add question mark if it looks like a question
        if query.split()[0].lower() in ['who', 'what', 'where', 'when', 'why', 'how', 'which']:
            variant = query.rstrip('.') + '?'
            if validate_constraints(variant):
                variants.append(variant)

    # "Tell me" variations
    if query.startswith(('What ', 'Who ', 'Where ', 'When ', 'How ')):
        variant = "Tell me " + query[0].lower() + query[1:]
        if validate_constraints(variant):
            variants.append(variant)

        variant = "Can you tell me " + query[0].lower() + query[1:]
        if validate_constraints(variant):
            variants.append(variant)

    # "I want to know" variations
    if query.startswith(('What ', 'Who ', 'Where ', 'When ', 'How ')):
        variant = "I want to know " + query[0].lower() + query[1:]
        if validate_constraints(variant):
            variants.append(variant)

    return variants


def generate_variants(base_queries: List[str], target_count: int = 10000) -> List[str]:
    """
    Generate exactly target_count unique queries from base queries.

    Uses multiple variant generation techniques:
    - Original queries
    - Synonym substitution
    - Question format variations
    - Filler word additions
    - Article removal
    - Minor rephrasing
    """
    unique_queries: Set[str] = set()

    # First, add all base queries
    for query in base_queries:
        if validate_constraints(query):
            unique_queries.add(query)
        else:
            # Truncate if needed
            truncated = truncate_to_constraints(query)
            unique_queries.add(truncated)

    print(f"Added {len(unique_queries)} base queries")

    # Keep generating variants until we reach target
    iteration = 0
    max_iterations = 100  # Safety limit

    while len(unique_queries) < target_count and iteration < max_iterations:
        iteration += 1
        queries_to_process = list(unique_queries)

        for query in queries_to_process:
            if len(unique_queries) >= target_count:
                break

            # Apply each variant generation technique
            variants = []
            variants.extend(apply_synonym_substitution(query, SYNONYMS))
            variants.extend(apply_question_format_variations(query))
            variants.extend(apply_filler_additions(query))
            variants.extend(apply_article_variations(query))
            variants.extend(apply_minor_rephrasings(query))

            # Add valid variants
            for variant in variants:
                if len(unique_queries) >= target_count:
                    break
                if variant not in unique_queries:
                    unique_queries.add(variant)

        print(f"Iteration {iteration}: Generated {len(unique_queries)} unique queries")

    # If we still don't have enough, create more aggressive variants
    if len(unique_queries) < target_count:
        print(f"Need more variants. Creating combinations...")
        queries_to_process = list(unique_queries)

        for query in queries_to_process:
            if len(unique_queries) >= target_count:
                break

            # Combine multiple techniques
            # Synonym + filler
            syn_variants = apply_synonym_substitution(query, SYNONYMS)
            for syn_var in syn_variants:
                if len(unique_queries) >= target_count:
                    break
                filler_variants = apply_filler_additions(syn_var)
                for fv in filler_variants:
                    if fv not in unique_queries:
                        unique_queries.add(fv)
                        if len(unique_queries) >= target_count:
                            break

            # Question format + article removal
            q_variants = apply_question_format_variations(query)
            for qv in q_variants:
                if len(unique_queries) >= target_count:
                    break
                article_variants = apply_article_variations(qv)
                for av in article_variants:
                    if av not in unique_queries:
                        unique_queries.add(av)
                        if len(unique_queries) >= target_count:
                            break

    # Convert to list and ensure exactly target_count
    result = list(unique_queries)[:target_count]

    print(f"\nFinal count: {len(result)} unique queries")
    return result


def validate_uniqueness(queries: List[str]) -> bool:
    """Validate that all queries are unique."""
    unique_set = set(queries)
    if len(unique_set) != len(queries):
        print(f"ERROR: Found duplicates! {len(queries)} queries, but only {len(unique_set)} unique")
        return False
    print(f"✓ All {len(queries)} queries are unique")
    return True


def validate_all_constraints(queries: List[str]) -> bool:
    """Validate that all queries meet API constraints."""
    invalid_count = 0
    for i, query in enumerate(queries):
        if not validate_constraints(query):
            print(f"ERROR: Query {i+1} fails constraints: {query[:100]}...")
            invalid_count += 1

    if invalid_count == 0:
        print(f"✓ All {len(queries)} queries meet API constraints")
        return True
    else:
        print(f"ERROR: {invalid_count} queries fail constraints")
        return False


def save_queries(queries: List[str], output_path: str) -> None:
    """Save queries to output file, one per line."""
    with open(output_path, 'w', encoding='utf-8') as f:
        for query in queries:
            f.write(query + '\n')

    print(f"\n✓ Saved {len(queries)} queries to {output_path}")


def main():
    """Main execution function."""
    # Paths
    base_dir = Path(__file__).parent
    csv_path = base_dir / "sealqa_seal_hard.csv"
    output_path = base_dir / "queries_10k.txt"

    print("=" * 80)
    print("Query Variant Generator for Multi-API Performance Testing")
    print("=" * 80)

    # Load base queries
    base_queries = load_base_queries(str(csv_path))

    if len(base_queries) == 0:
        raise ValueError("No base queries loaded!")

    # Generate variants
    print(f"\nGenerating 10,000 query variants from {len(base_queries)} base queries...")
    queries = generate_variants(base_queries, target_count=10000)

    # Validate
    print("\nValidating generated queries...")
    uniqueness_ok = validate_uniqueness(queries)
    constraints_ok = validate_all_constraints(queries)

    if not uniqueness_ok or not constraints_ok:
        raise ValueError("Validation failed!")

    # Save
    save_queries(queries, str(output_path))

    # Print statistics
    print("\n" + "=" * 80)
    print("Statistics:")
    print("=" * 80)
    avg_length = sum(len(q) for q in queries) / len(queries)
    avg_words = sum(len(q.split()) for q in queries) / len(queries)
    max_length = max(len(q) for q in queries)
    max_words = max(len(q.split()) for q in queries)

    print(f"Total queries: {len(queries)}")
    print(f"Average length: {avg_length:.1f} characters")
    print(f"Average words: {avg_words:.1f}")
    print(f"Max length: {max_length} characters (limit: 400)")
    print(f"Max words: {max_words} words (limit: 50)")
    print("=" * 80)
    print("✓ Query generation complete!")
    print("=" * 80)


if __name__ == "__main__":
    main()
