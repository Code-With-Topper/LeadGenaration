"""
Shared data-quality core.

Both the lead generation scraper and the CSV/Excel importer route every
record through these modules, so a lead is cleaned, validated and
de-duplicated the same way no matter where it came from.

    normalize   turn a raw value into a canonical, comparable form
    validation  decide whether a value is usable, and say why not
    dedupe      decide whether a record is new, a duplicate, or unclear
    quality     score how complete and trustworthy a record is
"""
