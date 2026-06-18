"""Central design tokens + theme-aware colours for the UI.

Phase A of the UI refactor: kill hardcoded, theme-blind hex colours.  Every
colour the app paints itself should come from here so it follows light/dark
and the system accent — instead of being baked in (e.g. ``#000000`` text that
vanishes on a dark card, or an ``#e0e0e0`` chip that glares on a dark surface).

Colours are resolved at call time via :func:`isDarkTheme`, so read them when a
widget is built or refreshed.  Fluent's own widgets restyle themselves on a
live theme switch; our custom-painted bits pick up the new colours on their
next rebuild (scan/backup/restore pages rebuild their cards on every scan).
"""

from __future__ import annotations

from qfluentwidgets import isDarkTheme, themeColor


# --- Spacing & shape tokens (px) ------------------------------------------
PAGE_MARGIN_H = 36
PAGE_MARGIN_V = 20
GAP_XS, GAP_SM, GAP_MD, GAP_LG, GAP_XL = 4, 8, 12, 16, 24
RADIUS_SM, RADIUS_MD, RADIUS_PILL = 4, 6, 9


# --- Accent ---------------------------------------------------------------
def accent() -> str:
    """The system / Fluent accent colour as a ``#rrggbb`` string."""
    return themeColor().name()


# --- Neutral text (theme-aware) -------------------------------------------
def text_primary() -> str:
    """Primary foreground — high-contrast body/title text."""
    return "#ffffff" if isDarkTheme() else "#1b1b1b"


def text_secondary() -> str:
    """Secondary foreground — sub-labels, less prominent meta."""
    return "#c8c8c8" if isDarkTheme() else "#605e5c"


def text_muted() -> str:
    """Muted foreground — captions, hints, table headers, empty states."""
    return "#9a9a9a" if isDarkTheme() else "#8a8886"


# --- Surfaces -------------------------------------------------------------
def subtle_fill() -> str:
    """Faint chip/badge background that reads on a card in either theme."""
    return "rgba(255, 255, 255, 0.09)" if isDarkTheme() else "rgba(0, 0, 0, 0.05)"


def subtle_fill_text() -> str:
    """Foreground for text sitting on :func:`subtle_fill`."""
    return "#dcdcdc" if isDarkTheme() else "#444444"


def divider() -> str:
    """Hairline separator colour."""
    return "rgba(255, 255, 255, 0.10)" if isDarkTheme() else "rgba(0, 0, 0, 0.09)"


def on_accent() -> str:
    """Foreground for text on a solid accent/status fill (always white)."""
    return "#ffffff"


# --- Semantic status colours ----------------------------------------------
# key -> (base, dark-variant).  ``base`` is a solid fill that white text reads
# on in both themes; the dark-variant is a lifted shade used for *coloured
# text* so it stays legible on a dark surface.
_STATUS: dict[str, tuple[str, str]] = {
    "savestate": ("#0078d4", "#60cdff"),
    "memcard":   ("#107c10", "#6ccb5f"),
    "folder":    ("#107c10", "#6ccb5f"),
    "battery":   ("#ff8c00", "#ffc14d"),
    "file":      ("#8764b8", "#c5a3ff"),
    "added":     ("#107c10", "#6ccb5f"),
    "removed":   ("#c42b1c", "#ff99a4"),
    "modified":  ("#ff8c00", "#ffc14d"),
    "pinned":    ("#d83b01", "#ff8f5e"),
}
_STATUS_FALLBACK = ("#888888", "#9a9a9a")


def apply_card_list_layout(layout) -> None:
    """Configure a vertical card-list layout for an even visible rhythm.

    Cards carry 12px internal top+bottom padding, so the whitespace *between*
    cards is ``spacing + 2*pad`` while the whitespace from a bare label (e.g. a
    section/count row) to the first card is only ``page_spacing + 1*pad``.  We
    give the list a top margin equal to its spacing so the first card's visible
    gap matches the inter-card gap.  One place, used by every card page.

    Spacing GAP_XS → ~28px of visible whitespace between cards (GAP_XS + the two
    12px card paddings).
    """
    layout.setContentsMargins(GAP_XS, GAP_XS, GAP_XS, GAP_XS)
    layout.setSpacing(GAP_XS)


def status_fill(key: str) -> str:
    """Solid pill-background colour for *key* (white text sits on top)."""
    return _STATUS.get(key, _STATUS_FALLBACK)[0]


def status_text(key: str) -> str:
    """Coloured-text colour for *key*, lifted for legibility on dark."""
    base, dark = _STATUS.get(key, _STATUS_FALLBACK)
    return dark if isDarkTheme() else base


def success() -> str:
    """Green status text (e.g. a passing connection test)."""
    return status_text("added")


def error() -> str:
    """Red status text (e.g. a failing connection test)."""
    return status_text("removed")
