"""User-authorized internal SKU grouping, independent of publication targets."""
import re

PREFIXES=('66','77','88','99')


def internal_sku(value):
    raw=str(value or '').strip(' \t\r\n')
    if re.fullmatch('[0-9]{1,4}',raw):return raw.zfill(4)
    if re.fullmatch('(66|77|88|99)[0-9]{4}',raw):return raw[2:]
    return raw  # Never truncate arbitrary long strings or platform IDs.


def aliases_for(sku):
    if re.fullmatch('[0-9]{4}',sku):
        short=sku.lstrip('0') or '0'
        return sorted({sku,*[short.zfill(n) for n in range(len(short),5)],*[p+sku for p in PREFIXES]})
    return [sku]
