"""Shared display formatting helpers for token counts."""

# Excel custom number format: shows K/M/B suffixes while keeping the cell
# numeric (so spreadsheets remain summable). Uses comma-scaling:
#   0,   -> /1,000        (K)
#   0,,  -> /1,000,000    (M)
#   0,,, -> /1,000,000,000 (B)
TOKEN_NUMFMT = (
    '[>=1000000000]0.00,,,"B";'
    '[>=1000000]0.00,,"M";'
    '[>=1000]0.00,"K";'
    "0"
)


def fmt_tokens(n) -> str:
    """Format a token count with K/M/B suffixes, 2 decimals."""
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.2f}K"
    return str(n)
