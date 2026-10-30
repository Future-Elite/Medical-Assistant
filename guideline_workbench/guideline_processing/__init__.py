"""PDF-to-Markdown and guideline chunk preparation workflow."""

__all__ = ["process_guidelines"]


def process_guidelines(*args, **kwargs):
    from .pipeline import process_guidelines as _process_guidelines

    return _process_guidelines(*args, **kwargs)
